# -*- coding: utf-8 -*-
"""把跑好的事件产物，转成前端 mirofish.html 需要的数据结构。

用法：
    python tools/build_frontend_data.py --run runs/out_n1 --event-key EVT-001 --name "某某事件" --out runs/out_n1/frontend_event.json

产物 frontend_event.json 形如：
    { "id": "EVT-001", "tag": "e1", "name": "...", "t0": "...", "docs": 6,
      "chdist": {...}, "entities": [...], "relations": [...],
      "issues": [...], "slots": {...}, "agents": [...] }

把它追加进 frontend/mirofish.html 里的 const D={"events":[...]} 即可。
"""
from __future__ import annotations
import argparse, json, os
from pathlib import Path

V20 = ("media", "org")


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)}


def contain(a, b):
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(min(len(A), len(B)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="out_nN 目录")
    ap.add_argument("--event-key", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    R, K = Path(a.run), a.event_key
    J = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    pick = lambda d: d if isinstance(d, list) else (d.get("agents") or d.get("members") or [])

    kg = J(R / "kg" / ("event_kg_%s.json" % K))
    pkg = R / "交付包"
    dd = pick(J(pkg / "D_Agent名单.json"))
    mm = pick(J(pkg / "_中间产物" / "D_Agent名单_完整.json"))
    by = {x["agent_id"]: x for x in mm}
    try:
        rel = {x["agent_id"]: x for x in J(R / "relevance" / ("relevance_%s.json" % K))["records"]}
    except Exception:
        rel = {}
    try:
        t1 = {x["agent_id"]: x for x in J(R / "relevance" / ("t1_activation_%s.json" % K))["agents"]}
    except Exception:
        t1 = {}
    rpf = R / "downstream" / ("%s_t0_real_personas.json" % K)
    rp = J(rpf) if rpf.exists() else {"agents": []}
    bound = {x.get("plays_entity") for x in (rp.get("agents") or [])}

    # 实体：只留有关系边或有绑定的（= t0 发声主体），并按包含度去重
    ents = [{"id": "E%d" % (i + 1), "name": e["name"], "kind": e.get("kind"),
             "type": e.get("type"), "role": e.get("role"), "bound": e["name"] in bound}
            for i, e in enumerate(kg.get("entities") or [])]
    rels = [{"f": r["from"], "t": r["to"], "type": r["type"], "fact": (r.get("fact") or "")[:240]}
            for r in (kg.get("relations") or [])]
    for e in ents:
        e["edge"] = sum(1 for r in rels if r["f"] == e["id"] or r["t"] == e["id"])
        e["actor"] = e["edge"] > 0 or e["bound"]
    ents = sorted([e for e in ents if e["actor"]], key=lambda x: -x["edge"])
    keep, drop = [], []
    for e in ents:
        d = next((k for k in keep if contain(k["name"], e["name"]) >= 0.60
                  or k["name"] in e["name"] or e["name"] in k["name"]), None)
        (drop.append((e, d)) if d else keep.append(e))
    mp = {}
    for i in range(len(kg.get("entities") or [])):
        mp["E%d" % (i + 1)] = "E%d" % (i + 1)
    for e, d in drop:
        mp[e["id"]] = d["id"]
    rels = [dict(r, f=mp[r["f"]], t=mp[r["t"]]) for r in rels if mp[r["f"]] != mp[r["t"]]]
    seen, uniq = set(), []
    for r in rels:
        k = (r["f"], r["t"], r["type"])
        if k not in seen:
            seen.add(k); uniq.append(r)
    rels = uniq
    ents = keep
    for e in ents:
        e["edge"] = sum(1 for r in rels if r["f"] == e["id"] or r["t"] == e["id"])
        e["t0"] = e["edge"] > 0

    # 议题
    issues = [{"leaf": x["leaf"], "heat": x.get("heat")}
              for x in (kg.get("issues") or []) if (x.get("provenance") or "ontology") != "auto:oov"]
    # 渠道分布（全量帖）
    ev = J(Path(a.run).parent / "examples" / "x.json") if False else None
    chdist = {}
    try:
        sl = J(R / "slots" / ("slots_%s.json" % K)); sl = sl.get("slots") or sl
    except Exception:
        sl = []
    # agents
    agents = []
    for x in dd:
        m = by.get(x["agent_id"], {})
        agents.append({
            "id": x["agent_id"], "role": x["role_type"], "ch": x["primary_channel"],
            "org": x["role_type"] in ("媒体", "监管"), "real": bool(m.get("is_t0_real")),
            "name": m.get("plays_entity"), "orgT": m.get("org_type"), "orgP": m.get("org_position"),
            "orgS": m.get("org_scope"), "unk": m.get("unknown_fields") or [],
            "g": m.get("gender"), "age": m.get("age"), "reg": m.get("region"), "ut": m.get("urban_tier"),
            "edu": m.get("education"), "occ": m.get("occupation"), "ms": m.get("marital_status"),
            "inc": m.get("income_band"), "circ": m.get("interest_circle"), "fans": m.get("followers"),
            "inf": x["influence_weight"], "hrs": x["active_hours"], "it": x["interest_topics"],
            "sb": x["stance_bias"], "es": x["emotion_sensitivity"],
            "vb": x["valence_baseline"], "ab": x["arousal_baseline"],
            "vb0": x["valence_base_0"], "ab0": x["arousal_base_0"],
            "bio": x["persona_bio"], "tier": (t1.get(x["agent_id"]) or {}).get("tier"),
            "rel": (rel.get(x["agent_id"]) or {}).get("relevance", 0),
            "seat": m.get("seat"), "side": m.get("side"), "issue": m.get("issue_leaf")})
    out = {"id": K, "tag": a.tag or K.lower(), "name": a.name or K, "t0": kg.get("t0"),
           "docs": kg.get("t0_docs") or 0, "chdist": chdist, "entities": ents, "relations": rels,
           "issues": issues, "slots": {"n": len(sl), "head": sum(x.get("headcount") or 0 for x in sl)},
           "t0real": len(rp.get("agents") or []), "agents": agents}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  %s ｜ %d 人 ｜ 实体 %d 边 %d ｜ 议题 %d ｜ t0真实 %d" %
          (K, len(agents), len(ents), len(rels), len(issues), out["t0real"]))
    print("  -> %s" % a.out)


if __name__ == "__main__":
    main()
