# -*- coding: utf-8 -*-
r"""前端自检：真起服务把静态页取下来，逐项断言。

    python -I _selftest_static.py

查三类问题（都不需要浏览器）：
  ① 路由与 MIME —— `/` `/run` `/report` `/style.css` `/app.js` 都得 200 且类型对；
  ② **JS 语法** —— 页面里的每段内联 `<script type="module">` 与 `app.js` 都过一遍
     `node --check`（写成 `.mjs` 再查，模块语法才算数）；
  ③ **选择器绑定** —— 脚本里每个 `$('#x')` / `mount('#x')` 的 id，HTML 里都得存在。
     这是前端最容易错、又最不容易被发现的一类（改名漏改一处 = 整页白屏）。
"""
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app        # noqa: E402
import paths      # noqa: E402

PORT = 5098
BASE = "http://127.0.0.1:%d" % PORT
NODE = shutil.which("node") or r"D:\Program Files\nodejs\node.exe"
fails = []


def check(cond, label, extra=""):
    print("%s %s%s" % ("\u2714" if cond else "\u2718", label, ("   " + extra) if extra else ""))
    if not cond:
        fails.append(label)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


# ── node 语法检查 ─────────────────────────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="simfe_")


def node_check(src, label):
    p = os.path.join(_tmp, "c.mjs")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    r = subprocess.run([NODE, "--check", p], capture_output=True, text=True)
    err = (r.stderr or "").strip().splitlines()
    check(r.returncode == 0, label, err[-1][:120] if err else "")
    return r.returncode == 0


# ── id 绑定核对 ───────────────────────────────────────────────────────────
SEL = re.compile(r"""[\$(]?\s*\$\(\s*['"]#([A-Za-z0-9_-]+)['"]\s*\)""")
SEL2 = re.compile(r"""mount\(\s*['"]#([A-Za-z0-9_-]+)['"]""")
ID = re.compile(r"""\bid=["']([A-Za-z0-9_-]+)["']""")


