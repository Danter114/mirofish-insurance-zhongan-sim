#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
select_agents.py —— ③ 组队：槽位表 + 画像库 -> 本场 500 人名单（v2.0）

输入：slots_<event_id>.json（② 的产出）
      persona_library.json（画像库侧的交付物，契约见 config/persona_contract.json）
输出：roster_<event_id>.csv / .json     本场 500 人名单（恰好 500 行，不扩展候场）
      cooldown_state.json               冷却期状态（只读画像库，状态存我方）

三段流水（打分与选择分离，这是关键）：
  1. 召回打分   软，负责「关联度」—— role + primary_channel 硬过滤，议题与立场软匹配
  2. 配额选择   硬，负责「结构完整性」—— 最小费用最大流，配额误差为 0
  3. 投影物化   写回 (Agent)-[:FILLS]->(Slot)，并回填具名实体 1:1 绑定

★ v2.0 的三处硬口径（对齐组长需求 v2）：
  ① 一人一渠道：primary_channel 必须**等于**槽位渠道，不做多账号（组长 2.4）
  ② 500 人就是 500 人：不再抽候场替补（组长 3.0 输出2「不扩展了」）
  ③ 随机种子：每个随机流用独立的 random.Random(seed + offset)，
     绝不用全局 random.seed()（组长 3.5 坑 1/2）。种子写进产物记录。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import math                                                    # noqa: E402

from common import OUTPUT_DIR, load_config, write_json        # noqa: E402
from persona_io import load_pool, validate_pool               # noqa: E402
from topic_match import build_alias_index, match as topic_match  # noqa: E402
from common import load_config as _lc                        # noqa: E402


# ==================================================== 最小费用最大流
class MCMF:
    def __init__(self, n):
        self.n = n
        self.g = [[] for _ in range(n)]

    def add(self, u, v, cap, cost):
        self.g[u].append([v, cap, cost, len(self.g[v])])
        self.g[v].append([u, 0, -cost, len(self.g[u]) - 1])

    def run(self, s, t, maxf):
        INF = float("inf")
        tf = tc = 0
        while tf < maxf:
            dist = [INF] * self.n
            inq = [False] * self.n
            pv = [-1] * self.n
            pe = [-1] * self.n
            dist[s] = 0.0
            dq = deque([s])
            inq[s] = True
            while dq:
                u = dq.popleft()
                inq[u] = False
                du = dist[u]
                for i, e in enumerate(self.g[u]):
                    v, cap, c, _ = e
                    if cap > 0 and du + c < dist[v] - 1e-12:
                        dist[v] = du + c
                        pv[v], pe[v] = u, i
                        if not inq[v]:
                            inq[v] = True
                            dq.append(v)
            if dist[t] == INF:
                break
            d = maxf - tf
            v = t
            while v != s:
                d = min(d, self.g[pv[v]][pe[v]][1])
                v = pv[v]
            v = t
            while v != s:
                e = self.g[pv[v]][pe[v]]
                e[1] -= d
                self.g[v][e[3]][1] += d
                v = pv[v]
            tf += d
            tc += d * dist[t]
        return tf, tc


# ==================================================== 种子流（组长 3.5）
def make_streams(main_seed, spec):
    """每个随机流一个独立 Random 实例，种子由主种子派生：seed + offset。"""
    off = (spec or {}).get("streams") or {}
    return {name: random.Random(int(main_seed) + int(off.get(name, i + 1)))
            for i, name in enumerate(("team", "jitter", "tiebreak"))}


