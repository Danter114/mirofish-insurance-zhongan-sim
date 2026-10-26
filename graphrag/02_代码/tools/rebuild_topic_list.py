#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_topic_list.py —— 按每条帖子**自己的**文本证据，重建它的 topic_list。

背景：采集侧交付的 topic_list 是「一份写死的话题清单复制到所有帖子上」，
29 条里 28 条逐字相同。那个清单描述的是**事件**，不是**这条帖子**，
下游没法拿它做话题相关性判断（每条帖子的相关性分数会一模一样）。

做法（可复现、零 LLM）：
  1. 词表 config/topic_vocab.json 定义「话题要素」并按层分组：
     主体 / 产品 / 事件类型 / 涉事内容 / 责任链路 / 回应处置 / 舆情反应 / 法律合规 / 传播动作 / 经营与背景
  2. 对每条帖子，扫描它自己的 title + content + topic_tag + extract_entity；
     **只有在这条帖子里找到证据的词，才会进这条帖子的 topic_list**。
  3. 顺序按层，层内按词表定义顺序 —— 同样输入必得同样输出。
  4. 顺带产出 topic_leaves / topic_oov：用建图侧同一套 taxonomy + rules 做议题归一，
     让「这条帖子贡献了哪个议题」逐帖可见（字段规范里 topic_leaves 是 P0）。

用法：
    python tools/rebuild_topic_list.py --src ../event1_pilot_last_version.json \
        --out ../event1_pilot_last_version.json \
        --review ../event1_topic_list_review.md --csv ../event1_topic_list_review.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config          # noqa: E402
from extractor import RuleExtractor     # noqa: E402


def load_vocab(cfg):
    layers = []
    for L in cfg["layers"]:
        terms = [(t["term"], re.compile(t["evidence"], re.I)) for t in L["terms"]]
        layers.append((L["name"], terms))
    return layers


