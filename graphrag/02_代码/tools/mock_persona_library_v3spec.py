#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_persona_library_v3spec.py —— 严格按画像组《舆情仿真人物画像字段规范》生成画像库。

用途：画像组还没交付，先按他们自己的规范造一份**能跑起来的**库，
      用来验证「GraphRAG 去画像库选 500 人」这条链路。

    python tools/mock_persona_library_v3spec.py --n 2000 --topics spec    --out out/persona_v3spec_2000.json
    python tools/mock_persona_library_v3spec.py --n 2000 --topics taxonomy --out out/persona_v3spec_2000_aligned.json

两个 --topics 档：
  spec      严格按规范：concern_topics 的键 ⊆ 他们那 12 个话题
  taxonomy  规范照用，只把话题库换成我们的 taxonomy 叶子（这是我们建议的 P0 改动）

★ 唯一一处格式偏离（必须让画像组知道）：
  规范写 agent_id 的 NN = 01~05（每格 5 人），30 格 x 5 = **150 人**，
  而组长要单场 500 人、池内 >=1500。所以这里把 NN 放宽到 3 位（001~319），
  按他们附录的「平台 x 角色 权重分布」把总量放大到 --n。**除这一处，其余逐字照规范。**
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config, largest_remainder, write_json      # noqa: E402
from prng import DeterministicRNG, persona_rng, digest_of          # noqa: E402

# ---- 规范附录：平台 x 角色 权重分布（30 格，合计 1.0）
CELL_WEIGHTS = load_config("matrix_v3spec")

SPEC_TOPICS = ["食品安全", "经济形势", "就业保障", "医疗健康", "消费者权益", "教育公平",
               "社会治安", "理赔速度", "服务态度", "保费", "条款透明度", "监管处罚"]

# ★ 档 3：话题挂「通用层」—— 保险舆情真正会用到的 12 个通用关切
#   （9 个 = 保险议题直接映射到的关切；另 3 个生活场景是修桥时发现的：
#    用户是因为「养宠/开车/旅行」才关心宠物险/车险/旅游险，不是"关心金融投资"）
GENERAL_TOPICS = ["金融与投资风险", "消费者权益保护", "品牌营销与广告伦理", "企业经营与财报",
                  "行业监管与合规", "性别与女性权益", "隐私与个人信息保护", "就业与劳动权益",
                  "社会治安与违法犯罪", "交通出行安全", "宠物与养宠", "旅行与消费生活方式"]
# 角色 x 关切 的 affinity（高1.0 / 中0.7 / 低0.4）—— 决定谁会关心什么
C_AFF = {
 "金融与投资风险":    {"网民":"中","客户":"高","KOL":"中","媒体":"中","专业背书方":"高","监管":"中"},
 "消费者权益保护":    {"网民":"高","客户":"高","KOL":"高","媒体":"高","专业背书方":"高","监管":"中"},
 "品牌营销与广告伦理":  {"网民":"高","客户":"中","KOL":"高","媒体":"高","专业背书方":"中","监管":"中"},
 "企业经营与财报":    {"网民":"低","客户":"低","KOL":"高","媒体":"高","专业背书方":"中","监管":"高"},
 "行业监管与合规":    {"网民":"低","客户":"中","KOL":"高","媒体":"高","专业背书方":"高","监管":"高"},
 "性别与女性权益":    {"网民":"高","客户":"中","KOL":"高","媒体":"中","专业背书方":"中","监管":"低"},
 "隐私与个人信息保护":  {"网民":"中","客户":"中","KOL":"中","媒体":"中","专业背书方":"高","监管":"中"},
 "就业与劳动权益":    {"网民":"中","客户":"中","KOL":"中","媒体":"中","专业背书方":"中","监管":"低"},
 "社会治安与违法犯罪":  {"网民":"中","客户":"低","KOL":"中","媒体":"高","专业背书方":"中","监管":"高"},
 "交通出行安全":      {"网民":"中","客户":"高","KOL":"低","媒体":"低","专业背书方":"低","监管":"低"},
 "宠物与养宠":        {"网民":"高","客户":"高","KOL":"中","媒体":"低","专业背书方":"低","监管":"低"},
 "旅行与消费生活方式":  {"网民":"中","客户":"中","KOL":"中","媒体":"低","专业背书方":"低","监管":"低"}}
