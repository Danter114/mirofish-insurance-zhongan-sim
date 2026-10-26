#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
downstream.py —— 向下游节点（仿真层）输出三件交付物（组长 3.0）

组长 3.0 写死了仿真层要的三样东西，这个文件只做这三件事：

    输出 1  t0 状态快照       —— 具名实体 / 议题节点 / 语义关系边 + 第 0 轮初始动作
    输出 2  500 人组队名单     —— 恰好 500 行，一人一条、一人一渠道
    输出 3  随机种子与产物记录 —— 种子 / 输入指纹 / 产物 sha256 / 复现命令

以及一件「让仿真层能直接跑」的事：**把 initial_posts 的 poster_agent_id 回填**。
组长 3.1 ② 给了代码依据：T0 注入读的只有 poster_agent_id + content
（MiroFish 的 run_parallel_simulation.py:1172-1207），所以这两个字段必须对得上。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import manifest as mf
import stance_value as sv
from prng import DeterministicRNG


def resolve_initial_posts(kg, roster):
    """给每条 t0 初始动作找扮演它的那个 agent（具名实体 1:1 绑定，组长 3.4 ①）。

    解析顺序：
      ① 名单里有没有人 plays_post_ids 命中这条动作的 post_ids
      ② 退一步：有没有人 plays_entity == 发帖人昵称
      ③ 都不行 -> 如实标 unbound（不编一个 agent 上去）
    """
    by_post, by_name = {}, {}
    for p in roster:
        for pid in (p.get("plays_post_ids") or []):
            by_post[pid] = p
        if p.get("plays_entity"):
            by_name[p["plays_entity"]] = p
    out = []
    for ip in kg.get("initial_posts") or []:
        row = dict(ip)
        hit, how = None, "unbound"
        for pid in ip.get("post_ids") or []:
            if pid in by_post:
                hit, how = by_post[pid], "bound_by_post_id"
                break
        if hit is None and ip.get("poster_name") in by_name:
            hit, how = by_name[ip["poster_name"]], "bound_by_name"
        if hit is not None:
            row["poster_agent_id"] = hit["agent_id"]
            row["poster_slot_id"] = hit["slot_id"]
            row["poster_role_slot"] = hit["role_type"]
            row["plays_entity"] = hit.get("plays_entity")
            # ★ 扮演者的主渠道与原帖渠道不一致时如实标注：
            #   来源是「该 (角色 x 渠道) 组合不在 20 有效组合矩阵内」，
            #   槽位按组长 2.6 #2 落回了合法格（见 out/procurement/matrix_review.csv）。
            #   仿真层要按这个标记决定：是照原渠道还原，还是按扮演者自己的渠道发。
            row["channel_mismatch"] = (hit.get("primary_channel") != ip.get("channel"))
            if row["channel_mismatch"]:
                row["channel_note"] = ("原帖渠道 %s 不在该角色的有效组合内，已落回合法格 %s；"
                                       "还原时请确认按哪个渠道发"
                                       % (ip.get("channel"), hit.get("primary_channel")))
        row["binding"] = how
        if how == "unbound":
            row["unbound_reason"] = ("该发帖人没有对应的 1:1 槽位（可能被角色配额挤掉，"
                                     "或没有可用的同角色同渠道画像）—— 仿真层这一步无法还原，"
                                     "请按 unbound 统计，不要编一个 agent 上去")
        out.append(row)
    return out


def _bin_counter(roster):
    return Counter(p.get("stance_bin") for p in roster if p.get("stance_bin"))


