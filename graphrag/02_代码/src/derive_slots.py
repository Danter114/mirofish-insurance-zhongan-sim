#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
derive_slots.py —— ② 定槽位：事件知识图谱 -> 角色槽位表（L2）

规则（见 ../../建图与定槽位规则_MVP.md）：
  R2.0 槽位 = (角色, 议题, 正反方, 渠道, 层级) + 人数
  R2.1 四步收敛，每步最大余数法，保证 sum(headcount) == 配额
  R2.2 w = heat x affinity三档
  R2.3 合法性门禁：issue_leaf 在词表内、(角色 x 渠道) 在 23 个有效组合内
  R2.4 可行性门禁：候选池 >= 人数 x ratio（需画像库；未接入则跳过）
  R2.6 可复现与可解释：固定 tie-break + reason_chain + SHA1 摘要

★ 本模块只出「需求」，不出「人」。槽位表是它与画像库之间唯一的接口。
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from common import largest_remainder


def purity_of(heat, pc):
    """议题热度 -> 该槽位里「本身就关注该议题」的人应占的比例。
    热度高 -> 纯度高（精准人群）；热度低 -> 纯度低（掺噪声）。"""
    if not pc or not pc.get("enabled"):
        return 1.0
    lo, hi = pc.get("heat_floor", 0.30), pc.get("heat_ceil", 1.00)
    t = (float(heat) - lo) / max(1e-9, hi - lo)
    t = max(0.0, min(1.0, t))
    return round(pc["min"] + (pc["max"] - pc["min"]) * (t ** pc.get("gamma", 1.0)), 4)


def slot_channel(role, issue, rc):
    """槽位主渠道 = 该议题在该角色可达渠道里提及量最大者；全 0 则退回角色主阵地。
    ★ v1.3 起主路径改用 channel_split（多渠道路由），本函数保留作单渠道退路与回归对照。"""
    cand = {c: (issue.get("channel_dist") or {}).get(c, 0) for c in rc[role]}
    if sum(cand.values()) <= 0:
        return max(rc[role], key=lambda c: (rc[role][c], c))
    return max(cand, key=lambda c: (cand[c], rc[role][c]))


# ★★ 2026-10-07 渠道分配的三个修正（原先量纲错了，t0 证据被完全淹没）
#
#   旧实现： w = t0提及量 + baseline × rc[role][c]
#            t0提及量的量纲是【帖数 0~15】，rc 是【百万级用户数 4~130】
#            于是 0.3 × 130 = 39 的基线把 0~15 的真实证据彻底压死 ——
#            实测 某新事件（t0 全在小红书/抖音，微博 0 帖）却给出微博 39.6% 的槽位。
#
#   新实现： w = t0提及量
#              + baseline × (rc 归一化) × DIFFUSE[c] × len(rc) × g(n_t0)
#           ① 量纲对齐：rc 先归一化到 0~1，再 × 渠道数，回到「帖数」量级
#           ② 扩散先验 DIFFUSE：微博是舆论场中枢（其他平台的讨论最终汇聚于此），
#              权重最高；小红书往往是起源地，权重最低
#           ③ 自适应 g(n_t0)：t0 帖越少，证据越不足，越依赖历史扩散规律
#              t0 帖越多，证据越充分，越按 t0 的真实分布走
# ★★ 2026-10-07 全局渠道参与结构先验（事件无关）——
#   为什么要它：event3/event4 完全没有互动量数据，event2 互动几乎全 0，
#   只能退回「帖数」口径 -> 微博帖多 -> 微博吃掉 70~81% 的人。
#   但中国舆情的跨平台参与结构是有稳定规律的：微博参与人数最多（舆论场），
#   抖音用户基数最大，小红书是消费维权/品牌伦理类的主力。
#   本先验保证【任何事件】选出来的人：微博最多，但小红书/抖音都有合理规模。
#   ★ 必须【按角色】给 —— 因为 channel_split 只在 rc[role] 的合法渠道内分配，
#     若用全局先验，门户/黑猫/监管 的份额会被浪费掉，剩下的渠道里微博占比自动抬高
#     （实测 event4 网民可用渠道只有 3 个 -> 微博占 58.7%）。
CHANNEL_PRIOR = {
    "网民": {"微博": 0.42, "抖音": 0.30, "小红书": 0.28},
    "客户": {"小红书": 0.40, "微博": 0.24, "黑猫投诉": 0.21, "抖音": 0.15},
    "KOL":  {"微博": 0.42, "抖音": 0.31, "小红书": 0.27},
    "媒体": {"新闻门户": 0.42, "微博": 0.40, "抖音": 0.10, "小红书": 0.08},
    "监管": {"监管通报": 1.00},
}
#   证据充分度：帖越多、互动数据越全，越按事件真实分布走
PRIOR_LAMBDA_MAX = 0.35      # 事件证据最多占 60%
PRIOR_FLOOR = 0.70           # 每个渠道的占比保底 = 先验 × 该系数
PRIOR_DOC_FULL = 30.0        # 帖数达到这个量级算「证据充分」
PRIOR_LAMBDA_NO_ENG = 0.35   # 没有互动数据时，证据充分度打折
CHANNEL_DIFFUSE = {"微博": 1.0, "新闻门户": 1.1, "抖音": 1.0,
                   "小红书": 0.8, "黑猫投诉": 0.5, "监管通报": 0.3}
