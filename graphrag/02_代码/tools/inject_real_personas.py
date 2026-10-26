#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inject_real_personas.py —— 把 GraphRAG 推导出的 t0 真实人物【注入名册】

★ 为什么单独成模块：
  t0_real_personas.py 只负责【推导】，注入是另一件事。
  之前注入是人工步骤（做过两次、漏了一次），导致 500 人版里三个真实人物
  停在「被绑到随机库内人」的状态 —— 相关度只有 0.40/0.35，且没有 is_t0_real 标记。
  现在并进管线：run.py 在 run_event() 之后、写 roster 之前调用本模块。

注入内容（与 100 人版一致）：
  ① agent_id          改成真实昵称 ID（原 ID 存 agent_id_original）
  ② 覆盖推导字段      interest_topics / concern_tier / active_hours / occupation /
                      org_type / org_scope / stance_base / stance_bias / followers /
                      region / sentiment_baseline / interest_circle
  ③ 标记              is_t0_real / real_persona_of / unknown_fields / derived_by_graphrag
  ④ 立场              side 由实测 stance_base 派生
  ⑤ 相关度            relevance 抬到 max(原值, FLOOR) 并强制 A 档
                      （★ 真实参与者不走「选不选得上」的抽样相关度）

允许清单外的字段【一处都不能改】—— 脚本内断言，不符即拒绝写回。
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ★ 2026-10-14：t0 真实人物的人设表【位置不唯一】—— 上游跑事件时的运行目录是
#   `out/_personas_v2/`，交付包里作为「预设输入资产」放在
#   `03_输入数据/画像库/_bio源批次/`。原先只认第一个路径，而交付布局下
#   `out/` 根本不存在 ⇒ 表【静默跳过】（正是下面 docstring 里写的兜底行为），
#   t0 真实人物的 persona_bio 回落成画像库里的通用版、只有属性生效 ——
#   实测 EVT-006 有 10/187 人中招（其他 13 个字段逐位一致，只有 persona_bio 错）。
#   这里改成按候选顺序查找，两个布局都能命中。
_BIO_DIRS = (
    ROOT / "out" / "_personas_v2",
    ROOT.parent / "03_输入数据" / "画像库" / "_bio源批次",
)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from common import load_config  # noqa: E402

# ★ 真实参与者的相关度下限（事件本身证明其在场，不该被抽样相关度压低）
RELEVANCE_FLOOR = 0.95

# ★ emotion_sensitivity 的角色基准（与 tools/make_personas_v3.py 的 SENS_BASE 一致）
SENS_BASE = {"网民": 0.906, "客户": 1.195, "KOL": 1.050, "媒体": 0.713, "监管": 0.569}
RELEVANCE_TIER = "A"

# 47 话题 -> 兴趣圈层（由 interest_topics 的 top1 推）
CIRCLE_OF = {
    "宠物与养宠": "宠物", "动物保护与宠物福利": "宠物",
    "品牌营销与广告伦理": "消费维权", "消费者权益保护": "消费维权", "价格与市场垄断": "消费维权",
    "性别与女性权益": "女性议题", "未成年人保护": "母婴育儿", "亲子与育儿": "母婴育儿",
    "教育与升学": "教育关注", "金融与投资风险": "理财投资", "企业经营与财报": "理财投资",
    "行业监管与合规": "时政观察", "政策法规与制度改革": "时政观察", "反腐与政务监督": "时政观察",
    "医疗健康与医保": "健康养生", "公共卫生事件": "健康养生", "养老与托育": "健康养生",
    "心理健康": "情感生活", "婚恋与家庭": "情感生活",
    "就业与劳动权益": "职场打工人", "收入与物价": "职场打工人",
    "食品安全": "美食生活", "旅行与消费生活方式": "美食生活",
    "交通出行安全": "车主", "体育赛事": "体育迷", "游戏与电竞": "游戏电竞",
    "影视综艺": "影视", "明星与粉丝文化": "饭圈", "音乐与演出": "音乐",
    "短视频与网络红人": "短视频", "全民健身与户外": "健身运动",
    "环境保护与气候变化": "公益环保", "人工智能与算法": "科技数码",
    "科技创新与产业": "科技数码", "网络安全与诈骗": "科技数码", "隐私与个人信息保护": "科技数码",
}

