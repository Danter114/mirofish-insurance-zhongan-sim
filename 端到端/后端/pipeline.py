# -*- coding: utf-8 -*-
"""一键全链：把 `跑端到端_v3.py` 的采集 → 图谱 → 仿真 → 报告 → 前端 挂到 HTTP 上。

**为什么是子进程，不是 import**：编排器会 `sys.exit()`、会改 `sys.path`、
会 `rmtree` 自己的输出目录 —— 那是命令行工具的行为，不是库的行为。
用子进程跑，一条链崩了就是这一条链崩，后端进程不受影响。

状态全部落盘（`_运行\\<KEY>\\pipeline.json` + `pipeline.log`）：

  POST /api/events/{id}/pipeline        起一条链（同一事件同时只许一条）
  GET  /api/events/{id}/pipeline        进度：state / stage / 已跑段 / 日志尾
  POST /api/events/{id}/pipeline/stop   停（Windows 上连子进程树一起杀）

★ 进度是**从日志里读出来的**，不是估算的：编排器每段开头打 `【采集】` 这种行，
  这里就按这几个标记算"现在跑到哪一段"。日志本身一行不改。
★ 凭据只从环境（含界面里存进事件配置的 key）取，**不回显、不落日志**。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.join(paths.ROOT, "跑端到端_v3.py")
STAGES = ("采集", "图谱", "仿真", "报告", "前端")
STAGE_RX = re.compile(r"【(%s)】" % "|".join(STAGES))

_PROCS = {}          # KEY -> Popen
_LOCK = threading.Lock()
# ★ 状态文件**同一个 .tmp 名**会被两条线程同时写（读状态的 GET 与读日志的 pump），
#   Windows 上 `os.replace` 会撞 "另一个进程正在使用" 或找不到刚被替换掉的文件 ——
#   实测过一次 500。写状态一律串行。
_WLOCK = threading.Lock()


class PipeError(Exception):
    pass


def _dir(key):
    d = os.path.join(paths.RUNTIME_DIR, key)
    os.makedirs(d, exist_ok=True)
    return d


def _state_path(key):
    return os.path.join(_dir(key), "pipeline.json")


def _log_path(key):
    return os.path.join(_dir(key), "pipeline.log")


def _read_state(key):
    p = _state_path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_state(key, st):
    p = _state_path(key)
    with _WLOCK:
        tmp = "%s.%d.%d.tmp" % (p, os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def _alive(key):
    p = _PROCS.get(key)
    return bool(p and p.poll() is None)


def _refresh(key):
    """把日志读一遍，更新 stage / 行数。日志可能很大，只读新增部分。"""
    st = _read_state(key) or {}
    lp = _log_path(key)
    if not os.path.exists(lp):
        return st
    size = os.path.getsize(lp)
    with open(lp, "rb") as f:
        f.seek(min(st.get("_offset") or 0, size))
        chunk = f.read().decode("utf-8", "replace")
    st["_offset"] = size
    if chunk:
        st["_buf"] = ((st.get("_buf") or "") + chunk)[-200000:]
    buf = st.get("_buf") or ""
    marks = [m.group(1) for m in STAGE_RX.finditer(buf)]
    if marks:
        st["stage"] = marks[-1]
        st["stage_index"] = STAGES.index(marks[-1]) + 1
        done = []
        for s in STAGES:
            if s in marks and (marks.index(s) < len(marks) - 1 or "✔" in buf.split("【%s】" % s)[-1][:4000]):
                done.append(s)
        st["stages_seen"] = sorted(set(marks), key=STAGES.index)
    st["lines"] = buf.count("\n")
    _write_state(key, st)
    return st


def status(key):
    st = _read_state(key)
    if st is None:
        return {"ok": True, "event_id": key, "state": "idle", "stage": None,
                "stages_seen": [], "lines": 0, "tail": []}
    st = _refresh(key)
    p = _PROCS.get(key)
    # ★ 进程刚退出、pump 线程还没来得及写 returncode 的那一瞬间，`poll()` 已经非 None
    #   而状态文件里还是 `returncode=None` —— 直接判 failed 会**误报失败**（实测过）。
    #   以 Popen 自己的 returncode 为准，它一定在 poll() 非 None 之后可读。
    if st.get("state") == "running" and p is not None and p.poll() is not None:
        code = p.returncode if p.returncode is not None else st.get("returncode")
        st["state"] = "done" if code == 0 else "failed"
        st["returncode"] = code
        st.setdefault("finished", time.time())
        _write_state(key, st)
    if _alive(key):
        st["state"] = "running"
    tail = (st.get("_buf") or "").splitlines()[-60:]
    started = st.get("started")
    if started:
        end = time.time() if st.get("state") == "running" else (st.get("finished") or time.time())
        elapsed = int(end - started)
    else:
        elapsed = None
    return {"ok": True, "event_id": key, "state": st.get("state") or "idle",
            "stage": st.get("stage"), "stage_index": st.get("stage_index"),
            "stages_seen": st.get("stages_seen") or [],
            "started": started, "elapsed": elapsed,
            "returncode": st.get("returncode"), "error": st.get("error"),
            "argv": st.get("argv"), "log": _log_path(key), "tail": tail}


def _child_env(cfg):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 界面上填进事件配置的 key ⇒ 转成子进程认识的三个变量（**不写日志**）。
    #
    # ★ 2026-10-21 实测确认：这三个变量**引擎不读** —— `t1_runner._load_llm()` 与
    #   `precheck._check_llm()` 只认「`.env` 文件 + 配置里的 `llm` 段」，进程环境
    #   两边都不看；唯一的环境钩子是 `_sim_config` 里的 **`SIM_ENV` = .env 的路径**。
    #   所以真正让后端起的链拿到钥匙的是**下面这行把 key 写回配置**（`POST /llm` 干的
    #   就是这件事）—— 引擎读配置里的 `llm` 段。这两行保留只为万一有外部脚本读环境，
    #   别指望它能配好钥匙：要配钥匙，走界面上的「接入 API Key」或给 `SIM_ENV`。
    llm = (cfg or {}).get("llm") or {}
    if (llm.get("api_key") or "").strip():
        env["LLM_API_KEY"] = llm["api_key"].strip()
        if llm.get("base_url"):
            env["LLM_BASE_URL"] = llm["base_url"].strip()
        if llm.get("model_name"):
            env["LLM_MODEL_NAME"] = llm["model_name"].strip()
    return env


def start(key, body):
    """起一条链。`body` 见 `_argv()`。"""
    if not os.path.exists(ORCH):
        raise PipeError("找不到编排器：%s" % ORCH)
    with _LOCK:
        if _alive(key):
            raise PipeError("这一场已经在跑了（pid %s）" % _PROCS[key].pid)
        argv = _argv(key, body)
        lp = _log_path(key)
        open(lp, "w", encoding="utf-8").close()      # 每次起跑重开日志
        try:
            cfg = paths.load_config(key)
        except Exception:
            cfg = None
        env = _child_env(cfg)
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.Popen(argv, cwd=paths.ROOT, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=flags)
        _PROCS[key] = p
        st = {"state": "running", "stage": None, "stages_seen": [], "started": time.time(),
              "argv": [_safe(a) for a in argv], "log": lp, "_offset": 0, "_buf": ""}
        _write_state(key, st)

    t = threading.Thread(target=_pump, args=(key, p), daemon=True)
    t.start()
    return status(key)


def _safe(a):
    """argv 里的绝对路径照留；key 之类的值本来也不敏感，统一过一遍以防万一。"""
    s = str(a)
    if len(s) > 180:
        s = s[:177] + "…"
    return s


def _pump(key, p):
    lp = _log_path(key)
    with open(lp, "a", encoding="utf-8", newline="\n") as f:
        for raw in iter(p.stdout.readline, b""):
            f.write(raw.decode("utf-8", "replace"))
            f.flush()
            _refresh(key)
    code = p.wait()
    st = _read_state(key) or {}
    st["returncode"] = code
    st["state"] = "done" if code == 0 else "failed"
    st["finished"] = time.time()
    st["elapsed"] = int(st["finished"] - (st.get("started") or st["finished"]))
    if code != 0:
        # 编排器失败时最后几行有意义（闸的报错），留在 error 里给界面直接显示
        st["error"] = "\n".join(((st.get("_buf") or "").splitlines())[-8:])
    _write_state(key, st)


def stop(key):
    p = _PROCS.get(key)
    if not p or p.poll() is not None:
        return {"ok": True, "stopped": False, "reason": "没有在跑的进程"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            p.terminate()
    except Exception as e:
        raise PipeError("停不下来：%s" % e)
    st = _read_state(key) or {}
    st["state"] = "stopped"
    _write_state(key, st)
    return {"ok": True, "stopped": True, "pid": p.pid}


def _argv(key, b):
    """把界面上的表单翻成编排器的命令行。

    输入三选一（按 `from` 决定要不要、要哪个）：
      · `raw_path`  原始帖子 JSON（要跑采集段时用）
      · `event_path` 采集侧成品 eventN.json（跳过采集）
      · 都没有且 `from` 在仿真/报告 ⇒ 纯重跑尾段
    """
    b = b or {}
    # 被只读装饰过的 key 走同一套白名单
    bkey = paths.safe_event_id(b.get("key") or key)
    if bkey != key:
        raise PipeError("事件键不一致：URL 是 %s，表单是 %s" % (key, bkey))
    frm = b.get("from") or "采集"
    to = b.get("to") or "前端"
    if frm not in STAGES or to not in STAGES:
        raise PipeError("段名只能是 %s" % "/".join(STAGES))
    argv = [sys.executable, "-X", "utf8", ORCH, "--key", key, "--from", frm, "--to", to]

    raw = (b.get("raw_path") or "").strip()
    ev = (b.get("event_path") or "").strip()
    inline = b.get("raw_json")
    if inline:
        # 贴进来的原始 JSON：落成本事件自己的输入文件（不进交付目录）
        p = os.path.join(_dir(key), "raw_posts_%s.json" % key)
        with open(p, "w", encoding="utf-8") as f:
            f.write(inline if isinstance(inline, str) else json.dumps(inline, ensure_ascii=False))
        raw = p
    if raw:
        if not os.path.exists(raw):
            raise PipeError("原始数据文件不存在：%s" % raw)
        argv += ["--raw", os.path.abspath(raw)]
    if ev:
        if not os.path.exists(ev):
            raise PipeError("事件成品不存在：%s" % ev)
        argv += ["--event", os.path.abspath(ev)]
    if STAGES.index(frm) <= STAGES.index("图谱") and not raw and not ev:
        raise PipeError("跑「采集 / 图谱」至少要给一个输入：原始数据或采集侧成品")
    if b.get("id"):
        argv += ["--id", str(int(b["id"]))]
    for k, flag in (("title", "--title"), ("t0", "--t0"),
                    ("event_type", "--event-type"), ("subject", "--subject")):
        v = (b.get(k) or "").strip()
        if v:
            argv += [flag, v]
    if b.get("gen_personas"):
        argv.append("--gen-personas")
    return argv
