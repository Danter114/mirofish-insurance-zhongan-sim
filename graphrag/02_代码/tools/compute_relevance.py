#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_relevance.py —— 算出每个选出的人与本次事件的相关度

    python tools/compute_relevance.py \
        --roster out_cat9h/downstream/EVT-001_roster.json \
        --kg     out_cat9h/kg/event_kg_EVT-001.json \
        --t0     out_cat9h/downstream/EVT-001_t0_snapshot.json \
        --event  examples/event1_pilot_last_version.json \
        --personas out/persona_cat9h_2000_adapted.json \
        --out    out_cat9h/relevance

产出：
    relevance_EVT-001.json          逐人相关度（含三个分量与证据）
    relevance_EVT-001.csv           同上的表格版
    relevance_EVT-001.digest.txt    库指纹 + 全部输入文件的 sha256
"""
from __future__ import annotations

import argparse
import csv
import hashlib as _hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common import load_config                      # noqa: E402
from persona_io import load_pool                    # noqa: E402
from topic_match import build_alias_index, match    # noqa: E402
import relevance as R                               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--kg", required=True)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--event", required=True, help="含 topic_list 的原始事件文件")
    ap.add_argument("--personas", required=True)
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()

    tax, contract, slots_cfg = load_config("taxonomy"), load_config("persona_contract"), load_config("slots")
    alias_cfg, sel_cfg = load_config("topic_alias"), load_config("selection")
    lib, rel_cfg = load_config("concern_topics_library"), load_config("relevance")
    tmap = load_config("topiclist_to_cat9")
    # ★ 三级归一化链 ②->③ 的两个映射（组长 v3 §2.4）
    t2c9 = load_config("topic_to_cat9")        # 47 话题 -> 9 大类
    # alias_cfg 已含 general_bridge（47话题 -> [议题 leaf]），传给 compute 做反向查

    roster = json.loads(Path(args.roster).read_text(encoding="utf-8"))
    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    t0 = json.loads(Path(args.t0).read_text(encoding="utf-8"))
    ev = json.loads(Path(args.event).read_text(encoding="utf-8"))
    ev_posts = ev if isinstance(ev, list) else (ev.get("posts") or ev.get("records") or [])

    pool = load_pool(args.personas, tax, contract, slots_cfg, alias_cfg)
    by_id = {p["agent_id"]: p for p in pool}
    leaf2top = {lf["leaf"]: t for t, v in tax["categories"].items() for lf in v["leaves"]}
    alias_index = build_alias_index(alias_cfg, set(leaf2top))
    l2c9 = lib.get("leaf_to_cat9") or {}
    l2cn = lib.get("leaf_to_concern") or {}

    members = roster.get("members") if isinstance(roster, dict) else roster
    # ★★ t0 真实参与者的相关度下限 —— 事件本身证明其在场，不该被抽样相关度压低。
    #   与 inject_real_personas.RELEVANCE_FLOOR 保持一致。
    _is_real = {m.get("agent_id"): bool(m.get("is_t0_real")) for m in members}
    recs = R.compute(members, kg, t0, ev_posts, lib, rel_cfg, tmap,
                     match, alias_index, leaf2top, l2c9, l2cn, sel_cfg, by_id,
                     topic_alias=alias_cfg, topic_to_cat9=t2c9)

    # ★★ t0 真实参与者的相关度下限 —— 必须在【排名与分档之前】。
    #   事件本身证明其在场，不该被抽样相关度压低。下限后必须【重新排名】，
    #   否则 rank/tier 还是按旧相关度算的（踩过：真实人物 relevance=0.95 但 tier 还是 D）。
    _REAL_FLOOR = 0.95
    _n_real = 0
    for _r in recs:
        if _is_real.get(_r.get("agent_id")):
            _old = _r.get("relevance") or 0.0
            # ★ 2026-10-13 组长要求「每个人的相关度必须特异不一样」：
            #   原先这里把全部 t0 真实人物硬压成同一个 0.95（实测 EVT-001 有 6 人并列、
            #   EVT-010 有 18 人并列）。现在在 0.95 之上叠一个由 agent_id 派生的唯一微扰，
            #   量级 1e-6~9e-4 —— 小到不影响「t0 真实人物排最前」，但保证任意两人不同。
            #   与 tools/inject_real_personas.py 用同一个派生方式，两处结果一致。
            _h = _hashlib.sha256(("t0-rel:" + str(_r.get("agent_id"))).encode("utf-8")).digest()
            _eps = 1e-6 * (1 + int.from_bytes(_h[:3], "big") % 900)
            _r["relevance"] = round(max(_old, _REAL_FLOOR) + _eps, 6)
            _r["relevance_source"] = ("★ t0 真实参与者：max(抽样值 %.4f, %.2f) —— "
                                      "事件本身证明其在场" % (_old, _REAL_FLOOR))
            _n_real += 1
    if _n_real:
        recs.sort(key=lambda x: (-(x.get("relevance") or 0.0), str(x.get("agent_id"))))
        for _i, _r in enumerate(recs, 1):
            _r["rank"] = _i
        print("  ★ t0 真实参与者 %d 人：相关度抬到 %.2f 并重排名（现为第 1~%d 名，A 档）"
              % (_n_real, _REAL_FLOOR, _n_real))

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    eid = roster.get("event_id") or kg.get("event_id") or "EVENT"
    jp = out / ("relevance_%s.json" % eid)
    cp = out / ("relevance_%s.csv" % eid)
    dp = out / ("relevance_%s.digest.txt" % eid)

    dg = R.digest(recs)
    jp.write_text(json.dumps({
        "artifact": "relevance", "artifact_version": "1.0", "event_id": eid,
        "rule": "relevance = %.2f*R1_topic + %.2f*R2_post + %.2f*R3_graph" % (
            rel_cfg["weights"]["R1"], rel_cfg["weights"]["R2"], rel_cfg["weights"]["R3"]),
        "weights": rel_cfg["weights"], "graph_tier": rel_cfg["graph_tier"],
        "component_defs": {
            "R1_topic": "图谱 issues 按 heat 加权 × 该人在该议题上的匹配分，再除以 heat 之和",
            "R2_post": "t0 窗内每条帖子的 topic_list 映射到 9 大类，取该人在这些大类上的权重占比，按帖子互动量加权",
            "R3_graph": "图谱位置：角色基础档位 + 具名直绑加成 + 帖子关联加成，截断到 [0,1]",
        },
        "inputs": {n: {"path": str(p), "sha256": R.file_sha256(p)} for n, p in [
            ("roster", args.roster), ("kg", args.kg), ("t0", args.t0),
            ("event", args.event), ("personas", args.personas)]},
        "digest": dg, "count": len(recs),
        "distribution": {"max": recs[0]["relevance"] if recs else 0,
                         "min": recs[-1]["relevance"] if recs else 0},
        "records": recs,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(cp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "agent_id", "role_type", "primary_channel", "issue_leaf",
                    "relevance", "R1_topic", "R2_post", "R3_graph",
                    "graph_evidence", "issue_evidence"])
        for r in recs:
            w.writerow([r["rank"], r["agent_id"], r["role_type"], r["primary_channel"],
                        r["issue_leaf"] or "", r["relevance"],
                        r["components"]["R1_topic"], r["components"]["R2_post"], r["components"]["R3_graph"],
                        r["evidence"]["graph"], " / ".join(r["evidence"]["issues"][:4])])

    lines = ["event_id=%s" % eid, "records=%d" % len(recs), "digest=%s" % dg,
             "weights=R1:%.2f R2:%.2f R3:%.2f" % (rel_cfg["weights"]["R1"], rel_cfg["weights"]["R2"], rel_cfg["weights"]["R3"]),
             "", "[input sha256]"]
    for n, p in [("roster", args.roster), ("kg", args.kg), ("t0", args.t0),
                 ("event", args.event), ("personas", args.personas)]:
        lines.append("%-10s %s  %s" % (n, R.file_sha256(p), p))
    dp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ================= ★ t1 激活包（给组长推演层用） =================
    tiers_cfg = rel_cfg.get("activation_tiers") or [
        {"tier": "A", "pct": 0.05, "label": "高激活", "t1_behavior": "t1 主动发起动作（首发扩散层）"},
        {"tier": "B", "pct": 0.15, "label": "较高", "t1_behavior": "t1 响应已激活内容，做互动类动作"},
        {"tier": "C", "pct": 0.40, "label": "中", "t1_behavior": "t1 仅被动接收，不主动动作"},
        {"tier": "D", "pct": 0.70, "label": "低", "t1_behavior": "t1 不动作"},
        {"tier": "E", "pct": 1.00, "label": "最低", "t1_behavior": "t1 不动作"},
    ]
    n = len(recs)
    cuts = []
    prev = 0
    for t in tiers_cfg:
        k = min(n, max(prev + 1, round(t["pct"] * n)))
        cuts.append((t, prev, k))
        prev = k
    pmap = {p["agent_id"]: p for p in pool}
    agents = []
    for r in recs:
        tier = "E"
        for t, a, b in cuts:
            if a < r["rank"] <= b:
                tier = t["tier"]; break
        m = next((x for x in members if x.get("agent_id") == r["agent_id"]), {})
        pf = pmap.get(r["agent_id"]) or {}
        cb = pf.get("channel_behavior") or {}
        agents.append({
            "activation_priority": r["rank"], "tier": tier,
            "agent_id": r["agent_id"], "relevance": r["relevance"],
            "components": r["components"],
            "role_type": r["role_type"], "primary_channel": r["primary_channel"],
            "slot_id": r.get("slot_id"), "issue_leaf": r.get("issue_leaf"),
            "side": m.get("side"), "stance_bias": m.get("stance_bias"),
            "plays_entity": m.get("plays_entity"), "plays_post_ids": m.get("plays_post_ids") or [],
            # ★★ 字段表对齐当前口径（此前残留 7 个已删字段、缺 8 个现行字段）：
            #   删 action_threshold(单) / share_propensity / complaint_propensity /
            #      valence_baseline / arousal_baseline / follower_count / activity_level
            #   增 action_thresholds / interest_topics / concern_tier / stance_base /
            #      active_level / followers / emotional_stability / extraversion /
            #      is_t0_real / relevance_source / unknown_fields
            "influence_weight": m.get("influence_weight"), "followers": m.get("followers"),
            "active_level": m.get("active_level"), "active_hours": m.get("active_hours"),
            "emotional_stability": m.get("emotional_stability"),
            "extraversion": m.get("extraversion"),
            "emotion_sensitivity": m.get("emotion_sensitivity"),
            "emotion_decay": m.get("emotion_decay"),
            "contagion_susceptibility": m.get("contagion_susceptibility"),
            "interest_topics": m.get("interest_topics"), "concern_tier": m.get("concern_tier"),
            "stance_base": m.get("stance_base"),
            "is_t0_real": bool(m.get("is_t0_real")),
            "relevance_source": r.get("relevance_source"),
            "unknown_fields": m.get("unknown_fields"),
            # --- 以下字段透传，供推演层决定"激活后做什么动作"
            #     ★ action_thresholds 与 emotion_decay 是【角色x渠道规则表】15 格，同格同值
            "action_thresholds": m.get("action_thresholds") or pf.get("action_thresholds"),
            "channel_behavior": cb or None,
            "available_actions": cb.get("available_actions"),
            "reach_multiplier": cb.get("reach_multiplier"),
            "emotion_amplifier": cb.get("emotion_amplifier"),
            "info_decay_rate": cb.get("info_decay_rate"),
            "evidence": r["evidence"],
        })
    # (渠道 x 角色) x 档位 的人数矩阵，方便推演层按渠道配比激活
    matrix = {}
    for a in agents:
        k = "%s|%s" % (a["role_type"], a["primary_channel"])
        matrix.setdefault(k, {})
        matrix[k][a["tier"]] = matrix[k].get(a["tier"], 0) + 1
    tier_rows = [{"tier": t["tier"], "label": t["label"], "pct": t["pct"],
                  "rank_from": a + 1, "rank_to": b,
                  "count": b - a, "relevance_min": recs[b - 1]["relevance"] if b > 0 else 0,
                  "relevance_max": recs[a]["relevance"] if a < n else 0,
                  "t1_behavior": t["t1_behavior"]}
                 for t, a, b in cuts]
    ap = out / ("t1_activation_%s.json" % eid)
    ap.write_text(json.dumps({
        "artifact": "t1_activation_pack", "artifact_version": "1.0", "event_id": eid,
        "purpose": "给推演层：t1 时点按相关度优先级激活 agent 做动作",
        "relevance_formula": "relevance = %.2f*R1_topic + %.2f*R2_post + %.2f*R3_graph" % (
            rel_cfg["weights"]["R1"], rel_cfg["weights"]["R2"], rel_cfg["weights"]["R3"]),
        "relevance_digest": dg,
        "tier_rule": "按本场相关度的分位数分层（不用固定阈值 —— 每场自动适配本场分布）",
        "tiers": tier_rows,
        "channel_role_matrix": matrix,
        "how_to_use": [
            "① 推演层按 tiers 选激活范围：建议 t1 只激活 A 档（或 A+B）",
            "② 按 activation_priority 升序逐人处理",
            "③ 每个人的 action_thresholds + available_actions 决定它具体做什么动作、做不做",
            "④ 相关度只负责『谁先被激活』；『激活后干什么』由画像参数决定",
        ],
        "agents": agents,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / ("t1_activation_%s.csv" % eid), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["activation_priority", "tier", "agent_id", "relevance", "R1_topic", "R2_post",
                    "R3_graph", "role_type", "primary_channel", "issue_leaf", "stance_bias",
                    "plays_entity", "influence_weight", "t1_behavior"])
        for a in agents:
            beh = next((t["t1_behavior"] for t in tier_rows if t["tier"] == a["tier"]), "")
            w.writerow([a["activation_priority"], a["tier"], a["agent_id"], a["relevance"],
                        a["components"]["R1_topic"], a["components"]["R2_post"], a["components"]["R3_graph"],
                        a["role_type"], a["primary_channel"], a["issue_leaf"] or "",
                        a["stance_bias"] or "", a["plays_entity"] or "",
                        a["influence_weight"], beh])
    print("")
    print("t1 激活包 -> %s" % ap)
    for t in tier_rows:
        print("  %s 档 %-4s 第 %d~%d 名（%d 人）  相关度 %.4f ~ %.4f  %s"
              % (t["tier"], t["label"], t["rank_from"], t["rank_to"], t["count"],
                 t["relevance_min"], t["relevance_max"], t["t1_behavior"]))
    print("相关度已算：%d 人" % len(recs))
    print("  公式：relevance = %.2f*R1 + %.2f*R2 + %.2f*R3"
          % (rel_cfg["weights"]["R1"], rel_cfg["weights"]["R2"], rel_cfg["weights"]["R3"]))
    print("  最高 %.4f（%s）  最低 %.4f（%s）"
          % (recs[0]["relevance"], recs[0]["agent_id"], recs[-1]["relevance"], recs[-1]["agent_id"]))
    print("  相关度 ≥0.6 的 %d 人；≥0.4 的 %d 人"
          % (sum(1 for r in recs if r["relevance"] >= 0.6), sum(1 for r in recs if r["relevance"] >= 0.4)))
    print("  指纹 digest = %s" % dg)
    print("  产出 -> %s" % jp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