TIER3 = {"高": 1.0, "中": 0.7, "低": 0.4}
# 9 大类 x 6 角色 的 affinity（叶子继承大类）—— 决定"谁会关心什么"
CAT_AFF = {
 "民生保障":        {"网民":"高","客户":"高","KOL":"中","媒体":"高","专业背书方":"中","监管":"高"},
 "公共安全与社会治理": {"网民":"中","客户":"低","KOL":"中","媒体":"高","专业背书方":"低","监管":"高"},
 "价值观与社会议题":   {"网民":"高","客户":"中","KOL":"高","媒体":"中","专业背书方":"中","监管":"低"},
 "国际与政治":       {"网民":"中","客户":"低","KOL":"中","媒体":"高","专业背书方":"低","监管":"高"},
 "情感与生活方式":     {"网民":"中","客户":"中","KOL":"中","媒体":"低","专业背书方":"低","监管":"低"},
 "经济与商业":       {"网民":"中","客户":"高","KOL":"高","媒体":"高","专业背书方":"高","监管":"高"},
 "科技与信息":       {"网民":"中","客户":"低","KOL":"高","媒体":"中","专业背书方":"中","监管":"中"},
 "体育与游戏":       {"网民":"中","客户":"低","KOL":"中","媒体":"低","专业背书方":"低","监管":"低"},
 "文娱与明星":       {"网民":"高","客户":"低","KOL":"高","媒体":"低","专业背书方":"低","监管":"低"}}

ROLE_SENS = {"网民": 1.2, "客户": 1.1, "KOL": 0.95, "专业背书方": 0.8, "媒体": 0.7, "监管": 0.6}
INF = {"网民": (0.05, 0.15), "客户": (0.10, 0.25), "KOL": (0.20, 0.70),
       "专业背书方": (0.45, 0.75), "媒体": (0.50, 0.80), "监管": (0.60, 0.90)}
VAL = {"网民": (-0.5, 0.5), "客户": (-0.3, 0.3), "KOL": (-0.4, 0.4),
       "专业背书方": (-0.2, 0.2), "媒体": (-0.1, 0.1), "监管": (-0.05, 0.05)}
ARO = {"网民": (0.3, 0.8), "客户": (0.3, 0.7), "KOL": (0.3, 0.7),
       "专业背书方": (0.2, 0.5), "媒体": (0.1, 0.4), "监管": (0.1, 0.3)}
DEC = {"网民": (0.70, 0.75), "客户": (0.75, 0.80), "KOL": (0.75, 0.85),
       "专业背书方": (0.80, 0.85), "媒体": (0.85, 0.90), "监管": (0.85, 0.90)}
CON = {"网民": (0.60, 0.90), "客户": (0.50, 0.80), "KOL": (0.40, 0.80),
       "专业背书方": (0.30, 0.60), "媒体": (0.20, 0.50), "监管": (0.10, 0.30)}
# 档位阈值：轻/中/重（重度只对网民与客户存在）
THR = {"网民": [(-0.25, -0.35), (-0.39, -0.61), (-0.53, -0.87)],
       "客户": [(-0.31, -0.39), (-0.46, -0.64), (-0.61, -0.89)],
       "KOL": [(-0.36, -0.44), (-0.52, -0.68), None],
       "专业背书方": [(-0.42, -0.48), (-0.59, -0.71), None],
       "媒体": [(-0.47, -0.53), (-0.65, -0.75), None],
       "监管": [(-0.58, -0.62), (-0.77, -0.83), None]}
