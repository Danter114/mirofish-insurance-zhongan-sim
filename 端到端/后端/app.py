# -*- coding: utf-8 -*-
"""仿真推演后端 —— 零依赖 HTTP 服务。

    python 后端\\app.py                 # http://127.0.0.1:5001
    python 后端\\app.py --port 8080 --host 0.0.0.0

为什么不用 Flask
----------------
不是不能用，是**没必要**。整个交付物（引擎、报告、这个后端）到目前是**纯标准库**：
`python app.py` 就跑，评审方不需要 `pip install` 任何东西。Flask 省掉的是两百行路由样板，
代价是给交付物加一条安装步骤和一个版本约束。这笔账在这个规模上不划算。
（真要换回 Flask，改的只是本文件最外层的分发那几十行，业务代码一行不动。）

线程模型
--------
`ThreadingHTTPServer`：每个请求一个线程。仿真本身跑在**独立子进程**里（见 `jobs.py`），
HTTP 线程只负责读状态 / 转日志尾 —— 所以长任务不会把服务卡住。

安全
----
只监听 `127.0.0.1`（除非显式 `--host`）。**任何接口都不会回显 LLM 凭据明文** ——
`llm` 段只回"哪个键填了"的布尔量（见 `mask_llm`）。
"""
import argparse
import io
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 只在**还不是 UTF-8** 时才包一层。无脑重包会踩一个坑：谁先包、谁被 GC 时
# 就把底层 buffer 关掉 —— 后面那个 wrapper 直接 "I/O operation on closed file"。
if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import jobs        # noqa: E402
import paths       # noqa: E402
import precheck    # noqa: E402
import progress    # noqa: E402

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
    ".woff2": "font/woff2", ".map": "application/json",
}


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# ── 序列化助手 ────────────────────────────────────────────────────────────
def mask_llm(llm):
    """★ 只看"填没填"，**绝不回显** api_key 等任何明文。"""
    llm = llm or {}
    ep = (llm.get("env_path") or "").strip()
    return {
        "base_url_set": bool((llm.get("base_url") or "").strip()),
        "api_key_set": bool((llm.get("api_key") or "").strip()),
        "model_name_set": bool((llm.get("model_name") or "").strip()),
        "base_url": "",          # 有意留空：前端要展示的就用 base_url_set
        "model_name": "",
        "env_path": ep,
        "env_exists": bool(ep) and os.path.exists(ep),
    }


def config_view(cfg):
    """前端要看的配置面（不含任何凭据）。"""
    return {
        "event_id": cfg["event_id"],
        "event_brief": cfg["event_brief"],
        "t0_time": cfg["t0_time"],
        "round_hours": cfg["round_hours"],
        "n_rounds": cfg.n_rounds,
        "run_dir": cfg.run_dir,
        "persona_file": cfg["persona_file"],
        "config_path": cfg.get("_path") or "",
        "report_title": cfg.get("report_title", ""),
        "event_type": cfg.get("event_type", ""),
        "llm": mask_llm(cfg.llm),
    }


def event_summary(eid):
    """列表页一行所需的全部信息。**每个数字现算**。"""
    try:
        cfg = paths.load_config(eid)
    except paths.ConfigError as e:
        return {"event_id": eid, "broken": True, "error": str(e)}
    inv = progress.inventory(cfg)
    return {
        "event_id": eid,
        "event_type": cfg.get("event_type", ""),
        "t0_time": cfg["t0_time"],
        "n_rounds": cfg.n_rounds,
        "round_hours": cfg["round_hours"],
        "broken": False,
        **inv,
        "job": jobs.status(eid),
    }


