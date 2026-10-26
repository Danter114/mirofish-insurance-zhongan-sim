#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t0_init.py —— 交付物 E · t0 一次性初始化（组长《给画像侧_事件集与t0快照交付清单》§六）

E1 具名实体 1:1 绑定   —— 已在上游完成（roster 的 plays_entity）
E2 每人的初始效价      ★ 本工具算
E3 每人的初始唤醒      ★ 本工具算（组长标为"当前缺口"）
E4 关注网络按本议题轻量重连  ★ 本工具算

★ 只做一次；之后每轮只改动态状态，不再动人设。
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src")) if str(Path(__file__).resolve().parent.parent / "src") not in sys.path else None
from common import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 卷入度：越靠近事件核心越高（★ 组长未给公式，此处按角色 + 是否具名派生）
INVOLVE = {"客户": 0.60, "KOL": 0.40, "媒体": 0.40, "监管": 0.20, "网民": 0.15}
INVOLVE_NAMED = 1.00          # 具名实体（1:1 绑定的真实当事人）

# 事件类型基准（组长：理赔类偏高、营销类偏低）
TYPE_BASE = {"理赔类": 0.15, "营销类": -0.05, "销售与投保类": 0.05, "服务体验类": 0.08,
             "产品与费率类": 0.00, "数据与隐私类": 0.05, "公司经营类": -0.05,
             "代理维权类": 0.10, "诉讼与司法类": 0.08, "产品线专项类": 0.00,
             "雇主品牌类": -0.08, "保险欺诈类": 0.10}
DEFAULT_BASE = 0.00
AROUSAL_FLOOR, AROUSAL_GAIN = 0.30, 0.40        # 组长给的形态：0.30 + 卷入度 × 0.4


def _event_stance(kg):
    """事件级立场目标：所有关系边的极性均值（质疑=-1 / 报道=0 / 支持=+1）"""
    POL = {"质疑": -1.0, "指控": -1.0, "批评": -1.0, "报道": 0.0, "回应": 0.2, "支持": 1.0, "辩护": 1.0}
    vs = [POL.get(r.get("type"), 0.0) for r in (kg.get("relations") or [])]
    return round(sum(vs) / len(vs), 3) if vs else -0.3


def _topic2cat9():
    """47 话题 -> 9 大类（扁平字典）"""
    try:
        return load_config("topic_to_cat9")
    except Exception:
        return {}


