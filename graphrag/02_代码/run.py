#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py —— ★ 仿真层上游固定流程的唯一入口（建图 -> 定槽位 -> 选 500 人 -> 交付下游）

    输入：事件集 JSON（采集侧扁平格式，或已适配的标准事件包）
         画像库 JSON（画像库侧的交付物，契约见 config/persona_contract.json）
    输出：① t0 状态快照   ② 500 人组队名单   ③ 随机种子与产物记录
          （三件套落在 <out>/downstream/，供仿真层直接读取）

    python run.py --event examples/event1_pilot_last_version.json \
                  --personas out/persona_library_mock.json \
                  --seed 20261002 --out out

这条流程是**死代码**：没有 LLM、没有联网、没有人工判断，同样的输入 + 同样的种子
必得同样的产物。全流程纯标准库，数据不出域。

固定流程（每一步都落盘，可单独复查）：
    [0] 读配置 + 适配事件集        -> out/packs/event_pack_<event>.json
    [1] ① 建图 + t0 状态快照       -> out/kg/event_kg_<event>.json / .cypher
                                      out/holdout/holdout_<event>.json
    [2] ② 定槽位（20 有效组合）     -> out/slots/slots_<event>.json / .csv
                                      out/procurement/slot_gap.csv
    [3] ③ 选 500 人（种子可复现）   -> out/selection/roster_<event>.json / .csv
                                      out/selection/cooldown_state.json
    [4] 交付下游三件套             -> out/downstream/<event>_t0_snapshot.json
                                      out/downstream/<event>_roster.json / .csv
                                      out/downstream/<event>_run_manifest.json
    [5] 自检                       -> out/report/verify_<event>.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from common import load_config, write_json, largest_remainder  # noqa: E402
from extractor import build_extractor                          # noqa: E402
from build_kg import KGBuilder                                 # noqa: E402
from derive_slots import (acceptance, auto_total, derive, feasibility_gate,  # noqa: E402
                           legality_gate, )
from persona_io import load_pool, validate_pool                # noqa: E402
from select_agents import make_streams, run_event              # noqa: E402
import manifest as mf                                          # noqa: E402
import downstream as ds                                        # noqa: E402

ROOT = Path(__file__).resolve().parent
ROLES = ["网民", "客户", "KOL", "媒体", "专业背书方", "监管"]
CHANNELS = ["微博", "抖音", "小红书", "黑猫投诉", "新闻门户", "监管通报"]


# ---------------------------------------------------------------- 工具
def export_cypher(kg, slots, path):
    L = ["// 由「仿真层上游项目代码（建图、定槽位、选人）」自动生成",
         "// provenance: extracted=真实抽取 / generated=规则生成 / emergent=仿真涌现",
         "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;",
         "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Slot) REQUIRE s.id IS UNIQUE;",
         "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Issue) REQUIRE i.id IS UNIQUE;"]
    for e in kg["entities"]:
        L.append("MERGE (e:Entity {id:'%s'}) SET e.name='%s', e.type='%s', e.role=%s, "
                 "e.channel=%s, e.bindable=%s, e.is_impostor=%s, e.provenance='extracted';"
                 % (e["id"], e["name"], e["type"],
                    ("'%s'" % e["role"]) if e.get("role") else "null",
                    ("'%s'" % e["channel"]) if e.get("channel") else "null",
                    str(bool(e.get("bindable"))).lower(), str(bool(e["is_impostor"])).lower()))
    for i in kg["issues"]:
        L.append("MERGE (i:Issue {id:'%s'}) SET i.leaf='%s', i.top='%s', i.heat=%s, "
                 "i.controversy=%s, i.provenance='extracted';"
                 % (i["id"], i["leaf"], i["top"], i["heat"], i["controversy"]))
    for r in kg["relations"]:
        L.append("MATCH (a:Entity {id:'%s'}),(b:Entity {id:'%s'}) MERGE (a)-[:%s "
                 "{fact:'%s', source_url:'%s', provenance:'extracted'}]->(b);"
                 % (r["from"], r["to"], r["type"], r["fact"].replace("'", ""), r["source_url"]))
    for s in slots:
        L.append("MERGE (s:Slot {id:'%s'}) SET s.role='%s', s.issue='%s', s.side='%s', "
                 "s.channel='%s', s.tier='%s', s.headcount=%d, s.bound_entity=%s, "
                 "s.provenance='generated';"
                 % (s["slot_id"], s["role_type"], s["issue"], s["side"], s["channel"], s["tier"],
                    s["headcount"],
                    ("'%s'" % s["bound_entity"]) if s["bound_entity"] else "null"))
    Path(path).write_text("\n".join(L), encoding="utf-8")