#   自适应强度：g = N0 / (N0 + n_t0)
#     n_t0 = 5    -> g = 0.86  （证据不足，扩散先验主导）
#     n_t0 = 29   -> g = 0.41  （证据充分，t0 分布主导）
DIFFUSE_N0 = 25.0


def channel_split(role, issue, rc, n, cfg, n_t0=None):
    """R2.8 渠道分配：把一个 (角色, 议题) 上的人数切到该角色的各个可达渠道。

        weight(c) = t0 提及量  +  baseline × 归一化阵地权重 × DIFFUSE[c] × 渠道数 × g(n_t0)
                    └ 真实证据 ┘   └────────── 预期承接（历史扩散规律）──────────┘

    为什么需要承接项：候选池是**整场事件**的，不是 t0 的活跃者名单。
    议题在 t0 往往只在 1~2 个渠道出现；若赢者通吃，t0 之后的跨渠道扩散就没有承接者。
    ★ 但要**有序**地留：微博承接力最强（DIFFUSE 2.0），小红书最弱（0.8），
      而不是像旧实现那样按用户数量大小无差别地灌基线。

    实现：最大余数法，保证该议题该角色各渠道人数求和 == n。
    """
    th = cfg.get("thresholds") or {}
    base = float(th.get("channel_baseline", 0.3))
    bias = th.get("channel_bias") or {}
    diffuse = dict(CHANNEL_DIFFUSE)
    diffuse.update(th.get("channel_diffusion") or {})      # 配置可覆盖
    n0 = float(th.get("diffuse_n0") or DIFFUSE_N0)
    # ② 自适应系数：t0 帖越少 -> g 越大 -> 越依赖历史扩散规律
    _n = float(n_t0 if n_t0 is not None else (sum((issue.get("channel_dist") or {}).values()) or 1))
    g = n0 / (n0 + _n)
    # ① 量纲对齐：rc 归一化后 × 渠道数，使承接项与「帖数」同量级
    _rcs = rc[role] or {}
    _tot = sum(float(v) for v in _rcs.values()) or 1.0
    _k = float(len(_rcs)) or 1.0
    # ★★ 2026-10-07 证据口径修正：从「帖数」改成「帖数 + 互动量」混合 ——
    #   event1 实测：微博 15 帖只有 132 互动（均 9，通稿转发没人理），
    #              小红书 3 帖却有 28165 互动（均 9388）。
    #   只按帖数 -> 微博吃走 51.7% 的渠道人数；按 log 互动量 -> 小红书 36.6% / 抖音 32.9% / 微博 17.5%。
    #   混合系数 channel_evidence_mix：帖数权重，其余给互动量。
    # ★★ 2026-10-13 t0-only：渠道证据一律取 t0 口径（full_* 现在是 build_kg 写出的 t0 副本）
    _cd = issue.get("channel_dist") or issue.get("full_channel_dist") or {}
    _ce = issue.get("full_channel_engagement") or {}
    _mix = float(th.get("channel_evidence_mix", 0.2))
    _tp = sum(float(v) for v in _cd.values()) or 1.0
    _le = {c: math.log1p(float(v or 0)) for c, v in _ce.items()}
    _te = sum(_le.values()) or 1.0
    _ev_raw = {}
    for c in set(list(_cd.keys()) + list(_le.keys())):
        _p = float(_cd.get(c, 0)) / _tp
        _e = _le.get(c, 0.0) / _te
        _ev_raw[c] = _mix * _p + (1.0 - _mix) * _e          # 事件证据（未归一）
    # ★★ 与全局先验混合 —— 事件证据占比由「证据充分度」决定
    _ndoc = sum(float(v) for v in _cd.values()) or 0.0
    _has_eng = any(float(v or 0) > 0 for v in _ce.values())
    _strength = min(1.0, _ndoc / PRIOR_DOC_FULL) * (1.0 if _has_eng else PRIOR_LAMBDA_NO_ENG)
    _lam = min(PRIOR_LAMBDA_MAX, _strength)
    _prior = dict(CHANNEL_PRIOR.get(role) or {})            # ★ 按角色取先验
    _prior.update((th.get("channel_prior") or {}).get(role) or {})   # 配置可覆盖
    _ev = {}
    for c in set(list(_ev_raw.keys()) + list(_prior.keys())):
        _mixv = (1.0 - _lam) * float(_prior.get(c, 0.0)) + _lam * float(_ev_raw.get(c, 0.0))
        _fl = float(_prior.get(c, 0.0)) * PRIOR_FLOOR       # ★ 保底，防某渠道被清零
        _ev[c] = max(_mixv, _fl) * _k                       # × 渠道数 -> 量纲回到「帖数」级别
    w = {}
    for c, rw in _rcs.items():
        _carry = base * (float(rw) / _tot) * _k * diffuse.get(c, 1.0) * g
        w[c] = (float(_ev.get(c, 0.0)) + _carry) * float(bias.get(c, 1.0))
    # ★ 向上取整：该角色的每个合法渠道至少分到 1 人（只要总人数够）
    #   否则小规模（50/100 人）下部分 (角色,渠道) 格子会是 0，15 格落不满
    chans = list(w.keys())
    if n >= len(chans):
        alloc = {c: 1 for c in chans}
        rest = n - len(chans)
        if rest > 0:
            add = largest_remainder(w, rest)
            for c in chans:
                alloc[c] += int(add.get(c, 0))
        return alloc
    return largest_remainder(w, n)


