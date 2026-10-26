# -*- coding: utf-8 -*-
"""输入预检 —— 开跑前 3 秒把「这份数据能不能跑」问清楚。

为什么要有它
------------
引擎是**自愈循环**：跑挂了重启就从断点接着跑。这在"偶发失败"上是优点，
在"输入本来就不对"上是灾难 —— 它会跑到第 17 轮才在某个字段上炸掉，
而那时已经烧掉十几分钟的 LLM 调用。

所以：**所有能在开跑前判掉的问题，一律在开跑前判掉。**

查什么
------
    ① 配置     必备键、时间格式、轮次/轮长
    ② 运行目录  存不存在、是不是续跑
    ③ 画像库    13 字段齐不齐、枚举对不对、话题字段是不是非空 dict
                （话题**权重和**另有一条 warn：图谱侧口径本就 ≠ 1.00，见 _check_persona）
    ④ t0 快照   注入帖有没有、发帖人绑没绑上画像、窗口与 t0 自不自洽
    ⑤ LLM     有没有可用的凭据（**只看存在性，绝不读/打印明文**）

等级
----
    error  跑不了 / 跑了也是错的 —— 必须修
    warn   能跑，但结果可疑 —— 建议先看一眼
    info   陈述事实 —— 不用动

返回值是**纯数据**（dict），不打印 —— 命令行和 HTTP 接口共用同一份判定。
"""
import json
import os

# 命题口径冻结的分类法（见《命题更新版指标口径》）：5 角色 × 6 渠道。
ROLES = ("网民", "客户", "KOL", "媒体", "监管")
CHANNELS = ("微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报")

# 画像库对仿真层的 13 项交付字段（见《仿真层对画像侧的需求说明》§一，已冻结）
PERSONA_FIELDS = (
    "agent_id", "role_type", "primary_channel", "persona_bio", "interest_topics",
    "influence_weight", "active_hours", "stance_bias", "emotion_sensitivity",
    "valence_base_0", "arousal_base_0", "valence_baseline", "arousal_baseline",
)

# 5 角色 × 6 渠道的**有效组合**（命题口径，15 格）。图谱侧规则 A2「15 格闭合」、
# 桥接 B9「越界退出」说的都是这张表。
# 为什么必须在预检里拦：动作表 `TABLE1` 按 (role, channel) 取，组合越界
# ⇒ 引擎在 `build_user` 处 `KeyError`，**第 0 轮就跑不起来**，而且是在 42 轮
# 开跑之后才炸。预检提前一句话拦掉，比跑到一半崩掉强。
TABLE_1 = {
    "微博": {"网民", "客户", "KOL", "媒体"},
    "抖音": {"网民", "客户", "KOL", "媒体"},
    "小红书": {"网民", "客户", "KOL", "媒体"},
    "黑猫投诉": {"客户"},
    "新闻门户": {"媒体"},
    "监管通报": {"监管"},
}


class Report:
    """收集条目。`ok` = 没有 error。"""

    def __init__(self):
        self.items = []

    def add(self, level, code, title, detail=""):
        self.items.append({"level": level, "code": code, "title": title, "detail": detail})

    def error(self, code, title, detail=""):
        self.add("error", code, title, detail)

    def warn(self, code, title, detail=""):
        self.add("warn", code, title, detail)

    def info(self, code, title, detail=""):
        self.add("info", code, title, detail)

    def as_dict(self, event_id=None):
        n = {k: sum(1 for i in self.items if i["level"] == k) for k in ("error", "warn", "info")}
        return {
            "event_id": event_id,
            "ok": n["error"] == 0,
            "counts": n,
            "items": self.items,
        }


