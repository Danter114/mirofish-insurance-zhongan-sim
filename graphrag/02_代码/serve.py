#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py —— GraphRAG 上游端到端服务（纯标准库，零第三方依赖）

对外三段：
  ① 前端接口   GET  /                       前端页面
                POST /api/run                提交事件 JSON -> 跑全链路
                GET  /api/job/<job_id>       轮询进度
                GET  /api/result/<event_id>  取完整结果（图谱 + 名单）
                GET  /api/events             已知事件清单
  ② 下游接口   GET  /api/delivery/<event_id>  D_Agent名单.json（14 字段）
                GET  /api/t0/<event_id>        B_t0状态快照.json（t0 事件脉络）
                GET  /api/full/<event_id>      完整件（69 字段，自用）
  ③ 运维       GET  /api/health

★ 核心不变量：同一份事件 JSON 进来，展示与输出必须【逐字节一致】。
   实现：① 内容指纹 = sha256(归一化后的帖子) —— 忽略字段顺序与无关空白
         ② 已知事件直接返回缓存产物（runs/<eid>/），不重跑
         ③ 新事件用固定主种子跑确定性管线，产物落盘后即成为「已知事件」

用法：
    python serve.py                 # 默认 127.0.0.1:8848
    python serve.py --port 9000
    python serve.py --host 0.0.0.0  # 允许局域网访问
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
PERSONAS = ROOT / "out" / "persona_v18_3k_adapted.json"
SEED = 20261002
PY = sys.executable
T0_DAYS = 2          # ★ t0 = Day0 + Day1（用户口径）

# 已知事件（与交付包一致）
KNOWN = {
    "EVT-001": "宠物险广告低俗 P图女性私密部位", "EVT-002": "72岁肝癌患者投诉恶意拒赔",
    "EVT-003": "19元低价引流自动扣费", "EVT-004": "金融监管1号罚单 被罚180万",
    "EVT-005": "1.6元低价诱导自动扣费", "EVT-006": "新冠隔离险阳了不赔",
    "EVT-007": "隔离险拒赔 上海无中高风险区", "EVT-008": "诱导老人投保低价引流",
    "EVT-009": "营销骚扰 上海消保委315前夕点名", "EVT-010": "众民保美国不可能有电梯广告出圈",
    "EVT-011": "百保君子公司暴雷 京东E卡兑付危机", "EVT-012": "齐鲁保搜索劫持 广告碰瓷惠民保",
}
# 事件指纹 -> event_id（启动时扫描 runs/ 填充）
FP2EID = {}
JOBS = {}           # job_id -> {status, pct, msg, event_id, started}
LOCK = threading.Lock()

# ── 内容指纹 ────────────────────────────────────────────────────────────
#   只取【决定图与选人】的字段，忽略顺序、缩进、无关空白。
#   这样「同一个事件的不同排版 JSON」也会被认成同一个事件。
# ★ 指纹必须覆盖【所有影响建图/选人的字段】。2026-10-13 补 role_type：
#   改了 role_type（中央媒体 -> 监管）但指纹不变 -> 服务端会返回旧缓存（踩过）。
FP_FIELDS = ("post_id", "content", "channel", "publish_time", "author_name", "role_type")


def fingerprint(posts):
    rows = []
    for p in posts:
        rows.append("|".join(str(p.get(k) or "").strip() for k in FP_FIELDS))
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def eid_of(posts):
    return FP2EID.get(fingerprint(posts))


def _dirs():
    """所有候选产物目录：runs/<EID> 与 out_n<N>（历史布局）。返回 [(eid, Path)]"""
    out = []
    if RUNS.is_dir():
        for d in sorted(RUNS.iterdir()):
            if d.is_dir():
                out.append((d.name, d))
    for d in sorted(ROOT.glob("out_n*")):
        suf = d.name[5:]
        if suf.isdigit():
            out.append(("EVT-%03d" % int(suf), d))
    return out