def merge_small_slots(slots, min_size):
    """同 (角色, 层级) 内的碎片槽位合并。铁律：绝不跨层级合并。"""
    # ★ 分组键必须带 正反方 / purity_group / issue_leaf 三者：
    #   不带 side        -> 小 counter 会被并进 pro，「正反双方都要有」名存实亡（实测 counter 18% -> 1.4%）
    #   不带 purity_group -> focused 会被并进 noise，热度对人群纯度的控制失效
    #   不带 issue_leaf   -> **议题归属被打乱**：一个标着「退保损失/noise」的槽位里
    #                        混进了「宠物险争议」的 noise，于是「该议题选出的人里
    #                        真关注该议题的占多少」这个指标再也算不准（实测偏 31 个百分点）。
    #                        noise 对「不是这个议题」成立，但对「是哪个议题」不成立，
    #                        所以跨议题合并只对噪声语义成立、对统计口径不成立 —— 一并禁掉。
    #   ★★ 2026-10-07 补：分组键必须带 channel ——
    #     不带 channel 时，合并会跨渠道串味：小红书的 1~2 人小槽位被并进「同组里最大的槽」，
    #     而最大的槽通常是微博（因为 channel_split 的权重 + carry），于是微博人数虚增。
    #     实测 event4：channel_split 给网民微博 45%，但合并后槽位变成微博 67%。
    groups = defaultdict(list)
    for s in slots:
        groups[(s["role_type"], s["tier"], s["side"], s.get("purity_group", "focused"),
                s.get("issue_leaf") or ("BOUND:" + str(s.get("bound_entity"))),
                s.get("channel"))].append(s)
    out = []
    for _, arr in groups.items():
        def small(s):
            return s["bound_entity"] is None and s["headcount"] < min_size
        fixed = [s for s in arr if not small(s)]
        loose = [s for s in arr if small(s)]
        if not loose:
            out.extend(fixed)
            continue
        if fixed:
            host = max(fixed, key=lambda s: s["headcount"])
            out.extend(fixed)
        else:
            host = max(loose, key=lambda s: s["headcount"])
            out.append(host)
        for d in loose:
            if d is host:
                continue
            host["headcount"] += d["headcount"]
            host.setdefault("reason", {}).setdefault("merged_in", []).append(
                {"from": d["slot_id"], "issue": d["issue"], "side": d["side"], "n": d["headcount"]})
    return sorted(out, key=lambda s: s["slot_id"])