def _load_json(path, r, code, label):
    if not os.path.exists(path):
        r.error(code + ".missing", "%s 不存在" % label, path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        r.error(code + ".broken", "%s 解析失败" % label, "%s\n  %s" % (path, e))
        return None


# ── ① 配置 ────────────────────────────────────────────────────────────────
def _check_config(cfg, r):
    brief = str(cfg.get("event_brief") or "")
    if not brief.strip():
        r.error("cfg.brief", "`event_brief` 是空的",
                "它会以【事件背景】注入每一轮的 SYSTEM 提示词 —— 空的话模型不知道在演什么。")
    elif "__EVENT_BRIEF__" in brief:
        r.error("cfg.brief.placeholder", "`event_brief` 里还留着 `__EVENT_BRIEF__` 占位符",
                "占位符没被替换 —— 检查配置是不是被手工改坏了。")
    else:
        r.info("cfg.brief", "事件背景 %d 字" % len(brief), brief[:60] + ("…" if len(brief) > 60 else ""))

    try:
        t0 = cfg.t0
        r.info("cfg.t0", "t0 = %s" % t0.strftime("%Y-%m-%d %H:%M"), "")
    except SystemExit as e:
        r.error("cfg.t0", "`t0_time` 解析不了", str(e))
        t0 = None

    rh, nr = cfg.round_hours, cfg.n_rounds
    if not isinstance(rh, (int, float)) or rh <= 0:
        r.error("cfg.round_hours", "`round_hours` 不合法：%r" % (rh,), "轮长必须是正数。")
    if not isinstance(nr, int) or nr <= 0:
        r.error("cfg.n_rounds", "`n_rounds` 不合法：%r" % (nr,), "轮数必须是正整数。")
    if t0 and isinstance(rh, (int, float)) and rh > 0 and isinstance(nr, int) and nr > 0:
        r.info("cfg.span", "覆盖 %d 轮 × %sh = %sh（%.1f 天）"
               % (nr, rh, nr * rh, nr * rh / 24.0), "起点 %s" % t0)
    return t0


# ── ② 运行目录 ────────────────────────────────────────────────────────────
def _check_rundir(cfg, r):
    d = cfg.run_dir
    if not os.path.isdir(d):
        r.error("run_dir.missing", "运行目录不存在", d)
        return
    r.info("run_dir.ok", "运行目录就绪", d)


# ── ③ 画像库 ──────────────────────────────────────────────────────────────
def _check_persona(cfg, r):
    pf = _load_json(cfg.persona_path, r, "persona", "画像库")
    if pf is None:
        return None
    if not isinstance(pf, dict) or not isinstance(pf.get("agents"), list):
        r.error("persona.shape", "画像库顶层不是 {agents:[...]}", cfg.persona_path)
        return None

    agents = pf["agents"]
    r.info("persona.count", "画像库 %d 人" % len(agents), cfg.persona_path)
    if "count" in pf and pf["count"] != len(agents):
        r.error("persona.count_mismatch", "`count` 与 `agents` 长度对不上",
                "count=%r，实际 %d 条" % (pf["count"], len(agents)))
    if pf.get("event_id") and pf["event_id"] != cfg["event_id"]:
        r.error("persona.event_id", "画像库的 event_id 与配置不一致",
                "画像库=%r，配置=%r —— 八成是拿错了文件" % (pf["event_id"], cfg["event_id"]))

    seen, bad_roles, bad_chans, bad_sum, wsum, bad_hours = set(), [], [], [], [], []
    bad_cell = []       # 角色、渠道各自合法，但组合不在 15 格里
    for a in agents:
        aid = a.get("agent_id")
        if aid in seen:
            r.error("persona.dup", "agent_id 重复：%s" % aid, "")
        seen.add(aid)
        missing = [k for k in PERSONA_FIELDS if k not in a]
        if missing:
            r.error("persona.fields", "%s 缺字段" % aid, "缺：%s" % "、".join(missing))
            continue
        if a["role_type"] not in ROLES:
            bad_roles.append((aid, a["role_type"]))
        if a["primary_channel"] not in CHANNELS:
            bad_chans.append((aid, a["primary_channel"]))
        # 只对「两者都在各自枚举里」的人查组合 —— 否则同一个人会报两次
        if a["role_type"] in ROLES and a["primary_channel"] in CHANNELS:
            if a["role_type"] not in TABLE_1[a["primary_channel"]]:
                bad_cell.append((aid, a["role_type"], a["primary_channel"]))
        it = a["interest_topics"]
        if not isinstance(it, dict) or not it:
            bad_sum.append((aid, it, "空"))
        elif abs(sum(it.values()) - 1.0) > 0.01:
            # 权重和 ≠ 1.00 **不是错**，只是图谱侧的口径：
            #   画像库按设计是「3~6 个话题 + 一个 `其他` 兜底键」凑满 1.00，
            #   而下游用的 adapted 版把 `其他` 丢了 ⇒ 和落在 0.5~0.85。
            #   交付的上游 12 事件包、以及已跑完的 EVT-002/004/006 全是这个形态。
            # 引擎侧 `interest_topics` 只进 prompt 文本（`render_interest` 打成
            #   「话题（关注度 0.75）」），**绝对值不参与任何计算**，只看排序。
            # ⚠️ 但**桥接**用它算 t0 的 P：`match = 0.1 + 0.9 × Σ(本人 interest_topics[t] for t in 帖topics)`
            #   ⇒ 权重和越小，该人的兴趣匹配因子越小。P 在引擎里按人归一（ΣP），
            #   所以每人和相同则整体约掉、不同才会真的改结果。
            # ⇒ 仍只提示、不能拦：不改变可运行性，且判 error 会把每一份图谱产出的画像库全数拒掉。
            wsum.append((aid, round(sum(it.values()), 4)))
        ah = a["active_hours"]
        if not isinstance(ah, list) or not ah or any(
                not isinstance(h, int) or not 0 <= h <= 23 for h in ah):
            bad_hours.append((aid, ah))

    if bad_roles:
        r.error("persona.role", "%d 人的 role_type 不在 5 类枚举里" % len(bad_roles),
                "枚举：%s\n  例：%s" % ("、".join(ROLES), bad_roles[:3]))
    if bad_chans:
        r.error("persona.channel", "%d 人的 primary_channel 不在 6 渠道枚举里" % len(bad_chans),
                "枚举：%s\n  例：%s" % ("、".join(CHANNELS), bad_chans[:3]))
    if bad_cell:
        _cells = "、".join("%s→%s" % (ch, "/".join(sorted(rs)))
                           for ch, rs in sorted(TABLE_1.items()))
        r.error("persona.cell",
                "%d 人的 (role_type, primary_channel) 不在 15 格里" % len(bad_cell),
                "(角色, 渠道) 组合越界 ⇒ 引擎在 `build_user` 处 KeyError，第 0 轮就跑不起来。\n"
                "  例（agent_id, 角色, 渠道）：%s\n  有效格：%s" % (bad_cell[:3], _cells))
    if bad_sum:
        r.error("persona.topics", "%d 人的 interest_topics 不是非空 dict" % len(bad_sum),
                "例：%s" % bad_sum[:3])
    if wsum:
        r.warn("persona.topics.sum",
               "%d 人的 interest_topics 权重和 ≠ 1.00（图谱侧口径：adapted 版丢了 `其他` 兜底键）"
               % len(wsum),
               "例：%s\n  引擎只把它渲染成 prompt 文本；但**桥接**用它算 t0 的兴趣匹配因子"
               "（`0.1 + 0.9×Σ`）⇒ 和越小该因子越小。P 在引擎里按人归一（ΣP），"
               "每人和相同则约掉、不同才会改结果。不改变可运行性 ⇒ 可照跑。"
               % wsum[:3])
    if bad_hours:
        r.error("persona.hours", "%d 人的 active_hours 不是 0–23 整点数组" % len(bad_hours),
                "例：%s" % bad_hours[:3])

    oob = [a["agent_id"] for a in agents
           if isinstance(a.get("valence_base_0"), (int, float)) and not -1 <= a["valence_base_0"] <= 1]
    if oob:
        r.warn("persona.valence_range", "%d 人的 valence_base_0 越出 [-1,1]" % len(oob), "例：%s" % oob[:3])
    oob = [a["agent_id"] for a in agents
           if isinstance(a.get("arousal_base_0"), (int, float)) and not 0 <= a["arousal_base_0"] <= 1]
    if oob:
        r.warn("persona.arousal_range", "%d 人的 arousal_base_0 越出 [0,1]" % len(oob), "例：%s" % oob[:3])
    flat = [a["agent_id"] for a in agents
            if not isinstance(a.get("stance_bias"), dict) or not a["stance_bias"]]
    if flat:
        r.warn("persona.stance_bias", "%d 人的 stance_bias 是空字典" % len(flat),
               "空字典 ⇒ 查不到初始立场，会退回中性。按事件议题填：" + "、".join(flat[:3]))
    return pf


# ── ④ t0 快照 ─────────────────────────────────────────────────────────────
def _check_t0(cfg, r, persona):
    t0 = _load_json(cfg.t0_path, r, "t0", "t0 状态快照")
    if t0 is None:
        return None
    if not isinstance(t0, dict):
        r.error("t0.shape", "t0 快照顶层不是 JSON 对象", cfg.t0_path)
        return None

    agents = t0.get("agents") or []
    if not agents:
        r.error("t0.agents", "t0 快照里没有 agents", "第 0 轮的房间里没有人 —— 跑不起来。")
    else:
        r.info("t0.agents", "t0 房间 %d 人" % len(agents), "")

    # 快照里的人必须在画像库里（否则没有画像参数可查）
    if persona and agents:
        pset = {a.get("agent_id") for a in persona["agents"]}
        ghost = [a.get("agent_id") for a in agents if a.get("agent_id") not in pset]
        if ghost:
            r.error("t0.ghost", "%d 个 t0 扮演者不在画像库里" % len(ghost),
                    "画像库是唯一的参数源，查不到就演不了。例：%s" % ghost[:5])

    # 注入帖
    posts = t0.get("injected_posts")
    if posts is None:
        r.error("t0.posts", "t0 快照没有 `injected_posts`", "t0 没东西可发 —— 沙盘第一天是空的。")
    elif not isinstance(posts, list) or not posts:
        r.error("t0.posts", "`injected_posts` 是空的", "至少要有 1 条 t0 原帖。")
    else:
        aids = {a.get("agent_id") for a in agents}
        unbound, nocontent = [], []
        for p in posts:
            pid = p.get("post_id") or p.get("doc_id") or "?"
            if not (p.get("content") or "").strip():
                nocontent.append(pid)
            pa = p.get("poster_agent_id")
            if not pa:
                unbound.append((pid, "poster_agent_id 缺失"))
            elif aids and pa not in aids:
                unbound.append((pid, "poster_agent_id=%s 不在 t0 房间" % pa))
        r.info("t0.posts.ok", "t0 注入帖 %d 条" % len(posts),
               "发帖人：" + "、".join(str(p.get("poster_agent_id")) for p in posts))
        if nocontent:
            r.error("t0.posts.content", "%d 条注入帖没有正文" % len(nocontent), "例：%s" % nocontent[:3])
        if unbound:
            r.error("t0.posts.binding", "%d 条注入帖没绑上发帖人" % len(unbound),
                    "按《下游接口说明》§1.4，t0 注入只读 `poster_agent_id` + `content` 两个字段，"
                    "绑不上就没法还原。例：%s" % unbound[:3])

    # 窗口与 t0 自洽
    w, ws = t0.get("window"), t0.get("window_slots")
    if w and isinstance(w, list) and str(w[0])[:16] != cfg["t0_time"][:16]:
        r.warn("t0.window", "快照窗口起点与配置 `t0_time` 不一致",
               "快照 window[0]=%r，配置 t0_time=%r。" % (w[0], cfg["t0_time"])
               + "换事件时 t0 口径要整体平移，别只改一处。")
    if ws and isinstance(ws, list):
        try:
            hour = cfg.t0.hour
            if ws[0] != hour:
                r.warn("t0.slots", "`window_slots` 起点与 t0 的小时对不上",
                       "window_slots[0]=%r，t0 是 %02d 点。" % (ws[0], hour))
            if cfg.round_hours and len(ws) != int(cfg.round_hours):
                r.warn("t0.slots_len", "`window_slots` 格数与 `round_hours` 不匹配",
                       "len(window_slots)=%d，round_hours=%s。" % (len(ws), cfg.round_hours))
        except SystemExit:
            pass
    return t0


# ── ⑤ LLM 凭据 ────────────────────────────────────────────────────────────
_LLM_KEYS = ("base_url", "api_key", "model_name")


def _check_llm(cfg, r, has_products):
    """★ 全程只判**存在性** —— 绝不打开凭据文件、绝不打印其中任何内容。

    `has_products=True`（这一场已经有跑完的产物）时降级为 warn：
    没凭据只是"不能重跑"，不妨碍看已有的报告。
    """
    llm = cfg.llm or {}
    ep = (llm.get("env_path") or "").strip()
    has_env = bool(ep) and os.path.exists(ep)

    given = [k for k in _LLM_KEYS if (llm.get(k) or "").strip()]
    inline_full = len(given) == len(_LLM_KEYS)

    if has_env:
        r.info("llm.env", "LLM 凭据文件就位", ep)
    elif ep:
        r.warn("llm.env_missing", "配置里指向的 `.env` 不存在", ep)
    if inline_full:
        r.info("llm.inline", "`llm` 段已填全（base_url / api_key / model_name）", "")
    elif given:
        miss = [k for k in _LLM_KEYS if k not in given]
        r.warn("llm.inline_partial", "`llm` 段只填了一部分",
               "填了 %s，缺 %s。缺的会回落到 `.env`。" % ("、".join(given), "、".join(miss)))

    if has_env or inline_full:
        return
    msg = ("引擎要有 LLM 才能演。两种给法：\n"
           "  ① 在事件配置的 `llm` 段填 base_url / api_key / model_name\n"
           "  ② 放一份 `.env`（见 `事件\\EVT-001\\.env.example`）")
    if has_products:
        r.warn("llm.none", "没有可用的 LLM 凭据（已跑完，不妨碍看报告）",
               msg + "\n  ⚠ 但**重跑 / 续跑**需要先补上。")
    else:
        r.error("llm.none", "没有可用的 LLM 凭据", msg)


# ── 入口 ──────────────────────────────────────────────────────────────────
def run(cfg, inventory=None):
    """对一份已加载的配置跑全套预检 → dict。

    `inventory` 由调用方（通常 `progress.inventory(cfg)`）传进来，避免重复扫盘。
    """
    r = Report()
    _check_config(cfg, r)
    _check_rundir(cfg, r)
    persona = _check_persona(cfg, r)
    _check_t0(cfg, r, persona)
    has_products = bool(inventory and (inventory.get("rounds_done") or inventory.get("report_exists")))
    _check_llm(cfg, r, has_products)
    return r.as_dict(cfg["event_id"])


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import paths

    _eid = sys.argv[1] if len(sys.argv) > 1 else "EVT-001"
    import progress

    _cfg = paths.load_config(_eid)
    _rep = run(_cfg, progress.inventory(_cfg))
    ICON = {"error": "✗", "warn": "!", "info": "·"}
    print("=" * 78)
    print("输入预检 ｜ %s ｜ %s" % (_rep["event_id"], "通过" if _rep["ok"] else "** 不通过 **"))
    print("   error %d ｜ warn %d ｜ info %d" % (_rep["counts"]["error"], _rep["counts"]["warn"],
                                                _rep["counts"]["info"]))
    print("=" * 78)
    for it in _rep["items"]:
        print("%s [%-5s] %s" % (ICON[it["level"]], it["level"], it["title"]))
        if it["detail"]:
            for ln in str(it["detail"]).splitlines():
                print("           %s" % ln)
    sys.exit(0 if _rep["ok"] else 1)