# 动作集：直接采用规范 v2 的「角色 x 平台」结果表（动作名 like/comment/repost/report/complaint/post）
ALL_CH = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]
ALL_ROLE = ["网民", "客户", "KOL", "媒体", "专业背书方", "监管"]
_L, _C, _R, _P = "like", "comment", "repost", "post"
_FULL = [_L, _C, _R, _P]
ACTIONS = {
 ("网民","微博"): _FULL+["report"], ("网民","抖音"): _FULL+["report"],
 ("网民","小红书"): _FULL+["report"], ("网民","新闻门户"): _FULL+["report"],
 ("网民","黑猫投诉"): _FULL+["report"], ("网民","监管通报"): _FULL,
 ("客户","微博"): _FULL+["report"], ("客户","抖音"): _FULL+["report"],
 ("客户","小红书"): _FULL+["report"], ("客户","黑猫投诉"): _FULL+["report","complaint"],
 ("KOL","微博"): _FULL+["report"], ("KOL","抖音"): _FULL+["report"],
 ("KOL","小红书"): _FULL+["report"], ("KOL","黑猫投诉"): _FULL+["report"],
 ("专业背书方","微博"): _FULL+["report"], ("专业背书方","抖音"): _FULL+["report"],
 ("专业背书方","小红书"): _FULL+["report"], ("专业背书方","新闻门户"): _FULL+["report"],
 ("专业背书方","黑猫投诉"): _FULL+["report"], ("专业背书方","监管通报"): _FULL,
 ("媒体","微博"): _FULL, ("媒体","抖音"): _FULL, ("媒体","小红书"): _FULL,
 ("媒体","新闻门户"): _FULL, ("媒体","监管通报"): _FULL,
 ("监管","微博"): _FULL, ("监管","抖音"): _FULL, ("监管","小红书"): _FULL,
 ("监管","新闻门户"): _FULL, ("监管","监管通报"): _FULL,
}
# 规范 v2 的 reach_multiplier / emotion_amplifier 精确矩阵
REACH = {"微博":{"网民":1.40,"客户":1.60,"KOL":2.40,"媒体":2.80,"专业背书方":2.40,"监管":3.00},
 "抖音":{"网民":1.75,"客户":2.00,"KOL":3.00,"媒体":3.50,"专业背书方":3.00,"监管":3.75},
 "小红书":{"网民":1.05,"客户":1.20,"KOL":1.80,"媒体":2.10,"专业背书方":1.80,"监管":2.25},
 "新闻门户":{"网民":0.70,"媒体":1.40,"专业背书方":1.20,"监管":1.50},
 "黑猫投诉":{"网民":0.42,"客户":0.48,"KOL":0.72,"专业背书方":0.72},
 "监管通报":{"网民":0.91,"媒体":1.82,"专业背书方":1.56,"监管":1.95}}
EMOAMP = {"微博":{"网民":1.12,"客户":1.28,"KOL":1.92,"媒体":2.24,"专业背书方":1.92,"监管":2.40},
 "抖音":{"网民":1.33,"客户":1.52,"KOL":2.28,"媒体":2.66,"专业背书方":2.28,"监管":2.85},
 "小红书":{"网民":0.84,"客户":0.96,"KOL":1.44,"媒体":1.68,"专业背书方":1.44,"监管":1.80},
 "新闻门户":{"网民":0.46,"媒体":0.91,"专业背书方":0.78,"监管":0.98},
 "黑猫投诉":{"网民":0.70,"客户":0.80,"KOL":1.20,"专业背书方":1.20},
 "监管通报":{"网民":0.32,"媒体":0.63,"专业背书方":0.54,"监管":0.68}}
PLAT_MID = {"微博": 2.0, "抖音": 2.5, "小红书": 1.5, "新闻门户": 1.0, "黑猫投诉": 0.6, "监管通报": 1.3}
EMO_MID = {"微博": 1.6, "抖音": 1.9, "小红书": 1.2, "新闻门户": 0.65, "黑猫投诉": 1.0, "监管通报": 0.45}
GRP = {"监管": 1.50, "媒体": 1.40, "KOL": 1.20, "专业背书方": 1.20, "客户": 0.80, "网民": 0.70}
DECAY_RATE = {"微博": (0.7, 0.9), "抖音": (0.6, 0.8), "小红书": (0.4, 0.6),
              "新闻门户": (0.1, 0.3), "黑猫投诉": (0.3, 0.5), "监管通报": (0.1, 0.2)}