def make_rounds_plan(total_rounds=30):
    """只含轮次安排，不含任何真实历史数据 —— 那是 t0 之后的信息，不能泄漏进推演。"""
    plan, t, rnd = [], 0.0, 0
    for n, step, phase in [(12, 2, "爆发期"), (12, 6, "扩散期"), (6, 12, "长尾期")]:
        for _ in range(n):
            rnd += 1
            if rnd > total_rounds:
                break
            plan.append({"round": rnd, "phase": phase, "advance_hours": step,
                         "start_hour": round(t, 1), "end_hour": round(t + step, 1)})
            t += step
    return plan


def slots_csv(slots, path):
    head = ("slot_id,role_type,issue_leaf,side,channel,tier,headcount,bound_entity,bound_role,"
            "bound_channel,heat,issue_weight")
    rows = []
    for s in slots:
        rs = s.get("reason") or {}
        rows.append("%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s" % (
            s["slot_id"], s["role_type"], s.get("issue_leaf") or "", s["side"], s["channel"],
            s["tier"], s["headcount"], s["bound_entity"] or "", s.get("bound_role") or "",
            s.get("bound_channel") or "", rs.get("heat", ""), rs.get("issue_weight", "")))
    Path(path).write_text(head + "\n" + "\n".join(rows), encoding="utf-8")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="仿真层上游固定流程：建图 -> 定槽位 -> 选 500 人")
    ap.add_argument("--event", required=True, help="事件集 JSON（采集侧扁平格式或标准事件包）")
    ap.add_argument("--personas", required=True, help="画像库 JSON")
    ap.add_argument("--out", default="out")
    ap.add_argument("--event-id", default=None, help="只跑指定的 event_id（默认全部）")
    ap.add_argument("--seed", type=int, default=None, help="主随机种子（默认读 config/selection.json）")
    ap.add_argument("--t0-mode", default=None, choices=["strict", "day", "window", "days"])
    ap.add_argument("--t0-window-hours", type=float, default=None)
    ap.add_argument("--t0-days", type=int, default=None, help="days 模式：覆盖 t0 所在自然日起 N 个自然日（2 = Day0+Day1）")
    ap.add_argument("--extractor", default="rule", choices=["rule", "llm"])
    ap.add_argument("--channel-baseline", type=float, default=None,
                    help="R2.8 渠道基线：越大越向『角色阵地权重』靠拢、越小越被本场 t0 的帖子渠道带偏")
    ap.add_argument("--channel-boost", default=None,
                    help="渠道偏置，形如 微博=3,抖音=3（对 R2.8 的渠道权重做乘性调整）")
    ap.add_argument("--r_max", type=float, default=None, help="规模公式：具名实体占总人数的最小比（默认 1/8）")
    ap.add_argument("--n_min", type=int, default=None, help="规模公式：小事件下限（默认 50）")
    ap.add_argument("--n_cap", type=int, default=None, help="规模公式：上限（默认 1200）")
    ap.add_argument("--total", default=None,
                    help="单场人数。auto = 按 t0 火热程度自动（上限 600）；整数 = 手动；不传 = 读配额 500")
    ap.add_argument("--temperature", type=float, default=None,
                    help="概率抽样的温度 T（0=确定性最优，默认读 config/selection.json）")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    outdir = Path(args.out)
    if not outdir.is_absolute():
        outdir = (ROOT / outdir).resolve()
    log_path = Path(args.report) if args.report else (outdir / "report" / "run_log.txt")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = open(log_path, "w", encoding="utf-8")
    P = print

    run_id = "%s-%s" % (datetime.now().strftime("%Y%m%dT%H%M%S"),
                        hashlib.sha1(str(random.random()).encode()).hexdigest()[:6])

    tax, rules, onto = load_config("taxonomy"), load_config("rules"), load_config("ontology")
    slots_cfg, sel_cfg = load_config("slots"), load_config("selection")
    alias_cfg = load_config("topic_alias")    # 画像库话题 <-> taxonomy 的对齐底稿
    # ★ 小型样本：把配额按比例缩放到 --total
    if args.channel_boost:
        slots_cfg = dict(slots_cfg)
        _th = dict(slots_cfg.get("thresholds") or {})
        _b = {}
        for kv in args.channel_boost.split(","):
            if "=" in kv:
                k, v = kv.split("=", 1); _b[k.strip()] = float(v)
        _th["channel_bias"] = _b
        slots_cfg["thresholds"] = _th
        P("    [0] 渠道偏置 channel_bias: %s" % _b)
    if args.channel_baseline is not None:
        slots_cfg = dict(slots_cfg)
        _th = dict(slots_cfg.get("thresholds") or {})
        _old = _th.get("channel_baseline", 0.3)
        _th["channel_baseline"] = args.channel_baseline
        slots_cfg["thresholds"] = _th
        P("    [0] 渠道基线 channel_baseline: %.2f -> %.2f" % (_old, args.channel_baseline))
    if args.total and str(args.total).strip().lower() not in ("auto", "a"):
        q = slots_cfg["quota"]
        # ★ 2026-10-07：--total 现在可能是字符串（"auto"/"100"），必须显式转 int，
        #   否则 largest_remainder 内部 `total <= 0` 会 TypeError: str <= int
        _T = int(args.total)
        scaled = largest_remainder(q, _T)
        scaled = {r: scaled.get(r, 0) for r in q}
        P("    [0] 配额缩放：%s -> %s（合计 %d）"
          % (q, scaled, sum(scaled.values())))
        slots_cfg = dict(slots_cfg)
        slots_cfg["quota"] = scaled
    if args.temperature is not None:
        sel_cfg.setdefault("sampling", {})["temperature"] = args.temperature
    contract = load_config("persona_contract")
    if args.t0_mode:
        slots_cfg.setdefault("t0_policy", {})["mode"] = args.t0_mode
    if args.t0_window_hours is not None:
        slots_cfg.setdefault("t0_policy", {})["window_hours"] = args.t0_window_hours
    if args.t0_days is not None:
        slots_cfg.setdefault("t0_policy", {})["days"] = args.t0_days
        slots_cfg.setdefault("t0_policy", {})["mode"] = "days"
    main_seed = int(args.seed if args.seed is not None else (sel_cfg.get("seed") or {}).get("main", 20261002))
    seed_info = {"main": main_seed, "streams": mf.derive_seeds(main_seed, sel_cfg.get("seed") or {}),
                 "sampling": {"mode": (sel_cfg.get("sampling") or {}).get("mode"),
                              "temperature": (sel_cfg.get("sampling") or {}).get("temperature"),
                              "note": "组队按 softmax(score/T) 概率抽样；种子固定即名单固定"}}
    pol = slots_cfg.get("t0_policy") or {}

    P("=" * 88)
    P("仿真层上游固定流程：建图 -> 定槽位 -> 选 500 人 -> 交付下游")
    P("=" * 88)
    P("run_id       %s" % run_id)
    P("主随机种子    seed = %d   派生流 %s" % (main_seed, seed_info["streams"]))
    P("口径         角色 %d 类 %s" % (len(slots_cfg["roles"]), slots_cfg["roles"]))
    P("             渠道 %d 个 %s" % (len(CHANNELS), CHANNELS))
    combos = sum(len(v) for v in slots_cfg["role_channel"].values())
    P("             有效组合 %d 类   单场组队 %d 人"
      % (combos, sum(slots_cfg["quota"].values())))
    P("             t0 口径 %s / 前置窗 %gh"
      % (pol.get("mode"), float(pol.get("pre_window_hours", 0) or 0)))
    P("抽取后端      %s    词表 %s" % (args.extractor, tax["version"]))

    # ---------------- [0] 适配事件集
    P("\n[0] 读事件集 + 适配")
    raw = json.loads(Path(args.event).read_text(encoding="utf-8"))
    is_pack = isinstance(raw, dict) and "events" in raw and "docs" in raw
    packs_dir = outdir / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    if is_pack:
        pack = raw
        src_note = "输入已是标准事件包"
    else:
        import adapt_collect
        recs = raw
        if isinstance(recs, dict):
            recs = recs.get("posts") or recs.get("docs") or recs.get("data") or []
        pack = adapt_collect.adapt(recs)
        src_note = "由 tools/adapt_collect.py 从采集侧扁平格式适配"
    pack.setdefault("meta", {})["源文件"] = str(Path(args.event).resolve())
    pack["meta"]["适配方式"] = src_note
    write_json(packs_dir / "event_pack.json", pack)
    events, docs = pack["events"], {d["doc_id"]: d for d in pack["docs"]}
    if args.event_id:
        events = [e for e in events if e["event_id"] == args.event_id]
    P("    %s：事件 %d 个 / 帖子 %d 条 -> %s"
      % (src_note, len(events), len(docs), packs_dir / "event_pack.json"))

    # ---------------- 画像库校验
    pool = load_pool(args.personas, tax, contract, slots_cfg, alias_cfg)
    ok_pool, pool_rep = validate_pool(pool, tax, contract, slots_cfg, alias_cfg)
    P("\n[0b] 画像库契约校验：%s（%d 条）" % ("通过" if ok_pool else "存在 FAIL", len(pool)))
    for l in pool_rep:
        if l.strip().startswith("[FAIL]") or "可支撑场次" in l or l.startswith("    "):
            P("    " + l.strip())

    ex = build_extractor(args.extractor, rules, tax, onto)
    stance_cfg = load_config("poster_stance")
    # ★ t0 立场值标定配置（注意与上面的「发帖者类型」是两条不同的轴）：
    #   类型 = 谁在说话（当事人型/围观型/机构型）；立场 = 他站哪边（连续量 ∈ [-1,+1]）
    stance_value_cfg = load_config("stance")
    builder = KGBuilder(ex, tax, slots_cfg, stance_cfg, stance_value_cfg)
    streams = make_streams(main_seed, sel_cfg.get("seed") or {})
    outputs, all_checks = [], []

    for ev in events:
        eid = ev["event_id"]
        P("\n" + "-" * 88)
        P("事件 %s  %s" % (eid, ev.get("event_abstract")))
        P("-" * 88)
        for sub in ("kg", "slots", "selection", "simseed", "holdout", "procurement", "downstream", "report"):
            (outdir / sub).mkdir(parents=True, exist_ok=True)

        # ---------------- [1] ① 建图 + t0 状态快照
        kg, st = builder.build(ev, docs)
        if kg is None:
            P("    [1] ① 建图：失败 —— %s" % " | ".join(st.get("reasons", [])))
            continue
        write_json(outdir / "kg" / ("event_kg_%s.json" % eid), kg)
        hold = getattr(builder, "_holdout", []) or []
        write_json(outdir / "holdout" / ("holdout_%s.json" % eid),
                   [{"doc_id": d.get("doc_id"), "channel_std": d.get("channel_std"),
                     "poster_role": d.get("poster_role"), "publish_time": d.get("publish_time"),
                     "author_id": d.get("author_id"), "source_url": d.get("source_url"),
                     "raw_content": d.get("raw_content")} for d in hold])
        P("    [1] ① 建图：%s / %s  窗口内 %d 帖（留出 %d）/ 议题 %d / 具名实体 %d / 边 %d"
          % (st["status"], kg["quality"], kg["doc_count"], len(hold),
             len(kg["issues"]), len(kg["named_entities"]), len(kg["relations"])))
        P("        事件类型：%s   热度口径：%s"
          % (kg.get("event_type"), (kg.get("heat_model") or {}).get("mode")))
        P("        t0 = %s   cutoff = %s   泄漏闸：%s"
          % (kg["t0"], kg["visible_until"], kg["leakage_guard"]["passed"]))
        P("        第 0 轮初始动作 %d 条（同作者同渠道已合并）"
          % len(kg.get("initial_posts") or []))
        mix = (kg.get("t0_snapshot") or {}).get("t0_role_mix") or {}
        ign = (kg.get("t0_snapshot") or {}).get("ignition") or {}
        P("        ★ 首发发帖者类型：%s（判定角色 %s，采集侧声明 %s，置信 %s）"
          % ({"self_interest": "当事人型（自己受损/自己维权 -> 客户）",
              "commentary": "围观型（仅评论事件/宣泄情绪 -> 网民）",
              "institutional": "机构型（媒体/监管/KOL/专业背书方）"}.get(ign.get("poster_stance"),
                                                                        ign.get("poster_stance")),
             ign.get("poster_role_effective"), ign.get("poster_role_declared"),
             ign.get("stance_confidence")))
        P("          证据：%s" % (ign.get("stance_evidence") or "（无显式证据，低置信基线）"))
        P("        t0 房间姿态分布：%s   当事人型占比 %.0f%%"
          % (mix.get("by_stance"), 100.0 * (mix.get("self_interest_ratio") or 0)))

        # ---------------- [2] ② 定槽位
        # ★★ 组长 v3 §2.7 规模公式（★ 决定权归画像侧，公式只是建议）
        #     N_agent = max( N_min , ceil( N_具名实体 / r_max ) )      r_max ≈ 1/8
        #     小事件 50–100 ｜ 中等 480–500 ｜ 大 1200
        #     命题要求的是「能支持 >=500」，不是「每场必须 500」
        #     acceptance.yaml 强制 >=500（满足命题）；verify.yaml 可 50–100（小事件验证便宜）
        if not getattr(args, "total", None):
            import math as _math
            _named = [e for e in (kg.get("named_entities") or []) if e.get("bindable")]
            _nn = len(_named)
            _rmax = float(getattr(args, "r_max", None) or 0.125)
            _nmin = int(getattr(args, "n_min", None) or 50)
            _cap = int(getattr(args, "n_cap", None) or 1200)
            _by_formula = int(_math.ceil(_nn / _rmax)) if _nn else 0
            _n = min(_cap, max(_nmin, _by_formula))
            _band = "小事件" if _n <= 100 else ("中等事件" if _n <= 500 else "大事件")
            q0 = slots_cfg["quota"]
            scaled = largest_remainder(q0, _n)
            scaled = {rr: scaled.get(rr, 0) for rr in q0}
            # ★ 小角色保底（组长：由画像侧设）—— 池子里没有监管，第 30 轮该监管上场时就没人，推演直接断掉
            _FLOOR = {"媒体": 3, "监管": 1, "KOL": 2}
            for _fr, _fv in _FLOOR.items():
                if scaled.get(_fr, 0) < _fv:
                    _need = _fv - scaled.get(_fr, 0)
                    _big = max(scaled, key=lambda k: scaled[k])
                    scaled[_big] -= _need
                    scaled[_fr] = _fv
            slots_cfg = dict(slots_cfg)
            slots_cfg["quota"] = scaled
            slots_cfg["auto_scale_note"] = ("★ 组长 v3 §2.7：N_agent = max(N_min, ceil(N_具名/r_max))；"
                                            "具名 %d 个 / r_max %.3f / N_min %d -> 公式给 %d，取 %d（%s 档）"
                                            % (_nn, _rmax, _nmin, _by_formula, _n, _band))
            P("    [0] ★ 自动规模：具名实体 %d 个 -> 公式 %d -> 取 %d（%s 档）"
            % (_nn, _by_formula, _n, _band))
            P("        配额：%s（合计 %d）｜ 小角色保底 %s"
            % (scaled, sum(scaled.values()), _FLOOR))
        # ★★ 2026-10-07 新增：--total auto —— 按 t0 火热程度自动决定仿真规模
        #   必须放在 KG 建好【之后】—— 它要用 kg["t0_effective"]。
        if str(args.total or "").strip().lower() in ("auto", "a"):
            _at = auto_total(kg, slots_cfg)
            if _at:
                _q0 = slots_cfg["quota"]
                _sc = largest_remainder(_q0, _at["n"])
                _sc = {r: _sc.get(r, 0) for r in _q0}
                slots_cfg = dict(slots_cfg)
                slots_cfg["quota"] = _sc
                P("    [0a] 自动规模：t0 帖数=%d ｜ t0 互动量=%.0f ｜ N=%d 人"
                  % (_at["posts"], _at["engagement"], _at["n"]))
                # ★ v5 可解释公式：N = base_size + per_poster_gain*max(0,posts-post_threshold)
                #                              + engagement_gain*log10(1+engagement/engagement_base)
                _pr = _at["params"]
                P("         = %g(基线) + %g×(max(0, %d−%g)) + %g×log10(1+%.0f/%g)"
                  % (_pr["base_size"], _pr["per_poster_gain"], _at["posts"], _pr["post_threshold"],
                     _pr["engagement_gain"], _at["engagement"], _pr["engagement_base"]))
                P("         = %.1f + %.1f + %.1f = %.1f   ->  clip(%d, %d) = %d 人"
                  % (_pr["base_size"], _at["posts_term"], _at["engagement_term"], _at["raw"],
                     _pr["min_size"], _pr["max_size"], _at["n"]))
                P("         角色占比定死，配额缩放：%s -> %s（合计 %d）" % (_q0, _sc, sum(_sc.values())))
            else:
                P("    [0a] auto_total 返回 None（auto_scale.enabled=false），沿用原配额")
        slots, drops = derive(kg, slots_cfg, tax)
        mv = list(getattr(derive, "last_matrix_violations", []) or [])
        slots, bad_leaf, bad_combo = legality_gate(slots, slots_cfg, tax)
        gaps, _ = feasibility_gate(slots, pool, slots_cfg["thresholds"]["feasibility_ratio"])
        export_cypher(kg, slots, outdir / "kg" / ("event_kg_%s.cypher" % eid))
        write_json(outdir / "slots" / ("slots_%s.json" % eid), slots)
        slots_csv(slots, outdir / "slots" / ("slots_%s.csv" % eid))
        acc, digest = acceptance(slots, slots_cfg, kg)
        head = sum(s["headcount"] for s in slots)
        P("    [2] ② 定槽位：%d 个槽位 / 合计 %d 人 / 越界 %d   摘要 SHA1 %s"
          % (len(slots), head, len(bad_leaf) + len(bad_combo), digest))
        P("        人物卡口：具名 1:1 直绑 %d 个 / 议题槽位 %d 个"
          % (sum(1 for s in slots if s["bound_entity"]),
             sum(1 for s in slots if s["issue_leaf"])))
        if gaps:
            write_json(outdir / "procurement" / "slot_gap.json", gaps)
        # ★ 组长 2.6 #2：(角色, 渠道) 必须落在有效组合矩阵内。
        #   事件集里一旦出现矩阵外的组合，就把证据交出去请画像库侧复核 —— 不静默。
        obs = Counter((d.get("poster_role"), d.get("channel_std"))
                      for d in docs.values() if d.get("doc_id") in set(kg["t0_snapshot"]["visible_doc_ids"])
                      or True)
        bad_obs = {k: v for k, v in obs.items()
                   if k[0] in ROLES and k[1] in CHANNELS and k[1] not in slots_cfg["role_channel"].get(k[0], {})}
        if mv or bad_obs:
            P("        ⚠ 矩阵外组合：槽位落地时越界 %d 例；事件集全量观测到 %d 个矩阵外 (角色x渠道) 组合"
              % (len(mv), len(bad_obs)))
            for k, v in sorted(bad_obs.items(), key=lambda kv: -kv[1])[:10]:
                P("           %s x %s  %d 条" % (k[0], k[1], v))
        with open(outdir / "procurement" / "matrix_review.csv", "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("类型,角色,真实渠道,落地渠道,实体/说明,帖子数\n")
            for m in mv:
                fh.write("槽位越界,%s,%s,%s,%s,%d\n" % (m["role_type"], m["channel_real"],
                                                        m["channel_used"], m["entity"],
                                                        len(m["post_ids"])))
            for k, v in sorted(bad_obs.items(), key=lambda kv: -kv[1]):
                fh.write("事件集观测,%s,%s,,%s,%d\n" % (k[0], k[1], "采集侧存在该组合", v))

        # ---------------- [3] ③ 选 500 人
        roster, rstat = run_event(slots, pool, tax, sel_cfg, {}, 1, streams, alias_cfg)

        # ★★ 把 GraphRAG 推导出的 t0 真实人物注入名册 ——
        #   必须在【写 roster 之前】、并且【在算相关度之前】（这样 R1/R2 才用推导后的兴趣）。
        #   此前注入是人工步骤（做过两次、漏了一次），导致 500 人版里真实人物
        #   停在「被绑到随机库内人」的状态。现在并进管线。
        try:
            import json as _json
            sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
            import inject_real_personas as _IRP
            # ★ t0 快照【不能读 downstream/】，那个文件由后面的 export_all 才写出。
            #   kg 里本来就有 t0_snapshot 与 initial_posts，直接取。
            _t0snap = dict(kg.get("t0_snapshot") or {})
            _t0snap["initial_posts"] = kg.get("initial_posts") or []
            _pack = _json.loads((packs_dir / "event_pack.json").read_text(encoding="utf-8"))
            _IRP.derive_and_inject(kg, _t0snap, _pack, roster, verbose=True)
        except Exception as _e:
            import traceback as _tb
            P("    [真实人物] ⚠️ 注入失败：%s" % str(_e)[:120])
            P("        " + _tb.format_exc().strip().split(chr(10))[-1][:120])
        write_json(outdir / "selection" / ("roster_%s.json" % eid), roster)
        quota = Counter(p["role_type"] for p in roster)
        want = Counter(s["role_type"] for s in slots for _ in range(s["headcount"]))
        qdiff = sum(abs(quota[r] - want[r]) for r in set(quota) | set(want))
        P("    [3] ③ 组队：%d 人 / 配额误差 %d / 填不满的槽位 %d / 兜底顶替 %d"
          % (len(roster), qdiff, rstat["shortfall"],
             sum(1 for p in roster if not p["purity_fit"])))
        P("        议题匹配层级：%s" % rstat.get("match_levels"))
        P("        抽样：%s（temperature=%.2f，Gumbel 极值 %.2f）—— 种子 %d 固定即结果固定"
          % (rstat["sampling"]["mode"], rstat["sampling"]["temperature"],
             rstat["sampling"]["max_gumbel"], main_seed))

        # ---------------- [4] 交付下游三件套
        counts = {"roster": len(roster), "slots": len(slots), "issues": len(kg["issues"]),
                  "named_entities": len(kg["named_entities"]),
                  "initial_posts": len(kg.get("initial_posts") or []),
                  "holdout": len(hold), "by_role": dict(quota)}
        checks = {"quota_error": qdiff, "unfilled_slots": rstat["shortfall"],
                  "leakage_guard": kg["leakage_guard"]["passed"],
                  "combos_legal": not bad_combo, "leaves_legal": not bad_leaf}
        # 先建 manifest 骨架 -> 导出三件套 -> 回填产物清单
        base_manifest = mf.build(
            run_id=run_id, event_id=eid, main_seed=main_seed, seed_spec=sel_cfg.get("seed") or {},
            inputs={
                "event_set": mf.file_record(args.event),
                "event_pack": mf.file_record(packs_dir / "event_pack.json"),
                "persona_library": dict(mf.file_record(args.personas), personas=len(pool)),
                "configs": {k: mf.file_record(ROOT / "config" / ("%s.json" % k))
                            for k in ("taxonomy", "ontology", "rules", "slots", "selection",
                                      "topic_vocab", "persona_contract", "poster_stance")},
            },
            outputs=[], versions={"taxonomy": tax["version"], "rules": rules["version"],
                                  "slots": slots_cfg["version"], "selection": sel_cfg["version"],
                                  "persona_contract": contract["version"],
                                  "manifest": mf.MANIFEST_VERSION},
            counts=counts, checks=checks,
            command=("python run.py --event %s --personas %s --seed %d --out %s"
                     % (Path(args.event).as_posix(), Path(args.personas).as_posix(),
                        main_seed, outdir.as_posix())),
            extra={"event_type": kg.get("event_type"),
                   "mvp_scope": {"cross_channel": False,
                                 "note": "MVP 只管渠道内传播；跨渠道搬运（组长 2.4）留到非 MVP"},
                   "topic_match_levels": rstat.get("match_levels"),
                   "sampling": rstat.get("sampling"),
                   "action_threshold_form": "画像库 v3 形态：效价一维三档解锁线 + 平台动作集（本流程只透传）",
                   "t0": kg.get("t0"), "visible_until": kg.get("visible_until"),
                   "t0_policy": kg.get("t0_policy"),
                   "slots_digest_sha1": digest,
                   "note": "产物记录：种子 -> 输入指纹 -> 每个交付文件的 sha256，可逐项复现"})
        written, snap, team = ds.export_all(outdir=outdir, kg=kg, roster=roster,
                                            slots=slots, seed_info=seed_info,
                                            manifest_obj=base_manifest)
        base_manifest["outputs"] = written
        mf.write(outdir / "downstream" / ("%s_run_manifest.json" % eid), base_manifest)
        outputs.extend(written)
        P("    [4] 交付下游：t0 快照 / 500 人名单（json+csv）/ 产物记录  -> %s"
          % (outdir / "downstream"))
        P("        第 0 轮初始动作绑定：%d/%d 条已绑到具体 agent（未绑的如实标 unbound）"
          % (snap["initial_posts_stats"]["bound"], snap["initial_posts_stats"]["count"]))
        all_checks.append((eid, checks, rstat))

        # ---------------- [5] 自检
        rep = outdir / "report" / ("verify_%s.txt" % eid)
        lines = ["=" * 80, "单事件交付物自检：%s" % eid, "=" * 80,
                 "人数守恒 == 单场配额              %s" % (len(roster) == sum(slots_cfg["quota"].values())),
                 "配额误差 == 0                    %s" % (qdiff == 0),
                 "填不满的槽位 == 0                 %s" % (rstat["shortfall"] == 0),
                 "(角色x渠道) 全在 20 组合内         %s" % (not bad_combo),
                 "议题 leaf 全在词表内               %s" % (not bad_leaf),
                 "无 (:Agent) 节点                  %s" % (not any(e["type"] == "Agent" for e in kg["entities"])),
                 "关系边全带 source_url              %s" % all(r.get("source_url") for r in kg["relations"]),
                 "建图输入 == 第 0 轮注入输入         %s" % (len(kg["t0_snapshot"]["visible_doc_ids"]) == kg["doc_count"]),
                 "泄漏闸（无 cutoff 之后的帖子）      %s" % kg["leakage_guard"]["passed"],
                 "一人一渠道（无多账号）             %s" % all(p["primary_channel"] in CHANNELS for p in roster),
                 "名单内无重复 agent                %s" % (len({p["agent_id"] for p in roster}) == len(roster)),
                 "★ t0 首发发帖者类型已判定          %s（%s -> %s，置信 %s）"
                 % (bool(snap["ignition"].get("poster_stance")),
                    snap["ignition"].get("poster_role_declared"),
                    snap["ignition"].get("poster_role_effective"),
                    snap["ignition"].get("stance_confidence")),
                 "★ 每条第 0 轮动作都带发帖者类型     %s"
                 % all(p.get("poster_stance") for p in snap["initial_posts"]),
                 "★ 每个具名实体都带 role+stance     %s"
                 % all(e.get("role") and e.get("poster_stance") for e in snap["named_entities"]),
                 "产物记录含种子与 sha256            %s" % bool(base_manifest["seed"]["main"]),
                 "复现命令：%s" % base_manifest["reproduce"]["command"]]
        # 人读的报告用 utf-8-sig（带 BOM）—— Windows 记事本 / Excel / Get-Content 才不会乱码
        rep.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    # ---------------- 汇总
    P("\n" + "=" * 88)
    P("汇总")
    P("=" * 88)
    P("run_id %s   主种子 %d" % (run_id, main_seed))
    P("交付目录：%s" % (outdir / "downstream"))
    P("  ├─ <event>_t0_snapshot.json     输出1 t0 状态快照（具名实体/议题/关系边/初始动作）")
    P("  ├─ <event>_roster.json / .csv   输出2 500 人组队名单")
    P("  └─ <event>_run_manifest.json    输出3 随机种子与产物记录")
    P("过程产物：kg/ slots/ selection/ holdout/ procurement/ packs/ report/")
    P("重跑同一条命令 + 同一个 --seed，必得同一份名单（比对 roster 的 sha256 即可）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
