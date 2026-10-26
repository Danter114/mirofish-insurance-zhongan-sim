# -*- coding: utf-8 -*-
"""一键演示编排：快照回放 -> build_event -> 舆情看板，全程脚本化、可复现。

用法：
  python 一键演示.py --card 事件参数卡.json --snapshot-dir 快照目录 --out 输出目录
  python 一键演示.py --card ... --snapshot-dir ... --out ... --no-llm   # 现场无 API 时规则兜底

参数卡与快照格式见 使用说明.md；快照目录需含 snapshot_meta.json 与原文 txt。
每一步打印耗时与产物路径，采集日志（采集日志.jsonl）作为可复现凭证落在输出目录。
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 交付改动（仅此一行）：原版指向采集侧机器上的 `D:\数据采集\yuqing_collector.py`，
#   那个文件没有随包交付。这里改成同目录的 `采集链.py`（同一个 --mode snapshot 契约，
#   回放规则实现在它里面）。要用真正的爬虫，换回 --collector <yuqing_collector.py 路径>。
COLLECTOR_DEFAULT = os.path.join(HERE, "采集链.py")


def sh(args, desc):
    t0 = time.time()
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    mark = "OK" if r.returncode == 0 else "FAIL"
    print("[%s] %s（%.1fs）" % (mark, desc, time.time() - t0))
    if r.returncode != 0:
        print((r.stdout or "")[-600:])
        print((r.stderr or "")[-600:])
        sys.exit(1)
    return r


def auto_pack(card, rows):
    """演示模式的自动文案包：图表数据全部来自 processed；叙事模块为规则归纳版。"""
    rows = sorted(rows, key=lambda r: r["publish_time"])
    n = len(rows)
    chans = {}
    sents = {}
    for r in rows:
        chans[r["channel"]] = chans.get(r["channel"], 0) + 1
        sents[r["sentiment"]] = sents.get(r["sentiment"], 0) + 1
    overview = (
        "%s「**%s**」事件（**%s**类）：%s ~ %s 两日内采集 **%d** 条公开舆情"
        "（%s），情感分布 负面 %d / 中性 %d / 正面 %d。"
        % (card.get("subject") or "", card.get("event_name") or "", card.get("event_type") or "",
           card["start_date"], card["end_date"], n,
           "、".join("%s %d" % (k, v) for k, v in sorted(chans.items(), key=lambda x: -x[1])),
           sents.get("负面", 0), sents.get("中性", 0), sents.get("正面", 0)))
    timeline = []
    for i, r in enumerate(rows):
        plat = r.get("source_platform") or r["channel"]
        if not r.get("is_origin", True) and r.get("author_name") and r["author_name"] != plat:
            plat += "（转载自 %s）" % r["author_name"]
        if r["channel"] == "监管通报":
            plat += "（监管通报渠道）"
        if not r.get("post_url"):
            plat += "（无链接）"
        badges = []
        if i == 0:
            badges.append("key:全网可核验最早")
        if r["channel"] == "监管通报":
            badges.append("key:权威转载")
        if r["channel"] == "微博" and r["role_type"] == "网民":
            badges.append("key:网民扩散")
        timeline.append({"post_id": r["post_id"], "plat": plat, "badges": badges, "warn": None})
    media = [r for r in rows if r["role_type"] in ("媒体", "监管")]
    opinion = {
        "groups": [{
            "name": "媒体报道汇总",
            "count": len(media),
            "desc": "演示模式自动分组（按角色），建议后续人工精修",
            "members": ["%s · %s · %s" % (r.get("source_platform") or r["channel"],
                                         r["publish_time"][5:16], (r.get("title") or "")[:24])
                        for r in media],
        }],
        "points": [],
        "note": "本看板为演示模式自动生成（叙事模块为规则归纳，非人工精修版本）。",
    }
    non_wb = [r for r in rows if r["channel"] != "微博"]
    wb = [r for r in rows if r["channel"] == "微博"]
    chain = {
        "sources": [{"label": card.get("subject") or "涉事主体", "subs": [card.get("event_name") or ""], "day0": False}],
        "origin": None,
        "spread": [],
        "weibo": [],
    }
    if non_wb:
        first = non_wb[0]
        chain["origin"] = {
            "label": "%s · %s" % (first.get("source_platform") or first["channel"], first["publish_time"][5:16]),
            "subs": ["全网可核验最早帖" if first is rows[0] else "首发传播", (first.get("title") or "")[:26]],
            "day0": first["timespan_days"] == 0,
        }
        for r in non_wb[1:9]:
            chain["spread"].append({
                "label": "%s · %s" % (r.get("source_platform") or r["channel"], r["publish_time"][5:16]),
                "sub": "Day%d" % r["timespan_days"] + (" · 权威转载" if r["channel"] == "监管通报" else ""),
                "day0": r["timespan_days"] == 0,
            })
    for i, r in enumerate(wb[:4]):
        chain["weibo"].append({
            "label": "%s · %s" % (r.get("author_name") or "微博用户", r["publish_time"][5:16]),
            "subs": [(r.get("title") or "")[:26]],
            "dashed": not r.get("post_url"),
            "from_spread": 0 if chain["spread"] else None,
        })
    return {"overview": overview, "timeline": timeline, "opinion": opinion, "chain": chain}


def main():
    ap = argparse.ArgumentParser(description="一键演示：快照回放 -> build_event -> 舆情看板")
    ap.add_argument("--card", required=True, help="事件参数卡 JSON")
    ap.add_argument("--snapshot-dir", required=True, help="快照目录（snapshot_meta.json + 原文 txt）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--collector", default=COLLECTOR_DEFAULT, help="yuqing_collector.py 路径")
    ap.add_argument("--no-llm", action="store_true", help="现场无 API 时用规则兜底标注")
    args = ap.parse_args()

    with open(args.card, encoding="utf-8") as f:
        card = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    evt = "EV%s" % card["event_id"]
    raw = os.path.join(args.out, "%s_raw.json" % evt)
    event = os.path.join(args.out, "%s_processed.json" % evt)
    pack = os.path.join(args.out, "%s_pack_auto.json" % evt)
    dash = os.path.join(args.out, "%s_舆情看板.html" % evt)

    print("== 第 1 步：快照回放采集（三层硬过滤 + 21 字段组装 + 结构化日志）==")
    sh([args.collector, "--mode", "snapshot", "--card", args.card,
        "--snapshot-dir", args.snapshot_dir, "--output-dir", args.out], "快照回放")

    print("== 第 2 步：结构化与标注（规则角色 + LLM 标注 + 口径仲裁）==")
    build_cmd = [os.path.join(HERE, "build_event.py"), "--raw", raw,
                 "--event-id", str(card["event_id"]), "--t0", card["start_date"],
                 "--topic-tag", card.get("topic_tag") or card.get("event_name") or "",
                 "--topic-list"] + list(card.get("topic_list") or [])
    build_cmd += ["--output", event]
    if args.no_llm:
        build_cmd.append("--no-llm")
    sh(build_cmd, "build_event")

    print("== 第 3 步：看板生成（数据层 + 演示模式文案包）==")
    with open(event, encoding="utf-8") as f:
        rows = json.load(f)
    with open(pack, "w", encoding="utf-8") as f:
        json.dump(auto_pack(card, rows), f, ensure_ascii=False, indent=1)
    sh([os.path.join(HERE, "00_程序与文档", "看板生成", "evt_dash_builder.py"),
        args.out, pack, dash], "看板生成")

    print()
    print("完成。产物：")
    print("  raw      : %s" % raw)
    print("  processed: %s" % event)
    print("  看板     : %s" % dash)
    print("  采集日志 : %s" % os.path.join(args.out, "采集日志.jsonl"))


if __name__ == "__main__":
    main()