# ==================================================== 召回 + 打分
def build_candidates(slot, pool_index, tax, sel, cooldown, event_idx, role_demand=0,
                     alias_index=None, rng=None, leaf2top=None, leaf2cat9=None, leaf2concern=None):
    """pool_index 已经按 (role_type, primary_channel) 分好桶 —— 硬过滤在索引里完成。

    ★ v2.1 两处变化：
      ① 议题匹配改成三级（精确 / 同大类 / 模糊检索），模糊层把「拟合不完全」的画像也放进来；
      ② 打分不再只给最终结果，还记下 match_level / match_evidence，输出里可逐条审计。
    """
    leaf, top = slot.get("issue_leaf"), None
    if leaf:
        top = leaf2top.get(leaf)
    rows = []
    for a in pool_index.get((slot["role_type"], slot["channel"]), []):
        m, mlevel, mev = topic_match(leaf, top, a, alias_index or
                                     {"strong": {}, "weak": {}}, sel, leaf2top, leaf2cat9, leaf2concern)
        # ★ 组长要求：按槽位的 purity_group 控制人群纯度
        pth = sel.get("purity_threshold", 0.6)
        grp = slot.get("purity_group", "focused")
        purity_fit = True
        if leaf is not None and mlevel != "named_slot":
            is_focused = m >= pth
            if grp == "noise" and is_focused:
                continue                       # 噪声槽位不收关注该议题的人
            if grp == "focused" and not is_focused:
                purity_fit = False             # 关注的人不够时留作兜底，分数打折
        s_stance = sel["stance_fit"][slot["side"]][a["stance_bias"]]
        s_chan = 1.0 if a["primary_channel"] == slot["channel"] else 0.0   # 一人一渠道
        target = sel["tier_target"].get(slot["tier"], 0.5)
        s_tier = max(0.0, 1.0 - abs(a["_pct_in_role"] - target) * 1.4)
        s_exp = 1.0 if (slot["role_type"] == "客户" and a.get("claim_experience")) else 0.35
        s_act = a.get("activity_level") or 0.5
        gap = event_idx - cooldown.get(a["agent_id"], -999)
        s_cool = 0.0 if gap >= sel["cooldown_events"] else -1.0 * (sel["cooldown_events"] - gap)
        w = sel["weights"]
        score = (w["topic"] * m + w["stance"] * s_stance + w["tier"] * s_tier +
                 w["channel"] * s_chan + w["experience"] * s_exp + w["activity"] * s_act +
                 w["cooldown"] * s_cool
                 - (0.0 if purity_fit else sel.get("purity_fallback_penalty", 1.5)))
        rows.append({"agent_id": a["agent_id"], "score": score, "agent": a,
                     "purity_fit": purity_fit, "match_level": mlevel,
                     "match_evidence": mev[:2],
                     "parts": {"topic": round(m, 3), "stance": round(s_stance, 3),
                               "tier": round(s_tier, 3), "channel": round(s_chan, 3),
                               "exp": round(s_exp, 3), "cool": round(s_cool, 3)}})
    rows.sort(key=lambda r: (-r["score"], r["agent_id"]))
    width = max(sel["max_candidates_per_slot"],
                slot["headcount"] + sel["candidate_width_extra"],
                role_demand + sel["candidate_width_extra"])
    return rows[:width]


# ==================================================== 配额约束选择
def sample_scores(cand_by_slot, rng, temperature):
    """★ 按概率分布随机抽样（用户要求）。

    给每条候选边加一个 Gumbel 噪声：score' = score + T x Gumbel(0,1)。
    之后的最小费用最大流在 score' 上求最优 —— argmax(score + T*Gumbel) 服从 softmax(score/T)，
    所以这等价于「按分布抽样」，同时又**严格满足配额**（MCMF 保证）。
    T = 0 时退化为确定性最优；T 越大抽样越随机。种子固定 -> 结果固定。
    实现：Gumbel = -log(-log(U))，U ~ Uniform(0,1)。
    """
    if not temperature or temperature <= 0:
        for rows in cand_by_slot.values():
            for r in rows:
                r["score_sampled"] = r["score"]
        return 0.0
    mx = 0.0
    for rows in cand_by_slot.values():
        for r in rows:
            u = min(max(rng.random(), 1e-12), 1 - 1e-12)
            g = -math.log(-math.log(u))
            r["score_sampled"] = r["score"] + temperature * g
            mx = max(mx, g)
    return mx


