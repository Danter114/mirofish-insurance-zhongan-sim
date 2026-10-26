#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# -*- coding: utf-8 -*-



build_kg.py —— ① 建图：事件包 -> 事件知识图谱（L1 事实层）

规则（见 ../../建图与定槽位规则_MVP.md）：
  M2  事件成立性门禁（两级：硬门禁 + 质量三标）
  M3  三条输入过滤（转发去重 / mention 不进发声主体 / 冒名别名表）
  M4  实体抽取（3 类 + 归类 + 别名归一）
  M5  议题归一（优先落 taxonomy 叶子；★ 事件无关：词表外议题自动挂成新节点，不再丢弃）
  M6  热度 heat = 0.30 + 0.70 * minmax(议题互动量) —— 互动量 = 赞/评论/收藏/转发 压缩加权和
  M10 图谱中不得出现 (:Agent) 节点
  M11 每条关系边必须带 source_url
"""

from __future__ import annotations
import json
from pathlib import Path
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from common import largest_remainder, norm_minmax, parse_time, load_config
from poster_stance import classify as classify_stance
from stance_value import (aggregate as aggregate_stance, calibrate as calibrate_stance_value,

                          to_bin as stance_to_bin)


TEXT_LEVELS = ("native", "paraphrase", "description", "none")

# ★ M6 热度 = 互动量的多少（v1.5）。四路互动分别对应采集侧的四个字段。
ENG_FIELDS = (("like", "like_cnt"), ("comment", "comment_cnt"),
              ("collect", "collect_cnt"), ("repost", "repost_cnt"))


def _num(v):
    """把 '1.5万' / '' / 2977 / '2977' / None 统一成 float。互动量字段脏得很，必须容错。"""
    s = str(v if v is not None else "").strip().replace(",", "").replace("+", "")
    if not s or s.lower() in ("nan", "none", "null", "-", "—"):
        return 0.0
    m = re.match(r"^([0-9.]+)\s*万$", s)
    if m:
        return float(m.group(1)) * 10000.0
    m = re.match(r"^([0-9.]+)\s*[千kK]$", s)
    if m:
        return float(m.group(1)) * 1000.0
    m = re.match(r"^([0-9.]+)\s*[亿]$", s)
    if m:
        return float(m.group(1)) * 100000000.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _worst_conf(confs):
    """合并多帖时取【最保守】的置信度：low < medium < high。

    一条动作由多帖合并而来时，只要其中有一帖是低置信兜底，就不该把这个动作
    当成高置信锚点用。
    """
    rank = {"low": 0, "medium": 1, "high": 2}
    vals = [c for c in (confs or []) if c in rank]
    return min(vals, key=lambda c: rank[c]) if vals else None


def post_engagement(d, hcfg):
    """一条帖子的互动量。

    ★ 四路互动各自 log1p 压缩后再加权求和，不是把原始数直接相加。
      原因：互动量是重尾分布（实测事件1 的 like_cnt 从 1 到 26000 跨 4 个数量级），
      直接相加会让「谁有一条爆款谁就是唯一热点」，其余议题全被压到地板。
      log1p 是单调的，不会改变「互动多就更热」这个方向，只是不让单个爆款通吃。
      需要原始口径时把 config 的 heat.compress 改成 "none"。
    """
    w = (hcfg or {}).get("weights") or {}
    comp = (hcfg or {}).get("compress", "log1p")
    vals = {k: _num(d.get(f)) for k, f in ENG_FIELDS}
    if comp == "none":
        total = sum(w.get(k, 1.0) * v for k, v in vals.items())
    else:
        total = sum(w.get(k, 1.0) * math.log1p(v) for k, v in vals.items())
    return total, vals

T0_MODES = ("strict", "day", "window", "days")


def resolve_t0_policy(ev, cfg):
    """把「t0 及以前」这件事，变成一段**可计算、可审计**的时间区间。

    组长规范（环节0）：t0 及之前 -> 建图 + 第 0 轮注入；t0 之后 -> 留出做回测对照。
    但「t0 及之前」的边界有三种读法，config/slots.json -> t0_policy.mode 选：

      strict  E1  publish_time <= t0                        字面口径，最保守
      day     E3  publish_time <= t0 所在自然日 23:59:59     「t0 当天及以前」
      window  E2  publish_time <= t0 + window_hours          「t0 起的观察窗」
      days    E4  publish_time <= (t0 所在自然日 + days-1) 23:59:59
                  ★ 2026-10-13 新增【现行默认】—— 用户口径：t0 = Day0 + Day1，
                    Day2 及以后一律不算 t0（这一层不关注 t0 之后发生的任何东西）。
                    注意 days 模式锚在【自然日边界】，不是 t0 的时刻 ——
                    用 window_hours 算不出来（evt1 t0=05-21 22:00，+48h 到 05-23 22:00，
                    仍会吃进 Day2 的帖）。

    ★ 无论哪种口径，返回值里的 cutoff_time 就是「下游能看见的时间上界」：
      凡进图谱、进第 0 轮注入的内容，publish_time 必须 <= cutoff_time。
      再往前留一个前置窗 pre_window_hours（默认 0），用于「t0 之前已有铺垫」的场景。
    """
    th = cfg.get("thresholds", {})
    pol = dict(cfg.get("t0_policy") or {})
    mode = (pol.get("mode") or "window").lower()
    if mode not in T0_MODES:
        mode = "window"
    win = float(pol.get("window_hours", th.get("t0_window_hours", 24)))
    days = int(pol.get("days", th.get("t0_days", 2)))
    pre = float(pol.get("pre_window_hours", 0) or 0)
    anchor = (pol.get("anchor") or "ignition").lower()

    t0 = parse_time(ev.get("first_seen_time"))
    cutoff = None
    if t0 is not None:
        if mode == "strict":
            cutoff = t0
        elif mode == "day":
            cutoff = t0.replace(hour=23, minute=59, second=59, microsecond=0)
        elif mode == "days":
            # ★ 锚在自然日边界：从 t0 所在日起算 days 个自然日（days=2 -> Day0 + Day1）
            cutoff = (t0 + timedelta(days=max(1, days) - 1)).replace(
                hour=23, minute=59, second=59, microsecond=0)
        else:
            cutoff = t0 + timedelta(hours=win)
    floor = (t0 - timedelta(hours=pre)) if t0 is not None else None
    return {"mode": mode, "anchor": anchor, "window_hours": win, "days": days, "pre_window_hours": pre,
            "t0": ev.get("first_seen_time"), "t0_dt": t0, "cutoff": cutoff, "floor": floor,
            "cutoff_time": cutoff.strftime("%Y-%m-%d %H:%M:%S") if cutoff else None,
            "floor_time": floor.strftime("%Y-%m-%d %H:%M:%S") if floor else None,
            "semantics": {"strict": "t0 时刻及以前（字面口径 E1）",
                          "day": "t0 所在自然日及以前（口径 E3）",
                          "window": "t0 起 %g 小时观察窗（口径 E2）" % win,
                          "days": "t0 所在自然日起 %d 个自然日（口径 E4，现行默认；用户口径 Day0+Day1）" % days}[mode]}


def classify_text(d):
    """判定这条帖子的 raw_content 到底是「原文 / 转述 / 元描述 / 空」。
    这件事必须机器可见 —— 它决定议题能不能抽出来、agent 有没有东西可感知。
    优先看采集侧自报的 raw_content_note，没有标注时按启发式判。"""
    note = (d.get("raw_content_note") or "")
    txt = (d.get("raw_content") or "").strip()
    if not txt:
        return "none"
    if "未采集" in note or "仅获取标题" in note:
        return "description"
    if len(txt) < 40:
        return "description"
    if "转述" in note or "未逐字核对" in note or "摘要" in note:
        return "paraphrase"
    # 没有标注时按启发式：正文里出现「用户/记者/采集员」当主语，基本是转述或说明
    if re.search(r"(用户|记者|采集|发帖|帖子).{0,6}(表示|称|反映|发布|记录)", txt):
        return "paraphrase"
    return "native" if not note else "paraphrase"


def _pick_ignition(docs):
    """定位 t0 核心发起者（起火点）。采集侧有显式标注就用，否则按「最早 + 互动量最高」推。"""
    if not docs:
        return None
    marked = [d for d in docs if d.get("is_ignition") or d.get("ignition")]
    pool = marked or docs

    def score(d):
        t = parse_time(d.get("publish_time"))
        ts = t.timestamp() if t else 9e18        # 越早越小
        eng = sum(_num(d.get(f)) for _, f in ENG_FIELDS)
        return (ts, -eng)
    top = sorted(pool, key=score)[0]
    return {"doc_id": top.get("doc_id"), "author_id": top.get("author_id"),
            "author_name": top.get("author_name"), "poster_role": top.get("poster_role"),
            "channel": top.get("channel_std"), "time": top.get("publish_time"),
            "source_url": top.get("source_url"),
            "method": "采集侧标注" if marked else "按最早+互动量推定（采集侧未标 ignition）"}


# ★ 词表外议题（开放词汇）的热度折扣：原词不如归一后的议题可信
OPEN_HEAT_DISCOUNT = 0.70
# 软挂载的最小字符相似度阈值（低于此不成边，做孤儿节点）
OOV_MOUNT_MIN_SIM = 0.34


def _char_sim(a, b):
    """字符级相似度（Dice 系数，中文字符二元组）。纯离线、可复现。

    ★ 事件无关（event-agnostic）建图的挂载函数：
      正则规则表只能匹配 47 个已知议题；新事件带来的词表外议题，
      用本函数找最近的已知议题作 parent，建软边继承一部分历史热度。
    """
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    def _bg(s):
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
    A, B = _bg(a), _bg(b)
    return (2.0 * len(A & B) / (len(A) + len(B))) if (A or B) else 0.0


class KGBuilder:
    def __init__(self, extractor, taxonomy, cfg, stance_cfg=None, stance_value_cfg=None):
        self.ex = extractor
        self.tax = taxonomy
        self.cfg = cfg
        # ★ 组长要求：t0 状态要标注**首发发帖者的类型**（当事人型=客户 / 围观型=网民），
        #   判定依据是帖子内容（自己受损 vs 仅评论宣泄），不是账号标签。
        self.stance_cfg = stance_cfg or {}
        # ★ t0 立场值标定（stance ∈ [-1,+1]）：三值只是它的分档，内部保持连续量。
        #   与上面的「发帖者类型」是两回事，别混：类型回答「谁在说话」，立场回答「他站哪边」。
        self.stance_value_cfg = stance_value_cfg or {}
        self.leaves = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
        self.th = cfg["thresholds"]

    # ---------------- M2 事件成立性门禁
    def gate(self, ev, docs):
        members = [docs[m] for m in ev["member_docs"] if m in docs]
        # 预计算每条帖距 t0 的时间差（t0 状态快照与口径核对都要用）
        pol = resolve_t0_policy(ev, self.cfg)
        t0 = pol["t0_dt"]
        self._t0 = ev.get("first_seen_time")
        self._t0_policy = pol
        for d in members:
            t = parse_time(d.get("publish_time"))
            if t and t0:
                delta = (t - t0).total_seconds()
                d["_hours_from_t0"] = round(delta / 3600.0, 1)
                d["_days_from_t0"] = int(delta // 86400)
            else:
                d["_hours_from_t0"] = None
                d["_days_from_t0"] = None
        # ★ 组长规范（环节0）：t0 及之前 -> 建图 + 第 0 轮注入；t0 之后 -> 留出做回测对照。
        #   边界由 t0_policy.mode 决定（strict / day / window），统一落到 cutoff_time 上。
        #   进了推演就等于让 agent 提前知道未来，所以这里必须是硬切分。
        if t0 is None or all(d["_hours_from_t0"] is None for d in members):
            members, self._holdout = members, []          # 时间解析不出来，整体放行
        else:
            in_win = []
            for d in members:
                t = parse_time(d.get("publish_time"))
                if t is None:
                    in_win.append(d)                       # 时间缺失的单条不因口径被误杀
                elif t <= pol["cutoff"] and (pol["floor"] is None or t >= pol["floor"]):
                    in_win.append(d)
            self._holdout = [d for d in members if d not in in_win]
            members = in_win or members[:1]
        hard = []
        if not members:
            hard.append("无有效帖子")
        if not ev.get("first_seen_time"):
            hard.append("无 first_seen_time（没有时间原点）")
        span = ev.get("time_span_days")
        if span is None and members:
            ts = sorted(t for t in (parse_time(d.get("publish_time")) for d in members) if t)
            span = (ts[-1] - ts[0]).days if ts else None
        combos = {(d.get("channel_std"), d.get("poster_role")) for d in members}
        q = {"multi_post": len(members) >= self.th["event_min_docs"],
             "focused": span is not None and span <= self.th["event_max_span_days"],
             "multi_source": len(combos) >= 2}
        quality = "full" if all(q.values()) else "thin"
        return (not hard), hard, quality, q, members, span

    # ---------------- M3 输入过滤
    @staticmethod
    def dedupe(members):
        """同正文只算一个独立发声（转发不等于新声音）。"""
        kept, seen = [], set()
        for d in members:
            key = (d.get("raw_content") or "")[:80]
            if key and key in seen:
                continue
            seen.add(key)
            kept.append(d)
        return kept

    # ---------------- M5 议题归一 + M6 互动量归集
    def extract_issues(self, kept):
        """返回 (议题提及数, OOV, 议题级渠道分布, 议题级互动量明细)。

        ★ 三项都**必须按议题单独统计**：
          渠道分布按事件级统计会让同角色所有槽位挤在一个渠道上；
          互动量按事件级统计则每个议题热度都一样，M6 直接失去意义。
        """
        cnt, oov = Counter(), set()
        # ★★ 事件无关（event-agnostic）：开放议题入口 ——
        #   正则规则表只能匹配 47 个已知议题；采集侧的 topic_hit / topic_list 是
        #   【每个事件自带】的主题词，不受我们的词表限制。词表外的词在这里收进 oov，
        #   再由图谱把它们挂成新节点（见 build 里的 _mount_oov）。
        _known = getattr(self.ex, "leaves", set()) or set()
        ch_of = defaultdict(Counter)
        # ★★ 2026-10-07：逐渠道【互动量】（不只是帖数）——
        #   event1 实测：微博 15 帖只有 132 互动（均 9，通稿转发没人理），
        #              小红书 3 帖却有 28165 互动（均 9388）。
        #   只按帖数分配渠道人数 -> 微博被严重高估。渠道权重必须看互动量。
        eng_ch = defaultdict(Counter)
        hcfg = self.cfg["thresholds"].get("heat") or {}
        eng_of = defaultdict(lambda: {"posts": 0, "engagement": 0.0, "engagement_max": 0.0,
                                      "like": 0.0, "comment": 0.0,
                                      "collect": 0.0, "repost": 0.0, "imputed": 0})
        # 先把每条帖子的互动量算出来，便于对「四个字段全空」的帖子做插补
        per_doc = {}
        for d in kept:
            per_doc[d.get("doc_id")] = post_engagement(d, hcfg)
        has_data = sorted(v for v, kv in per_doc.values() if any(kv.values()))
        impute = "zero"
        fill = 0.0
        if (hcfg.get("impute_missing") or "median") == "median" and has_data:
            impute = "median"
            fill = has_data[len(has_data) // 2]
        for d in kept:
            text = " ".join([(d.get("raw_content") or ""), (d.get("title") or ""),
                             " ".join(str(x) for x in (d.get("entities") or []))])
            r = self.ex.extract(text, d)
            e_val, vals = per_doc[d.get("doc_id")]
            blank = not any(vals.values())
            if blank:
                e_val = fill
            # ★ 逐帖记录它涉及哪些议题（给具名槽位定 issue_leaf 用）
            if not hasattr(self, "_post_issues"):
                self._post_issues = {}
                self._post_issues_src = {}
            # ★ 三级归一化链 ①->②：读采集侧的 topic_hit（唯一逐帖不同的字段），
            #   用同一套抽取器归一到 taxonomy 议题节点，再与全文抽取的议题取并集。
            #   只靠全文抽取会漏掉「帖子正文没直说、但采集侧已标注」的议题。
            _hit_issues = []
            _hit = d.get("topic_hit") or []
            if _hit:
                _hr = self.ex.extract(" ".join(str(x) for x in _hit), d)
                _hit_issues = [it["leaf"] for it in _hr["issues"]]
            _ls = list(dict.fromkeys([it["leaf"] for it in r["issues"]] + _hit_issues))
            if _ls:
                self._post_issues[d.get("doc_id")] = _ls
                self._post_issues_src[d.get("doc_id")] = {
                    "from_text": [it["leaf"] for it in r["issues"]],
                    "from_topic_hit": _hit_issues, "topic_hit": _hit}
            # ★ 开放收集：采集侧主题词里，不在 47 议题词表内的 -> OOV 候选
            for _oh in (_hit + list(d.get("topic_list") or [])):
                _oh = str(_oh).strip()
                if _oh and _oh not in _known:
                    oov.add(_oh)

            for it in r["issues"]:
                leaf = it["leaf"]
                cnt[leaf] += 1
                ch_of[leaf][d.get("channel_std")] += 1
                # ★ 用【原始】互动量（未 log 压缩）—— 压缩会把「小红书 9388 赞/帖」
                #   和「微博 9 赞/帖」抹成同一量级，失去区分度。
                _raw_eng = sum(float(x or 0) for x in (vals or {}).values())
                eng_ch[leaf][d.get("channel_std")] += _raw_eng
                g = eng_of[leaf]
                g["posts"] += 1
                # ★ 平台校正：同样真实热度，微博互动量数字显著偏高（可买粉买热搜），
                #   小红书/监管通报最实。先校正再聚合，避免「微博数字大就热」。
                _raw_tot = sum(_num(d.get({"like": "like_cnt", "comment": "comment_cnt",
                                           "collect": "collect_cnt",
                                           "repost": "repost_cnt"}[k]))
                               for k in ("like", "comment", "collect", "repost"))
                # ★ 互动总量 < 200 的帖子：所有平台都不作惩罚，认定其互动量为真实
                #   （小体量内容没有刷量动机，平台的"数据虚高"问题不适用）
                if _raw_tot < float(((self._heat_config().get("params") or {})
                                     .get("small_post_threshold", 200))):
                    _w = 1.0
                else:
                    _w = float(((self._heat_config().get("platform") or {})
                                .get(d.get("channel_std")) or {}).get("credibility", 1.0))
                _ea = e_val * _w
                g["engagement"] += _ea
                g["engagement_max"] = max(g["engagement_max"], _ea)
                for k in ("like", "comment", "collect", "repost"):
                    g[k] += _num(d.get({"like": "like_cnt", "comment": "comment_cnt",
                                        "collect": "collect_cnt",
                                        "repost": "repost_cnt"}[k]))
                if blank:
                    g["imputed"] += 1
            oov |= set(r["oov_issues"])
        self._eng_meta = {"impute_missing": impute,
                          "posts_with_metrics": len(has_data),
                          "posts_without_metrics": len(kept) - len(has_data),
                          "imputed_value": round(fill, 4)}
        self._eng_ch = {k: dict(v) for k, v in eng_ch.items()}   # ★ 逐渠道互动量
        return cnt, oov, ch_of, eng_of

    # ---------------- M6 热度：互动量的多少
    def _heat_config(self):
        """config/heat.json —— 平台可信度 + 饱和参数（缺失则退回旧口径）"""
        if getattr(self, "_hc", None) is None:
            try:
                self._hc = load_config("heat")
            except Exception:
                self._hc = {}
        return self._hc

    def _hist_index(self):
        """★ 上游读历史：32 场历史事件 KG 的跨事件索引（config/cross_event_index.json）"""
        if getattr(self, "_hidx", None) is None:
            try:
                self._hidx = load_config("cross_event_index")
            except Exception:
                self._hidx = {}
        return self._hidx

    def _mount_oov(self, leaf_cnt, eng_of, oov):
        """★ 事件无关（event-agnostic）：把词表外议题挂成图节点。

        必须在 heat_of() 【之前】调用 —— 否则 heat_map 里没有这些键，
        heat / heat_hist 会静默取默认值 0（踩过的 bug）。
        """
        if not oov:
            return {}
        _known = sorted(getattr(self.ex, "leaves", set()) or [])
        nodes = {}
        for _term in sorted(oov):
            _best, _bs = None, 0.0
            for _k in _known:
                _s = _char_sim(_term, _k)
                if _s > _bs:
                    _best, _bs = _k, _s
            nodes[_term] = {"parent": _best if _bs >= OOV_MOUNT_MIN_SIM else None,
                            "sim": round(_bs, 4)}
        self._oov_nodes = nodes
        self._oov_parent = {k: v["parent"] for k, v in nodes.items() if v["parent"]}
        self._oov_parent_sim = {k: v["sim"] for k, v in nodes.items() if v["parent"]}
        for _term in sorted(nodes):
            if _term not in leaf_cnt:
                leaf_cnt[_term] = 1
            if _term not in eng_of:
                eng_of[_term] = {"posts": 1, "engagement": 0.0, "engagement_max": 0.0,
                                 "like": 0.0, "comment": 0.0, "collect": 0.0,
                                 "repost": 0.0, "imputed": 1}
        return nodes

    def hist_factor(self, subjects, leaf, cap=5.0):
        """历史因子 = min(1, 该议题在该主体历史事件里的出现次数(不含本场) / cap)

        ★ 用途：一个反复出问题的议题是主体的『慢性病』—— 酝酿更久、涉及人更多，
          应该配更多候选槽位。只按本场 heat 选人，等于把公司一整年的历史扔了。
        """
        idx = self._hist_index()
        m = idx.get("entity_issue_matrix") or {}
        n = 0
        for s in (subjects or []):
            n = max(n, int((m.get(s) or {}).get(leaf, 0)))
        # ★★ 事件无关：硬查表查不到时走图上软传播（1跳共现邻居 + OOV软挂载 parent）
        if n <= 0 and leaf:
            ico = idx.get("issue_cooccurrence") or {}
            nb = {}
            for k, w in ico.items():
                a, _, b = k.partition(" + ")
                if a == leaf: nb[b] = max(nb.get(b, 0), w)
                elif b == leaf: nb[a] = max(nb.get(a, 0), w)
            for s in (subjects or []):
                row = m.get(s) or {}
                for j, w in nb.items():
                    n = max(n, int(row.get(j, 0) * min(1.0, w / 3.0)))
            _par = (getattr(self, "_oov_parent", {}) or {}).get(leaf)
            _sim = (getattr(self, "_oov_parent_sim", {}) or {}).get(leaf, 0.0)
            if _par:
                for s in (subjects or []):
                    row = m.get(s) or {}
                    n = max(n, int(row.get(_par, 0) * _sim))
        n = max(0, n - 1)                      # 减去本场
        return min(1.0, n / cap)

    def heat_of(self, leaf_cnt, eng_of):
        """heat(i) = base + (1 - base) x minmax(该议题的互动量)。

        互动量 E(i) = 该议题在 t0 窗口内每一条独立发声帖的互动量之和
                      （同正文已按 M3 去重，避免同一段话被多号转发把热度灌水）。

        退化保护：若所有议题的互动量完全相同（含「全为 0，四个互动字段都没采到」），
        直接输出常数会让 R2.7 的噪声控制失效，此时退回「独立发声数」口径并在
        kg["heat_model"]["mode"] 里标明是 fallback，不静默。
        """
        hcfg = self.cfg["thresholds"].get("heat") or {}
        base = float(hcfg.get("base", 0.30))
        # aggregate：sum = 该议题累计吸引到的互动量（默认，兼顾广度与深度）
        #            max = 该议题最爆的那一条的互动量（只看峰值）
        agg = (hcfg.get("aggregate") or "sum").lower()
        key = "engagement_max" if agg == "max" else "engagement"
        e = {l: float((eng_of.get(l) or {}).get(key, 0.0)) for l in leaf_cnt}
        if not e:
            return {}, "empty"
        # ★ M6 v2.0：饱和映射（绝对尺度），不再 minmax
        #   旧口径 minmax 会让【最热的议题永远是 1.000】，把相对值伪装成绝对值。
        #   新口径 heat = floor + (1-floor)*(1-exp(-E/S))，E=0 给 floor，趋近 1 但永不到 1。
        if (hcfg.get("mode") or "saturate") == "saturate":
            hc = self._heat_config()
            floor = float((hc.get("params") or {}).get("floor", 0.05))
            S = float((hc.get("params") or {}).get("S", 120.0)) or 120.0
            return ({l: round(floor + (1 - floor) * (1 - math.exp(-e[l] / S)), 3)
                     for l in leaf_cnt}, "saturate")
        mx, mn = max(e.values()), min(e.values())
        if mx - mn < 1e-9:
            mxv = max(leaf_cnt.values()) or 1
            return ({l: round(base + (1 - base) * (c / mxv), 3) for l, c in leaf_cnt.items()},
                    "voices_fallback")
        return ({l: round(base + (1 - base) * ((e[l] - mn) / (mx - mn)), 3) for l in leaf_cnt},
                "engagement")

    # ---------------- t0 状态快照
    def state_at(self, kept):
        """★ 交给下游节点的「初始条件」：cutoff_time 时刻场上已经发生的一切。

        kept 已经是**切分后**的可见集合（build 里传进来的），所以这里不再二次过滤——
        「建图用的帖子」与「第 0 轮注入的帖子」是同一个集合，不可能对不上。
        每个字段都只由 publish_time <= cutoff_time 的帖子算出，**没有未来泄漏**。
        """
        sel = list(kept)
        pol = getattr(self, "_t0_policy", {}) or {}
        it = self.extract_issues(sel)
        leaf_cnt = it[0] if it else Counter()
        eng_of = it[3] if it and len(it) > 3 else defaultdict(dict)
        # ★ 事件无关：把词表外议题挂成图节点 —— 必须在 heat_of 之前，
        #   否则 heat_map 里没有这些键，heat/heat_hist 会静默取 0
        self._mount_oov(leaf_cnt, eng_of, (it[1] if it and len(it) > 1 else set()))
        # ★ 与图谱用**同一个** M6 公式（互动量），否则第 0 轮注入的热度会和图谱对不上
        heat_map, heat_mode = self.heat_of(leaf_cnt, eng_of)
        times = sorted(d.get("publish_time") for d in sel if d.get("publish_time"))
        latest = times[-1] if times else None
        cutoff = pol.get("cutoff")
        leaked = [d.get("doc_id") for d in sel
                  if cutoff is not None and (parse_time(d.get("publish_time")) or cutoff) > cutoff]
        return {
            "t0": pol.get("t0"),
            "t0_mode": pol.get("mode"),
            "t0_semantics": pol.get("semantics"),
            "cutoff_time": pol.get("cutoff_time"),
            "floor_time": pol.get("floor_time"),
            "window_hours": pol.get("window_hours"),
            "visible_until_latest_post": latest,
            "doc_count": len(sel),
            # ★ 可审计：这一串 doc_id 就是「第 0 轮注入」的全部输入，多一条都算泄漏
            "visible_doc_ids": [d.get("doc_id") for d in sel],
            "leakage_check": {"passed": not leaked, "offending_doc_ids": leaked,
                              "rule": "所有进推演的帖子必须 publish_time <= cutoff_time"},
            "issues": [{"leaf": l, "heat": heat_map.get(l),
                    # ★ 历史加权热度：本场 heat × (1 + 0.5 × 历史因子)
                    "heat_hist": round(heat_map.get(l, 0.0) * (1 + 0.5 * self.hist_factor(
                        getattr(self, "_subjects", []), l)), 4),
                    "hist_factor": round(self.hist_factor(
                        getattr(self, "_subjects", []), l), 4),
                    "mentions": c,
                        "engagement": round((eng_of.get(l) or {}).get("engagement", 0.0), 4)}
                       for l, c in sorted(leaf_cnt.items(),
                                          key=lambda kv: (-heat_map.get(kv[0], 0), kv[0]))],
            "heat_model": {"mode": heat_mode,
                           "formula": "heat = 0.30 + 0.70 x minmax(该议题在 t0 窗口的互动量之和)",
                           "engagement_of": "like/comment/collect/repost 各自 log1p 压缩后加权求和",
                           "note": "与 event_kg 的 heat_model 完全一致；这是第 0 轮的初始条件"},
            "channel_dist": dict(Counter(d.get("channel_std") for d in sel)),
            "poster_role_dist": dict(Counter(d.get("poster_role") for d in sel)),
            # ★ 第 0 轮房间里「已经在说话的人」——下游注入时要按这个名单建初始 actor
            "speakers": sorted({d.get("author_id") for d in sel if d.get("author_id")}),
            "speakers_detail": [{"author_id": d.get("author_id"),
                                 "author_name": d.get("author_name"),
                                 "poster_role": d.get("poster_role"),
                                 "channel": d.get("channel_std"),
                                 "doc_id": d.get("doc_id"),
                                 "like_cnt": d.get("like_cnt"),
                                 "comment_cnt": d.get("comment_cnt"),
                                 "sentiment": d.get("sentiment"),
                                 # ★ 发行姿态：当事人型(客户) / 围观型(网民) / 机构型
                                 "poster_stance": (d.get("_stance") or {}).get("stance"),
                                 "role_effective": (d.get("_stance") or {}).get("role_effective"),
                                 "stance_confidence": (d.get("_stance") or {}).get("confidence"),
                                 "stance_evidence": [e["match"] for e in
                                                      ((d.get("_stance") or {}).get("evidence") or [])][:5],
                                 "stance_reason": (d.get("_stance") or {}).get("reason"),
                                 # ★ 两条不同的轴，别混：
                                 #   poster_stance = 谁在说话（当事人型/围观型/机构型）
                                 #   stance_value  = 他站哪边（连续量，三值只是它的分档）
                                 "stance_value": (d.get("_stance_value") or {}).get("value"),
                                 "stance_bin": (d.get("_stance_value") or {}).get("bin"),
                                 "stance_value_confidence": (d.get("_stance_value") or {}).get("confidence"),
                                 "stance_value_reason": (d.get("_stance_value") or {}).get("reason")}
                                for d in sorted(sel, key=lambda x: x.get("publish_time") or "")],
            # ★ t0 房间里的人怎么分布：下游推理演化层按这个定初始立场/效价/动作阈值档位
            "t0_role_mix": self._role_mix(sel),
            # ★ t0 立场值分布：三档占比 + 均值 + 极化度（下游直接拿去当演化起点与报告指标）
            "t0_stance_mix": self._stance_mix(sel),
            # ★ 组长 §环节1：语义关系边要能「定位 t0 核心发起者」
            #   起火点判定：采集侧显式标注优先，否则取「最早 + 互动量最高」
            "ignition": self._ignition_with_stance(sel),
            "seed_posts": [{"doc_id": d.get("doc_id"), "channel": d.get("channel_std"),
                            "poster_role": d.get("poster_role"), "time": d.get("publish_time"),
                            "hours_from_t0": d.get("_hours_from_t0"),
                            "like_cnt": d.get("like_cnt"), "comment_cnt": d.get("comment_cnt"),
                            "sentiment": d.get("sentiment"),
                            "entities": d.get("entities"),
                            "content": (d.get("raw_content") or "")[:200],
                            "source_url": d.get("source_url")} for d in sel],
            "note": "仿真第 0 轮的初始输入：这份状态 = cutoff_time 为止场上已经发生的事，"
                    "不含任何 t0 之后的未来信息",
        }

    # ---------------- 供下游：事件类型 / 具名实体 / t0 初始动作
    @staticmethod
    def event_type_of(issues):
        """事件类型 = 热度最高议题所属的 taxonomy 大类。

        组长 3.0：**事件集里的「事件类型」必须传给仿真层** —— 画像库侧的
        emotion_sensitivity / stance_bias 是按事件类型分档给的，没类型就查不了表。
        这里用图谱自己算：取 heat 最高的议题，它的大类就是本场事件类型；
        同时给出全部大类的热度权重，供仿真层做混合取值。
        """
        if not issues:
            return None, {}
        # ★ 先按 taxonomy 大类**聚合热度**再取最大 —— 不能只看 heat 最高的那一个议题。
        #   事件1 的例子：宠物险争议 heat 1.000（大类「产品线专项」）排第一，
        #   但这场戏真正的性质是「营销内容与价值观争议」
        #   （广告内容低俗 0.794 + 性别与价值观争议 0.389 = 1.183 > 1.000）。
        #   只取单个议题会把事件类型判错，画像库侧那边就查错表了。
        dist = defaultdict(float)
        for i in issues:
            dist[i.get("top")] += i.get("heat", 0)
        if not dist:
            return None, {}
        tot = sum(dist.values()) or 1.0
        ranked = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0], {k: round(v / tot, 4) for k, v in ranked}

    def _role_mix(self, sel):
        """t0 房间里三类人的分布：当事人型 / 围观型 / 机构型，以及判定后的 6 类角色。"""
        st = Counter((d.get("_stance") or {}).get("stance") or "unknown" for d in sel)
        rl = Counter((d.get("_stance") or {}).get("role_effective") or d.get("poster_role")
                     for d in sel)
        return {
            "by_stance": dict(st), "by_role_effective": dict(rl),
            "self_interest_ratio": round(st.get("self_interest", 0) / max(1, len(sel)), 4),
            "note": ("当事人型 = 帖子里在讲自己受损/自己维权（角色取客户）；"
                     "围观型 = 只在评论事件、宣泄情绪（角色取网民）；机构型 = 媒体/监管/KOL/专业背书方。"
                     "下游按它决定初值：当事人型起始效价更低、动作阈值走客户那一档。"),
        }

    def _stance_mix(self, sel):
        """t0 房间里的立场值分布：三档占比 + 均值 + 极化度。

        ★ 均值不动但极化度上升，是最危险的信号 —— 只报三档占比会把它藏起来。
        """
        vs = [(d.get("_stance_value") or {}) for d in sel]
        vals = [v["value"] for v in vs if v.get("value") is not None]
        if not vals:
            return {"by_bin": {}, "note": "t0 窗口没有可用帖文，无法标定立场"}
        m = sum(vals) / len(vals)
        sd = ((sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5) if len(vals) > 1 else 0.0
        return {
            "by_bin": dict(Counter(v.get("bin") for v in vs if v.get("bin"))),
            "mean": round(m, 4), "polarization": round(sd, 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "confidence_dist": dict(Counter(v.get("confidence") for v in vs if v.get("confidence"))),
            "note": ("立场 = 对事件主体的公开行为承诺强度 ∈ [-1,+1]，三档只是它的分档。"
                     "均值看整体漂移，极化度(std)看分化。"),
        }

    def _ignition_with_stance(self, sel):
        ign = _pick_ignition(sel)
        if not ign:
            return ign
        d = next((x for x in sel if x.get("doc_id") == ign.get("doc_id")), None)
        st = (d or {}).get("_stance") or {}
        ign.update({
            # ★ 组长要求：首发发帖者的类型（当事人型 / 围观型 / 机构型）
            "poster_stance": st.get("stance"),
            "poster_role_declared": st.get("role_declared") or (d or {}).get("poster_role"),
            "poster_role_effective": st.get("role_effective") or (d or {}).get("poster_role"),
            "stance_confidence": st.get("confidence"),
            "stance_evidence": [e["match"] for e in (st.get("evidence") or [])][:8],
            "stance_reason": st.get("reason"),
            "role_overridden": bool(st.get("role_overridden")),
            # ★ 起火点的 t0 立场值：整场演化的锚点，标错了后面每一轮都在错的地基上演
            "stance_value": ((d or {}).get("_stance_value") or {}).get("value"),
            "stance_bin": ((d or {}).get("_stance_value") or {}).get("bin"),
            "stance_value_confidence": ((d or {}).get("_stance_value") or {}).get("confidence"),
            "stance_value_reason": ((d or {}).get("_stance_value") or {}).get("reason"),
        })
        return ign

    @staticmethod
    def _merge_topic_lists(lists):
        """把同一发帖动作合并的多帖 topic_list 合起来：按出现顺序去重。
        另外保留 per_post 明细，便于溯源每一帖各自提了什么。"""
        seen, out = set(), []
        for lst in lists:
            for t in (lst or []):
                if t not in seen:
                    seen.add(t); out.append(t)
        return out

    def initial_posts_at(self, kept):
        """t0 的「真实动作」，给仿真层做第 0 轮还原（组长 3.1 ②：T0 是还原，不是生成）。

        代码依据：MiroFish 的 T0 注入走 initial_posts，**只读 poster_agent_id + content**。
        这里的合并规则按组长 3.1 的 PS：
          · 一个用户在**同一个渠道**发多帖  -> 合并成**一个发帖动作**
          · 一个用户在**多个渠道**都有账号  -> 视作**多个用户**（作者跨平台认不出同一个人）
        所以主键是 (author_id, channel)，而不是 author_id。
        """
        buckets = {}
        for d in kept:
            key = (d.get("author_id") or d.get("author_name") or d.get("doc_id"),
                   d.get("channel_std"))
            b = buckets.setdefault(key, {"post_ids": [], "contents": [], "times": [],
                                         "source_urls": [], "eng": 0.0,
                                         "topic_lists": [], "topic_per_post": {},
                                         "svalues": [], "sconfs": []})
            b["post_ids"].append(d.get("doc_id"))
            b["contents"].append((d.get("raw_content") or "").strip())
            if d.get("publish_time"):
                b["times"].append(d.get("publish_time"))
            if d.get("source_url"):
                b["source_urls"].append(d.get("source_url"))
            b["eng"] += _num(d.get("like_cnt")) + _num(d.get("comment_cnt"))
            _tl = [str(x) for x in (d.get("topic_list") or [])]
            b["topic_lists"].append(_tl)
            if _tl:
                b["topic_per_post"][d.get("doc_id")] = _tl
            b["svalues"].append((d.get("_stance_value") or {}).get("value"))
            b["sconfs"].append((d.get("_stance_value") or {}).get("confidence"))
            b["_doc"] = d
        out = []
        for (aid, ch), b in sorted(buckets.items(), key=lambda kv: (min(kv[1]["times"] or ["~"]))):
            d = b["_doc"]
            texts = [t for t in b["contents"] if t]
            out.append({
                "post_ids": b["post_ids"],
                "poster_author_id": aid,
                "poster_name": d.get("author_name"),
                "poster_role": d.get("poster_role"),
                "channel": ch,
                "publish_time": min(b["times"]) if b["times"] else None,
                "merged_posts": len(b["post_ids"]),
                "content": "\n\n".join(texts)[:2000],
                "source_urls": b["source_urls"],
                "engagement": round(b["eng"], 1),
                # ★ t0 快照自带 topic_list（合并后的并集 + 逐帖明细），下游不必再按 post_id 关联
                "topic_list": self._merge_topic_lists(b["topic_lists"]),
                "topic_list_per_post": b["topic_per_post"],
                "sentiment": d.get("sentiment"),
                # ★ 首发/发帖者类型：当事人型(客户) / 围观型(网民) / 机构型
                "poster_stance": (d.get("_stance") or {}).get("stance"),
                "poster_role_declared": (d.get("_stance") or {}).get("role_declared")
                                        or d.get("poster_role"),
                "poster_role_effective": (d.get("_stance") or {}).get("role_effective")
                                         or d.get("poster_role"),
                "stance_confidence": (d.get("_stance") or {}).get("confidence"),
                "stance_evidence": [e["match"] for e in
                                    ((d.get("_stance") or {}).get("evidence") or [])][:6],
                "stance_reason": (d.get("_stance") or {}).get("reason"),
                # ---- ★ t0 立场值（连续量）：推理演化层的演化起点 ----------------
                #   合并多帖时取【均值】（与 topic_list 的处理方式一致），
                #   逐帖明细留在 stance_per_post 里便于溯源。
                #   注意命名：上面的 stance_confidence 是「发帖者类型」的置信度，
                #   这里的 stance_value_confidence 是「立场值」的置信度，两者不是一回事。
                "stance_value": aggregate_stance(b["svalues"], self.stance_value_cfg),
                "stance_bin": stance_to_bin(aggregate_stance(b["svalues"], self.stance_value_cfg),
                                            self.stance_value_cfg),
                "stance_value_confidence": _worst_conf(b["sconfs"]),
                "stance_per_post": {pid: (self._svalue_of.get(pid) or {}).get("value")
                                    for pid in b["post_ids"]},
                "stance_axis": "对事件主体的公开行为承诺强度（-1=站质疑方 / +1=站辩护方）",
                "stance_provenance": "extracted",
                # ★ 由下游导出环节用名单回填：扮演这个发帖人的 agent_id
                "poster_agent_id": None,
            })
        return out

    def named_entities_of(self, ent_map):
        """下游要的「具名实体」清单：少而确定，1:1 直绑画像，不进抽签（组长 3.1）。"""
        rows = []
        for v in ent_map.values():
            if not v.get("role"):
                continue
            rows.append({"entity_id": v["id"], "name": v["name"], "type": v["type"],
                         "kind": v.get("kind"), "role": v["role"], "channel": v.get("channel"),
                         "bindable": bool(v.get("bindable")), "anonymous": bool(v.get("anonymous")),
                         "is_author": bool(v.get("is_author")), "mentions": len(v.get("post_ids") or []),
                         # ★ 首发/发帖者类型（当事人型=客户 / 围观型=网民 / 机构型）
                         "poster_stance": v.get("poster_stance"),
                         "poster_role_declared": v.get("poster_role_declared"),
                         "role_overridden": bool(v.get("role_overridden")),
                         "stance_confidence": v.get("stance_confidence"),
                         "stance_evidence": v.get("stance_evidence") or [],
                         # ★ t0 立场值（连续量 + 三值分档）：具名实体是 1:1 直绑的，
                         #   它的立场会直接成为扮演者 agent 的 t0 立场
                         "stance_value": v.get("stance_value"),
                         "stance_bin": v.get("stance_bin"),
                         "channels": v.get("channels") or {},
                         "post_ids": v.get("post_ids") or [],
                         "source_urls": (v.get("source_urls") or [])[:3]})
        rows.sort(key=lambda r: (-r["mentions"], r["name"]))
        return rows

    # ---------------- 主流程
    def build(self, ev, docs):
        ok, hard, quality, qflags, members, span = self.gate(ev, docs)
        if not ok:
            return None, {"event_id": ev["event_id"], "status": "rejected", "reasons": hard}

        kept = self.dedupe(members)
        # ★★ 2026-10-07 新增：t0 有效发声量 t0_effective —— 用于【自动决定仿真规模】。
        #   E = Σ over t0 每条帖 ( 1 + ln(1 + 该帖原始总互动) )
        #   · 每条帖打底 1 分 -> 代表一个独立发声者（帖数即广度）
        #   · 互动量做对数加成   -> 代表传播强度；用 log 是为了避免【单条爆款把规模拉爆】
        #     （实测 event1 只有 3 条 t0 帖，但有一条 2.9 万赞；线性相加会直接饱和到上限）
        #   · 只算 t0 窗口、每条帖只算一次（不按议题重复计数）
        _hcfg0 = self.cfg["thresholds"].get("heat") or {}
        _t0_eff = 0.0
        _t0_eng = 0.0
        for _d in kept:
            _v0, _vals0 = post_engagement(_d, _hcfg0)
            _raw0 = sum(float(x or 0) for x in (_vals0 or {}).values())
            _t0_eff += 1.0 + math.log1p(_raw0)
            _t0_eng += _raw0
        self._t0_eff = _t0_eff
        self._t0_eng = _t0_eng
        self._t0_docs = len(kept)
        # ★ 逐帖判定发帖姿态（当事人型/围观型/机构型），结果挂在帖子上，后面
        #   ① 用于发声主体实体的 role（决定具名实体绑到哪个角色的 agent）
        #   ② 原样进 t0 快照，交给下游推理演化层定初值
        self._stance_of = {}
        self._svalue_of = {}
        for d in kept:
            sres = classify_stance(d, self.stance_cfg or {}, d.get("poster_role"))
            d["_stance"] = sres
            self._stance_of[d.get("doc_id")] = sres
            # ★ t0 立场值：从正文标定「对事件主体的公开行为承诺强度」∈ [-1,+1]。
            #   sentiment 只做兜底方向，且询问/报道这类未表态形式不兜底（情绪 != 立场）。
            vres = calibrate_stance_value(d, self.stance_value_cfg, sres, self.stance_cfg)
            d["_stance_value"] = vres
            self._svalue_of[d.get("doc_id")] = vres
        tq = Counter(classify_text(d) for d in kept)
        text_ready = (tq["native"] + tq["paraphrase"]) >= 0.8 * max(1, len(kept))
        leaf_cnt, oov, ch_of, eng_of = self.extract_issues(kept)
        # ★ 事件无关：词表外议题挂成图节点 —— 必须在 heat_of 之前（见 _mount_oov 注释）
        self._mount_oov(leaf_cnt, eng_of, oov)
        # ★★ 事件无关：抽不出词表内议题时不再判失败 —— 有采集侧主题词就用它们建图
        self._open_mode = False
        if not leaf_cnt:
            if oov:
                self._open_mode = True
                leaf_cnt = Counter({k: 1 for k in sorted(oov)})
                for k in leaf_cnt:
                    eng_of[k] = {"posts": 1, "engagement": 0.0, "engagement_max": 0.0,
                                 "like": 0.0, "comment": 0.0, "collect": 0.0,
                                 "repost": 0.0, "imputed": 1}
            else:
                return None, {"event_id": ev["event_id"], "status": "no_issue",
                              "reasons": ["抽不出任何词表内议题，且采集侧未提供主题词"],
                              "oov": sorted(oov), "doc_count": len(kept)}

        # ---- M4 实体抽取 + 别名归一
        # ★ v2.0：每个具名实体都要带组长 3.1 要求的三个标注 ——
        #     role（6 类之一，否则绑不到画像）、channel（6 个之一，否则落不到位）、
        #     bindable（有真实名称的才 1:1 直绑，匿名的走抽签）
        ent_map, relations, ent_types = {}, [], Counter()
        ENTITY_ROLE = {"网民主体": "网民", "当事人": "客户", "消费者": "客户",
                       "律所/专业背书方": "专业背书方", "媒体": "媒体",
                       "监管机构": "监管", "KOL": "KOL", "公司主体": None, "产品": None}
        ENTITY_KIND = {"网民主体": "person", "当事人": "person", "消费者": "person",
                       "律所/专业背书方": "org", "媒体": "org", "监管机构": "org",
                       "KOL": "person", "公司主体": "company", "产品": "product"}

        def _touch(nm, typ, d, is_author):
            """登记一个具名实体，并累积它的 role / channel / post_ids / bindable。"""
            row = ent_map.get(nm)
            if row is None:
                row = ent_map[nm] = {"id": "E%d" % (len(ent_map) + 1), "name": nm,
                                     "type": typ, "is_impostor": False, "aliases": [],
                                     "role": ENTITY_ROLE.get(typ), "kind": ENTITY_KIND.get(typ, "subject"),
                                     "channel": None, "_ch": Counter(), "post_ids": [],
                                     "post_channels": {}, "_svals": [],
                                     "source_urls": [], "is_author": bool(is_author)}
                ent_types[typ] += 1
            ch = d.get("channel_std")
            if ch:
                row["_ch"][ch] += 1
                row["channel"] = row["_ch"].most_common(1)[0][0]
            pid = d.get("doc_id")
            if pid and pid not in row["post_ids"]:
                row["post_ids"].append(pid)
            if pid:
                row.setdefault("post_channels", {})[pid] = ch
            su = d.get("source_url")
            if su and su not in row["source_urls"]:
                row["source_urls"].append(su)
            return row

        for d in kept:
            r = self.ex.extract(" ".join([(d.get("raw_content") or ""), (d.get("title") or "")]), d)
            for e in r["entities"]:
                nm = e["name"]
                row = _touch(nm, e["type"], d, is_author=False)
                if e.get("is_impostor"):
                    row["is_impostor"] = True
                if e.get("alias_of") and e.get("raw") and e["raw"] not in row["aliases"]:
                    row["aliases"].append(e["raw"])
            # 发声主体也进实体（有 author_id 才算真实主体）
            aid = d.get("author_id")
            typ = self.ex.ROLE_TO_ENTITY_TYPE.get(d.get("poster_role"))
            if aid and typ:
                # ★ 去重键仍是 author_id（稳定性），但展示名用昵称 —— 图谱里不该是一串 uid
                row = ent_map.get(aid)
                if row is None:
                    row = _touch(aid, typ, d, is_author=True)
                    row["name"] = d.get("author_name") or aid
                row["author_id"] = aid
                row["author_id_is_surrogate"] = bool(d.get("author_id_is_surrogate"))
                row["author_name"] = d.get("author_name") or aid
                row["poster_role"] = d.get("poster_role")
                # ★ 发声主体的角色 = **帖子内容判定后的角色**（当事人型 -> 客户 / 围观型 -> 网民），
                #   机构号保持账号身份。采集侧的 poster_role 另存为 declared，便于对账。
                st = d.get("_stance") or {}
                row["role"] = st.get("role_effective") or d.get("poster_role") or row.get("role")
                row["poster_role_declared"] = st.get("role_declared") or d.get("poster_role")
                row["poster_stance"] = st.get("stance")
                row["stance_confidence"] = st.get("confidence")
                row["stance_evidence"] = [e["match"] for e in (st.get("evidence") or [])][:6]
                row["stance_reason"] = st.get("reason")
                row["role_overridden"] = bool(st.get("role_overridden"))
                # ★ 这个发声主体在各帖上的立场值，收尾时取均值
                sv = d.get("_stance_value") or {}
                if sv.get("value") is not None:
                    row["_svals"].append(sv["value"])

        for row in ent_map.values():
            ch = row.pop("_ch", None)
            # ★ 立场值：该主体在各帖上的均值（没有帖文的主体 -> None）
            _sv = aggregate_stance(row.pop("_svals", []), self.stance_value_cfg)
            row["stance_value"] = _sv
            row["stance_bin"] = stance_to_bin(_sv, self.stance_value_cfg)
            # ★ 组长 3.1 PS：一个用户在多个渠道发帖 -> 视作多个用户。
            #   所以实体要把「在哪些渠道出现过」原样留下来，定槽位时按 (实体 x 渠道) 展开。
            row["channels"] = dict(ch) if ch else {}
            if ch and not row.get("channel"):
                row["channel"] = ch.most_common(1)[0][0]
            # 匿名主体（没有昵称、只有平台内部 ID）不 1:1 直绑，走抽签（组长 3.1）
            nm = str(row.get("name") or "")
            anon = bool(row.get("is_author")) and (not row.get("author_name")
                                                   or nm.startswith("AN_")
                                                   or re.match(r"^[A-Za-z0-9_\-]{12,}$", nm))
            row["anonymous"] = anon
            row["bindable"] = bool(row.get("role")) and not anon

        # ★ 主体名单 —— 历史因子要用（上游读 32 场历史 KG）
        self._subjects = [str(row.get("name")) for row in ent_map.values() if row.get("name")]

        # ---- M11 关系边：发声主体 -> 公司主体，必须带 source_url
        for d in kept:
            src = ent_map.get(d.get("author_id") or "", {}).get("id")
            cos = [v["id"] for v in ent_map.values() if v["type"] == "公司主体"]
            if src and cos and d.get("source_url"):
                relations.append({"from": src, "to": cos[0],
                                  "type": "报道" if d.get("poster_role") == "媒体" else "质疑",
                                  "fact": (d.get("raw_content") or "")[:120],
                                  "source_url": d["source_url"], "provenance": "extracted"})

        # ---- M6 热度 = 互动量（v1.5）
        #      heat 由「该议题在 t0 窗口内每条独立发声帖的互动量之和」minmax 归一得到
        heat_map, heat_mode = self.heat_of(leaf_cnt, eng_of)
        agg = ((self.cfg["thresholds"].get("heat") or {}).get("aggregate") or "sum").lower()
        chdist = Counter(d.get("channel_std") for d in kept)
        # ★★ 2026-10-13 用户口径变更【t0-only】：GraphRAG 只能根据 t0 的状态建图、选人。
        #   原先这里是「全量口径」——docs 取本事件【全部】文档（不受 t0 cutoff 限制），
        #   用 full_channel_dist / full_mentions / full_channel_engagement 去定槽位和选人。
        #   那是**信息泄漏**：选人用到了 cutoff 之后才发生的帖子，等于提前知道了未来。
        #   现在一律退回 t0 窗口（kept），full_* 字段保留键名以兼容下游，但取值 = t0 口径。
        #   cutoff 之后的帖只进 holdout/，仅用于回测对照，绝不参与建图/定槽位/选人。
        _all = list(kept)
        full_chdist = Counter(d.get("channel_std") for d in _all)
        full_leaf_cnt, _, full_ch_of, full_eng_of = self.extract_issues(_all)
        # ★ t0 口径的「议题 -> 逐渠道互动量」，供 derive_slots 的渠道证据用。
        #   ★ 修 bug：原先写出的键名与 derive_slots 读的对不上（它读 full_channel_engagement），
        #     导致下游 _ce 恒为空 -> _has_eng=False -> 渠道分配退化成纯先验。
        #   直接复用 extract_issues 已经算好的 full_eng_of（= t0 口径的逐渠道互动量）。
        self._eng_ch = {k: dict(v) for k, v in (full_eng_of or {}).items()}
        # ★ 事件无关：词表外议题已由 _mount_oov 挂成图节点（heat_of 之前）
        _oov_nodes = getattr(self, "_oov_nodes", {}) or {}

        issues = []
        for i, (leaf, c) in enumerate(sorted(leaf_cnt.items(),
                                             key=lambda kv: (-heat_map.get(kv[0], 0), kv[0])), 1):
            g = eng_of.get(leaf) or {}
            issues.append({"id": "I%d" % i, "leaf": leaf,
                           "top": next((t for t, v in self.tax["categories"].items()
                                       if any(l["leaf"] == leaf for l in v["leaves"])),
                                       "开放议题" if leaf in _oov_nodes else None),
                           "provenance": ("auto:oov" if leaf in _oov_nodes else "ontology"),
                           "oov_parent": (_oov_nodes.get(leaf) or {}).get("parent"),
                           "oov_sim": (_oov_nodes.get(leaf) or {}).get("sim"),
                           "heat": round(heat_map.get(leaf, 0.30) * (OPEN_HEAT_DISCOUNT if leaf in _oov_nodes else 1.0), 4),
                           # ★ 历史加权热度（上游读 32 场历史 KG）
                           #   heat_hist = heat × (1 + 0.5 × 历史因子)
                           #   历史因子 = min(1, 该议题在该主体历史事件里出现的次数(不含本场) / 5)
                           #   用途：反复出问题的议题是主体的慢性病，酝酿更久、涉及人更多，配更多候选
                           "heat_hist": round(heat_map.get(leaf, 0.0) * (OPEN_HEAT_DISCOUNT if leaf in _oov_nodes else 1.0) * (1 + 0.5 * self.hist_factor(
                               getattr(self, "_subjects", []), leaf)), 4),
                           "hist_factor": round(self.hist_factor(
                               getattr(self, "_subjects", []), leaf), 4),
                           "controversy": self.cfg["thresholds"]["controversy_default"],
                           "controversy_source": "mvp_default",
                           "mentions": c, "channel_dist": dict(ch_of[leaf] or chdist),
                            # ★ 全量事件口径（选人用这两个；上面两个是 t0 口径）
                            "full_mentions": full_leaf_cnt.get(leaf, c),
                            "full_channel_dist": dict(full_ch_of.get(leaf) or full_chdist),
                           # ★ 逐渠道互动量（全量口径）—— 渠道人数分配的证据，见 derive_slots.channel_split
                           "full_channel_engagement": dict((getattr(self, "_eng_ch", {}) or {}).get(leaf) or {}),
                           # ★ 热度必须可回溯到互动量：把原始四路数据一起存下来
                           "engagement": {"engagement": round(g.get("engagement", 0.0), 4),
                                          "engagement_max": round(g.get("engagement_max", 0.0), 4),
                                          "aggregate": agg, "posts": g.get("posts", c),
                                          "like": int(g.get("like", 0)),
                                          "comment": int(g.get("comment", 0)),
                                          "collect": int(g.get("collect", 0)),
                                          "repost": int(g.get("repost", 0)),
                                          "imputed_posts": g.get("imputed", 0),
                                          "pct_like": (round(100.0 * g.get("like", 0) / max(1.0, g.get("like", 0) + g.get("comment", 0) + g.get("collect", 0) + g.get("repost", 0)), 1))}})
        self._heat_mode = heat_mode

        # ---- t0 状态快照（不是"事件脉络"：t0 是一个时点，构不成脉络）
        pol = getattr(self, "_t0_policy", {}) or {}
        state_t0 = self.state_at(kept)
        # cutoff 之后的帖子：只报数量，不进图谱、不进推演
        _hold = getattr(self, "_holdout", []) or []
        holdout = {"doc_count": len(_hold),
                   "cutoff_time": pol.get("cutoff_time"),
                   "note": "cutoff_time 之后的帖子，不进推演，仅用于回测对照（已另存 holdout/）",
                   "doc_ids": [d.get("doc_id") for d in _hold],
                   "times": sorted(d.get("publish_time") for d in _hold
                                   if d.get("publish_time"))[:5]}

        # ---- M10 / M11 门禁
        bad = []
        if any(v["type"] in ("Agent",) for v in ent_map.values()):
            bad.append("出现 Agent 节点")
        relations = [r for r in relations if r.get("source_url")]

        self._post_issues = getattr(self, "_post_issues", {})
        event_type, event_type_dist = self.event_type_of(issues)
        initial_posts = self.initial_posts_at(kept)
        named_entities = self.named_entities_of(ent_map)
        kg = {"event_id": ev["event_id"], "event_key": ev.get("event_key"),
              "title": ev["event_abstract"], "t0": ev["first_seen_time"],
              # ★ 组长 3.0：事件类型必须传给仿真层（画像库侧按事件类型查表）
              "event_type": event_type, "event_type_dist": event_type_dist,
              # ★ 组长 3.1：具名实体 / 议题节点 / 语义关系边 三类输出
              "named_entities": named_entities,
              "entities": list(ent_map.values()), "issues": issues, "relations": relations,
              "channel_dist": dict(chdist), "oov_issues": sorted(oov),
              # ★ 逐帖 -> 议题 的反查表（具名槽位的 issue_leaf 从这里来）
              "post_issues": dict(self._post_issues),
              # ★ 三级归一化链的可审计凭据：每条帖的 topic_hit / 全文抽取 / topic_hit归一 各命中了什么
              "post_issues_src": dict(getattr(self, "_post_issues_src", {})),
              # ★ 渠道分布（t0 口径）—— 2026-10-13 起与 channel_dist 同源（用户口径 t0-only）
              "full_channel_dist": dict(full_chdist),
              "selection_scope": {"used_for_slots": "t0_only",
                                  "cutoff_time": (getattr(self, "_t0_policy", {}) or {}).get("cutoff_time"),
                                  "note": "★ 2026-10-13 用户口径：GraphRAG 只根据 t0 状态建图与选人。cutoff 之后的帖只进 holdout/，仅用于回测对照，绝不参与建图/定槽位/选人。"},
              "doc_count": len(kept), "time_span_days": span,
        "t0_effective": round(getattr(self, "_t0_eff", 0.0), 4),   # ★ t0 有效发声量（帖数+log互动，保留备用）
        "t0_engagement": round(getattr(self, "_t0_eng", 0.0), 2),  # ★ t0 原始总互动量（自动规模主输入）
        "t0_docs": int(getattr(self, "_t0_docs", 0)),             # ★ t0 帖数（自动规模次输入）
              "sentiment_dist": ev.get("sentiment_dist"),
              "poster_role_dist": ev.get("poster_role_dist"),
              "quality": quality, "quality_flags": qflags,
              # ★ M6 口径声明：下游拿到的 heat 是怎么算出来的，必须写在图里
              "heat_model": {"mode": heat_mode,
                             "aggregate": ((self.cfg["thresholds"].get("heat") or {}).get("aggregate") or "sum"),
                             "formula": "heat = floor + (1-floor) x (1 - exp(-E/S))，E = Σ 平台校正后的帖互动量（t0 窗口，按 M3 去重）",
                             "engagement_per_post": "like/comment/collect/repost 各自 %s 压缩后加权求和"
                                                    % ((self.cfg["thresholds"].get("heat") or {}).get("compress", "log1p"),),
                             "dedupe": "同正文按 M3 只算一次，避免转发灌水",
                             "coverage": getattr(self, "_eng_meta", {})},
              "text_quality": {"level": ("native" if tq["native"] == len(kept) else
                                         "description" if tq["description"] * 2 > len(kept) else
                                         "paraphrase"),
                               "dist": dict(tq), "ready": text_ready},
              # ★ 给下游推演节点：cutoff_time 为止的场上状态（第 0 轮初始条件）
              # ★ 立场模型声明：下游拿到的 stance 是怎么标出来的，必须写在图里
              "stance_model": {
                  "config": "config/stance.json",
                  "formula": "stance = 方向 x clip(基座 + Σ幅度修正, 0, 1)",
                  "axis": "对事件主体的公开行为承诺强度（-1=站质疑方 / +1=站辩护方）",
                  "bins": "反对 <= -0.35 / 支持 >= +0.35 / 其间为中立；滞回：退出用 ±0.25",
                  "not_sentiment": "sentiment 只做方向兜底，且询问/报道这类未表态形式不兜底（情绪 != 立场）",
                  "provenance": "extracted —— 有真实帖文才标；其余 roster 成员由下游按槽位采样，标 generated",
                  "fields": {"initial_posts[].stance_value": "连续量（多帖合并取均值）",
                             "initial_posts[].stance_bin": "三值分档",
                             "initial_posts[].stance_per_post": "逐帖明细，可溯源",
                             "ignition.stance_value": "起火点立场，整场演化的锚点",
                             "named_entities[].stance_value": "具名实体的立场（1:1 直绑给扮演者）",
                             "t0_snapshot.t0_stance_mix": "三档占比 + 均值 + 极化度"},
              },
              "t0_snapshot": state_t0,
              # ★ 第 0 轮的「真实动作」清单：仿真层直接拿 poster_agent_id + content 还原
              "initial_posts": initial_posts,
              # 口径与边界单独抬到顶层，便于下游与验收脚本核对，不必翻进 snapshot 里找
              "visible_until": pol.get("cutoff_time"),
              "t0_policy": {k: pol.get(k) for k in
                            ("mode", "anchor", "window_hours", "pre_window_hours",
                             "semantics", "t0", "cutoff_time", "floor_time")},
              "leakage_guard": state_t0["leakage_check"],
              "holdout": holdout,
              "gate": {"M2_hard": True, "M10_M11": not bad},
              "provenance": "extracted",
              "note": "raw_content 实为第三方转述，非平台原文（见 eventpack_check.txt §9.2）"}
        stat = {"event_id": ev["event_id"], "status": "built", "quality": quality,
                "text_quality": kg["text_quality"],
                "doc_count": len(kept), "span": span, "issues": len(issues),
                "oov": len(oov), "entities": len(ent_map), "relations": len(relations),
                "ent_types": dict(ent_types), "quality_flags": qflags}
        return kg, stat