def _dir_of(eid):
    """按 event_id 找产物目录（runs/ 优先，其次 out_n<N>）"""
    p = RUNS / eid
    if p.is_dir():
        return p
    suf = eid[-3:]
    if suf.isdigit():
        p2 = ROOT / ("out_n%d" % int(suf))
        if p2.is_dir():
            return p2
    return p


def scan_runs():
    """建立 指纹 -> event_id 映射（历史布局也用 out_n<N>/_event_fingerprint.txt）"""
    for eid, d in _dirs():
        f = d / "_event_fingerprint.txt"
        if f.is_file():
            FP2EID[f.read_text(encoding="utf-8").strip()] = eid


def write_fingerprint(eid, posts):
    d = _dir_of(eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "_event_fingerprint.txt").write_text(fingerprint(posts), encoding="utf-8")
    FP2EID[fingerprint(posts)] = eid


# ── 管线 ────────────────────────────────────────────────────────────────
def sh(args, log):
    t0 = time.time()
    p = subprocess.run([PY] + args, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    ok = p.returncode == 0
    log.append("  %s %-24s %5.1fs" % ("OK " if ok else "FAIL", args[0], time.time() - t0))
    if not ok:
        log.append("       " + ((p.stderr or p.stdout or "").strip().split("\n")[-1])[:180])
    return ok


STEPS = [
    ("① 建图 / 定槽位 / 选人", lambda ev, out, eid: ["run.py", "--event", ev, "--personas", str(PERSONAS),
        "--seed", str(SEED), "--total", "auto", "--t0-mode", "days", "--t0-days", str(T0_DAYS), "--out", out]),
    ("② 相关度", lambda ev, out, eid: ["tools/compute_relevance.py", "--roster", "%s/downstream/%s_roster.json" % (out, eid),
        "--kg", "%s/kg/event_kg_%s.json" % (out, eid), "--t0", "%s/downstream/%s_t0_snapshot.json" % (out, eid),
        "--event", ev, "--personas", str(PERSONAS), "--out", "%s/relevance" % out]),
    ("③ t0 初始化", lambda ev, out, eid: ["tools/t0_init.py", "--roster", "%s/downstream/%s_roster.json" % (out, eid),
        "--kg", "%s/kg/event_kg_%s.json" % (out, eid), "--event-pack", "%s/packs/event_pack.json" % out,
        "--personas", str(PERSONAS), "--out", "%s/downstream/%s_t0_init.json" % (out, eid)]),
    ("④ t0 真实人物", lambda ev, out, eid: ["tools/t0_real_personas.py", "--kg", "%s/kg/event_kg_%s.json" % (out, eid),
        "--t0", "%s/downstream/%s_t0_snapshot.json" % (out, eid), "--pack", "%s/packs/event_pack.json" % out,
        "--out", "%s/downstream/%s_t0_real_personas.json" % (out, eid)]),
    ("⑤ 组队校验", lambda ev, out, eid: ["tools/group_check.py", "--roster", "%s/downstream/%s_roster.json" % (out, eid),
        "--personas", str(PERSONAS), "--t0-init", "%s/downstream/%s_t0_init.json" % (out, eid),
        "--out", "%s/report/group_check.json" % out]),
    ("⑥ 打包交付", lambda ev, out, eid: ["tools/package_delivery.py", "--run-dir", out, "--personas", str(PERSONAS),
        "--out", "%s/交付包" % out, "--event-key", eid, "--event-name", KNOWN.get(eid, eid)]),
]


def ensure_package(eid):
    """按需打包：out_nN / runs 里只有 downstream/ 时，现跑一次 package_delivery"""
    d = _dir_of(eid)
    f = d / "交付包" / "D_Agent名单.json"
    if f.is_file():
        return f
    if not (d / "downstream" / ("%s_roster.json" % eid)).is_file():
        return None
    subprocess.run([PY, "tools/package_delivery.py", "--run-dir", str(d), "--personas", str(PERSONAS),
                    "--out", str(d / "交付包"), "--event-key", eid, "--event-name", KNOWN.get(eid, eid)],
                   cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return f if f.is_file() else None


def run_pipeline(posts, name, job_id):
    """跑全链路。返回 event_id。"""
    log = []
    try:
        fp = fingerprint(posts)
        # ① 已知事件 -> 直接命中缓存
        with LOCK:
            hit = FP2EID.get(fp)
        if hit:
            JOBS[job_id].update(status="done", pct=100, event_id=hit,
                                msg="命中已知事件缓存（未重跑，保证结果一致）")
            return hit
        # ② 新事件 -> 分配编号
        with LOCK:
            used = {int(x[-3:]) for x in KNOWN if x[-3:].isdigit()}
            used |= {int(d.name[-3:]) for d in RUNS.iterdir() if d.is_dir() and d.name[-3:].isdigit()} if RUNS.is_dir() else set()
            nxt = max(used or {0}) + 1
        eid = "EVT-%03d" % nxt
        out = _dir_of(eid)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        # ★ 关键：run.py 从【事件 JSON 里的 event_id 字段】取产物编号，不是命令行参数。
        #   不注入的话产物会叫 EVT-NA，下游按 EVT-013 找不到（踩过）。
        _num = int(eid[-3:])
        posts2 = [dict(p, event_id=p.get("event_id") or _num) for p in posts]
        ev = out / "_input_event.json"
        ev.write_text(json.dumps(posts2, ensure_ascii=False, indent=1), encoding="utf-8")
        with LOCK:
            KNOWN[eid] = name or eid
        JOBS[job_id].update(event_id=eid, msg="开始跑管线")
        tot = len(STEPS)
        for i, (label, mk) in enumerate(STEPS):
            with LOCK:
                JOBS[job_id].update(pct=int(i * 100 / tot), msg=label)
            ok = sh(mk(str(ev), str(out), eid), log)
            if not ok and label != "⑤ 组队校验":        # 组队校验是告警级
                with LOCK:
                    JOBS[job_id].update(status="error", msg="%s 失败" % label, log="\n".join(log))
                return eid
        # ★ 生成前端所需的数据（与前端 const D 同构）
        rc = subprocess.run([PY, "tools/build_frontend_data.py", "--run", str(out), "--event-key", eid,
                             "--name", KNOWN.get(eid, eid), "--out", str(out / "_frontend_event.json")],
                            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        log.append("  %s 前端数据" % ("OK " if rc.returncode == 0 else "FAIL"))
        write_fingerprint(eid, posts)
        with LOCK:
            JOBS[job_id].update(status="done", pct=100, msg="完成", log="\n".join(log))
        return eid
    except Exception:
        with LOCK:
            JOBS[job_id].update(status="error", msg="内部错误", log=traceback.format_exc()[-800:])
        return None


# ── HTTP ────────────────────────────────────────────────────────────────
CT = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
      ".csv": "text/csv; charset=utf-8", ".md": "text/markdown; charset=utf-8",
      ".zip": "application/zip"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, p: Path, download=False):
        if not p.is_file():
            return self._send(404, {"error": "not found", "path": str(p.relative_to(ROOT))})
        b = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CT.get(p.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % p.name)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        try:
            if path in ("/", "/index.html"):
                return self._file(ROOT / "frontend" / "mirofish.html")
            if path == "/api/health":
                return self._send(200, {"ok": True, "known": len(KNOWN), "cached": len(FP2EID),
                                        "personas": PERSONAS.is_file(), "runs": str(RUNS)})
            if path == "/api/events":
                return self._send(200, {"events": [{"event_id": k, "name": v,
                    "cached": (_dir_of(k) / "downstream" / ("%s_roster.json" % k)).is_file()} for k, v in sorted(KNOWN.items())]})
            if path.startswith("/api/job/"):
                jid = path[len("/api/job/"):]
                with LOCK:
                    j = JOBS.get(jid)
                return self._send(200, j) if j else self._send(404, {"error": "unknown job"})
            m = re.match(r"^/api/(result|delivery|t0|full|kg)/([A-Za-z0-9_\-]+)$", path)
            if m:
                kind, eid = m.group(1), m.group(2)
                if not re.match(r"^[A-Za-z0-9_\-]+$", eid):
                    return self._send(400, {"error": "bad event id"})
                base = _dir_of(eid)
                if kind == "result":
                    f = base / "_frontend_event.json"
                    if not f.is_file():      # 已知事件：现算一次并落盘
                        subprocess.run([PY, "tools/build_frontend_data.py", "--run", str(base),
                            "--event-key", eid, "--name", KNOWN.get(eid, eid), "--out", str(f)],
                            cwd=str(ROOT), capture_output=True,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                    return self._file(f)
                if kind == "delivery":
                    f = ensure_package(eid)
                    if not f:
                        return self._send(404, {"error": "该事件尚无交付件（roster 缺失）", "event_id": eid})
                    return self._file(f, download=False)
                if kind == "t0":
                    return self._file(base / "downstream" / ("%s_t0_snapshot.json" % eid))
                if kind == "full":
                    ensure_package(eid)
                    return self._file(base / "交付包" / "_中间产物" / "D_Agent名单_完整.json")
                if kind == "kg":
                    return self._file(base / "kg" / ("event_kg_%s.json" % eid))
            if path.startswith("/runs/"):
                return self._file(ROOT / path.lstrip("/"))
            return self._send(404, {"error": "not found", "path": path})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        except Exception as e:
            return self._send(400, {"error": "读取请求体失败: %s" % e})
        if u.path != "/api/run":
            return self._send(404, {"error": "not found"})
        # 解析事件
        try:
            body = json.loads(raw)
        except Exception as e:
            return self._send(400, {"error": "不是合法 JSON: %s" % e})
        posts = body.get("event") if isinstance(body, dict) else body
        name = (body.get("name") if isinstance(body, dict) else "") or ""
        if not isinstance(posts, list) or not posts:
            return self._send(400, {"error": "事件必须是【非空 JSON 数组】"})
        need = ["post_id", "content", "channel", "publish_time", "author_name"]
        miss = [k for k in need if k not in posts[0]]
        if miss:
            return self._send(400, {"error": "缺少必需字段: %s" % "、".join(miss),
                                    "required": need, "got": sorted(posts[0].keys())[:20]})
        jid = hashlib.sha256((raw + str(time.time())).encode()).hexdigest()[:16]
        with LOCK:
            JOBS[jid] = {"status": "running", "pct": 0, "msg": "已接收", "event_id": None,
                         "n_posts": len(posts), "started": time.time(), "log": ""}
        threading.Thread(target=run_pipeline, args=(posts, name, jid), daemon=True).start()
        return self._send(200, {"job_id": jid, "n_posts": len(posts),
                                "fingerprint": fingerprint(posts)[:16]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    a = ap.parse_args()
    scan_runs()
    # 给已知 12 事件补指纹（首次运行时）
    for eid in KNOWN:
        f = _dir_of(eid) / "_event_fingerprint.txt"
        if not f.is_file():
            src = ROOT / "examples" / ("evt%d.json" % int(eid[-3:]))
            if src.is_file():
                try:
                    write_fingerprint(eid, json.loads(src.read_text(encoding="utf-8")))
                except Exception:
                    pass
    print("=" * 72)
    print("  GraphRAG 上游 · 端到端服务")
    print("=" * 72)
    print("  前端界面     http://%s:%d/" % (a.host, a.port))
    print("  已知事件 %d 个 ｜ 已建指纹 %d 个 ｜ 画像库 %s" % (
        len(KNOWN), len(FP2EID), "OK" if PERSONAS.is_file() else "缺失!"))
    print()
    print("  前端接口   POST /api/run            提交事件 JSON")
    print("             GET  /api/job/<job_id>   轮询进度")
    print("             GET  /api/result/<eid>   完整结果")
    print("             GET  /api/events         已知事件清单")
    print("  下游接口   GET  /api/delivery/<eid> D_Agent名单（14 字段）")
    print("             GET  /api/t0/<eid>       t0 状态快照（事件脉络）")
    print("             GET  /api/full/<eid>     完整件（69 字段）")
    print()
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