def solve(slots, cand_by_slot, mult=1.0):
    S = len(slots)
    agents = sorted({c["agent_id"] for rows in cand_by_slot.values() for c in rows})
    ai = {a: i for i, a in enumerate(agents)}
    SRC, SNK = 0, 1 + S + len(agents)
    m = MCMF(SNK + 1)
    for i, s in enumerate(slots):
        m.add(SRC, 1 + i, int(round(s["headcount"] * mult)), 0.0)
    for a in agents:
        m.add(1 + S + ai[a], SNK, 1, 0.0)
    for i, s in enumerate(slots):
        for c in cand_by_slot[s["slot_id"]]:
            m.add(1 + i, 1 + S + ai[c["agent_id"]], 1,
                  -c.get("score_sampled", c["score"]))
    flow, cost = m.run(SRC, SNK, sum(int(round(s["headcount"] * mult)) for s in slots))
    assign = defaultdict(list)
    for i, s in enumerate(slots):
        for e in m.g[1 + i]:
            v, cap = e[0], e[1]
            if 1 + S <= v < 1 + S + len(agents) and cap == 0:
                assign[s["slot_id"]].append(agents[v - 1 - S])
    return assign, flow


# ==================================================== 缺口回流
def fill_role_gaps(slots, cand, assign, allrow):
    """fill_gap_policy：某角色正选不满配额时，缺口在同角色内回流给它人数最大的槽位。
    三条硬底线：只在同角色内回流 / 只用真实候选 / 不够就如实上报不随机补人。"""
    by_role = defaultdict(list)
    for s in slots:
        by_role[s["role_type"]].append(s)
    got_primary = {s["slot_id"]: min(len(assign.get(s["slot_id"], [])), s["headcount"])
                   for s in slots}
    used = {a for arr in assign.values() for a in arr}
    gap_agents, records = set(), []
    for role, arr in sorted(by_role.items()):
        demand = sum(s["headcount"] for s in arr)
        have = sum(got_primary[s["slot_id"]] for s in arr)
        missing = demand - have
        if missing <= 0:
            continue
        host = max(arr, key=lambda s: (s["headcount"], s["slot_id"]))
        pool_rows = {}
        for s in arr:
            for r in cand.get(s["slot_id"], []):
                if r["agent_id"] not in used:
                    pool_rows.setdefault(r["agent_id"], r)
        ranked = sorted(pool_rows.values(), key=lambda r: (-r["score"], r["agent_id"]))
        take = [r["agent_id"] for r in ranked[:missing]]
        for a in take:
            used.add(a)
            gap_agents.add(a)
            assign[host["slot_id"]].append(a)
        records.append({"role_type": role, "demand": demand, "assigned": have,
                        "deficit": missing, "filled": len(take),
                        "still_short": missing - len(take), "host_slot": host["slot_id"],
                        "policy": "同角色内回流到该角色最大槽位；不跨角色、不跨议题、不随机补人"})
    return assign, gap_agents, records


