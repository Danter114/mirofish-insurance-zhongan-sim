#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
persona_io.py —— 画像库加载 + 契约校验（v2.0，对齐组长需求 v2）

三件事：
  1. load_pool()      读入并做字段级规整（缺失键补默认值，不改原始文件）
  2. validate_pool()  按 config/persona_contract.json 出体检报告，交给画像组修
  3. describe_pool()  出画像库的分布画像，判断够不够支撑单场 500 人

★ v2.0 的两处硬口径（组长需求 v2 / 2.4 与 四、共同口径）：
    · 角色 6 类：KOC 已删，代理维权已排除
    · 一人一渠道：primary_channel 单值，(角色 x 渠道) 必须落在 20 个有效组合内
      —— 跨渠道传播靠 cross_channel_propensity + 渠道折扣矩阵，不靠多账号
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROLES = ["网民", "客户", "KOL", "媒体", "专业背书方", "监管"]
CHANNELS = ["微博", "抖音", "小红书", "黑猫投诉", "新闻门户", "监管通报"]
STANCES = ["倾向维权", "中立", "倾向企业"]

# 20 个有效组合（6 角色 x 6 渠道，非笛卡尔积 36）。
# 两条结构约束：黑猫投诉只有客户可达；监管通报是单向渠道（只有监管能发）。
# KOC 整行 3 格随角色删除：23 - 3 = 20。
ROLE_CHANNEL = {
    "网民": ["微博", "抖音", "小红书"],
    "客户": ["微博", "抖音", "小红书", "黑猫投诉"],
    "KOL": ["微博", "抖音", "小红书", "新闻门户"],
    "媒体": ["微博", "抖音", "新闻门户"],
    "专业背书方": ["微博", "抖音", "小红书", "新闻门户"],
    "监管": ["新闻门户", "监管通报"],
}
VALID_COMBOS = {(r, c) for r, cs in ROLE_CHANNEL.items() for c in cs}


def _combo_from_cfg(contract=None, slots_cfg=None):
    """有效组合优先从 config/slots.json :: role_channel 读；读不到再用本文件的兜底表。"""
    if slots_cfg and slots_cfg.get("role_channel"):
        return {(r, c) for r, cs in slots_cfg["role_channel"].items() for c in cs}
    return set(VALID_COMBOS)


_T2C_CACHE = {}


def _topic_to_cat9():
    """47 通用话题 -> 9 大类 的映射（config/topic_to_cat9.json）。
    用于把「通用话题权重」聚合成「9 大类权重」，让 R2 能算。"""
    import json as _json
    from pathlib import Path as _P
    if not _T2C_CACHE:
        try:
            _T2C_CACHE.update(load_config("topic_to_cat9"))
        except Exception:
            pass
    return _T2C_CACHE


