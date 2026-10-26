# -*- coding: utf-8 -*-
r"""跑端到端 v3 —— 采集 → 图谱（含 LLM 捏人）→ 仿真 → 报告。

与 v1（`初赛提交\端到端\跑端到端.py`）的差别，全部来自 2026-10-20 的两条定案：

  ① **真人必须 LLM 捏**：发帖者与当事人的人设由 LLM 写、冻结进
     `kgcopy\输入\llm_persona_cache.json`。图谱段自带一道「捏人闸」：
     缓存里缺人就停，绝不用规则模板顶上；跑完再从 `LLM捏人清单.json` 复核
     「每个 A 类真人是不是都 persona_llm=llm」。
  ② **事件类型以采集侧 8 类为准**：图谱侧不再自推。类型从
     `EVT-XXX_processed.json`/登记表取，并落成交付包旁的 sidecar；
     配置里的 `event_type_source` 必须 == "采集侧"。

据此链路重新分段（不再有独立的"桥"段 —— 桥接已并入图谱段的 `run_layer.py`，
它才是同时持有 build_t0_real_agents / reselect_v5 / bridge 的那一层）：

    采集  raw ──▶ event*.json                      [初始按 --raw，可选]
    图谱  event* ──▶ 交付包 + 三件仿真输入           [kgcopy\代码\run_layer.py]
          ├ 捏人闸：缓存缺人 ⇒ 停
          ├ 类型闸：event_type_source == 采集侧
          └ 捏人复核：LLM捏人清单.json 里 missing 为空、A 类全员 llm
    仿真  42 轮 × 4h                                [e2e_v3\引擎\跑全轮.py（合并版引擎）]
    报告  ──▶ 可视化报告                            [e2e_v3\报告\生成报告.py]
    前端  ──▶ board.json / graph.json                [前端数据\生成前端数据.py]
          后端 `/api/events/<KEY>/board|graph` 直出的就是这两个文件 ——
          跑完这一段，在线版的看板 / 图谱 / 回放三页当场能看到这一场。

用法：
    python 跑端到端_v3.py --key EVT-002 --event <kgcopy\输入\event2.json>
    python 跑端到端_v3.py --key EVT-013 --raw raw13.json --id 13 --title "…" --t0 2026-06-01 --subject 众安保险
    python 跑端到端_v3.py --key EVT-002 --event … --to 报告      # 只跑到报告（不烘前端数据）
    python 跑端到端_v3.py --key EVT-002 --from 前端              # 只重烘前端那两份

★ 不给子进程加 `-I`：`-I` 隐含 `-E`，会吃掉 `PYTHONIOENCODING`，
  中文 print 在 GBK 控制台当场 UnicodeEncodeError ⇒ 整链误报失败（2026-10-14 踩过）。
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                                  # _p252_intake
KG = os.path.join(PKG, "kgcopy")                             # 改过的知识图谱包
KG_CODE = os.path.join(KG, "代码")
GRAPH_CODE = os.path.join(PKG, "graphrag", "02_代码")          # 上游建图（跑新事件.py）
PERSONA_CACHE = os.path.join(KG, "输入", "llm_persona_cache.json")

SIM = HERE
ENGINE_DIR = os.path.join(SIM, "引擎")
ENGINE = os.path.join(ENGINE_DIR, "跑全轮.py")
T1 = os.path.join(ENGINE_DIR, "t1_runner.py")
REPORT_DIR = os.path.join(SIM, "报告")
REPORT = os.path.join(REPORT_DIR, "生成报告.py")
BACKEND = os.path.join(SIM, "后端")
# 前端数据：board.json / graph.json（后端两页直出的就是这两个文件）
GEN_FRONTEND = os.path.join(SIM, "前端数据", "生成前端数据.py")


def _find_collect_one():
    r"""采集段脚本的位置。★2026-10-20 修：原先写死的相对路径多退了一层
    （`..\..\..\初赛提交\...`），`--raw` 那条路一跑就退 —— 只是历次都用 `--event`
    跳过了采集段，所以一直没暴露。改成按候选顺序找，包搬到别处也能跑。"""
    cands = [os.path.join(HERE, "采集_单事件.py"),                     # 随身带
             os.path.join(PKG, "采集", "采集_单事件.py"),
             os.path.join(os.path.dirname(PKG), "初赛提交", "端到端", "采集_单事件.py")]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[0])


COLLECT_ONE = _find_collect_one()

WORK = os.path.join(HERE, "中间产物")
EVENTS = os.path.join(HERE, "事件")

STAGES = ("采集", "图谱", "仿真", "报告", "前端")
_ENV = dict(os.environ)
_ENV["PYTHONIOENCODING"] = "utf-8"
_ENV["PYTHONUTF8"] = "1"
_ENV["PYTHONDONTWRITEBYTECODE"] = "1"
_ENV.pop("PYTHONHOME", None)

_STAT = []


def say(msg=""):
    print(msg)


def head(t):
    say("")
    say("=" * 88)
    say("  " + t)
    say("=" * 88)


def run_cmd(args, cwd=None):
    say("    $ " + " ".join('"%s"' % a if " " in str(a) else str(a) for a in args))
    p = subprocess.Popen([str(x) for x in args], cwd=cwd, env=_ENV,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines = []
    for ln in p.stdout:
        ln = ln.rstrip("\n")
        lines.append(ln)
        print("      | " + ln)
    p.wait()
    return p.returncode, lines


def gate(stage, ok, why):
    _STAT.append((stage, "✔" if ok else "✗", why))
    if not ok:
        say("")
        say("✗ 卡在【%s】—— %s" % (stage, why))
        say("  用 `--from %s` 可以接着跑，前面的段不会重跑。" % stage)
        done_table()
        sys.exit(1)


def done_table():
    say("")
    say("-" * 88)
    say("  链路体检")
    say("-" * 88)
    for s, mark, why in _STAT:
        say("   %s %-4s %s" % (mark, s, why))
    say("-" * 88)


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────
def stage_collect(a):
    """采集：raw → event*.json。给了 --event 就跳过。"""
    if not a.raw:
        say("    （跳过 —— 已由 --event 给出成品）")
        _STAT.append(("采集", "·", "跳过 —— 用现成 %s" % a.event))
        return
    for k in ("id", "title", "t0"):
        if not getattr(a, k):
            gate("采集", False, "--raw 要配套 --%s" % k)
    out = os.path.join(WORK, "event%s.json" % a.id)
    cmd = [sys.executable, os.path.abspath(COLLECT_ONE), "--raw", a.raw,
           "--out", out, "--id", str(a.id), "--title", a.title, "--t0", a.t0]
    if a.annotate:
        cmd.append("--annotate")
    rc, _ = run_cmd(cmd)
    ok = rc == 0 and os.path.exists(out)
    n = 0
    if ok:                       # ★2026-10-20：0 条成品也是"跑通"，后面才炸；这里先拦
        try:
            ev = jload(out)
            n = len(ev) if isinstance(ev, list) else len(ev.get("posts") or [])
        except Exception:
            n = 0
        ok = n > 0
    if ok:
        a.event = out
    gate("采集", ok, "exit=%d ｜ 成品 %d 条 ｜ %s%s"
         % (rc, n, out, "" if ok else "  —— 多半是 --t0 落在帖子日期之外（全被切掉）"))


def stage_graph(a):
    """图谱：建图 + LLM 捏人 + 选人 + 桥接，一次跑完（run_layer 内部有序）。"""
    out = os.path.join(WORK, "图谱", a.key)
    cmd = [sys.executable, os.path.join(KG_CODE, "run_layer.py"),
           "--key", a.key, "--event", a.event,
           "--graph-code", GRAPH_CODE,
           "--persona-cache", PERSONA_CACHE,
           "--engine", T1, "--out", out]
    if a.event_type:
        cmd += ["--event-type", a.event_type]
    if a.gen_personas:
        cmd += ["--gen-personas"]
    if a.allow_rule_persona:
        cmd += ["--allow-rule-persona"]
    rc, lines = run_cmd(cmd, cwd=KG_CODE)

    cfg = os.path.join(out, "事件配置.json")
    ok = rc == 0 and os.path.exists(cfg)
    why = "exit=%d" % rc
    if not ok:
        gate("图谱", False, why + " ｜ 没出 事件配置.json —— 看上面哪一步 ❌")
    a._cfg = cfg

    c = jload(cfg)
    why += " ｜ T0=%s ｜ %d 轮" % (c.get("t0_time"), c.get("n_rounds") or 0)

    # ── 闸 1：事件类型必须来自采集侧 ────────────────────────────────
    et, src = c.get("event_type"), c.get("event_type_source")
    if src != "采集侧":
        gate("图谱", False, "事件类型不是采集侧给的（%r / source=%r）。"
             "检查 `输入/采集侧_事件类型.json` 或显式 `--event-type`。" % (et, src))
    _STAT.append(("类型", "✔", "%s（采集侧 8 类）" % et))

    # ── 闸 1b：报告标题要有舆情主题 ──────────────────────────────────
    #   `输入/采集侧_主题.json` 没登记该事件时，bridge 会**回落到起火点帖的标题**
    #   （EVT-001 曾印成「建议某些行业在挑选工作者的时候能够先考察人品…」）。
    #   这条只 warn 不拦：没登记也能跑完，但报告标题一眼看不出主题。
    if c.get("topic_title"):
        _STAT.append(("主题", "✔", "%s → 标题「%s」" % (c.get("topic_title"),
                                                     c.get("report_title") or "")))
    else:
        _STAT.append(("主题", "！", "未登记舆情主题（`输入/采集侧_主题.json` 没有 %s）—— "
                                    "报告标题将回落到起火点帖标题" % a.key))

    # ── 闸 2：捏人复核（LLM捏人清单.json 由 build_t0_real_agents 写）──
    lst = os.path.join(out, "LLM捏人清单.json")
    if not os.path.exists(lst):
        gate("图谱", False, "缺 LLM捏人清单.json —— 建人那一步没跑或版本不对")
    L = jload(lst)
    miss = L.get("missing") or []
    not_llm = [x for x in (L.get("agents") or []) if x.get("persona_llm") != "llm"]
    if miss or not_llm:
        detail = []
        if miss:
            detail.append("缓存缺 %d 人：%s" % (len(miss), "、".join(miss)))
        if not_llm:
            detail.append("没走 LLM 的真人账号 %d 个：%s"
                          % (len(not_llm), "、".join(x["agent_id"] for x in not_llm)))
        gate("图谱", False, "真人没有全部用 LLM 捏 —— " + "；".join(detail))
    _STAT.append(("捏人", "✔", "%d 个真人账号全部 LLM（缓存 %s）"
                  % (L.get("n_llm") or 0, L.get("cache_version"))))

    # ── 当事人覆盖：party_entities 点到的每个人，每个渠道都要有账号 ──
    H = jload(os.path.join(out, "H_来源与审计.json"))
    parties = c.get("party_entities") or []
    if parties:
        have = {}
        for x in (H.get("agents") or []):
            if x.get("entity_role") == "当事人":
                have.setdefault(_name_of(x), []).append(x.get("agent_id"))
        lack = [p for p in parties if p not in have]
        if lack:
            gate("图谱", False, "当事人没有建出账号：%s" % "、".join(lack))
        _STAT.append(("当事人", "✔", "；".join("%s ×%d 渠道（%s）"
                                              % (p, len(have[p]), "、".join(have[p]))
                                              for p in parties)))

    # ── 闸 3：名册 ⊆ 槽位（2026-10-20 网民→媒体 事故的永久闸）────────
    #   D 里没有 slot_id，用 H（逐人审计）做连接键。
    #   判据两条：① 画像库人员的角色/渠道 == 他落座的槽位；
    #             ② 名字是 网民NNN / 网友X 的，角色只能是 网民。
    slots = {s.get("slot_id"): s for s in jload(os.path.join(out, "C_槽位表.json")).get("slots") or []}
    D = jload(os.path.join(out, "D_Agent名单.json")).get("agents") or []
    #   D（交付给下游的 14 字段）里没有 persona_source / slot_id，
    #   这两样只在 H（逐人审计）里 ⇒ 必须用 H 做连接。
    meta_of = {x.get("agent_id"): x for x in (H.get("agents") or [])}
    bad, org_soft = [], []
    lib_n = 0
    PERSONAL_ROLES = ("网民", "客户", "KOL")
    for r in D:
        meta = meta_of.get(r.get("agent_id")) or {}
        if meta.get("persona_source") != "library":
            continue
        lib_n += 1
        s = slots.get(meta.get("slot_id"))
        if not s:
            continue
        got = (r.get("role_type"), r.get("primary_channel"))
        want = (s.get("role_type"), s.get("channel"))
        if got == want:
            continue
        msg = ("%s：%s×%s ≠ 槽位 %s×%s"
               % (r.get("display_name") or r.get("agent_id"), got[0], got[1],
                  want[0], want[1]))
        # 只有涉及个人角色（网民/客户/KOL）才是事故；机构↔机构是上游两套归一实现的
        # 口径差（党央媒按监管 / 人民法院报按媒体），只告警。
        if got[0] in PERSONAL_ROLES or want[0] in PERSONAL_ROLES:
            bad.append(msg)
        else:
            org_soft.append(msg)
    wrong_name = [r.get("display_name") or r.get("agent_id") for r in D
                  if re.match(r"^(网民|网友|路人)\d*$", str(r.get("display_name") or ""))
                  and r.get("role_type") != "网民"]
    if bad:
        gate("名册", False, "名册角色与槽位不一致 %d 例（正是 2026-10-20「网民被标成媒体」"
             "那类事故）：%s" % (len(bad), "；".join(bad[:5])))
    if wrong_name:
        gate("名册", False, "网名被标成非网民 %d 例：%s"
             % (len(wrong_name), "、".join(wrong_name[:5])))
    if org_soft:
        say("      ⚠️  机构口径差 %d 例（槽位表 vs 名单，不拦）：%s"
            % (len(org_soft), "；".join(org_soft[:3])))
    _STAT.append(("名册", "✔", "%d 人（画像库 %d）个人角色与槽位一致，无网名误标%s"
                  % (len(D), lib_n,
                     "（机构口径差 %d 例，告警）" % len(org_soft) if org_soft else "")))

    say("      └ 配置 %s" % cfg)
    gate("图谱", True, why + " ｜ event_type=%s ｜ 画像 %s" % (et, c.get("persona_file")))


def _name_of(rec):
    """H 里没有 display_name，只能从 agent_id 尾段 / bio 首个「」取。"""
    aid = str(rec.get("agent_id") or "")
    parts = aid.split("_", 2)
    return parts[2] if len(parts) == 3 and parts[2] else aid


def stage_sim(a):
    cfg = a._cfg
    if not a.dry_run:
        try:
            sys.path.insert(0, ENGINE_DIR)
            sys.path.insert(0, BACKEND)
            import _sim_config, precheck, progress      # noqa: E402
            c = _sim_config.load(path=cfg, quiet=True)
            rep = precheck.run(c, progress.inventory(c))
        except SystemExit as e:
            gate("仿真", False, "输入预检加载失败：%s" % e)
            return
        say("      预检：error %d ｜ warn %d ｜ info %d"
            % (rep["counts"]["error"], rep["counts"]["warn"], rep["counts"]["info"]))
        for it in rep["items"]:
            if it["level"] == "error":
                say("        ✗ [error] %s" % it["title"])
        if not rep["ok"]:
            gate("仿真", False, "输入预检不通过（上面每一条 error 都要先修）")
    t0 = time.time()
    rc, _ = run_cmd([sys.executable, ENGINE, "--config", cfg], cwd=ENGINE_DIR)
    dt = time.time() - t0
    # ★ 2026-10-20：引擎只写轮 1…41 的记录员行（41×7=287），**轮 0 必须补**。
    #   不补 ⇒ 报告按轮号索引整体错一轮、末轮关键节点渲染成空，而 `生成报告.py` 照样 exit 0。
    #   必须在这里补（报告段之前），且 rerun_recorder 对同一轮是替换、可重复执行。
    rc2, _ = run_cmd([sys.executable, os.path.join(ENGINE_DIR, "rerun_recorder.py"),
                      "0", "--config", cfg], cwd=ENGINE_DIR)
    c = jload(cfg)
    n = c.get("n_rounds") or 42
    done = _rounds_done(cfg)
    rec_n = _recorder_lines(cfg)
    gate("仿真", rc == 0 and rc2 == 0 and done >= n - 1 and rec_n == n * 7,
         "exit=%d ｜ 轮 %d/%d ｜ 记录员 %d 行（应 %d）｜ %.1f min"
         % (rc, done, n - 1, rec_n, n * 7, dt / 60.0))
    a._html = os.path.join(os.path.dirname(cfg), c.get("out_html") or "")


def _rounds_done(cfg):
    c = jload(cfg)
    d = os.path.dirname(cfg)
    n = 0
    for r in range(1, (c.get("n_rounds") or 42) + 1):
        if os.path.exists(os.path.join(d, (c.get("snapshot_pattern") or "t{round}状态_第{round}轮.json")
                                       .format(round=r))):
            n = r
    return n


def _recorder_lines(cfg):
    """记录员行数。42 轮 × 7 行 = 294，少一轮就说明轮 0 没补上。"""
    c = jload(cfg)
    p = os.path.join(os.path.dirname(cfg), c.get("recorder_file") or "round_snapshot.jsonl")
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip())


def stage_report(a):
    cfg = a._cfg
    rc, _ = run_cmd([sys.executable, REPORT, "--config", cfg], cwd=REPORT_DIR)
    c = jload(cfg)
    d = os.path.dirname(cfg)
    html = os.path.join(d, c.get("out_html") or "")
    data = os.path.join(d, c.get("data_file") or "")
    ok = rc == 0 and os.path.exists(html) and os.path.exists(data)
    why = "exit=%d" % rc
    if os.path.exists(html):
        why += " ｜ 报告 %.1f KB" % (os.path.getsize(html) / 1024.0)
    if os.path.exists(data):
        try:
            al = jload(data).get("alert") or {}
            lv = {}
            for r in (al.get("rows") or []):
                lv[r.get("level")] = lv.get(r.get("level"), 0) + 1
            if lv:
                why += " ｜ 预警 " + "、".join("%s %d 轮" % kv for kv in sorted(lv.items()))
        except Exception:
            pass
    a._html = html
    gate("报告", ok, why)


def _has_collect_source(key):
    """生成器能不能自己找到这一场的采集成品（口径同 `前端数据\\生成前端数据.py`）。

    命中 = 交付自带的 `采集成品\\<KEY>_processed.json`，或本链采集段刚写的
    `中间产物\\event{N}.json`，或 `采集侧_可复现资料包\\…\\event{N}.json`。
    """
    m = re.match(r"^EVT-(\d+)$", key or "")
    if not m:
        return False
    n = int(m.group(1))
    return any(os.path.exists(p) for p in (
        os.path.join(SIM, "前端数据", "采集成品", "%s_processed.json" % key),
        os.path.join(WORK, "event%d.json" % n),
        os.path.join(os.path.dirname(SIM), "采集侧_可复现资料包", "02_数据与输入材料",
                     "event", "event%d.json" % n),
    ))


def _staged_dir(key):
    """暂存区：图谱段还没开跑时，事件目录**还不存在**（`run_layer.py` 一开跑还会 rmtree 它）。

    所以「采集段一结束就想让看板亮」只能先烘到这儿，等图谱段出了成品再搬过去。
    放在 `_运行\\<KEY>\\publish\\` 而不是 `中间产物\\` —— 后者是链路产物，别混展示件。
    """
    return os.path.join(HERE, "_运行", key, "publish")


def publish(key, only, event=None, subject=None, stage=False):
    """把这一场的前端数据包**提前**烘一份，让页面早点亮（失败不拦）。

    ★ 2026-10-21：原来只有最后一段「前端」才生成 `board.json` / `graph.json`，
      于是新事件跑的过程中，看板页和图谱页一直是空的 —— 界面上看到的是
      "后端在跑、前端没反应"。现在：
        · 采集段一出成品 ⇒ 立刻烘**看板**（不用等图谱那几分钟的 LLM 捏人）；
        · 图谱段一出包   ⇒ 立刻烘**图谱**（仿真还没开始，第 02 页就能看人了）。
    ★ **失败不拦**：这两份是展示件，不是链路产物。烘不出来只在体检表里记一个「！」，
      链路接着跑；最后「前端」段还会再烘一次，成功的话覆盖成同一份。
    ★ 落点 = 本链的事件目录（后端 `paths.EVENTS_DIR` 直接读这儿），写完不用重启后端。
      `stage=True` 时改落到 `_staged_dir()`，由 `_adopt_staged()` 在图谱段之后搬进去。
    """
    out = _staged_dir(key) if stage else os.path.join(WORK, "图谱", key)
    if stage:
        try:
            os.makedirs(out, exist_ok=True)
        except Exception:
            pass
    argv = [sys.executable, GEN_FRONTEND, "--key", key, "--only", only]
    if only == "board" and event and os.path.exists(event) and not _has_collect_source(key):
        argv += ["--posts", event]
    if subject:
        argv += ["--subject", subject]
    if stage:
        argv += ["--out-dir", out]
    rc, lines = run_cmd(argv, cwd=os.path.dirname(GEN_FRONTEND))
    p = os.path.join(out, "board.json" if only == "board" else "graph.json")
    ok = rc == 0 and os.path.exists(p)
    name = "看板" if only == "board" else "图谱"
    if not ok:
        tail = [x for x in lines if "✗" in x][:3]
        say("    ！%s没烘出来（不影响后面，最后一段还会再试）：%s" % (name, " / ".join(tail)))
    _STAT.append(("前期发布", "✔" if ok else "！",
                  "%s %s%s" % (name, "%.1f KB" % (os.path.getsize(p) / 1024.0) if ok else "没出",
                               "（暂存）" if ok and stage else "")))


def _adopt_staged(key):
    """把暂存的看板搬进事件目录（图谱段刚建好目录之后调）。失败不拦。"""
    src = os.path.join(_staged_dir(key), "board.json")
    dst = os.path.join(WORK, "图谱", key, "board.json")
    if not os.path.exists(src) or not os.path.isdir(os.path.dirname(dst)):
        return False
    try:
        shutil.copyfile(src, dst)
        return True
    except Exception:
        return False


def stage_frontend(a):
    """前端：把这一场的 `board.json` / `graph.json` 烘进事件目录。

    ★ 2026-10-21 新增。没有这一段，新事件跑完四个段之后，前端那三页仍然看不到它：
      后端的 `/api/events/<KEY>/board|graph` 是**直出**（读文件，读不到就 404），
      而这两个文件原来只有 EVT-001 有（一次性脚本从前端烤死的数据里导出来的）。
      这一段把它变成链路的一部分 —— 新事件跑完，看板 / 图谱 / 回放三页当场有数据。

    ★ 落点就是**本链的事件目录**（`中间产物\\图谱\\<KEY>\\`），也就是后端
      `paths.EVENTS_DIR` 指向的那个目录：写完不用拷、不用重启后端。
    """
    evtdir = os.path.join(WORK, "图谱", a.key)
    argv = [sys.executable, GEN_FRONTEND, "--key", a.key]
    # ★ 只有"生成器自己找不到源"时才把这一场的 event 文件塞进去。
    #   有采集侧成品（EVT-001…013 就有）时不传，让它按自己的优先级取 —— 否则
    #   `--event` 给的是整周期那份，烘出来的看板和采集侧交付的那张图对不上。
    if a.event and os.path.exists(a.event) and not _has_collect_source(a.key):
        argv += ["--posts", a.event]
    if a.subject:
        argv += ["--subject", a.subject]
    rc, lines = run_cmd(argv, cwd=os.path.dirname(GEN_FRONTEND))

    b = os.path.join(evtdir, "board.json")
    g = os.path.join(evtdir, "graph.json")
    ok = rc == 0 and os.path.exists(b) and os.path.exists(g)
    why = "exit=%d" % rc
    for name, p in (("看板", b), ("图谱", g)):
        why += " ｜ %s %s" % (name, "%.1f KB" % (os.path.getsize(p) / 1024.0) if os.path.exists(p) else "✗")
    if not ok:
        tail = [x for x in lines if "✗" in x or "·" in x][:4]
        gate("前端", False, why + ("\n     " + "\n     ".join(tail) if tail else ""))
    _STAT.append(("前端", "✔", why))


def main():
    ap = argparse.ArgumentParser(description="端到端 v3：采集 → 图谱(LLM捏人) → 仿真 → 报告")
    ap.add_argument("--key", required=True)
    ap.add_argument("--event", default=None)
    ap.add_argument("--raw", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--t0", default=None)
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--event-type", default=None, help="采集侧 8 类（登记表里没有时显式给）")
    ap.add_argument("--subject", default=None,
                    help="图谱中心实体（新事件必给：事件配置里通常没有 subject，"
                         "`前端数据\\事件元数据.json` 里也不会预先登记）")
    ap.add_argument("--gen-personas", action="store_true", help="缓存缺人时先调 LLM 生成")
    ap.add_argument("--allow-rule-persona", action="store_true", help="调试用：允许退回规则模板")
    ap.add_argument("--from", dest="frm", default="采集", choices=STAGES)
    ap.add_argument("--to", dest="to", default="前端", choices=STAGES)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # ★ 2026-10-20：`--event` 一律转绝对路径。子进程在 `kgcopy\代码` 下跑，
    #   相对路径会被它按那个 cwd 解析 —— `..\kgcopy\输入\event12.json` 会变成
    #   `kgcopy\kgcopy\输入\...`，当场 "missing event"（实测踩过）。
    if a.event:
        a.event = os.path.abspath(a.event)

    # 只有真要跑「采集/图谱」时才需要输入；--from 仿真/报告 是纯重跑尾段
    _need_input = STAGES.index(a.frm) <= STAGES.index("图谱")
    if _need_input and not a.raw and not a.event:
        sys.exit("✗ 至少要给 --event（采集侧成品）或 --raw（原始数据）")
    if a.raw and not a.event:
        a.event = os.path.join(WORK, "event%s.json" % a.id)
    a._cfg = None
    a._html = None

    i0, i1 = STAGES.index(a.frm), STAGES.index(a.to)
    if i0 > i1:
        sys.exit("✗ --from 在 --to 之后")
    todo = STAGES[i0:i1 + 1]
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(EVENTS, exist_ok=True)

    head("端到端 v3 ｜ %s" % a.key)
    say("  输入    : %s" % (a.raw or a.event))
    say("  段      : %s" % " → ".join(todo))
    say("  图谱包  : %s" % KG)
    say("  建图    : %s" % GRAPH_CODE)
    say("  人设缓存: %s" % PERSONA_CACHE)
    say("  引擎    : %s" % T1)
    say("  成品    : %s\\%s" % (os.path.join(WORK, "图谱"), a.key))

    t0 = time.time()
    for st in todo:
        head("【%s】" % st)
        if st == "采集":
            stage_collect(a)
            # 成品一出来就先烘看板 —— 图谱那一段要几分钟，别让前端干等。
            # ★ 此刻事件目录还不存在（图谱段一开跑还会 rmtree 它）⇒ 先烘到暂存区。
            publish(a.key, "board", a.event, a.subject, stage=True)
        elif st == "图谱":
            if not os.path.exists(a.event):
                gate(st, False, "找不到输入事件：%s" % a.event)
            stage_graph(a)
            # 图谱段刚建好事件目录：把采集段烘好的看板搬进去，前端当场就能看
            _adopt_staged(a.key)
            # 图谱包一出来就烘图谱；顺手补一次看板（--from 图谱 的链没跑采集段）
            publish(a.key, "graph", a.event, a.subject)
            publish(a.key, "board", a.event, a.subject)
        elif st == "仿真":
            if not a._cfg:
                a._cfg = os.path.join(WORK, "图谱", a.key, "事件配置.json")
                if not os.path.exists(a._cfg):
                    gate(st, False, "找不到事件配置（先跑 --to 图谱）")
            stage_sim(a)
        elif st == "报告":
            if not a._cfg:
                a._cfg = os.path.join(WORK, "图谱", a.key, "事件配置.json")
                if not os.path.exists(a._cfg):
                    gate(st, False, "找不到事件配置")
            stage_report(a)
        elif st == "前端":
            stage_frontend(a)

    done_table()
    say("")
    say("✅ 端到端 v3 跑通 ｜ %s ｜ %.1f min" % (a.key, (time.time() - t0) / 60.0))
    if a._html:
        say("   报告：%s" % a._html)


if __name__ == "__main__":
    main()