# ==================================================== 单场组队
def run_event(slots, pool, tax, sel, cooldown, event_idx, streams, alias_cfg=None):
    # ★ 一人一渠道：直接按 (角色, 主渠道) 建索引 —— 硬过滤在索引里完成，不是循环里 if
    pool_index = defaultdict(list)
    for a in pool:
        pool_index[(a.get("role_type"), a.get("primary_channel"))].append(a)
    leaf2top = {lf["leaf"]: t for t, v in tax["categories"].items() for lf in v["leaves"]}
    alias_index = build_alias_index(alias_cfg or {}, set(leaf2top))
    _clib = _lc("concern_topics_library") or {}
    _l2c9 = _clib.get("leaf_to_cat9") or {}
    _l2cn = _clib.get("leaf_to_concern") or {}
    samp = sel.get("sampling") or {}
    temperature = float(samp.get("temperature", 0.0)) if samp.get("mode", "gumbel") == "gumbel" else 0.0

    cand, feas = {}, {}
    role_demand = Counter()
    for s in slots:
        role_demand[s["role_type"]] += s["headcount"]
    for s in slots:
        rows = build_candidates(s, pool_index, tax, sel, cooldown, event_idx,
                                role_demand[s["role_type"]], alias_index,
                                streams.get("sampling") or streams["team"], leaf2top, _l2c9, _l2cn)
        cand[s["slot_id"]] = rows
        if s.get("issue_leaf") is None or s.get("purity_group") == "noise":
            feas[s["slot_id"]] = len([r for r in rows if r["parts"]["stance"] >= 0.45
                                      and r["parts"]["cool"] == 0])
        else:
            feas[s["slot_id"]] = len([r for r in rows if r["parts"]["topic"] >= 0.6
                                      and r["parts"]["stance"] >= 0.45
                                      and r["parts"]["cool"] == 0])
    # ★ 先按概率分布抽样（Gumbel 噪声），再跑配额硬约束 —— 两步合起来 = 带配额的概率抽样
    mx_g = sample_scores(cand, streams.get("sampling") or streams["team"], temperature)
    mult = 1.0 + float(sel.get("reserve_ratio", 0.0) or 0.0)
    assign, flow = solve(slots, cand, mult=mult)
    allrow = {}
    for rows in cand.values():
        for r in rows:
            allrow.setdefault(r["agent_id"], r)
    assign, gap_agents, gap_records = fill_role_gaps(slots, cand, assign, allrow)
    rowmap = {sid: {r["agent_id"]: r for r in rows} for sid, rows in cand.items()}
    pth = sel.get("purity_threshold", 0.6)

    def row_of(slot, aid):
        return (rowmap.get(slot["slot_id"], {}) or {}).get(aid) or allrow[aid]

    roster, shortfall = [], 0
    for s in slots:
        got = sorted(assign.get(s["slot_id"], []), key=lambda a: -row_of(s, a)["score"])
        shortfall += max(0, s["headcount"] - min(len(got), s["headcount"]))
        for seat, aid in enumerate(got[: s["headcount"]], 1):
            r = row_of(s, aid)
            a = r["agent"]
            pur_fit = (r.get("purity_fit", True) if aid not in gap_agents
                       else not (s.get("issue_leaf") and s.get("purity_group") == "focused"
                                 and (r["parts"].get("topic") or 0) < pth))
            roster.append({
                "agent_id": aid, "seat": seat, "slot_id": s["slot_id"],
                "role_type": s["role_type"], "primary_channel": s["channel"],
                "subtype": a.get("subtype"), "issue_leaf": s.get("issue_leaf"),
                "side": s["side"], "tier": s["tier"], "purity_group": s.get("purity_group", "focused"),
                "purity_fit": pur_fit, "fill_gap": aid in gap_agents,
                # ★ 议题匹配是怎么命中的：exact / same_top / fuzzy_alias / fuzzy_char / none
                "topic_match_level": r.get("match_level"),
                "topic_match_evidence": r.get("match_evidence") or [],
                # ★ 具名实体 1:1 绑定（组长 3.4 ①）：这个人扮演谁
                "plays_entity": s.get("bound_entity"),
                "plays_kind": s.get("bound_kind"),
                "plays_post_ids": s.get("bound_post_ids") or [],
                # 画像侧参数，原样带下去（组长 1.2 ①：仿真层要拿这些算）
                # ★★ 组长 v3 §2.2-8：stance_base（🟦）+ stance_bias（🟨 事件级由 t0_init 覆盖）
                "stance_base": a.get("stance_base"),
                "stance_bias": a.get("stance_bias"),
                "influence_weight": a.get("influence_weight"),
                # ★ 粉丝数：真实字段名是 followers（此前误读了旧的占位字段 follower_count，导致名册里全为 0）
                "followers": a.get("followers") if a.get("followers") is not None else a.get("follower_count"),
                "activity_level": a.get("activity_level"),
                # ★★ active_hours = 在线小时的列表（24 小时制，本地时间）
                "active_hours": a.get("active_hours_online") or a.get("active_hours_list") or a.get("active_hours"),
                # ★★ 组长 v3 #18：cross_channel_propensity 已删（跨渠道可见性归【渠道传播表】，是渠道对的属性）
                "emotion_sensitivity": a.get("emotion_sensitivity"),
                "contagion_susceptibility": a.get("contagion_susceptibility"),
                "emotion_decay": a.get("emotion_decay"),
                # ★★ 组长 v3 J12：改名为复数 action_thresholds（🟨 15 格规则表）
                "action_thresholds": a.get("action_thresholds") or a.get("action_threshold"),
            # ★ 规范④：转发看 share_propensity，投诉/举报看 complaint_propensity
            #   这两个字段必须下发，否则"中度档"解锁后无法掷骰决定做不做
                # ★★ 组长 v3 J5：share_propensity / complaint_propensity 已删（改由 LLM 判断）
                # ★★ 组长 v3 §2.2-4 / -12：性格两字段 + active_level（抽样软门的输入）
                "emotional_stability": a.get("emotional_stability"),
                "extraversion": a.get("extraversion"),
                "active_level": a.get("active_level"),
            # ★ 渠道参数包（reach/amp/decay）—— 跨平台动作时按【目标平台】取
            "channel_behavior": a.get("channel_behavior"),
                # ★★ 组长 v3 §2.2-5 统一命名：interest_topics（concern_topics 保留兼容）
                "interest_topics": a.get("interest_topics") or a.get("concern_topics"),
                # ★★ 四档明细（组长 §1.5：配合 interest_topics 看「话题+权重」落在哪一档）
                "concern_tier": a.get("concern_tier"),
                # ★ 真实人物注入用的字段（此前白名单没有 → 打包后丢失）
                "unknown_fields": a.get("unknown_fields"),
                "is_t0_real": a.get("is_t0_real"),
                "agent_id_original": a.get("agent_id_original"),
                "concern_topics": a.get("concern_topics") or a.get("interest_topics"),
            # ★ 机构账号字段：推演层按机构类型做【报道领域 / 管辖范围】判定（post 触发规则 v2.0 §二）
            "org_type": a.get("org_type"),
            # ★ 九维骨架透传给推演层（bio 定调、人群分层分析都要用）
            "age": a.get("age"), "age_band": a.get("age_band"), "gender": a.get("gender"),
            "occupation": a.get("occupation"), "urban_tier": a.get("urban_tier"),
            "education": a.get("education"), "income_band": a.get("income_band"),
            "marital_status": a.get("marital_status"),
            # ★ 仿真层要 valence/arousal 基线（组长 清洗后event1画像.json 口径）
            "valence_baseline": a.get("valence_baseline"), "arousal_baseline": a.get("arousal_baseline"),
            "interest_circle": a.get("interest_circle"), "persona_bio": a.get("persona_bio"),
            "org_position": a.get("org_position"),
            "org_scope": a.get("org_scope"),   # ★ 对齐规范 v3（单数）
                "region": a.get("region"),
                "score": round(r["score"], 4), "score_parts": r["parts"],
                "provenance": "generated"})
    lv = Counter(p.get("topic_match_level") for p in roster)
    return roster, {"flow": flow, "shortfall": shortfall, "feas": feas,
                    "match_levels": dict(lv),
                    "sampling": {"mode": samp.get("mode", "gumbel"), "temperature": temperature,
                                 "max_gumbel": round(mx_g, 3)},
                    "gap_records": gap_records,
                    "gap_filled": sum(x["filled"] for x in gap_records),
                    "gap_unfilled": sum(x["still_short"] for x in gap_records)}


