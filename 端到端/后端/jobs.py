# -*- coding: utf-8 -*-
"""任务状态机 —— 把「跑一整条链」变成可启动 / 可查 / 可停的作业。

为什么状态要**落盘**而不是放进程内字典
----------------------------------
MiroFish 原生框架把任务放在进程内的 `TaskManager` 字典里。演示时没问题，但有两个后果：
  ① 服务一重启，正在跑的任务就"失联"—— 子进程还在跑，界面却说没有任务；
  ② 想从另一个窗口看状态，看不到。

仿真一次要十几分钟，这个差别很实际。所以这里把状态写进 `_运行\\<event_id>\\job.json`，
并把子进程 pid 一并记下：**重启后按 pid 探活**就知道任务还在不在，不需要靠内存。

状态取值
--------
    idle      没跑过
    running   子进程活着
    done      子进程正常退出（仿真还要看轮次是否跑满，见 progress.inventory）
    failed    退出码非 0，或 pid 已消失但状态还写着 running（"失联"）
    stopped   被 stop() 主动停掉
"""
import json
import os
import subprocess
import sys
import threading
import time

import paths

KINDS = {
    "sim": {
        "script": os.path.join(paths.ENGINE_DIR, "跑全轮.py"),
        "cwd": paths.ENGINE_DIR,
        "label": "仿真推演",
    },
    "report": {
        "script": os.path.join(paths.REPORT_DIR, "生成报告.py"),
        "cwd": paths.REPORT_DIR,
        "label": "生成报告",
    },
}


class JobError(Exception):
    pass


def log_path(event_id, kind):
    return os.path.join(paths.runtime_dir(event_id), "%s.log" % kind)


def _job_path(event_id):
    return os.path.join(paths.runtime_dir(event_id), "job.json")


def _read_raw(event_id):
    p = _job_path(event_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(event_id, d):
    p = _job_path(event_id)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)          # 原子替换：读到的永远是完整 JSON，不会是半截
    return d


def _alive(pid):
    """pid 还活着吗。Windows 上用 `tasklist` 探（`os.kill(pid,0)` 在 Windows 上语义不同）。"""
    if not pid:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# 本进程起过的作业 → Popen 句柄。有句柄时 `poll()` 是**权威**答案，
# 不需要靠 pid 探活去猜（探活有窗口期，会把刚跑完的判成"失联"）。
_PROCS = {}
_LOCK = threading.Lock()


def _finish(event_id, rc, note=None):
    """写终态。**唯一**的终态写入口 —— `_wait` 和 `status()` 都走这里，避免两处抢写。

    已经被 stop() 标成 stopped 的不改判（停掉之后子进程的退出码没意义）。
    """
    with _LOCK:
        d = _read_raw(event_id) or {"event_id": event_id}
        if d.get("state") == "stopped":
            return d
        d.update({
            "state": "done" if rc == 0 else "failed",
            "exit_code": rc,
            "ended_at": d.get("ended_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": None if rc == 0 else (note or "退出码 %s —— 看日志尾" % rc),
        })
        return _write(event_id, d)


def status(event_id):
    """当前作业状态。**每次现算**。

    判"失联"的两条路径，优先级从高到低：
      ① 本进程持有 Popen 句柄 → `poll()`。非 None 说明已退出，直接按退出码收尾
         （否则会和 `_wait` 线程抢写，把刚跑完的任务误判为 failed）。
      ② 没句柄（服务重启过）→ 只能按 pid 探活。死了就判 failed，并且如实说明
         "退出码未知"。
    """
    d = _read_raw(event_id) or {"event_id": event_id, "state": "idle", "kind": None}
    if d.get("state") != "running":
        d.setdefault("event_id", event_id)
        return d

    proc = _PROCS.get(event_id)
    if proc is not None:
        rc = proc.poll()
        if rc is not None:
            _PROCS.pop(event_id, None)
            return _finish(event_id, rc)
        d.setdefault("event_id", event_id)
        return d

    if not _alive(d.get("pid")):
        d = dict(d)
        d.update({"state": "failed", "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "error": "子进程已消失（服务重启过，或进程被杀）—— 退出码未知"})
        return _write(event_id, d)
    d.setdefault("event_id", event_id)
    return d


def is_busy(event_id):
    return status(event_id).get("state") == "running"


def _kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass


def start(event_id, kind="sim"):
    """起一个作业。同一事件同时只允许一个 —— 仿真会覆盖同名快照，并行跑必然打架。"""
    if kind not in KINDS:
        raise JobError("不认识的任务类型：%r（可选 %s）" % (kind, " / ".join(KINDS)))
    k = KINDS[kind]

    if is_busy(event_id):
        raise JobError("这个事件已经有任务在跑（%s，pid=%s）—— 先停掉再起。"
                       % (status(event_id).get("kind"), status(event_id).get("pid")))

    cfg = paths.load_config(event_id)          # 读不出来会抛 ConfigError，交给上层

    if not os.path.exists(k["script"]):
        raise JobError("找不到脚本：%s" % k["script"])

    cmd = [sys.executable, "-I", k["script"]]
    if cfg.get("_path"):
        cmd += ["--config", cfg["_path"]]

    lp = log_path(event_id, kind)
    lf = open(lp, "w", encoding="utf-8")
    lf.write("$ %s\n" % " ".join(cmd))
    lf.flush()

    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True     # 便于整组杀
    proc = subprocess.Popen(cmd, cwd=k["cwd"], stdout=lf, stderr=subprocess.STDOUT, **kwargs)
    _PROCS[event_id] = proc

    d = {
        "event_id": event_id,
        "kind": kind,
        "label": k["label"],
        "state": "running",
        "pid": proc.pid,
        "cmd": cmd,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": None,
        "exit_code": None,
        "log": lp,
        "error": None,
    }
    _write(event_id, d)

    def _wait():
        rc = proc.wait()
        try:
            lf.close()
        except Exception:
            pass
        # ★ 先收尾、再摘句柄。反过来的话，两者之间那一瞬间 `status()` 走了
        #   "没句柄 → 按 pid 探活" 的分支，会把刚跑完的任务误判成"失联"。
        _finish(event_id, rc)
        _PROCS.pop(event_id, None)

    threading.Thread(target=_wait, daemon=True).start()
    return d


def stop(event_id):
    d = status(event_id)
    if d.get("state") != "running":
        raise JobError("没有在跑的任务（当前状态：%s）" % d.get("state"))
    pid = d.get("pid")
    _kill_tree(pid)
    for _ in range(20):                        # 最多等 2s 确认真的走了
        if not _alive(pid):
            break
        time.sleep(0.1)
    d = dict(d)
    d.update({"state": "stopped", "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "error": "已手动停止"})
    return _write(event_id, d)


def tail(event_id, kind=None, n=200):
    """日志尾 n 行。kind 缺省取当前作业的类型。"""
    if kind is None:
        kind = (status(event_id).get("kind") or "sim")
    p = log_path(event_id, kind)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return lines[-n:]