# t0 窗口的帖数（自适应系数用）—— 由 derive() 设置，channel_split 读取
_N_T0 = [None]


def auto_total(kg, cfg):
    """★★ 2026-10-13 v5：可解释性重构 —— 每个参数都能讲出意义。

        N = base_size
          + per_poster_gain * max(0, posts - post_threshold)
          + engagement_gain * log10(1 + engagement / engagement_base)
        N = clip(round(N), min_size, max_size)

    ┌─────────────────────┬──────┬────────────────────────────────────────────┐
    │ 参数                │ 值   │ 意义                                       │
    ├─────────────────────┼──────┼────────────────────────────────────────────┤
    │ base_size           │   60 │ 基线规模 —— 再冷的事件也必须有的最小舆论场  │
    │ post_threshold      │    5 │ 事件成立门槛 —— 少于 5 帖算不上「事件」，   │
    │                     │      │ 这 5 帖是固有信息量，不产生额外参与者        │
    │ per_poster_gain     │   30 │ 人均带动数 —— 每多 1 个独立发帖者，         │
    │                     │      │ 带动 30 个围观者                            │
    │ engagement_base     │  100 │ 热度基准点 —— 100 互动 ≈ 一条小爆款，       │
    │                     │      │ 作为对数的零点                              │
    │ engagement_gain     │   80 │ 热度弹性 —— 互动量每翻 10 倍，多带 80 人     │
    │ min_size / max_size │60/600│ 人数上下限                                 │
    └─────────────────────┴──────┴────────────────────────────────────────────┘

    ★ 为什么帖数主导（而非互动量）：
      帖数   = 有多少独立的人在说 -> 决定【舆论场规模】
      互动量 = 声量大小           -> 决定【传播强度】，不决定有多少人参与
      只有 3 个人发帖但帖子火了的事件，舆论场其实只有 3 个人。

    ★ 为什么互动量用 log10 而不是幂律（旧版是 4*E^0.28）：
      幂律指数 0.28 讲不出意义；log10 可直接读成「每翻 10 倍多带 N 人」，
      且天然收敛，不需要额外设封顶。

    ★ 为什么前 post_threshold 帖不算：
      要让「5 帖的事件 = 60 人」与「17 帖的事件 ≈ 550 人」同时成立，
      纯线性做不到（12 帖跨 490 人，那 5 帖时就有 200+ 人）。
      把前 5 帖视为「事件成立的固有信息量」即可。

    ★ 标定（2026-10-13 实测，t0 = Day0+Day1）：
      拒赔 17帖/4333互动 = 552（全场最高）｜ 广告 12帖/35781互动 = 474
      骚扰 16帖/4互动    = 391 ｜ 齐鲁保 4帖/0互动 = 60（触底）

    ★ 角色占比【定死不变】（网民57% 客户23% KOL11% 媒体6% 监管3%）。
      实际名单人数 = N + 格子保底补齐数。
    """
    a = (cfg.get("thresholds") or {}).get("auto_scale") or {}
    if not a.get("enabled", True):
        return None
    # ── 参数（新名优先，兼容旧名，便于旧配置平滑迁移）──
    base_size = float(a.get("base_size", a.get("base_n", 60.0)))
    post_threshold = float(a.get("post_threshold", a.get("post_free", 5.0)))
    per_poster_gain = float(a.get("per_poster_gain", a.get("post_coef", 30.0)))
    engagement_base = float(a.get("engagement_base", 100.0))
    engagement_gain = float(a.get("engagement_gain", a.get("eng_coef", 80.0)))
    min_size = int(a.get("min_size", a.get("n_floor", 60)))
    max_size = int(a.get("max_size", a.get("n_max", 600)))
    P = float(kg.get("t0_docs") or kg.get("doc_count") or 0)
    E = float(kg.get("t0_engagement") or 0.0)
    # 发帖带动项：前 post_threshold 帖是事件成立的固有信息量，不产生额外参与者
    posts_term = per_poster_gain * max(0.0, P - post_threshold)
    # 热度加成项：互动量每翻 10 倍多带 engagement_gain 人
    eng_term = engagement_gain * math.log10(1.0 + max(0.0, E) / engagement_base)
    raw = base_size + posts_term + eng_term
    n = max(min_size, min(max_size, int(round(raw))))
    return {"n": n, "raw": round(raw, 2), "posts": int(P), "engagement": round(E, 2),
            "posts_term": round(posts_term, 2), "engagement_term": round(eng_term, 2),
            "params": {"base_size": base_size, "post_threshold": post_threshold,
                       "per_poster_gain": per_poster_gain, "engagement_base": engagement_base,
                       "engagement_gain": engagement_gain,
                       "min_size": min_size, "max_size": max_size}}


