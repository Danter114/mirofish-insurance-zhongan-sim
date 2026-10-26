#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_persona_library.py —— 参考画像库生成器（占位，不是交付物）

画像是画像库侧的交付物。这个脚本只做两件事：
  1. 让选人模块现在就能端到端跑通、被验证；
  2. 给画像库侧一份**字段填法的活样例**（比文档更不容易看错）。

★ v2.0 对齐组长需求 v2：
    · 角色 6 类（KOC 已删）
    · 一人一渠道：primary_channel 单值，且 (角色 x 渠道) 必须落在 20 个有效组合内
    · 池内主渠道分布跟随典型事件的渠道分布（按 role_channel 的阵地权重铺开）
    · 补齐新字段：cross_channel_propensity / influence_weight /
      emotion_sensitivity（按事件类型）/ action_thresholds（角色 x 动作，区间）

用法：
  python tools/mock_persona_library.py --n 2000 --out out/persona_library_mock.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config, largest_remainder, write_json      # noqa: E402
from persona_io import ROLE_CHANNEL                                 # noqa: E402

# 角色配比：保证每个角色都满足组长 2.6「池内条数 >= 单场配额的 3 倍」
POOL_RATIO = {"网民": 0.52, "客户": 0.20, "KOL": 0.11,
              "媒体": 0.07, "专业背书方": 0.06, "监管": 0.04}

SUBTYPES = {
    "网民": ["吃瓜围观", "段子手", "情绪化跟评", "理性讨论", "本地生活", "学生党", "宝妈", "职场人", "养宠人"],
    "客户": ["理赔被拒", "理赔拖延", "退保损失", "销售误导", "续保被拒", "正常投保未出险", "车险客户", "宠物险客户"],
    "KOL": ["财经解读", "法律科普", "保险测评", "医疗健康", "消费维权", "科技观察", "女性议题"],
    "媒体": ["财经周刊", "都市报", "门户科技", "行业垂媒", "地方广电", "财经自媒体", "女性媒体"],
    "专业背书方": ["执业律师", "精算师", "临床医生", "财经分析师", "保险经纪人", "消保专家"],
    "监管": ["总局派出机构", "地方金融监管局", "行业协会", "消保局", "信访窗口", "法院新媒体"],
}
FOLLOW_SCOPES = ["财经媒体", "法律维权", "母婴育儿", "车生活", "健康医疗", "本地民生",
                 "保险从业", "消费测评", "科技互联网", "监管动态", "女性议题", "宠物生活"]
REGIONS = ["北京", "上海", "广东深圳", "广东广州", "浙江杭州", "江苏南京", "四川成都",
           "湖北武汉", "山东青岛", "陕西西安", "湖南长沙", "福建厦门", "河南郑州", "辽宁沈阳"]
MBTI = ["INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP"]
AGES = ["18-24", "25-30", "31-40", "41-50", "51-60"]
OCCUPATIONS = ["企业职员", "公务员/事业单位", "自由职业", "个体经营", "学生", "教师",
               "医护人员", "IT从业者", "金融从业者", "全职妈妈", "退休"]
# 跨渠道搬运倾向：KOL / 媒体 高，普通网民极低（组长 2.4 ③）
CCP_BAND = {"网民": (0.02, 0.15), "客户": (0.05, 0.25), "KOL": (0.55, 0.95),
            "媒体": (0.60, 0.95), "专业背书方": (0.25, 0.60), "监管": (0.05, 0.20)}
# 影响力：可比尺度 0~1（不是绝对粉丝数）
INFLUENCE_BAND = {"网民": (1.2, 2.6), "客户": (1.5, 2.8), "KOL": (2.4, 4.2),
                  "媒体": (2.6, 4.4), "专业背书方": (2.0, 3.4), "监管": (2.4, 3.8)}

