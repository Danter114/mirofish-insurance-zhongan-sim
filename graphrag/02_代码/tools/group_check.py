#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
group_check.py —— 组队五条规则的第 3、4 条校验（组长《交付清单》§五）

  3. 多样性校验（立场 / 敏感度 / 活跃度分布不塌缩）
  4. 子型覆盖（KOL 要覆盖财经 / 法律 / 母婴等不同垂类）

第 1、2、5 条（无放回 / 冷却期 5 / 固定种子）已由 select_agents 保证。
本工具只做【校验 + 报告】，不改选人结果。
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _stance_bin(m):
    """把 stance_bias 数字分档。事件级优先（t0_init 算的），退回画像默认值。"""
    v = None
    t0 = m.get("t0") or {}
    sb = t0.get("stance_bias")
    if isinstance(sb, dict) and sb:
        # 取该人主议题槽位上的 stance_bias；没有就用均值
        leaf = m.get("issue_leaf")
        v = sb.get(leaf) if leaf in sb else (sum(sb.values()) / len(sb))
    if v is None:
        v = t0.get("stance_base") if t0.get("stance_base") is not None else m.get("stance_base")
    if v is None:
        x = m.get("stance_bias")
        v = x if isinstance(x, (int, float)) else None
    if v is None:
        return "未标"
    v = float(v)
    if v <= -0.45: return "强负(≤-.45)"
    if v <= -0.15: return "偏负(-.45~-.15)"
    if v <   0.15: return "中立(-.15~+.15)"
    if v <   0.45: return "偏正(+.15~+.45)"
    return "强正(≥+.45)"