def derive(kg, cfg, taxonomy):
    quota, rc = cfg["quota"], cfg["role_channel"]
    roles, ts = cfg["roles"], cfg["tier_score"]
    aff, th = cfg["affinity_tier"], cfg["thresholds"]
    leaves = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
    leaf2top = {lf["leaf"]: top for top, v in taxonomy["categories"].items() for lf in v["leaves"]}
    bindable = {e["type"] for e in cfg.get("bind_types", [])} if cfg.get("bind_types") else None
    ENTITY_ROLE = {"当事人": "客户", "消费者": "客户", "律所/专业背书方": "专业背书方",
                   "媒体": "媒体", "监管机构": "监管", "KOL": "KOL",
                   "公司主体": None, "产品": None}

    # ★ Step 0 具名实体 1:1 直绑（组长 3.4 ①：把真实媒体号 / 律所号 / 监管号绑到具体 agent）
    #   图谱里的实体已经带 role / channel / bindable 三个标注（见 build_kg.py），
    #   直接照抄即可 —— 这就是「角色（6 类）+ 渠道（6 个）」的落位依据。
    matrix_violations = []
    bound = defaultdict(list)
    for e in kg.get("entities", []):
        r = e.get("role") or ENTITY_ROLE.get(e["type"])
        if r not in roles:
            continue
        # 两类都进 1:1 直绑：
        #   ① bindable 机构号（媒体/律所/监管）—— 组长 3.4 ① 要求 1:1 绑到具体 agent
        #   ② is_author 的 t0 发声主体 —— 组长 3.1 ② 要求「让扮演发帖人的 agent 把原帖发出来」，
        #      所以每一个 t0 发帖人都必须有一个 agent，包括匿名的网民账号
        if not (e.get("bindable") or e.get("is_author")):
            continue
        chans = e.get("channels") or ({e["channel"]: 1} if e.get("channel") else {})
        if not chans:
            chans = {None: 1}
        pch = e.get("post_channels") or {}
        for ch_real in chans:
            pids = [p for p in (e.get("post_ids") or [])
                    if ch_real is None or pch.get(p, ch_real) == ch_real]
            bound[r].append({"name": e["name"], "channel": ch_real,
                             "type": e["type"], "post_ids": pids or (e.get("post_ids") or []),
                             "kind": e.get("kind") or "subject",
                             "anonymous": bool(e.get("anonymous")),
                             "bindable": bool(e.get("bindable"))})

    # ★ 上游读历史：把 heat 替换成历史加权热度（heat_hist），原值留在 heat_raw
    #   这样本函数内所有下游用法（槽位权重 w / 纯度 / 具名 issue_leaf / 输出）自动生效
    _iss = []
    for _i in kg["issues"]:
        if _i.get("heat_hist") is None:
            _iss.append(_i)
        else:
            _j = dict(_i); _j["heat_raw"] = _i.get("heat"); _j["heat"] = _i["heat_hist"]
            _iss.append(_j)
    kg = dict(kg); kg["issues"] = _iss
    # ★ 相对阈值：heat >= max(heat) × min_heat_ratio
    #   绝对阈值会随热度模型尺度漂移（minmax->饱和曲线时，0.25 从"筛最冷"变成"只留最热1~2个"）
    _ratio = float(th.get("min_heat_ratio") or 0.0)
    _hmax = max([float(i.get("heat") or 0.0) for i in kg["issues"]] or [0.0])
    # 相对阈值【取代】绝对阈值（不是取两者较大值 —— 那样绝对下限会压过相对阈值，等于没改）
    _cut = (_hmax * _ratio) if _ratio > 0 else float(th["min_heat"])
    issues = [i for i in kg["issues"] if i["heat"] >= _cut and i["leaf"] in leaves]
    # ★ t0 窗口帖数：信道自适应系数的输入（t0 帖少 -> 更依赖历史扩散规律）
    _N_T0[0] = sum((kg.get("channel_dist") or {}).values()) or None
    drops = [i["leaf"] for i in kg["issues"] if i["leaf"] not in leaves or i["heat"] < th["min_heat"]]

    slots, sid = [], 0
    # ★ 纯度配比的取整补偿：按 (角色, 议题) 记一个「欠账」，
    #   下一次切分时补回来。否则每个小槽位各自取整会**系统性偏向噪声**
    #   （实测 退保损失 目标 50%，一刀刀切完只剩 34.5%）。
    pur_carry = defaultdict(float)
    for role in roles:
        budget = quota[role]
        quota0 = budget
        for b in bound.get(role, [])[: max(0, budget - 1)]:
            sid += 1
            # 渠道优先取该实体真实出现的渠道。
            # ★ 组长 2.6 #2：(角色, 渠道) 必须落在有效组合矩阵内，越界判废 ——
            #   所以矩阵外的真实渠道要落回该角色的合法格，并把越界事实**记下来**
            #   （matrix_violation），由 run.py 汇成复核清单交给组长/画像库侧。
            ch_real = b.get("channel")
            legal = ch_real in rc[role]
            ch = ch_real if legal else max(rc[role], key=lambda c: (rc[role][c], c))
            if not legal:
                matrix_violations.append({"entity": b["name"], "role_type": role,
                                          "channel_real": ch_real, "channel_used": ch,
                                          "post_ids": b.get("post_ids") or []})
            # ★ 原发帖人必须字段完整：issue_leaf 取「他发的帖涉及的议题里 heat 最高的那个」
            _pi = (kg.get("post_issues") or {})
            _hm = {i.get("leaf"): i.get("heat", 0) for i in (kg.get("issues") or [])}
            _cand = []
            for _pid in (b.get("post_ids") or []):
                _cand += _pi.get(_pid) or []
            _leaf = max(_cand, key=lambda l: (_hm.get(l, 0), l)) if _cand else None
            slots.append({"slot_id": "S%03d" % sid, "role_type": role, "issue": _leaf or "具名主体",
                          "issue_leaf": _leaf, "side": "neutral", "channel": ch, "tier": "具名",
                          "tier_target": 1.0, "headcount": 1, "bound_entity": b["name"],
                          "bound_role": role, "bound_channel": ch,
                          "bound_post_ids": b.get("post_ids") or [],
                          "bound_kind": b.get("kind") or "subject",
                          "bound_bindable": bool(b.get("bindable")),
                          "bound_anonymous": bool(b.get("anonymous")),
                          "bound_channel_raw": ch_real,
                          "matrix_violation": (not legal),
                          "bound_post_ids_used": b.get("post_ids") or [],
                           "purity_group": "focused", "purity": 1.0,   # 具名实体天然精准
                          "reason": {"step": "R2.1 Step0 具名实体 1:1 直绑（组长 3.4 ①）",
                                     "role_quota": quota0, "bound_entity": b["name"],
                                     "bound_channel": ch,
                                     "bound_post_ids": b.get("post_ids") or []}})
            budget -= 1
        if budget <= 0:
            continue
        tiers = cfg["tier_plan"].get(role) or cfg["default_tier"]
        tier_alloc = largest_remainder({t[0]: t[1] for t in tiers}, budget)
        for tname, tshare in tiers:
            c = tier_alloc[tname]
            if c <= 0:
                continue
            w = {it["leaf"]: it["heat"] * ts[aff[role][leaf2top[it["leaf"]]]] for it in issues}
            if not w:
                continue
            iss_alloc = largest_remainder(w, c)
            for leaf, n in iss_alloc.items():
                if n <= 0:
                    continue
                it = next(x for x in issues if x["leaf"] == leaf)
                # ★ R2.8 渠道分配：先按渠道切人数，再在渠道内切正反方与人群纯度。
                #   槽位五维里的「渠道」从此真正参与收敛，而不是整个角色塌在一个渠道上。
                ch_alloc = channel_split(role, it, rc, n, cfg, n_t0=_N_T0[0])
                # ★ 组长要求：按议题热度切「人群纯度」——
                #   热度高 -> focused（本身就关注该议题的人）占比大
                #   热度低 -> noise（不关注该议题、被卷进来的人）占比大
                pc = cfg["thresholds"].get("topic_purity") or {}
                pur = purity_of(it["heat"], pc)
                for ch in sorted(ch_alloc):
                    n_ch = ch_alloc[ch]
                    if n_ch <= 0:
                        continue
                    if (it["controversy"] >= th["controversy_threshold"]
                            and role in th["counter_roles"] and n_ch >= 2):
                        sp = largest_remainder({"pro": 1 - th["counter_share"],
                                                "counter": th["counter_share"]}, n_ch)
                        parts = [("pro", sp["pro"]), ("counter", sp["counter"])]
                    elif (it["controversy"] >= th["controversy_threshold"]
                          and role not in th["counter_roles"]):
                        parts = [("pro", n_ch)]
                    else:
                        parts = [("neutral", n_ch)]
                    for side, cnt in parts:
                        if cnt <= 0:
                            continue
                        if pc.get("enabled"):
                            k_pur = (role, leaf)
                            want_f = max(0.0, min(float(cnt), pur * cnt + pur_carry[k_pur]))
                            grp_alloc = largest_remainder({"focused": want_f,
                                                           "noise": float(cnt) - want_f}, cnt)
                            pur_carry[k_pur] = want_f - grp_alloc["focused"]
                        else:
                            grp_alloc = {"focused": cnt}
                        for grp, gcnt in grp_alloc.items():
                            if gcnt <= 0:
                                continue
                            sid += 1
                            slots.append({"slot_id": "S%03d%s" % (sid, "F" if grp == "focused" else "N"),
                                          "role_type": role, "issue": leaf,
                                          "issue_leaf": leaf, "side": side, "channel": ch,
                                          "tier": tname,
                                          "tier_target": 0.5, "headcount": gcnt,
                                          "bound_entity": None,
                                          "purity_group": grp, "purity": pur,
                                          "reason": {"role_quota": quota0, "after_bound": budget,
                                                     "tier": tname, "tier_alloc": c,
                                                     "issue_weight": round(w[leaf], 4),
                                                     "issue_alloc": n, "channel_alloc": ch_alloc,
                                                     "channel_n": n_ch,
                                                     "side_split": {side: cnt},
                                                     "purity_of_issue": pur,
                                                     "purity_split": grp_alloc,
                                                     "heat": it["heat"],
                                                     "controversy": it["controversy"]}})
    slots = merge_small_slots(slots, th["min_slot_size"])
    # ★★ 格子保底：该角色的每个合法渠道至少 1 个槽位
    #    小规模（50/100 人）下，议题切分 + 渠道切分会把某些格子切没。
    #    做法：找出空的 (角色,渠道)，从该角色人数最多的渠道挪一个槽位过去。
    _cell_fix = []
    for _role in roles:
        _want = list(rc[_role].keys())
        _have = {s["channel"] for s in slots if s["role_type"] == _role}
        _miss = [c for c in _want if c not in _have]
        for _c in _miss:
            # 找该角色里「同渠道人数最多」的槽位群，挪其中人数最多、且 headcount>1 的
            _cand = [s for s in slots if s["role_type"] == _role and s["headcount"] > 1]
            if not _cand:
                # 没有可拆的 -> 把该角色某个单人格子整格改渠道（优先改同 tier 的重复渠道）
                _cand = [s for s in slots if s["role_type"] == _role and s["channel"] != _c]
            if not _cand:
                break
            _src = max(_cand, key=lambda s: (s["headcount"], s["channel"]))
            if _src["headcount"] > 1:
                _src["headcount"] -= 1
                _new = dict(_src); _new["headcount"] = 1
            else:
                _new = dict(_src)
            # slot_id 可能是合并过的复合形式（如 S001+S002），用正则安全取最大序号
            import re as _re
            _mx = 0
            for _s in slots:
                for _n in _re.findall(r"S(\d+)", str(_s.get("slot_id") or "")):
                    _mx = max(_mx, int(_n))
            _new["slot_id"] = "S%03d" % (_mx + 1)
            _new["channel"] = _c
            _new["bound_channel"] = _c if _new.get("bound_entity") else None
            _new["reason"] = dict(_new.get("reason") or {})
            _new["reason"]["cell_floor"] = "★ 格子保底：%s x %s 原本为 0，从 %s 挪 1 人" % (_role, _c, _src["channel"])
            slots.append(_new)
            _cell_fix.append("%s x %s" % (_role, _c))
    if _cell_fix:
        import sys as _sys
        print("      [格子保底] 补齐 %d 格: %s" % (len(_cell_fix), ", ".join(_cell_fix)))
    derive.last_matrix_violations = matrix_violations
    return slots, drops


