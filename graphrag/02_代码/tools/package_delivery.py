#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
package_delivery.py —— 把一次 run 的产物打成【推演层交付包】（A–F 编号）

★ 可复用：不写死任何事件。换事件、换规模、换种子都只需换参数。

    python tools/package_delivery.py --run-dir out_500 --personas out/persona_v5_50k_adapted.json \
        --out 给推演层_交付/EVT-001_500人 --event-name "众安宠物险低俗广告"
"""
from __future__ import annotations
import argparse, json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CH = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]
# 15 格矩阵（组长《交付清单》§4.3）—— 发布范围
PUB = {"微博": ["网民", "客户", "KOL", "媒体"], "抖音": ["网民", "客户", "KOL", "媒体"],
       "小红书": ["网民", "客户", "KOL", "媒体"], "黑猫投诉": ["客户"],
       "新闻门户": ["媒体"], "监管通报": ["监管"]}
DROP = [
    # ★★ 组长 v3 后已删的字段（画像库里不再有）
    "cross_channel_propensity", "complaint_propensity", "share_propensity",
    # 旧的纯角色随机值，已被 influence_weight 取代
    "influence_score",
    # 上游内部字段（槽位分配用，推演层不需要）
    "seat", "purity_group", "purity_fit", "fill_gap", "tier",
    # 已被 E2 / E3 初始值取代（t0 一次性初始化算好）
    # ★ 2026-10-07：valence_baseline / arousal_baseline 【不再 DROP】——
    #   仿真层要这两个（组长 清洗后event1画像.json 口径）
    # ★ 注意：influence_weight / emotion_decay / emotion_sensitivity / contagion_susceptibility
    #   组长 v3 §2.2 明确【需要】—— 不能再剔
    # ★ 组长 2026-10-06 明确：【concern_tier 不需要】—— 交付时删掉
    #   （它在生成器内部仍用于 bio 一致性与档位判定，只是不进交付物）
    "concern_tier",
    # ★ 用户 2026-10-07：删除 concern_topics（已用 interest_topics）
    #   注意：concern_topics 是【该人绑定的事件议题列表】(list)，
    #   和 interest_topics（47 话题权重 dict）不是一回事；确认不再需要后删除。
    "concern_topics",
    # ★ 用户 2026-10-07：删除 emotion_decay（属【演化层】的事，不在上游交付）
    "emotion_decay",
    # ★ 组长 2026-10-07：【所有人】删除 activity_level
    #   实测它恒为 0.5（500/500 同一个值），零信息量，已被 active_level 完整取代。
    "activity_level",
]

# ★★ 组长 2026-10-07：【机构账号】删除个人骨架字段
#   机构（媒体/监管）没有年龄/性别/婚育/学历/收入/职业/圈层语义 ——
#   保留会出现「高中及以下的都市报深度调查账号」这类组合。
#   ★ age_band 是 age 的分桶（如 "31-40"），删 age 留 age_band 会不自洽，一并删。
# ★★ 2026-10-07 仿真层交付字段 —— 严格照抄组长《清洗后event1画像.json》的 13 个键（顺序也照抄）。
#   其余字段一律进 _中间产物/，不交给下游。
# ★★ 仿真层需求 §二-2 给的 SENS 表（角色）
SENS_REQ = {"网民": 1.2, "客户": 1.1, "KOL": 0.95, "媒体": 0.8, "监管": 0.5}
#   按画像库角色配额(.57/.23/.11/.06/.03)加权 = 1.1045 —— 需求要求全体均值 ≈ 1.0，故预除该系数
_SENS_K = 0.57 * 1.2 + 0.23 * 1.1 + 0.11 * 0.95 + 0.06 * 0.8 + 0.03 * 0.5
SENS_CAL = {r: v / _SENS_K for r, v in SENS_REQ.items()}
EMO_LO, EMO_HI = 0.5, 2.0

SIM_FIELDS = [
    "agent_id", "role_type", "primary_channel", "persona_bio",
    "interest_topics", "influence_weight", "active_hours", "stance_bias",
    "emotion_sensitivity", "valence_base_0", "arousal_base_0",
    "valence_baseline", "arousal_baseline",
    # ★ 2026-10-07 用户要求：A/B 类标记（A = t0 就动作的真实人物 or t0 已知情者）
    "is_A_type",
]

ORG_ROLES = ("媒体", "监管")
ORG_DROP = [
    "age", "age_band", "gender", "marital_status", "occupation",
    "education", "income_band", "urban_tier",
    "interest_circle",
]


# ★★ 组长 v3 §2.6 的 5 动作池：引用 / 转发 / 评论 / 点赞 / 沉默
#   「客户投诉」「媒体报道」「监管通报」不是独立动作，都是【引用】落在各自 primary_channel。
#   旧实现返回 ["like","comment","repost","post"(+"report"/"complaint")] —— 已废弃，
#   害得 D_Agent名单.json 里 500/500 带 "post"、0/500 带 "quote"。
_ACT_5 = ["like", "comment", "repost", "quote"]
ROLE_ACTIONS = {"网民": list(_ACT_5), "客户": list(_ACT_5), "KOL": list(_ACT_5),
                "媒体": list(_ACT_5), "监管": ["quote"]}   # 监管 = {引用, 沉默}
QUOTE_MEANING = {"网民": "发帖", "客户": "投诉", "KOL": "表态", "媒体": "报道", "监管": "通报"}


def acts(role, ch):
    """按【角色】返回动作集（5 动作池）；不再按 (角色,渠道) 分叉"""
    return list(ROLE_ACTIONS.get(role, _ACT_5))


def action_channels(role, primary):
    """可动作渠道：表达型全开；功能型有身份门槛（黑猫=客户 / 通报=监管）"""
    out = []
    for c in CH:
        if c == "黑猫投诉" and not (role == "客户" or primary == "黑猫投诉"):
            continue
        if c == "监管通报" and not (role == "监管" or primary == "监管通报"):
            continue
        out.append(c)
    return out


def J(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--personas", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--event-key", default="EVT-001", help="run 里的 event_key（产物文件名用它）")
    ap.add_argument("--event-name", default="")
    a = ap.parse_args()

    R = Path(a.run_dir)
    k = a.event_key
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "规范").mkdir(parents=True)
    (out / "参考实现").mkdir(parents=True)

    kg = J(R / "kg" / ("event_kg_%s.json" % k))
    pack = J(R / "packs" / "event_pack.json")
    roster = J(R / "downstream" / ("%s_roster.json" % k))
    t0snap = J(R / "downstream" / ("%s_t0_snapshot.json" % k))
    rel = J(R / "relevance" / ("relevance_%s.json" % k))
    act = J(R / "relevance" / ("t1_activation_%s.json" % k))
    t0i = J(R / "downstream" / ("%s_t0_init.json" % k))
    slots = J(R / "slots" / ("slots_%s.json" % k))
    gc = J(R / "report" / "group_check.json") if (R / "report" / "group_check.json").exists() else {}
    enum_map = load_config("event_type_enum")

    # ---------- A 事件包 ----------
    A = {"schema": "event_pack/v2", "spec": "组长《交付清单》§二 交付物 A",
         "A1_event_id": kg["event_id"], "A1_event_name": a.event_name or kg.get("title"),
         # ★ 2026-10-20 删除 A2_event_type / A2_event_type_labels / A2_event_type_dist：
         #   事件类型以采集侧 8 类为准（`采集侧_事件类型.json` sidecar 是唯一载体），
         #   图谱侧不再自推。这三项留着只会与采集侧口径打架（EVT-009/010/012 实测不同）。
         #   下面两项是**图谱自己的话题分类**，仅作溯源，不冒充事件类型。
         "A2_source_taxonomy": kg.get("event_type"),
         "A2_source_taxonomy_dist": t0i.get("A2_source_taxonomy_dist"),
         "A3_posts": [{"doc_id": d.get("doc_id"), "content": d.get("raw_content"),
                       "title": d.get("title"), "url": d.get("source_url")}
                      for d in (pack.get("docs") or [])],
         "A4_ignition": pack["meta"].get("ignition_point"), "A4_t0": kg.get("t0"),
         "A5_first_channel": (pack["meta"].get("ignition_point") or {}).get("channel"),
         "A6_participants": [{"agent_id": m["agent_id"], "author": m.get("plays_entity"),
                              "platform": m.get("primary_channel"), "role": m.get("role_type")}
                             for m in roster["members"]],
         "A7_dataset_split": pack["meta"].get("dataset_split"),
         "A7_rule": pack["meta"].get("dataset_split_rule"),
         "A7_frozen_at": pack["meta"].get("dataset_split_frozen_at"),
         "A8_t0_cutoff": (kg.get("t0_snapshot") or {}).get("cutoff_time")}
    (out / "A_事件包.json").write_text(json.dumps(A, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- B t0 快照 ----------
    # ★★ 2026-10-13：交付物 B 合并成一份 —— 原先只写 spec/B1/B2/B3/note 五个键，
    #   把 t0 / visible_until / t0_policy / leakage_guard / initial_posts 全丢了，
    #   下游拿到 B 根本看不到「t0 状态」和「能看到到哪一刻」。完整快照虽另存在 _原始.json，
    #   但下游很容易只读 B 而错过关键字段（踩过）。现在 B = 完整快照 + spec 标记。
    _B = dict(t0snap or {})
    _B["spec"] = "交付物 B —— t0 状态快照（唯一权威来源；含可见时间上界与防泄漏声明）"
    _B["B1_named_entities"] = kg.get("named_entities")
    _B["B2_issues"] = kg.get("issues")
    _B["B3_relations"] = kg.get("relations")
    _B["note"] = ("t0 是数据驱动的还原点，不是生成。leakage_guard：所有进推演的帖子必须 "
                  "publish_time <= visible_until。B3 允许稀疏（t0 段几乎没有跨渠道边），不要为凑边而虚构关系。")
    _B["用于下游"] = {"第0轮注入": "initial_posts", "可见时间上界": "visible_until",
                     "具名实体": "named_entities", "议题": "issues", "关系边": "relations",
                     "种子": "seed", "防泄漏声明": "leakage_guard"}
    (out / "B_t0状态快照.json").write_text(
        json.dumps(_B, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "B_t0状态快照_原始.json").write_text(
        json.dumps(t0snap, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- C 槽位表 ----------
    quota = {}
    for c, rs in PUB.items():
        for r in rs:
            quota[r] = quota.get(r, 0)
    sc = load_config("slots")
    (out / "C_槽位表.json").write_text(json.dumps(
        {"spec": "交付物 C —— 槽位 = 角色 × 立场 × 议题 × 主渠道 × 人数",
         "matrix_cells": sum(len(v) for v in PUB.values()), "publish_matrix": PUB,
         "quota": sc.get("quota"),
         "receive_vs_publish": "★ 接收范围 ⊋ 发布范围：所有人都能看到门户/通报/黑猫的内容（跨渠道折扣矩阵管），但不在那里发 —— 矩阵是 15 格而非满格 30",
         "slots": slots}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- D Agent 名单 ----------
    # ★★ 事件议题 + 议题→47话题 桥接（算议题级 emotion_sensitivity 用）
    _all_iss = [i for i in (kg.get("issues") or []) if i.get("leaf")]
    # ★★ 议题分层（与 tools/t0_init.py 保持一致）：
    #   ① ontology 议题（归一后的正式议题）—— stance_bias / emotion_sensitivity 只用它们
    #   ② auto:oov 节点（词表外的采集侧原词）—— 只参与建图与 R3 图遍历，不当事件议题
    #   ③ 若一个 ontology 议题都没有（全新事件），才降级用 OOV
    _onto = [i for i in _all_iss if (i.get("provenance") or "ontology") != "auto:oov"]
    issues = _onto if _onto else _all_iss
    # ★ 主议题 = heat_hist 最高的那个（stance_bias 只留它）
    _main_issue = None
    if issues:
        _main_issue = max(issues, key=lambda i: (
            i.get("heat_hist") if i.get("heat_hist") is not None else (i.get("heat") or 0)))["leaf"]
    _l2t = {}
    for _tp, _ls in ((load_config("topic_alias").get("general_bridge")) or {}).items():
        for _lf in (_ls or []):
            _l2t.setdefault(_lf, []).append(_tp)

    Rr = {x["agent_id"]: x for x in rel["records"]}
    T = {x["agent_id"]: x for x in (act.get("agents") or [])}
    T0 = {x["agent_id"]: x for x in t0i.get("agents", [])}
    agents = []
    agents_sim = []
    for m in roster["members"]:
        # ★ 机构账号额外剔除个人骨架字段
        _drop = DROP + (ORG_DROP if m.get("role_type") in ORG_ROLES else [])
        o = {kk: vv for kk, vv in m.items() if kk not in _drop}
        aid = m["agent_id"]; prim = m["primary_channel"]
        o["visible_channels"] = [prim]
        o["action_channels"] = action_channels(m["role_type"], prim)
        o["action_sets"] = {c: acts(m["role_type"], c) for c in o["action_channels"]}
        o["relevance"] = (Rr.get(aid) or {}).get("relevance")
        o["activation_tier"] = (T.get(aid) or {}).get("tier")
        o["activation_priority"] = (T.get(aid) or {}).get("activation_priority")
        t = T0.get(aid) or {}
        # ★ 组长 v3 §2.6 硬规则 2：正反双方都要建。
        #   side 以【由 stance_bias 派生】的值为准（槽位阶段那个是配额随机，不是真立场）
        if t.get("side"):
            o["side"] = t["side"]
            o["side_source"] = t.get("side_source")
        # ★★ 组长 v3 §2.2-8：stance_bias 是【事件级 dict】不是画像库里的默认字符串
        #    roster 里那个是画像默认值（= stance_base），必须用 t0_init 按本场议题算的版本覆盖
        if t.get("stance_bias") is not None:
            o["stance_bias"] = t["stance_bias"]
        if t.get("stance_base") is not None:
            o["stance_base"] = t["stance_base"]
        if t.get("stance_bias_default") is not None:
            o["stance_bias_default"] = t["stance_bias_default"]
        o["t0_init"] = {"involvement": t.get("involvement"), "E2_valence_0": t.get("E2_valence_0"),
                        "E3_arousal_0": t.get("E3_arousal_0"), "E4_follows": t.get("E4_follows")}
        # ★★ 仿真层需求 §二-2：emotion_sensitivity 改为【议题级】，0.5~2.0，全体均值 ≈ 1.0
        #   公式沿用需求的  concern_topics[t] × SENS[role] + 噪声，
        #   但需求同时要求 0.5~2.0 / 均值 1.0（文档内部不一致）—— 按硬要求校准：
        #     emo[议题] = clip( SENS_CAL[角色] × (0.55 + 0.90 × 对齐度) × 个体因子, 0.5, 2.0 )
        #   对齐度 = 该议题桥接到的话题在该人 interest_topics 里的权重和（0~1）
        _ind = m.get("emotion_sensitivity_individual") or 1.0
        _base = SENS_CAL.get(m.get("role_type"), 1.0)
        _it = m.get("interest_topics") or {}
        #   第一遍先算 raw（不夹取），循环结束后统一做两遍校准 -> 全体均值 = 1.0
        _es_raw = {}
        for _is in issues:
            _lf = _is.get("leaf")
            _topics = _l2t.get(_lf) or [_lf]
            _al = sum(float(_it.get(_t, 0) or 0) for _t in _topics)   # ★ interest_topics（已取代 concern_topics）
            _al = min(1.0, max(0.0, _al))
            # ★ 兜底：该议题与该人的 interest_topics 完全不对齐时（如监管的关注面是政策法规），
            #   用他本人 interest_topics 的 top1 权重作伪对齐 —— 保证【同角色内仍有离散度】
            #   （需求硬要求：不许给成"角色常量的复读"）
            if _al <= 0:
                _nz = [float(v) for kk, v in _it.items() if kk != "其他" and (v or 0) > 0]
                _al = max(_nz) if _nz else 0.25
            #   ★ 对齐因子 (0.70 + 0.65 × align)：下限 0.70 保证媒体/监管不撞 0.5 下限
            _es_raw[_lf] = _base * (0.70 + 0.65 * _al) * _ind
        o["_es_raw"] = _es_raw
        agents.append(o)

    # ★★ emotion_sensitivity 校准 —— 让【全体均值 = 1.0】（需求硬要求）且【不撞 0.5/2.0 边界】
    #   第一遍已按 interest_topics 算好 raw（议题级）。
    #   用线性映射 v' = 1 + (v − mean) × k  —— 这样 v' 的均值【恒等于 1.0】。
    #   k 取到「最小值刚好落在 0.5、最大值刚好落在 2.0」为止（谁先撞线谁定 k）。
    _allraw = [v for o in agents for v in (o.get("_es_raw") or {}).values()]
    if _allraw:
        _m0 = sum(_allraw) / len(_allraw)
        _dlo = _m0 - min(_allraw)
        _dhi = max(_allraw) - _m0
        _k = 1.0
        if _dlo > 0:
            _k = min(_k, (1.0 - EMO_LO) / _dlo)   # ★ 映射后均值 = 1.0，故用 1.0 而非 _m0
        if _dhi > 0:
            _k = min(_k, (EMO_HI - 1.0) / _dhi)   # ★ 同上
        _k *= 0.999   # 留一丝余量，避免浮点撞线
        for o in agents:
            o["emotion_sensitivity"] = {kk: round(1.0 + (vv - _m0) * _k, 4)
                                        for kk, vv in (o.get("_es_raw") or {}).items()}
            o.pop("_es_raw", None)
        _mv = [v for o in agents for v in o["emotion_sensitivity"].values()]
        print("  ★ emotion_sensitivity 议题级: k=%.4f ｜ 全体均值=%.4f ｜ 范围 %.4f~%.4f"
              % (_k, sum(_mv) / len(_mv), min(_mv), max(_mv)))

    # ★★ 仿真层交付版：只留 SIM_FIELDS 那 13 个（组长口径）
    agents_sim = []
    for _o in agents:
        _s = {k: _o.get(k) for k in SIM_FIELDS}
        _t0 = T0.get(_o["agent_id"]) or {}
        # ★ 2026-10-07 用户决定：stance_bias 回退到【多议题】版本 —— 议题由 GraphRAG 给出
        #   （本场 = 宠物险争议 / 广告内容低俗 / 性别与价值观争议 三个）
        _s["stance_bias"] = _o.get("stance_bias") or {}
        # ★ A 类 = t0 就动作的真实人物（具名）or t0 已知情者（involvement >= 0.6）
        _s["is_A_type"] = bool(_t0.get("is_named")) or float(_t0.get("involvement") or 0) >= 0.6
        _s["valence_base_0"] = _t0.get("E2_valence_0")
        _s["arousal_base_0"] = _t0.get("E3_arousal_0")
        agents_sim.append(_s)

    # ★★ 交付件：只含仿真层要的 13 个字段
    (out / "D_Agent名单.json").write_text(json.dumps(
        {"spec": "交付物 D —— 仿真层输入（字段口径对齐组长《清洗后event1画像.json》）",
         "fields": SIM_FIELDS,
         "count": len(agents_sim), "agents": agents_sim}, ensure_ascii=False, indent=1), encoding="utf-8")
    # ★★ 中间产物：全量字段（画像侧自留，不交下游）
    _mid = out / "_中间产物"
    _mid.mkdir(parents=True, exist_ok=True)
    (_mid / "D_Agent名单_完整.json").write_text(json.dumps(
        {"spec": "画像侧自留 —— 全量字段（含组队中间产物 / 人口属性 / 溯源）",
         "note": "★ 这是中间产物，不交给仿真层。仿真层只看 D_Agent名单.json（13 字段）",
         "count": len(agents), "agents": agents}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- E / F / G ----------
    (out / "E_t0一次性初始化.json").write_text(json.dumps(t0i, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "F_种子与产物记录.json").write_text(json.dumps(
        {"spec": "交付物 F", "run_manifest": J(R / "downstream" / ("%s_run_manifest.json" % k)),
         "note": "随机性分布在四处，全在 Python 层可锁死；只有 LLM 采样不可复现 —— 复现目标是锁调度层 + 方向标签一致。"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    if gc:
        (out / "G_组队校验报告.json").write_text(json.dumps(gc, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 配置 / 规范 / 参考实现 ----------
    for f in sorted((ROOT / "config").glob("*.json")):
        # ★ 2026-10-07：matrix_v3spec 也必须带 —— 它是 make_personas_v3.py 的
        #   格位规格（CELLS = load_config("matrix_v3spec")）。之前排除它，导致交付包
        #   在 merged 布局下 make_personas 直接 KeyError。
        shutil.copy2(f, out / ("配置_" + f.name))
    for f in sorted((ROOT / "docs").glob("*.md")):
        shutil.copy2(f, out / "规范" / f.name)
    for f in ("t0_init.py", "group_check.py", "post_rules.py", "make_personas_v3.py", "package_delivery.py"):
        p = ROOT / "tools" / f
        if p.exists():
            shutil.copy2(p, out / "参考实现" / f)

    n = len(agents)
    print("交付包已生成 -> %s" % out)
    print("  规模 %d 人 ｜ 格数 %d / 15 ｜ 议题 %d 个" %
          (n, len({(m["role_type"], m["primary_channel"]) for m in agents}), len(kg.get("issues") or [])))
    print("  A7=%s ｜ A2=%s ｜ E4 关注边 %d" %
          (A["A7_dataset_split"], kg.get("event_type"), sum(len(x.get("E4_follows") or []) for x in t0i.get("agents", []))))
    if gc:
        print("  组队校验：%s" % ("通过 ✅" if gc.get("passed") else "未通过 ❌ " + "；".join(gc.get("fails") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