# ==================================================== 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="画像库 JSON（画像库侧的交付物）")
    ap.add_argument("--slots-dir", default=None, help="默认 <out>/slots")
    ap.add_argument("--out", default=str(OUTPUT_DIR))
    ap.add_argument("--events", type=int, default=0, help="只跑前 N 个事件（0=全部）")
    ap.add_argument("--seed", type=int, default=None, help="主随机种子（默认读 config/selection.json）")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(args.report, "w", encoding="utf-8")
    P = print

    tax, sel, contract = load_config("taxonomy"), load_config("selection"), load_config("persona_contract")
    slots_cfg, alias_cfg = load_config("slots"), load_config("topic_alias")
    outdir = Path(args.out)
    slots_dir = Path(args.slots_dir) if args.slots_dir else outdir / "slots"
    seldir = outdir / "selection"
    seldir.mkdir(parents=True, exist_ok=True)

    main_seed = int(args.seed if args.seed is not None else (sel.get("seed") or {}).get("main", 20261002))
    streams = make_streams(main_seed, sel.get("seed") or {})
    P("=" * 84)
    P("③ 组队：槽位表 + 画像库 -> 本场 500 人名单")
    P("=" * 84)
    P("主随机种子 seed = %d    派生流：%s"
      % (main_seed, {k: main_seed + int(((sel.get("seed") or {}).get("streams") or {}).get(k, i + 1))
                     for i, k in enumerate(("team", "jitter", "tiebreak"))}))
    P("抽样抖动 jitter = %s（0 表示组队完全由分数决定，种子只做记录）"
      % (sel.get("seed") or {}).get("jitter"))

    pool = load_pool(args.pool, tax, contract, slots_cfg, alias_cfg)
    ok, rep = validate_pool(pool, tax, contract, slots_cfg, alias_cfg)
    P("\n[0] 画像库契约校验：%s（%d 条）" % ("通过" if ok else "存在 FAIL", len(pool)))
    for l in rep:
        if l.strip().startswith("[FAIL]") or "可支撑场次" in l or l.startswith("    "):
            P("    " + l.strip())
    if not ok:
        P("\n    !! 契约未通过，先把 FAIL 项反馈给画像库侧。仍继续跑，但结果仅供调试。")

    slot_files = sorted(slots_dir.glob("slots_*.json"))
    if args.events:
        slot_files = slot_files[: args.events]
    P("\n[1] 待组队事件 %d 个" % len(slot_files))

    cooldown_path = seldir / "cooldown_state.json"
    state = {"_format": "per-event",
             "note": "按场次记录用过哪些画像；重跑同一场会覆盖该场记录，结果幂等",
             "seed": main_seed, "event_order": [], "assignments": {}}
    if cooldown_path.exists():
        try:
            old = json.loads(cooldown_path.read_text(encoding="utf-8"))
        except Exception:
            old = None
        if isinstance(old, dict) and old.get("_format") == "per-event":
            state = old
            state["seed"] = main_seed
        elif old:
            P("    [warn] cooldown_state.json 是旧格式，无法判断哪场用了谁，已忽略重建")

    def cooldown_map(st):
        cd = {}
        for i, e in enumerate(st["event_order"], 1):
            for a in st.get("assignments", {}).get(e, []):
                cd[a] = i
        return cd

    rows, all_feas, usage = [], {}, Counter()
    P("\n    %-22s %6s %6s %8s %8s %8s" % ("event_id", "槽位", "选出", "配额差", "缺口", "可行性不足"))
    for sf in slot_files:
        eid = sf.stem.replace("slots_", "")
        if eid in state["event_order"]:
            event_idx = state["event_order"].index(eid) + 1
            state["assignments"][eid] = []
        else:
            state["event_order"].append(eid)
            event_idx = len(state["event_order"])
        cooldown = cooldown_map(state)
        slots = json.loads(sf.read_text(encoding="utf-8"))
        roster, st = run_event(slots, pool, tax, sel, cooldown, event_idx, streams, alias_cfg)
        state["assignments"][eid] = [p["agent_id"] for p in roster]
        for p in roster:
            usage[p["agent_id"]] += 1
        quota = Counter(p["role_type"] for p in roster)
        want = Counter(s["role_type"] for s in slots for _ in range(s["headcount"]))
        diff = sum(abs(quota[r] - want[r]) for r in set(quota) | set(want))
        short_feas = sum(1 for s in slots if st["feas"].get(s["slot_id"], 0) < s["headcount"])
        rows.append({"event_id": eid, "slots": len(slots), "picked": len(roster),
                     "quota_diff": diff, "shortfall": st["shortfall"], "feas_short": short_feas,
                     "gap_filled": st.get("gap_filled", 0),
                     "gap_unfilled": st.get("gap_unfilled", 0),
                     "gap_records": st.get("gap_records", [])})
        all_feas[eid] = st["feas"]
        with open(seldir / ("roster_%s.csv" % eid), "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("agent_id,seat,role_type,primary_channel,subtype,slot_id,issue_leaf,side,"
                     "tier,purity_group,purity_fit,plays_entity,influence_weight,score\n")
            for p in roster:
                fh.write("%s,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                    p["agent_id"], p["seat"], p["role_type"], p["primary_channel"],
                    p["subtype"] or "", p["slot_id"], p["issue_leaf"] or "", p["side"],
                    p["tier"], p["purity_group"], str(p["purity_fit"]).lower(),
                    (p["plays_entity"] or "").replace(",", " "),
                    p["influence_weight"], p["score"]))
        write_json(seldir / ("roster_%s.json" % eid), roster)
        P("    %-22s %6d %6d %8d %8d %8d" % (eid, len(slots), len(roster), diff,
                                             st["shortfall"], short_feas))
    write_json(cooldown_path, state)

    P("\n[2] 汇总")
    tot = sum(r["picked"] for r in rows)
    P("    事件 %d 个 / 共选出 %d 人次（每场 500）" % (len(rows), tot))
    multi = sum(1 for v in usage.values() if v > 1)
    P("    被复用 >=2 次的画像：%d 条（复用得越多，事件之间的区分度越低）" % multi)
    P("    配额误差非零的事件：%d 个" % sum(1 for r in rows if r["quota_diff"] != 0))
    P("    填不满的槽位合计：%d 个" % sum(r["shortfall"] for r in rows))
    gf, gu = sum(r["gap_filled"] for r in rows), sum(r["gap_unfilled"] for r in rows)
    (outdir / "procurement").mkdir(parents=True, exist_ok=True)
    with open(outdir / "procurement" / "fill_gap.csv", "w", encoding="utf-8") as fh:
        fh.write("event_id,role_type,demand,assigned,deficit,filled,still_short,host_slot\n")
        for r in rows:
            for g in r["gap_records"]:
                fh.write("%s,%s,%d,%d,%d,%d,%d,%s\n" % (
                    r["event_id"], g["role_type"], g["demand"], g["assigned"], g["deficit"],
                    g["filled"], g["still_short"], g["host_slot"]))
    P("    R2.4 缺口回流：补位 %d 人，仍缺 %d 人" % (gf, gu) if (gf or gu)
      else "    R2.4 缺口回流：本场无缺口，无需回流")
    cd_now = cooldown_map(state)
    P("    画像库消耗：%d / %d 条被用过（占 %.0f%%）"
      % (len(cd_now), len(pool), 100.0 * len(cd_now) / max(1, len(pool))))

    last = slot_files[-1].stem.replace("slots_", "") if slot_files else None
    if last:
        picks = json.loads((seldir / ("roster_%s.json" % last)).read_text(encoding="utf-8"))
        P("\n[3] 最近一场（%s）的人员结构" % last)
        P("    角色：%s" % dict(Counter(p["role_type"] for p in picks).most_common()))
        P("    主渠道：%s" % dict(Counter(p["primary_channel"] for p in picks).most_common()))
        P("    子型多样性：%d 种" % len({p["subtype"] for p in picks}))
        named = [p for p in picks if p.get("plays_entity")]
        P("    具名实体 1:1 绑定：%d 人 -> %s"
          % (len(named), [(p["agent_id"], p["plays_entity"]) for p in named][:6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