def gini(vals):
    """0 = 完全均匀，1 = 完全集中。用来判断分布有没有塌缩。"""
    v = sorted(float(x) for x in vals)
    n = len(v)
    if n == 0 or sum(v) == 0:
        return 0.0
    cum = sum((2 * i - n + 1) * x for i, x in enumerate(v, 1))
    return round(cum / (n * sum(v)), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--personas", required=True)
    ap.add_argument("--t0-init", default=None, help="t0 一次性初始化（E2/E3），有则一并校验")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    roster = json.loads(Path(a.roster).read_text(encoding="utf-8"))["members"]
    pool = {p["agent_id"]: p for p in json.loads(Path(a.personas).read_text(encoding="utf-8"))}
    t0 = {}
    if a.t0_init and Path(a.t0_init).exists():
        _t = json.loads(Path(a.t0_init).read_text(encoding="utf-8"))
        t0 = {x["agent_id"]: x for x in _t.get("agents", [])}
        for _m in roster:
            _x = t0.get(_m["agent_id"])
            if _x: _m["t0"] = _x

    rep, fails = {}, []

    # ---- 规则 3：多样性校验
    div = {}
    for dim, get in [
        # ★★ 组长 v3 §2.2-8：stance_bias 是【数字】不是"中立"字符串
        #    按它分档，才能真正看出正反方有没有切出来
        ("stance", lambda m: _stance_bin(m)),
        ("side", lambda m: (m.get("t0") or {}).get("side") or m.get("side") or "—"),
        ("role", lambda m: m.get("role_type")),
        ("channel", lambda m: m.get("primary_channel")),
        ("issue", lambda m: m.get("issue_leaf") or "—"),
        ("circle", lambda m: m.get("interest_circle") or "—"),
    ]:
        c = Counter(get(m) for m in roster)
        div[dim] = {"dist": dict(c.most_common(8)), "distinct": len(c),
                    "top_share": round(max(c.values()) / len(roster), 3)}
    # 连续量的集中度
    for dim, get in [
        ("arousal_0", lambda m: (m.get("t0") or {}).get("E3_arousal_0", m.get("arousal_baseline") or 0)),
        ("valence_0", lambda m: (m.get("t0") or {}).get("E2_valence_0", m.get("valence_baseline") or 0)),
        ("share_propensity", lambda m: m.get("share_propensity") or 0),
        ("involvement", lambda m: (m.get("t0") or {}).get("involvement", 0)),
    ]:
        vals = [get(m) for m in roster if get(m) is not None]
        if vals:
            import statistics as _st
            _signed = min(vals) < 0
            div[dim] = {"min": round(min(vals), 3), "max": round(max(vals), 3), "n": len(vals),
                        "std": round(_st.pstdev(vals), 3),
                        # ★ 基尼只对非负量有意义；有符号量（如效价）用标准差衡量离散度
                        **({"gini": gini(vals)} if not _signed else {"signed": True})}
    rep["rule3_diversity"] = div
    # 塌缩判据：某个维度 top_share > 0.60 或 gini > 0.55
    for k, v in div.items():
        if v.get("top_share", 0) > 0.60:
            fails.append("规则3 多样性：维度「%s」过于集中（top %.0f%%）" % (k, v["top_share"] * 100))
        if v.get("gini", 0) > 0.55:
            fails.append("规则3 多样性：维度「%s」基尼 %.2f 偏集中" % (k, v["gini"]))

    # ---- 规则 4：子型覆盖（KOL 垂类 = interest_circle）
    kol = [m for m in roster if m.get("role_type") == "KOL"]
    kc = Counter((m.get("interest_circle") or "未标注") for m in kol)
    rep["rule4_subtype"] = {
        "kol_count": len(kol), "distinct_circles": len(kc), "dist": dict(kc),
        "criterion": "KOL 垂类数 >= min(3, KOL 人数)；同一垂类不超过 KOL 的 60%",
    }
    if kol:
        need = min(3, len(kol))
        if len(kc) < need:
            fails.append("规则4 子型覆盖：KOL %d 人只有 %d 个垂类（需 >= %d）" % (len(kol), len(kc), need))
        if kc and max(kc.values()) / len(kol) > 0.60:
            fails.append("规则4 子型覆盖：KOL 垂类「%s」占 %.0f%%（需 <= 60%%）"
                         % (kc.most_common(1)[0][0], 100 * kc.most_common(1)[0][0][1] / len(kol)))

    # ---- 附带：规则 1/2/5 的状态声明
    rep["rule1_no_replacement"] = {"distinct_agents": len({m["agent_id"] for m in roster}),
                                   "total": len(roster), "ok": len({m["agent_id"] for m in roster}) == len(roster)}
    rep["rule5_seed"] = "由 run.py --seed 固定（本次 20261002）"
    rep["rule2_cooldown"] = "由 select_agents 的 cooling k=5 保证（跨事件，需事件序列才能验证）"

    rep["fails"] = fails
    rep["passed"] = not fails
    print("=" * 70)
    print("组队五条规则 · 校验报告")
    print("=" * 70)
    print(chr(10) + "规则 1 无放回：%s（%d 人，去重后 %d）" % (
        "✅" if rep["rule1_no_replacement"]["ok"] else "❌",
        rep["rule1_no_replacement"]["total"], rep["rule1_no_replacement"]["distinct_agents"]))
    print("规则 2 冷却期：%s" % rep["rule2_cooldown"])
    print("规则 5 固定种子：%s" % rep["rule5_seed"])
    print(chr(10) + "规则 3 多样性：")
    for k in ("role", "channel", "issue", "circle", "side", "stance"):
        d = div.get(k) or {}
        print("  %-9s %2d 类  最大占比 %.0f%%   %s" % (k, d.get("distinct", 0),
              d.get("top_share", 0) * 100, json.dumps(d.get("dist", {}), ensure_ascii=False)[:70]))
    for k in ("arousal_0", "valence_0", "share_propensity", "involvement"):
        d = div.get(k) or {}
        if "gini" in d:
            print("  %-9s 基尼 %.2f  区间 %.3f ~ %.3f" % (k, d["gini"], d["min"], d["max"]))
    print(chr(10) + "规则 4 子型覆盖（KOL 垂类）：")
    print("  KOL %d 人 ｜ 垂类 %d 个 ｜ %s" % (rep["rule4_subtype"]["kol_count"],
          rep["rule4_subtype"]["distinct_circles"], json.dumps(dict(kc), ensure_ascii=False)))
    print(chr(10) + ("★ 全部通过 ✅" if rep["passed"] else "★ 未通过 ❌"))
    for f in fails:
        print("   " + f)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