DERIVED_FIELDS = ("interest_topics", "concern_tier", "emotion_sensitivity", "active_hours", "occupation",
                  "org_type", "org_scope", "sentiment_baseline",
                  "stance_base", "stance_bias", "followers", "region",
                  # ★★ 2026-10-07 新增：年龄及其联动字段 —— 原先不在清单里，
                  #   导致 t0 真实人物只能沿用画像库随机抽到的年龄（刘嘻嘻 64 岁退休教师）。
                  "age", "age_band", "education", "marital_status", "income_band", "urban_tier",
                  # ★★ 2026-10-13 新增：persona_bio —— 原先不在允许清单里，
                  #   导致 t0 真实人物的【属性全对、bio 却沿用被绑那个库内人的】：
                  #   刘嘻嘻属性是「女 26 深圳 企业职员 宠物」，bio 却写「杭州人 做档案三十多年 看体育」。
                  #   实测 25 人全部错位，且出现两人 bio 完全相同（违反仿真层验收第 4 条）。
                  # ★★ 2026-10-14 新增：gender —— 原先漏在清单外。人工推断表
                  #   （`_t0_inferred.json`，`_INF_FIELDS` 共 9 个字段）里【只有它】没登记，
                  #   于是表一命中就写 gender -> 断言报「谭先生.gender」-> bad 非空。
                  #   后果有二：① 打印「断言失败 N 处，已回滚」的假警报；
                  #   ② 本文件下方「按 _bio源批次 覆盖 persona_bio」那段带 `not bad` 守卫，
                  #      被一起挡掉 -> 4 个人的 bio 回落成 adapted 版（实测 EVT-002
                  #      新闻门户_媒体_031 等 4 人各多一句尾巴）。一并补齐 9 个字段。
                  "gender",
                  "persona_bio")
ANNOT_FIELDS = ("agent_id", "agent_id_original", "interest_circle", "unknown_fields",
                # ★★ 2026-10-07 补：inject() 重算 emotion_sensitivity 时会同步写这个字段，
                #   但它原先不在允许清单里 -> 断言失败 -> 【整个注入被静默回滚】。
                #   实测 e7 的 8 个真实人物属性全部没生效（年龄还是库里的 31/37/39/56 岁）。
                "emotion_sensitivity_individual",
                "derived_by_graphrag", "is_t0_real", "real_persona_of",
                "relevance", "relevance_old", "relevance_source",
                "activation_tier", "activation_priority", "side")
ALLOW = set(DERIVED_FIELDS) | set(ANNOT_FIELDS)


# ═══════════════════════════════════════════════════════════════════════
# ★★ 2026-10-07 t0 真实人物的【年龄兜底 + 属性联动】
#   问题：t0_real_personas 只能从帖文推 gender/occupation，age 常推不出；
#        注入时沿用画像库随机抽到的年龄 -> 「刘嘻嘻」成了 64 岁退休教师，
#        而她的帖文写「妈妈给我买个宠物险吧」（在对妈妈说话，显然是年轻人）。
#   规则（用户口径）：① 尽量不给 t0 真实网民安排大龄设定
#                    ② 帖文看不出职业等特征时，安排较年轻的人设
#                    ③ 人设必须与其所发帖文一致
#   实现：帖文推得出的字段【最高优先，不动】；推不出的按新年龄重写，
#        并保证学历/职业/婚育/收入/城乡与年龄自洽。
# ═══════════════════════════════════════════════════════════════════════
_T0_SEED = 20261002      # 与主库同源，保证确定性
T0_DEFAULT_AGE = (24, 30)     # ★★ 用户口径：t0 真实网民一律取 24~30 岁（24 岁以上的第一个年龄段，比较年轻）
T0_AGE_EDU = {"高中及以下": 18, "大专": 18, "本科": 18, "硕士+": 22}
T0_AGE_OCC = {"学生": 18, "自由职业": 18, "个体经营": 20, "全职妈妈": 20, "全职爸爸": 20,
              "企业职员": 21, "IT": 21, "教师": 22, "医护": 22, "金融": 22,
              "公务员": 23, "退休": 55}
T0_EDU_OCC = {"教师": ("本科", "硕士+"), "医护": ("大专", "本科", "硕士+"),
              "公务员": ("大专", "本科", "硕士+"), "金融": ("大专", "本科", "硕士+"),
              "IT": ("大专", "本科", "硕士+")}
