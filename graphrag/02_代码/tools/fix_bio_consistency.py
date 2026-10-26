#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_bio_consistency.py —— 只改 persona_bio 的一致性回改脚本（v1.0）

口径：与另一个组员同一套方法 —— 只拦【明显不可能】，允许清单外改动必须为 0。
与他的差别：他改的是字段（income_band / marital_status / interest_circle），
            本轮按用户要求【只改 persona_bio】，字段级矛盾登记不改。

用法：python fix_bio_consistency.py --roster D_Agent名单.json [--apply]
"""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path

# 允许改动的字段（其余一律断言为 0 改动）
ALLOW = {"persona_bio"}

FIX = [
    {"id": "抖音_网民_1072", "old": "考研复习到深夜是常事", "new": "刚上大学，还什么都不太熟",
     "why": "19 岁在读「考研复习到深夜」—— 19 岁最多大二，不可能考研"},
    {"id": "抖音_KOL_193", "old": "考研复习到深夜是常事", "new": "刚上大学，还什么都不太熟",
     "why": "18 岁在读「考研复习到深夜」—— 18 岁大一"},
    {"id": "抖音_KOL_163", "old": "在家办公久了，有点社恐", "new": "接单不稳定，好的时候一个月顶三个月",
     "why": "18 岁在读「在家办公久了」—— 18 岁谈不上「办公久了」"},
    {"id": "微博_网民_2379", "old": "远程办公两年了，作息有点乱", "new": "天天对着屏幕，颈椎不太好",
     "why": "20 岁在读「远程办公两年了」—— 18 岁起远程办公，明显不成立"},
    {"id": "小红书_客户_456", "old": "孩子上学去了，白天在家", "new": "暂时没上班，在家待着",
     "why": "婚育状态为「未婚」，bio 却写「孩子上学去了」"},
    {"id": "小红书_客户_208", "old": "一天三顿加接送，基本围着孩子转", "new": "年纪不大，暂时在家待着",
     "why": "20 岁「未婚」，bio 却写「基本围着孩子转」"},
    {"id": "抖音_客户_458", "old": "孩子上学去了，白天在家", "new": "暂时没上班，在家待着",
     "why": "婚育状态为「未婚」，bio 却写「孩子上学去了」"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--apply", action="store_true", help="不传则只预览")
    a = ap.parse_args()
    p = Path(a.roster)
    raw = p.read_text(encoding="utf-8")
    doc = json.loads(raw)
    agents = doc["agents"]
    before = json.loads(json.dumps(agents))

    hit = 0
    for f in FIX:
        x = next((y for y in agents if y["agent_id"] == f["id"]), None)
        if x is None:
            print("  [SKIP] 找不到 %s" % f["id"]); continue
        if f["old"] not in x["persona_bio"]:
            print("  [SKIP] %s bio 里没有「%s」" % (f["id"], f["old"])); continue
        x["persona_bio"] = x["persona_bio"].replace(f["old"], f["new"])
        hit += 1
        print("  [FIX ] %-20s %s -> %s" % (f["id"], f["old"], f["new"]))

    # ---- 断言：允许清单外的字段改动必须为 0
    bad = []
    for i in range(len(agents)):
        a0, a1 = before[i], agents[i]
        for k in set(a0) | set(a1):
            if k in ALLOW:
                continue
            if json.dumps(a0.get(k), sort_keys=True, ensure_ascii=False) != \
               json.dumps(a1.get(k), sort_keys=True, ensure_ascii=False):
                bad.append("%s.%s" % (a1.get("agent_id"), k))
    print("\n改 %d 人 ｜ 允许清单外改动 %d 处 %s" % (hit, len(bad), "OK" if not bad else "FAIL"))
    for b in bad[:20]:
        print("   X " + b)
    if bad:
        print("\n断言失败，不写盘。")
        return 1

    out = json.dumps(doc, ensure_ascii=False, indent=1)
    if json.dumps(json.loads(out), ensure_ascii=False, indent=1) != out:
        print("round-trip 不保真，不写盘。"); return 1
    print("round-trip 逐字符保真 OK")
    if not a.apply:
        print("（预览模式，未写盘；加 --apply 才写）")
        return 0
    bak = p.with_suffix(".biobak.json")
    if not bak.exists():
        shutil.copy2(p, bak); print("备份 -> %s" % bak.name)
    p.write_text("\n" + out, encoding="utf-8")
    print("已写回 %s" % p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())