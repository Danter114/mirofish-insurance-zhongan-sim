#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relevance.py —— 每个人与本次事件的相关度

三个分量，数据来源互不重叠：

    R1 议题相关度   <- GraphRAG 知识图谱的 issues（含 heat）  x  画像库 concern_topics
    R2 帖文相关度   <- 帖子的 topic_list（逐帖）              x  画像库 concern_topics
    R3 图谱位置相关度 <- ★ GraphRAG 真实图遍历（cross_event_index：议题共现 + 实体-议题矩阵 + 实体共现）

    relevance = W1*R1 + W2*R2 + W3*R3

★ 可复现性由三件事保证：
   ① 全部输入是文件，无网络、无随机、无时间依赖
   ② 公式与本文件写死，参数在 config/relevance.json
   ③ 输出带 sha256 指纹；输入文件也各带指纹，一起写进产物记录
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


# ★ 2026-10-13 个人参与价值 Π 的默认角色权重（可被 config/relevance.json :: personal.role_value 覆盖）
ROLE_VALUE = {"监管": 1.00, "媒体": 0.88, "KOL": 0.76, "客户": 0.58, "网民": 0.40}
# Π 各分项默认权重（可被 config :: personal.pi 覆盖）
PI_W = {"fans": 0.30, "influence": 0.25, "role": 0.20, "susceptibility": 0.15, "activity": 0.10}


def _unique_eps(aid, scale=0.000001):
    """由 agent_id 派生的唯一性微扰 —— 保证任意两人的 relevance 必不相同。

    量级 1e-6 ~ 9e-4，小到不会改变排序（除非原本就是并列），但足以打破并列。
    完全可复现：同一个 agent_id 永远得到同一个值。
    """
    h = hashlib.sha256(("rel-eps:" + str(aid)).encode("utf-8")).digest()
    return scale * (1 + int.from_bytes(h[:3], "big") % 900)


# 匹配层级 -> 分值。topic_match 返回的是连续分，这里直接用它的分值。
def _m(score):
    return max(0.0, min(1.0, float(score or 0.0)))