def audit_ids(html, label):
    have = set(ID.findall(html))
    want = set(SEL.findall(html)) | set(SEL2.findall(html))
    missing = sorted(want - have)
    check(not missing, label, ("缺 id: " + "、".join(missing)) if missing else
          "%d 个选择器全部有落点" % len(want))


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), app.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("=" * 78)
    print("前端自检 ｜ %s" % BASE)
    print("=" * 78)

    print("\n[1] 路由与 MIME")
    probe = [("/", "text/html"), ("/index.html", "text/html"),
                       ("/run", "text/html"), ("/report", "text/html"),
                       ("/sim", "text/html"),
                       ("/collect", "text/html"),
                       ("/demo", "text/html"), ("/demo/", "text/html"),
                       ("/demo/step2/", "text/html"),
                       ("/demo/step3/", "text/html"),
                       ("/demo/step3/report.html", "text/html"),
                       ("/demo/step5/", "text/html"),
                       ("/style.css", "text/css"), ("/app.js", "text/javascript"),
                       ("/sim.css", "text/css"), ("/sim.js", "text/javascript"),
                       ("/demo/demo.css", "text/css"), ("/demo/demo.js", "text/javascript"),
                       ("/demo/step2/step2.css", "text/css"),
                       ("/demo/step2/step2.js", "text/javascript"),
                       ("/demo/step3/step3.css", "text/css"),
                       ("/demo/step3/step3.js", "text/javascript"),
                       ("/demo/step5/step5.css", "text/css"),
                       ("/demo/step5/step5.js", "text/javascript")]
    # ECharts 是旁挂还是内联，见 [3d]；只有真发了这个文件才查它这一条
    if os.path.isfile(os.path.join(paths.STATIC_DIR, "demo", "step3", "echarts.min.js")):
        probe.append(("/demo/step3/echarts.min.js", "javascript"))
    for path, want in probe:
        try:
            s, ct, body = get(path)
            check(s == 200 and want in ct and len(body) > 100,
                  "GET %s" % path, "%d · %s · %d B" % (s, ct, len(body)))
        except Exception as e:
            check(False, "GET %s" % path, str(e)[:80])

    # ★ `/demo` 必须**重定向**到 `/demo/`，不能就地返回一页。
    #   页面之间的跳转全是相对链接（`./step2/`、`../`），而相对链接按"当前目录"
    #   解析：url 少了末尾斜杠，浏览器就把当前目录认成上一级，`./step2/` 会跑到
    #   `/step2/`。这一条一旦丢，本机后端上点「构建知识图谱」就跳飞 ——
    #   而 GitHub Pages 那边（目录 url 天然带斜杠）却是好的，属于最难查的那种
    #   "只在一边坏"。
    print("\n[1b] /demo 必须重定向到 /demo/（相对链接的前提）")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        urllib.request.build_opener(_NoRedirect).open(BASE + "/demo", timeout=10)
        check(False, "GET /demo 是重定向", "居然就地返回了 —— 相对链接会跳飞")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") or ""
        check(e.code in (301, 302) and loc == "/demo/",
              "GET /demo → /demo/", "%d · Location: %s" % (e.code, loc or "（无）"))

    print("\n[2] 静态目录里该有的文件都在")
    for f in ("index.html", "run.html", "report.html", "app.js", "style.css",
              "sim.html", "sim.js", "sim.css",
              "collect.html",
              os.path.join("demo", "index.html"),
              os.path.join("demo", "demo.js"),
              os.path.join("demo", "demo.css"),
              os.path.join("demo", ".nojekyll"),
              os.path.join("demo", "step2", "index.html"),
              os.path.join("demo", "step2", "step2.js"),
              os.path.join("demo", "step2", "step2.css"),
              os.path.join("demo", "step3", "index.html"),
              os.path.join("demo", "step3", "step3.js"),
              os.path.join("demo", "step3", "step3.css"),
              os.path.join("demo", "step3", "report.html"),
              os.path.join("demo", "step5", "index.html"),
              os.path.join("demo", "step5", "step5.js"),
              os.path.join("demo", "step5", "step5.css")):
        check(os.path.exists(os.path.join(paths.STATIC_DIR, f)), "静态\\" + f)

    # ECharts 两种随包形态必居其一：并排放一份，或者已经内联进 report.html（判据见 [3d]）。
    _s3 = os.path.join(paths.STATIC_DIR, "demo", "step3")
    _side = os.path.join(_s3, "echarts.min.js")
    _inl = os.path.isfile(os.path.join(_s3, "report.html")) and \
        'data-vendor="echarts"' in open(os.path.join(_s3, "report.html"), encoding="utf-8").read()
    check(os.path.isfile(_side) or _inl, "静态\\demo\\step3 的 ECharts 随包可离线",
          "并排 %.1f KB" % (os.path.getsize(_side) / 1024.0) if os.path.isfile(_side) else "已内联进 report.html")

    print("\n[3] JS 语法（node --check）")
    if not os.path.exists(NODE):
        check(False, "找得到 node", NODE)
    else:
        ap = os.path.join(paths.STATIC_DIR, "app.js")
        node_check(open(ap, encoding="utf-8").read(), "app.js 语法")
        for page in ("index.html", "run.html", "report.html"):
            html = open(os.path.join(paths.STATIC_DIR, page), encoding="utf-8").read()
            blocks = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
            check(len(blocks) == 1, "%s 恰好一段内联模块" % page, "共 %d 段" % len(blocks))
            for i, b in enumerate(blocks):
                node_check(b, "%s 内联脚本语法" % page)

    print("\n[3b] 模块三页面（/sim）—— 离线自证 + 产物语法")
    sim_html = open(os.path.join(paths.STATIC_DIR, "sim.html"), encoding="utf-8").read()
    refs = re.findall(r'<(?:script|link)[^>]*?(?:src|href)="([^"]+)"', sim_html)
    check(sorted(refs) == ["/sim.css", "/sim.js"],
          "sim.html 只引用本地两个资源", "、".join(refs) or "（无）")
    for f in ("sim.js", "sim.css", "sim.html"):
        t = open(os.path.join(paths.STATIC_DIR, f), encoding="utf-8").read()
        # 判据是"浏览器真会去取"的形态；XML 命名空间串 / 许可证注释不算
        ext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+'
                         r'|url\(\s*["\']?https?://[^)]+'
                         r'|@import\s+(?:url\()?["\']?https?://[^)"\']+', t, re.I)
        check(not ext, "静态\\%s 无运行期外链" % f, "、".join(ext[:3]))
    if os.path.exists(NODE):
        node_check(open(os.path.join(paths.STATIC_DIR, "sim.js"), encoding="utf-8").read(),
                   "sim.js 语法")
        check(len(re.findall(r'<script type="module">', sim_html)) == 0,
              "sim.html 不含内联脚本（脚本全在 sim.js 里）")

    print("\n[3c] demo 各页 —— 每页三条硬不变量 + 一条跳转纪律")
    # ★ **目录结构 = 路由结构**：每一页住自己那一层，页面自己的 js/css 就放在同一层。
    #   于是 `./` 永远指自己这一页，`../` 永远是上一页，五页可以无限往下加。
    #   平铺的话 `../` 会退到站点根 —— 那是另一个页面，本机后端上就是运营台。
    # ★ 目录名跟着**工作流步骤号**走，不是页数：05 深度互动住 `step5\`，
    #   `step4\` 空着留给还没拆出来的「报告生成」（它的内容目前在第 03 页里）。
    #   所以这里**不能**假设"目录就是 01..0N 连号"。
    DEMO_PAGES = [("demo", "demo"), ("demo/board", "board"), ("demo/step2", "step2"),
                  ("demo/step3", "step3"), ("demo/step5", "step5")]

    for sub, key in DEMO_PAGES:
        here = os.path.join(paths.STATIC_DIR, *sub.split("/"))
        html = open(os.path.join(here, "index.html"), encoding="utf-8").read()
        js = open(os.path.join(here, key + ".js"), encoding="utf-8").read()

        # ① **相对**路径，而且指向的是**本层**那两个资源。
        #    这是"所有人都打得开"的实现方式：挂在本机 `/demo/…` 能开，整包丢到
        #    GitHub Pages 能开，双击 html（file://）也能开。写成 `/demo.js` 只有
        #    第一种能开 —— 而后端没起、或被挪走时，双击是 demo 唯一的活路。
        #    （内联 favicon 是 `data:` 开头的，不是外链也不是文件，剔掉。）
        refs = [r for r in re.findall(r'<(?:script|link)[^>]*?(?:src|href)="([^"]+)"', html)
                if not r.startswith("data:")]
        check(sorted(refs) == ["./" + key + ".css", "./" + key + ".js"],
              "%s 只引用本层两个**相对**资源" % sub, "、".join(refs) or "（无）")
        check(len(re.findall(r'<script type="module">', html)) == 0,
              "%s 不含 module 脚本（file:// 下会被 CORS 拦）" % sub)

        # ② **在线版：这几页真的调后端，但只许调白名单里的接口**。
        #    看板 / 图谱 / 深度互动三页走 `/api/events/<KEY>/{board,graph,chat}`。
        #    ★ 第 01 页 2026-10-21 起也接了后端：顶上的「一键全链」面板先探
        #      `/api/health`，连不上就整块不渲染；连上了走
        #      `/api/events/<KEY>/pipeline`。所以这一页现在是 LIVE，
        #      而且 `/api/health` 是**唯一**一个不带事件前缀的允许项。
        #    ★ 第 03 页（回放）2026-10-21 起同样是 LIVE：轮次不再自己数，
        #      每 2 秒问 `/api/events/<KEY>/status?tail=0`，进度 = 后端
        #      `rounds_done / n_rounds`，跑完才去 `/report` 取报告。
        #      连不上就退回原来那个按秒推进的内置时间轴（`file://` 仍可看）。
        #    五页全都**失败回落内置数据** —— `file://` 双击照样能看。
        for f in (key + ".js", key + ".css", "index.html"):
            t = open(os.path.join(here, f), encoding="utf-8").read()
            ext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+'
                             r'|url\(\s*["\']?https?://[^)]+'
                             r'|@import\s+(?:url\()?["\']?https?://[^)"\']+', t, re.I)
            check(not ext, "%s\\%s 无运行期外链" % (sub.replace("/", "\\"), f),
                  "、".join(ext[:3]))
        apis = sorted(set(re.findall(r"/api/[A-Za-z0-9_/.\-]*", js)))
        LIVE = {"demo": True, "demo/board": True, "demo/step2": True,
                "demo/step3": True, "demo/step5": True}
        if LIVE.get(sub):
            # 允许两类：事件级 `/api/events/<KEY>/…`，以及**不带事件前缀**的三条公共接口 ——
            # `/api/health`（第 01 页探活）、`/api/events`（顶栏选择器列场次）。
            # 除这三条以外出现任何其它 `/api/…` 都算越界。
            ok_api = all(a.startswith("/api/events/") or a.rstrip("/") in
                         ("/api/health", "/api/events") for a in apis)
            check(bool(apis) and ok_api,
                  "%s\\%s 只调事件级接口（+ /api/health、/api/events）"
                  % (sub.replace("/", "\\"), key + ".js"),
                  "、".join(apis[:4]) or "（一个都没有）")
        else:
            check(not apis, "%s\\%s 不调任何接口（这一页是纯演示）" % (sub.replace("/", "\\"), key + ".js"),
                  "、".join(apis[:4]))

        # 兜底：无论调不调接口，**网络调用只许出现在在线版那三页**里。
        if not LIVE.get(sub):
            check(not re.search(r"fetch\s*\(|XMLHttpRequest|WebSocket", js),
                  "%s\\%s 没有任何网络调用" % (sub.replace("/", "\\"), key + ".js"))

        # ③ **同一层的挂载点**：#app 必须在本页自己的壳里。
        #    改名漏改一处 = 整页白屏，而且只有打开那一页才看得见。
        mounts = set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', js))
        have = set(ID.findall(html))
        check(bool(mounts) and mounts <= have,
              "%s\\%s 的挂载点在本页壳里" % (sub.replace("/", "\\"), key + ".js"),
              "挂载点 %s ／ 壳里有 %s" % (sorted(mounts), sorted(have)))

        if os.path.exists(NODE):
            node_check(js, "%s\\%s 语法" % (sub.replace("/", "\\"), key + ".js"))

    # ④ **跳转纪律**：页与页之间只许用相对路径。
    #    绝对跳转（`/step3/`）只在 Pages 或只在本机对；退过了头（`../../`）会越过
    #    上一层 —— 两种写法都会让"本机后端"和"GitHub Pages"表现不一致，
    #    属于最难查的 bug（只在一边坏，另一边完全正常）。
    #
    # ★★ 2026-10-13 这条**改成"真解析一遍、看落点存不存在"**。改之前是一张
    #    `ALLOWED` 白名单，只审"写法合不合法"、不看"跳到哪儿去了" ——
    #    于是漏掉了这个真 bug：第 02 页写 `./step3/`，从 `/demo/step2/` 出发
    #    解析成 `/demo/step2/step3/`，**404**。写法完全合法（`./` 开头、只退零层），
    #    白名单放行。而 01→02 的 `./step2/` 从 `/demo/` 出发**恰好**是对的
    #    （01 页就在根上）—— 一个对的样本把整条规则照绿了。
    #    ⇒ 审"写法"永远堵不住这类洞，必须**按页在树里的位置把人解析一遍**。
    # ★★ 2026-10-13 又补一条：**取"调用实参"而不是"所有字面量"**。
    #    跳转这一轮全收进了 `nav.js` 的 `go()`，六个目标写成一张**共享表**
    #    （`{'./step2/': …, '../': …}`）—— 于是**每个页面的包里都有全部六个键**。
    #    按"字面量"取就会把别的页的跳转算到本页头上：`demo.js`（第 01 页）里
    #    躺着 `'../'`，从 `demo/` 解析就是"退出 demo 根"，**四条全假红**。
    #    ⇒ 取**真被当成跳转用出去**的那些：调用括号里紧跟的相对串。
    #      实测取出来正好六条，且各归各页：01→`./step2/`；02→`../` + `../step3/`；
    #      03→`../step2/` + `../step5/`；05→`../step3/`。
    #      （`src="./report.html"` 那种是 JSX 属性、不是调用，天然不会被取到。）
    ABS_JUMP = re.compile(r'["\'](/(?!api/)[A-Za-z][A-Za-z0-9_/-]*)["\']')
    CALL_JUMP = re.compile(r'\(\s*"(\.\.?/[A-Za-z0-9_./-]*)"')
    for sub, key in DEMO_PAGES:
        js = open(os.path.join(paths.STATIC_DIR, *sub.split("/"), key + ".js"),
                  encoding="utf-8").read()
        hops = sorted(set(CALL_JUMP.findall(js)))

        # ① 绝对路径一律不许（`/api/…` 除外 —— demo 本来就不该有，但那条另有专查）
        absj = sorted(set(ABS_JUMP.findall(js)))
        check(not absj, "%s 里没有绝对路径跳转" % sub,
              ("、".join(absj)) if absj else "（没有）")

        # ② 每个相对目标**真的解析一遍**：拿这一页所在的目录当基准，
        #    逐段拼接（`..` 退一段），最后落点必须真的存在 —— 目录要带
        #    `index.html`，文件要真的在。这一条才是"跳得通"的判据。
        #    ★ "目录要带 index.html"这一句**不再是习惯，是代码本身**：
        #      `nav.js` 的 `withIndex()` 给目录形态的落点补上 `index.html`
        #      （补了 `file://` 双击才翻得动页 —— 浏览器不会把 `step2/` 落到
        #      index.html 上，给的是目录列表）。这里跟着一起判，两边口径一致。
        bad, seen = [], []
        for h in hops:
            segs = sub.split("/")         # 例如 ["demo", "step2"]
            dead = None
            for part in h.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if len(segs) <= 1:
                        dead = "退出了 demo 根"
                        break
                    segs.pop()
                else:
                    segs.append(part)
            target = os.path.join(paths.STATIC_DIR, *segs)
            if dead is None:
                if os.path.isdir(target):
                    if not os.path.isfile(os.path.join(target, "index.html")):
                        dead = "是目录但没有 index.html"
                elif not os.path.isfile(target):
                    dead = "落点不存在"
            seen.append("%s → %s" % (h, "、".join(segs)))
            if dead:
                bad.append("%s（%s）" % (h, dead))
        check(not bad, "%s 的每个相对跳转都真的落得到" % sub,
              ("★ " + "；".join(bad)) if bad else ("；".join(seen) or "（无跳转）"))

        # ③ 不许从 demo 树里跳出去（`../../` 那种会落到站点根 = 运营台）
        out = [h for h in hops if h.startswith("../../")]
        check(not out, "%s 没有跳出 demo 树" % sub, "、".join(out) or "（没有）")

    # ⑤ 第 03 页的**回放物料**：报告 + ECharts 都随包发，一个外链都不许有。
    #    这一页在 `/sim` 上是接后端的（轮询 /api/…、报告走后端路由），
    #    搬到 demo 就得把**已经跑完的那一次**的结果烤进包里，否则整包丢到
    #    GitHub Pages 上这一页必然 404。物料对不对，这里按字节验。
    print("\n[3d] 第 03 页的回放物料（报告 + ECharts）")
    s3 = os.path.join(paths.STATIC_DIR, "demo", "step3")
    raw = open(os.path.join(s3, "report.html"), "rb").read()
    rep = raw.decode("utf-8")

    # ★ 打包有**两种**合法形态，`build.mjs` 的 packReport 按源文件自己选：
    #     旁挂式  report.html 只把那行 CDN 改成本层路径，ECharts 另存一份并排放
    #     自包含  源报告已经把 ECharts 内联（`data-vendor="echarts"`）⇒ 原样照抄，不另存
    #   两种都得认 —— 否则"换掉报告源"会把这里判红，可红的是自检，不是包。
    inline = 'data-vendor="echarts"' in rep

    # ① 报告里的引用：旁挂式只许有本层那一个 echarts，自包含则一个都不许有。
    rrefs = re.findall(r'<(?:script|link)[^>]*?(?:src|href)="([^"]+)"', rep)
    if inline:
        check(not rrefs, "report.html 自包含，不引用任何外部脚本", "、".join(rrefs) or "（无）")
        check(len(raw) > 500 * 1024, "ECharts 已内联进 report.html",
              "%.1f KB" % (len(raw) / 1024.0))
    else:
        check(sorted(rrefs) == ["./echarts.min.js"],
              "report.html 只引用本层 ./echarts.min.js", "、".join(rrefs) or "（无）")
        side = os.path.join(s3, "echarts.min.js")
        check(os.path.isfile(side) and os.path.getsize(side) > 500 * 1024,
              "echarts.min.js 在位",
              "%.1f KB" % (os.path.getsize(side) / 1024.0) if os.path.isfile(side) else "缺文件")
    rext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+'
                      r'|url\(\s*["\']?https?://[^)]+'
                      r'|@import\s+(?:url\()?["\']?https?://[^)"\']+', rep, re.I)
    check(not rext, "report.html 无运行期外链", "、".join(rext[:3]))

    # ② 报告是**真跑出来的那一份**，不是另写的。判据是**逐位**。
    #    ★ 2026-10-21：回放源从"事件目录里那份"换成"满规则重跑那一份"（见
    #      `demo/step3/replay.js` 头部的取数口径），而那一份自身是自包含的。
    #      所以比对基准跟着打包形态走：
    #        旁挂式 → 把源里那行 CDN 换成本层路径，再与 demo 报告比字节
    #        自包含 → 直接与源报告比字节（一个字节都不许变）
    #      源报告找不到（比如只拷了运行包出去）就退化成结构核对，不硬判红。
    src_bytes, src_html, why = None, "", ""
    if inline:
        base = os.path.dirname(os.path.dirname(paths.ROOT))   # …\端到端程序与输出数据
        cand = glob.glob(os.path.join(base, "02_事件输出数据", "EVT-001",
                                      "07_满规则重跑_*", "05_报告", "*.html"))
        if cand:
            src_html = max(cand, key=os.path.getmtime)
            src_bytes = open(src_html, "rb").read()
        else:
            why = "（没找到回放源，只做结构核对）"
    else:
        try:
            cfg = paths.load_config("EVT-001")
            src_html = os.path.join(os.path.dirname(cfg["_path"]), cfg["out_html"])
            src_bytes = open(src_html, "rb").read().replace(
                b'<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>',
                b'<script src="./echarts.min.js"></script>')
        except Exception as e:
            why = "（取不到源：%s）" % str(e)[:60]

    if src_bytes is not None:
        check(raw == src_bytes,
              "report.html 与回放源逐位相同" + ("（自包含原样照抄）" if inline else "（只差被换掉的那行 ECharts）"),
              "%d B ／ 源 %s %d B" % (len(raw), os.path.basename(src_html), len(src_bytes)))
    else:
        check(inline and b'"rounds":42' in raw,
              "report.html 是一份真跑出来的 EVT-001 报告", why)

    # ③ 口径：报告印的是 **42 轮**（R-50 等长 4h × 42）。数换了这里要跟着换，
    #    别让页面上的"42"和报告里的"36"各说各话。
    check('"rounds":42' in rep, "报告口径 = 42 轮", "meta.rounds 找到了")

    print("\n[3e] 采集控制台（/collect）—— 离线自证 + 接口边界")
    # 这一页和后端其他页不是一个来源：它调的是 `/api/collect/*`（采集层），
    # 不是 `/api/events/*`（事件级）。所以 [3c] 那张 `LIVE` 白名单管不了它，
    # 单独在这里立三条规矩：
    #   ① 零构建、零外链（PRD 内网化要求，断网也得能开）
    #   ② 用到的接口**逐个对得上路由表** —— 少注册一条，页面就静默变灰
    #   ③ 只许碰采集层自己的五条，**不许**伸手到事件级接口去
    ch = open(os.path.join(paths.STATIC_DIR, "collect.html"), encoding="utf-8").read()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", ch, re.S)
    check(len(blocks) == 1, "collect.html 恰好一段内联脚本", "共 %d 段" % len(blocks))
    crefs = [r for r in re.findall(r'<(?:script|link)[^>]*?(?:src|href)="([^"]+)"', ch)
             if not r.startswith("data:")]
    check(not crefs, "collect.html 不加载任何外部文件（零构建）",
          "、".join(crefs) or "（无）")
    cext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+'
                      r'|url\(\s*["\']?https?://[^)]+'
                      r'|@import\s+(?:url\()?["\']?https?://[^)"\']+', ch, re.I)
    check(not cext, "collect.html 无运行期外链（内网可开）", "、".join(cext[:3]))
    # 这一页的 `$` 是 `id => document.getElementById(id)`，取的是**裸 id 不带 #**，
    # 用不了 [4] 那个按 `$('#x')` 写的 `audit_ids`（它会一个都取不到、然后假绿）。
    cwant = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', ch))
    cmissing = sorted(cwant - set(ID.findall(ch)))
    check(cwant and not cmissing, "collect.html 的 %d 个挂载点都在壳里" % len(cwant),
          ("缺 id: " + "、".join(cmissing)) if cmissing else "（全部有落点）")
    if os.path.exists(NODE):
        for i, b in enumerate(blocks):
            node_check(b, "collect.html 内联脚本语法")

    # ② 接口边界：**七条采集路由白名单**。多一条就说明有人把事件级接口写进来了。
    #    2026-10-21 加了 job/open + job/collect（新事件的检索作业：开单 / 收单）。
    COLLECT_ROUTES = {"/api/collect/status", "/api/collect/card", "/api/collect/run",
                      "/api/collect/job/open", "/api/collect/job/collect",
                      "/api/collect/log", "/api/collect/preview"}
    clits = sorted(set(re.findall(r"/api/[A-Za-z0-9_/.\-]*", ch)))
    cout = [l for l in clits if l not in COLLECT_ROUTES]
    check(len(clits) >= 4 and not cout,
          "collect.html 的 %d 个 /api 调用都在采集层白名单里" % len(clits),
          ("越界：" + "、".join(cout)) if cout else "、".join(clits))
    croutes = {rx.pattern.strip("^$") for _, rx, _ in app.ROUTES}
    cmiss = [l for l in clits if l not in croutes]
    check(not cmiss, "collect.html 用到的接口都真的注册了",
          ("没注册：" + "、".join(cmiss)) if cmiss else "（全部对上）")

    print("\n[4] 选择器绑定（每个 #id 都有落点）")
    for page in ("index.html", "run.html", "report.html"):
        html = open(os.path.join(paths.STATIC_DIR, page), encoding="utf-8").read()
        audit_ids(html, "%s 的 id 绑定" % page)
    # 模块三是编译产物，选择器在 bundle 里 —— 只核它的挂载点
    sim_js = open(os.path.join(paths.STATIC_DIR, "sim.js"), encoding="utf-8").read()
    mounts = set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', sim_js))
    have = set(ID.findall(sim_html))
    check(bool(mounts) and mounts <= have, "sim.js 的挂载点都在 sim.html 里",
          "挂载点 %s ／ HTML 里有 %s" % (sorted(mounts), sorted(have)))
    # demo 各页的挂载点核对在 [3c] ③ —— 那里是按"页"循环的，五页加进来不用改这儿。

    print("\n[5] 页面里引用到的接口都真的注册了")
    routes = {rx.pattern.strip("^$").replace("([^/]+)", "X") for _, rx, _ in app.ROUTES}

    def hit(lit):
        # 页面里写成拼接（'/api/events/' + enc），取到的是半截 → 允许前缀匹配
        return lit.startswith("/api/events/") if lit.endswith("/") else lit in routes

    for page in ("index.html", "run.html", "report.html"):
        src = open(os.path.join(paths.STATIC_DIR, page), encoding="utf-8").read()
        src = re.sub(r"\$\{[^}]*\}", "X", src)      # 模板插值 → X
        src = re.sub(r"\benc\b", "X", src)          # enc = encodeURIComponent(EID)
        for lit in re.findall(r"""api\(\s*[`'"]([^`'"?]+)""", src):
            check(hit(lit), "%s 用到的接口 %s" % (page, lit))

    # bundle 里是 fetch + 模板串。抠 `/api/...` 时**必须把 ${…} 一起吃掉**，
    # 否则压缩后的 `\`/api/events/${enc(id)}/status?tail=0\`` 会被截成半截
    # `/api/events/`，检查看着绿其实什么都没查。
    lits = sorted(set(re.findall(
        r"/api/[A-Za-z0-9_/.\-]*(?:\$\{[^}]*\}[A-Za-z0-9_/.\-]*)*", sim_js)))
    norm = lambda l: re.sub(r"\$\{[^}]*\}", "X", l)   # ${enc(id)} → X
    miss = [l for l in lits if norm(l) not in routes]
    check(len(lits) >= 3 and not miss,
          "sim.js 的 %d 个 /api 调用都对得上路由" % len(lits),
          ("对不上：" + "、".join(miss)) if miss else "、".join(sorted(norm(l) for l in lits)))

    print("\n[6] 静态服务不许被路径穿越打穿")
    for bad in ("/../app.py", "/..%2fapp.py", "/%2e%2e/style.css"):
        try:
            s, ct, b = get(bad)
            check(False, "穿越被挡 %s" % bad, "竟然得 %d" % s)
        except urllib.error.HTTPError as e:
            check(e.code in (403, 404), "穿越被挡 %s" % bad, "得 %d" % e.code)

    srv.shutdown()
    shutil.rmtree(_tmp, ignore_errors=True)
    print("=" * 78)
    if fails:
        print("\u2718 不通过：%s" % "；".join(fails))
        sys.exit(1)
    print("\u2714 全部通过")


if __name__ == "__main__":
    main()