SHARE = {  # 规范 ④ share_propensity 表：role -> platform -> (lo,hi)
 "网民":{"微博":(.62,.78),"抖音":(.52,.68),"小红书":(.32,.48),"新闻门户":(.22,.38),"黑猫投诉":(.12,.28),"监管通报":(.02,.18)},
 "客户":{"微博":(.48,.64),"抖音":(.40,.56),"小红书":(.24,.40),"新闻门户":(.16,.32),"黑猫投诉":(.08,.24),"监管通报":(.00,.16)},
 "KOL":{"微博":(.80,.96),"抖音":(.67,.83),"小红书":(.42,.58),"新闻门户":(.30,.46),"黑猫投诉":(.17,.33),"监管通报":(.05,.21)},
 "专业背书方":{"微博":(.55,.71),"抖音":(.46,.62),"小红书":(.28,.44),"新闻门户":(.19,.35),"黑猫投诉":(.10,.26),"监管通报":(.01,.17)},
 "媒体":{"微博":(.69,.85),"抖音":(.58,.74),"小红书":(.36,.52),"新闻门户":(.25,.41),"黑猫投诉":(.14,.30),"监管通报":(.03,.19)},
 "监管":{"微博":(.27,.43),"抖音":(.22,.38),"小红书":(.12,.28),"新闻门户":(.07,.23),"黑猫投诉":(.02,.18),"监管通报":(.00,.13)}}
COMPLAINT_BASE = {"网民": (.12,.28), "客户": (.32,.48), "KOL": (.17,.33),
                  "专业背书方": (.22,.38), "媒体": (.02,.18), "监管": (.00,.13)}
COMPLAINT_HEIMAO = {"网民": (.32,.48), "客户": (.62,.78), "KOL": (.37,.53),
                    "专业背书方": (.42,.58), "媒体": (.12,.28)}
AGES = ["18-24", "25-30", "31-40", "41-50", "51-60"]
OCC = ["企业职员", "公务员", "自由职业", "个体经营", "学生", "教师", "医护", "IT", "金融", "全职妈妈", "退休"]
REGIONS = ["北京", "上海", "广东深圳", "广东广州", "浙江杭州", "江苏南京", "四川成都",
           "湖北武汉", "山东青岛", "陕西西安", "湖南长沙", "福建厦门", "河南郑州", "辽宁沈阳"]