T0_MA_AGE = {"未婚": 18, "已婚无孩": 20, "已婚有孩": 21, "离异": 23, "丧偶": 30}
T0_INCOME_RANK = {"3千以下": 0, "3-6千": 1, "6-10千": 2, "10-20千": 3, "2万以上": 4}
T0_OCC_FALLBACK = "自由职业"   # ★★ 用户口径：帖文看不出职业 -> 一律自由职业（不抽池子）
T0_BANDS = [(18, 21), (22, 24), (25, 30), (31, 40), (41, 50), (51, 60), (61, 72)]


def _t0_rng(seed, key):
    """确定性随机流（SHA-256 派生）—— 与主库的 persona_rng 同构，互不干扰。"""
    import hashlib, random as _rnd
    h = hashlib.sha256(("%s|%s" % (seed, key)).encode("utf-8")).digest()
    return _rnd.Random(int.from_bytes(h[:8], "big"))


def _wpick(rng, pairs):
    """按权重抽一个，返回 1 元组（与 make_personas_v3._wpick 语义一致）。"""
    tot = sum(w for _, w in pairs) or 1.0
    r = rng.random() * tot
    acc = 0.0
    for k, w in pairs:
        acc += w
        if r <= acc:
            return (k,)
    return (pairs[-1][0],)


def _wpick_age(rng):
    """t0 真实网民的默认年龄 —— 24~30 岁（用户口径：取 24 岁以上的第一个年龄段，比较年轻）。"""
    lo, hi = T0_DEFAULT_AGE
    return int(rng.randint(lo, hi))


def t0_recohere(x, rng, derived, is_org):
    """t0 真实人物：把帖文推不出的字段补成【年轻、自洽】的人设。原地改。"""
    if is_org:
        return
    dv = derived or {}
    # ★★ 年龄口径（用户）：一律 24~30 岁。
    #   例外：帖文有【硬证据】表明不可能是年轻人 —— 提到孙辈 / 自称退休，此时保留线索值。
    #   其余线索（如「妈妈给我」推 18~26）与 [24,30] 冲突时，以 24~30 为准。
    # ★ 用 t0_real_personas 打的【显式标记】，不做字符串匹配 ——
    #   踩过：_age_source 的提示语里含「退休教师」，把所有人都误判成老年人，钳制全被跳过。
    _senior = bool(dv.get("_age_senior"))
    if not _senior:
        _a = x.get("age")
        if not isinstance(_a, int) or not (24 <= _a <= 30):
            _old_a = _a
            x["age"] = _wpick_age(rng)
            if _old_a is not None:
                dv["_age_source"] = ("帖文线索推得 %s 岁，不在 t0 真实网民的 24~30 岁口径内，已钳制为 %d 岁" % (_old_a, x["age"]))
            else:
                dv["_age_source"] = "帖文无年龄线索 -> 取 24~30 岁（t0 真实网民默认口径）"
    try:
        age = int(x.get("age") or 26)
    except (TypeError, ValueError):
        age = 26; x["age"] = 26
    x["age_band"] = "24-30" if 24 <= age <= 30 else next(
        ("%d-%d" % (lo, hi) for lo, hi in T0_BANDS if lo <= age <= hi), "%d-%d" % (24, 30))
    if "education" not in dv:
        _ep = ([("高中及以下", .18), ("大专", .22), ("本科", .52), ("硕士+", .08)] if age <= 24 else
               ([("高中及以下", .12), ("大专", .20), ("本科", .55), ("硕士+", .13)] if age <= 30 else
                [("高中及以下", .30), ("大专", .26), ("本科", .38), ("硕士+", .06)]))
        _ep = [(e, w) for e, w in _ep if T0_AGE_EDU[e] <= age] or [("本科", 1.0)]
        x["education"] = _wpick(rng, _ep)[0]
    edu = x.get("education") or "本科"
    if "occupation" not in dv:
        # ★★ 用户口径：职业【只从帖文判断】，判断不出就设「自由职业」——
        #   不再从职业池里随机抽（抽出来的职业帖文里没有任何依据，等于编）。
        x["occupation"] = T0_OCC_FALLBACK
    if "marital_status" not in dv:
        _mp = ([("未婚", .94), ("已婚无孩", .04), ("已婚有孩", .02)] if age <= 24 else
               ([("未婚", .58), ("已婚无孩", .22), ("已婚有孩", .18), ("离异", .02)] if age <= 30 else
                [("未婚", .26), ("已婚无孩", .14), ("已婚有孩", .56), ("离异", .04)]))
        _mp = [(m, w) for m, w in _mp if T0_MA_AGE[m] <= age] or [("未婚", 1.0)]
        x["marital_status"] = _wpick(rng, _mp)[0]
    if "income_band" not in dv:
        _cap = 2 if age <= 21 else (3 if age <= 24 else 4)
        if x.get("urban_tier") == "乡镇农村":
            _cap = min(_cap, 3)
        _ip = [("3千以下", .30), ("3-6千", .28), ("6-10千", .26), ("10-20千", .13), ("2万以上", .03)]
        _ip = [(kk, w) for kk, w in _ip if T0_INCOME_RANK[kk] <= _cap] or [("3千以下", 1.0)]
        x["income_band"] = _wpick(rng, _ip)[0]
    if not x.get("urban_tier"):
        x["urban_tier"] = _wpick(rng, [("一线", .16), ("新一线", .26), ("二线", .24),
                                       ("三线", .13), ("四线及以下", .16), ("乡镇农村", .05)])[0]


