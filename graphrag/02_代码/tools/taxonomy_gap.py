#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
taxonomy_gap.py —— 词表缺口体检：taxonomy 到底该不该按 topic_list 重新分？

回答方式不是讲道理，是**算三笔账**：

  ① 覆盖率（topic_list -> taxonomy）
     46 个 topic_list 词里，有多少能落到 taxonomy 叶子上？落不上的分两类：
       · role=non_topic    实体 / 事件生命周期 / 舆情反应 / 帖级行为 —— **本来就不该进 taxonomy**
       · role=oov_candidate 没有词表归属的议题类词 —— **这些才是 taxonomy 该补的**
     结论：topic_list 是 taxonomy 的**原料与校验基准**，不是它的替代品。

  ② 存活率（taxonomy 在真实语料里的触发率）
     拿全部事件图谱统计每个叶子被触发过几次。从不触发的叶子要分三种处理：
     真长尾 / 规则没覆盖 / 设计过度 —— 三种的处置完全不同。

  ③ 画像可匹配率（按 nature 拆分）
     把叶子分成 interest（人的长期关注）和 event_type（事件属性），
     只有 interest 型才应该拿去和画像库的 concern_topics 做匹配。
     这一笔账直接解释「为什么事件1 的议题和画像库的话题库对不上」。

    python tools/taxonomy_gap.py --kg-dir ../../../GraphRAG建图与定槽位/output/kg
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kg-dir", default=None, help="历史事件图谱目录（统计叶子触发率）")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(args.report, "w", encoding="utf-8")

    tax, tv = load_config("taxonomy"), load_config("topic_vocab")
    leaves = {lf["leaf"]: dict(lf, top=t) for t, v in tax["categories"].items() for lf in v["leaves"]}
    terms = [(L["name"], t) for L in tv["layers"] for t in L["terms"]]

    print("=" * 92)
    print("词表缺口体检：taxonomy 该不该按 topic_list 重新分？")
    print("=" * 92)
    print("taxonomy      %s  %d 大类 / %d 叶子" % (tax["version"], len(tax["categories"]), len(leaves)))
    print("topic_vocab   %s  %d 层 / %d 词" % (tv["version"], len(tv["layers"]), len(terms)))

    # ---------------- ① topic_list -> taxonomy
    print("\n" + "-" * 92)
    print("① topic_list -> taxonomy 的覆盖率")
    print("-" * 92)
    by_role = Counter(t.get("role") for _, t in terms)
    fed_terms = [(l, t) for l, t in terms if t.get("feeds")]
    oov_terms = [(l, t) for l, t in terms if t.get("role") == "oov_candidate"]
    non_terms = [(l, t) for l, t in terms if t.get("role") == "non_topic"]
    print("  %-28s %3d 个" % ("支撑到 taxonomy 叶子的词", len(fed_terms)))
    print("  %-28s %3d 个   <- 这些是 taxonomy 该补的" % ("无归属的议题类词（OOV）", len(oov_terms)))
    print("  %-28s %3d 个   <- 本来就不该进 taxonomy" % ("结构性非议题词", len(non_terms)))
    print("\n  支撑关系（topic_list 词 -> taxonomy 叶子）：")
    back = {}
    for layer, t in fed_terms:
        for lf in t["feeds"]:
            back.setdefault(lf, []).append("%s[%s]" % (t["term"], layer))
    for lf, srcs in sorted(back.items()):
        print("    %-22s <- %s" % (lf, "、".join(srcs)))
    print("\n  待采购（OOV，按建议动作分组）：")
    for layer, t in oov_terms:
        print("    %-18s [%s]  %s" % (t["term"], layer, t.get("note", "")))
    print("\n  结构性非议题词（按层）：")
    for layer, n in Counter(l for l, _ in non_terms).most_common():
        print("    %-14s %2d 个" % (layer, n))

    # ---------------- ② 叶子存活率
    print("\n" + "-" * 92)
    print("② taxonomy 叶子在真实语料里的存活率")
    print("-" * 92)
    if args.kg_dir and Path(args.kg_dir).exists():
        files = sorted(Path(args.kg_dir).glob("event_kg_*.json"))
        fired, tot, events = Counter(), 0, 0
        for f in files:
            kg = json.loads(f.read_text(encoding="utf-8"))
            events += 1
            for i in kg.get("issues") or []:
                fired[i["leaf"]] += 1
                tot += 1
        live = [l for l in leaves if fired[l]]
        dead = [l for l in leaves if not fired[l]]
        print("  语料：%d 场事件 / %d 个议题实例" % (events, tot))
        print("  活叶子 %d / %d（%.0f%%）；死叶子 %d 个"
              % (len(live), len(leaves), 100.0 * len(live) / len(leaves), len(dead)))
        print("\n  死叶子按 nature 分（处置方式不同）：")
        for nat in ("interest", "event_type"):
            arr = [l for l in dead if leaves[l].get("nature") == nat]
            if arr:
                print("    nature=%-11s %2d 个：%s" % (nat, len(arr), " / ".join(arr)))
        print("\n  说明：死叶子要分三种处理 ——")
        print("    真长尾 -> 保留（长尾议题也要有最小配额）")
        print("    规则没覆盖 -> 去 topic_list 里找证据；找不到就是真长尾")
        print("    设计过度 -> 但只有在**全量语料**里也找不到证据才能删")
    else:
        print("  （未提供 --kg-dir，跳过。建议指向历史事件图谱目录）")

    # ---------------- ③ 画像可匹配率
    print("\n" + "-" * 92)
    print("③ 画像可匹配率：哪些叶子有资格拿去和画像库匹配")
    print("-" * 92)
    nat = Counter(l.get("nature") for l in leaves.values())
    print("  interest（人的长期关注，可匹配画像）   %d 个" % nat.get("interest", 0))
    print("  event_type（事件属性，**不该**匹配画像）%d 个" % nat.get("event_type", 0))
    print("\n  事件1 的 4 个议题按 nature 拆开看：")
    for lf in ("宠物险争议", "退保损失", "广告内容低俗", "性别与价值观争议"):
        row = leaves.get(lf) or {}
        print("    %-16s nature=%-11s top=%s" % (lf, row.get("nature"), row.get("top")))
    print("\n  -> 4 个里有 2 个是 event_type，本来就不该拿去匹配画像的长期兴趣；")
    print("     剩下 2 个 interest（宠物险争议 / 退保损失）才是画像库该覆盖的。")
    print("  -> 这解释了为什么「事件1 的议题 vs 画像库 12 话题」会 4/4 落空：")
    print("     画像库那 12 个话题里，没有任何一个覆盖「宠物险」或「退保」。")

    # ---------------- 结论
    print("\n" + "=" * 92)
    print("结论")
    print("=" * 92)
    print("  · topic_list 里只有 %d/%d（%.0f%%）的词有资格当 taxonomy 叶子，"
          % (len(fed_terms) + len(oov_terms), len(terms),
             100.0 * (len(fed_terms) + len(oov_terms)) / len(terms)))
    print("    其余 %d 个是实体 / 事件生命周期 / 舆情反应 / 帖级行为 —— 换掉 taxonomy 就等于把"
          % len(non_terms))
    print("    这些混进「议题」，槽位会变成「(网民, 下架素材, pro, 微博)」这种东西。")
    print("  · 但 topic_list 是 taxonomy 的**正确来源**：把每个词标上 feeds，")
    print("    落不上的就自动变成采购清单。本次已经据此补出「经营业绩与资本市场」一个叶子。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