def scan(rec, layers, fields):
    """返回 (topic_list, 命中的分层明细)。只有在本帖文本里找到证据的词才会入选。"""
    text = "\n".join(str(rec.get(f) or "") for f in fields)
    flat, detail = [], []
    for name, terms in layers:
        hit = [t for t, rx in terms if rx.search(text)]
        if hit:
            detail.append({"layer": name, "terms": hit})
            flat.extend(hit)
    return flat, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="待修的 JSON（数组，每条一个帖子）")
    ap.add_argument("--out", default=None, help="输出 JSON，默认原地覆盖")
    ap.add_argument("--vocab", default=None, help="话题词表，默认 config/topic_vocab.json")
    ap.add_argument("--review", default=None, help="人读的复核报告（markdown）")
    ap.add_argument("--csv", default=None, help="机读的复核表")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out) if args.out else src
    recs = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(recs, dict):
        for k in ("posts", "docs", "data", "records"):
            if k in recs:
                recs = recs[k]
                break

    vcfg = (json.loads(Path(args.vocab).read_text(encoding="utf-8")) if args.vocab
            else load_config("topic_vocab"))
    tax, rules = load_config("taxonomy"), load_config("rules")
    ex = RuleExtractor(rules, tax)
    leaves = {lf["leaf"] for v in tax["categories"].values() for lf in v["leaves"]}
    layers, fields = load_vocab(vcfg), vcfg["scan_fields"]

    if out == src and not args.no_backup:
        bak = src.with_suffix(".bak.json")
        bak.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
        print("已备份原文件 -> %s" % bak)

    before = Counter(json.dumps(r.get("topic_list"), ensure_ascii=False) for r in recs)
    rows = []
    for r in recs:
        tl, detail = scan(r, layers, fields)
        # 议题归一用与建图**同一套**词表与规则，保证 topic_leaves 和图谱的 issues 对得上
        text = " ".join([str(r.get("content") or ""), str(r.get("title") or ""),
                         " ".join(str(x) for x in (r.get("extract_entity") or "").split("；"))])
        got = ex.extract(text, {"entities": []})
        tls = sorted({i["leaf"] for i in got["issues"] if i["leaf"] in leaves})
        oov = sorted(set(got["oov_issues"]))
        # 原地替换，保持字段顺序（只动 topic_list，并在其后补 topic_leaves / topic_oov）
        new = {}
        for k, v in r.items():
            if k == "topic_list":
                new["topic_list"] = tl
                new["topic_leaves"] = tls
                new["topic_oov"] = oov
            elif k in ("topic_leaves", "topic_oov"):
                continue                      # 重跑时不要重复插入
            else:
                new[k] = v
        if "topic_list" not in r:             # 原本没有这个字段的帖子也要有
            new["topic_list"], new["topic_leaves"], new["topic_oov"] = tl, tls, oov
        r.clear()
        r.update(new)
        rows.append({"post_id": r.get("post_id"), "publish_time": r.get("publish_time"),
                     "channel": r.get("source_channel"), "role": r.get("author_label"),
                     "author": r.get("author_name"), "title": r.get("title"),
                     "n_topics": len(tl), "topic_list": "|".join(tl),
                     "topic_layers": " ".join("%s:%s" % (d["layer"], "/".join(d["terms"]))
                                              for d in detail),
                     "topic_leaves": "|".join(tls), "topic_oov": "|".join(oov)})

    out.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    after = Counter(json.dumps(r.get("topic_list"), ensure_ascii=False) for r in recs)

    print("\n帖子 %d 条 -> %s" % (len(recs), out))
    print("改之前：%d 种不同的 topic_list（最常见的一种覆盖 %d 条 = %.0f%%）"
          % (len(before), before.most_common(1)[0][1], 100.0 * before.most_common(1)[0][1] / len(recs)))
    print("改之后：%d 种不同的 topic_list（最常见的一种覆盖 %d 条 = %.0f%%）"
          % (len(after), after.most_common(1)[0][1], 100.0 * after.most_common(1)[0][1] / len(recs)))
    lens = [len(r["topic_list"]) for r in recs]
    print("每条词数：min %d / 中位 %d / max %d / 合计 %d"
          % (min(lens), sorted(lens)[len(lens) // 2], max(lens), sum(lens)))
    tl_cover = Counter(t for r in recs for t in r["topic_list"])
    lv_cover = Counter(t for r in recs for t in r["topic_leaves"])
    print("词表覆盖：%d / %d 个词被用到" % (len(tl_cover), sum(len(t) for _, t in layers)))
    print("议题覆盖：%s" % dict(lv_cover))
    print("\n最『薄』/最『厚』的帖子：")
    for r in sorted(rows, key=lambda x: x["n_topics"])[:3]:
        print("  %-14s %2d 词  %s" % (r["post_id"], r["n_topics"], r["topic_list"]))
    for r in sorted(rows, key=lambda x: -x["n_topics"])[:3]:
        print("  %-14s %2d 词  %s" % (r["post_id"], r["n_topics"], r["topic_list"]))

    if args.csv:
        p = Path(args.csv)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("\n机读复核表 -> %s" % p)

    if args.review:
        p = Path(args.review)
        p.parent.mkdir(parents=True, exist_ok=True)
        L = ["# topic_list 重建复核", "",
             "> 词表：config/topic_vocab.json ｜ 脚本：tools/rebuild_topic_list.py",
             "> 铁律：一个词只有在**这条帖子自己的文本**里找到证据才会入选，因此各帖天然不同。", "",
             "## 一、修之前 vs 修之后", "",
             "| | 不同的 topic_list 种数 | 最常见一种覆盖 | 每条词数 |",
             "| --- | ---: | ---: | --- |",
             "| 修之前 | %d | %d 条（%.0f%%） | 固定 10 |"
             % (len(before), before.most_common(1)[0][1], 100.0 * before.most_common(1)[0][1] / len(recs)),
             "| **修之后** | **%d** | **%d 条（%.0f%%）** | **min %d / 中位 %d / max %d** |"
             % (len(after), after.most_common(1)[0][1], 100.0 * after.most_common(1)[0][1] / len(recs),
                min(lens), sorted(lens)[len(lens) // 2], max(lens)), "",
             "## 二、逐帖 topic_list", "",
             "| post_id | 时间 | 渠道 | 标题 | 词数 | topic_list | 议题叶子 |",
             "| --- | --- | --- | --- | ---: | --- | --- |"]
        for r in sorted(rows, key=lambda x: (str(x["publish_time"]), x["post_id"])):
            L.append("| %s | %s | %s | %s | %d | %s | %s |"
                     % (r["post_id"], r["publish_time"], r["channel"],
                        (r["title"] or "").replace("|", "/")[:28], r["n_topics"],
                        r["topic_list"].replace("|", " · ") or "（无）",
                        r["topic_leaves"].replace("|", " · ") or "—"))
        L += ["", "## 三、词频（哪些话题要素在这个事件里被反复提到）", "",
              "| 词 | 出现的帖子数 |", "| --- | ---: |"]
        for t, c in tl_cover.most_common():
            L.append("| %s | %d |" % (t, c))
        L += ["", "## 四、话题要素分层明细", ""]
        for name, terms in layers:
            used = [t for t, _ in terms if tl_cover.get(t)]
            if used:
                L.append("- **%s**：%s" % (name, " · ".join(used)))
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        print("人读复核报告 -> %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
