# -*- coding: utf-8 -*-
r"""采集层接口 —— 把 `端到端\采集层\` 那一套挂到 HTTP 上。

六条路由（都只调**子进程**，不 import 采集侧代码）：

  GET  /api/collect/status        这一层现在能跑什么（模式可用性 / 样本 / 缺什么）
  POST /api/collect/card          主题 → 参数卡（`参数卡生成.py`）
  POST /api/collect/run           跑一趟：采集（+ 可选结构化 + 可选看板）
  POST /api/collect/job/open      新事件：开一张检索作业单（`检索作业.py 开单`）
  POST /api/collect/job/collect   新事件：收单校验 + 出 raw（`检索作业.py 收单 --collect`）
  GET  /api/collect/log           上一次跑的完整日志

设计取向和后端其他部分一致：**能跑的就说能跑，跑不了就报错说缺什么**。
采集器不在、适配器不存在、脚本崩了，都返回 400/500 带人话，不返回一个空数组。
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time

import paths

COLLECT_DIR = os.path.join(paths.ROOT, "采集层")
LINK = os.path.join(COLLECT_DIR, "采集链.py")
CARD_GEN = os.path.join(COLLECT_DIR, "参数卡生成.py")
BUILD_EVENT = os.path.join(COLLECT_DIR, "build_event.py")
JOB_TOOL = os.path.join(COLLECT_DIR, "检索作业.py")
DASH_BUILDER = os.path.join(COLLECT_DIR, "00_程序与文档", "看板生成", "evt_dash_builder.py")

RUN_ROOT = os.path.join(paths.RUNTIME_DIR, "采集层")

# 事件类型 8 类枚举（`舆情事件分类体系.md`；build_event 会强制校验）
EVENT_TYPES = ["理赔类", "销售与投保类", "产品与费率类", "服务态度类",
               "营销类", "数据与隐私类", "公司经营类", "司法与维权类"]
_LOCK = threading.Lock()
_LAST = {"dir": None, "log": "", "at": None}

ApiError = None      # 由 app.py 注入


def _env():
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e


def _run(argv, timeout=300):
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, encoding="utf-8",
                           errors="replace", env=_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ApiError(504, "这一步超过 %d 秒没结束，已中止：%s" % (timeout, os.path.basename(argv[1])))
    except OSError as e:
        raise ApiError(500, "起不来子进程：%s" % e)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out, time.time() - t0


def _need():
    if not os.path.exists(LINK):
        raise ApiError(500, "采集层不在位：%s\n  这一份交付应该自带它；如果是拆开拷的，"
                            "把 `端到端\\采集层\\` 一起拷过来。" % LINK)


def _samples():
    out = []
    for d in sorted(glob.glob(os.path.join(COLLECT_DIR, "EVT-*"))):
        card = glob.glob(os.path.join(d, "EVT-*_参数卡.json"))
        snap = os.path.join(d, "原文快照")
        if not card:
            continue
        n = 0
        window = None
        meta = os.path.join(snap, "snapshot_meta.json")
        if os.path.exists(meta):
            try:
                with open(meta, encoding="utf-8-sig") as f:
                    rows = json.load(f)
                n = len(rows)
                # 快照里真实的时间跨度 —— 前端生成参数卡时用它兜窗口，
                # 否则默认"今天~明天"会把 2026-05 的快照全过滤掉（这个坑踩过）
                days = sorted((r.get("publish_time") or "")[:10] for r in rows
                              if (r.get("publish_time") or "")[:10])
                if days:
                    window = [days[0], days[-1]]
            except Exception:
                n = 0
        m = __import__("re").match(r"EVT-(\d+)_", os.path.basename(d))
        out.append({"dir": os.path.basename(d),
                    "event_id": int(m.group(1)) if m else None,
                    "card": card[0],
                    "snapshot_dir": snap if os.path.isdir(snap) else None,
                    "raw": (glob.glob(os.path.join(d, "EVT-*_raw.json")) or [None])[0],
                    "snapshots": n, "window": window})
    return out


def safe_output(p):
    """采集产物的取用路径白名单：只许 `_运行\\采集层\\` 下面的 `.html`。

    采集产物落在运行目录，不在 `静态\\` 里，所以浏览器打不开磁盘路径；
    这一条把它直出成 HTTP。**只放行自己写出来的那一棵树**，不变成任意文件读取。
    """
    if not p:
        raise ApiError(400, "要给 path（采集产物 HTML 的服务端路径）")
    ap = os.path.abspath(p)
    root = os.path.abspath(RUN_ROOT)
    if not (ap == root or ap.startswith(root + os.sep)):
        raise ApiError(403, "只允许取 %s 下面的文件。" % root)
    if not ap.lower().endswith((".html", ".json", ".jsonl")):
        raise ApiError(403, "只放行 .html / .json / .jsonl。")
    if not os.path.isfile(ap):
        raise ApiError(404, "没有这个文件：%s" % ap)
    return ap


def _count_raw(p):
    if not p or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8-sig") as f:
        rows = json.load(f)
    ch = {}
    for r in rows:
        k = r.get("channel") or "（空）"
        ch[k] = ch.get(k, 0) + 1
    return {"path": p, "count": len(rows),
            "channels": dict(sorted(ch.items(), key=lambda kv: -kv[1])),
            "fields": len(rows[0]) if rows else 0}


def register(route, api_error_cls):
    global ApiError
    ApiError = api_error_cls

    @route("GET", r"/api/collect/status")
    def _(h, m, query, body):
        if not os.path.isdir(COLLECT_DIR):
            return {"ok": False, "collect_dir": COLLECT_DIR,
                    "error": "采集层目录不存在：%s" % COLLECT_DIR}
        info = {}
        try:
            rc, out, _ = _run([sys.executable, LINK, "--check"], timeout=60)
            i = out.find("{")
            info = json.loads(out[i:]) if i >= 0 else {}
        except ApiError as e:
            info = {"error": str(e.args[1])}
        return {"ok": True, "collect_dir": COLLECT_DIR,
                "modes": info, "samples": _samples(),
                "adapter_template": os.path.join(COLLECT_DIR, "采集适配器_模板.py"),
                "last_run": _LAST["dir"],
                "has_key": bool(os.environ.get("LLM_API_KEY"))}

    @route("POST", r"/api/collect/card")
    def _(h, m, query, body):
        _need()
        topic = ((body or {}).get("topic") or "").strip()
        if not topic:
            raise ApiError(400, "topic 是空的。给一个模糊主题就行，比如「宠物险」。")
        if len(topic) > 60:
            raise ApiError(400, "topic 太长（%d 字），给个关键词就行。" % len(topic))
        d = os.path.join(RUN_ROOT, "card_%s" % time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "参数卡.json")
        argv = [sys.executable, CARD_GEN, "--topic", topic, "--out", p]
        b = body or {}
        for k, flag in (("event_id", "--event-id"), ("start", "--start"), ("end", "--end")):
            if b.get(k):
                argv += [flag, str(b[k])]
        rc, out, cost = _run(argv, timeout=120)
        if rc != 0 or not os.path.exists(p):
            raise ApiError(500, "参数卡生成失败（exit=%d）：\n%s" % (rc, out[-800:]))
        with open(p, encoding="utf-8") as f:
            card = json.load(f)
        # 8 类事件类型：生成器拿不准时会留空。界面上补选一个，这里落实并**写回磁盘**——
        # 否则结构化那一步会被 build_event 的 8 类校验挡下来（不是崩，是明确拒绝）。
        et = (b.get("event_type") or "").strip()
        if et:
            if et not in EVENT_TYPES:
                raise ApiError(400, "event_type 只能是 %s 之一。" % " / ".join(EVENT_TYPES))
            card["event_type"] = et
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
        return {"ok": True, "card": card, "path": p,
                "llm_used": "规则兜底" not in out, "cost_s": round(cost, 2),
                "event_types": EVENT_TYPES,
                "log_tail": out[-800:]}

    @route("POST", r"/api/collect/run")
    def _(h, m, query, body):
        _need()
        b = body or {}
        mode = b.get("mode") or "snapshot"
        if mode not in ("snapshot", "api", "live"):
            raise ApiError(400, "mode 只能是 snapshot / api / live。")
        card = (b.get("card") or "").strip()
        if not card:
            raise ApiError(400, "要给 card（参数卡 JSON 的服务端路径，或 /api/collect/card 返回的 path）。")
        if not os.path.exists(card):
            raise ApiError(400, "参数卡文件不存在：%s" % card)
        # ★ 直接读一遍，把 event_id 取出来 —— 后面找产物要用它，不猜
        try:
            with open(card, encoding="utf-8-sig") as f:
                card_obj = json.load(f)
            eid = int(card_obj["event_id"])
        except Exception as e:
            raise ApiError(400, "参数卡读不动或缺 event_id：%s" % e)

        out_dir = (b.get("out") or "").strip()
        if not out_dir:
            out_dir = os.path.join(RUN_ROOT, "%s_%s" % (time.strftime("%Y%m%d_%H%M%S"),
                                                        card_obj.get("event_name") or "事件"))
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)

        argv = [sys.executable, LINK, "--mode", mode, "--card", card, "--out", out_dir]
        if mode == "snapshot":
            sd = (b.get("snapshot_dir") or "").strip()
            if not sd or not os.path.isdir(sd):
                raise ApiError(400, "快照回放要给 snapshot_dir（含 snapshot_meta.json 的目录）：%r" % (sd,))
            argv += ["--snapshot-dir", sd]
        elif mode == "api":
            ad = (b.get("adapter") or "").strip()
            if not ad or not os.path.exists(ad):
                raise ApiError(400, "api 模式要给 adapter（适配器 .py 的服务端路径）：%r" % (ad,))
            argv += ["--adapter", ad]
        elif b.get("collector"):
            argv += ["--collector", b["collector"]]

        steps = [{"step": "采集（%s）" % mode, "cmd": " ".join(os.path.basename(x) for x in argv[:1] + argv[2:])}]
        rc, log, cost = _run(argv, timeout=600)
        steps[0].update(exit=rc, cost_s=round(cost, 2))
        raw_p = os.path.join(out_dir, "EV%d_raw.json" % eid)
        if rc != 0 or not os.path.exists(raw_p):
            # 采集器不在 / 参数不对，原文贴回去，别让人猜
            with _LOCK:
                _LAST.update(dir=out_dir, log=log, at=time.strftime("%Y-%m-%d %H:%M:%S"))
            raise ApiError(400, "采集这一步没成功（exit=%d）：\n%s" % (rc, log[-1500:]))

        res = {"ok": True, "mode": mode, "event_id": eid, "out": out_dir,
               "raw": _count_raw(raw_p), "steps": steps}

        # ── 可选：结构化（raw 21 → processed 28）─────────────────────────
        if b.get("structure"):
            t0 = card_obj.get("start_date") or ""
            argv2 = [sys.executable, BUILD_EVENT, "--raw", raw_p,
                     "--event-id", str(eid), "--t0", t0,
                     "--topic-tag", card_obj.get("topic_tag") or card_obj.get("event_name") or "",
                     "--topic-list"]
            tl = card_obj.get("topic_list") or []
            if not tl:
                raise ApiError(400, "参数卡缺 topic_list，结构化这一步没法跑。")
            argv2 += [str(x) for x in tl]
            proc_p = os.path.join(out_dir, "EV%d_processed.json" % eid)
            argv2 += ["--output", proc_p]
            if not os.environ.get("LLM_API_KEY"):
                argv2.append("--no-llm")
            t1 = time.time()
            rc2, log2, cost2 = _run(argv2, timeout=900)
            steps.append({"step": "结构化与标注", "exit": rc2, "cost_s": round(cost2, 2)})
            log += log2
            if rc2 == 0 and os.path.exists(proc_p):
                with open(proc_p, encoding="utf-8") as f:
                    rows = json.load(f)
                res["processed"] = {"path": proc_p, "count": len(rows),
                                    "fields": len(rows[0]) if rows else 0,
                                    "labeled_by": "llm" if os.environ.get("LLM_API_KEY") else "rules"}
                # ── 可选：看板（processed + 自动文案包 → 单文件 HTML）───────
                if b.get("dashboard"):
                    pack_p = os.path.join(out_dir, "EV%d_pack_auto.json" % eid)
                    try:
                        if COLLECT_DIR not in sys.path:
                            sys.path.insert(0, COLLECT_DIR)
                        import importlib
                        onepot = importlib.import_module("一键演示")
                        with open(pack_p, "w", encoding="utf-8", newline="\n") as f:
                            json.dump(onepot.auto_pack(card_obj, rows), f,
                                      ensure_ascii=False, indent=1)
                        dash_p = os.path.join(out_dir, "EV%d_舆情看板.html" % eid)
                        rc3, log3, cost3 = _run([sys.executable, DASH_BUILDER, out_dir,
                                                 pack_p, dash_p], timeout=300)
                        steps.append({"step": "舆情看板", "exit": rc3, "cost_s": round(cost3, 2)})
                        log += log3
                        if rc3 == 0 and os.path.exists(dash_p):
                            res["dashboard"] = {"path": dash_p,
                                                "size_kb": round(os.path.getsize(dash_p) / 1024.0, 1)}
                    except ApiError:
                        raise
                    except Exception as e:
                        steps.append({"step": "舆情看板", "exit": 1, "note": str(e)[:200]})

        with _LOCK:
            _LAST.update(dir=out_dir, log=log, at=time.strftime("%Y-%m-%d %H:%M:%S"))
        res["log_tail"] = log[-2000:]
        return res

    # ── 新事件：检索作业（开单 / 收单）─────────────────────────────────
    @route("POST", r"/api/collect/job/open")
    def _(h, m, query, body):
        _need()
        if not os.path.exists(JOB_TOOL):
            raise ApiError(500, "检索作业.py 不在位：%s" % JOB_TOOL)
        b = body or {}
        card = (b.get("card") or "").strip()
        if not card or not os.path.exists(card):
            raise ApiError(400, "要给 card（服务端路径的参数卡 JSON）：%r" % (card,))
        job = os.path.join(RUN_ROOT, "作业_%s" % time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(job, exist_ok=True)
        rc, out, cost = _run([sys.executable, JOB_TOOL, "开单", "--card", card, "--out", job], timeout=120)
        if rc != 0:
            raise ApiError(400, "开单失败（exit=%d）：\n%s" % (rc, out[-1200:]))
        files = sorted(glob.glob(os.path.join(job, "**", "*"), recursive=True))
        return {"ok": True, "job": job, "cost_s": round(cost, 2),
                "files": [f for f in files if os.path.isfile(f)],
                "queries": [l.strip().lstrip("- ").strip() for l in out.splitlines()
                            if "建议检索串：" in l],
                "log_tail": out[-1200:]}

    @route("POST", r"/api/collect/job/collect")
    def _(h, m, query, body):
        _need()
        b = body or {}
        job = (b.get("job") or "").strip()
        if not job or not os.path.isdir(job):
            raise ApiError(400, "要给 job（作业目录的服务端路径）：%r" % (job,))
        # 作业目录必须在运行目录下 —— 别让它变成一个能对任意目录跑子进程的口子
        if not os.path.abspath(job).startswith(os.path.abspath(RUN_ROOT) + os.sep):
            raise ApiError(403, "作业目录只允许在 %s 下面。" % RUN_ROOT)
        card = (b.get("card") or "").strip()
        if not card or not os.path.exists(card):
            raise ApiError(400, "要给 card（服务端路径的参数卡 JSON）：%r" % (card,))
        out_dir = os.path.join(job, "_收单输出")
        argv = [sys.executable, JOB_TOOL, "收单", "--job", job, "--card", card,
                "--out", out_dir, "--collect"]
        rc, out, cost = _run(argv, timeout=900)
        with _LOCK:
            _LAST.update(dir=job, log=out, at=time.strftime("%Y-%m-%d %H:%M:%S"))
        rep = {}
        rp = os.path.join(out_dir, "收单校验.json")
        if os.path.exists(rp):
            try:
                with open(rp, encoding="utf-8-sig") as f:
                    rep = json.load(f)
            except Exception:
                rep = {}
        # 校验没过也把清单带回去（界面要能看见缺哪一项），所以 ok 恒为 True
        raw_p = (sorted(glob.glob(os.path.join(out_dir, "EV*_raw.json"))) or [None])[0]
        return {"ok": True, "passed": rc == 0, "job": job, "out": out_dir,
                "fails": rep.get("fails") or [], "warns": rep.get("warns") or [],
                "metas": rep.get("metas"), "claimed_kept": rep.get("claimed_kept"),
                "raw": _count_raw(raw_p) if raw_p else None,
                "cost_s": round(cost, 2), "log_tail": out[-2500:]}

    @route("GET", r"/api/collect/log")
    def _(h, m, query, body):
        return {"ok": True, "dir": _LAST["dir"], "at": _LAST["at"],
                "log": _LAST["log"]}
