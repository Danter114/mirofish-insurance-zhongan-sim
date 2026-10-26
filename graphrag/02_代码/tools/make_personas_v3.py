#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_personas_v3.py —— 带人设、且全字段一致的自洽画像生成器

流程（单向派生，后一步都从前面来，所以不可能冲突）：
    ① 抽【人设骨架】 age -> 职业(条件) -> gender(平台倾向) -> region -> channel(格位)
    ② 骨架 + config/persona_rules.json 决定每个话题落在四档之一
         key=重点关注  mild=略关注  weak=略不在乎  ban=完全不关注(键不出现)
    ③ 只在非 ban 话题里按 W1 x tier 权重抽样
    ④ bio 由【骨架 + 抽中的话题】反推 —— 与话题同源
    ⑤ 校验：冲突数必须为 0
"""
from __future__ import annotations
import argparse, json, sys
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from common import load_config, largest_remainder, write_json   # noqa: E402
from prng import DeterministicRNG, persona_rng, digest_of       # noqa: E402

CELLS = load_config("matrix_v3spec")
# ★ 数据表：城市人口 / 圈层权重 / 语句库（可单独替换，不改代码）
_PD = load_config("persona_data")
# ★ 城市权重 = 网民规模（人口 x 互联网普及率），不是人口
CITY_TABLE = [(c["name"], c.get("netizens_wan") or c["pop_wan"], c["tier"]) for c in _PD["cities"]]
CITY_POP = {c["name"]: c["pop_wan"] for c in _PD["cities"]}
CITY_NET = {c["name"]: c.get("netizens_wan") or c["pop_wan"] for c in _PD["cities"]}
CITY_BIO = {c["name"]: c.get("bio_region", c["name"]) for c in _PD["cities"]}
# ★ 2026-10-07：region 改为「省份城市」（如 广西南宁）；直辖市就是市名。
#   name 仍作 pop/net 的查表键，不能改。
CITY_REGION = {c["name"]: c.get("region") or c["name"] for c in _PD["cities"]}

# ---- 骨架：年龄（CNNIC 网民结构量级） -> 职业（条件） -> 性别/地域
# ★ 按 CNNIC《中国互联网络发展状况统计报告》网民年龄结构折算到本表年龄段
#   原始：10-19岁 13.6% / 20-29岁 14.4% / 30-39岁 20.9% / 40-49岁 18.5% / 50-59岁 17.5% / 60岁及以上 15.1%
#   折算：18-24=11.0  25-30=8.0  31-40=25.5  41-50=20.6  51-60=19.4  61-72=15.5（已归一）
# ★ 2026-10-07 修正：原表是【全国人口年龄结构】（41-72 岁占 33%），
#   原表导致名册中老年过多 -> 婚育被「已婚有孩」主导（47~51%），与直觉不符。
#   调整后 18-30 占 45%（原 24%），41-72 占 30%（原 52%）。
AGE_BANDS = [(18,24,0.240),(25,30,0.210),(31,40,0.250),(41,50,0.130),(51,60,0.100),(61,72,0.070)]
OCC_BY_AGE = {
 (18,24): [("学生",.42),("企业职员",.16),("自由职业",.10),("IT",.09),("个体经营",.07),("教师",.05),("医护",.04),("金融",.04),("全职妈妈",.03)],
 (25,30): [("企业职员",.28),("IT",.16),("自由职业",.12),("个体经营",.11),("金融",.09),("教师",.07),("医护",.06),("公务员",.06),("全职妈妈",.05)],
 (31,40): [("企业职员",.26),("个体经营",.15),("IT",.13),("全职妈妈",.12),("金融",.09),("教师",.08),("医护",.07),("自由职业",.06),("公务员",.04)],
 (41,50): [("企业职员",.24),("个体经营",.18),("公务员",.12),("教师",.10),("医护",.09),("金融",.09),("自由职业",.07),("全职妈妈",.06),("IT",.05)],
 (51,60): [("个体经营",.22),("企业职员",.18),("退休",.17),("公务员",.12),("教师",.10),("医护",.08),("自由职业",.07),("金融",.06)],
 (61,72): [("退休",.74),("个体经营",.12),("教师",.06),("医护",.04),("自由职业",.04)],
}
OCC_LIFE = {  # 生活状态句（由职业派生）
 "学生":["课业挺重，闲下来就刷刷手机","大四了，一边实习一边准备考试","刚上大二，住校，周末才回家"],
 "企业职员":["上班通勤单程要一个小时","年底冲业绩，加班比较多","公司最近在调整，气氛有点紧"],
 "IT":["项目赶进度，经常盯到半夜","远程办公两年了，作息有点乱","天天对着屏幕，颈椎不太好"],
 "金融":["行情波动大，早上第一件事就是看盘","客户催得紧，季末最忙"],
 "个体经营":["店里请了一个人帮忙","生意这两年不好做，成本一直在涨","自己看店，基本没休息日"],
 "自由职业":["接单不稳定，好的时候一个月顶三个月","时间自由，但收入也没保障"],
 "公务员":["工作节奏稳定，就是琐事多","在区里上班，朝九晚五"],
 "教师":["带两个班，作业改到晚上","寒暑假能喘口气，平时很满"],
 "医护":["三班倒，睡不够","科室人手紧，连轴转是常事"],
 "全职妈妈":["孩子上学去了，白天在家","一天三顿加接送，基本围着孩子转"],
 "退休":["退休两年了，每天早上去公园走走","帮儿女带孙子，日子挺满","老伴身体一般，跑医院比较勤"],
}
# ---- 新增 4 个维度（城乡 / 教育 / 收入 / 婚育）
#   ★ 2026-10-07：digital_literacy（数字素养）已彻底删除，不再生成
URBAN = [("一线",.13),("新一线",.17),("二线",.18),("三线",.20),("四线及以下",.20),("乡镇农村",.12)]
EDU_BY_AGE = {
 (18,24):[("高中及以下",.18),("大专",.22),("本科",.52),("硕士+",.08)],
 (25,30):[("高中及以下",.12),("大专",.20),("本科",.55),("硕士+",.13)],
 (31,40):[("高中及以下",.22),("大专",.26),("本科",.45),("硕士+",.07)],
 (41,50):[("高中及以下",.36),("大专",.26),("本科",.34),("硕士+",.04)],
 (51,60):[("高中及以下",.52),("大专",.22),("本科",.24),("硕士+",.02)],
 (61,72):[("高中及以下",.68),("大专",.16),("本科",.15),("硕士+",.01)],
}

# ═══════════════════════════════════════════════════════════════════════
# ★★ 2026-10-13 罕见组合过滤（A 方案：从生成器源头去掉）
#
#   背景：婚育在职业【之前】抽（第 476 行 vs 第 498 行），所以能抽出
#   「学生 × 已婚有孩」「18-24 × 已婚有孩」「61-72 × 未婚」这类组合。
#   实测占比：学生×已婚有孩 0.18% ｜ 18-24×已婚有孩 0.26% ｜ 61-72×未婚 0.15%。
#
#   为什么必须去掉：这些组合在真实舆情受众里几乎不存在，而且**写 bio 时必然显眼**
#   —— bio 只有 110 字，没法既提到「24 岁在校生有孩子」又让它不突出。
#   要么渲染成戏剧化故事（不真实），要么绕着走（不自然）。
#
#   做法：不做事后打补丁，而是【收紧可行域】—— 职业定稿后若组合非法，
#   从同一张表里重抽一个合法值（同一个 rng，只是候选集变小）。
# ═══════════════════════════════════════════════════════════════════════
MARITAL_FORBID = [
    # (判定条件, 禁用的婚育取值)
    (lambda age, occ, lo, hi: occ == "学生",
     {"已婚有孩", "已婚无孩", "离异", "丧偶"}),          # 在校生不婚不育
    (lambda age, occ, lo, hi: hi <= 24,
     {"已婚有孩", "离异", "丧偶"}),                       # 24 岁以下不可能是「有孩/离异/丧偶」
    (lambda age, occ, lo, hi: lo >= 61,
     {"已婚有孩"}),                                       # 25 岁以下全职妈妈罕见
    (lambda age, occ, lo, hi: occ == "退休",
     {"未婚"}),                                          # 退休且未婚罕见
    (lambda age, occ, lo, hi: occ in ("全职妈妈", "全职爸爸"),
     {"未婚", "已婚无孩", "离异", "丧偶"}),   # ★ 全职家长必须【已婚有孩】—— 未婚/没孩子不可能全职带
]


def fix_marital(rng, m, age, occ, lo, hi):
    """婚育与（年龄 × 职业）冲突时，从同一张表里重抽一个合法值。

    ★ 幂等：合法就原样返回，不消耗 rng —— 保证没冲突的人不受影响。
    """
    banned = set()
    for _cond, _bad in MARITAL_FORBID:
        if _cond(age, occ, lo, hi):
            banned |= _bad
    if m not in banned:
        return m
    pool = [(x, w) for x, w in MARITAL_BY_AGE[(lo, hi)]
            if MA_AGE_MIN[x] <= age and x not in banned]
    return _wpick(rng, pool)[0] if pool else m
INCOME_BY_EDU = {
 "高中及以下":[("3千以下",.28),("3-6千",.34),("6-10千",.25),("10-20千",.11),("2万以上",.02)],
 "大专":      [("3千以下",.15),("3-6千",.28),("6-10千",.33),("10-20千",.20),("2万以上",.04)],
 "本科":      [("3千以下",.08),("3-6千",.18),("6-10千",.32),("10-20千",.31),("2万以上",.11)],
 "硕士+":     [("3千以下",.03),("3-6千",.08),("6-10千",.22),("10-20千",.40),("2万以上",.27)],
}
# ★ 2026-10-07 微调：各年龄段未婚概率小幅上调（+2~8pp），已婚有孩对应下调。
#   理由：这张表原本按【全国人口婚育统计】定，但舆情参与者明显偏年轻、偏未婚 ——
#         未婚人群可支配上网时间更多、在热点话题上的参与更活跃；
#         实测调整前名册「已婚有孩 47~51%」压过「未婚 37~45%」，与直觉不符。
#   调整幅度刻意保守（每档 +2~8pp），不改变「年龄越大越可能已婚」的总体形状。
# ═══════════════════════════════════════════════════════════════════════
# ★★ 2026-10-07 新增：人口属性【可行性约束】
#   起因：全库扫描发现 51.6% 的人至少有一处属性矛盾 ——
#     年龄<学历最低要求 10.09%（18岁·本科）｜ 学历不满足职业 9.73%（高中及以下·金融）
#     年龄<职业最低要求  5.54%（18岁·企业职员）｜ 学生却有高收入 3.44%（学生·2万以上）
#     未婚却有孩话题    22.57%（未婚·亲子与育儿）
#   根因：EDU_BY_AGE / OCC_BY_AGE / MARITAL_BY_AGE / INCOME_BY_EDU 四张表
#         【只按年龄或学历分档，彼此不约束】，而且收入在职业【之前】抽。
#   修法：不做事后打补丁（会破坏确定性），而是【收紧可行域】——
#         在抽取时就只从合法取值里抽（同一个 rng，只是候选集变小）。
# ═══════════════════════════════════════════════════════════════════════
# ★ 学历字段的语义 =「教育程度（含在读）」，不是「已获学位」——
#   18 岁的大二学生自述「本科」是常态，所以本科下限取 18。
#   「18 岁不能当教师」是【职业准入】问题，由 AGE_OCC_MIN + EDU_OCC_MIN 双重挡住，
#   不该由学历字段承担。硕士在读最早 22 岁（本科 4 年）。
AGE_EDU_MIN = {"高中及以下": 18, "大专": 18, "本科": 18, "硕士+": 22}
# ★★ 2026-10-13：学历【完成】年龄（与 AGE_EDU_MIN 的「在读下限」不同）
#   本科 4 年 / 硕士再 3 年。正式职业要求学历已完成，否则会出现「21 岁本科做 IT」。
EDU_DONE_AGE = {"高中及以下": 18, "大专": 21, "本科": 22, "硕士+": 25}
# 需要学历的正式职业（自由职业/个体经营不受限 —— 学生兼职是常态）
FORMAL_OCC = ("IT", "企业职员", "教师", "医护", "金融", "公务员")
AGE_OCC_MIN = {"学生": 18, "自由职业": 18, "个体经营": 20, "全职妈妈": 20, "全职爸爸": 20,
               "企业职员": 21, "IT": 21, "教师": 22, "医护": 22, "金融": 22,
               "公务员": 23, "退休": 55}
EDU_OCC_MIN = {"教师": ("本科", "硕士+"), "医护": ("大专", "本科", "硕士+"),
               "公务员": ("大专", "本科", "硕士+"), "金融": ("大专", "本科", "硕士+"),
               "IT": ("大专", "本科", "硕士+")}
MA_AGE_MIN = {"未婚": 18, "已婚无孩": 20, "已婚有孩": 21, "离异": 23, "丧偶": 30}
# ★ 特殊职业的收入独立于学历 —— 学生不可能月入 2 万，退休金也不会因学历而两极分化
INCOME_BY_OCC = {
    "学生":     [("3千以下",.60),("3-6千",.29),("6-10千",.11)],   # ★ 无收入的学生不可能是 10-20千+
    "退休":     [("3千以下",.18),("3-6千",.34),("6-10千",.31),("10-20千",.14),("2万以上",.03)],
    "全职妈妈": [("3千以下",.30),("3-6千",.30),("6-10千",.24),("10-20千",.13),("2万以上",.03)],
    "全职爸爸": [("3千以下",.26),("3-6千",.28),("6-10千",.26),("10-20千",.16),("2万以上",.04)],
}


MARITAL_BY_AGE = {
 (18,24):[("未婚",.94),("已婚无孩",.04),("已婚有孩",.02)],
 (25,30):[("未婚",.58),("已婚无孩",.22),("已婚有孩",.18),("离异",.02)],
 (31,40):[("未婚",.26),("已婚无孩",.14),("已婚有孩",.56),("离异",.04)],
 (41,50):[("未婚",.10),("已婚无孩",.08),("已婚有孩",.74),("离异",.06),("丧偶",.02)],
 (51,60):[("未婚",.05),("已婚无孩",.10),("已婚有孩",.75),("离异",.06),("丧偶",.04)],
 (61,72):[("未婚",.03),("已婚无孩",.08),("已婚有孩",.70),("离异",.05),("丧偶",.14)],
}
CITY_BY_URBAN = {
 "一线":["北京","上海","广东深圳","广东广州"], "新一线":["浙江杭州","四川成都","湖北武汉","江苏南京","陕西西安","湖南长沙"],
 "二线":["福建厦门","山东青岛","安徽合肥","辽宁沈阳","河南郑州","江西南昌"],
 "三线":["河北石家庄","云南昆明","贵州贵阳","甘肃兰州","江苏南京","河南郑州"],
 "四线及以下":["河北石家庄","云南昆明","贵州贵阳","甘肃兰州","江西南昌"],
 "乡镇农村":["河南郑州","河北石家庄","云南昆明","贵州贵阳","甘肃兰州"],
}
REGIONS = [("北京",.09),("上海",.09),("广东深圳",.08),("广东广州",.07),("浙江杭州",.06),("江苏南京",.05),
 ("四川成都",.05),("湖北武汉",.05),("山东青岛",.04),("陕西西安",.04),("湖南长沙",.04),("福建厦门",.03),
 ("河南郑州",.04),("辽宁沈阳",.03),("河北石家庄",.03),("安徽合肥",.03),("江西南昌",.02),("云南昆明",.02),("贵州贵阳",.02),("甘肃兰州",.01)]

# ---- 话题语句（bio 由话题反推 —— 与 concern_topics 同源，所以不可能冲突）
PHRASE = {
 "就业与劳动权益":["身边同事聊得最多的是裁员和降薪","担心行业不景气，饭碗不稳"],
 "收入与物价":["感觉钱越来越不经花","买菜的时候会算一算"],
 "住房与租房":["房贷压力不小","房租又涨了，在考虑换地方"],
 "教育与升学":["孩子在念小学，比较关心升学政策","亲戚家孩子高考，跟着看了不少志愿的事"],
 "医疗健康与医保":["家里老人常年跑医院，医保报销的事会留意","自己体检报告有几项不太好"],
 "养老与托育":["开始琢磨自己以后养老怎么办","父母年纪大了，养老院的事在看"],
 "食品安全":["买菜买肉都会看一眼产地","外卖吃得多，也在意卫生"],
 "社会治安与违法犯罪":["小区群里常有人转反诈提醒","对诈骗的新闻比较敏感"],
 "事故灾难与生产安全":["看到火灾爆炸的新闻会多看两眼"],
 "自然灾害与应急救援":["沿海台风季，会提前备点东西"],
 "公共卫生事件":["经历过那几年，对传染病的事比较上心"],
 "交通出行安全":["每天开车通勤，路上事多","电动车新规出来之后一直在关注"],
 "基层治理与执法争议":["小区物业和业委会的事闹过一阵"],
 "国际关系与外交":["爱看时政评论，国际新闻基本不落"],
 "军事与国防":["对装备和军演的新闻有兴趣"],
 "政策法规与制度改革":["政策一变，很多事就得跟着调"],
 "反腐与政务监督":["单位里对这类通报比较敏感"],
 "港澳台与民族议题":["台海的新闻会留意"],
 "婚恋与家庭":["家里催婚催得紧","结婚几年了，日子过得挺平"],
 "亲子与育儿":["孩子上小学三年级，每天接送","刚生了二胎，累并快乐"],
 "心理健康":["工作压力大，偶尔会失眠","身边有朋友抑郁，对这事比较在意"],
 "旅行与消费生活方式":["一年出去玩两趟，喜欢自由行","周末爱逛市集和露营"],
 "宠物与养宠":["家里养了只猫，刷到宠物的会停下来看","养狗三年了，宠物的事比较上心"],
 "企业经营与财报":["会看上市公司的财报","同行几家最近都不太好过"],
 "行业监管与合规":["监管口径一变，业务就得重做","经常看罚单和约谈的通报"],
 "价格与市场垄断":["对大数据杀熟特别反感","平台抽成高，小商家很难"],
 "品牌营销与广告伦理":["对擦边营销很反感","广告翻车的新闻会多看一眼"],
 "消费者权益保护":["退换货遇到过扯皮，比较在意这个","办过一次预付卡跑路，从此长记性"],
 "金融与投资风险":["手里有理财也有保险，会盯行情","买过基金，亏了之后谨慎多了"],
 "人工智能与算法":["工作里已经在用AI了","算法推荐把我困在信息茧房里"],
 "平台治理与内容审核":["刷到过好几次封号限流的事"],
 "隐私与个人信息保护":["接到的推销电话太多了","对刷脸和过度索权很警惕"],
 "网络安全与诈骗":["身边有人被电诈过，印象很深"],
 "科技创新与产业":["关注芯片和新能源的进展"],
 "体育赛事":["球赛基本每场都看","奥运和世界杯从不落下"],
 "游戏与电竞":["平时打游戏，也追赛事","手游氪了不少"],
 "全民健身与户外":["每周跑三次步","周末喜欢骑行或者爬山"],
 "影视综艺":["追剧比较凶，一周能刷完一部","综艺下饭，吃饭必开"],
 "明星与粉丝文化":["追星好几年了，超话天天签到"],
 "音乐与演出":["演唱会抢票是每年的大事"],
 "短视频与网络红人":["刷短视频的时间比看剧还多","关注了不少博主，也看直播带货"],
}
TIER_WEIGHT = {"key": 3.0, "mild": 1.0, "weak": 0.25, "ban": 0.0}
# ══════════════════════════════════════════════════════════════════════
# ★★ emotion_sensitivity —— 组长 2026-10-06 新口径
#   语义：**同一件事，这个人的情绪反应被放大几倍**
#   形态：{事件类型: 倍数}   ← 和【事件】挂钩，不是和话题挂钩
#   范围：**0.5 ~ 2.0**   全体均值 **≈ 1.0**
#
#   三重因子相乘：
#     ① 角色基准（本身已让全体加权均值 = 1.000）
#     ② 个体因子（每人一个，跨事件类型相同）—— 让个人值散开到 0.5~2.0
#     ③ 事件类型因子（每类一个，全员相同）—— 不同事件类型的情绪强度
# ══════════════════════════════════════════════════════════════════════
# ① 角色基准。★ 按画像库角色配额加权 = 1.000
#   .57x0.94 + .23x1.24 + .11x1.09 + .06x0.74 + .03x0.59 = 1.000
# ★ 2026-10-07 实测校准：0.5/2.0 两端夹取会截掉低尾，把全体均值从 1.000 抬到 1.038。
#   这里按 1/1.038 预校准，使【夹取之后】的全体均值回到 1.00（实测 0.9997）。
SENS_BASE = {"网民": 0.906, "客户": 1.195, "KOL": 1.050, "媒体": 0.713, "监管": 0.569}
# ② 个体因子：对数正态，中位 1.0，sigma 0.28 -> 个人值大致落在 0.5~2.0
SENS_IND_SIGMA = 0.28
# ③ 事件类型因子（12 类，均值 = 1.0）
SENS_ET = {
    "理赔类": 1.15,        # 切身利益受损，情绪最强
    "数据与隐私类": 1.10,
    "服务态度类": 1.05,
    "营销类": 1.05,        # 道德义愤型
    "销售与投保类": 1.00,
    "代理维权类": 1.00,
    "产品与费率类": 0.95,
    "产品线专项类": 0.95,
    "诉讼与司法类": 0.90,
    "保险欺诈类": 0.90,
    "公司经营类": 0.85,    # 远离个人切身
    "雇主品牌类": 0.85,
}
SENS_LO, SENS_HI = 0.5, 2.0


def _event_types():
    """12 个事件类型标签（与 config/event_type_enum.json 对齐）"""
    try:
        em = load_config("event_type_enum")
        vals = list(dict.fromkeys((em.get("taxonomy_to_enum") or {}).values()))
        # 补上 SENS_ET 里有但枚举没有的，保证不漏
        return sorted(set(vals) | set(SENS_ET))
    except Exception:
        return sorted(SENS_ET)


def draw_emotion_sensitivity(rng, role, topics):
    """按【47 话题】给情绪放大倍数。

    ★ 2026-10-07 组长口径修正：
      键 = 该人 interest_topics 里的话题（去掉「其他」）—— 和兴趣直接挂钩
      值 = 情绪放大倍数，**0.5~2.0，全体均值 ≈ 1.0**
      语义 = 同一件事落到这个话题上，他的情绪反应被放大几倍

    三重因子相乘：
      base  角色基准（按画像库配额加权 = 1.000）
      ind   个体因子（每人一个，跨话题相同）—— 对数正态，中位 1.0
      tf    话题权重因子 = 该话题权重 / 他所有话题权重的均值
            ★ 关注度越高 -> 情绪反应越大；tf 自身均值 = 1.0，不影响总体均值
    """
    ind = math.exp(rng.gauss(0.0, SENS_IND_SIGMA))
    base = SENS_BASE.get(role, 1.0)
    ks = [t for t, w in (topics or {}).items() if t != "其他" and (w or 0) > 0]
    if not ks:
        return {}, round(ind, 3)
    ws = [topics[t] for t in ks]
    wm = sum(ws) / float(len(ws))
    out = {}
    for t, w in zip(ks, ws):
        tf = min(1.8, max(0.5, (w / wm) if wm > 0 else 1.0))
        out[t] = round(min(SENS_HI, max(SENS_LO, base * ind * tf)), 2)
    return out, round(ind, 3)


SENS = {"网民":1.2,"客户":1.1,"KOL":0.95,"媒体":0.7,"监管":0.6}   # 旧口径，仅供遗留引用
INF = {"网民":(.05,.15),"客户":(.10,.25),"KOL":(.20,.70),"媒体":(.50,.80),"监管":(.60,.90)}
VAL = {"网民":(-.5,.5),"客户":(-.3,.3),"KOL":(-.4,.4),"媒体":(-.1,.1),"监管":(-.05,.05)}
ARO = {"网民":(.3,.8),"客户":(.3,.7),"KOL":(.3,.7),"媒体":(.1,.4),"监管":(.1,.3)}
DEC = {"网民":(.70,.75),"客户":(.75,.80),"KOL":(.75,.85),"媒体":(.85,.90),"监管":(.85,.90)}
CON = {"网民":(.60,.90),"客户":(.50,.80),"KOL":(.40,.80),"媒体":(.20,.50),"监管":(.10,.30)}
THR = {"网民":[(-.25,-.35),(-.39,-.61),None],"客户":[(-.31,-.39),(-.46,-.64),(-.61,-.89)],
 "KOL":[(-.36,-.44),(-.52,-.68),None],
 "媒体":[(-.47,-.53),(-.65,-.75),None],"监管":[(-.58,-.62),(-.77,-.83),None]}
SHARE = {"网民":{"微博":(.62,.78),"抖音":(.52,.68),"小红书":(.32,.48),"新闻门户":(.22,.38),"黑猫投诉":(.12,.28),"监管通报":(.02,.18)},
 "客户":{"微博":(.48,.64),"抖音":(.40,.56),"小红书":(.24,.40),"新闻门户":(.16,.32),"黑猫投诉":(.08,.24),"监管通报":(.00,.16)},
 "KOL":{"微博":(.80,.96),"抖音":(.67,.83),"小红书":(.42,.58),"新闻门户":(.30,.46),"黑猫投诉":(.17,.33),"监管通报":(.05,.21)},
 
 "媒体":{"微博":(.69,.85),"抖音":(.58,.74),"小红书":(.36,.52),"新闻门户":(.25,.41),"黑猫投诉":(.14,.30),"监管通报":(.03,.19)},
 "监管":{"微博":(.27,.43),"抖音":(.22,.38),"小红书":(.12,.28),"新闻门户":(.07,.23),"黑猫投诉":(.02,.18),"监管通报":(.00,.13)}}
COMP = {"网民":(.12,.28),"客户":(.32,.48),"KOL":(.17,.33),"媒体":(.02,.18),"监管":(.00,.13)}
COMP_HM = {"网民":(.32,.48),"客户":(.62,.78),"KOL":(.37,.53),"媒体":(.12,.28)}
DECAY = {"微博":(.7,.9),"抖音":(.6,.8),"小红书":(.4,.6),"新闻门户":(.1,.3),"黑猫投诉":(.3,.5),"监管通报":(.1,.2)}
REACH = {"微博":{"网民":1.40,"客户":1.60,"KOL":2.40,"媒体":2.80,"监管":3.00},
 "抖音":{"网民":1.75,"客户":2.00,"KOL":3.00,"媒体":3.50,"监管":3.75},
 "小红书":{"网民":1.05,"客户":1.20,"KOL":1.80,"媒体":2.10,"监管":2.25},
 "新闻门户":{"网民":0.70,"客户":0.80,"KOL":1.20,"媒体":1.40,"监管":1.50},
 "黑猫投诉":{"网民":0.42,"客户":0.48,"KOL":0.72},
 "监管通报":{"网民":0.91,"媒体":1.82,"监管":1.95}}
EMOAMP = {"微博":{"网民":1.12,"客户":1.28,"KOL":1.92,"媒体":2.24,"监管":2.40},
 "抖音":{"网民":1.33,"客户":1.52,"KOL":2.28,"媒体":2.66,"监管":2.85},
 "小红书":{"网民":0.84,"客户":0.96,"KOL":1.44,"媒体":1.68,"监管":1.80},
 "新闻门户":{"网民":0.46,"客户":0.52,"KOL":0.78,"媒体":0.91,"监管":0.98},
 "黑猫投诉":{"网民":0.70,"客户":0.80,"KOL":1.20},
 "监管通报":{"网民":0.32,"媒体":0.63,"监管":0.68}}
# ===================== ★ 组长 v3 §2.6 动作池（5 个） =====================
# 引用 / 转发 / 评论 / 点赞 / 沉默
#   · 监管 = {引用, 沉默} —— 【唯一例外】：监管每个动作都是公开表态
#   ·「客户投诉」「媒体报道」「监管通报」不是独立动作，都是【引用】落在各自 primary_channel
#   · 引用替代发帖：上游有值 = 引用，上游为空 = 退化成发帖
#   · 强度阶梯：沉默 < 点赞 < 评论 < 转发 < 引用
# ★ 已砍掉我们旧版的 post / report / complaint 三个动作
_ACT_5 = ["like", "comment", "repost", "quote"]
ROLE_ACTIONS = {
    "网民": list(_ACT_5), "客户": list(_ACT_5), "KOL": list(_ACT_5), "媒体": list(_ACT_5),
    "监管": ["quote"],                       # {引用, 沉默}；沉默不是动作，故只列 quote
}
# 引用的落位语义（组长 v3 §5.6.4）：同一个动作，在不同角色/渠道上叫不同的事
QUOTE_MEANING = {"网民": "发帖", "客户": "投诉", "KOL": "表态", "媒体": "报道", "监管": "通报"}
_ACT_CH = ["微博", "抖音", "小红书", "黑猫投诉", "新闻门户", "监管通报"]
ACTIONS = {(r, c): list(ROLE_ACTIONS[r]) for r in ROLE_ACTIONS for c in _ACT_CH}

# ===================== ★ 组长 v3 §2.2-11/13 角色x渠道规则表（15 格） =====================
# 🟨 粒度：同格同值，【不跟画像个性化】。此前我们做成了每人一个随机值 —— 粒度错了。
def _mid(band):
    return round((float(band[0]) + float(band[1])) / 2.0, 3)

# 渠道对动作门槛的位移（正 = 门槛更高、更难被触发）
CH_THR_SHIFT = {"微博": 0.00, "抖音": -0.02, "小红书": -0.01,
                "黑猫投诉": -0.05, "新闻门户": 0.03, "监管通报": 0.05}
ACTION_THRESHOLDS_15 = {}   # (role,ch) -> {"轻度","中度","重度"}
EMOTION_DECAY_15 = {}       # (role,ch) -> 数值
for _r in ROLE_ACTIONS:
    for _c in _ACT_CH:
        if (_r, _c) not in ACTIONS:
            continue
        _b = THR[_r]
        # 该格有没有「引用」权决定重度档是否存在（组长：越线推荐档位的存在性由动作集决定）
        _has_q = "quote" in ACTIONS[(_r, _c)]
        # ★ 渠道位移：门槛 = 情绪要跌到多低才倾向动作。
        #   广场型（微博/抖音）门槛低，专属场（黑猫/门户/通报）门槛高 —— 后者不是随便发声的地方。
        _shift = CH_THR_SHIFT.get(_c, 0.0)
        ACTION_THRESHOLDS_15[(_r, _c)] = {
            "轻度": round(_mid(_b[0]) + _shift, 3), "中度": round(_mid(_b[1]) + _shift, 3),
            "重度": (round(_mid(_b[2]) + _shift, 3) if (_b[2] and _has_q) else None),
        }
        EMOTION_DECAY_15[(_r, _c)] = _mid(DECAY[_c])

# ===================== ★ 组长 v3 §2.2-12 active_level（15 格基线 + 外向性抖动） =====
# active_level(i) = 基线[role,channel] + 0.15 x (外向性_i − 0.5)，夹到 [0.05, 0.95]
# ★ 基线数值由【画像侧】定（组长点名）—— 参考组长给的实体默认值：官方 0.2 / 媒体 0.5 / 普通 0.7
ACTIVE_BASE_ROLE = {"网民": 0.70, "客户": 0.55, "KOL": 0.85, "媒体": 0.90, "监管": 0.20}
ACTIVE_BASE_CH = {"微博": 1.10, "抖音": 1.10, "小红书": 1.00,
                  "黑猫投诉": 0.60, "新闻门户": 0.80, "监管通报": 0.80}
ACTIVE_BASE_15 = {(r, c): round(min(0.95, max(0.05, ACTIVE_BASE_ROLE[r] * ACTIVE_BASE_CH[c])), 3)
                  for r in ACTIVE_BASE_ROLE for c in _ACT_CH}
EXTRAVERSION_JITTER = 0.15

# ★★ 2026-10-07 深夜在线时段（0-5 点）——【只允许年轻人】（<=32 岁）
#   分布要求：0-2 时人较多，3-5 时人较少（递减）
#   独立随机流 night:<aid>，不扰动主流
NIGHT_P = {0: 0.42, 1: 0.32, 2: 0.22, 3: 0.14, 4: 0.09, 5: 0.06}
NIGHT_MAX_AGE = 32
# ★★ 2026-10-07 新增：深夜（0-5 点）活跃的【职业】白名单 ——
#   原先只看年龄（<=32 岁就能加深夜时段），不看职业，导致 543 个公务员、
#   637 个教师在 0-4 点活跃（全库 19,222 人深夜在线，占 42%，明显偏高）。
#   作息规律、不该熬夜的职业被排除：公务员 / 教师 / 企业职员 / 退休。
NIGHT_OK_OCC = {"学生", "自由职业", "个体经营", "IT", "金融", "医护",
                "全职妈妈", "全职爸爸"}
# ★ 收入按年龄封顶（月入档位序号）：<=21 岁最高 6-10千；22-24 最高 10-20千
INCOME_RANK = {"3千以下": 0, "3-6千": 1, "6-10千": 2, "10-20千": 3, "2万以上": 4}

# ★ gender 受话题倾向影响的程度（0 = 只看平台，1 = 话题主导）
GENDER_TOPIC_PULL = 1.30

# ===================== ★ 组长 v3 §2.2-4 性格（两数值，删 MBTI） =====================
# emotional_stability 情绪稳定性：高的不易被激怒（做情绪噪声的语义解释）
# extraversion        外向性：驱动 active_level 的个体抖动
# ===================== ★ 组长 v3 §2.2-8 / J7 stance_base（🟦 基础倾向） =====================
# stance_bias[议题] = stance_base x 议题对齐度      ← 组长的公式
#   「中立」是【结果】不是【值】：对不关心的议题，乘积自然接近 0
#   base 是跨事件稳定的总体倾向（对"企业/机构"的默认态度），负 = 偏批评、正 = 偏维护
STANCE_BASE_BY_ROLE = {
    "网民": (-0.55, 0.25),   # 偏批评 —— 围观者天然站弱势方
    "客户": (-0.75, -0.15),  # 明显偏批评 —— 当事人，有直接利益受损
    "KOL":  (-0.60, 0.20),   # 偏批评 —— 批判性内容更吸粉
    "媒体": (-0.30, 0.35),   # 接近中立偏正 —— 要讲客观、讲信源，也常有"别一边倒"的声音
    "监管": (-0.05, 0.50),   # 中立偏正 —— 程序导向，不预设立场，但会强调"依法依规、别带节奏"
}

STABILITY_BY_ROLE = {"网民": (0.25, 0.75), "客户": (0.20, 0.60), "KOL": (0.35, 0.80),
                     "媒体": (0.55, 0.90), "监管": (0.70, 0.95)}
EXTRAV_BY_ROLE = {"网民": (0.20, 0.70), "客户": (0.25, 0.65), "KOL": (0.65, 0.95),
                  "媒体": (0.40, 0.75), "监管": (0.10, 0.40)}

# ★ 用 config/persona_data.json 的语句库与城市表覆盖内置版本
PHRASE = {k: list(v) for k, v in _PD["phrases"].items()} or PHRASE
TOPIC_AGES = _PD.get("topic_ages") or {}
PH_COND = _PD.get("phrases_conditional") or {}
# ★ 平台语气库（规范 v3 的平台基调表）：bio 的写法 = 平台基调 叠加 角色身份
try:
    VOICE = (load_config("platform_voice").get("platforms") or {})
except Exception:
    VOICE = {}
# ★ 机构集体人设（媒体/监管）：规范 v1.0 §3.4
try:
    ORG_P = load_config("org_persona")
except Exception:
    ORG_P = {}
ORG_ROLES = ("媒体", "监管")
SENIOR_ALT = _PD.get("senior_alt") or {}
OCC_LIFE = {k: list(v) for k, v in _PD["occ_life"].items()} or OCC_LIFE


def _wpick(rng, pairs, k=1):
    items = [p[0] for p in pairs]; w = [p[1] for p in pairs]
    return rng.weighted_sample_without_replacement(items, w, k)


def _apply_circle_weights(rules):
    """用 config/persona_data.json 的圈层权重覆盖规则文件里的 w"""
    cw = _PD.get("circles_weight") or {}
    for z in rules.get("circles", {}).get("list", []):
        if z["id"] in cw:
            z["w"] = cw[z["id"]][0]
    return rules


def draw_followers(rng, role, ch):
    """★ 抽粉丝数 —— influence_weight 的主要来源，不再只由角色决定。

    数据源里 29 个作者只有 4 个有真粉丝数（32~3612），不足以反推分布，
    故用 config/persona_data.json :: followers（C 级：平台量级常识 + 渠道系数）。
    """
    import math as _m
    f = (_PD.get("followers") or {})
    cfg = (f.get("by_role") or {}).get(role) or {"dist": "lognormal", "median": 200,
                                                 "sigma": 1.5, "min": 5, "max": 10000}
    m = f.get("channel_mult") or {}
    mult = m.get(ch, 1.0)
    if cfg.get("dist") == "pareto":
        # ★ 幂律（Pareto）：x = x_min * u^(-1/alpha)
        #   媒体用这个 —— 真实媒体粉丝量是极端长尾：县级融媒体几千到几万，
        #   省级都市报几十万，央视/人民日报上亿。幂律才能表达「大媒体非常稀少」。
        a = float(cfg.get("alpha", 1.3))
        u = max(1e-9, rng.random())
        v = (cfg["min"] * mult) * (u ** (-1.0 / a))
    else:
        # 对数正态：中位数 med，对数标准差 sigma/2
        med = cfg["median"] * mult
        v = med * _m.exp(rng.gauss(0.0, cfg["sigma"] * 0.5))
    return int(max(cfg["min"], min(cfg["max"], round(v))))


def influence_of(followers, role):
    """★ influence_weight 由【角色类型】与【粉丝数】双驱动，各占 50%

        influence_weight = 0.50 x role_base + 0.50 x follower_factor

        role_base      角色基本盘：媒体 0.80（机构公信力）/ 网民 0.10（无身份加成）
        follower_factor log10(1+followers)/7   1千=0.43  1万=0.57  10万=0.71  100万=0.86  1000万=1.0

        ★ 两者是并列的两股力量：媒体靠身份，网民靠粉丝。
          只算角色 -> 同是网民，3600 粉和 32 粉算成一样；
          只算粉丝 -> 80 万粉的机构号和 80 万粉的个人号算成一样。
    """
    import math as _m
    im = _PD.get("influence_model") or {}
    rb = (im.get("role_base") or {}).get(role, 0.2)
    ff = _m.log10(1 + max(0, followers)) / 7.0
    return round(min(0.95, max(0.05, 0.50 * rb + 0.50 * ff)), 3)


def skeleton(rng, role, ch, rules):
    """① 抽人设骨架（9 个维度，单向派生）：
       年龄 -> 城乡 -> 教育 -> 收入 -> 职业 -> 婚育 -> 兴趣圈层 -> 性别/地域
       ★ 监管/媒体不取 18-24 段（不该存在「公务员 x 18-24」这种组合）"""
    bands = AGE_BANDS if role not in ("监管",) else [b for b in AGE_BANDS if b[0] >= 25]
    # ★ 按【该平台的活跃网民】年龄分布抽，不是全局网民分布
    _pw = (_PD.get("platform_age", {}).get("weights") or {}).get(ch) or {}
    _wts = [_pw.get("%d-%d" % (b[0], b[1]), b[2]) for b in bands]
    ai = rng.choice_index(_wts)
    lo, hi, _ = bands[ai]
    age = rng.randint(lo, hi)
    occ_pairs = OCC_BY_AGE[(lo, hi)]
    _otype = None
    if role in ORG_ROLES:                      # ★ 机构类型必须先定 —— 话题抽样要在领域内做
        _otype = _wpick(rng, (ORG_P.get(role) or {}).get("org_types") or [["机构", 1]])[0]
    _ct = _wpick(rng, [(c[0], c[1]) for c in CITY_TABLE])[0]
    urban = next(c[2] for c in CITY_TABLE if c[0] == _ct)
    edu = _wpick(rng, [(e, w) for e, w in EDU_BY_AGE[(lo, hi)] if AGE_EDU_MIN[e] <= age])[0]
    # ★ income 移到 occ 之后（学生 / 退休 的收入必须独立于学历）
    marital = _wpick(rng, [(m, w) for m, w in MARITAL_BY_AGE[(lo, hi)] if MA_AGE_MIN[m] <= age])[0]
    # 圈层：按圈层权重 x 年龄匹配度抽
    cs = rules.get("circles", {}).get("list", [])
    def _amatch(c):
        for a in c.get("ages", [[0, 99]]):
            if a[0] <= age <= a[1]: return 1.0
        return 0.0                      # ★ 年龄不符 -> 完全排除，不给残留概率
    cw = [(c["id"], c["w"] * _amatch(c)) for c in cs]
    cw = [(k, w) for k, w in cw if w > 0] or [(c["id"], c["w"]) for c in cs]
    cid = _wpick(rng, cw)[0]
    circle = next(c for c in cs if c["id"] == cid)
    if role == "监管":
        occ = "公务员"
    elif role == "媒体":
        occ = _wpick(rng, [("企业职员",.3),("自由职业",.25),("教师",.2),("公务员",.15),("IT",.1)])[0]
    else:
        # ★ 职业可行域：年龄下限 + 学历门槛双重过滤
        _occ_ok = [(o, w) for o, w in occ_pairs
                   if AGE_OCC_MIN.get(o, 18) <= age
                   and (o not in EDU_OCC_MIN or edu in EDU_OCC_MIN[o])]
        if not _occ_ok:                               # 兜底：该年龄该学历一个都不剩
            _occ_ok = [(o, w) for o, w in occ_pairs if AGE_OCC_MIN.get(o, 18) <= age] or [("自由职业", 1.0)]
        occ = _wpick(rng, _occ_ok)[0]
    if age < 25 and occ == "公务员":    # ★ 不该存在「公务员 x 18-24」
        occ = "企业职员"
    if age >= 62:                        # ★ 62 岁以上一律退休，避免"66 岁还三班倒"
        occ = "退休"
    # ★★ 2026-10-13：18-24 岁不可能是全职家长。
    #   全职家长要求「已婚有孩」（规则 B），但 24 岁以下禁「已婚有孩」（规则 C），
    #   两条互斥 -> fix_marital 的候选集为空 -> 兜底保留原值「未婚」-> 造出矛盾（实测 13 人）。
    #   这里直接把职业改掉，从源头消解冲突。
    if hi <= 24 and occ in ("全职妈妈", "全职爸爸"):
        occ = "自由职业"
    # ★★ 2026-10-13：学历与职业的【完成时序】校验 ——
    #   ① 硕士+ 需 25 岁（本科 22 + 3）
    #   ② 正式职业（IT/企业职员/教师/医护/金融/公务员）要求学历已完成
    #   ③ 学历被降级后，可能不再满足职业门槛（EDU_OCC_MIN），需再兜一层
    if edu == "硕士+" and age < EDU_DONE_AGE["硕士+"]:
        edu = ("本科" if age >= EDU_DONE_AGE["本科"]
               else ("大专" if age >= EDU_DONE_AGE["大专"] else "高中及以下"))
    if occ in FORMAL_OCC and age < EDU_DONE_AGE[edu]:
        occ = "学生" if age <= 24 else "自由职业"
    if occ in EDU_OCC_MIN and edu not in EDU_OCC_MIN[occ]:
        occ = "自由职业"

    # ★★ 2026-10-13：婚育必须在【职业定稿之后】再过一遍 ——
    #   婚育原本在职业之前抽，会抽出「学生 × 已婚有孩」这类罕见组合。
    marital = fix_marital(rng, marital, age, occ, lo, hi)
    circle = fix_circle(rng, circle, marital, rules)
    # ★ 收入必须在 occ【完全定稿之后】抽 —— 否则 62 岁被改成退休、
    #   或 18-24 岁公务员被改成企业职员时，收入还按旧职业配，又是一处矛盾。
    # ★★ 2026-10-07：收入再加【年龄 + 城乡】封顶 ——
    #   原先只看职业/学历，实测越界 805 人：<=21 岁月入 1-2 万 437、22-24 岁月入 2 万+ 238、
    #   乡镇农村月入 2 万+ 101。
    _icap = 2 if age <= 21 else (3 if age <= 24 else 4)
    if urban == "乡镇农村":
        _icap = min(_icap, 3)
    _itab = INCOME_BY_OCC.get(occ) or INCOME_BY_EDU[edu]
    _iok = [(k, w) for k, w in _itab if INCOME_RANK[k] <= _icap] or _itab[:1]
    income = _wpick(rng, _iok)[0]
    income = fix_income(rng, income, occ, age)
    fem = (rules.get("platform_demographics", {}).get(ch) or {}).get("gender_female", 0.5)
    gender = "女" if rng.random() < fem else "男"
    # ★ 性别硬修正（必须在 occ 与 gender 都定了之后）：
    #   男性不能是「全职妈妈」。踩过：29 岁男 + 全职妈妈 + 未婚。
    if occ == "全职妈妈" and gender == "男":
        occ = "全职爸爸"
    elif occ == "全职爸爸" and gender == "女":
        occ = "全职妈妈"
    region = CITY_REGION.get(_ct, _ct)   # ★ 省份城市（如 广西南宁）
    # ★★ 2026-10-13：性别修正可能把 occ 改成「全职妈妈/爸爸」—— 此时 marital 已定稿，
    #   必须再校验一次，否则会留下「未婚 × 全职妈妈」这种矛盾（实测漏 13 人）。
    #   fix_marital 幂等：合法时直接返回、不消耗 rng。
    marital = fix_marital(rng, marital, age, occ, lo, hi)
    bio_region = CITY_BIO.get(_ct, _ct)
    return {"age": age, "age_band": "%d-%d" % (lo, hi), "occ": occ, "gender": gender, "region": region,
            "urban": urban, "edu": edu, "income": income, "marital": marital,
            "bio_region": bio_region,
            "circle": circle, "org_type": _otype}


def fix_circle(rng, circle, marital, rules):
    """无孩者不该混「母婴育儿」圈。

    ★★ 2026-10-13 修：这条规则原先只作用在 interest_topics（话题「亲子与育儿」），
       没管 interest_circle（圈层「母婴育儿」）—— 两个字段名不同，实测漏了 43 人。
    """
    if not isinstance(circle, dict):
        return circle
    if marital not in ("未婚", "已婚无孩"):
        return circle
    if circle.get("id") != "母婴育儿":
        return circle
    cs = rules.get("circles", {}).get("list", []) or []
    cand = [c for c in cs if isinstance(c, dict) and c.get("id") and c["id"] != "母婴育儿"]
    if not cand:
        return circle
    _pairs = [(c["id"], float(c.get("w") or 0.0)) for c in cand]
    if sum(w for _, w in _pairs) <= 0:
        _pairs = [(cid, 1.0) for cid, _ in _pairs]
    cid = _wpick(rng, _pairs)[0]
    for c in cand:
        if c["id"] == cid:
            return c
    return cand[0]


def fix_income(rng, inc, occ, age):
    """退休金不该到 2 万+（实测 3 人）。"""
    if occ != "退休" or inc != "2万以上":
        return inc
    pool = [("10-20千", .35), ("6-10千", .65)] if age <= 65 else [("6-10千", .7), ("3-6千", .3)]
    return _wpick(rng, pool)[0]


def tiers_of(sk, ch, topics, rules, CT=None):
    """② 骨架 + 规则 -> 每个话题的档位

    默认【略不在乎】—— 对任何一个人来说，47 个话题里绝大部分是他不关心的。
    只有被规则命中或与大类内重点话题同属一类，才升到 mild / key。
    """
    t = {x: "weak" for x in topics}
    # ★ 第 7 维度：圈层决定主要 key
    for x in sk.get("circle", {}).get("key", []):
        if x in t: t[x] = "key"
    for r in rules.get("key", []):   # key 先算，后面同大类扩散要用
        pass
    for r in rules.get("key", []):
        w = r["when"]; ok = True
        if "occ_in" in w and sk["occ"] not in w["occ_in"]: ok = False
        if "age_min" in w and sk["age"] < w["age_min"]: ok = False
        if "age_max" in w and sk["age"] > w["age_max"]: ok = False
        if "ch" in w and ch != w["ch"]: ok = False
        if ok:
            for x in r["key"]:
                if x in t: t[x] = "key"
    # ★ 同大类扩散：重点话题所在的大类，其余话题升为「略关注」
    if CT:
        kc = {CT[x] for x in t if t[x] == "key"}
        for x in t:
            if t[x] == "weak" and CT.get(x) in kc: t[x] = "mild"
    for r in rules.get("damp", []):   # ★ 放在同大类扩散之后，否则降权会被扩散盖回去
        w = r["when"]; ok = True
        if "age_min" in w and sk["age"] < w["age_min"]: ok = False
        if "age_max" in w and sk["age"] > w["age_max"]: ok = False
        if "occ_in" in w and sk["occ"] not in w["occ_in"]: ok = False
        if ok:
            for x in r["damp"]:
                if x in t and t[x] in ("mild", "weak"): t[x] = "weak"
    # ★ 婚育约束必须在【所有 key 规则之后】执行，否则会被职业规则设回 key
    if sk.get("marital") in ("未婚", "已婚无孩"):
        for x in ("亲子与育儿", "教育与升学"):
            if t.get(x) == "key": t[x] = "mild"
    for r in rules.get("ban", []):
        w = r["when"]; ok = True
        if "age_min" in w and sk["age"] < w["age_min"]: ok = False
        if "age_max" in w and sk["age"] > w["age_max"]: ok = False
        if "occ_in" in w and sk["occ"] not in w["occ_in"]: ok = False
        if ok:
            for x in r["ban"]:
                if x in t: t[x] = "ban"
    return t


def _when_ok(w, sk):
    """条件句的 when 是否满足人设骨架"""
    if not w: return True
    if "age" in w and not (w["age"][0] <= sk["age"] <= w["age"][1]): return False
    if "marital" in w and sk.get("marital") not in w["marital"]: return False
    if "occ" in w and sk.get("occ") not in w["occ"]: return False
    if "gender" in w and sk.get("gender") != w["gender"]: return False
    return True


def pick_sentence(topic, sk, rng, used=None):
    """★ 跨维度条件化取句：先挑 when 全满足的条件句，没有就用通用句。
    每个话题至少有一条通用句，所以永远取得到。"""
    used = used or set()
    P = PH_COND.get(topic)
    if not P:
        arr = PHRASE.get(topic) or []
        c = [s for s in arr if s not in used]
        return rng.choice(c) if c else None
    c = [b["s"] for b in (P.get("by") or []) if _when_ok(b.get("when"), sk) and b["s"] not in used]
    if c: return rng.choice(c)
    c = [s for s in (P.get("gen") or []) if s not in used]
    if c: return rng.choice(c)
    c = [b["s"] for b in (P.get("by") or []) if _when_ok(b.get("when"), sk)]
    if c: return rng.choice(c)
    c = list(P.get("gen") or [])          # ★ 最后兜底：宁可重复也要有依据
    return rng.choice(c) if c else None


def pick_stance(ch, val, role):
    """从【平台默认立场】+【情绪基准效价】派生这个人的立场。
    规范 v3 要求 bio『须能从文本读出立场』，但库里没有 stance_bias 字段，
    所以立场必须由已有维度派生 —— 可复现、可解释。"""
    p = VOICE.get(ch) or {}
    if ch in ("新闻门户", "监管通报"):
        thr = 0.20          # 理性/程序导向：要更强的效价才表态
    elif ch in ("小红书", "黑猫投诉"):
        thr = 0.05          # 维权倾向：容易表态
    else:
        thr = 0.12          # 微博/抖音：情绪先行
    if role == "监管":
        thr = max(thr, 0.35)
    if val < -thr: return "neg"
    if val > thr: return "pos"
    return "neu"


def make_bio(rng, sk, picked, tier, _core_val=0.0):
    """④ bio 由骨架 + 话题反推 —— 与 concern_topics 同源"""
    a = sk["age"]
    _ch = sk.get("channel") or ""
    _v = VOICE.get(_ch) or {}
    _conn = _v.get("conn") or [""]
    _reg = sk.get("bio_region") or sk["region"]
    # ① 开篇：平台化的年龄/性别/地域写法
    _op = rng.choice(_v.get("open") or ["{age}岁，{gender}性，在{region}。"])
    s1 = _op.replace("{age}", str(a)).replace("{gender}", sk["gender"]).replace("{region}", _reg)
    # ② 平台身份句（规范 v3 平台基调表的『典型身份』）
    _self = rng.choice(_v.get("self") or [""])
    _life_pool = OCC_LIFE.get(sk["occ"], ["日子过得挺平常"])
    _HARD = ("通勤","加班","客户催","赶进度","季末","三班倒","连轴","上线前","住在公司")
    if sk["age"] >= 58:                       # 临近退休不用「通勤/加班/三班倒」这类句子
        _p2 = [s for s in _life_pool if not any(k in s for k in _HARD)]
        _life_pool = _p2 or ["日子过得挺平常"]
    if sk["age"] >= 62 and sk["occ"] != "退休":
        _life_pool = [s for s in _life_pool if not any(k in s for k in _HARD)] or ["日子过得挺平常"]
    life = rng.choice(_life_pool)
    parts = []
    # ★ 圈层语句只有在【它对应的话题确实是 key/mild】时才用（否则就自相矛盾了）
    _seen = set()
    # ★ 只写 key / mild 话题 —— weak 是「略不在乎」，写进自述就自相矛盾了
    _say = [t for t in picked if tier.get(t, "weak") in ("key", "mild")] or picked
    _LIFE_T = ("旅行与消费生活方式", "影视综艺", "音乐与演出", "明星与粉丝文化", "游戏与电竞",
               "全民健身与户外", "体育赛事", "宠物与养宠", "短视频与网络红人",
               "食品安全", "住房与租房", "婚恋与家庭", "亲子与育儿")
    if _ch in ("监管通报", "黑猫投诉"):        # 公文/诉求平台只讲与事件相关的关切
        _say = [t for t in _say if t not in _LIFE_T] or _say
    _say = _say[:1]
    for tp in _say:
        _use = pick_sentence(tp, sk, rng, _seen)
        if _use and _use != life:
            _seen.add(_use); parts.append(_use)
    if parts and _ch in ("微博", "抖音", "小红书"):
        _c = rng.choice(_conn)
        if _c and not _c.endswith(("：", "，")): _c += "，"
        parts[0] = _c + parts[0]
    _clean = lambda z: z.rstrip("。；")
    _parts2 = [_clean(life)]
    if _self: _parts2.append(_clean(_self))
    s2 = "。".join([x for x in _parts2 if x]) + "。"
    # ③ 立场句（平台默认立场 × 该人效价）—— 规范 v3 硬要求
    _sp = (_v.get("stancePhrase") or {})
    _st = pick_stance(_ch, _core_val, sk.get("role") or sk.get("occ_role") or "")
    s2b = rng.choice(_sp.get(_st) or [""])
    if s2b: s2 = s2.rstrip("。") + "。" + s2b
    # 话题句：最多 1 句，放在末尾，避免 bio 过长（规范 v3：1-3 句）
    s3 = (parts[0].rstrip("。") + "。") if parts else ""
    return s1 + s2 + s3


def make_org_bio(rng, role, ch, val, ot_forced=None):
    """★ 机构集体人设 —— 不出现年龄、性别、婚育、生活状态。
    结构：① 机构类型 + 账号定位，覆盖范围 ② 语体 + 信息来源 ③ 立场基调"""
    o = ORG_P.get(role) or {}
    ot = ot_forced or _wpick(rng, o.get("org_types") or [["机构", 1]])[0]
    pos = _wpick(rng, o.get("positions") or [["信息发布", 1]])[0]
    sc = _wpick(rng, o.get("scopes") or [["市级", 1]])[0]
    s1 = "%s的%s账号，覆盖%s。" % (ot, pos, sc)
    s2 = rng.choice(o.get("styles") or ["以正式发布为准。"]) + "。" + \
         rng.choice(o.get("sources") or ["信息来源以公开材料为准。"]) + "。"
    if role == "监管":
        thr = 0.35
    elif ch in ("新闻门户",):
        thr = 0.20
    else:
        thr = 0.12
    st = "neg" if val < -thr else ("pos" if val > thr else "neu")
    s3 = rng.choice((o.get("stances") or {}).get(st) or [""])
    return s1 + s2 + s3, {"org_type": ot, "org_position": pos, "org_scope": sc, "org_stance": st}


def finalize_bio(pool, master_seed, org_roles):
    """★ 最后一步生成 persona_bio。

    架构原则：先把【所有其他参数】按跨维度方式生成并定稿，最后再生成 bio ——
    而不是边生成参数边写 bio（那样 bio 写的是 normalize 之前的状态，必然与最终参数矛盾）。

      ① 从 _bio_sku 取回骨架 / 抽中话题 / 效价
      ② 用【最终的 concern_tier】重算可写话题（只保留仍在库里的 key/mild）
      ③ 生成 + 去重（沿用同一分支，机构不会退化成个人）
    """
    used = set()
    ORGSFX = {
        "媒体": ["本账号接受读者报料。", "欢迎提供新闻线索。", "转载须注明来源。",
                "报道以见报稿为准。", "线索一经采用将回访核实。"],
        "监管": ["本账号不受理个案投诉。", "咨询请通过官方渠道。", "内容以正式文件为准。",
                "具体事项请向属地部门反映。", "本账号不回应个案咨询。"]}
    PSFX = ["日子就这么过着。", "没什么特别的。", "算不上多精彩。", "大体上还算安稳。",
            "偶尔也会觉得累。", "忙起来就顾不上这些。", "身边人也差不多。", "说不上好也说不上坏。"]
    for x in pool:
        sku = x.pop("_bio_sku", None) or {}
        sk = dict(sku.get("sk") or {})
        val = sku.get("val", 0.0)
        role, ch = x["role_type"], x["channel"]
        sk["channel"] = ch; sk["role"] = role
        tier = dict(x.get("concern_tier") or {})
        lib = x.get("interest_topics") or {}
        picked = [t for t in (sku.get("picked") or []) if t in lib]
        picked += [t for t in lib if t not in picked and t != "其他"]
        rng = persona_rng(master_seed, "bio:" + x["agent_id"])
        if role in org_roles:
            ot = x.get("org_type")
            def _gen(_r, _role=role, _ch=ch, _v=val, _ot=ot):
                return make_org_bio(_r, _role, _ch, _v, _ot)[0]
        else:
            def _gen(_r, _sk=sk, _pk=picked, _tr=tier, _v=val):
                return make_bio(_r, _sk, _pk, _tr, _v)
        bio = _gen(rng); _t = 0
        while bio in used and _t < 40:
            bio = _gen(rng); _t += 1
        base, sfx = bio, 1
        sl = ORGSFX.get(role) or PSFX
        while bio in used:
            bio = base.rstrip("。") + "。" + rng.choice(sl); sfx += 1
            if sfx > 200:
                bio = base.rstrip("。") + "（%d）" % sfx; break
        used.add(bio)
        x["persona_bio"] = bio
    return pool



def normalize(pool, rules, W1):
    """★ 生成后统一规范化 —— 把所有硬不变量在这里一次性钉死，
    不依赖生成时的执行顺序（顺序错了也不会有漏网的）。"""
    CN = ["亲子与育儿", "教育与升学"]
    from prng import DeterministicRNG as _DR
    for x in pool:
        _rng = _DR(20261002, "norm:" + x["agent_id"])
        _added = []
        if x.get("role_type") in ORG_ROLES:
            # ★ 机构集体人设：bio 由 make_org_bio 一次写成，normalize 不再改动它
            # ★ 但 emotion_sensitivity 仍要算（键 = 该机构的 interest_topics）
            _emo, _emo_ind = draw_emotion_sensitivity(
                _DR(20261002, "emo:" + x["agent_id"]), x["role_type"], x["interest_topics"])
            x["emotion_sensitivity"] = _emo
            x["emotion_sensitivity_individual"] = _emo_ind
            continue
        ct, tier, cw = x["interest_topics"], x["concern_tier"], x["channel_behavior"]
        # ① 未婚/无孩 -> 育儿类不得是 key
        if x.get("marital_status") in ("未婚", "已婚无孩"):
            for t in CN:
                if tier.get(t) == "key": tier[t] = "mild"
        # ② 圈层是这个人的主标签 —— 它的 key 话题【必须】出现在画像里，没有就补进去
        c = next((z for z in rules["circles"]["list"] if z["id"] == x["interest_circle"]), None)
        _nochild = x.get("marital_status") in ("未婚", "已婚无孩")
        if c:
            cand = [t for t in c["key"] if tier.get(t) != "ban"
                    and not (_nochild and t in CN)]
            if cand:
                # 先看有没有已经在库里的
                have = [t for t in cand if t in ct and tier.get(t) == "key"]
                inside = [t for t in cand if t in ct]
                if not have and inside:
                    tier[inside[0]] = "key"
                elif not inside:
                    # ★ 一个都没抽中 -> 用圈层 key 替换掉一个 weak 话题
                    weaks0 = [t for t, v in tier.items() if v == "weak" and t in ct]
                    pick = cand[0]
                    if weaks0:
                        drop = weaks0[0]
                        del ct[drop]; del tier[drop]
                    ct[pick] = 0.01; tier[pick] = "key"; _added.append(pick)
        # ③ 任何人必须有 key：没有就从 mild 提
        if not any(v == "key" for v in tier.values()):
            _ok = lambda t: not (_nochild and t in ["亲子与育儿", "教育与升学"])
            milds = [t for t, v in tier.items() if v == "mild" and _ok(t)]
            if milds:
                tier[milds[0]] = "key"
            elif ct:
                cand0 = [t for t in ct if t != "其他" and _ok(t)] or [t for t in ct if t != "其他"]
                if cand0: tier[cand0[0]] = "key"
        # ③b 「其他」太高说明关注太散 -> 从 allowed 里补 mild 话题进来
        _o = ct.get("其他", 0.2)
        if _o > 0.48:
            need = 3 if _o > 0.65 else (2 if _o > 0.58 else 1)
            pool_avail = [t for t in W1 if tier.get(t) in (None, "mild") and t not in ct
                          and not (_nochild and t in CN)]
            pool_avail.sort(key=lambda t: -W1[t])
            for t in pool_avail[:need]:
                ct[t] = 0.01; tier[t] = "mild"; _added.append(t)

        # ④ 权重按档位重新分配（保证 key 有下限、weak 有上限）
        keys = [t for t, v in tier.items() if v == "key" and t in ct]
        milds = [t for t, v in tier.items() if v == "mild" and t in ct]
        weaks = [t for t, v in tier.items() if v == "weak" and t in ct]
        if not (keys or milds):
            continue
        wsum = 1.0 - ct.get("其他", 0.20)
        wsum = min(max(wsum, 0.70), 0.92)      # ★ 关注度更集中（原 0.55~0.85）
        kshare = 0.68 if keys else 0.0          # ★ 重点话题拿更多（原 0.60）
        mshare = 0.27 if milds else 0.0
        wshare = max(0.05, 1.0 - kshare - mshare)
        neww = {}
        def split(arr, tot, cap, floor):
            if not arr: return
            tot = min(tot, cap * len(arr)) if cap else max(tot, floor * len(arr))
            if cap: tot = min(tot, cap * len(arr))
            each = tot / len(arr)
            for t in arr:
                v = round(each, 2)
                if cap: v = min(v, cap)
                neww[t] = max(0.01, v)
        split(keys, wsum * kshare, None, 0.05)
        # ★ mild 的单个权重不得超过单个 key（否则档位与权重倒挂）
        _ke = min([neww[t] for t in keys], default=0.20) if keys else 0.20
        split(milds, wsum * mshare, max(0.03, min(0.20, _ke * 0.95)), 0.03)
        _me = min([neww[t] for t in milds], default=0.10) if milds else 0.10
        split(weaks, wsum * wshare, max(0.01, min(0.05, _me * 0.5)), 0.01)
        # 归一 + 补「其他」
        tot = sum(neww.values())
        if tot > 0:
            # ★ 关键修正：分给话题的总额必须达到 wsum，否则余量会全落到「其他」
            scale = wsum / tot
            neww = {t: max(0.01, round(v * scale, 2)) for t, v in neww.items()}
        other = round(1.0 - sum(neww.values()), 2)
        if other < 0.15:
            over = 0.15 - other
            big = max(neww, key=lambda t: neww[t])
            neww[big] = max(0.01, round(neww[big] - over, 2))
            other = round(1.0 - sum(neww.values()), 2)
        x["interest_topics"] = {t: neww[t] for t in sorted(neww, key=lambda z: -neww[z])}
        x["interest_topics"]["其他"] = other
        x["concern_tier"] = {t: tier[t] for t in x["interest_topics"] if t != "其他"}
        # ═══════════════════════════════════════════════════════════════
        # ★★ 2026-10-07 gender 与 interest_topics 关联（用户口径）
        #   ① 平台基础：platform_demographics[渠道].gender_female
        #   ② 话题倾向：该人 interest_topics 的 FEM_TOPIC 加权得分（-1~+1）
        #   ③ 修正：p = clip(平台基础 × (1 + TOPIC_PULL × 得分), 0.05, 0.95)
        #   ④ 职业硬约束：全职妈妈 → 必女；全职爸爸 → 必男
        #   ★ 用独立随机流 gender:<aid> —— 改这条规则不扰动其他字段
        _fem = ((rules.get("platform_demographics") or {}).get(x["channel"]) or {}).get("gender_female", 0.5)
        _ft = (load_config("gender_topic") or {}).get("map") or {}
        _nz = [(t, w) for t, w in x["interest_topics"].items() if t != "其他" and (w or 0) > 0]
        _sw = sum(w for _, w in _nz)
        _sc = (sum(w * _ft.get(t, 0.0) for t, w in _nz) / _sw) if _sw > 0 else 0.0
        _p = min(0.95, max(0.05, _fem * (1.0 + GENDER_TOPIC_PULL * _sc)))
        if x.get("occupation") == "全职妈妈":
            _p = 1.0
        elif x.get("occupation") == "全职爸爸":
            _p = 0.0
        x["gender"] = "女" if _DR(20261002, "gender:" + x["agent_id"]).random() < _p else "男"
        x["gender_source"] = ("平台 %.2f × 话题倾向 %+.2f -> p=%.3f" % (_fem, _sc, _p))
        #   ★ bio 用的是 _bio_sku 里那份骨架，必须同步，否则 bio 与 gender 打架
        if isinstance(x.get("_bio_sku"), dict) and isinstance(x["_bio_sku"].get("sk"), dict):
            x["_bio_sku"]["sk"]["gender"] = x["gender"]
        #   性别硬修正（occupation 有性别语义）
        if x.get("occupation") == "全职妈妈" and x["gender"] == "男":
            x["occupation"] = "全职爸爸"
        elif x.get("occupation") == "全职爸爸" and x["gender"] == "女":
            x["occupation"] = "全职妈妈"
        # ═══════════════════════════════════════════════════════════════

        # ⑤ 重度档：该格没有【越线动作权】就为空
        #   ★★ 2026-10-07 修复：此处原判 "complaint" not in available_actions，
        #      但 complaint 早已在 5 动作池（like/comment/repost/quote）里被删，
        #      条件【恒为真】—— 15 格的「重度」被无条件清空。
        #      实测：代码算出 客户.重度 = -0.75，交付件却是 null。
        #      改判当前的越线动作 quote（ACTION_THRESHOLDS_15 已按 THR[role][2]
        #      与 _has_q 定过存在性 —— 只有【客户】三档齐全）。
        th = x["action_thresholds"]
        if th.get("重度") is not None and "quote" not in cw["available_actions"]:
            th["重度"] = None
        # ⑤b 新注入的话题要补进 bio（否则 bio 与 concern_topics 脱钩）
        if _added:
            _sk2 = {"age": x["age"], "marital": x.get("marital_status"), "occ": x.get("occupation"),
                    "gender": x.get("gender")}
            _ph = []
            for t in _added:
                _s2 = pick_sentence(t, _sk2, _rng)
                if _s2: _ph.append(_s2)
            _ph = [s for s in _ph if s not in x["persona_bio"]]
            if _ph:
                x["persona_bio"] = (x["persona_bio"].rstrip("。") + "。"
                                    + _rng.choice(["另外，", "还有，", "另外一点，"]) + "，".join(_ph) + "。")
        # ⑤c 归一化后档位可能变了 -> 给尚未在 bio 里出现过的 key/mild 话题补句
        _sk3 = {"age": x["age"], "marital": x.get("marital_status"),
                "occ": x.get("occupation"), "gender": x.get("gender")}

        _LIFE_T2 = ("旅行与消费生活方式", "影视综艺", "音乐与演出", "明星与粉丝文化", "游戏与电竞",
                    "全民健身与户外", "体育赛事", "宠物与养宠", "短视频与网络红人",
                    "食品安全", "住房与租房", "婚恋与家庭")
        _missing_sent = []
        for _t, _v in x["concern_tier"].items():
            if _v not in ("key", "mild"):
                continue
            if x.get("channel") in ("监管通报", "黑猫投诉") and _t in _LIFE_T2:
                continue
            _P = PH_COND.get(_t)
            _all = ([b["s"] for b in (_P.get("by") or [])] + list(_P.get("gen") or [])) if _P else list(PHRASE.get(_t) or [])
            if _all and not any(s in x["persona_bio"] for s in _all):
                _s3 = pick_sentence(_t, _sk3, _rng)
                if _s3: _missing_sent.append(_s3)
        if _missing_sent:
            _missing_sent = [s for s in _missing_sent if s not in x["persona_bio"]]
            if _missing_sent:
                # ★ 只补 1 句，且 bio 总长控制在 160 字内（规范 v3：1-3 句）
                if len(x["persona_bio"]) < 120:
                    x["persona_bio"] = x["persona_bio"].rstrip("。") + "。" + _missing_sent[0] + "。"
        # ⑥ emotion_sensitivity 必须与【最终】interest_topics 的键集合完全一致。
        #   ★ 旧实现只做「删除」（剔掉不在 interest_topics 里的），不做「补齐」——
        #     而 normalize 前面的步骤会往 interest_topics 【加】话题，
        #     导致 9599/50000 的人出现「interest_topics 有、emotion_sensitivity 没有」。
        #   现在双向对齐：已有的保留，缺的按同一公式补。
        # ★★ emotion_sensitivity：键必须 == 最终 interest_topics 去掉「其他」的键集合，
        #   值 0.5~2.0、全体均值 ≈ 1.0。此处 interest_topics 已定稿，是唯一正确的计算点。
        _emo, _emo_ind = draw_emotion_sensitivity(
            _DR(20261002, "emo:" + x["agent_id"]), x["role_type"], x["interest_topics"])
        x["emotion_sensitivity"] = _emo
        x["emotion_sensitivity_individual"] = _emo_ind
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261002)
    args = ap.parse_args()
    rules = _apply_circle_weights(load_config("persona_rules"))
    lib = load_config("concern_topics_library")
    W1 = {x["leaf"]: x["w1"] for x in lib["leaves_flat"]}
    CT = {x["leaf"]: x["cat"] for x in lib["leaves_flat"]}
    TOPICS = list(W1.keys())
    quota = {"网民":285,"客户":115,"KOL":55,"媒体":30,"监管":15}   # v6.0 组长口径   # ★ v5.0 去掉专业背书方后的新配额
    alloc = largest_remainder({"%s|%s" % (c, r): w for c, r, w in CELLS}, args.n)

    EVENT_TYPES = _event_types()          # ★ 12 个事件类型（emotion_sensitivity 的键）
    pool, used, tierstat, banstat = [], set(), Counter(), Counter()
    for (ch, role, _w) in CELLS:
        cnt = alloc.get("%s|%s" % (ch, role), 0)
        for nn in range(1, cnt + 1):
            aid = "%s_%s_%03d" % (ch, role, nn)
            rng = persona_rng(args.seed, aid)
            sk = skeleton(rng, role, ch, rules)
            tier = tiers_of(sk, ch, TOPICS, rules, CT)
            for t, v in tier.items(): (tierstat if v != "ban" else banstat)[v] += 1
            allowed = [t for t in TOPICS if tier[t] != "ban"]
            # ★ 机构账号只在【报道领域 / 管辖范围】内抽话题
            if role in ORG_ROLES and sk.get("org_type"):
                _dom = ((ORG_P.get(role) or {}).get("domains") or {}).get(sk["org_type"]) or []
                _in = [t for t in allowed if t in _dom]
                if _in: allowed = _in
            k = rng.randint(3, 6)
            ckeys = set(sk.get("circle", {}).get("key", []))
            wts = [max(W1[t], 1e-9) * TIER_WEIGHT[tier[t]] * (5.0 if t in ckeys else 1.0)
                   * (1.6 if tier[t] == "mild" else 1.0)      # ★ mild 提权，压低「其他」
                   for t in allowed]
            picked = rng.weighted_sample_without_replacement(allowed, wts, k)
            # ★ 圈层是这个人的主标签 —— 圈层 key 里若有被抽中的，必须有一个当重点
            _ck = [t for t in sk["circle"]["key"] if t in picked and tier[t] != "ban"]
            if _ck and not any(tier[t] == "key" for t in _ck):
                tier[_ck[rng.randint(0, len(_ck) - 1)]] = "key"
            # ★ 硬保证：任何人都不该没有重点话题
            if not any(tier[t] == "key" for t in picked):
                milds = [t for t in picked if tier[t] == "mild"]
                if milds:
                    tier[milds[rng.randint(0, len(milds) - 1)]] = "key"      # 从已有 mild 提一个
                else:
                    cl = sk["circle"]["key"]
                    _nochild = sk.get("marital") in ("未婚", "已婚无孩")
                    cand = [t for t in cl if t in allowed and t not in picked
                            and not (_nochild and t in ("亲子与育儿", "教育与升学"))] or allowed
                    extra = rng.weighted_sample_without_replacement(
                        cand, [max(W1[t], 1e-9) for t in cand], 1)[0]
                    if extra not in picked: picked[-1] = extra
                    tier[extra] = "key"
            # ★ 至少要有一个「关注」的话题
            if not any(tier[t] in ("key", "mild") for t in picked):
                cand = [t for t in allowed if tier[t] == "mild"] or allowed
                extra = rng.weighted_sample_without_replacement(
                    cand, [max(W1[t], 1e-9) for t in cand], 1)[0]
                if extra not in picked:
                    picked[-1] = extra
                tier[extra] = "mild" if tier[extra] == "weak" else tier[extra]
            # 权重切分：key 话题占大头
            # ★ 权重严格按档位分配：key 拿大头、mild 中等、weak 很小
            wsum = rng.uniform(0.70, 0.92)
            keyp = [t for t in picked if tier[t] == "key"]
            mildp = [t for t in picked if tier[t] == "mild"]
            weakp = [t for t in picked if tier[t] == "weak"]
            concern = {}
            # 各档占比（有人缺档就把份额让给下一档）
            kshare = 0.60 if keyp else 0.0
            mshare = 0.32 if mildp else 0.0
            # ★ 缺档不让渡份额 —— 没有重点话题的人，本来关注就分散，余额落到「其他」
            wshare = max(0.05, 1.0 - kshare - mshare)
            for arr, share, cap in ((keyp, kshare, None), (mildp, mshare, 0.20), (weakp, wshare, 0.05)):
                if not arr: continue
                tot = wsum * share
                if cap: tot = min(tot, cap * len(arr))      # ★ weak 绝不拿大权重
                if not cap and share >= 0.5:                 # ★ key：每个至少 0.05
                    tot = max(tot, min(0.05 * len(arr), wsum * 0.85))
                cuts = sorted(rng.random() for _ in range(len(arr) - 1))
                edges = [0.0] + cuts + [1.0]
                for i, t in enumerate(arr):
                    v = tot * (edges[i+1] - edges[i])
                    if cap: v = min(v, cap)
                    concern[t] = max(0.01, round(v, 2))
            concern = {t: v for t, v in concern.items() if v > 0}
            concern["其他"] = round(1 - sum(concern.values()), 2)
            if concern["其他"] < 0.15:
                over = 0.15 - concern["其他"]
                big = max([t for t in concern if t != "其他"], key=lambda t: concern[t])
                concern[big] = round(concern[big] - over, 2)
                concern["其他"] = 0.15
            _val0 = round(rng.uniform(*VAL[role]), 2)      # ★ 先生成效价，立场要从它派生
            _followers = draw_followers(rng, role, ch)      # ★ 粉丝数 -> influence_weight 的主来源
            # ★★ 组长 v3 §2.2-8 / J7：stance_base = 总体倾向（🟦 每人一个，跨事件稳定）
            _stance_base = round(rng.uniform(*STANCE_BASE_BY_ROLE.get(role, (-0.4, 0.3))), 3)
            # 库内也写一份 stance_bias（= base）作为默认值；
            # 事件级版本由 t0_init 按【本场议题】覆盖（🟨 由画像侧按事件算）
            _stance_bias_default = _stance_base
            # ★ 机构字段（org_type / org_position / org_scope）仍在此处取；bio 文本留到 finalize_bio
            _org = None
            if role in ORG_ROLES:
                # ★ 用【独立随机流】取机构字段 —— bio 相关逻辑不得扰动主流的其他参数，
                #    否则改一次 bio 规则就换一批人（选人结果必须与 bio 实现解耦）
                _, _org = make_org_bio(persona_rng(args.seed, "orgsku:" + aid),
                                       role, ch, _val0, sk.get("org_type"))
            # ★★ bio 延迟生成：先把【所有其他参数】按跨维度方式定稿（含 normalize 的档位调整），
            #    最后再由 finalize_bio() 统一写 bio —— 这样 bio 与参数天然一致，不需事后打补丁。
            bio = ""
            _bio_sku = {"sk": sk, "picked": list(picked), "val": _val0}
            # ★ emotion_sensitivity 延迟到 normalize() 末尾再算 ——
            #   它必须跟随【最终】interest_topics 的键集合（normalize 会加/减话题）
            emo, emo_ind = {}, 1.0
            # ★★ 组长 v3 §2.2-4：性格两字段（MBTI 已删）
            _stab = round(rng.uniform(*STABILITY_BY_ROLE[role]), 2)   # 情绪稳定性
            _extr = round(rng.uniform(*EXTRAV_BY_ROLE[role]), 2)      # 外向性
            # ★★ 组长 v3 §2.2-12：active_level = 15格基线 + 0.15 x (外向性 − 0.5)
            _active = round(min(0.95, max(0.05,
                ACTIVE_BASE_15.get((role, ch), 0.7) + EXTRAVERSION_JITTER * (_extr - 0.5))), 3)
            # ★★ 组长 v3 §2.2-11/13：action_thresholds 与 emotion_decay 是【角色x渠道规则表】
            #    15 格同格同值，不跟画像个性化 —— 此前我们做成每人随机，粒度错了
            # ★★ 组长 v3 §2.2-9：active_hours 生成 24 维 0–1 向量
            # ★★ 真实作息表（此前是 rng.sample(6..23, 3~7) 纯随机 —— 人人 12 小时在线、职业无差异）
            _ahp = _PD.get("active_hours_profile") or {}
            _shift = int((_ahp.get("by_channel_shift") or {}).get(ch, 0))
            _age_v = int(sk.get("age") or 30)
            if _age_v <= 24:
                _shift += int((_ahp.get("_age_shift") or {}).get("le24", 0))
            elif _age_v >= 60:
                _shift += int((_ahp.get("_age_shift") or {}).get("ge60", 0))
            if role in ORG_ROLES:
                _base_h = list(rng.choice((ORG_P.get(role) or {}).get("hours")
                                          or [[9, 10, 11, 14, 15, 16, 17]]))
            else:
                _base_h = list((_ahp.get("by_occ") or {}).get(sk.get("occ"))
                               or _ahp.get("_default_occ") or [12, 13, 20, 21])
                _base_h = rng.sample(_base_h, rng.randint(2, len(_base_h)))   # 只在这个人群的作息里挑
            _ah_hours = sorted({(h + _shift) % 24 for h in _base_h})
            # ★★ 2026-10-07 深夜时段（0-5）：【只允许年轻人】，且 0-2 多、3-5 少
            #   踩过：2 时一个人都没有（by_occ 里没有 2 点，shift 最多把 23 推到 0）；
            #         4-5 点全是退休老人早起，不是年轻人熬夜。
            #   ★ 独立随机流 "night:<aid>" —— 不扰动主流，不影响其他字段
            _night_rng = persona_rng(20261002, "night:" + aid)   # 模块级导入，normalize 里的 _DR 是局部的
            _ns = set(_ah_hours)
            if (_age_v <= NIGHT_MAX_AGE and role not in ORG_ROLES
                    and sk.get("occ") in NIGHT_OK_OCC):        # ★ 加职业白名单
                for _h, _pr in NIGHT_P.items():
                    if _night_rng.random() < _pr:
                        _ns.add(_h)
            else:
                _ns = {h for h in _ns if h >= 6}   # 非年轻人 / 机构 不得出现在 0-5
            # ★★ 2026-10-07 兜底：不允许空在线时段 ——
            #   实测 20 个退休 60+ 的人 _base_h + _shift 后全部 < 6，被上面的 {h>=6} 过滤光，
            #   active_hours=[] 会让仿真层「这个人永远不上线」。
            if not _ns:
                _ns = {8, 10, 14, 16}
            _ah_hours = sorted(_ns)
            # ★★ active_hours 就是【在线小时的列表】，不生成强度向量
            #    （旧的「主时段 0.6~1.0 + 邻接时段 0.1~0.4」向量已废弃）
            _at15 = ACTION_THRESHOLDS_15.get((role, ch)) or {"轻度": -0.4, "中度": -0.6, "重度": None}
            _dec15 = EMOTION_DECAY_15.get((role, ch), 0.75)
            pool.append({
                "agent_id": aid, "channel": ch, "role_type": role, "persona_bio": bio,
                "followers": _followers,
                # ★ 个体差异噪声有界（±0.08）—— 无界高斯会把低粉账号偶尔顶到 0.95 上限
                "influence_weight": round(min(0.95, max(0.05, influence_of(_followers, role)
                                          + max(-0.08, min(0.08, rng.gauss(0, 0.035))))), 3),
                "influence_score": round(rng.uniform(*INF[role]), 2),
                "valence_baseline": _val0,
                # ★★ 组长 v3 §2.2-8：stance_base（🟦 跨事件稳定）+ stance_bias（默认=base，事件级由画像侧覆盖）
                "stance_base": _stance_base,
                "stance_bias": _stance_bias_default,
                "stance_bias_note": "画像库默认值 = stance_base；事件级版本由 t0_init 按本场议题算 stance_bias[议题] = stance_base x 议题对齐度",
                "arousal_baseline": round(rng.uniform(*ARO[role]), 2),
                "emotion_sensitivity": emo,          # {47话题: 倍数} 0.5~2.0（normalize 末尾定稿）
                "emotion_sensitivity_individual": emo_ind,   # 个体因子（审计用）
                "emotion_decay": _dec15,                     # 🟨 15格规则表（同格同值）
                "contagion_susceptibility": round(rng.uniform(*CON[role]), 2),
                # ★ 性格两字段（组长 v3 §2.2-4）
                "emotional_stability": _stab, "extraversion": _extr,
                # ★ active_level（组长 v3 §2.2-12，组长点名要画像侧给）
                "active_level": _active,
                # ★★ 组长 v3 J5：删掉 complaint_propensity / share_propensity（改由 LLM 判断）
                #    ★★ 组长 v3 #18：删掉 cross_channel_propensity（跨渠道可见性归【渠道传播表】）
                # ★ 改名：concern_topics -> interest_topics（组长 v3 §2.2-5 的统一命名）
                "interest_topics": {t: concern[t] for t in sorted(concern, key=lambda x: -concern[x])},
                "concern_tier": {t: tier[t] for t in concern if t != "其他"},
                # ★★ 组长 v3 §2.2-9：active_hours 是【0–1 时段向量】——一天里哪些时段在线（强度）
                #    用途：④ 抽样 + 可见性资格层（"他醒着没有"）
                #    机构账号 = 值班时段；个人 = 作息。两套语义，同一形态。
                # ★★ active_hours 的自描述元数据 —— 解决「0.39 算哪个时间」：
                #    光看向量读不出时间，必须带上 index->小时的映射与阈值口径
                # ★★ active_hours = 【在线小时的列表】，24 小时制，本地时间（Asia/Shanghai）
                #    例 [22] = 22:00-23:00 在线；[13,21,22,23] = 13 点与 21~23 点在线
                #    ★ 不再是 0~1 强度向量 —— 向量形态下「0.39 算哪个时间」无法回答，是设计错误
                "active_hours": _ah_hours,
                "action_thresholds": dict(_at15),             # 🟨 15格规则表（组长的复数命名）
                "channel_behavior": {"role_type": role, "channel": ch,
                    "available_actions": ACTIONS[(role, ch)],
                    "reach_multiplier": round(min(4.0, max(0.3, (REACH.get(ch) or {}).get(role, 1.0) + rng.gauss(0, .05))), 2),
                    "emotion_amplifier": round(min(3.0, max(0.2, (EMOAMP.get(ch) or {}).get(role, 1.0) + rng.gauss(0, .05))), 2),
                    "quote_meaning": QUOTE_MEANING.get(role),   # 引用在本角色上叫什么事
                    "info_decay_rate": _dec15},
                # ★★ 2026-10-07 修复：机构号（媒体/监管）不得携带个人属性 ——
                #   原先这里【无条件】把骨架的 age/gender/occupation/education/income/
                #   marital/urban 写进所有 persona。虽然 unknown_fields 已声明这些字段
                #   对机构号无意义，但【值仍在】，导致任何读中间产物（69 字段）的下游
                #   都会把「长城网 38 岁 · 女 · 企业职员 · 未婚」当成真的。
                #   现在机构号这些字段一律 None，只保留 region（机构所在地）与 interest_circle
                #   （后者是话题抽样的依据，不在 unknown_fields 里）。
                **(dict(age=None, age_band=None, gender=None, occupation=None,
                        urban_tier=None, education=None, income_band=None,
                        city_pop_wan=None, city_netizens_wan=None, marital_status=None)
                   if role in ORG_ROLES else
                   dict(age=sk["age"], age_band=sk["age_band"], gender=sk["gender"],
                        occupation=sk["occ"], urban_tier=sk["urban"], education=sk["edu"],
                        income_band=sk["income"], city_pop_wan=CITY_POP.get(sk["region"]),
                        city_netizens_wan=CITY_NET.get(sk["region"]),
                        marital_status=sk["marital"])),
                "region": sk["region"],
                "interest_circle": sk["circle"]["id"],
                "_bio_sku": _bio_sku,
                **(_org or {}),
            })
    pool = normalize(pool, rules, W1)
    # ═══════════════════════════════════════════════════════════════════
    # ★★ 2026-10-07 最终硬过滤：无孩者不得关注「亲子与育儿」
    #   为什么要在 normalize()【之后】再做一遍：normalize 里有两处会把话题加回来 ——
    #     ① 第 749-751 行把 CN 里的 key 话题降级为 mild（但 ban 的语义丢了）
    #     ② 第 752-771 行的「圈层主标签必须出现在画像里」会把圈层 key 补进来
    #   实测：只改 tiers_of 的话，2000 人里仍有 30+ 人带着「亲子与育儿」。
    #   这里做一次权威剔除，冲突数归零。
    # ═══════════════════════════════════════════════════════════════════
    _CN_HARD = {"亲子与育儿"}
    _purged = 0
    for _x in pool:
        if _x.get("role_type") in ORG_ROLES:
            continue
        if _x.get("marital_status") not in ("未婚", "已婚无孩"):
            continue
        _it = _x.get("interest_topics") or {}
        _hit = [t for t in list(_it) if t in _CN_HARD]
        if not _hit:
            continue
        for _t in _hit:
            _it.pop(_t, None)
            (_x.get("concern_tier") or {}).pop(_t, None)
            (_x.get("emotion_sensitivity") or {}).pop(_t, None)
        # 权重重新归一到 1.0
        _s = sum(_it.values()) or 1.0
        for _k in list(_it):
            _it[_k] = round(_it[_k] / _s, 6)
        _purged += 1
    if _purged:
        print("  ★ 无孩者剔除「亲子与育儿」：%d 人" % _purged)
    # ★★ bio 最后生成 —— 此时所有参数（含 normalize 的档位调整 / 话题注入 / 权重重分配）已定稿
    pool = finalize_bio(pool, args.seed, ORG_ROLES)
    write_json(args.out, pool)
    # ★ 指纹必须覆盖所有会影响下游的字段 —— 此前漏了 followers / influence_weight，
    #   导致改了粉丝分布和影响力公式后指纹不变，无法判断库有没有变。
    dg = digest_of(pool, ["agent_id","channel","role_type","persona_bio","interest_topics",
                          # ★ 2026-10-07 补：个人属性也要进指纹 —— 机构号这些字段置 None 后
                          #   指纹原本不变（清单没覆盖它们），无法判断库有没有真的变。
                          "age","age_band","gender","occupation","education","income_band",
                          "marital_status","urban_tier","org_type","org_position","org_scope",
                          "concern_tier","emotion_sensitivity","action_thresholds",
                          "channel_behavior","followers","influence_weight",
                          "contagion_susceptibility","active_hours","active_hours_list",
                          "active_level","emotional_stability","extraversion","stance_base"])
    Path(args.out).with_suffix(".digest.txt").write_text(
        "master_seed=%d\nrecords=%d\nsha256=%s\n" % (args.seed, len(pool), dg), encoding="utf-8")
    # ---- ⑤ 校验
    bad = []
    for x in pool:
        m = x["persona_bio"]
        # ★ 机构集体人设（媒体/监管）【设计上就不出现】年龄/性别/婚育/地域（见 make_org_bio），
        #   也不写生活话题句 —— 这几项检查只对个人人设（网民/客户/KOL）生效。
        _is_org = x["role_type"] in ("媒体", "监管")
        if not _is_org:
            if str(x["age"]) not in m: bad.append((x["agent_id"], "age 不在 bio 里"))
            if x["gender"] not in m: bad.append((x["agent_id"], "gender 不在 bio 里"))
            # ★ region 现是「省份城市」（如 山西太原），但 bio 里写的是城市部分（在太原）。
            #   两者任一命中即可。
            _reg = x.get("bio_region") or x["region"]
            #   ★ 省名长度不一样：黑龙江 / 内蒙古 是 3 字，其余多为 2 字。
            _PROV3 = ("黑龙江", "内蒙古")
            if any(_reg.startswith(_p3) for _p3 in _PROV3):
                _city = _reg[3:]
            elif len(_reg) > 3:
                _city = _reg[2:]
            else:
                _city = _reg
            if _reg not in m and _city not in m:
                bad.append((x["agent_id"], "region 不在 bio 里"))
        # ★ 正确的检查：bio 只容纳 1 条话题句（make_bio 里 _say[:1]，规范 v3 要求 1-3 句），
        #   所以不能要求「所有非 weak 话题都有依据」—— 那个条件结构上不可满足。
        #   改为检查：bio 里至少有 1 条【key/mild 话题】的语句依据；且不得出现 ban 话题。
        _sayable = [t for t in x["interest_topics"]
                    if t != "其他" and x["concern_tier"].get(t) in ("key", "mild")]
        _hit = False
        for t in _sayable:
            _ph = list(PHRASE.get(t) or [])
            _pc = PH_COND.get(t) or {}
            _ph += [b.get("s") for b in (_pc.get("by") or []) if b.get("s")]
            _ph += list(_pc.get("gen") or [])
            if any(p in m for p in _ph):
                _hit = True
                break
        if _sayable and not _hit and not _is_org:
            bad.append((x["agent_id"], "bio 里没有任何 key/mild 话题的语句依据"))
        for t in x["interest_topics"]:
            if t == "其他": continue
            if x["concern_tier"].get(t) == "ban":
                bad.append((x["agent_id"], "话题「%s」被禁却出现在库里" % t))
        if abs(sum(x["interest_topics"].values()) - 1.0) > 0.011: bad.append((x["agent_id"], "权重和 != 1"))
        # ★ emotion_sensitivity：键 == interest_topics 去掉「其他」的键；值 0.5~2.0
        _es = x["emotion_sensitivity"] or {}
        _it_k = {t for t in x["interest_topics"] if t != "其他" and x["interest_topics"][t] > 0}
        if set(_es) != _it_k:
            bad.append((x["agent_id"], "emotion_sensitivity 键 != interest_topics 键（差 %s）"
                        % sorted(set(_es) ^ _it_k)[:3]))
        _oob = [k for k, v in _es.items() if not (SENS_LO - 1e-9 <= v <= SENS_HI + 1e-9)]
        if _oob:
            bad.append((x["agent_id"], "emotion_sensitivity 越界 0.5~2.0：%s" % _oob[:3]))
        # ★ 注意：【不检查单人均值】—— 要求是【全体均值 ≈ 1.0】，个人差异正是要的。
        #   （每人均值天然会散开：角色基准 + 个体因子 + 话题权重因子 + 0.5/2.0 夹取）
        if False: bad.append((x["agent_id"], "emo 键不在 concern"))
        # ═══════════════════════════════════════════════════════════════
        # ★★ 2026-10-07 新增：人口属性【一致性】检查 —— 此前完全没有这一段。
        #   旧自检只查 bio / 话题 / 权重 / 情绪，属性矛盾全部漏过，
        #   全库扫描发现 51.6% 的人至少有一处矛盾（年龄<学历 10.09%、
        #   学历不满足职业 9.73%、年龄<职业 5.54%、学生高收入 3.44%、
        #   未婚却关注育儿 22.57%）。现在这些一律记入 bad，冲突数必须为 0。
        # ═══════════════════════════════════════════════════════════════
        if not _is_org:
            _ae = AGE_EDU_MIN.get(x.get("education"))
            if _ae is not None and x["age"] < _ae:
                bad.append((x["agent_id"], "年龄(%d) < 学历「%s」最低要求(%d)" % (x["age"], x["education"], _ae)))
            _ao = AGE_OCC_MIN.get(x.get("occupation"), 18)
            if x["age"] < _ao:
                bad.append((x["agent_id"], "年龄(%d) < 职业「%s」最低要求(%d)" % (x["age"], x["occupation"], _ao)))
            _eo = EDU_OCC_MIN.get(x.get("occupation"))
            if _eo and x.get("education") not in _eo:
                bad.append((x["agent_id"], "学历「%s」不满足职业「%s」要求" % (x["education"], x["occupation"])))
            if x.get("occupation") == "学生" and x.get("income_band") in ("10-20千", "2万以上"):
                bad.append((x["agent_id"], "学生却有高收入(%s)" % x["income_band"]))
            _am = MA_AGE_MIN.get(x.get("marital_status"))
            if _am is not None and x["age"] < _am:
                bad.append((x["agent_id"], "年龄(%d) < 婚育「%s」最低要求(%d)" % (x["age"], x["marital_status"], _am)))
            if x.get("occupation") == "全职妈妈" and x.get("gender") != "女":
                bad.append((x["agent_id"], "「全职妈妈」性别不为女"))
            if x.get("occupation") == "全职爸爸" and x.get("gender") != "男":
                bad.append((x["agent_id"], "「全职爸爸」性别不为男"))
            if x.get("marital_status") in ("未婚", "已婚无孩") and "亲子与育儿" in (x.get("interest_topics") or {}):
                bad.append((x["agent_id"], "无孩者(%s)却关注「亲子与育儿」" % x["marital_status"]))
    print("生成 %d 人 -> %s" % (len(pool), args.out))
    print("  角色:", dict(Counter(x["role_type"] for x in pool)))
    print("  平台:", dict(Counter(x["channel"] for x in pool)))
    print("  档位统计（人次）:", dict(tierstat))
    print("  被禁（人次）:", dict(banstat))
    print("  bio 唯一:", len(used) == len(pool))
    print("  ★ 冲突数:", len(bad), "（必须为 0）")
    for b in bad[:8]: print("     ", b)
    print("  库指纹:", dg)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
