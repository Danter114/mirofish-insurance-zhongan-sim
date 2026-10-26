# -*- coding: utf-8 -*-
r"""把「采集侧成品 + 图谱侧产物」烘成前端要的两份数据包：`board.json` / `graph.json`。

    python 生成前端数据.py --key EVT-002
    python 生成前端数据.py --all
    python 生成前端数据.py --key EVT-001 --posts <别人给的 processed.json> --pack <看板文案包>

★ 为什么要有这一层
    后端 `api_live.py` 的 `/api/events/<KEY>/board` 与 `/graph` 只做**直出**：读
    `中间产物\图谱\<KEY>\board.json`，文件不在就 404。而这两个文件原先是用一个
    一次性脚本（`_p280_make_live_data.mjs`）从前端**烤死的数据**里导出来的 ——
    于是只有 EVT-001 有，其余事件的前端两页一律静默回落内置数据。
    这个脚本补的就是那一段：**从真产物现算**，每个事件一份。

★ 两份数据的来源（都不手抄，缺字段当场报错，不写半份出去）

    board.json  ← 采集侧成品（processed 口径）＋ 看板文案包
                  · 数据：`EVT-00N_processed.json`，或交付自带的
                    `采集侧_可复现资料包\02_数据与输入材料\event\event{N}.json`
                    （按 `timespan_days <= --day-max` 切 Day0–Day1）
                  · 文案：`00_程序与文档\看板生成\evt_packs\EVT-00N_pack.json`
                    （概述 / 时间线徽标 / 媒体观点 / 传播链条），已随交付放在
                    `前端数据\看板文案包\`。取不到就**不出**那几段，不编。
    graph.json  ← 图谱侧真正交给仿真的那一份
                  `清洗后<KEY>画像.json` + `t0状态_第0轮.json` + `事件配置.json`

★ 数字一律现算。换事件只改 `--key`，脚本里没有一处写死的事件名/条数。
"""

import argparse
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))       # …\端到端\前端数据
BASE = os.path.dirname(HERE)                            # …\端到端  ← 中间产物 在这一层
ROOT = os.path.dirname(BASE)                            # 03 交付根
EVENTS_DIR = os.path.join(BASE, "中间产物", "图谱")
if not os.path.isdir(EVENTS_DIR):
    EVENTS_DIR = os.path.join(BASE, "事件")
COLLECT_DATA = os.path.join(ROOT, "采集侧_可复现资料包", "02_数据与输入材料", "event")
# 采集侧交付的**成品**（processed 口径，Day0–Day1 就是从这个窗口切的）。
# 随交付放一份，是为了"只拿这份材料也能重算出同一张看板"——那份包在
# `全量数据包\采集侧\` 里，交付里原本没有。
COLLECT_PRODUCT = os.path.join(HERE, "采集成品")
PACK_DIR = os.path.join(HERE, "看板文案包")
META_FILE = os.path.join(HERE, "事件元数据.json")

# ── 口径（与采集侧成品同一套，改这里就是改口径，别散在函数里）──────────────
CHANNELS = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]
CH_ALIAS = {"监管渠道": "监管通报"}      # 采集侧词表 → 下游 6 类
ROLES = ["监管", "媒体", "KOL", "客户", "网民"]
SENTS = ["负面", "中性", "正面"]
BUCKET_H = 6                            # 情感演化曲线分桶宽度（小时）
DAY_TAG = {0: "曝光", 1: "发酵"}

BOARD_REQUIRED = ("post_id", "publish_time", "author_name", "role_type", "sentiment",
                  "channel", "title", "timespan_days", "event_t0")
GRAPH_NEED = [
    "agent_id", "role_type", "primary_channel", "persona_bio",
    "interest_topics", "influence_weight", "active_hours",
    "stance_bias", "emotion_sensitivity",
    "valence_base_0", "arousal_base_0", "valence_baseline", "arousal_baseline",
]
ROLE_ORDER = ["网民", "客户", "KOL", "媒体", "监管"]
# t0 原帖的动作词。引擎的记录员口径里第 0 轮那几条就是「发帖」；
# 换事件若出现别的动作，表里没有 ⇒ 当场停，不默认兜一个出去。
ACTION_CN = {"post": "发帖"}