def load_pool(path, taxonomy, contract, slots_cfg=None, alias_cfg=None) -> list:
    """读画像库。返回规整后的 list[dict]，不修改磁盘上的原始文件。

    ★ v2.1：concern_topics 保留两份 ——
        concern_raw     画像库给的**原词**（可能是他们的话题库，如「理赔速度」）
        concern_leaves  经 config/topic_alias.json 归一后的 **taxonomy 叶子**
      选人时用三级匹配（精确 / 同大类 / 模糊），模糊层就是拿 concern_raw 去检索的。
    """
    leaves_all = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
    strong = dict((alias_cfg or {}).get("strong") or {})
    weak = (alias_cfg or {}).get("weak") or {}
    # ★ general_bridge（通用关切 -> 保险议题）与 strong 同等对待：
    #   「关心宠物与养宠」在语义上就等价于「会被宠物险争议激活」。
    for k, v in ((alias_cfg or {}).get("general_bridge") or {}).items():
        strong[k] = list(strong.get(k) or []) + list(v)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw["personas"] if isinstance(raw, dict) and "personas" in raw else raw
    pool = []
    for i, p in enumerate(items):
        a = dict(p)
        a.setdefault("agent_id", "UNKNOWN_%05d" % i)
        # ★ 一人一渠道
        pc = a.get("primary_channel")
        if pc not in CHANNELS:
            cp = a.get("channel_presence") or {}
            pc = max(CHANNELS, key=lambda c: (float(cp.get(c, 0) or 0), c)) if cp else ""
        a["primary_channel"] = pc
        _orig = a.get("concern_topics") or {}
        if isinstance(_orig, dict):                     # 画像库的 concern_topics 是 dict 权重
            raw = [k for k, _v in sorted(_orig.items(), key=lambda kv: -kv[1]) if k != "其他"]
        else:
            raw = list(_orig)
            _orig = {}
        raw = [str(x) for x in raw]
        norm = []
        for t in raw:
            if t in leaves_all:
                norm.append(t)
            for lf in (strong.get(t) or []):            # 强映射：等价于精确
                if lf in leaves_all and lf not in norm:
                    norm.append(lf)
        _T2C = _topic_to_cat9()
        CAT9 = {"民生保障","公共安全与社会治理","价值观与社会议题","国际与政治",
                "情感与生活方式","经济与商业","科技与信息","体育与游戏","文娱与明星"}
        _k = [x for x in raw if x != "其他"]
        if _k and all(x in CAT9 for x in _k):
            # ★ 模式 A：画像库直接给 9 大类的关注度权重
            a["cat_weights"] = {x: float(_orig[x]) for x in _k}
        elif _orig and _T2C:
            # ★ 模式 B（v2.1 新增）：画像库给的是【通用话题】权重（如 47 话题库），
            #   按 config/topic_to_cat9.json 聚合到 9 大类。
            #   没有这一步 R2（帖文相关度，权重 0.3）会恒为 0，
            #   相关度天花板直接从 0.92 掉到 0.59 —— 实测踩过这个坑。
            _cw = {}
            for _t, _w in _orig.items():
                if _t == "其他":
                    continue
                _c = _T2C.get(_t)
                if _c:
                    _cw[_c] = round(_cw.get(_c, 0.0) + float(_w), 4)
            if _cw:
                a["cat_weights"] = _cw
        if a.get("topic_hints"):
            a["topic_hints"] = [str(x) for x in a["topic_hints"]]
        a["concern_raw"] = raw
        a["concern_leaves"] = norm
        a["concern_topics"] = norm or raw               # 兼容下游：优先给归一后的叶子
        a.setdefault("stance_bias", "中立")
        a.setdefault("activity_level", 0.5)
        a.setdefault("claim_experience", False)
        a.setdefault("subtype", "")
        a.setdefault("cross_channel_propensity", 0.1)
        a.setdefault("persona_bio", "")
        a.setdefault("follow_scope", [])
        pool.append(a)
    # 影响力分位 + 可比的 influence_weight（角色内归一化，避免 KOL 粉丝量碾压）
    for role in ROLES:
        arr = [a for a in pool if a.get("role_type") == role]
        arr.sort(key=lambda a: (a.get("follower_count") or 0, a.get("agent_id") or ""))
        for i, a in enumerate(arr):
            a["_pct_in_role"] = i / max(1, len(arr) - 1)
    for a in pool:
        # follower_count / activity_level 为 0 会被当成缺省，这里显式区分 0 与 None
        if a.get("follower_count") is None:
            a["follower_count"] = 0
        if a.get("influence_weight") is None:
            a["influence_weight"] = round(a.get("_pct_in_role", 0.5), 4)
        a["age_band"] = a.get("age_band")
    return pool