def build_real(kg, t0, pack, topic_alias=None, org_persona=None):
    """调用 t0_real_personas 的推导逻辑，返回真实人物列表"""
    import t0_real_personas as TRP  # noqa: E402
    t0x = dict(t0)
    t0x["issue_leaves"] = [i.get("leaf") for i in (kg.get("issues") or [])]
    return TRP.build(kg, t0x, pack,
                     topic_alias or load_config("topic_alias"),
                     org_persona or load_config("org_persona"))


def _load_inferred():
    """★ 2026-10-13：人工推断表 —— 大模型逐条读 t0 帖文推断的人口学属性。
    键形如 "EVT-010|微博_网民_董川"；个人账号不跨事件重复，故去掉事件前缀做扁平查找。

    ★ 2026-10-14：路径同样【不唯一】—— 上游跑事件时在 `out/_t0_inferred.json`，
    交付包里作为预设输入资产放在 `03_输入数据/画像库/_bio源批次/`。原先只认第一个
    路径，而交付布局下 `out/` 根本不存在 ⇒ 表【静默跳过】，t0 真实参与者的 9 个
    人口学字段回落成画像库的通用默认值（实测 EVT-002「谭先生」9 个全错：男→女、
    50→29 岁、湖南长沙→黑龙江哈尔滨、本科→硕士+……）。
    ⇒ 只有两份【调试收据】能看出来：`_中间产物/D_Agent名单_完整.json` 与
    `G_组队校验报告.json`（圈层计数）；`D_Agent名单.json` 的 14 字段 schema 不含
    这些字段，仿真链路更不读 `_中间产物/` —— 所以对交付链路的产物零影响。
    这里改成按候选顺序查找，两个布局都能命中。"""
    raw = {}
    for _d in _BIO_DIRS:
        try:
            _p = _d / "t0_inferred.json"
            if _p.exists():
                raw = json.loads(_p.read_text(encoding="utf-8"))
                break
        except Exception:
            continue
    try:
        return {k.split("|", 1)[-1]: v for k, v in raw.items()}
    except Exception:
        return {}


_INFERRED = _load_inferred()
_INF_FIELDS = ("gender", "age", "region", "urban_tier", "education",
               "occupation", "marital_status", "income_band", "interest_circle")


