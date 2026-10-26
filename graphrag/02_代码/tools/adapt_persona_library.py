#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapt_persona_library.py —— 把画像库《舆情仿真人物画像字段规范》格式的库，接进本流程。

做法：**能映射的映射，映射不了的不编造**，并逐项出对接报告。
     映射不上的字段一律给「显式默认值 + 标记」，让下游看得见哪些列是猜的。

    python tools/adapt_persona_library.py --src 画像库.json --out out/persona_library_adapted.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config, write_json        # noqa: E402

# ★ v2.1：映射表搬到 config/topic_alias.json（两边共同维护的对齐底稿），这里只读取。
#    strong = 语义等价 -> 直接当归一后的叶子；weak = 语义接近 -> 交给模糊层打分。
# MVP 不做跨渠道搬运，所以 cross_channel_propensity 缺失不再是问题（字段保留但不参与选人）。


def adapt(recs, tax, alias_cfg=None):
    leaves = {lf["leaf"] for v in tax["categories"].values() for lf in v["leaves"]}
    pool, rep = [], Counter()
    for r in recs:
        a = dict(r)
        cb = a.get("channel_behavior") or {}
        # agent_id 形如 {平台}_{角色}_{NN}，平台位就是 primary_channel
        aid = str(a.get("agent_id") or "")
        ch = cb.get("channel") or (aid.split("_")[0] if "_" in aid else "")
        role = a.get("role_type") or cb.get("role_type")
        # concern_topics：dict 权重 -> 列表；只保留能映射到 taxonomy 的键
        # ★ 适配器只做**字段名映射**，归一交给 src/persona_io.load_pool 做。
        #   这里必须把**原词**原样带下去 —— 原词正是模糊检索（topic_match）的输入；
        #   一旦在这里把没落上叶子的词丢掉，模糊层就永远拿不到「消费者权益」这种词了。
        ct = a.get("interest_topics") or a.get("concern_topics") or {}
        if isinstance(ct, dict):
            # ★ 保留 dict（带权重）—— 9 大类方案里权重是核心信息，转成 list 会丢掉
            seq = {k: v for k, v in sorted(ct.items(), key=lambda kv: -kv[1]) if k != "其他"}
            seq_list = [k for k in seq]
        else:
            seq = [str(x) for x in ct]
            seq_list = list(seq)
        strong = dict((alias_cfg or {}).get("strong") or {})
        for k, v in ((alias_cfg or {}).get("general_bridge") or {}).items():
            strong.setdefault(k, [])
            strong[k] = list(strong[k]) + list(v)
        mapped = [x for t in seq_list for x in (strong.get(t) or []) if x in leaves]
        dropped = [t for t in seq_list if t not in leaves]
        out = {
            "agent_id": aid,
            "role_type": role,
            # ★ 规范 v2：平台是顶层 channel 字段；旧版放在 primary_channel 或 channel_behavior 里，逐个兜底
            "primary_channel": (a.get("channel") or a.get("primary_channel")
                                or (a.get("channel_behavior") or {}).get("channel")),
            # ★ 组长 v3 §2.2-5 统一命名：concern_topics -> interest_topics
            "interest_topics": seq,          # ★ 原样保留（dict 保权重 / list 保顺序），归一由 load_pool 做
            "concern_topics": seq,           # 兼容旧消费方（load_pool 仍读这个名字）
            # ★ v2 二级结构：话题细项（可选）。大类命中给基础分，细项命中再提分
            **({"topic_hints": a["topic_hints"]} if a.get("topic_hints") else {}),
            "concern_topics_strong_mapped": mapped,
            "concern_topics_unmapped": dropped,
            "persona_bio": a.get("persona_bio"),
            # ★ 修正：画像库真实字段是 influence_weight（由 角色基准 x 粉丝因子 双驱动算出）；
            #   此前误读了 influence_score（那是旧的纯角色随机值），导致公式白做。
            "influence_weight": a.get("influence_weight") if a.get("influence_weight") is not None
                                else a.get("influence_score"),
            # ★★ active_hours = 在线小时的列表（旧版是 24 维强度向量，已废弃）
            "active_hours": a.get("active_hours_online") or a.get("active_hours_list") or a.get("active_hours"),
            # ---- 画像库没给的，给显式默认值并标记来源（不静默）
            "stance_bias": "中立",
            "_stance_bias_source": "MISSING_DEFAULT（画像库规范里没有这个字段）",
            # MVP 不做跨渠道搬运 —— 这一列不参与选人，留空即可（组长 2.4 的跨渠道留到非 MVP）
            # ★★ 组长 v3 #18：cross_channel_propensity 已删 —— 跨渠道可见性是【渠道对】的属性，
            #    语义并入渠道传播稀疏表（归仿真侧），不再是"人"的字段
            "activity_level": 0.5,
            "_activity_level_source": "MISSING_DEFAULT（画像库规范里没有这个字段）",
            "subtype": "%s-%s" % (role, ch),
            "_subtype_source": "DERIVED_FROM_ROLE_CHANNEL（画像库规范里没有这个字段）",
            "claim_experience": False,
            "_claim_experience_source": "MISSING_DEFAULT（画像库规范里没有这个字段）",
            # ★ 修正：粉丝数此前写死为 0。现读画像库真实字段 followers。
            "followers": a.get("followers"),
            "follower_count": a.get("followers") or 0,
            # ---- 画像库给了的，原样透传给下游（本流程选人用不到，但仿真层要）
            "valence_baseline": a.get("valence_baseline"),
            "arousal_baseline": a.get("arousal_baseline"),
            "emotion_sensitivity": a.get("emotion_sensitivity"),
            "emotion_decay": a.get("emotion_decay"),
            "contagion_susceptibility": a.get("contagion_susceptibility"),
            # ★★ 组长 v3 J5：complaint_propensity / share_propensity 已删（改由 LLM 判断）
            # ★★ 组长 v3 J12：action_thresholds / emotion_decay 是【角色x渠道规则表】15 格
            "action_thresholds": a.get("action_thresholds") or a.get("action_threshold"),
            # ★★ 组长 v3 §2.2-4 / -12：性格两字段 + active_level（组长点名要画像侧给）
            # ★★ 组长 v3 §2.2-8：stance_base（🟦 跨事件稳定的总体倾向）
            "stance_base": a.get("stance_base"),
            "emotional_stability": a.get("emotional_stability"),
            "extraversion": a.get("extraversion"),
            "active_level": a.get("active_level"),
            "channel_behavior": cb,
            # ★ 画像库 v3 扩展字段原样透传（机构集体人设 + 九维骨架）
            #   不丢字段 —— 推演层要用 org_type 做【报道领域 / 管辖范围】判定
            "_passthrough": True,
            "_source_format": "persona_library_v3_spec"}
        for _k in ("org_type", "org_position", "org_scope", "org_stance",
                   "age", "age_band", "gender", "occupation", "region", "urban_tier",
                   "education", "income_band", "marital_status",
                   "interest_circle", "bio_region", "concern_tier",
                   "city_pop_wan", "city_netizens_wan"):
            if _k in a and out.get(_k) is None:
                out[_k] = a[_k]
        _unused = {
        }
        if not mapped and dropped:
            rep["interest_topics 全部落空"] += 1
        rep["strong 直接归一到 taxonomy 的话题"] += len(mapped)
        rep["需要走模糊层的话题"] += len(dropped)
        pool.append(out)
    return pool, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    tax, alias_cfg = load_config("taxonomy"), load_config("topic_alias")
    raw = json.loads(Path(args.src).read_text(encoding="utf-8"))
    recs = raw["personas"] if isinstance(raw, dict) and "personas" in raw else raw
    pool, rep = adapt(recs, tax, alias_cfg)
    write_json(args.out, pool)
    lines = ["=" * 76, "画像库 v3 规范 -> 本流程契约 对接报告", "=" * 76,
             "输入 %d 条 -> 输出 %d 条" % (len(recs), len(pool)), ""]
    lines.append("① interest_topics（唯一的强耦合点）")
    lines.append("   本流程 taxonomy 叶子 %d 个；画像库话题库 %d 个" %
                 (sum(len(v["leaves"]) for v in tax["categories"].values()), 12))
    lines.append("   strong（等价，直接归一）：%s"
                 % "、".join("%s->%s" % (k, "/".join(v))
                             for k, v in (alias_cfg.get("strong") or {}).items()))
    lines.append("   weak（接近，交给模糊层打分）：%s"
                 % "、".join("%s≈%s" % (k, "/".join(v) or "（无对应）")
                             for k, v in (alias_cfg.get("weak") or {}).items()))
    lines.append("   实测：strong 直接归一 %d 次；只能走模糊层的 %d 次"
                 % (rep["strong 直接归一到 taxonomy 的话题"], rep["需要走模糊层的话题"]))
    lines.append("   ★ 原词全部保留在 interest_topics 里 —— 模糊层要用它去检索（见 src/topic_match.py）")
    lines.append("")
    lines.append("② 画像库规范里没有、本流程必须要的字段（已给显式默认值，全部标记来源）")
    for f, why in [("stance_bias", "正反方切分 / 立场相容打分（组长 2.2 字段8、3.2 #4）"),
                   ("cross_channel_propensity", "【MVP 不用】跨渠道搬运，组长 2.4 留给非 MVP"),
                   ("activity_level", "R1 每轮点名采样权重（组长 2.2 字段9）"),
                   ("subtype", "多样性校验"),
                   ("claim_experience", "客户槽位加权"),
                   ("follower_count", "影响力分层锚点（可用 influence_score 代替）"),
                   ("region", "报告群体切片（组长 2.2 字段3）")]:
        lines.append("   %-26s %s" % (f, why))
    lines.append("")
    lines.append("③ 有效组合矩阵对不上（这是结构性冲突，不是字段问题）")
    lines.append("   本流程 20 格；画像库规范 30 格。详见 docs/画像库对接差异_v3.md")
    txt = "\n".join(lines)
    print(txt)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(txt + "\n", encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