class Bad(Exception):
    """数据不足以生成这一份 —— 调用方按"这一场跳过 / 这一场失败"处理。"""


# ── 通用 ────────────────────────────────────────────────────────────────
def load(path):
    if not os.path.exists(path):
        raise Bad("文件不存在：%s" % path)
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def dump(path, obj, indent=1):
    """`ensure_ascii=False` + LF：中文原样写出去，diff 读得懂，跨平台不抖。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=indent))
        f.write("\n")


def index_of(key):
    m = re.match(r"^EVT-(\d+)$", key or "")
    if not m:
        raise Bad("事件键要长成 EVT-001 这样：%r" % (key,))
    return int(m.group(1))


def usable_url(u):
    """原帖链接的可用性判定（与采集侧看板 `evt_dash_builder.py` 同一口径）。

    只滤一种情况：裸平台首页（`https://weibo.com/` 这种）—— 那不算"这条帖子的原文"，
    点过去是个信息流首页，比显示「—」更误导。其余原样返回。
    """
    if not u:
        return ""
    m = re.match(r"^https?://([^/]+)/?$", u)
    if m and m.group(1) in ("weibo.com", "m.weibo.cn", "www.weibo.com"):
        return ""
    return u


def md_runs(text):
    """`**加粗**` 切成 [{t, b}] —— 前端不用 dangerouslySetInnerHTML。"""
    out = []
    for i, seg in enumerate(re.split(r"\*\*(.+?)\*\*", text or "")):
        if seg:
            out.append({"t": seg, "b": i % 2 == 1})
    return out


_TIME_FMT = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_time(s):
    """`publish_time` 的三种写法都接：带秒、到分、只有日期（按当天 00:00）。"""
    t = (s or "").strip()
    for fmt in _TIME_FMT:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    raise Bad("publish_time 认不出来：%r" % (s,))


def meta_for(key):
    """事件级元数据（名称 / 主体）。**主体取不到就留空**，不拿别的事件的主体顶上。

    ★ 这里原来把 `subject` 兜成「众安保险」。在 EVT-001…013 这批全是众安事件时看不出问题，
      换个公司的新事件就会把图谱中心画成别人 —— 所以改成留空，由调用方报错说清楚怎么补。
    """
    m = {}
    if os.path.exists(META_FILE):
        try:
            m = (load(META_FILE) or {}).get(key) or {}
        except Exception:
            m = {}
    return {"name": m.get("name") or key,
            "subject": m.get("subject") or ""}


# ── 一、看板 ────────────────────────────────────────────────────────────
def find_posts(key, explicit=None):
    """采集侧成品的位置。

    顺序：`--posts` → 交付自带的 `采集成品\\<KEY>_processed.json`（采集侧成品原件，
    时间戳是完整的）→ **本链采集段刚产出的 `中间产物\\event{N}.json`** →
    `采集侧_可复现资料包\\…\\event\\event{N}.json`（整周期那份，按天切；个别帖的
    `publish_time` 只到日期，切出来会落在 00:00）。

    ★ 第三个候选是**新事件**那条路：`--raw` 跑起来时，采集段把成品写在
      `中间产物\\event{N}.json`，采集侧的历史成品里根本没有它。
    ★ 为什么不默认用最后一个：它是**整周期**成品，而且有 2 处只到日（EV1/EV4），
      拿它烘出来的看板和采集侧那份成品对不上 —— 看板要的是"采集侧交付的这张图"。
    """
    if explicit:
        return explicit
    n = index_of(key)
    p = os.path.join(COLLECT_PRODUCT, "%s_processed.json" % key)
    if os.path.exists(p):
        return p
    for p in (os.path.join(BASE, "中间产物", "event%d.json" % n),
              os.path.join(BASE, "中间产物", "图谱", key, "event%d.json" % n),
              os.path.join(BASE, "事件", key, "event%d.json" % n)):
        if os.path.exists(p):
            return p
    p = os.path.join(COLLECT_DATA, "event%d.json" % n)
    if os.path.exists(p):
        return p
    raise Bad("找不到这一场的采集成品：\n"
              "        %s\\%s_processed.json\n"
              "        %s\n"
              "        %s\n"
              "        （用 --posts 指一份）"
              % (COLLECT_PRODUCT, key, os.path.join(BASE, "中间产物", "event%d.json" % n), p))


def find_pack(key, explicit=None):
    if explicit:
        return explicit
    p = os.path.join(PACK_DIR, "%s_pack.json" % key)
    return p if os.path.exists(p) else None


def _add_days(y, m, d, k):
    """真日期加 k 天（纯手算，不引时区）。"""
    mdays = [31, 29 if _leap(y) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    d += k
    while d > mdays[m - 1]:
        d -= mdays[m - 1]
        m += 1
        if m > 12:
            m = 1
            y += 1
    return y, m, d


def _leap(y):
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def _ad_note(chain):
    """概述底下那行小注：解释「文案称…」里的那个数是什么的价。

    文案原文从 pack 的 `chain.sources[].subs` 里 `广告文案：…` 那一条取，
    **不在这里重抄数字**（重抄一手，换事件就印错）；取不到就返回空串，页面不渲染。
    """
    for s in (chain.get("sources") or []):
        for sub in (s.get("subs") or []):
            if sub.startswith("广告文案"):
                body = sub.split("：", 1)[1] if "：" in sub else sub
                return ("概述里的「文案」指涉事主体的对外宣传文案：%s。"
                        "其中的金额是这份产品的报价，首发帖正是拿它来讽刺。" % body)
    return ""


def build_board(key, posts_path=None, pack_path=None, day_max=1, generated=None):
    """采集侧成品 + 文案包 → 看板整包（与 `src\\demo\\board\\boardData.js` 同构）。"""
    raw = load(find_posts(key, posts_path))
    if isinstance(raw, dict):
        raw = raw.get("posts") or raw.get("data") or []
    if not isinstance(raw, list) or not raw:
        raise Bad("采集侧成品不是非空数组")
    sliced = [p for p in raw if int(p.get("timespan_days") or 0) <= day_max]
    if not sliced:
        raise Bad("按 Day0–Day%d 切完一条不剩（这份成品的 timespan_days 都对不上？）" % day_max)
    if len(sliced) != len(raw):
        raw = sliced

    pk = find_pack(key, pack_path)
    pack = load(pk) if pk else {}
    if not pk:
        print("    ⚠ 没有看板文案包 —— 概述/时间线徽标/媒体观点/传播链条这四段会是空的")

    rows = []
    for p in raw:
        for k in BOARD_REQUIRED:
            if k not in p:
                raise Bad("帖子 %s 缺字段 %r" % (p.get("post_id"), k))
        ch = CH_ALIAS.get(p["channel"], p["channel"])
        if ch not in CHANNELS:
            raise Bad("渠道不在六类白名单里：%r（帖子 %s）" % (p["channel"], p.get("post_id")))
        rows.append({
            "id": p["post_id"],
            "channel": ch,
            "platform": p.get("source_platform") or ch,
            "author": p["author_name"] or "（未署名）",
            "role": p["role_type"],
            "sentiment": p["sentiment"],
            # 统一成「YYYY-MM-DD HH:MM」：看板页头与时间线都按这个长度切。
            # 原文只到日期的那种按当天 00:00 补（来源那一行会照实写，不冒充有值）。
            "time": parse_time(p["publish_time"]).strftime("%Y-%m-%d %H:%M"),
            "title": (p.get("title") or p.get("content") or "").strip().splitlines()[0] or "（无标题）",
            "excerpt": " ".join((p.get("content") or "").split())[:160],
            "like": int(p.get("like_cnt") or 0),
            "comment": int(p.get("comment_cnt") or 0),
            "repost": int(p.get("repost_cnt") or 0),
            "collect": int(p.get("collect_cnt") or 0),
            "day": int(p["timespan_days"]),
            "t0": p["event_t0"],
            "topic_hit": list(p.get("topic_hit") or []),
            "entity": list(p.get("extract_entity") or []),
            "url": usable_url(p.get("post_url") or ""),
        })
    rows.sort(key=lambda r: (r["time"], r["id"]))

    n = len(rows)
    ch_c = Counter(r["channel"] for r in rows)
    sent_c = Counter(r["sentiment"] for r in rows)
    role_c = Counter(r["role"] for r in rows)
    day_c = Counter(r["day"] for r in rows)
    topic_c = Counter(t for r in rows for t in r["topic_hit"])
    ent_c = Counter(e.replace("（", "(").replace("）", ")") for r in rows for e in r["entity"])
    inter = sum(r["like"] + r["comment"] + r["repost"] for r in rows)

    t0 = rows[0]["t0"]
    window_days = max(day_c)
    neg, neu, pos = sent_c.get("负面", 0), sent_c.get("中性", 0), sent_c.get("正面", 0)
    bad_role = sorted({r["role"] for r in rows} - set(ROLES))
    if bad_role:
        raise Bad("角色不在 5 类白名单里：%s" % "、".join(bad_role))
    top_roles = sorted(role_c.items(), key=lambda kv: (-kv[1], ROLES.index(kv[0])))
    dom = max([("负面", neg), ("中性", neu), ("正面", pos)], key=lambda x: x[1])

    y, m, dd = [int(x) for x in t0.split("-")]
    day_rows = []
    for d in range(window_days + 1):
        real = _add_days(y, m, dd, d)
        day_rows.append({
            "day": d,
            "date": "%04d-%02d-%02d" % real,
            "label": "Day%d" % d,
            "title": "Day%d · %s" % (d, DAY_TAG.get(d, "传播")),
            "n": day_c.get(d, 0),
        })

    badges_by_id = {b["post_id"]: b for b in (pack.get("timeline") or [])}
    timeline = []
    for d in day_rows:
        items = []
        for r in rows:
            if r["day"] != d["day"]:
                continue
            meta = badges_by_id.get(r["id"]) or {}
            items.append({
                "id": r["id"],
                "time": r["time"][5:16],
                "plat": meta.get("plat") or ("%s · %s" % (r["channel"], r["author"])),
                "title": r["title"],
                "role": r["role"],
                "sentiment": r["sentiment"],
                "key": [b[4:] for b in (meta.get("badges") or []) if b.startswith("key:")],
                "warn": meta.get("warn"),
            })
        timeline.append({"day": d["day"], "title": d["title"] + ("（%d 条）" % d["n"]),
                         "date": d["date"], "items": items})

    # 情感演化按**观测窗口**分桶（不是按首末帖）：一两条帖按首末分桶会塌成一个点。
    day0 = datetime.strptime(t0, "%Y-%m-%d")
    n_bucket = max(1, (window_days + 1) * 24 // BUCKET_H)
    buckets = []
    for i in range(n_bucket):
        start = i * BUCKET_H
        buckets.append({"label": "D%d·%02d-%02dh" % (start // 24, start % 24, start % 24 + BUCKET_H),
                        "d": start // 24, "neg": 0, "neu": 0, "pos": 0})
    for r in rows:
        t = parse_time(r["time"])
        i = int((t - day0).total_seconds() // (BUCKET_H * 3600))
        i = max(0, min(n_bucket - 1, i))
        kk = {"负面": "neg", "中性": "neu", "正面": "pos"}.get(r["sentiment"])
        if kk is None:
            raise Bad("情感不在三分类里：%r（帖子 %s）" % (r["sentiment"], r["id"]))
        buckets[i][kk] += 1
    for b in buckets:
        b["total"] = b["neg"] + b["neu"] + b["pos"]

    cloud = [{"word": w, "kind": "topic", "n": k} for w, k in topic_c.items()]
    cloud += [{"word": w, "kind": "entity", "n": k} for w, k in ent_c.items() if w not in topic_c]
    cloud.sort(key=lambda x: (-x["n"], 0 if x["kind"] == "topic" else 1, x["word"]))

    chan_rows = []
    for c in CHANNELS:
        who = []
        for r in rows:
            if r["channel"] == c and r["author"] not in who:
                who.append(r["author"])
        chan_rows.append({"name": c, "n": ch_c.get(c, 0),
                          "pct": round(ch_c.get(c, 0) / n * 100, 1),
                          "who": "、".join(who) or "—"})

    cross = []
    for c in CHANNELS:
        ps = [r for r in rows if r["channel"] == c]
        if not ps:
            continue
        rc = Counter(p["role"] for p in ps)
        cross.append("%s %d → %s" % (c, len(ps), "、".join("%s %d" % kv for kv in rc.items())))

    chain = dict(pack.get("chain") or {})
    chain.setdefault("sources", [])
    chain["weibo"] = chain.get("weibo") or []
    chain["window_note"] = (
        "Day0–Day1 窗口内未采集到微博扩散；微博侧的二次传播发生在 Day2 之后，不在本次采集范围内"
        if not chain["weibo"] else "")

    types = Counter(str(p.get("event_type") or "").strip() for p in raw)
    etype = types.most_common(1)[0][0] if types else ""
    mt = meta_for(key)
    base = {
        "event": {
            "id": key,
            "name": mt["name"],
            "type": etype.replace("类", "") or "未分类",
            "type_raw": etype or "未分类",
            "subject": mt["subject"],
            "t0": t0,
            "t1": day_rows[-1]["date"],
            "window_label": "%s ~ %s（Day0–Day%d）" % (t0, day_rows[-1]["date"], window_days),
            "generated": generated or datetime.now().strftime("%Y-%m-%d"),
        },
        "overview": md_runs(pack.get("overview")),
        "overview_note": _ad_note(chain),
        "kpi": {
            "posts": n,
            "window_days": window_days + 1,
            "channels_hit": sum(1 for c in CHANNELS if ch_c.get(c)),
            "neg_pct": round(neg / n * 100),
            "top_roles": " / ".join(str(c) for _, c in top_roles[:2]),
            "dominant": dom[0],
            "roles": sum(1 for r in ROLES if role_c.get(r)),
            "authors": len({r["author"] for r in rows}),
            "negative": neg, "neutral": neu, "positive": pos,
            "interactions": inter,
        },
        "timeline": timeline,
        "channels": chan_rows,
        "channel_cross": cross,
        "sentiments": [{"name": s, "n": sent_c.get(s, 0)} for s in SENTS],
        "roles": [{"name": r, "n": role_c.get(r, 0)} for r in ROLES],
        "days": day_rows,
        "trend": {"bucket_h": BUCKET_H, "buckets": buckets},
        "cloud": cloud,
        "opinion": pack.get("opinion") or {"groups": [], "points": []},
        "chain": chain,
        "posts": [{k: r[k] for k in ("id", "channel", "platform", "author", "role", "sentiment",
                                     "time", "day", "title", "excerpt",
                                     "like", "comment", "repost", "collect", "url")}
                  for r in rows],
        "caliber": [
            "情感 / 角色 / 主题 / 实体标注沿用采集侧成品（LLM 标注 + 规则判定），本看板只做格式转换。",
            "情感口径 = 发布者本人褒贬：单纯转述通稿、无明显褒贬的记「中性」，不因事件本身负面就判负面。",
            "渠道为 6 类封闭白名单；监管通报 = 中央部委 / 监管机构及党央媒官方渠道，地方与商业媒体转载归新闻门户。",
            "主题词取自 topic_hit、实体取自 extract_entity；词云字号按出现条数缩放。",
            "采集窗口为 Day0–Day1（t0 起 48 小时）—— 这批帖子就是知识图谱与仿真推演的初始态输入。",
            "明细表的「原文」可点，新标签打开原帖（需要联网才能访问）；看板本页不加载任何外部资源，离线照常打开。",
            "无原帖链接的帖子（采集时未记录 URL，或只有平台首页）链接列显示「—」。",
        ],
    }

    # 自检：任何一张分布加起来必须等于总帖数 —— 半份数据的样子是"某个柱子凭空是 0"
    assert sum(x["n"] for x in base["channels"]) == n, "渠道计数不等于总帖数"
    assert sum(x["n"] for x in base["sentiments"]) == n, "情感计数不等于总帖数"
    assert sum(x["n"] for x in base["roles"]) == n, "角色计数不等于总帖数"
    assert sum(x["n"] for x in base["days"]) == n, "逐日计数不等于总帖数"
    assert sum(b["total"] for b in buckets) == n, "情感演化分桶之和 ≠ 总帖数"
    assert sum(len(t["items"]) for t in timeline) == n, "时间线条数 ≠ 总帖数"
    return base


# ── 二、图谱 ────────────────────────────────────────────────────────────
def _pairs(d):
    """字典 → [[键, 值], …]，按键名排（不排序会产生"值没变、顺序变了"的假 diff）。"""
    return [[k, d[k]] for k in sorted(d)]


def _name_key(agents):
    return {a.get("agent_id"): a for a in agents if isinstance(a.get("agent_id"), str)}


def build_graph(key, cfg_path=None, subject=None):
    """画像 + t0 快照 + 事件配置 → 图谱整包（与 `src\\demo\\step2\\graph.js` 同构）。"""
    evtdir = os.path.join(EVENTS_DIR, key)
    cfp = cfg_path or os.path.join(evtdir, "事件配置.json")
    cfg = load(cfp)
    base = os.path.dirname(os.path.abspath(cfp))

    pf = os.path.join(base, cfg.get("persona_file") or "")
    tf = os.path.join(base, cfg.get("t0_file") or "")
    pdoc = load(pf)
    t0 = load(tf)

    agents = pdoc.get("agents")
    if not isinstance(agents, list) or not agents:
        raise Bad("画像文件里没有 agents[]，或者它是空的")

    center = (subject or cfg.get("subject") or meta_for(key)["subject"] or "").strip()
    if not center:
        raise Bad("取不到中心实体：事件配置里没写 subject，`事件元数据.json` 也没登记 %s。\n"
                  "        （新事件用 `--subject <主体名>` 指一个，或先把它登记进 "
                  "`前端数据\\事件元数据.json`）" % key)

    t0_time = (cfg.get("t0_time") or "").strip()
    if len(t0_time) < 10:
        raise Bad("事件配置.json 的 t0_time 取不出日期：%r" % t0_time)
    built_at = t0_time[:10]

    brief_src = "event_brief"
    brief = (cfg.get("event_brief") or "").strip()
    ign = t0.get("injected_posts") or []
    # 知识图谱侧 v8 包起 `event_brief` 装的是**起火帖正文**，不是简介；原样贴一段
    # 网友的帖子会读成"中心本人在骂人"。所以这种情况下按包里的字段现拼一句，
    # 并把出处标到页面上（`BRIEF_SRC` → 卡片上那行小注），不冒充 `event_brief` 原文。
    if (ign and brief == (ign[0].get("content") or "").strip()) or center not in brief:
        seen = []
        for p in ign:
            if p.get("channel") and p["channel"] not in seen:
                seen.append(p["channel"])
        brief = ("%s（t0）起，%s因「%s」舆情成为图谱中心，起火点共 %d 条原帖（%s）。"
                 % (built_at, center, cfg.get("event_type") or "该事件", len(ign),
                    "、".join(seen)))
        brief_src = "derived"

    posts = t0.get("injected_posts")
    if not isinstance(posts, list) or not posts:
        raise Bad("t0 快照里没有 injected_posts —— 那就无从知道「中心连着谁」")

    acted = {a.get("agent_id"): a.get("action") for a in (t0.get("agents") or [])}
    by_id = _name_key(agents)
    edges = []
    warn = []
    for p in posts:
        aid = p.get("poster_agent_id")
        if aid not in by_id:
            raise Bad("t0 原帖 %s 的发帖人 %r 不在画像库里" % (p.get("post_id"), aid))
        act = acted.get(aid)
        if act not in ACTION_CN:
            raise Bad("发帖人 %s 在 t0 的动作是 %r，ACTION_CN 里没有这个词" % (aid, act))
        # 渠道对不上只提示、不拦：监管/媒体的号常把通告发在新闻门户上，图上照实
        # 画原帖渠道（边上的 `channel` 取 `p["channel"]`），不拿画像主渠道去覆盖它。
        if p.get("channel") != by_id[aid].get("primary_channel"):
            warn.append("t0 原帖 %s 落在「%s」，该号画像主渠道是「%s」"
                        % (p.get("post_id"), p.get("channel"), by_id[aid].get("primary_channel")))
        edges.append({
            "from": center, "to": aid, "action": ACTION_CN[act],
            "post_id": p.get("post_id"), "channel": p.get("channel"),
            "title": p.get("title") or "",
            "likes": int(p.get("like_cnt") or 0),
            "comments": int(p.get("comment_cnt") or 0),
        })
    if len({e["to"] for e in edges}) != len(edges):
        raise Bad("同一个人在 t0 发了不止一条帖 —— 画出来会有两条边落在同一个点上")

    bad = []
    for a in agents:
        for k in GRAPH_NEED:
            if a.get(k) is None or a.get(k) == "":
                bad.append("%s 缺字段 %s" % (a.get("agent_id") or "（无 id）", k))
        if a.get("role_type") not in ROLE_ORDER:
            bad.append("%s 的角色类型是 %r" % (a.get("agent_id"), a.get("role_type")))
        for k in ("interest_topics", "stance_bias", "emotion_sensitivity"):
            if not isinstance(a.get(k), dict) or not a[k]:
                bad.append("%s 的 %s 不是非空字典" % (a.get("agent_id"), k))
        if not isinstance(a.get("active_hours"), list) or not a["active_hours"]:
            bad.append("%s 的 active_hours 不是非空数组" % a.get("agent_id"))
    if bad:
        for b in bad[:12]:
            print("      ✗ %s" % b)
        raise Bad("画像文件有 %d 处不合规，先修数据再生成" % len(bad))
    dup = sorted({i for i, n in Counter(a["agent_id"] for a in agents).items() if n > 1})
    if dup:
        raise Bad("agent_id 有重复：%s" % "、".join(dup[:10]))

    nodes = [{
        "id": a["agent_id"], "role": a["role_type"], "channel": a["primary_channel"],
        "bio": a["persona_bio"], "influence": a["influence_weight"],
        "hours": [int(h) for h in sorted(a["active_hours"])],
        "topics": _pairs(a["interest_topics"]),
        "stance": _pairs(a["stance_bias"]),
        "sens": _pairs(a["emotion_sensitivity"]),
        "v0": a["valence_base_0"], "a0": a["arousal_base_0"],
        "vb": a["valence_baseline"], "ab": a["arousal_baseline"],
    } for a in agents]

    chans = Counter(a["primary_channel"] for a in agents)
    channels = [c for c, _ in sorted(chans.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "EVENT_ID": cfg.get("event_id") or key,
        "BUILT_AT": built_at,
        "CENTER": {"id": center, "label": center, "kind": "事件主体", "brief": brief},
        "BRIEF_SRC": brief_src,
        "NODES": nodes,
        "EDGES": edges,
        "CHANNELS": channels,
        "ENTITY_TOTAL": len(nodes) + 1,
        "EDGE_TOTAL": len(edges),
        "_warn": warn,          # 只在内存里传，落盘前 pop 掉
    }


# ── 三、落盘 ────────────────────────────────────────────────────────────
def generate(key, posts=None, pack=None, subject=None, day_max=1,
             board=True, graph=True, out_dir=None, quiet=False, generated=None):
    evtdir = out_dir or os.path.join(EVENTS_DIR, key)
    if out_dir:
        os.makedirs(evtdir, exist_ok=True)     # 暂存区可能是新建的
    if not os.path.isdir(evtdir):
        raise Bad("没有这个事件目录：%s（先跑图谱/仿真那一段）" % evtdir)
    out = {}

    if board:
        try:
            b = build_board(key, posts, pack, day_max, generated)
            dump(os.path.join(evtdir, "board.json"), b)
            out["board"] = (b, os.path.join(evtdir, "board.json"))
        except Bad as e:
            if not quiet:
                print("  ✗ 看板：%s" % e)
            out["board"] = (None, str(e))

    if graph:
        try:
            g = build_graph(key, os.path.join(evtdir, "事件配置.json"), subject)
            warns = g.pop("_warn", [])
            if warns and not quiet:
                for w in warns[:6]:
                    print("      · %s" % w)
                if len(warns) > 6:
                    print("      · …另有 %d 处" % (len(warns) - 6))
            dump(os.path.join(evtdir, "graph.json"), g)
            out["graph"] = (g, os.path.join(evtdir, "graph.json"))
        except Bad as e:
            if not quiet:
                print("  ✗ 图谱：%s" % e)
            out["graph"] = (None, str(e))
    return out


def report(key, res, quiet=False):
    b, g = res.get("board"), res.get("graph")
    parts = []
    if b and b[0]:
        d = b[0]
        parts.append("看板 %d 条 / %d 渠道 / Day0–Day%d"
                     % (d["kpi"]["posts"], d["kpi"]["channels_hit"], d["kpi"]["window_days"] - 1))
    elif b:
        parts.append("看板 ✗")
    if g and g[0]:
        parts.append("图谱 %d 实体 / %d 条 t0 边" % (g[0]["ENTITY_TOTAL"], g[0]["EDGE_TOTAL"]))
    elif g:
        parts.append("图谱 ✗")
    line = "  %-9s %s" % (key, " ｜ ".join(parts))
    if not quiet:
        print(line)
    return bool(b and b[0]) or bool(g and g[0])


def main():
    ap = argparse.ArgumentParser(description="生成前端要的 board.json / graph.json（每事件一份）")
    ap.add_argument("--key", help="事件键，如 EVT-002")
    ap.add_argument("--all", action="store_true", help="把 中间产物\\图谱\\ 下的事件全跑一遍")
    ap.add_argument("--posts", help="采集侧成品 JSON（默认用交付自带的那一份）")
    ap.add_argument("--pack", help="看板文案包 JSON（默认 前端数据\\看板文案包\\<KEY>_pack.json）")
    ap.add_argument("--subject", help="图谱中心实体（默认取事件配置的 subject）")
    ap.add_argument("--day-max", type=int, default=1, help="采集窗口切到第几天（默认 1 = Day0–Day1）")
    ap.add_argument("--only", choices=("board", "graph"), help="只生成其中一份")
    ap.add_argument("--generated", help="看板上那个生成日期（默认今天）")
    # ★ 2026-10-21：落点可指定。链路在「采集段刚跑完、图谱段还没开始」时要先把看板
    #   烘到一个**临时目录**（那一刻事件目录还不存在，而且图谱段一开跑会 rmtree 它），
    #   等图谱段出了成品再搬进去 —— 见 `跑端到端_v3.py` 的 `publish()` / `_adopt_staged()`。
    ap.add_argument("--out-dir", dest="out_dir", help="改落点（默认 中间产物\\图谱\\<KEY>）")
    a = ap.parse_args()

    if a.all:
        keys = sorted(d for d in os.listdir(EVENTS_DIR)
                      if re.match(r"^EVT-\d+$", d)
                      and os.path.exists(os.path.join(EVENTS_DIR, d, "事件配置.json")))
    elif a.key:
        keys = [a.key]
    else:
        sys.exit("✗ 要么 --key EVT-002，要么 --all")

    print("=" * 88)
    print("生成前端数据 ｜ 事件 %d 个 ｜ 事件目录 %s" % (len(keys), EVENTS_DIR))
    print("=" * 88)
    ok_n = 0
    for k in keys:
        res = generate(k, a.posts, a.pack, a.subject, a.day_max,
                       board=a.only != "graph", graph=a.only != "board",
                       generated=a.generated, quiet=True, out_dir=a.out_dir)
        ok_n += 1 if report(k, res) else 0
        for kind in ("board", "graph"):
            v = res.get(kind)
            if v and v[0]:
                print("            → %s" % v[1])
            elif v:
                print("            ✗ %s：%s" % (kind, v[1]))
    print("-" * 88)
    print("%d/%d 个事件生成了数据" % (ok_n, len(keys)))
    return 0 if ok_n else 1


if __name__ == "__main__":
    sys.exit(main())