def attach_t0_stance(roster, kg, seed_info, cfg):
    """给 500 人名单里的每个人定 t0 立场值（连续量 ∈ [-1,+1]）。就地写回 roster。

    ★ extracted 覆盖 generated（config/stance.json 的 provenance.rule）：
      · 有真实帖文的人 —— plays_post_ids 命中 t0 的 initial_posts
            -> 用 build_kg 标定出来的值；committed=True（已公开表态，刚性上调、步长下调）
      · 其余人 —— 按槽位 side + 画像 stance_bias 采样
            -> 可复现（种子固定即结果固定），但不等于事实

    为什么要在这里做而不是在建图里做：建图只知道「谁发了帖」，不知道「谁上场」。
    500 人名单是组队产物，所以「全员 t0 立场」只能在名单定下来之后才算得出来。
    但**有帖文的那部分值来自建图**，这里只是把它接上，不重新标。
    """
    by_pid = {}
    for ip in kg.get("initial_posts") or []:
        for pid, v in (ip.get("stance_per_post") or {}).items():
            if v is not None:
                by_pid[pid] = v

    master = int((seed_info or {}).get("main") or 0)
    c = cfg.get("committed") or {}
    n_ex = n_gen = 0

    for p in roster:
        vals = [by_pid[pid] for pid in (p.get("plays_post_ids") or []) if pid in by_pid]
        if vals:
            v = round(sum(vals) / len(vals), 4)
            p["stance_value"] = v
            p["stance_bin"] = sv.to_bin(v, cfg)
            p["stance_provenance"] = "extracted"
            p["stance_confidence"] = "high"
            # ★ 已公开表态 -> 承诺一致性成本：刚性上调、单轮步长下调。
            #   这里只给【增量】，不给绝对值 —— 刚性基数是画像库的字段（stance_rigidity），
            #   建图侧不掌握它，凭空写一个数会把画像库的口径覆盖掉。
            p["stance_committed"] = True
            p["stance_rigidity_bonus"] = c.get("rigidity_bonus", 0.20)
            p["stance_step_cap_factor"] = c.get("step_cap_factor", 0.6)
            n_ex += 1
        else:
            v = sv.sample_from_slot(p.get("side"), p.get("stance_bias"), p.get("role_type"),
                                    cfg, DeterministicRNG(master, "t0stance:" + str(p.get("agent_id"))))
            p["stance_value"] = v
            p["stance_bin"] = sv.to_bin(v, cfg)
            p["stance_provenance"] = "generated"
            p["stance_confidence"] = "designed"     # 采样值：是按设计给的，不是观测到的
            p["stance_committed"] = False
            n_gen += 1

    vals = [p["stance_value"] for p in roster if p.get("stance_value") is not None]
    m = sum(vals) / len(vals) if vals else 0.0
    sd = ((sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5) if len(vals) > 1 else 0.0
    t0 = kg.get("t0_snapshot") or {}
    return {
        "headcount": len(roster),
        "by_bin": dict(_bin_counter(roster)),
        "by_provenance": {"extracted": n_ex, "generated": n_gen},
        "mean": round(m, 4),
        "polarization": round(sd, 4),
        "min": round(min(vals), 4) if vals else None,
        "max": round(max(vals), 4) if vals else None,
        # 与「t0 房间里已经在说话的人」对比：那批人是真实帖文标出来的，这批是名单全量
        "t0_speakers_axis": (t0.get("t0_stance_mix") or {}).get("mean"),
        "note": ("立场 = 对事件主体的公开行为承诺强度 ∈ [-1,+1]，反对/中立/支持只是它的分档。"
                 "extracted = 有真实帖文标定；generated = 按槽位采样。"
                 "均值看整体漂移，极化度(std)看分化 —— 均值不动而极化上升是最危险的信号。"),
    }


def build_t0_snapshot(kg, roster, seed_info, stance_cfg=None, stance_stats=None):
    """输出 1：t0 状态快照（推演前只能说快照，不能说脉络 —— 组长 3.1 命名约定）。"""
    initial = resolve_initial_posts(kg, roster)
    named = kg.get("named_entities") or []
    return {
        "artifact": "t0_snapshot",
        "artifact_version": "1.0",
        "event_id": kg["event_id"],
        "event_title": kg.get("title"),
        "event_type": kg.get("event_type"),
        "event_type_dist": kg.get("event_type_dist"),
        "t0": kg.get("t0"),
        "visible_until": kg.get("visible_until"),
        "t0_policy": kg.get("t0_policy"),
        "leakage_guard": kg.get("leakage_guard"),
        "note": "推演前没有『事件脉络』，只有 t0 状态快照。T0 是数据驱动的还原点，不是生成。",
        # ---- 三类输出（组长 3.1）
        "named_entities": named,
        "issues": [{"issue_id": i["id"], "leaf": i["leaf"], "top": i["top"],
                    "heat": i["heat"], "mentions": i["mentions"],
                    "channel_dist": i.get("channel_dist"),
                    "engagement": i.get("engagement")} for i in kg.get("issues", [])],
        "relations": kg.get("relations", []),
        # ---- ★ 首发发帖者类型 + t0 房间姿态分布（组长要求，传给推理演化层）
        "ignition": (kg.get("t0_snapshot") or {}).get("ignition"),
        "t0_role_mix": (kg.get("t0_snapshot") or {}).get("t0_role_mix"),
        "poster_stance_legend": {
            "self_interest": "当事人型 —— 帖子里在讲自己受损 / 自己维权 -> 角色取客户",
            "commentary": "围观型 —— 只在评论事件、宣泄情绪 -> 角色取网民",
            "institutional": "机构型 —— 媒体 / 监管 / KOL / 专业背书方，按账号身份定角色",
            "confidence": "high=有显式证据；low=两侧都没命中，按基线判围观型（请按低置信处理）",
        },
        "initial_posts": initial,
        "initial_posts_stats": {
            "count": len(initial),
            "bound": sum(1 for x in initial if x.get("poster_agent_id")),
            "unbound": sum(1 for x in initial if not x.get("poster_agent_id")),
            "merging_rule": "同一作者同一渠道的多帖合并为一个发帖动作；同一作者多渠道视作多个用户",
        },
        # ---- ★ t0 立场：推理演化层的演化起点（自研层，见 config/stance.json）
        "t0_stance": {
            "model": kg.get("stance_model"),
            "legend": {
                "stance_value": "连续量 ∈ [-1,+1]：对事件主体的公开行为承诺强度。"
                                "-1 = 完全站在质疑方；0 = 无净方向；+1 = 完全站在辩护方",
                "stance_bin": "三值分档：value<=-0.35 反对 / value>=+0.35 支持 / 其间中立。"
                              "滞回：一旦表态，要回到 ±0.25 以内才离开该档 -> 立场有惯性",
                "stance_provenance": "extracted=有真实帖文标定；generated=按槽位 side 采样",
                "stance_committed": "t0 已公开发帖 = 已表态。刚性 +stance_rigidity_bonus，"
                                    "单轮步长 x stance_step_cap_factor（承诺一致性成本）",
                "not_emotion": "立场是【认知】（谁有理），情绪是【情感】（多生气）。"
                               "stance=-0.9 且 arousal=0.1 是「心里判了死刑但懒得说」",
            },
            "mix": stance_stats,
            "members": [{"agent_id": p.get("agent_id"), "role_type": p.get("role_type"),
                         "side": p.get("side"), "slot_id": p.get("slot_id"),
                         "stance_value": p.get("stance_value"), "stance_bin": p.get("stance_bin"),
                         "stance_provenance": p.get("stance_provenance"),
                         "stance_confidence": p.get("stance_confidence"),
                         "stance_committed": p.get("stance_committed"),
                         "stance_rigidity_bonus": p.get("stance_rigidity_bonus"),
                         "stance_step_cap_factor": p.get("stance_step_cap_factor")}
                        for p in roster],
        },
        # ---- 房间里的三类人怎么分布，下游据此定初始立场/效价/动作阈值档位
        "speakers_detail": (kg.get("t0_snapshot") or {}).get("speakers_detail"),
        "heat_model": kg.get("heat_model"),
        "seed": seed_info,
        "source_kg": "kg/event_kg_%s.json" % kg["event_id"],
    }


def build_team_package(kg, roster, slots, seed_info):
    """输出 2：500 人组队名单（下游视角，带分组统计，便于仿真层直接建 actor）。"""
    by_role = Counter(p["role_type"] for p in roster)
    by_ch = Counter(p["primary_channel"] for p in roster)
    by_slot = Counter(p["slot_id"] for p in roster)
    plays = [{"agent_id": p["agent_id"], "plays_entity": p["plays_entity"],
              "plays_kind": p["plays_kind"], "post_ids": p["plays_post_ids"],
              "role_type": p["role_type"], "primary_channel": p["primary_channel"]}
             for p in roster if p.get("plays_entity")]
    return {
        "artifact": "team_roster",
        "artifact_version": "1.0",
        "event_id": kg["event_id"],
        "event_type": kg.get("event_type"),
        "headcount": len(roster),
        "rule": "一人一条、一人一渠道；角色 x 渠道必须落在 20 个有效组合内",
        "counts": {"by_role": dict(by_role), "by_channel": dict(by_ch),
                   "slots": len(by_slot), "named_bound": len(plays),
                   # ★ 议题是"怎么匹配上的"：exact / same_top / fuzzy_alias / fuzzy_char / none
                   "by_topic_match_level": dict(Counter(p.get("topic_match_level") for p in roster))},
        "topic_match_level_legend": {
            "exact": "画像的 interest_topics 直接命中该议题叶子（或经 strong 映射等价归一）-> 1.00",
            "same_top": "与某个关注点同属一个 taxonomy 大类 -> 0.60",
            "fuzzy_alias": "经 config/topic_alias.json 的 weak 映射命中（语义接近）-> 0.45",
            "fuzzy_char": "字符 bigram 相似度兜底 -> 0.15~0.45",
            "none": "没匹配上（仍可能按配额入选，但议题相关性为 0）",
            "named_slot": "具名实体槽位，不按议题过滤",
        },
        "quota": dict(by_role),
        "named_entity_bindings": plays,
        "members": roster,
        "seed": seed_info,
        "sampling": (seed_info or {}).get("sampling"),
    }


def export_all(*, outdir, kg, roster, slots, seed_info, manifest_obj, stance_cfg=None):
    """把三件交付物写进 outdir/downstream/，并返回产物清单（给 manifest 用）。"""
    eid = kg["event_id"]
    ds = Path(outdir) / "downstream"
    ds.mkdir(parents=True, exist_ok=True)
    written = []

    if stance_cfg is None:
        from common import load_config
        stance_cfg = load_config("stance")
    # ★ 先定全员 t0 立场（extracted 覆盖 generated），再组装快照与名单 —— 两份产物同源
    stance_stats = attach_t0_stance(roster, kg, seed_info, stance_cfg)

    snap = build_t0_snapshot(kg, roster, seed_info, stance_cfg, stance_stats)
    p1 = ds / ("%s_t0_snapshot.json" % eid)
    p1.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append(mf.file_record(p1))
    written[-1]["artifact"] = "输出1 t0 状态快照"
    written[-1]["rows"] = len(snap["issues"])

    team = build_team_package(kg, roster, slots, seed_info)
    p2 = ds / ("%s_roster.json" % eid)
    p2.write_text(json.dumps(team, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append(mf.file_record(p2))
    written[-1]["artifact"] = "输出2 500 人组队名单（含画像参数，仿真层直接建 actor）"
    written[-1]["rows"] = len(roster)

    p3 = ds / ("%s_roster.csv" % eid)
    with open(p3, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("agent_id,seat,role_type,primary_channel,subtype,slot_id,issue_leaf,side,"
                 "tier,purity_group,purity_fit,plays_entity,influence_weight,"
                 "followers,active_level,emotional_stability,extraversion,active_hours,score,"
                 "stance_value,stance_bin,stance_provenance\n")
        for p in roster:
            fh.write("%s,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                p["agent_id"], p["seat"], p["role_type"], p["primary_channel"],
                p["subtype"] or "", p["slot_id"], p["issue_leaf"] or "", p["side"], p["tier"],
                p["purity_group"], str(p["purity_fit"]).lower(),
                (p["plays_entity"] or "").replace(",", " "), p["influence_weight"], p.get("followers"),
                p.get("active_level"), p.get("emotional_stability"), p.get("extraversion"),
                "|".join(str(h) for h in (p["active_hours"] or [])), p["score"],
                p.get("stance_value"), p.get("stance_bin"), p.get("stance_provenance")))
    written.append(mf.file_record(p3))
    written[-1]["artifact"] = "输出2 500 人组队名单（表格版）"
    written[-1]["rows"] = len(roster)

    # 产物记录本身也要写进去并进清单（输出 3）
    p4 = ds / ("%s_run_manifest.json" % eid)
    mf.write(p4, manifest_obj)
    written.append(mf.file_record(p4))
    written[-1]["artifact"] = "输出3 随机种子与产物记录"

    return written, snap, team
