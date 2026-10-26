#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
topic_match.py —— 议题匹配：三级（精确 / 同大类 / 模糊）+ 模糊检索放宽

背景：画像库的词表和我们的 taxonomy **拟合不完全**（12 个话题 vs 47 个叶子，
实测只有 5 个能精确对上）。硬性要求"必须在 taxonomy 里"会让匹配率归零，
所以 MVP 采用三级匹配，第三级是**模糊检索放宽**：

    ① 精确   slot.leaf 就在这个人的 concern_leaves 里            -> 1.00
    ② 同大类 这个人的某个关注点与 slot.leaf 同属一个 taxonomy 大类 -> 0.60
    ③ 模糊   映射表 weak + 字符 bigram 相似度                     -> 0.15 ~ 0.45
    ④ 无                                                          -> 0.00

模糊层不是"猜"，它有据可查：命中的是哪条映射、哪个原词、相似度多少，
全部写进候选记录的 `match_level` / `match_evidence`，输出里可以逐条审计。

★ 与"概率抽样"的分工：这一层只负责**把候选池放宽 + 给软权重**；
   最终抽谁由 select_agents 的 Gumbel + MCMF 按 softmax(score/T) 决定，种子固定即结果固定。
"""
from __future__ import annotations

import re

LEVELS = ("exact", "same_top", "fuzzy_alias", "fuzzy_char", "none")


def build_alias_index(alias_cfg, leaves):
    """把 topic_alias.json 展开成 {原词: set(叶子)} 两张索引。"""
    def _idx(key):
        out = {}
        for raw, arr in (alias_cfg.get(key) or {}).items():
            out[raw] = {x for x in arr if x in leaves}
        return out
    strong = _idx("strong")
    # ★ 通用关切层 -> 保险议题的桥（config/concern_topics_library.json :: taxonomy_bridge），
    #   按 strong 处理：一个关切覆盖多个议题是正常的，语义上就是等价命中。
    for raw, arr in (alias_cfg.get("general_bridge") or {}).items():
        strong.setdefault(raw, set()).update(x for x in arr if x in leaves)
    return {"strong": strong, "weak": _idx("weak")}


def _bigrams(s):
    s = re.sub(r"[\s\-_/]", "", str(s or ""))
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def char_sim(a, b):
    """字符 bigram 的 Jaccard。只做兜底 —— 单靠它「理赔速度」vs「理赔拖延」只有 0.2。"""
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def match(leaf, top, persona, index, sel_cfg, leaf2top=None, leaf2cat9=None,
          leaf2concern=None):
    """返回 (score, level, evidence)。persona 需要 concern_leaves（归一后）与 concern_raw（原词）。"""
    tm = (sel_cfg or {}).get("topic_match") or {}
    s_exact = float(tm.get("exact", 1.0))
    s_top = float(tm.get("same_top", 0.6))
    s_alias = float(tm.get("fuzzy_alias", 0.45))
    w_char = float(tm.get("fuzzy_char_weight", 0.6))
    floor = float(tm.get("fuzzy_floor", 0.15))

    # ★ 具名实体槽位不对应任何议题 —— 必须在所有议题匹配分支之前短路，
    #   否则会掉进"匹配不上"的判定里，被误报成 none（也就没有 evidence）
    if leaf is None:
        return 0.5, "named_slot", ["具名实体槽位：1:1 直绑事件发帖人，不按议题过滤"]

    # ★ 大类权重模式：画像库只给 9 个大类的关注度权重（M 方案）
    cw = persona.get("cat_weights")
    if cw and leaf2cat9:
        c9 = leaf2cat9.get(leaf)
        w = float(cw.get(c9, 0.0)) if c9 else 0.0
        if w <= 0:
            return 0.0, "none", []
        mx = max(cw.values()) or 1.0
        s = round(w / mx, 4)
        lvl = "cat_major" if s >= 0.66 else ("cat_minor" if s >= 0.33 else "cat_weak")
        ev = ["大类「%s」关注度 %.2f / 最高 %.2f" % (c9, w, mx)]
        # ★ 二级结构：话题细项命中 -> 提分（恢复同大类内不同议题之间的区分度）
        _cn = (leaf2concern or {}).get(leaf)          # 保险议题 -> 通用关切
        if leaf in (persona.get("topic_hints") or []) or \
           (_cn is not None and _cn in (persona.get("topic_hints") or [])):
            s = round(min(1.0, s + 0.35), 4)
            lvl = "hint_hit"
            ev.append("话题细项命中 %s" % (_cn or leaf))
        return s, lvl, ev

    lv_leaves = persona.get("concern_leaves") or []
    raw = persona.get("concern_raw") or []
    if leaf is None:
        return 0.5, "named_slot", []          # 具名主体槽位不按议题过滤
    if leaf in lv_leaves:
        return s_exact, "exact", ["concern_topics 直接命中 %s" % leaf]

    # ② 同大类
    if top and leaf2top:
        tops = {leaf2top.get(t) for t in lv_leaves}
        if top in tops:
            hit = next((t for t in lv_leaves if leaf2top.get(t) == top), None)
            return s_top, "same_top", ["同属大类「%s」：%s" % (top, hit)]

    # ③ 模糊层：映射表 weak 优先，字符相似度兜底
    best, level, ev = 0.0, "none", []
    for r in raw:
        if leaf in (index["weak"].get(r) or set()):
            if s_alias > best:
                best, level, ev = s_alias, "fuzzy_alias", ["映射表 weak：%s ~ %s" % (r, leaf)]
        if leaf in (index["strong"].get(r) or set()):     # 强映射等价于精确
            return s_exact, "exact", ["映射表 strong：%s -> %s" % (r, leaf)]
        for t in lv_leaves:
            s = char_sim(leaf, t) * w_char
            if s > best:
                best, level, ev = s, "fuzzy_char", ["字面相似：%s ~ %s（%.2f）" % (leaf, t, s)]
    if best >= floor:
        return round(best, 4), level, ev
    return 0.0, "none", []


def self_test(tax, alias_cfg, sel_cfg):
    leaves = {lf["leaf"]: t for t, v in tax["categories"].items() for lf in v["leaves"]}
    idx = build_alias_index(alias_cfg, set(leaves))
    cases = [
        ({"concern_leaves": ["拒赔"], "concern_raw": ["拒赔"]}, "拒赔", "理赔服务", "exact"),
        ({"concern_leaves": ["拒赔"], "concern_raw": ["拒赔"]}, "理赔拖延", "理赔服务", "same_top"),
        ({"concern_leaves": ["理赔拖延"], "concern_raw": ["理赔速度"]}, "理赔拖延", "理赔服务", "exact"),
        ({"concern_leaves": [], "concern_raw": ["消费者权益"]}, "销售误导", "销售与投保", "fuzzy_alias"),
        ({"concern_leaves": [], "concern_raw": ["食品安全"]}, "拒赔", "理赔服务", "none"),
    ]
    ok = 0
    for p, leaf, top, want in cases:
        s, lv, ev = match(leaf, top, p, idx, sel_cfg, leaves)
        good = lv == want
        ok += good
        print("   [%s] %-22s x %-10s -> %-12s %.3f  %s"
              % ("OK" if good else "FAIL", str(p["concern_raw"]), leaf, lv, s, (ev or [""])[0][:34]))
    print("   匹配器自测：%d/%d 通过" % (ok, len(cases)))
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config
    raise SystemExit(0 if self_test(load_config("taxonomy"), load_config("topic_alias"),
                                    load_config("selection")) else 1)
