#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
concern_topics_report.py —— 关切议题库报告 + 给画像组的建议词表

输出三样：
  ① 通用层：一个普通网民面对社会事件时的全部关切（8 大类 / 45 叶子）
  ② 事件层：本事件实际会激活哪些关切（三层同心圆 + 权重 + 依据）
  ③ 桥：本流程的保险议题 taxonomy 叶子 -> 通用关切叶子（两边词表真正对齐的地方）

    python tools/concern_topics_report.py --csv out/procurement/concern_topics_for_persona_team.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="导出给画像组的话题库建议")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(args.report, "w", encoding="utf-8")

    lib, tax = load_config("concern_topics_library"), load_config("taxonomy")
    leaves = {lf["leaf"] for v in tax["categories"].values() for lf in v["leaves"]}

    print("=" * 92)
    print("关切议题库：一个普通网民面对社会事件时的全部关切")
    print("=" * 92)
    print("来源：")
    for s in lib["sources"]:
        print("  · " + s)
    print("\n" + "-" * 92)
    print("① 通用层（%d 板块 / %d 大类 / %d 叶子）"
          % (len({l["board"] for l in lib["layers"]}), len(lib["layers"]),
             sum(len(l["leaves"]) for l in lib["layers"])))
    print("-" * 92)
    ba = lib.get("board_anchor") or {}
    if ba:
        print("  顶层锚（A 级证据）：%s" % ba.get("note", ""))
        print("    %-52s %8s %8s %8s" % ("来源", "娱乐", "垂直", "社会"))
        for d in ba.get("data", []):
            print("    %-52s %7.0f%% %7.0f%% %7.0f%%"
                  % (d["source"][:52], 100 * d["entertainment"], 100 * d["vertical"], 100 * d["social"]))
        m = ba.get("mean", {})
        print("    %-52s %7.0f%% %7.0f%% %7.0f%%"
              % ("【本规范采用：均值】", 100 * m.get("entertainment", 0),
                 100 * m.get("vertical", 0), 100 * m.get("social", 0)))
    for L in lib["layers"]:
        print("\n  【%s】板块=%s  W1 权重=%.3f" % (L["name"], L["board"], L["weight_general"]))
        print("     权重依据：%s" % L.get("note", ""))
        for lf in L["leaves"]:
            print("     %-16s  %s" % (lf["leaf"], "、".join(lf["examples"])))

    print("\n" + "-" * 92)
    print("② 事件层：本事件（EVT-001）会激活哪些关切")
    print("-" * 92)
    ea = lib["event_activation"]
    print("  事件：%s      事件类型：%s" % (ea["event_title"], ea["event_type"]))
    print("  推导：%s" % ea["derivation"])
    for ring in ("core", "identity", "public"):
        r = ea["rings"][ring]
        print("\n  ── %s（%s）" % (r["name"], r["note"]))
        print("     建议角色：%s" % r["role_hint"])
        for it in r["items"]:
            print("     %-16s [%s]  权重 %.3f" % (it["leaf"], it["layer"], it["heat"]))
            print("        为什么：%s" % it["why"])
            print("        依据　：%s" % it["evidence"])
    s = ea["activation_summary"]
    print("\n  配比建议：核心层 %.0f%% / 认同层 %.0f%% / 公众层 %.0f%%  ->  500 人里约 %d / %d / %d"
          % (100 * s["core_ratio_hint"], 100 * s["identity_ratio_hint"],
             100 * s["public_ratio_hint"], round(500 * s["core_ratio_hint"]),
             round(500 * s["identity_ratio_hint"]), round(500 * s["public_ratio_hint"])))
    print("  ⚠ " + ea["caveat"])

    print("\n" + "-" * 92)
    print("③ 桥：保险议题 taxonomy -> 通用关切（两边词表真正对齐的地方）")
    print("-" * 92)
    br = lib["taxonomy_bridge"]["map"]
    covered = set()
    for g, arr in sorted(br.items(), key=lambda kv: -len(kv[1])):
        covered |= set(arr)
        print("  %-16s <- %d 个保险议题：%s" % (g, len(arr), "、".join(arr)))
    print("\n  覆盖：%d / %d 个保险议题叶子被映射（%.0f%%）"
          % (len(covered & leaves), len(leaves), 100.0 * len(covered & leaves) / len(leaves)))
    print("  落点：保险舆情的 %d 个议题只落在**通用层的 %d 个关切**上 —— 这就是画像库该覆盖的范围"
          % (len(covered & leaves), len(br)))
    w2 = ((lib.get("weight_schemes") or {}).get("W2_insurance") or {}).get("weights") or {}
    if w2:
        print("\n  ★ W2 · 保险舆情份额（来自 32 场真实事件的 79 个议题实例，A 级证据）：")
        for k, v in sorted(w2.items(), key=lambda kv: -kv[1]):
            print("     %-16s %5.1f%%" % (k, 100 * v))

    print("\n" + "-" * 92)
    print("④ 覆盖率核算：按 W1 造的 2000 人库，每个话题能覆盖多少人")
    print("-" * 92)
    cm = lib.get("coverage_math") or {}
    pr = cm.get("params") or {}
    if pr:
        N, K, NEED = pr["N"], pr["K"], pr["NEED"]
        print("  公式：覆盖人数 ≈ N x (1-(1-w)^K)   N=%d K=%d 单场需要 NEED=%d" % (N, K, NEED))
        print("  最小可支撑权重 w_min = %.4f（%.2f%%）" % (cm.get("w_min", 0), 100 * cm.get("w_min", 0)))
        ok = bad = 0
        for x in lib.get("leaves_flat", []):
            if x["w1"] >= cm.get("w_min", 0): ok += 1
            else: bad += 1
        print("  能支撑一场戏的话题：%d / %d；撑不起的 %d 个" % (ok, ok + bad, bad))
        print("  ★ 结论：%s" % cm.get("finding", ""))
        for k, v in (cm.get("measured") or {}).items():
            print("    实测 %-4s -> 选出 %s / 配额误差 %s / 兜底 %s / 真关注 %s"
                  % (k, v["selected"], v["quota_error"], v["fallback"], v["true_focus"]))

    if args.csv:
        p = Path(args.csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["类型", "大类", "关切叶子", "举例/依据", "大类权重", "本事件权重", "本事件层级"])
            act = {}
            for ring, r in ea["rings"].items():
                for it in r["items"]:
                    act[it["leaf"]] = (it["heat"], ring)
            for L in lib["layers"]:
                for lf in L["leaves"]:
                    h, ring = act.get(lf["leaf"], ("", ""))
                    w.writerow(["通用关切", L["name"], lf["leaf"], "、".join(lf["examples"]),
                                L["weight_general"], h, ring])
            for g, arr in sorted(br.items()):
                w.writerow(["保险议题映射", "（桥）", g, "、".join(arr), "", "", ""])
        print("\n给画像组的话题库建议 -> %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