def legality_gate(slots, cfg, taxonomy):
    """R2.3：两道硬断言。越界即丢弃并把人数退回同角色最大槽位。"""
    leaves = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
    rc = cfg["role_channel"]
    bad_leaf = [s for s in slots if s["issue_leaf"] and s["issue_leaf"] not in leaves]
    bad_combo = [s for s in slots if s["channel"] not in rc[s["role_type"]]]
    dropped = {s["slot_id"] for s in bad_leaf + bad_combo}
    if dropped:
        keep, refund = [], defaultdict(int)
        for s in slots:
            if s["slot_id"] in dropped:
                refund[s["role_type"]] += s["headcount"]
            else:
                keep.append(s)
        for role, n in refund.items():
            host = max([s for s in keep if s["role_type"] == role], key=lambda s: s["headcount"], default=None)
            if host:
                host["headcount"] += n
                host.setdefault("reason", {}).setdefault("refunded", n)
        slots = keep
    return slots, bad_leaf, bad_combo


def feasibility_gate(slots, pool, ratio):
    """R2.4：候选池 >= 人数 x ratio。pool 为空（画像库未接入）则跳过。"""
    if not pool:
        return None, "画像库未接入，R2.4 跳过"
    gaps = []
    for s in slots:
        cands = [a for a in pool
                 if a["role_type"] == s["role_type"]
                 and a.get("channel_presence", {}).get(s["channel"], 0) > 0
                 and (s["issue_leaf"] is None or s["issue_leaf"] in (a.get("concern_topics") or []))]
        need = int(s["headcount"] * ratio)
        if len(cands) < need:
            gaps.append({"slot_id": s["slot_id"], "role_type": s["role_type"],
                         "issue_leaf": s["issue"], "side": s["side"], "channel": s["channel"],
                         "headcount": s["headcount"], "candidates": len(cands),
                         "deficit": need - len(cands)})
    return gaps, None


def acceptance(slots, cfg, kg):
    """MVP 验收 5 条。"""
    total = sum(s["headcount"] for s in slots)
    rc = cfg["role_channel"]
    a = [("1 人数守恒 == 配额", total == sum(cfg["quota"].values()),
          "实际 %d / 配额 %d" % (total, sum(cfg["quota"].values()))),
         ("2 组合合法（23 个有效组合）",
          all(s["channel"] in rc[s["role_type"]] for s in slots),
          "越界 %d 个" % sum(1 for s in slots if s["channel"] not in rc[s["role_type"]])),
         ("4 事实纯净（无 Agent 节点）",
          not any(e["type"] == "Agent" for e in kg.get("entities", [])),
          "Agent 节点 %d 个" % sum(1 for e in kg.get("entities", []) if e["type"] == "Agent"))]
    digest = hashlib.sha1(json.dumps(slots, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:12]
    a.append(("5 可复现（两次同摘要）", None, "槽位表 SHA1 " + digest))
    return a, digest