def validate_pool(pool, taxonomy, contract, slots_cfg=None, alias_cfg=None) -> tuple:
    """返回 (是否通过, 报告行 list)。"""
    leaves = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
    alias_leaves = set()
    for key in ("strong", "weak", "general_bridge"):
        for arr in ((alias_cfg or {}).get(key) or {}).values():
            alias_leaves |= set(arr)
    combos = _combo_from_cfg(contract, slots_cfg)
    quota = (slots_cfg or {}).get("quota") or {}
    n = len(pool)
    R, ok_all = [], True

    def line(ok, msg):
        nonlocal ok_all
        ok_all &= bool(ok)
        R.append("  [%s] %s" % ("OK" if ok else "FAIL", msg))

    R.append("画像库规模：%d 条（组长口径：1000~2000 条，且单场配额的 >= 3 倍）" % n)

    ids = [a.get("agent_id") for a in pool]
    dup = [k for k, c in Counter(ids).items() if c > 1]
    line(not dup, "agent_id 唯一性：重复 %d 个 %s" % (len(dup), dup[:5]))

    rc = Counter(a.get("role_type") for a in pool)
    bad_role = {k: v for k, v in rc.items() if k not in ROLES}
    line(not bad_role, "role_type 合法性（6 类，KOC 已删）：%s%s"
         % (dict(rc), "" if not bad_role else "  越界 %s" % bad_role))

    ch = Counter(a.get("primary_channel") for a in pool)
    bad_ch = {k: v for k, v in ch.items() if k not in CHANNELS}
    line(not bad_ch, "primary_channel 合法性（6 渠道，一人一个）：%s%s"
         % (dict(ch), "" if not bad_ch else "  越界 %s" % bad_ch))

    # ★ v2.1：矩阵外组合不再判 FAIL —— 画像库是按他们自己的 30 格矩阵产的，
    #   不是错；组长定的是 20 格，所以对**本流程**而言那些格子的人用不上。
    #   报 WARN + 量化浪费 + 把格子列出来，交给组长/画像库去对齐（详见 docs/画像库对接差异_v3.md §5）。
    bad_combo = [(a["role_type"], a["primary_channel"]) for a in pool
                 if (a.get("role_type"), a.get("primary_channel")) not in combos]
    waste = Counter(bad_combo)
    R.append("  [%s] (角色 x 渠道) 落在组长的 20 个有效组合内：矩阵外 %d 条（%.0f%%）"
             % ("OK" if not bad_combo else "WARN", len(bad_combo),
                100.0 * len(bad_combo) / max(1, n)))
    if waste:
        R.append("       ★ 这些人在本流程里**无法进入任何槽位**（矩阵按组长的 20 格），"
                 "等于闲置；用不上不是他们错，是两边矩阵不一致：")
        for (r, c), cnt in waste.most_common():
            R.append("         %-8s x %-8s %3d 条" % (r, c, cnt))
        R.append("       -> 处置：要么组长同意把这几格补进矩阵，要么画像库按 20 格重产。")

    sc = Counter(a.get("stance_bias") for a in pool)
    bad_st = {k: v for k, v in sc.items() if k not in STANCES}
    line(not bad_st, "stance_bias 合法性：%s%s" % (dict(sc), "" if not bad_st else "  越界 %s" % bad_st))

    outside = Counter(t for a in pool for t in a["concern_raw"] if t not in leaves)
    no_topic = sum(1 for a in pool if not a["concern_topics"])
    covered = sum(1 for a in pool if a["concern_topics"] and
                  all(t in leaves for t in a["concern_topics"]))
    mapped = sum(1 for a in pool if a["concern_raw"] and a["concern_leaves"])
    fuzzy_only = sum(1 for a in pool if a["concern_raw"] and not a["concern_leaves"]
                     and any(t in alias_leaves for t in a["concern_raw"]))
    R.append("  [%s] concern_topics 词表对齐：原词 %d 条、能归一到 taxonomy 的 %d 条、"
             "只能走模糊层的 %d 条、完全对不上的 %d 条"
             % ("OK" if mapped == n else "WARN", n, mapped, fuzzy_only,
                n - mapped - fuzzy_only))
    if outside:
        R.append("       原词不在 taxonomy 里（走模糊匹配，不再判 FAIL）：%d 种" % len(outside))
        for t, c in outside.most_common(20):
            tag = "（映射表 weak 可兜）" if t in alias_leaves else "（无映射，只能靠字面相似）"
            R.append("         %4d 次  %s %s" % (c, t, tag))

    for f, cond, hint in [
        ("subtype", lambda a: bool(a.get("subtype")), "空值会影响多样性校验"),
        ("persona_bio", lambda a: len(a.get("persona_bio") or "") >= 10, "太短会影响 agent 表现"),
        ("cross_channel_propensity", lambda a: a.get("cross_channel_propensity") is not None,
         "跨渠道传播的边权重靠它（组长 2.4 ③）"),
    ]:
        bad = sum(1 for a in pool if not cond(a))
        R.append("  [%s] %s 完整性：缺/不合格 %d 条  %s"
                 % ("OK" if bad == 0 else "WARN", f, bad, hint if bad else ""))
    bad_hours = sum(1 for a in pool if a.get("active_hours") and
                    any(not isinstance(h, int) for h in a["active_hours"]))
    R.append("  [%s] active_hours 必须为整数列表：格式错误 %d 条"
             % ("OK" if bad_hours == 0 else "WARN", bad_hours))
    no_w = sum(1 for a in pool if a.get("influence_weight") is None)
    R.append("  [%s] influence_weight（可比尺度的重要性）：缺失 %d 条（缺失时按角色内分位补）"
             % ("OK" if no_w == 0 else "WARN", no_w))

    # ---- 覆盖度：池子够不够撑 500 人
    R.append("  角色分布与需求匹配：（组长 2.6 要求池内条数 >= 单场配额的 3 倍；冷却期 k=5）")
    R.append("    %-10s %6s %6s %8s %10s" % ("角色", "池内", "单场需", "3倍下限", "可支撑场次"))
    for r in ROLES:
        have, nd = rc.get(r, 0), quota.get(r, 0)
        R.append("    %-10s %6d %6d %8d %10s"
                 % (r, have, nd, nd * 3, have // nd if nd else "-"))
    R.append("  主渠道分布（组长 2.6 要求主渠道分布跟随典型事件的渠道分布）：")
    R.append("    %s" % dict(ch))
    return ok_all, R


def describe_pool(pool, taxonomy):
    leaves = {lf["leaf"] for v in taxonomy["categories"].values() for lf in v["leaves"]}
    R = []
    R.append("  子型分布 Top10：%s" % Counter(a.get("subtype") for a in pool).most_common(10))
    tl = Counter(t for a in pool for t in a["concern_topics"] if t in leaves)
    R.append("  议题覆盖：%d/%d 个叶子有画像" % (len(tl), len(leaves)))
    missing = sorted(leaves - set(tl))
    if missing:
        R.append("  无画像覆盖的叶子：%s" % missing)
    R.append("  立场分布：%s" % dict(Counter(a.get("stance_bias") for a in pool)))
    R.append("  跨渠道倾向均值：%.3f（KOL/媒体应显著高于网民）"
             % (sum(a.get("cross_channel_propensity") or 0 for a in pool) / max(1, len(pool))))
    return R


def main():
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config
    ap = argparse.ArgumentParser(description="画像库契约校验器（交给画像组跑）")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(args.report, "w", encoding="utf-8")
    tax, contract = load_config("taxonomy"), load_config("persona_contract")
    slots_cfg, alias_cfg = load_config("slots"), load_config("topic_alias")
    pool = load_pool(args.pool, tax, contract, slots_cfg, alias_cfg)
    ok, rep = validate_pool(pool, tax, contract, slots_cfg, alias_cfg)
    print("=" * 76)
    print("画像库契约校验报告（契约版本 %s）" % contract["version"])
    print("=" * 76)
    for l in rep:
        print(l)
    print("\n分布画像：")
    for l in describe_pool(pool, tax):
        print(l)
    print("\n结论：%s" % ("通过，可以进入选人" if ok else "存在 FAIL 项，请按上面提示修正后再交付"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