# 动作阈值：(角色 x 动作) 的区间，量纲与仿真的「效价 -1~+1 / 唤醒 0~1」同尺度。
# 组长 2.3 ④ 的例子：普通网民不能去黑猫投诉 —— 投诉阈值只对客户有意义，
# 网民即使气到极点，动作也只会是转发 / 评论 / 举报 / 在自己渠道发声。
ACTIONS = ["投诉", "举报", "转发扩散", "声援反驳", "评论质疑", "沉默"]
THRESHOLD_TABLE = {
    "客户":       {"投诉": [-0.60, 0.70], "举报": [-0.70, 0.80], "转发扩散": [-0.30, 0.50],
                   "声援反驳": [0.50, 0.60], "评论质疑": [-0.20, 0.40], "沉默": [-1.00, 0.00]},
    "网民":       {"投诉": None,           "举报": [-0.75, 0.85], "转发扩散": [-0.30, 0.50],
                   "声援反驳": [0.50, 0.60], "评论质疑": [-0.20, 0.40], "沉默": [-1.00, 0.00]},
    "KOL":        {"投诉": None,           "举报": [-0.80, 0.85], "转发扩散": [-0.25, 0.45],
                   "声援反驳": [0.45, 0.55], "评论质疑": [-0.15, 0.35], "沉默": [-1.00, 0.00]},
    "媒体":       {"投诉": None,           "举报": None,           "转发扩散": [-0.20, 0.40],
                   "声援反驳": [0.40, 0.50], "评论质疑": [-0.10, 0.30], "沉默": [-1.00, 0.00]},
    "专业背书方": {"投诉": None,           "举报": [-0.70, 0.80], "转发扩散": [-0.35, 0.55],
                   "声援反驳": [0.45, 0.60], "评论质疑": [-0.25, 0.45], "沉默": [-1.00, 0.00]},
    "监管":       {"投诉": None,           "举报": None,           "转发扩散": [-0.50, 0.60],
                   "声援反驳": [0.30, 0.50], "评论质疑": [-0.30, 0.30], "沉默": [-1.00, 0.00]},
}
BIOS = {
    "客户": "有{exp}经历，{stance}，主阵地{chan}。关心：{topics}。",
    "网民": "{sub}，{region}，{age}岁，{chan}重度用户，平时爱看{scope}。关心：{topics}。",
    "KOL": "{sub}账号，{chan}为主阵地，{stance}，长期关注{scope}。关心：{topics}。",
    "媒体": "持证媒体机构号（{sub}），{chan}为主阵地。关心：{topics}。",
    "专业背书方": "{sub}，第三方视角发声，{chan}为主阵地。关心：{topics}。",
    "监管": "{sub}，官方主体，只在{chan}发布。关心：{topics}。",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", default="out/persona_library_mock.json")
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--topics-min", type=int, default=3)
    ap.add_argument("--topics-max", type=int, default=5)
    ap.add_argument("--affinity-base", type=float, default=0.6,
                    help="议题抽样权重 = affinity_base + affinity 三档值；调大 -> 覆盖更均匀")
    args = ap.parse_args()

    rng = random.Random(args.seed)          # ★ 组长 3.5：独立 rng 实例，不用全局 seed
    tax = load_config("taxonomy")
    rules = load_config("rules")
    slots_cfg = load_config("slots")
    leaves = [lf["leaf"] for v in tax["categories"].values() for lf in v["leaves"]]
    leaf2top = {lf["leaf"]: t for t, v in tax["categories"].items() for lf in v["leaves"]}
    tops = list(tax["categories"].keys())
    aff = slots_cfg["affinity_tier"]
    ts = slots_cfg["tier_score"]
    rc = slots_cfg["role_channel"]
    counts = largest_remainder(POOL_RATIO, args.n)

    pool, idx = [], 0
    for role in slots_cfg["roles"]:
        chans = list(rc[role].keys())
        weights = [rc[role][c] for c in chans]
        # 主渠道按阵地权重铺开（组长 2.6 ①：分布要跟随典型事件的渠道分布）
        ch_alloc = largest_remainder({c: w for c, w in zip(chans, weights)}, counts[role])
        for c in chans:
            for _ in range(ch_alloc[c]):
                idx += 1
                sub = rng.choice(SUBTYPES[role])
                primary = c
                w = [args.affinity_base + ts[aff[role][leaf2top[l]]] for l in leaves]
                picked, used = [], set()
                for _ in range(rng.randint(args.topics_min, args.topics_max)):
                    cand = [leaves[i] for i in range(len(leaves)) if leaves[i] not in used]
                    if not cand:
                        break
                    lf = rng.choices(cand, weights=[w[leaves.index(x)] for x in cand])[0]
                    used.add(lf)
                    picked.append(lf)
                base = {"网民": [0.35, 0.45, 0.20], "客户": [0.62, 0.24, 0.14],
                        "KOL": [0.32, 0.40, 0.28], "媒体": [0.28, 0.52, 0.20],
                        "专业背书方": [0.34, 0.36, 0.30], "监管": [0.22, 0.56, 0.22]}[role]
                stance = rng.choices(["倾向维权", "中立", "倾向企业"],
                                     weights=[max(.01, b) for b in base])[0]
                band = {"网民": (5, 3000), "客户": (3, 1500), "KOL": (100000, 3000000),
                        "媒体": (50000, 8000000), "专业背书方": (1000, 400000),
                        "监管": (5000, 1500000)}[role]
                u = rng.random() ** 2.2
                followers = int(band[0] * (band[1] / band[0]) ** u)
                claim = (rng.random() < 0.85) if role == "客户" else (rng.random() < 0.35)
                scope = rng.sample(FOLLOW_SCOPES, rng.randint(2, 3))
                lo, hi = INFLUENCE_BAND[role]
                infl = round(min(0.999, max(0.001, rng.betavariate(lo, hi))), 4)
                clo, chi = CCP_BAND[role]
                ccp = round(rng.uniform(clo, chi), 3)
                emo = {t: round(rng.uniform(0.25, 0.95), 3) for t in tops}
                thr = {a: (None if v is None else [round(v[0] + rng.uniform(-.05, .05), 3),
                                                   round(v[1] + rng.uniform(-.05, .05), 3)])
                       for a, v in THRESHOLD_TABLE[role].items()}
                bio = BIOS[role].format(exp="理赔" if claim else "投保", stance=stance,
                                        chan=primary, sub=sub, region=rng.choice(REGIONS),
                                        age=rng.choice(AGES), scope="、".join(scope[:2]),
                                        topics="、".join(picked[:2]))
                pool.append({
                    "agent_id": "AG%05d" % idx,
                    "role_type": role,
                    "primary_channel": primary,
                    "subtype": sub,
                    "concern_topics": picked,
                    "stance_bias": stance,
                    "influence_weight": infl,
                    "follower_count": followers,
                    "activity_level": round(min(0.99, max(0.05, rng.betavariate(2.2, 2.6))), 3),
                    "active_hours": sorted(rng.sample(range(6, 24), rng.randint(3, 7))),
                    "cross_channel_propensity": ccp,
                    "emotion_sensitivity": emo,
                    "action_thresholds": thr,
                    "persona_bio": bio,
                    "follow_scope": scope,
                    "claim_experience": claim,
                    "region": rng.choice(REGIONS),
                    "gender": rng.choice(["女", "男"]),
                    "age_band": rng.choice(AGES),
                    "occupation": rng.choice(OCCUPATIONS),
                    "mbti": rng.choice(MBTI),
                    "complaint_propensity": round(min(0.99, rng.betavariate(2, 3) +
                                                       (0.25 if role == "客户" else 0)), 3),
                    "share_propensity": round(rng.betavariate(2, 2), 3),
                    "_note": "MOCK —— 参考样例，不是画像库侧的交付物",
                    "_source": "先验合成（非统计、非推断），仅供链路验证",
                })
    write_json(args.out, pool)
    print("生成参考画像库 %d 条 -> %s" % (len(pool), args.out))
    print("角色分布：", {r: counts[r] for r in slots_cfg["roles"]})
    print("配比校验（组长 2.6：池内 >= 单场配额 x3）：")
    for r in slots_cfg["roles"]:
        q = slots_cfg["quota"][r]
        print("   %-10s 池内 %4d  单场 %3d  倍数 %.1f  %s"
              % (r, counts[r], q, counts[r] / q, "OK" if counts[r] >= q * 3 else "不足"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