def _bridge_index():
    """事件议题 leaf -> 画像 47 话题（组长 v3 §2.4 三级归一化链的第 ②->③ 步）"""
    try:
        gb = (load_config("topic_alias")
              .get("general_bridge") or {})
    except Exception:
        gb = {}
    idx = {}
    for topic, leaves in gb.items():
        for lf in (leaves or []):
            idx.setdefault(lf, topic)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--kg", required=True)
    ap.add_argument("--event-pack", required=True)
    ap.add_argument("--personas", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    kg = json.loads(Path(a.kg).read_text(encoding="utf-8"))
    pack = json.loads(Path(a.event_pack).read_text(encoding="utf-8"))
    roster = json.loads(Path(a.roster).read_text(encoding="utf-8"))["members"]
    pool = {p["agent_id"]: p for p in json.loads(Path(a.personas).read_text(encoding="utf-8"))}

    # ---- A2 事件类型枚举
    enum_map = load_config("event_type_enum")
    # ★★ 组长 v3 §2.5：A2 从单选改【1–2 个标签】
    #     按 event_type_dist 取 top1-2，逐个映射；查表时按标签逐个取再合并
    et = (kg.get("event_type") or "")
    _dist = kg.get("event_type_dist") or {}
    if isinstance(_dist, dict):
        _order = [k for k, _ in sorted(_dist.items(), key=lambda kv: -(kv[1] or 0))]
    elif isinstance(_dist, (list, tuple, set)):
        _order = [str(x) for x in _dist]
    else:
        _order = []
    if et and et not in _order:
        _order = [et] + _order
    _m = enum_map["taxonomy_to_enum"]
    etype_list = []
    for _k in _order[:2]:
        _v = _m.get(_k) or next((v for kk, v in _m.items() if kk and kk in _k), None)
        if _v and _v not in etype_list:
            etype_list.append(_v)
    if not etype_list:
        etype_list = ["其他类"]
    etype = etype_list[0]                     # 主标签（向后兼容旧的单值消费方）
    base = TYPE_BASE.get(etype, DEFAULT_BASE)

    S_event = _event_stance(kg)
    # ★ 事件级放大强度（组长 v3 §8.3：待标定）—— 0 = 纯画像倾向，越大越被事件点燃
    #   注意是【放大】不是【位移】：不改变立场符号，只放大已有倾向的强度
    EVENT_PULL = 0.55
    # 最小议题对齐度（兜底）：被选进来 = 至少有相关性，不该判成完全中立
    MIN_ALIGN = 0.25
    # ★★ 事件无关（event-agnostic）：议题分层 ——
    #   ① ontology 议题（归一后的正式议题）—— 有的话就只用它们做 stance_bias / emotion_sensitivity
    #   ② auto:oov 节点（词表外的采集侧原词）—— 只参与建图与 R3 图遍历，不当事件议题
    #   ③ 若一个 ontology 议题都没有（全新事件），才降级用 OOV 当议题，保证链路不断
    _all_iss = [i for i in (kg.get("issues") or []) if i.get("leaf")]
    _onto = [i for i in _all_iss if (i.get("provenance") or "ontology") != "auto:oov"]
    _used = _onto if _onto else _all_iss
    event_leaves = [i.get("leaf") for i in _used]
    _l2t = _bridge_index()
    _t2c9 = _topic2cat9()

    out, net = [], defaultdict(list)
    by_ch = defaultdict(list)
    for m in roster:
        aid = m["agent_id"]
        p = pool.get(aid) or {}
        vb = float(p.get("valence_baseline") or m.get("valence_baseline") or -0.1)
        ab = float(p.get("arousal_baseline") or m.get("arousal_baseline") or 0.45)
        named = bool(m.get("plays_entity"))
        inv = INVOLVE_NAMED if named else INVOLVE.get(m.get("role_type"), 0.15)

        # E2 初始效价：围观者≈基线，当事人顶到事件立场目标
        v0 = round(vb + inv * (S_event - vb), 4)
        # E3 初始唤醒：0.30 + 卷入度×0.4 + 事件类型基准（再与画像基线做一次融合）
        a0 = round(min(1.0, max(0.05, (AROUSAL_FLOOR + inv * AROUSAL_GAIN + base) * 0.7 + ab * 0.3)), 4)

        # ★★★ 组长 v3 §2.2-8 / J7：事件级 stance_bias[议题] = stance_base x 议题对齐度
        #      「中立」是【结果】不是【值】—— 对不关心的议题，乘积自然接近 0
        _it = p.get("interest_topics") or {}
        if isinstance(_it, list):
            _it = {t: 1.0 for t in _it}
        # ★ 议题对齐度用【四档结构】而不是权重归一 ——
        #   权重被 3~7 个话题摊薄后每个都很小，归一出来的 align 普遍偏低，
        #   会导致 94% 的人落在中立档（实测）。四档是语义分档，更贴合"他到底关不关心"。
        _tier = p.get("concern_tier") or p.get("interest_tier") or {}
        _TIER_ALIGN = {"key": 1.00, "mild": 0.60, "weak": 0.20, "ban": 0.0}
        # ★★ 优先用【名册】的 stance_base —— 画像库里的那个是默认值。
        #   t0 真实人物的 stance_base 由 GraphRAG 实测推导（如 -0.77），
        #   若读画像库会被默认值 0 覆盖（踩过：刘嘻嘻 -0.77 变 0）。
        _sbase = None
        for _src in (m.get("stance_base"), p.get("stance_base")):
            if _src is not None:
                _sbase = float(_src)
                break
        if _sbase is None:
            _sb0 = p.get("stance_bias")
            _sbase = float(_sb0) if isinstance(_sb0, (int, float)) else 0.0
        _sbias = {}
        for _lf in event_leaves:
            # ★★ 议题对齐度必须按【选人时实际用的层级】算 ——
            #    选人走的是三级匹配（exact / same_top / cat_*），
            #    实测只有 9/54 的人其议题话题精确落在 interest_topics 里。
            #    只看精确话题会让 83% 的人 align=0 -> 全体中立（这就是之前的 bug）。
            _topic = _l2t.get(_lf)
            if _topic and _topic in _tier:
                _align = _TIER_ALIGN.get(str(_tier.get(_topic) or "").lower(), 0.60)
            elif _topic and _topic in _it:
                _align = 0.30                       # 有权重但落在 weak 档（未写进 tier）
            else:
                # ② 大类级对齐：该人在【该话题所属 9 大类】上的权重占比
                # ★ cat_weights 是 load_pool() 运行时算的，适配后的原始库里没有 —— 这里内联算
                _cat = _t2c9.get(_topic) if _topic else None
                _cw = {}
                for _tt, _ww in _it.items():
                    _cc = _t2c9.get(_tt)
                    if _cc:
                        _cw[_cc] = _cw.get(_cc, 0.0) + float(_ww)
                _cmax = max(_cw.values()) if _cw else 0.0
                _align = (0.55 * (_cw.get(_cat, 0.0) / _cmax)) if (_cat and _cmax > 0) else 0.0
            # ① 组长公式：stance_bias[议题] = stance_base x 议题对齐度
            _v = _sbase   # ★ 2026-10-07 直接改用 stance_base（不乘议题对齐度）
            # ② 事件级校准（🟨 由画像侧按事件覆盖）——
            #    ★ 必须是【乘性放大】而不是【加性位移】：
            #      加性位移会把所有人一起往负方向推，结果是 counter 恒为 0（实测），
            #      违反组长 §2.6 硬规则 2「正反双方都要建槽位」。
            #      乘法只放大【已有的倾向】，不改变符号。
            # _v *= (1.0 + EVENT_PULL * abs(S_event))   # ★ 已停用（原先的乘性放大）
            # ★ 兜底：被选进来本身就说明有一定相关性 —— 给一个最小对齐，避免全库归零
            if False:   # ★ 兜底分支已作废
                _align = MIN_ALIGN
                pass
            _sbias[_lf] = round(max(-1.0, min(1.0, _v)), 3)

        # ★★ side 从 stance_bias 派生（组长 §2.6 三条硬规则之二：正反双方都要建）
        #    槽位阶段的 side 是配额切分（随机），选人之后有了真实立场，
        #    这里按【他自己的 stance_bias】重算 —— 这样正反方才是真的切出来了
        _leaf_self = m.get("issue_leaf")
        if _leaf_self and _leaf_self in _sbias:
            _v_side = _sbias[_leaf_self]
        elif _sbias:
            _v_side = sum(_sbias.values()) / len(_sbias)
        else:
            _v_side = 0.0
        _side = "pro" if _v_side < -0.15 else ("counter" if _v_side > 0.15 else "neutral")

        out.append({
            "side": _side,
            "side_source": "由 stance_bias 派生（不是槽位阶段的配额随机）",
            "stance_base": _sbase,
            "stance_bias": _sbias,
            "stance_bias_default": _sbase,
            "agent_id": aid, "role_type": m.get("role_type"), "primary_channel": m.get("primary_channel"),
            "issue_leaf": m.get("issue_leaf"), "plays_entity": m.get("plays_entity"),
            "involvement": inv, "is_named": named,
            "E2_valence_0": v0, "E3_arousal_0": a0,
            "baseline": {"valence": vb, "arousal": ab},
            "formula": {"E2": "vb + 卷入度 x (S_event - vb)", "E3": "(0.30 + 卷入度 x 0.40 + 事件类型基准) x 0.7 + ab x 0.3"},
        })
        by_ch[m.get("primary_channel")].append(aid)

    # ---- E4 关注网络：同渠道 + 同议题 优先（轻量重连，不建全局全连接）
    for m in out:
        aid = m["agent_id"]
        same_issue = [x["agent_id"] for x in out
                      if x["agent_id"] != aid and x["issue_leaf"] == m["issue_leaf"]
                      and x["primary_channel"] == m["primary_channel"]]
        same_ch = [x for x in by_ch[m["primary_channel"]] if x != aid]
        ties = [{"to": t, "type": "same_issue+same_channel", "weight": 1.0} for t in same_issue]
        ties += [{"to": t, "type": "same_channel", "weight": 0.4}
                 for t in same_ch if t not in same_issue][:12]
        net[aid] = ties

    res = {
        "artifact": "t0_init", "version": "1.0", "event_id": kg.get("event_id"),
        "spec": "组长《给画像侧_事件集与t0快照交付清单》§六 交付物 E",
        "A2_event_type_enum": etype,                 # 主标签（兼容）
        "A2_event_type_labels": etype_list,          # ★ 1–2 个标签（组长 v3 §2.5）
        "A2_source_taxonomy_type": et,
        "A2_source_taxonomy_dist": _dist,
        "E1_named_binding": [{"agent_id": x["agent_id"], "plays_entity": x["plays_entity"]}
                             for x in out if x["plays_entity"]],
        "event_level": {"S_event_stance_target": S_event, "type_arousal_base": base,
                        "note": "S_event = 所有关系边的极性均值；E2 用它把当事人的效价顶到事件立场方向"},
        "usage": {
            "E2": "情绪动力学的 S(0)；规则闸门的起跑线",
            "E3": "★ 闸门是二维的（效价 + 唤醒）—— 缺唤醒这一维，闸门只能判一半",
            "E4": "决定『谁能看到谁』；同议题同渠道优先，其余同渠道弱连接",
            "stance_bias": "★ 组长 v3 §2.2-8 / J7：stance_bias[议题] = stance_base x 议题对齐度 + 事件级校准。『中立』是结果不是值 —— 对不关心的议题乘积自然接近 0"},
        "agents": [{**x, "E4_follows": net[x["agent_id"]]} for x in out],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    import statistics as st
    print("事件类型标签：%s（源自 taxonomy「%s」）" % (etype_list, et))
    print("事件级立场目标 S_event = %s ｜ 唤醒基准 %+.2f" % (S_event, base))
    _sb_all = [v for x in out for v in (x.get("stance_bias") or {}).values()]
    if _sb_all:
        _neg = sum(1 for v in _sb_all if v < -0.15); _pos = sum(1 for v in _sb_all if v > 0.15)
        _neu = len(_sb_all) - _neg - _pos
        print("stance_bias[议题]：%d 条 ｜ 偏负 %d（%.0f%%）｜ 中立 %d（%.0f%%）｜ 偏正 %d（%.0f%%）｜ 区间 %.3f~%.3f"
              % (len(_sb_all), _neg, 100.0*_neg/len(_sb_all), _neu, 100.0*_neu/len(_sb_all),
                 _pos, 100.0*_pos/len(_sb_all), min(_sb_all), max(_sb_all)))
    print("E1 具名绑定 %d 人 ｜ E2/E3 已算 %d 人 ｜ E4 关注边 %d 条"
          % (len(res["E1_named_binding"]), len(out), sum(len(v) for v in net.values())))
    print("  初始效价 E2: %.3f ~ %.3f（均值 %.3f）"
          % (min(x["E2_valence_0"] for x in out), max(x["E2_valence_0"] for x in out),
             st.mean([x["E2_valence_0"] for x in out])))
    print("  初始唤醒 E3: %.3f ~ %.3f（均值 %.3f）"
          % (min(x["E3_arousal_0"] for x in out), max(x["E3_arousal_0"] for x in out),
             st.mean([x["E3_arousal_0"] for x in out])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