INTERESTS = ["理财记账", "养宠日常", "母婴育儿", "车生活", "健康科普", "本地民生",
             "消费测评", "法律维权", "保险从业", "职场吐槽", "追剧吃瓜", "数码科技"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--topics", default="spec", choices=["spec", "taxonomy", "general", "w1", "w1p", "archetype", "cat9"],
                    help="spec=规范那 12 话题；taxonomy=保险议题叶子；general=通用关切层（推荐）")
    ap.add_argument("--channels", default="spec", choices=["spec", "typical"],
                    help="spec=按规范附录的平台权重；typical=按 32 场历史事件的典型渠道分布配平")
    ap.add_argument("--uniform-blend", type=float, default=0.3,
                    help="typical 档：把典型渠道占比与均匀分布按此比例混合。纯典型分布会让小格饿死"
                         "（实测专业背书方x微博只剩 9 人，组队差 1 人），混一点均匀就能兜住。")
    ap.add_argument("--seed", type=int, default=20261002)
    args = ap.parse_args()

    # ★ 全程使用规范定义的确定性 PRNG（src/prng.py），不用 Python 自带 random
    seed_rng = DeterministicRNG(args.seed, "library")
    tax = load_config("taxonomy")
    slots_cfg = load_config("slots")
    leaves = [lf["leaf"] for v in tax["categories"].values() for lf in v["leaves"]]
    leaf2top = {lf["leaf"]: t for t, v in tax["categories"].items() for lf in v["leaves"]}
    aff, ts = slots_cfg["affinity_tier"], slots_cfg["tier_score"]

    if args.topics == "spec":
        pool_topics, mode = list(SPEC_TOPICS), "spec"
    elif args.topics == "general":
        pool_topics, mode = list(GENERAL_TOPICS), "general"
    elif args.topics == "cat9":
        cl = load_config("concern_topics_library")
        pool_topics = [L["name"] for L in cl["layers"]]
        W = {L["name"]: L["weight_general"] for L in cl["layers"]}
        CAT = {c: c for c in pool_topics}
        CAT_AFF = {L["name"]: L.get("aff", {}) for L in cl["layers"]} or CAT_AFF
        mode = "cat9"
    elif args.topics == "archetype":
        # ★ 原 archetype 模式（14 个生活形态原型）已在规范 v3.0 撤回，
        #   理由见 docs/通用关切词表规范_v3.0_给画像组.md §3.4.4：占比与话题映射均无出处。
        #   这里保留入口但直接退回 w1，避免静默产出无据的库。
        sys.stderr.write("[撤回] archetype 模式已于 v3.0 撤回，自动退回 w1 独立抽样。\n")
        cl = load_config("concern_topics_library")
        flat = cl["leaves_flat"]
        pool_topics = [x["leaf"] for x in flat]
        W = {x["leaf"]: x["w1"] for x in flat}
        CAT = {x["leaf"]: x["cat"] for x in flat}
        mode = "w1"
    elif args.topics in ("w1", "w1p"):
        cl = load_config("concern_topics_library")
        key = "w1p" if args.topics == "w1p" else "w1"
        flat = cl["leaves_flat"]
        pool_topics = [x["leaf"] for x in flat]
        W = {x["leaf"]: x[key] for x in flat}
        CAT = {x["leaf"]: x["cat"] for x in flat}
        mode = "w1"
    else:
        pool_topics, mode = list(leaves), "taxonomy"

    # ---- 格子人数：两档
    if args.channels == "spec":
        alloc_w = {"%s|%s" % (c, r): w for c, r, w in CELL_WEIGHTS}
        alloc = largest_remainder(alloc_w, args.n)
    else:
        # ★ 按「典型事件的渠道分布」配平：角色总量 = 配额 x (n/500)，再按 32 场历史的渠道占比切
        share = json.loads((Path(__file__).resolve().parent.parent /
                            "config" / "channel_share_typical.json").read_text(encoding="utf-8"))
        q = slots_cfg["quota"]
        role_total = largest_remainder({r: q[r] for r in slots_cfg["roles"]}, args.n)
        alloc = {}
        for r in slots_cfg["roles"]:
            cells = list(slots_cfg["role_channel"][r].keys())
            b = args.uniform_blend
            w = {c: (1 - b) * share.get("%s|%s" % (r, c), 0.0) + b * (1.0 / len(cells))
                 for c in cells}
            for c, k in largest_remainder(w, role_total[r]).items():
                alloc["%s|%s" % (c, r)] = k
    # 遍历顺序：spec 档按他们附录的 30 格；typical 档按组长的 20 格（矩阵外格不再产人）
    if args.channels == "spec":
        cell_list = [(c, r) for c, r, _w in CELL_WEIGHTS]
    else:
        cell_list = [(k.split("|")[0], k.split("|")[1]) for k in alloc]
    print("格子人数：渠道档=%s，共 %d 格，合计 %d 人"
          % (args.channels, len(cell_list), sum(alloc.values())))
    used_bio = set()
    pool = []
    for (ch, role) in cell_list:
        cnt = alloc.get("%s|%s" % (ch, role), 0)
        if cnt <= 0:
            continue
        for nn in range(1, cnt + 1):
            agent_id = "%s_%s_%03d" % (ch, role, nn)
            # ★ 每个人一条独立随机流，以 agent_id 为流名 —— 与生成顺序无关、可并行、可单独重生成
            rng = persona_rng(args.seed, agent_id)
            # --- 话题：3~6 个，库内权重和 w ~ U(0.55,0.85)，其他 = 1-w（规范 ⑤）
            k = rng.randint(3, min(6, len(pool_topics)))
            if mode == "taxonomy":
                wts = [0.6 + ts[aff[role][leaf2top[t]]] for t in pool_topics]
            elif mode == "general":
                wts = [TIER3[C_AFF[t][role]] for t in pool_topics]
            elif mode == "cat9":
                wts = [max(W[t], 1e-9) for t in pool_topics]
            elif mode == "w1":
                wts = [max(W[t], 1e-6) * TIER3[CAT_AFF[CAT[t]][role]] for t in pool_topics]
            else:
                wts = [1.0] * len(pool_topics)
            if False:      # archetype 模式已撤回（v3.0），保留分支结构不删以免误用
                ar = rng.choices(AR, weights=[a["share"] for a in AR])[0]
                kc = min(rng.randint(2, 3), k - 1)
                picked, cand = [], list(pool_topics)
                core = [t for t in [c[0] for c in ar["core"]] if t in cand]
                cw = {c[0]: c[1] for c in ar["core"]}
                for _ in range(min(kc, len(core))):
                    t = rng.choices(core, weights=[cw[x] for x in core])[0]
                    picked.append(t); core.remove(t); cand.remove(t)
                for _ in range(k - len(picked)):
                    if not cand: break
                    t = rng.choices(cand, weights=[max(W[x], 1e-9) for x in cand])[0]
                    picked.append(t); cand.remove(t)
            else:
                # ★ 规范的抽样动作：加权无放回（Efraimidis-Spirakis）
                picked = rng.weighted_sample_without_replacement(
                    pool_topics, [wts[pool_topics.index(x)] for x in pool_topics], k)
            wsum = rng.uniform(0.55, 0.85)
            cuts = sorted(rng.uniform(0, 1) for _ in range(k - 1))
            edges = [0.0] + cuts + [1.0]
            parts = [round(wsum * (edges[i + 1] - edges[i]), 2) for i in range(k)]
            parts[-1] = round(wsum - sum(parts[:-1]), 2)
            concern = {picked[i]: max(0.01, parts[i]) for i in range(k)}
            concern["其他"] = round(1 - sum(concern.values()), 2)
            # --- emotion_sensitivity：派生，不直接采样（规范 ③）
            emo = {t: round(min(1.0, max(0.0, concern[t] * ROLE_SENS[role] + rng.uniform(-0.1, 0.1))), 2)
                   for t in picked}
            # --- 阈值：三档，保证 重度 < 中度 < 轻度
            band = THR[role]
            q1 = rng.uniform(min(band[0]), max(band[0]))
            q2 = rng.uniform(min(band[1]), max(band[1]))
            q2 = min(q2, q1 - 0.02)
            q3 = (min(rng.uniform(min(band[2]), max(band[2])), q2 - 0.02)
                  if band[2] else None)
            # --- 渠道行为
            lo, hi = SHARE[role][ch]
            share = round(min(1.0, max(0.0, rng.uniform(lo, hi) + rng.gauss(0, 0.05))), 2)
            cl, chh = (COMPLAINT_HEIMAO.get(role) if ch == "黑猫投诉" else None) or COMPLAINT_BASE[role]
            comp = round(min(1.0, max(0.0, rng.uniform(cl, chh) + rng.gauss(0, 0.05))), 2)
            rlo, rhi = DECAY_RATE[ch]
            hints = []
            if mode == "cat9":
                cl2 = load_config("concern_topics_library")
                by_cat = {}
                for L in cl2["layers"]:
                    by_cat[L["name"]] = [x["leaf"] for x in L["leaves"]]
                for c in picked:
                    sub = by_cat.get(c) or []
                    if sub:
                        hints += rng.sample(sub, min(rng.randint(1, 2), len(sub)))
            bio = "%d岁%s性，%s，%s，%s重度用户，平时爱看%s。" % (
                rng.randint(18, 58), rng.choice("男女"), rng.choice(OCC), rng.choice(REGIONS),
                ch, rng.choice(INTERESTS))
            if bio in used_bio:                            # 规范 ①：persona_bio 必须去重
                bio = bio[:-1] + "，也关注%s。" % rng.choice(INTERESTS)
            used_bio.add(bio)
            ilo, ihi = INF[role]; vlo, vhi = VAL[role]; alo, ahi = ARO[role]
            dlo, dhi = DEC[role]; clo, chi = CON[role]
            pool.append({
                # ★ 字段顺序严格按规范 v2：agent_id -> channel -> role_type -> ...
                "agent_id": agent_id,                          # ★ NN 放宽到 3 位（见文件头说明）
                "channel": ch,                                 # ★ v2 新增：顶层平台字段
                "role_type": role,
                "persona_bio": bio,
                "influence_score": round(rng.uniform(ilo, ihi), 2),
                "valence_baseline": round(rng.uniform(vlo, vhi), 2),
                "arousal_baseline": round(rng.uniform(alo, ahi), 2),
                "emotion_sensitivity": emo,
                "emotion_decay": round(rng.uniform(dlo, dhi), 2),
                "contagion_susceptibility": round(rng.uniform(clo, chi), 2),
                "complaint_propensity": comp,
                "share_propensity": share,
                "concern_topics": concern,
                **({"topic_hints": hints} if mode == "cat9" else {}),
                "active_hours": sorted(rng.sample(range(6, 24), rng.randint(3, 7))),
                "action_threshold": {"轻度": round(q1, 2), "中度": round(q2, 2),
                                     "重度": (round(q3, 2) if q3 is not None else None)},
                "channel_behavior": {
                    "role_type": role, "channel": ch,
                    "available_actions": ACTIONS[(role, ch)],
                    "reach_multiplier": round(min(4.0, max(0.3, REACH[ch][role] + rng.gauss(0, 0.05))), 2),
                    "emotion_amplifier": round(min(3.0, max(0.2, EMOAMP[ch][role] + rng.gauss(0, 0.05))), 2),
                    "info_decay_rate": round(rng.uniform(rlo, rhi), 2)},
                "region": rng.choice(REGIONS),
                "gender": rng.choice(["女", "男"]),
                "age_band": rng.choice(AGES),
                "mbti": rng.choice(list("IE") + list("NS") + list("TF") + list("JP")) and
                        rng.choice(["INTJ","INTP","ENTJ","ENTP","INFJ","INFP","ENFJ","ENFP",
                                    "ISTJ","ISFJ","ESTJ","ESFJ","ISTP","ISFP","ESTP","ESFP"]),
                "_note": "参考画像库（占位，画像组交付后弃用）",
                "_source": "按《舆情仿真人物画像字段规范》合成；话题库=%s" % args.topics,
            })
    write_json(args.out, pool)
    dg = digest_of(pool, ["agent_id", "role_type", "concern_topics",
                          "emotion_sensitivity", "action_threshold", "channel_behavior"])
    (Path(args.out).with_suffix(".digest.txt")).write_text(
        "master_seed=%d\nrecords=%d\nsha256=%s\n" % (args.seed, len(pool), dg), encoding="utf-8")
    from collections import Counter
    print("按规范生成画像库 %d 条 -> %s（话题库档=%s）" % (len(pool), args.out, args.topics))
    print("  角色分布:", dict(Counter(p["role_type"] for p in pool)))
    print("  平台分布:", dict(Counter(p["channel_behavior"]["channel"] for p in pool)))
    print("  agent_id 唯一:", len({p["agent_id"] for p in pool}) == len(pool))
    print("  persona_bio 唯一:", len({p["persona_bio"] for p in pool}) == len(pool))
    print("  权重和=1 校验:", all(abs(sum(v for kk, v in p["concern_topics"].items() if kk != "其他")
                                  + p["concern_topics"]["其他"] - 1.0) < 0.02 for p in pool))
    print("  库指纹 sha256:", dg)
    print("  三档不倒挂校验:", all(p["action_threshold"]["重度"] is None or
                                  p["action_threshold"]["重度"] < p["action_threshold"]["中度"]
                                  < p["action_threshold"]["轻度"] for p in pool))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