# ── 事件注册 ──────────────────────────────────────────────────────────────
_TEMPLATE = {
    "event_id": "", "event_brief": "", "t0_time": "", "report_title": "",
    "doc_title": "", "eyebrow": "", "event_type": "", "event_type_source": "采集侧",
    # ★ 2026-10-21 第三十二轮（模块五改造）：报告标题改由**舆情主题**派生。
    #   `topic_title` = 主体 + 事件短语；`subject` = 涉事主体（模块五主线卡用）。
    #   注意 `party_entities` 是「被侵害方」清单，与 `subject` 不是一回事，别混填。
    "topic_title": "", "subject": "", "party_entities": [],
    "run_dir": ".", "persona_file": "清洗后画像.json",
    "t0_file": "t0状态_第0轮.json",
    "snapshot_pattern": "t{round}状态_第{round}轮.json",
    "t0_end_file": "t0轮末状态.json", "recorder_file": "round_snapshot.jsonl",
    "data_file": "_viz_data.json", "round_hours": 4, "n_rounds": 42,
    "channel_order": [], "llm": {"env_path": "", "base_url": "", "api_key": "", "model_name": ""},
    "out_html": "",
}


def create_event(body):
    eid = paths.safe_event_id(body.get("event_id"))
    if eid is None:
        raise ApiError(400, "event_id 不合法（字母数字下划线短横或中文，≤64 字符）")
    d = paths.event_dir(eid)
    if os.path.exists(os.path.join(d, "事件配置.json")):
        raise ApiError(409, "事件 %s 已经存在" % eid)
    if not (body.get("event_brief") or "").strip():
        raise ApiError(400, "event_brief 不能为空（它会作为【事件背景】进提示词）")
    if not (body.get("t0_time") or "").strip():
        raise ApiError(400, "t0_time 不能为空（格式 2026-05-22 22:00）")

    os.makedirs(d, exist_ok=True)
    cfg = dict(_TEMPLATE)
    cfg["event_id"] = eid
    cfg["event_brief"] = body["event_brief"].strip()
    cfg["t0_time"] = body["t0_time"].strip()
    cfg["report_title"] = (body.get("report_title") or "").strip() or ("%s 仿真推演报告" % eid)
    cfg["doc_title"] = (body.get("doc_title") or "").strip() or ("%s 多智能体仿真推演报告" % eid)
    cfg["eyebrow"] = (body.get("eyebrow") or "").strip() or ("Prediction Report · ID: %s" % eid)
    cfg["event_type"] = (body.get("event_type") or "").strip()
    cfg["event_type_source"] = (body.get("event_type_source") or "采集侧").strip()
    cfg["subject"] = (body.get("subject") or "").strip()
    cfg["party_entities"] = [str(x).strip() for x in (body.get("party_entities") or [])
                             if str(x).strip()]
    # ★ 给了舆情主题就由它派生标题（与 `_report_config.py` 同一格式串）；没给则保留上面那条。
    cfg["topic_title"] = (body.get("topic_title") or "").strip()
    if cfg["topic_title"]:
        cfg["report_title"] = "%s 舆情演化预测报告" % cfg["topic_title"]
    cfg["out_html"] = "%s_仿真运行可视化报告.html" % eid
    for k in ("n_rounds", "round_hours"):
        if body.get(k) is not None:
            cfg[k] = int(body[k])
    cfg["persona_file"] = (body.get("persona_file") or cfg["persona_file"]).strip()

    p = paths.config_path(eid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 预检要读配置，坏配置当场就报错 —— 但这里是新建，先让它写下去再看预检
    try:
        rep = precheck.run(paths.load_config(eid), progress.inventory(paths.load_config(eid)))
    except paths.ConfigError as e:
        rep = {"event_id": eid, "ok": False, "counts": {"error": 1, "warn": 0, "info": 0},
               "items": [{"level": "error", "code": "cfg.load", "title": "配置读不出来",
                          "detail": str(e)}]}
    return {"event_id": eid, "config_path": p, "precheck": rep}


def update_llm(eid, body):
    cfg = paths.load_config(eid)
    p = cfg["_path"]
    if not p:
        raise ApiError(400, "这个事件没有配置文件，改不了")
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    llm = dict(raw.get("llm") or {})
    for k in ("base_url", "model_name", "env_path"):
        if k in body:
            llm[k] = (body.get(k) or "").strip()
    # ★ api_key 单独处理：界面**从不回显**它，所以前端提交上来的空值不能当真删除 ——
    #   否则用户改个 model_name 就会顺手把钥匙清掉。只有显式 clear_api_key 才清。
    if "api_key" in body:
        v = (body.get("api_key") or "").strip()
        if v:
            llm["api_key"] = v
        elif body.get("clear_api_key"):
            llm["api_key"] = ""
    raw["llm"] = llm
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return {"event_id": eid, "llm": mask_llm(paths.load_config(eid).llm)}


# ── 路由表 ────────────────────────────────────────────────────────────────
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


# 在线版附加接口（看板 / 图谱 / 深度互动）。**必须在这里注册**：
# 只有 `app.py` 作为脚本直接跑时才成立，`api_live` 里反向 import 会拿到另一个
# 半初始化的模块对象，路由注册到不了真正的 ROUTES 里。
import api_live  # noqa: E402

api_live.register(route, ApiError)

# 采集层接口（参数卡 / 采集 / 日志）—— 同一个注册时机，理由同上。
# 采集层是子进程跑的：它的脚本在 `端到端\采集层\`，不 import，崩了不影响后端。
import collect_api  # noqa: E402

collect_api.register(route, ApiError)


def _q(query, key, default=None):
    v = query.get(key)
    return v[0] if v else default


@route("GET", r"/api/health")
def _(h, m, query, body):
    return {"ok": True, "root": paths.ROOT, "events": len(paths.list_events()),
            "python": sys.version.split()[0]}


@route("GET", r"/api/events")
def _(h, m, query, body):
    return {"events": [event_summary(e) for e in paths.list_events()]}


@route("POST", r"/api/events")
def _(h, m, query, body):
    return create_event(body or {})


@route("GET", r"/api/events/([^/]+)")
def _(h, m, query, body, eid):
    cfg = paths.load_config(eid)
    inv = progress.inventory(cfg)
    return {
        "event": event_summary(cfg["event_id"]),
        "config": config_view(cfg),
        "inventory": inv,
        "precheck": precheck.run(cfg, inv),
        "job": jobs.status(cfg["event_id"]),
    }


@route("POST", r"/api/events/([^/]+)/precheck")
def _(h, m, query, body, eid):
    cfg = paths.load_config(eid)
    return precheck.run(cfg, progress.inventory(cfg))


@route("POST", r"/api/events/([^/]+)/llm")
def _(h, m, query, body, eid):
    return update_llm(eid, body or {})


@route("POST", r"/api/events/([^/]+)/run")
def _(h, m, query, body, eid):
    cfg = paths.load_config(eid)
    rep = precheck.run(cfg, progress.inventory(cfg))
    if not rep["ok"]:
        errs = [i["title"] for i in rep["items"] if i["level"] == "error"]
        raise ApiError(400, "预检不通过，先修这些再跑：\n  · " + "\n  · ".join(errs))
    return jobs.start(eid, "sim")


@route("POST", r"/api/events/([^/]+)/stop")
def _(h, m, query, body, eid):
    return jobs.stop(eid)


@route("POST", r"/api/events/([^/]+)/report")
def _(h, m, query, body, eid):
    cfg = paths.load_config(eid)
    inv = progress.inventory(cfg)
    if not inv["t0_ready"]:
        raise ApiError(400, "连 t0 快照都没有，出不了报告（先放 `%s`）" % cfg["t0_file"])
    if inv["rounds_done"] < 1:
        raise ApiError(400, "一轮都还没跑，出不了报告")
    return jobs.start(eid, "report")


@route("GET", r"/api/events/([^/]+)/status")
def _(h, m, query, body, eid):
    cfg = paths.load_config(eid)
    inv = progress.inventory(cfg)
    st = jobs.status(eid)
    return {
        "job": st,
        "inventory": inv,
        "log_tail": jobs.tail(eid, None, int(_q(query, "tail", "200"))),
    }


@route("GET", r"/api/events/([^/]+)/report")
def _(h, m, query, body, eid):
    """报告 HTML 直出（前端用 iframe 嵌它）。"""
    cfg = paths.load_config(eid)
    inv = progress.inventory(cfg)
    p = inv["report_path"]
    if not (p and os.path.exists(p)):
        raise ApiError(404, "还没有报告。先在界面上点「生成报告」。")
    return _FileResponse(p, MIME[".html"])


@route("GET", r"/api/collect/preview")
def _(h, m, query, body):
    """采集产物（看板 HTML / raw JSON）直出。只放行 `_运行\\采集层\\` 下面的文件。"""
    p = collect_api.safe_output(_q(query, "path"))
    ext = os.path.splitext(p)[1].lower()
    return _FileResponse(p, MIME.get(ext) or "text/plain; charset=utf-8")


@route("GET", r"/api/events/([^/]+)/alert")
def _(h, m, query, body, eid):
    """报告层风险等级合成结果（模块三的红色预警弹窗读它）。

    真值只有一个来源：`viz_alert.py` 追加进 `_viz_data.json` 的 `alert` 块。
    拿不到就**报 404** —— 前端据此不弹窗。宁可不出，也绝不替它编一个等级出来。
    """
    cfg = paths.load_config(eid)
    d = cfg.get("data_file") or "_viz_data.json"
    p = os.path.join(os.path.dirname(cfg["_path"]), d)
    if not os.path.exists(p):
        raise ApiError(404, "还没有数据包 %s（先在界面上点「生成报告」）。" % d)
    try:
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
    except Exception as e:
        raise ApiError(500, "数据包读不出来：%s" % e)
    alert = blob.get("alert")
    if not alert:
        raise ApiError(404, "数据包里没有 alert 块（报告层还没跑 viz_alert.py）。")
    meta = blob.get("meta") or {}
    return {
        "event_id": cfg["event_id"],
        "alert": alert,
        "meta": {
            "t0_time": cfg.get("t0_time"),
            "round_hours": cfg.get("round_hours"),
            "n_rounds": cfg.get("n_rounds"),
            "rounds": meta.get("rounds"),
        },
    }


class _FileResponse:
    def __init__(self, path, ctype):
        self.path = path
        self.ctype = ctype


# ── HTTP 处理器 ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "MiroFishSim/1.0"
    protocol_version = "HTTP/1.1"
    # `--cors <origin>` 打开跨域（默认关）。取值 `*` 或某个具体 origin，
    # 由 `main()` 写进来；`_send` / `do_OPTIONS` 只读它。
    cors_origin = None

    # --- 输出 ---
    def _send(self, status, ctype, payload, extra=None):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # ★ 跨域：只在 `--cors` 开着、且是 `/api/` 的时候才发。
        #   默认**不开**：同源托管（后端顺带发页面）根本不需要它，
        #   而开了等于允许任何网页调这台机器的接口（`POST /pipeline` 会真起子进程）。
        if self.cors_origin and self.path.startswith("/api/"):
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            if self.cors_origin != "*":
                self.send_header("Vary", "Origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj, status=200):
        self._send(status, MIME[".json"], json.dumps(obj, ensure_ascii=False, indent=2))

    def _error(self, status, msg):
        self._json({"ok": False, "error": msg}, status)

    def _redirect(self, to, status=302):
        """把浏览器挪到 `to`。只用来补目录末尾那个斜杠（见 `/demo` 的注释）。"""
        self._send(status, "text/plain; charset=utf-8", "", {"Location": to})

    # --- 静态 ---
    def _static(self, rel):
        rel = rel.lstrip("/").replace("\\", "/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return self._error(403, "路径不合法")
        p = os.path.join(paths.STATIC_DIR, *parts)
        if os.path.isdir(p):
            p = os.path.join(p, "index.html")
        if not os.path.exists(p):
            return self._error(404, "没有这个文件：%s" % rel)
        with open(p, "rb") as f:
            data = f.read()
        return self._send(200, MIME.get(os.path.splitext(p)[1].lower(),
                                        "application/octet-stream"), data)

    # --- 分发 ---
    def _dispatch(self, method):
        u = urlparse(self.path)
        path, query = u.path, parse_qs(u.query)

        # 页面
        if method == "GET" and not path.startswith("/api/"):
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/run":
                return self._static("run.html")
            if path == "/report":
                return self._static("report.html")
            if path == "/sim":
                return self._static("sim.html")   # 模块三「仿真推演」界面（React 构建产物）
            if path == "/collect":
                return self._static("collect.html")   # 采集层控制台（零构建，纯 HTML+JS）
            if path == "/demo":
                # demo 五页的第一页。**必须把浏览器挪到 `/demo/`**，不能就地返回。
                #
                # 页面里的跳转全是**相对**链接（`./step2/`、`../`），而相对链接是
                # 相对"当前目录"解析的：url 是 `/demo` 时浏览器认为当前目录是 `/`，
                # `./step2/` 就跑到 `/step2/` 去了 —— 那是另一个地方。
                # 补上末尾斜杠，当前目录才是 `/demo/`，五个页面才对得上。
                #
                # 这条也是"本机后端"和"GitHub Pages"两种挂法行为一致的**前提**：
                # Pages 上 `/step2/` 这种目录 url 天然带斜杠，本机就靠这一行补齐。
                return self._redirect("/demo/")
            # `/demo/…` 与 `/demo/step2/…` 不用单独写：走下面的兜底
            # `_static(path)`，它会自己把目录补成 index.html。
            return self._static(path)

        body = None
        if method in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    return self._error(400, "请求体不是合法 JSON：%s" % e)

        for m, rx, fn in ROUTES:
            if m != method:
                continue
            mo = rx.match(path)
            if mo:
                try:
                    out = fn(self, method, query, body, *mo.groups())
                except paths.ConfigError as e:
                    return self._error(404, str(e))
                except jobs.JobError as e:
                    return self._error(409, str(e))
                except ApiError as e:
                    return self._error(e.status, e.msg)
                if isinstance(out, _FileResponse):
                    with open(out.path, "rb") as f:
                        return self._send(200, out.ctype, f.read())
                return self._json(out)
        return self._error(404, "没有这个接口：%s %s" % (method, path))

    def do_GET(self):
        try:
            self._dispatch("GET")
        except Exception:
            self._error(500, "服务端异常：\n" + traceback.format_exc())

    def do_POST(self):
        try:
            self._dispatch("POST")
        except Exception:
            self._error(500, "服务端异常：\n" + traceback.format_exc())

    # 跨域预检：`POST /api/…` 带 `Content-Type: application/json` 时浏览器会先发 OPTIONS。
    # 只有 `--cors` 才应答；没开就照常 404（等于明确告诉浏览器"这里不接跨域"）。
    def do_OPTIONS(self):
        if not self.cors_origin or not self.path.startswith("/api/"):
            return self._error(404, "没有这个接口：OPTIONS %s" % self.path)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        # 纯 ASCII：stderr 不走 UTF-8 包装，塞中文/中点会按控制台码页输出成乱码
        sys.stderr.write("  - %s %s\n" % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser(description="仿真推演后端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5001)
    # 前后端**分开部署**时用（页面在 GitHub Pages / 另一台机器，接口指过来）。
    # 同源托管不用给；不给 = 不接跨域，任何网页都调不动这台机器的接口。
    ap.add_argument("--cors", default=None, metavar="ORIGIN",
                    help="允许跨域的来源，例：https://someone.github.io 或 *（默认关）")
    a = ap.parse_args()
    Handler.cors_origin = (a.cors or "").strip() or None

    evs = paths.list_events()
    print("=" * 78)
    print("仿真推演后端")
    print("  根目录 :", paths.ROOT)
    print("  事件   :", "、".join(evs) if evs else "（无）")
    print("  监听   : http://%s:%d" % (a.host, a.port))
    print("  跨域   :", ("允许 %s（--cors）" % Handler.cors_origin) if Handler.cors_origin
          else "不接（同源托管；要跨域加 --cors <origin>）")
    print("=" * 78)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停服。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