def compute(roster, kg, t0, event_posts, lib, rel_cfg, topiclist_map,
            match_fn, alias_index, leaf2top, leaf2cat9, leaf2concern, sel_cfg,
            pool_by_id, topic_alias=None, topic_to_cat9=None):
    """返回 list[dict]，每人一条。纯函数：同样输入必得同样输出。"""
    W = rel_cfg["weights"]
    issues = kg.get("issues") or []
    # ★ 上游读历史：优先用历史加权热度 heat_hist（缺则退回 heat）
    def _h(it):
        v = it.get("heat_hist")
        return float(v if v is not None else (it.get("heat") or 0.0))
    heat_sum = sum(_h(i) for i in issues) or 1.0
    l2c9 = leaf2cat9 or {}
    tmap = (topiclist_map or {}).get("map") or {}

    # ---- ★★ R2 改用【逐帖归一化主题】—— 三级归一化链（组长 v3 §2.4）
    #   ① topic_hit（逐帖不同）-> ② 议题节点 taxonomy leaf（建图时的 post_issues）
    #   -> ③ interest_topics（画像 47 话题，走 general_bridge）-> 9 大类
    #
    #   ❌ 旧实现用 topic_list：它是【事件级常量】，三条帖一字不差
    #      -> cats 全集相同 -> R2 = Σ eng*s / Σ eng = s  【退化成与人无关的常数】
    _pi   = kg.get("post_issues") or {}
    _gb   = (topic_alias or {}).get("general_bridge") or {}      # 47话题 -> [leaf]
    #   ★ 一个议题可能被多个话题列出（如「广告内容低俗」被 品牌营销与广告伦理 / 道德与公序良俗 /
    #     地域与群体歧视 / 影视综艺 / 明星与粉丝文化 / 音乐与演出 / 短视频与网络红人 都列出）。
    #     旧实现用 setdefault 只留【第一个】，等于按字典序随机挑一个话题 -> 大类随之漂移。
    #     改为【保留全部】，取并集后再映射 9 大类。
    _l2t  = {}
    for _t, _ls in _gb.items():
        for _l in (_ls or []):
            _l2t.setdefault(_l, []).append(_t)
    # ★ topic_to_cat9.json 是【扁平字典】(47话题 -> 9大类)，不是 {map:{...}}
    #   此前按嵌套读会取到空字典 -> cats 为空 -> 全部走兜底 -> 三帖大类趋同
    _t2c  = (topic_to_cat9 or {}).get("map") or topic_to_cat9 or {}
    posts = []
    for tp in (t0.get("initial_posts") or []):
        eng = tp.get("engagement")
        if isinstance(eng, dict):
            e = sum(float(eng.get(k) or 0.0) for k in ("like", "comment", "collect", "repost", "share"))
        else:
            e = float(eng or 0.0)
        _pids = tp.get("post_ids") or []
        _leaves = []
        for _pid in _pids:
            _leaves += _pi.get(_pid) or []
        _tops = set()
        for _l in _leaves:
            _tops.update(_l2t.get(_l) or [])
        cats  = {_t2c[t] for t in _tops if t in _t2c}
        src   = "post_issues->bridge"
        if not cats:
            # 兜底：桥接不上时退回 topic_list 的旧映射（保持向后兼容，不让 R2 掉 0）
            cats = {tmap[t] for t in (tp.get("topic_list") or []) if t in tmap}
            src  = "fallback:topic_list"
        posts.append({"post_ids": _pids, "engagement": e,
                      "topic_list": [str(x) for x in (tp.get("topic_list") or [])],
                      "leaves": list(dict.fromkeys(_leaves)),
                      "interest_topics": sorted(_tops), "cats": cats, "cat_src": src})
    e_sum = sum(p["engagement"] for p in posts) or 1.0

    # ================= ★ R3 真实图遍历（统一图 + BFS 多跳） =================
    # 数据源：cross_event_index（跨 32 场历史事件的图）
    #   节点  I:议题  ｜  E:实体
    #   边    I-I  issue_cooccurrence(63条)  —— 议题共现
    #         E-E  entity_cooccurrence(48条) —— 实体共现
    #         I-E  entity_issue_matrix      —— 实体-议题关联（双向）
    #   ★ I-I via issue_index  —— 出现在同一历史事件里的两个议题
    #   ★ E-E via entity_index —— 出现在同一历史事件里的两个实体
    #   ★ E→事件→I 两跳：实体 e 出现过的历史事件里，还有哪些议题
    import sys as _sys, os as _os
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config as _lc
    _hist = _lc("cross_event_index") or {}
    _gb = (_lc("topic_alias") or {}).get("general_bridge") or {}   # 47话题 -> [议题 leaf]
    _eim = _hist.get("entity_issue_matrix") or {}
    _ico = _hist.get("issue_cooccurrence") or {}
    _eco = _hist.get("entity_cooccurrence") or {}
    _eidx = _hist.get("entity_index") or {}      # 实体 -> [事件ID]
    _iidx = _hist.get("issue_index") or {}       # 议题 -> [事件ID]

    _center = ""
    for _e in (kg.get("entities") or []):
        if (_e.get("kind") or "") in ("company", "product"):
            _center = _e.get("name") or ""
            break
    _S = [it.get("leaf") for it in issues if it.get("leaf")]

    # ---------- 建邻接表 ----------
    _adj = {}
    def _link(a, b):
        _adj.setdefault(a, set()).add(b)
        _adj.setdefault(b, set()).add(a)
    for _k in _ico:                                   # I-I 议题共现
        _a, _, _b = _k.partition(" + ")
        if _a and _b: _link("I:" + _a, "I:" + _b)
    for _k in _eco:                                   # E-E 实体共现
        _a, _, _b = _k.partition(" + ")
        if _a and _b: _link("E:" + _a, "E:" + _b)
    for _e, _iv in _eim.items():                      # I-E 实体-议题
        for _i, _c in (_iv or {}).items():
            if _c > 0: _link("E:" + _e, "I:" + _i)
    # ★ via issue_index：同一历史事件里的议题两两相连
    _by_ev_i = {}
    for _i, _evs in _iidx.items():
        for _ev in (_evs or []): _by_ev_i.setdefault(_ev, []).append(_i)
    for _ev, _lst in _by_ev_i.items():
        for _x in range(len(_lst)):
            for _y in range(_x + 1, len(_lst)): _link("I:" + _lst[_x], "I:" + _lst[_y])
    # ★ via entity_index：同一历史事件里的实体两两相连
    _by_ev_e = {}
    for _e, _evs in _eidx.items():
        for _ev in (_evs or []): _by_ev_e.setdefault(_ev, []).append(_e)
    for _ev, _lst in _by_ev_e.items():
        for _x in range(len(_lst)):
            for _y in range(_x + 1, len(_lst)): _link("E:" + _lst[_x], "E:" + _lst[_y])

    # ---------- 从本场议题 S 出发 BFS ----------
    MAX_HOP = 3
    _dist = {("I:" + _x): 0 for _x in _S}
    _frontier = list(_dist)
    for _hop in range(1, MAX_HOP + 1):
        _nxt = []
        for _n in _frontier:
            for _nb in _adj.get(_n, ()):
                if _nb not in _dist:
                    _dist[_nb] = _hop
                    _nxt.append(_nb)
        _frontier = _nxt
        if not _frontier: break
    _HOP_SCORE = {0: 1.00, 1: 0.70, 2: 0.45, 3: 0.25}
    _n_hop = {_q: sum(1 for v in _dist.values() if v == _q) for _q in range(MAX_HOP + 1)}
    #   ★ 两跳：实体 -> 历史事件 -> 议题（entity_index ∩ issue_index）
    _e2i = {}                                     # 实体 -> 它出现的事件里的议题集合
    for _e, _evs in _eidx.items():
        _evset = set()
        for _ev in (_evs or []): _evset.update(_by_ev_i.get(_ev) or [])
        _e2i[_e] = _evset

    def _graph_score(itopics, plays, _slot):
        """★ 真实图遍历：拿这个人自己的 interest_topics 去图上走
           话题 t --general_bridge--> 议题集合 I(t) --BFS--> 到本场议题的最短跳数
           返回 (综合分, 议题跳数分, 实体覆盖分, 实体边分)
        """
        _nz = [(k, float(v or 0)) for k, v in (itopics or {}).items() if k != "其他" and (v or 0) > 0]
        _sw = sum(w for _, w in _nz) or 1.0
        g_hop = 0.0
        g_mat = 0.0
        for _tk, _w in _nz:
            _iss = _gb.get(_tk) or []
            if not _iss: continue
            _best = min((_HOP_SCORE.get(_dist.get("I:" + _i, 99), 0.0) for _i in _iss), default=0.0)
            g_hop += _w * _best
            g_mat += _w * max((min(1.0, (_eim.get(_center) or {}).get(_i, 0) / 5.0) for _i in _iss), default=0.0)
        g_hop /= _sw
        g_mat /= _sw
        # ★ 兜底：兴趣话题都桥接不上时，用槽位绑定的议题（定槽位已保证绑到本场议题）
        if g_hop <= 0.0:
            _d = _dist.get("I:" + (_slot or ""), None)
            g_hop = _HOP_SCORE.get(_d, 0.0) if _d is not None else 0.0
            _fallback[0] += 1
        # ★ 实体侧：具名实体在图上到本场议题的最短跳数 + 它经历史事件能覆盖到哪些议题
        g_ent = 0.0
        if plays:
            _nd = _dist.get("E:" + plays, None)
            if _nd is not None:
                g_ent = _HOP_SCORE.get(_nd, 0.0)
            _cov = _e2i.get(plays) or set()
            if _cov:
                _hit = sum(1 for _i in _cov if _i in _S)
                _near = sum(1 for _i in _cov if _dist.get("I:" + _i, 99) == 1)
                g_ent = max(g_ent, min(1.0, (_hit * 1.0 + _near * 0.7) / 3.0))
            _w2 = (_eim.get(_center) or {})
            _ew = (_eco.get(_center + " + " + plays) or _eco.get(plays + " + " + _center) or 0)
            g_ent = max(g_ent, min(1.0, _ew / 3.0))
        return max(g_hop, g_ent), g_hop, g_mat, g_ent
    # ---- R3 的结构档位（可配置，见 config/relevance.json :: graph_tier）
    _fallback = [0]
    tier = rel_cfg["graph_tier"]

    # ★ 全体粉丝数最大值（Π 的 log 归一化分母）
    _max_fan = max([float(x.get("followers") or 0) for x in roster] or [1.0])
    out = []
    for m in roster:
        aid = m["agent_id"]
        p = pool_by_id.get(aid) or {}
        # ★★ 修：cat_weights 是 load_pool() 运行时算的，画像库里没有 ——
        #   缺了就让 R2（权重 0.30）恒为 0，整个分量是死的（实测 0/102 非零）。
        #   这里按 interest_topics --leaf2cat9--> 9 大类 现算一份。
        cw = p.get("cat_weights") or {}
        if not cw:
            #   ★ 必须用 topic_to_cat9（47 话题 -> 9 大类），不是 leaf2cat9（议题 -> 9 大类）——
            #     后者键是议题 leaf，画像库的 interest_topics 一个都映射不上（实测 0/47 命中）。
            _t9 = topic_to_cat9 or {}
            _t9 = _t9.get("map") or _t9          # 兼容 {map:{...}} 与扁平字典两种形态
            _it0 = p.get("interest_topics") or m.get("interest_topics") or {}
            for _tk, _wv in _it0.items():
                _cc = _t9.get(_tk)
                if _cc:
                    cw[_cc] = cw.get(_cc, 0.0) + float(_wv or 0)
        tot_w = sum(cw.values()) or 1.0

        # ---------- R1 议题相关度 ----------
        num = 0.0
        ev1 = []
        for it in issues:
            leaf = it.get("leaf")
            h = _h(it)
            s, lvl, _e = match_fn(leaf, leaf2top.get(leaf), p, alias_index,
                                  sel_cfg, leaf2top, l2c9, leaf2concern)
            num += h * _m(s)
            if s > 0:
                ev1.append("%s(%.2f x %.2f)" % (leaf, h, s))
        R1 = num / heat_sum

        # ---------- R2 帖文相关度 ----------
        num2 = 0.0
        ev2 = []
        for po in posts:
            cats = po["cats"]
            if not cats:
                continue
            s = sum(cw.get(c, 0.0) for c in cats) / tot_w
            num2 += po["engagement"] * s
            if s > 0:
                ev2.append("%s:%.2f" % ("/".join(sorted(cats)), s))
        R2 = num2 / e_sum

        # ---------- R3 图谱位置相关度 ----------
        role = m.get("role_type")
        base = float(tier.get(role, 0.0))
        bound = bool(m.get("plays_entity"))
        bonus_bind = float(tier["_binding_bonus"]) if bound else 0.0
        bonus_post = float(tier["_post_bonus"]) * min(len(m.get("plays_post_ids") or []), 3)
        # ★★ 真实图遍历得分（0~1）：议题共现距离 / 实体-议题矩阵 / 实体共现边
        g_graph, g_iss, g_mat, g_edg = _graph_score(p.get("interest_topics") or m.get("interest_topics"), m.get("plays_entity"), m.get("issue_leaf"))
        #   角色档位（先验）与图位置（实际）各占一半
        R3 = max(0.0, min(1.0, 0.5 * base + 0.5 * g_graph + bonus_bind + bonus_post))
        ev3 = "%s ｜ 图:议题%.2f/矩阵%.2f/边%.2f -> %.2f%s%s" % (
            role, g_iss, g_mat, g_edg, g_graph,
            " +具名直绑(%s)" % m["plays_entity"] if bound else "",
            " +帖子关联%d" % len(m.get("plays_post_ids") or []) if m.get("plays_post_ids") else "")

        # ---------- 个人参与价值 Π（2026-10-13 新增，组长要求）----------
        #   为什么：R1/R2/R3 都是离散档位，几百人只有几个取值 -> 大量并列
        #   （实测 EVT-010 有 128 人同为 0.135，全局重复率 19.1%）。
        #   Π 是连续量，把同档位的人区分开：粉丝数(log归一) / 影响力 / 角色 / 传染易感性 / 活跃度
        _pc = (rel_cfg.get("personal") or {})
        _piw = dict(PI_W); _piw.update(_pc.get("pi") or {})
        _rv = dict(ROLE_VALUE); _rv.update(_pc.get("role_value") or {})
        _fan = float(m.get("followers") or 0)
        _fan_norm = (math.log1p(_fan) / math.log1p(_max_fan)) if _max_fan > 1 else 0.0
        _inf = float(m.get("influence_weight") or 0.0)
        _susp = float(m.get("contagion_susceptibility") or 0.5)
        _act = min(1.0, len(m.get("active_hours") or []) / 12.0)
        PI = (_piw["fans"] * _fan_norm + _piw["influence"] * _inf
              + _piw["role"] * float(_rv.get(role, 0.40))
              + _piw["susceptibility"] * _susp + _piw["activity"] * _act)
        PI = max(0.0, min(1.0, PI))
        # ---------- 融合 ----------
        G = W["R1"] * R1 + W["R2"] * R2 + W["R3"] * R3
        if _pc.get("enabled", True):
            _wg = float(_pc.get("weight_graph", 0.72)); _wp = float(_pc.get("weight_personal", 0.28))
            rel = _wg * G + _wp * PI + _unique_eps(aid, float(_pc.get("unique_epsilon", 0.000001)))
        else:
            rel = G
        out.append({
            "agent_id": aid, "seat": m.get("seat"), "role_type": role,
            "primary_channel": m.get("primary_channel"),
            "issue_leaf": m.get("issue_leaf"), "slot_id": m.get("slot_id"),
            "relevance": round(rel, 6),
            "components": {"R1_topic": round(R1, 6), "R2_post": round(R2, 6), "R3_graph": round(R3, 6),
                               "G_graph": round(G, 6), "PI_personal": round(PI, 6)},
            "evidence": {"issues": ev1, "posts": ev2, "graph": ev3},
            "graph_traverse": {"center": _center, "n_S": len(_S), "n_graph_nodes": len(_adj), "n_reachable": len(_dist), "hop_dist": _n_hop,
                               "issue": round(g_iss, 4), "matrix": round(g_mat, 4), "edge": round(g_edg, 4)},
        })
    import sys as _s2
    print("  ★ R3 图遍历(BFS)：中心=%s ｜ 起点 %d 议题 ｜ 图节点 %d ｜ 图边 %d ｜ 可达 %d ｜ 跳数分布 %s ｜ 兴趣桥接失败走兜底 %d 人"
          % (_center, len(_S), len(_adj), sum(len(v) for v in _adj.values()) // 2, len(_dist), _n_hop, _fallback[0]), file=_s2.stderr)
    out.sort(key=lambda x: (-x["relevance"], x["agent_id"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def digest(records):
    lines = []
    for r in sorted(records, key=lambda x: x["agent_id"]):
        lines.append(json.dumps({k: r[k] for k in ("agent_id", "relevance", "components")},
                                ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()