def _load_personas():
    """★ 2026-10-13：t0 真实人物的【人设】表 —— 键 = 真实昵称，值 = 150~250 字第一人称人设。

    为什么和 _t0_inferred 分开：属性解决「他是谁」，人设解决「他在这场里是什么处境」。
    人设要按仿真层《需求说明 v1》5.3 的六段结构写，且必须含第⑥段
    「t0 时刻已经成立的关系」（发帖人 = 刷到并发了帖；当事人 = 与品牌/品类的既有关系），
    这无法由属性机械拼出，所以逐条手写、单独存放。
    表缺失时静默跳过（不阻断注入，bio 沿用原值，仅属性生效）。"""
    for _d in _BIO_DIRS:
        try:
            _p = _d / "t0_real.json"
            if _p.exists():
                return json.loads(_p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


_PERSONAS = _load_personas()


def _load_org_personas():
    """★ 2026-10-13：t0 真实【机构号】的人设表 —— 键 = "EVT-XXX|agent_id"。

    为什么必须带事件前缀：同一个机构号会在多场出现，而机构人设最后一段是
    「你在本场发了什么、现在口径是什么」（仿真层《需求说明 v1》5.6），逐场不同。
    实测 62 条覆盖 12 场全部机构号（法院号、央媒号、都市报号的表述原则各不相同）。
    表缺失时静默跳过。"""
    for _d in _BIO_DIRS:
        try:
            _p = _d / "t0_org.json"
            if _p.exists():
                return json.loads(_p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


_ORG_PERSONAS = _load_org_personas()


def _load_bio_library():
    """★ 2026-10-14：LLM 逐人撰写的 bio 批次（`_bio源批次/p*.json`，扁平 {agent_id: bio}）。

    与 `persona_v18_3k_adapted.json` 的关系：adapted 是【在这批 bio 的每条末尾
    再追加一句个性化行为准则】的版本。交付实测两者 3000 个共同键里有 160 处不同，
    全部形如「adapted = 批次 + 一句尾巴」（尾巴逐人不同，如「你不接任何商业推广。」）。
    **上游跑 12 事件 A–G 用的是批次这一版** —— 要复现那 12 个交付包，必须以批次为准。
    批次目录不存在时返回空字典，整段行为与原先完全一致。"""
    out = {}
    for _d in _BIO_DIRS:
        try:
            for _p in sorted(_d.glob("p*.json")):
                _raw = json.loads(_p.read_text(encoding="utf-8"))
                if isinstance(_raw, dict):
                    for _k, _v in _raw.items():
                        if isinstance(_v, str) and _v:
                            out[str(_k)] = _v
        except Exception:
            continue
    return out


_BIO_LIBRARY = _load_bio_library()


def inject(roster, real_personas, event_id=None):
    """把真实人物注入名册（原地改）。返回 (改了几人, 断言错误列表)

    event_id：当前事件号（EVT-XXX）。机构号的人设【逐场不同】，必须带事件前缀查表；
             个人号的人设跨场通用，不带前缀。"""
    before = json.loads(json.dumps(roster))
    hits = []
    for rp in real_personas:
        x = next((y for y in roster if y.get("plays_entity") == rp.get("plays_entity")), None)
        if x is None:
            continue
        dv = rp.get("derived") or {}
        for f in DERIVED_FIELDS:
            if f in dv:
                x[f] = dv[f]
        _top = sorted((x.get("interest_topics") or {}).items(), key=lambda kv: -kv[1])
        x["interest_circle"] = CIRCLE_OF.get(_top[0][0]) if _top else None
        # ★★ 2026-10-07：年龄兜底 + 属性联动
        #   帖文推得出的字段最高优先（dv 里已有，上面已写入 x）；推不出的按【年轻默认段】补全，
        #   并让 学历/职业/婚育/收入/城乡 与年龄自洽 —— 否则会出现「24 岁的退休教师」。
        try:
            _is_org = (x.get("role_type") in ORG_ROLES) if "ORG_ROLES" in dir() else (x.get("role_type") in ("媒体", "监管"))
            t0_recohere(x, _t0_rng(_T0_SEED, "recohere:" + str(x.get("agent_id"))), dv, _is_org)
        except Exception as _e:
            import sys as _s; print("    [t0_recohere 失败] %s: %s" % (x.get("agent_id"), _e), file=_s.stderr)


        # ★★ emotion_sensitivity 的键 == interest_topics 的键（去掉「其他」），
        #   值 0.5~2.0、全体均值 ≈ 1.0。注入改了 interest_topics，必须重算。
        #   个体因子取 1.0（真实人物的推导话题已足够特异，不再叠随机）。
        _base = SENS_BASE.get(x.get("role_type"), 1.0)
        _ks = [t for t, w in (x.get("interest_topics") or {}).items()
               if t != "其他" and (w or 0) > 0]
        if _ks:
            _wm = sum(x["interest_topics"][t] for t in _ks) / float(len(_ks))
            x["emotion_sensitivity"] = {
                t: round(min(2.0, max(0.5, _base * min(1.8, max(0.5,
                    (x["interest_topics"][t] / _wm) if _wm > 0 else 1.0)))), 2)
                for t in _ks}
            x["emotion_sensitivity_individual"] = 1.0
        x["unknown_fields"] = [f for f in (rp.get("unknown_fields") or [])
                               if f not in ("interest_circle", "city")]
        x["derived_by_graphrag"] = {k[1:]: v for k, v in dv.items() if k.startswith("_")}
        # ★★ 2026-10-13：人工推断表【最高优先】—— 大模型逐条读 t0 帖文推断，覆盖默认补全。
        #   ★ 必须放在上面那行【之后】：那行会整体覆盖 derived_by_graphrag，放前面标记会被冲掉（踩过）。
        _inf = _INFERRED.get(str(rp.get("plays_entity"))) or _INFERRED.get(str(rp.get("agent_id")))
        if _inf:
            for _k in _INF_FIELDS:
                if _inf.get(_k) is not None:
                    x[_k] = _inf[_k]
            x["derived_by_graphrag"]["人工读帖推断"] = _inf.get("reason")
        # ★★ 2026-10-13：人设覆盖 —— 与属性同源同优先级（都来自逐条读帖）。
        #   必须在 x["agent_id"] 被改写成真实昵称【之前或同段】按真实昵称查表。
        _bio = _PERSONAS.get(str(rp.get("agent_id"))) or _PERSONAS.get(str(rp.get("plays_entity")))
        if _bio:
            x["persona_bio"] = _bio
            x["derived_by_graphrag"]["persona_bio_source"] = (
                "out/_personas_v2/t0_real.json —— 按仿真层《需求说明 v1》5.3 六段结构手写，"
                "含第(6)段「t0 时刻已经成立的关系」（%d 字）" % len(_bio))
        # ★★ 2026-10-13：机构号人设 —— 按【事件 + agent_id】查表（同一机构各场不同）。
        #   仿真层 5.6：机构号要「账号定位 / 信息口径 / 表述原则」三段
        #   + 一句「你在本场发了什么、现在口径是什么」。命中则覆盖上面的通用 bio。
        _ev = str(event_id or "")
        _obio = _ORG_PERSONAS.get("%s|%s" % (_ev, rp.get("agent_id"))) if _ev else None
        if _obio:
            x["persona_bio"] = _obio
            x["derived_by_graphrag"]["persona_bio_source"] = (
                "out/_personas_v2/t0_org.json[%s] —— 按仿真层《需求说明 v1》5.6 机构号三段"
                "（账号定位/信息口径/表述原则）+ 本场一句手写（%d 字）" % (_ev, len(_obio)))
        if _top:
            x["derived_by_graphrag"]["interest_circle"] = (
                "由 interest_topics 的 top1「%s」推出" % _top[0][0])
        x["agent_id_original"] = x["agent_id"]
        x["agent_id"] = rp["agent_id"]
        # 相关度：真实参与者不走抽样
        _old = x.get("relevance") or 0.0
        x["relevance_old"] = _old
        # ★ 2026-10-13 组长要求「每个人的相关度必须特异不一样」：
        #   原先这里所有 t0 真实人物都被压成同一个 0.95（实测 EVT-010 有 18 人并列）。
        #   现在在 0.95 之上叠一个由 agent_id 派生的唯一微扰（1e-6~9e-4），
        #   量级小到不影响「t0 真实人物排最前」的语义，但保证任意两人不同。
        _h = hashlib.sha256(("t0-rel:" + str(rp.get("agent_id"))).encode("utf-8")).digest()
        _eps = 1e-6 * (1 + int.from_bytes(_h[:3], "big") % 900)
        x["relevance"] = round(max(_old, RELEVANCE_FLOOR) + _eps, 6)
        x["activation_tier"] = RELEVANCE_TIER
        x["activation_priority"] = 1
        x["relevance_source"] = (
            "★ t0 真实参与者 —— 事件本身证明其在场，不走「选不选得上」的抽样相关度。"
            "原抽样值 %.4f，置为 max(原值, %.2f) 并强制 %s 档。" % (_old, RELEVANCE_FLOOR, RELEVANCE_TIER))
        _sb = x.get("stance_base")
        if isinstance(_sb, (int, float)):
            x["side"] = "pro" if _sb < -0.15 else ("counter" if _sb > 0.15 else "neutral")
        x["is_t0_real"] = True
        x["real_persona_of"] = rp.get("plays_entity")
        hits.append((x["agent_id_original"], x["agent_id"], _old, x["relevance"]))

    # ---- 断言：允许清单外一处都不能改
    bad = []
    for i in range(len(roster)):
        for k in set(before[i]) | set(roster[i]):
            if k in ALLOW:
                continue
            if json.dumps(before[i].get(k), sort_keys=True, ensure_ascii=False) != \
               json.dumps(roster[i].get(k), sort_keys=True, ensure_ascii=False):
                bad.append("%s.%s" % (roster[i].get("agent_id"), k))
    return hits, bad


def derive_and_inject(kg, t0, pack, roster, verbose=True,
                      topic_alias=None, org_persona=None):
    """管线入口：推导 + 注入。返回 (hits, bad)"""
    try:
        reals = build_real(kg, t0, pack, topic_alias, org_persona)
    except Exception as e:
        if verbose:
            print("    [真实人物] 推导失败，跳过注入：%s" % str(e)[:120])
        return [], []
    if not reals:
        return [], []
    # ★ event_id：机构号人设逐场不同，必须知道当前是哪一场。
    #   ⚠️ run.py 传进来的 roster 【是 list（members）不是 dict】—— 实测 roster.get 拿不到，
    #   所以先看 roster（dict 形态时），再兜底到 kg["event_id"]（kg 里一定有）。
    _members = roster.get("members") if isinstance(roster, dict) else roster
    _eid = (roster.get("event_id") if isinstance(roster, dict) else None)         or (kg.get("event_id") if isinstance(kg, dict) else None)         or (kg.get("event_key") if isinstance(kg, dict) else None)
    hits, bad = inject(_members, reals, event_id=_eid)

    # ★ 2026-10-14：把 bio 换回【批次版】（上游 12 事件包用的那一版）。
    #   放在 inject 之后：t0 真实人物的 bio 由 t0_real/t0_org 表给出，而他们在
    #   批次里根本不存在（实测 EVT-006 那 10 人 10/10 不在），所以不会被冲掉。
    #   只覆盖批次里【有】的 agent；批次为空时整段是 no-op。
    if _BIO_LIBRARY and not bad:
        _n = 0
        for _x in _members:
            _b = _BIO_LIBRARY.get(str(_x.get("agent_id")))
            if _b and _x.get("persona_bio") != _b:
                _x["persona_bio"] = _b
                _n += 1
        if verbose and _n:
            print("    [bio 库] 按 _bio源批次 覆盖 %d 人的 persona_bio"
                  "（= 上游 12 事件包口径；共 %d 条可查）" % (_n, len(_BIO_LIBRARY)))

    if verbose:
        if bad:
            print("    [真实人物] ⚠️ 断言失败 %d 处，已回滚" % len(bad))
            for b in bad[:5]:
                print("        X " + b)
        elif hits:
            print("    [★ 真实人物] 注入 %d 人（agent_id 用真实昵称，相关度抬到 %.2f + A 档）"
                  % (len(hits), RELEVANCE_FLOOR))
            for o, n, r0, r1 in hits:
                print("        %-24s -> %-30s 相关度 %.4f -> %.2f" % (o, n, r0, r1))
    return hits, bad


def main():
    ap = argparse.ArgumentParser(description="把 t0 真实人物注入名册（也可由 run.py 自动调用）")
    ap.add_argument("--roster", required=True, help="名册 JSON（数组）")
    ap.add_argument("--kg", required=True)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", default=None, help="输出 JSON，默认原地覆盖")
    ap.add_argument("--dry", action="store_true", help="只预览不写盘")
    a = ap.parse_args()
    J = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    roster = J(a.roster)
    if isinstance(roster, dict) and "members" in roster:
        key = "members"
    elif isinstance(roster, dict) and "agents" in roster:
        key = "agents"
    else:
        key = None
    arr = roster[key] if key else roster
    hits, bad = derive_and_inject(J(a.kg), J(a.t0), J(a.pack), arr)
    if bad:
        print("断言失败，不写盘。")
        return 1
    if a.dry:
        print("（--dry，未写盘）")
        return 0
    out = Path(a.out) if a.out else Path(a.roster)
    txt = json.dumps(roster, ensure_ascii=False, indent=1)
    assert json.dumps(json.loads(txt), ensure_ascii=False, indent=1) == txt, "round-trip 不保真"
    out.write_text("\n" + txt, encoding="utf-8")
    print("已写回 %s" % out.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())