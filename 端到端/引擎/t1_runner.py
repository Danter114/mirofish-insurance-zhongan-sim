# -*- coding: utf-8 -*-
"""
EVT-001 · 第 1 轮（T1）执行器 —— 跑通版

对应草案：仿真层规则草案_MVP待验证.md
    R-01~R-05 名额      R-08~R-11 视野/时间表    R-17 情绪偏离
    R-19 监管例外       R-22 情绪刷新            R-23 抽样三步
    R-24 表1+表2+δ      R-26 上游帖加权抽        R-27 三层动作
    R-28/28a 渠道传播   R-29/R-31 prompt 与成本闸门

用法：
    python t1_runner.py                    # 真调 LLM（deepseek），轮次 = 已有的最大轮 + 1
    python t1_runner.py --round 3          # 显式指定轮次
    python t1_runner.py --mock             # 不调 LLM，用规则档位直接造动作，验证管线
    python t1_runner.py --round 2 --rng-from t1状态_第1轮.mock.json
                                           # 第 1→2 轮的引导（旧快照没有 rng_state），见登记表 §3.7

★ 跨轮（2026-10-07 起）：第 r 轮的入口是**第 r−1 轮的轮末快照**（`t{r-1}状态_第{r-1}轮.json`）
  —— R-22b「轮 r 轮首 ≡ 轮 r−1 轮末」。轮首**不再重算**一遍情绪（算了就是每轮刷两次）。
  随机流状态随快照落盘 / 恢复，保证「从任意轮恢复」≡「一口气连跑」。

红线自检：
    C-1 不出投诉量      本脚本只出动作计数与情绪统计，无任何投诉量
    C-4 不用全局 seed   每个随机流单独 random.Random(seed + offset)
    C-5 不泄漏          事件背景只写 t0 已知（不含"全网都在转"等）
"""
import json, math, os, random, re, sys, io, time, urllib.request, argparse
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# ★ 2026-10-13：stderr 一并钉成 UTF-8。后端把 stderr 合进**以 UTF-8 打开**的日志文件
#   （`后端\jobs.py` 的 `stderr=subprocess.STDOUT`），若这里按系统代码页写（简中 = GBK），
#   报错在界面上就是一串乱码 —— 而报错恰恰是审核方最需要看懂的那几行。
#   （`sys.__stderr__` 仍持着原对象，所以旧 wrapper 被回收时不会把 buffer 一起关掉。）
try:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
except Exception:
    pass

# ★ 引擎侧事件配置（BASE / 画像库 / 快照模板 / t0 / 轮次表 / 事件背景 / LLM）——
#   一律从 `事件配置.json` 来，代码里**不再写死**任何路径、事件身份串或事件内容。
#   解析顺序：`--config PATH` > 环境变量 `SIM_CONFIG` > 本目录 `事件配置.json`
#             → 回退 `报告配置.json`；两者都没有时按内置的 EVT-001 默认值跑（会打横幅）。
#   见 `_sim_config.py`；配置文件里的相对路径一律相对配置文件所在目录。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)      # `python -I` 不把脚本目录放进 sys.path，需显式加
import _sim_config
BASE = None        # ← 由配置填入（见下方 argparse 之后）
ENV_PATH = None    # ← 同上（LLM 的 .env）

# ── 命令行 ────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument('--mock', action='store_true', help='不调 LLM，用规则档位直接造动作，验证管线')
ap.add_argument('--mock-gear', type=int, default=None,
                help='调试用：mock 模式下把**所有被抽中者**的档位强制成这个值（0-4；'
                     '5 = COMPLAINT 但通常会被 R-33 乙的闸门降级，用 --mock-complaint 更直接）。'
                     '用于验证某条动作路径（如 --mock-gear 4 → 引用帖进池 + 视野绑定）。')
ap.add_argument('--quota', type=int, default=None,
                help='调试用：覆盖本轮名额（如 30 → 可上场者全抽一遍，冒烟测试更多路径）。')
ap.add_argument('--dump-prompt', default=None, metavar='AGENT_ID',
                help='调试用：只渲染该 agent 的 USER 提示词并退出（不进 LLM、不落盘）。')
ap.add_argument('--display-name', default=None, choices=('auto', 'agent_id'),
                help='调试用：临时覆盖配置里的 `display_name` 口径（**不改配置**）。'
                     '配 `--dump-prompt` 可原地对比两种口径渲染出的提示词。')
ap.add_argument('--mock-complaint', action='store_true',
                help='调试用：mock 模式下让**客户**的动作强制成 COMPLAINT（验证 R-33 的投诉落点；'
                     '会绕过"推荐集合里没有就降级"那道闸门）。')
ap.add_argument('--round', type=int, default=None,
                help='跑第几轮。缺省 = 已有的最大轮次 + 1（所以不会手滑重跑第 1 轮）。')
ap.add_argument('--rng-from', default=None, metavar='ROUND|STATE_JSON',
                help='引导用：从该快照里取 `rng_state` 恢复 4 条随机流。'
                     '给**轮号**（如 `--rng-from 1`）则读第 1 轮的 `--mock` 快照；'
                     '也可给文件路径。只在"续跑一个没有 rng_state 的旧快照"时需要'
                     '（第 1 轮 → 第 2 轮就是这种情况）。')
ap.add_argument('--force', action='store_true',
                help='覆盖已存在的本轮快照 / 记录员行（缺省拒绝，防第 1 轮事故重演）。')
_sim_config.add_config_arg(ap)
args = ap.parse_args()

# ── 载入事件配置（★ 引擎参数化的唯一入口）──────────────────────────
CFG = _sim_config.load(argv=sys.argv[1:])
BASE = CFG.run_dir
EVENT_ID = CFG['event_id']
EVENT_BRIEF = CFG['event_brief']
ENV_PATH = CFG.llm.get('env_path')

# ── 投诉资格：由**采集侧 8 类事件类型**决定（2026-10-20 定案）──────────────
#   旧做法是 prompt 里一句自由文本「如果只是广告、政治这类…就不必投诉」——
#   没有任何代码在管，模型可以不听（EVT-001 营销类仍跑出 2 起投诉）。
#   现在改成硬闸：类型不许 ⇒ `COMPLAINT` 不进推荐集合、不列进候选栏。
#     allow  可投诉（消费纠纷，黑猫是正常渠道）
#     deny   不许投诉（不是消费行为 / 与消费者无关 / 已在司法维权）
#     scene  视场景：只有事件有明确涉事主体（party_entities 非空）才允许
COMPLAINT_POLICY_BY_TYPE = {
    '理赔类':       'allow',
    '销售与投保类': 'allow',
    '产品与费率类': 'allow',
    '服务态度类':   'allow',
    '营销类':       'deny',
    '公司经营类':   'deny',
    '司法与维权类': 'deny',
    '数据与隐私类': 'scene',
}
EVENT_TYPE = str(CFG.get('event_type') or '').strip()
COMPLAINT_POLICY = COMPLAINT_POLICY_BY_TYPE.get(EVENT_TYPE, 'deny')
PARTY_ENTITIES = [str(x).strip() for x in (CFG.get('party_entities') or []) if str(x).strip()]


def complaint_allowed(aid=None):
    """本场是否允许走「投诉」。aid 预留给后续"按当事人"细筛。"""
    if COMPLAINT_POLICY == 'deny':
        return False
    if COMPLAINT_POLICY == 'scene':
        return bool(PARTY_ENTITIES)
    return True

# ★ 2026-10-07：`--mock` 的产物**不得覆盖真跑产物**（跑回归时把真跑快照冲掉过一次）。
#   mock 模式一律加 `.mock` 后缀，记录员追加到另一个文件里，互不干扰。
SUFFIX = '.mock' if args.mock else ''

# ══════════════════════════════════════════════════════════════
# 0. 常量与规则参数
# ══════════════════════════════════════════════════════════════
# ★ 2026-10-07 定案：T0 往后推一天 —— 05-21 22:00 → **05-22 22:00**（草案 §R-36 ⑨）。
#   依据：「**T0 应该是这三个用户最后发起的时间**」= 三条 t0 注入帖里**最后一条**的时刻。
#   ★ 2026-10-07 二次定案（**口径最终定稿**）：**整条线平移**，不是"各帖按自己的真实时间"。
#     ⇒ 三条 t0 帖**一并在 `2026-05-22 22:00`**：
#       · `EV1_DY_001`（刘嘻嘻）的 `publish_time` 由 `2026-05-21 22:00` **改为 `2026-05-22 22:00`**
#         （已落在 `t0状态_第0轮.json` 的 `injected_posts` 里）；
#       · `EV1_XHS_001` / `EV1_XHS_002` 原先是只有日期的 `2026-05-22`，
#         ⇒ 走 post_time() 的"缺时分 → 按 T0"分支。**2026-10-08 定案「T0 时间对齐」后已改**
#         —— 两条也显式写成 `2026-05-22 22:00`（草案 §R-43 B），三条一律走 `strptime` 正常分支；
#         缺时分分支**保留作兜底**（换事件时若上游只给日期，仍能按 T0 处理）。
#     ⇒ **Δt 对三条帖全为 0**，"Dt=0" 的前提**重新成立**。
#   ⚠️ **`t1..t15` 的快照是本次改动**之前**跑的**（当时 `DY_001` 的 Δt = 26h），
#     登记表 §5.15 的数与报告的数都出自那一跑 ⇒ **与现在的口径差一条帖的 Δt**。
#     明确指示**不重跑**；如需完全对齐，从轮 0 起 `--force` 重跑一遍即可。
#   ✅ 附带好处：3 位 t0 发帖人的 `persona_bio` 写的是「5月22日」（豆豆大侠 / 长城网）与
#     「5月21日晚上十点左右」（刘嘻嘻，**该条画像与本口径仍差一天**，待定）。
T0_TIME = CFG.t0          # ★ 由配置 `t0_time` 来（EVT-001 = 2026-05-22 22:00）
# ★ R-50（2026-10-12 定案）：轮次表由「2h/4h/8h × 36 轮」（R-11 旧表）改为
#   **「等长 4h × 42 轮」**。覆盖仍为 05-22 22:00 → 05-29 22:00 = 168h = 7.0 天。
#   理由（`_probe_grid.py` / `_probe_len.py` 实测）：旧表把**同一个起点钟点**切成
#   2/4/8h 三种宽度（12 个起点里 6 个中招）、日历日 26h / 28h ⇒「每天的情况」没有统一
#   分母；抽样池 41.2 / 62.8 / 83.7、传染迭代 12 / 6 / 3 轮每天 —— 这些差异**没有一条
#   是事件驱动的，全部是轮长的产物**。
#   为什么是 4h：等长 h + 覆盖满 168h + 命题 6「≥ 30 轮」+ h | 24 ⇒ h ∈ {1,2,3,4}，
#   **4h × 42 是最省的一档**（6h → 28 轮，**违反 ≥30 轮**）。
#   ⚠️ `N_ROUNDS` 只是文档性常量（代码里没有硬停逻辑，实际轮次由 `--round R` /
#      `next_round()` 驱动），但**改动轮次表必须同步改它**。
N_ROUNDS = CFG.n_rounds   # ★ 由配置 `n_rounds` 来（EVT-001 = 42）
GAIN = 2.0            # R-02 增益（P-05，建议区间 1.5–2.5，取中值）
# ★ 2026-10-07 修：`N_AGENTS` / 名额三档原先是**写死的 102 / 13 / 4 / 21**。
#   换事件（或命题 6 的 500 人场）时群规模一变，名额上下限**会静默算错**。
#   改为从 t0 快照的 `agents` 条数推导，见下方 §2 载入后的那三行。
#   ★ R-50（2026-10-12）：名额基准由 `⌈N×12%⌉` 改为 **`⌈N×9%⌉`**，上下限不动。
#     见下方 §2 那三行的注释（依据：峰槽在实测 dev 全域内恰好不触顶）。
#   规则式：名额基准 = ⌈N×9%⌉、下限 = ⌈N×3%⌉、上限 = ⌈N×20%⌉（N = 群规模）。

SEED = 20260521
OFF_SAMPLE, OFF_EV, OFF_EA = 1, 2, 3     # P-07 未定，先钉死三路
EPS_SIGMA = 0.05                          # 已拍板进 MVP
EPSILON = 0.1                             # R-08 兴趣匹配兜底
K_HEAT = 1.0                              # R-08 热度系数（placeholder）
# ★ 2026-10-07 定案（草案 §R-36 / 登记表 F-12）：**热度时间衰减**，只作用于视野排序。
#   病：`K_HEAT` 那项只涨不跌 ⇒ 老帖以 ~5 倍权重永久占位，15 轮里 30 条池有 23 条从未进过视野。
#   药：排序分 P_view = P × exp(−LAMBDA_HEAT × Δt_h)；**P 本身逐位不变**（传染权重 Σ_m 与快照都不动）。
#   值：半衰期 **24h** ⇒ λ = ln2/24 ≈ 0.0289。离线标定（`_calib_lambda.py`）拐点在 0.02–0.03：
#       λ 0.01→0.02 时新帖入视野率 25.1% → 51.2%（翻倍），老帖位次只掉 10 点（96.8% → 86.6%）。
#   🟡 待重跑复核（见登记表 §3.3）。
LAMBDA_HEAT = math.log(2) / 24.0          # R-36
N_VIEW = 5                                # R-09

PEAK_15   = {7, 8, 9, 10, 12, 13, 19, 20, 21, 22}
VALLEY_03 = {0, 1, 2, 3, 4, 5}

# ── R-22 显式参数（2026-10-07 定案）────────────────────────
# 见《仿真层参数与公式登记表.md》§3.2
DECAY_ANCHOR    = 'baseline' # 'base_0' | 'baseline'  衰减归位点（2026-10-19 改为 baseline：事件必须能回到事发前；待跑批验证）
# ★ R-42（2026-10-08 定案 = 方案 V3）：**传染项由「向均值靠拢」改为「渲染式相加」**
#   旧式 Δv = G·sense_i·Σ_m，Σ_m = Σ(w/Σw)(v̄_m − v_i) —— 是"把你朝看到的平均拉过去"。
#   病（实测 30 轮 1922 在线人次）：100% 的人看到的贴全是负面，却有 **455 人次（23.7%）
#     被推成正向** —— 越负面的人看负面内容反被"安慰"。且推动中位数仅 0.018 < 噪声 σ=0.05
#     ⇒ 社交信号被噪声盖过，这就是"情绪起伏不大"的算术原因。
#   新式 Δv = G·sense_i·v̄_seen·(1 − |v|)
#     · 方向 = sign(v̄_seen)：看到负面就更负面、看到正面就更正面（**渲染**，不是趋同）
#     · 强度 ∝ |v̄_seen|：对方情绪越弱，影响越小（定案原话："-0.1 的话变化不会很多"）
#     · (1−|v|) = **可被影响的余地**：中立者余地为 1、已顶到 ±1 者为 0 ⇒ 公式自带限幅，
#       不需要外挂封顶（V1 纯相加会饱和到 −1，实测末轮 2.9% 的人贴到 ±1）
#   ⚠️ G 的语义已变：旧 =「消掉差距的几成」（比例），新 =「对方情绪按几折加成」（折扣）。
#      数值不可互推。0.2 沿用；**多轮重标仍欠**（登记表 §4 那条 🟡）。
#   ⚠️ 本事件里 v̄_seen 全程为负 ⇒ 新式**只往下推、从不往上推**；曲线回升全部来自衰减
#      ⇒ 起伏来自昼夜节律（白天在线者被压下、夜里离线者回漂到 base）。已确认属预期。
CONTAGION_GAIN  = 0.2        # 传染项增益（R-42 起乘在 sense_i × v̄_seen × (1−|v|) 外面）
CONTAGION_GAIN_A = 0.2       # 唤醒维的传染增益
# ★ R-42：**唤醒维必须单独定义** —— 旧代码让唤醒复用效价的 Σ_m，V3 之后 Σ_m 已不存在；
#   且"看到负面内容 ⇒ 激动程度下降"语义直接反。三选一（尚未定，默认 gap）：
#     'gap'     Δa = G_a·sense·(ā_seen − a)   ← 默认。**唤醒是"强度"不是"极性"**，
#                 只有大小没有正负，所以"被带向看到的激动水平"是对的（趋同语义在强度维成立）
#     'mirror'  Δa = G_a·sense·ā_seen·(1 − a) ← V3 的逐字镜像。⚠️ 单向棘轮：ā_seen ≥ 0
#                 且 a∈[0,1] ⇒ 唤醒只涨不落，平衡点在 a = 1（实测会失真）
#     'legacy'  Δa = G_a·sense·(v̄_seen − v_衰减后) ← 逐位复现 R-42 之前的唤醒行为，
#                 用来**把效价的影响隔离出来看**（表2 的唤醒维不动）
AROUSAL_MODE = 'gap'

CHANNELS = ['微博', '小红书', '抖音', '黑猫投诉', '新闻门户', '监管通报']
FLOW_CH  = ['微博', '抖音', '小红书', '新闻门户']          # 流通型
EXIT_CH  = ['黑猫投诉', '监管通报']                        # 出口型（只出不进）

# R-28 / R-28c 渠道传播稀疏表： (帖渠道 A → 读者渠道 B) -> 折扣
#   2026-10-07 两改：① **延迟门取消**，只留折扣档（仿真侧："延迟跟折扣不会互相影响"）
#                    ② 黑猫投诉加 **4 条「行为入向」**（仿真侧："黑猫投诉肯定是有问题的"）
#   ⇒ 允许边 20 → 24；禁止边 10 → 6（4 流通型→监管通报 + 黑猫↔监管）
EDGE = {
    # ── 流通型 4×4 互达（12 条）──────────────────────────────
    ('抖音', '微博'):       0.8, ('抖音', '小红书'):     0.5,
    ('抖音', '新闻门户'):   0.5, ('小红书', '微博'):     0.5,
    ('小红书', '抖音'):     0.5, ('小红书', '新闻门户'): 0.2,
    ('微博', '抖音'):       0.5, ('微博', '小红书'):     0.2,
    ('微博', '新闻门户'):   0.8, ('新闻门户', '微博'):   0.8,
    ('新闻门户', '抖音'):   0.5, ('新闻门户', '小红书'): 0.2,
    # ── 出口型出向 · 黑猫投诉（4 条）─────────────────────────
    ('黑猫投诉', '微博'):       0.8, ('黑猫投诉', '新闻门户'): 0.8,
    ('黑猫投诉', '抖音'):       0.2, ('黑猫投诉', '小红书'):   0.2,
    # ── 出口型出向 · 监管通报（4 条）─────────────────────────
    ('监管通报', '微博'):       0.8, ('监管通报', '新闻门户'): 0.8,
    ('监管通报', '抖音'):       0.5, ('监管通报', '小红书'):   0.2,
    # ── ★ R-28c 新增：4 条「行为入向」低档边（流通型 → 黑猫投诉）──
    #    语义 = "人跨平台去投诉"，**不是**"内容被搬运" ⇒ 只给最低档 0.2。
    #    解决：驻黑猫的那 1 个客户（黑猫投诉_客户_2271）视野恒空 ⇒ 永远沉默。
    ('微博', '黑猫投诉'):       0.2, ('小红书', '黑猫投诉'):   0.2,
    ('抖音', '黑猫投诉'):       0.2, ('新闻门户', '黑猫投诉'): 0.2,
    # ── 明确禁止（6 条，不入表即禁）───────────────────────────
    #    4 流通型 → 监管通报（监管不看视野；出场倾向由 R-34 表2c 阈值B 决定。R-35 起监管是普通角色）
    #    黑猫投诉 ↔ 监管通报（2 条）
}

# R-24 表1： (角色, 渠道) -> dict(人数, 措辞, δ, 可用动作)
ALL5 = ['QUOTE_POST', 'REPOST', 'CREATE_COMMENT', 'LIKE_POST', 'DO_NOTHING']
# ★ 2026-10-07 R-33：`COMPLAINT`「投诉」独立动作 —— **只进客户的动作集**。
#   ⚠️ 它**不在** 0–4 的档位阶梯上（`GEAR` 不加第 5 档、`gear_of()` 的 clamp 上界仍是 4）。
#   它的开关由 **表2b** 表达（只有 ('强负','高') / ('负','高') 两格含 5），
#   且 **候选栏只在它进入本轮推荐集合时才列出**（仿真侧选「乙」= 真门槛，见 `build_user`）。
ALL5C = ALL5 + ['COMPLAINT']            # 客户在非黑猫渠道的完整动作集
TABLE1 = {
    ('网民', '微博'):     dict(n=40, word='引用',     act=ALL5, delta=0),
    ('客户', '小红书'):   dict(n=13, word='讲述遭遇', act=ALL5C, delta=1),
    ('网民', '抖音'):     dict(n=10, word='引用',     act=ALL5, delta=0),
    ('客户', '微博'):     dict(n=8,  word='讲述遭遇', act=ALL5C, delta=1),
    ('KOL',  '微博'):     dict(n=8,  word='发表观点', act=ALL5, delta=1),
    ('网民', '小红书'):   dict(n=7,  word='引用',     act=ALL5, delta=0),
    ('媒体', '微博'):     dict(n=5,  word='发稿',     act=ALL5, delta=None),  # ★ R-34 改走表2c
    ('监管', '监管通报'): dict(n=3,  word='发布通报', act=['QUOTE_POST', 'DO_NOTHING'], delta=None),
    ('KOL',  '抖音'):     dict(n=2,  word='发表观点', act=ALL5, delta=1),
    ('媒体', '小红书'):   dict(n=1,  word='发稿',     act=ALL5, delta=None),  # ★ R-34 改走表2c
    ('客户', '抖音'):     dict(n=1,  word='讲述遭遇', act=ALL5C, delta=1),   # ★ 2026-10-07 δ +2→+1
    # ★ R-33：黑猫席位的「引用」换成「投诉」（仿真侧：「他没法在黑猫发帖，他只有投诉」）
    ('客户', '黑猫投诉'): dict(n=1,  word='投诉',     act=['COMPLAINT', 'CREATE_COMMENT', 'LIKE_POST', 'DO_NOTHING'], delta=1),
    ('KOL',  '小红书'):   dict(n=1,  word='发表观点', act=ALL5, delta=1),
    ('媒体', '抖音'):     dict(n=1,  word='发稿',     act=ALL5, delta=None),  # ★ R-34 改走表2c
    ('媒体', '新闻门户'): dict(n=1,  word='发稿',     act=ALL5, delta=None),  # ★ R-34 改走表2c
}
# ★ 2026-10-07 两处 δ 修正（定案）⇒ δ 表从此**纯角色属性**（0 / +1 / +2），与 §5.4.4 对齐：
#   ① 媒体×新闻门户 +3→+2：+3 会让"并不在意的媒体（效价≈0）"也发稿；差异只在 表2=0/1 时出现。
#   ② 客户×抖音   +2→+1：违反 δ 语义（「+1 加成只给职务出口，不给客户自诉出口」）；且抖音的
#      传播力已算在 R-08 传播范围因子 / R-28 边权折扣里，再给 δ = 同一事实算两遍。

# 效价档分界（原属 R-24 表2；表2 已于 2026-10-19 删除，V_BANDS 仍被表2b / 表2c / 快照 band 使用）
V_BANDS = [('强负', -9.9, -0.6), ('负', -0.6, -0.2), ('中', -0.2, 0.2), ('正', 0.2, 0.6), ('强正', 0.6, 9.9)]
# ══════════════════════════════════════════════════════════════
# ★ R-44（2026-10-12 定案）：**唤醒界值分角色**，不再全体共用一套
# ══════════════════════════════════════════════════════════════
# 旧写法（全体共用）`[('低',-9.9,0.4),('中',0.4,0.7),('高',0.7,9.9)]` 有一条**死档**：
#   高唤醒下界 0.70 **全场没人够得到** —— 实测全场唤醒 max = 客户 0.680 / 网民 0.664 / KOL 0.563
#   ⇒ 653 条动作记录里**高唤醒 0 条**，15 格只踩 6 格，正 / 强正两行 0 条。
#   ⇒ 表2b 的「高唤醒列」（唯一的引用/投诉来源）**从未被踩到过**。
#
# 改法：**低界统一 0.40**（三档语义不变），只让**中/高界**按角色标定，
#   使各角色都有实测可及的高唤醒带：
#     · 网民 0.40 / **0.70**（不调，保持原样；实测 90 分位 0.468 ⇒ 进高仍罕见，见下）
#     · KOL  0.40 / **0.50**（实测进高 **9.6%**）—— KOL 的 0.50 是**「引用」开关**（高格相对中格新增引用）
#     · 客户 0.40 / **0.55**（实测进高 **13.0%**）—— 客户的 0.55 是**「投诉」开关**（高格相对中格新增投诉）
#   标定依据：每个角色「高」档的**新增动作**要正对它自己的身份出口（KOL→引用 / 客户→投诉），
#   故界值落在各自唤醒分布的中上段，而不是同一个绝对数。见登记表 §3.4e / §5.5。
#   ⚠️ **媒体 / 监管不给界值** —— 它们走表2c，不查表2/表2b（见 `rec_set()` 的机构分支）。
#   ⚠️ 低界统一 0.40 ⇒ 「低/中」分界对个体角色同义，角色差异**只压在一条界值上**；
#      代价是 `('负',0.52)` / `('中',0.52)` 两点 客户 ⊉ KOL（KOL 已在交叉带升档、客户还没），
#      已接受（交叉带正是 KOL 多出「引用」处，方向符合意图）。
A_BAND_LO = 0.40
A_BAND_HI = {'网民': 0.70, 'KOL': 0.50, '客户': 0.55}   # 媒体 / 监管 不进这张表（走表2c）
GEAR = {0: 'DO_NOTHING', 1: 'LIKE_POST', 2: 'CREATE_COMMENT', 3: 'REPOST', 4: 'QUOTE_POST',
        5: 'COMPLAINT'}          # ★ R-33：5 只是**表2b 里的一个记号**，不是档位阶梯的第 5 档
GEAR_CN = {0: '沉默', 1: '点赞', 2: '评论', 3: '转发', 4: '引用', 5: '投诉'}
GEAR_REV = {v: k for k, v in GEAR.items()}
COMPLAINT_GEAR = 5               # ★ R-33：表2b 里代表「投诉」的记号
# ★ 2026-10-19：**表2（单档位）已删除**。依据：真跑时它从未参与决策 ——
#   `TABLE2` 只在 `gear_of()` 里被读一次，而 `gear_of()` 的返回值只进快照 `gear`/`gear_cn`
#   与 `--mock` 的动作合成；真正喂 prompt 的 `rec_set()` 直接读 `TABLE2B`，全程不经过 TABLE2。
#   删表后：`rec['gear']` 一律为 None（字段保留，避免旧对账脚本 KeyError）、
#   `gear_cn` 改记「表2b / 表2c」、`--mock` 一律取推荐集合里的最高档。
#   投诉本来就没有档位（它在表2b 里是记号 5），与本次删除无关。

# ★ 2026-10-07 新增 表2b（R-24）：(效价档, 唤醒格) → **推荐集合**（非沉默动作的档位）
#   三条构成原则（定稿）：
#     ① **进阶**：同一效价行内，唤醒高的格子 ⊇ 唤醒低的格子（激动的人一样能做平静时做的事）
#     ② **沉默只推荐给低唤醒列**（中/高列不给）——沉默在任何格子都仍**合法**，这里定的是"推荐"
#     ③ **它只是推荐、不限制候选** ⇒ 只驱动 prompt 的 `【本轮倾向】` 一行，不做合法性校验
#   与 表2 的关系：表2（单档位）已于 2026-10-19 删除；**表2b 是唯一喂 prompt 的表**。
#
# ★ 2026-10-07 R-33 扩容：`('强负','高')` 与 `('负','高')` **两格加 5（投诉）**，其余 13 格逐位不变。
#   仿真侧：「**我们之前做那个表2b 才是最主要的那个表**，只是你在往里面加了投诉这个动作」
#        「**不，正向的人不会去做投诉这个行动的**」⇒ 正 / 强正两行**没有任何投诉**。
#   投诉只出现在**高唤醒列** ⇒ 顺带自动满足 ① 进阶（低 ⊂ 中 ⊂ 高）。
TABLE2B = {
    ('强负', '低'): [2, 3],        ('强负', '中'): [2, 3, 4],        ('强负', '高'): [2, 3, 4, 5],
    ('负',   '低'): [1, 2],        ('负',   '中'): [1, 2, 3],        ('负',   '高'): [1, 2, 3, 4, 5],
    ('中',   '低'): [1],           ('中',   '中'): [1, 2],           ('中',   '高'): [1, 2, 3],
    ('正',   '低'): [1],           ('正',   '中'): [1, 2],           ('正',   '高'): [1, 2],
    ('强正', '低'): [1, 2],        ('强正', '中'): [1, 2, 4],        ('强正', '高'): [1, 2, 4],
}

# ══════════════════════════════════════════════════════════════
# ★ 2026-10-07 R-34 新增：机构动作表 **表2c** —— 一张表 · 两套阈值
# ══════════════════════════════════════════════════════════════
# 为什么机构不能走表2b（实测依据）：
#   ① 表2b 喂的是**本人情绪**（效价 × 唤醒）。8 个媒体里 **6 个的 `interest_topics` 完全不沾本案**
#      （就业与劳动权益×4 / 教育医疗 / 食品安全）⇒ 表1 的"兴趣匹配"对它们恒 ≈ ε = 0.1。
#      旧规则下媒体能发稿全靠 `δ = +2` 硬顶（绕过兴趣）。
#   ② 监管的 `persona_bio` 逐条写「不回应个案咨询 / 不对未定性的传闻作任何回应」⇒ 用个人情绪驱动
#      监管在语义上是错的。监管只对**面上情况**表态：整体情绪有多负面 + 传得有多广。
#   ⇒ 机构的两根轴 = **群体效价档 × 传播范围档**（都不是"本人"的量）。
#
# 时序：第 r 轮读第 r−1 轮轮末的 ① 群体效价（= 本轮在线者均值，口径丙）② 帖子池（算 K*）。
# 与表2b 读"第 r−1 轮轮末的本人情绪"是同一条 R-22b 时序规则。
#
# ⚠️ 表2c 直接给**档位集**（同表2b 同构，倾向不是命令），**不过 δ 平移**
#    ⇒ `('媒体',·).delta` 已退休为 None。
SPREAD_BANDS = [('局域', 0, 2), ('跨平台', 3, 3), ('全网', 4, 99)]   # K* → 传播范围档
GROUP_V_MIN = 20        # 口径丙：在线者 < 20 人 ⇒ 沿用上一轮的效价档，不重判
T2C_WORD = {'DO_NOTHING': '沉默', 'LIKE_POST': '点赞', 'CREATE_COMMENT': '评论',
            'REPOST': '转发', 'QUOTE_POST': '引用'}

# 阈值 A —— **地方小媒体**（表1 动作集 = ALL5；仿真侧：「媒体指的是这种地方小媒体」）
TABLE2C_A = {
    ('强负', '局域'):   ['REPOST', 'QUOTE_POST'],
    ('强负', '跨平台'): ['QUOTE_POST'], ('强负', '全网'): ['QUOTE_POST'],
    ('负', '局域'):     ['DO_NOTHING', 'REPOST'],
    ('负', '跨平台'):   ['REPOST'],     ('负', '全网'):   ['REPOST', 'QUOTE_POST'],
    ('中', '局域'):     ['DO_NOTHING'],
    ('中', '跨平台'):   ['DO_NOTHING', 'LIKE_POST', 'CREATE_COMMENT'],
    ('中', '全网'):     ['CREATE_COMMENT', 'REPOST'],
    ('正', '局域'):     ['DO_NOTHING'], ('正', '跨平台'): ['DO_NOTHING'],
    ('正', '全网'):     ['DO_NOTHING', 'LIKE_POST'],
    ('强正', '局域'):   ['DO_NOTHING'], ('强正', '跨平台'): ['DO_NOTHING'],
    ('强正', '全网'):   ['DO_NOTHING', 'LIKE_POST'],
}
# 阈值 B —— **监管 / 中央级媒体**（仿真侧：「中央这边的媒体应该跟监管它是一个 level」
#   ⇒ 并入监管层）。**只有右上角一格** —— 仿真侧：「阈值B 监管你就只需要引用就行，
#   也就是做通报」⇒ 通报 = 监管格的 `QUOTE_POST`（表1 word = '发布通报'）。
TABLE2C_B = {
    ('强负', '全网'): ['QUOTE_POST'],
}
INST_ROLES = ('媒体', '监管')      # 走表2c 的角色（表2b 从此**只管个体角色**）

DECAY_V = {'客户': 0.96437, '网民': 0.95394, 'KOL': 0.94868, '媒体': 0.93808, '监管': 0.92736}
DECAY_A = {'客户': 0.880, '网民': 0.860, 'KOL': 0.850, '媒体': 0.830, '监管': 0.810}

# ★ R-41（2026-10-08 定案）：**上游给几个议题键就用几个** —— 仿真层**不写死任何议题名**。
#   定案原话：「他（的维度）应该是和事件对齐，也就是**上游给了多少 emotion_sensitivity，我们就用多少**，
#             这个是**脱离这个事件**的整体规则」。
#   实测：`stance_bias` / `emotion_sensitivity` **102/102 人每人恰好 1 个键**
#        （键名三种：宠物险争议 49 / 广告内容低俗 33 / 性别与价值观争议 20，且两个 dict 逐人键一致）
#        ⇒ 取值 = **单键直取**。
#   ⚠️ **不设白名单**：原先那版写死 `TOPIC_KEYS = [三个键]`，本质是"我们替上游认定哪些键合法"，
#      与"上游说了算"相反。改成：**取不出来就硬报错**（跑前断言，见 §PROF 之后的 `_topic_audit`）。
#   ⚠️ **绝不静默回落** —— 静默回落正是原 bug 的藏身处：旧代码只认 '宠物险争议'，
#      **53/102 人**无声取到 `stance_bias → 0.0`（显示成"中立"）/ `emotion_sensitivity → 1.0`
#      （比真值 ~0.7 **放大 ~40%**），没有任何提示。
def topic_val(d, who=''):
    """取**本人议题**的值：上游给几个议题键就用几个；实测每人一个 ⇒ 单键直取。

    取不出来（0 个键 / 多于 1 个键）⇒ **抛错**，不猜、不回落。
    上游若哪天给某人配多个议题，须补一个"本人主议题"字段；代码**不替它猜**是哪个。
    ★ 2026-10-20（v8）：上游可能直接给**裸 float**（0 个键）⇒ 单值直接返回。
    """
    if isinstance(d, (int, float)):
        return float(d)
    if len(d) == 1:
        return next(iter(d.values()))
    if len(d) == 0:
        raise KeyError('议题 dict 为空，取不出本人议题值（%s）' % (who or '?'))
    raise KeyError('议题 dict 有 %d 个键 %s，无法判定哪个是"本人议题"（%s）；'
                   '请上游每人只给一个议题键，或补 `primary_issue` 字段'
                   % (len(d), sorted(d), who or '?'))


# ══════════════════════════════════════════════════════════════
# 1. 工具
# ══════════════════════════════════════════════════════════════
def round_len(r):
    """R-50 轮长时间表（**2026-10-12 改版：2h/4h/8h × 36 轮 → 等长 4h × 42 轮**）

        轮 0 – 41   4h × 42 = 168h   05-22 22:00 → 05-29 22:00

    ★ 覆盖与 R-11 旧表**逐位一致**（168h = 7.0 天），只是格子切法变了。

    改版理由（2026-10-12：「**我觉得等长轮才是对的，不能因为我们认为前两天重要
    就这么判断**」）：旧表把**同一个起点钟点**切成多种宽度（`_probe_grid.py`：22:00 在
    05-22 是 2h 格、05-23 是 4h 格、05-25 是 8h 格），日历日 26h / 28h ⇒「每天的情况」
    没有统一分母；抽样池（41.2 / 62.8 / 83.7）与传染迭代（12 / 6 / 3 轮每天）同样是
    轮长的函数。**这些差异没有一条是事件驱动的。**

    为什么是 4h：等长 h + 覆盖满 168h + 命题 6「≥ 30 轮」+ h | 24 ⇒ h ∈ {1,2,3,4}，
    **4h × 42 是最省的一档**（6h → 28 轮，违反 ≥30；3h → 56 轮，贵 33% / 慢 33%，
    换来的只是前 24h 从 6 个采样点变 8 个）。

    ⚠️ 旧表「分段边界按轮号切（r<12 / r<24）」的约束**随本改版消失** —— 全表等长，
       无分段可言，`window_of()` 与轮号恒一致。
    """
    return CFG['round_hours']   # ★ 由配置 `round_hours` 来（EVT-001 = 4）

def window_of(r):
    s = T0_TIME + timedelta(hours=sum(round_len(k) for k in range(r)))
    return s, s + timedelta(hours=round_len(r))

def fmt_t(dt):
    return dt.strftime('%Y-%m-%d %H:%M')

def band(val, bands):
    for name, lo, hi in bands:
        if lo <= val < hi:
            return name
    return bands[-1][0]

def a_band(role, ar):
    """★ R-44：唤醒格 —— **按角色查界值**（低界统一 0.40，中/高界见 `A_BAND_HI`）。

    只有个体角色（网民 / 客户 / KOL）在表2/表2b 里用得到它；媒体 / 监管走表2c，
    `gear_of()` 与 `rec_set()` 都在查表之前就对机构角色返回，不会走到这里。
    给不出的角色（媒体 / 监管）退回 0.70 —— 兜底值，不影响任何实测数。
    """
    hi = A_BAND_HI.get(role, 0.70)
    return '低' if ar < A_BAND_LO else ('中' if ar < hi else '高')

def w_peak(r):
    """R-03 峰谷折扣 `w` —— **R-50 后恒为 1.0（回到全幅）**。

    ★ 2026-10-12：旧表是「按轮长索引」的三值表 `1.0(2h) / 0.25(4h) / 0.0(8h)`。
      旧表之所以需要它，是因为窗口越宽、日内节律被算术均值抹得越平（2h 摆 5×、
      旧 6h 摆 2×、12h 摆 1.5×），且 8h 窗口跨半天、"峰谷"语义不成立。
      **等长表下所有窗口同宽，这个前提消失** ⇒ `w` 退化为常量，`G(r) ≡ Ḡ(r)`。

    ★ 依据 = 2026-10-12 的口径：「4 个小时各看一下对应的峰谷系数，选出每个小时
      应该抽多少人，然后每个小时的抽样**乘以 0.25（一个小时在四个小时中的权重）后
      加总**」—— 该式逐位等于 `Ḡ = Σᵢvᵢ/4`，**没有任何"再收一次"的步骤**。

    ⚠️ **两个 0.25 不是一回事，勿混**：定案说的 0.25 = **小时在 4h 窗口里的占比
      （1/4）**，它**已经**写在 `G_of()` 的 `/len(vals)` 里；旧表的 `w(4h)=0.25` 是
      **峰谷折扣**（把 Ḡ 偏离 1 的幅度再收一档）。**同名不同物。**

    保留本函数（而非把 `G_of()` 里的乘法删掉）是为了：① `params` 继续记 `w` 供审计；
    ② 日后若要重开分段折扣，只改这一处。
    """
    return 1.0

def G_of(r):
    s, e = window_of(r)
    hrs = [h for h in range(s.hour, s.hour + round_len(r))]
    vals = [1.5 if (h % 24) in PEAK_15 else (0.3 if (h % 24) in VALLEY_03 else 1.0) for h in hrs]
    gbar = sum(vals) / len(vals)
    return 1.0 + (gbar - 1.0) * w_peak(r), gbar, hrs

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def channel_reach(ch_i, ch_m):
    """R-28a 渠道可达（**延迟门已取消**）：同渠道 1.0 ／ 无通路 0.0 ／ 有通路即刻给折扣。

    ch_i = 读者所在渠道，ch_m = 帖所在渠道。EDGE 的键是 (帖渠道, 读者渠道)。
    """
    if ch_i == ch_m:
        return 1.0
    return EDGE.get((ch_m, ch_i), 0.0)


def interest_match(it, topics):
    """兴趣匹配 = ε + (1-ε) × min(1, Σ 本人在帖话题上的权重)。"""
    if isinstance(it, str):
        s = 1.0 if it in (topics or []) else 0.0
    else:
        s = sum(float(v or 0) for k, v in (it or {}).items() if k in (topics or []))
    return EPSILON + (1.0 - EPSILON) * min(1.0, max(0.0, s))


def calc_P(aid, pid, t_i, posts=None):
    """P 四因子由仿真层现算；桥接不再预算。管道：兴趣匹配 / 传播范围 / 渠道可达 / 新鲜度。"""
    p = (posts or POSTS)[pid]
    prof = PROF[aid]
    reach = channel_reach(prof['primary_channel'], p['channel'])
    if reach <= 0:
        return None
    interest = interest_match(prof['interest_topics'], p.get('topics') or [])
    spread = float(PROF[p['poster_agent_id']]['influence_weight'])
    dt_h = max(0.0, (t_i - p['t_post']).total_seconds() / 3600.0)
    decay = max(math.log((271.8 - dt_h) / 100.0), 0.05)
    heat = 1.0 + K_HEAT * math.log(1 + p['interactions'])
    fresh = decay * heat
    P = interest * spread * reach * fresh
    decay_heat = math.exp(-LAMBDA_HEAT * dt_h)
    return dict(兴趣匹配=round(interest, 4), 传播范围=round(spread, 4),
                渠道可达=round(reach, 4), 新鲜度=round(fresh, 4),
                P=round(P, 6), 衰减_热度=round(decay_heat, 6),
                P_view=round(P * decay_heat, 6))


# ══════════════════════════════════════════════════════════════
# 2. 载入
# ══════════════════════════════════════════════════════════════
def state_file(r):
    """第 r 轮的**快照**路径。第 0 轮叫 `t0状态_第0轮.json`（t0 快照 = 轮首）。"""
    return CFG.snap_path(r)


def next_round():
    """还没跑的最大轮次 + 1 —— 缺省行为，所以手滑重跑第 1 轮不会发生。"""
    r = 0
    while os.path.exists(state_file(r + 1)):
        r += 1
    return r + 1


R = args.round if args.round is not None else next_round()   # ★ 本轮
W_START, W_END = window_of(R)

# ── 防覆盖（第 1 轮被 mock 冲掉的教训 → 现在是硬拒绝，要写得明明白白）──
_out_state = CFG.snap_path(R, SUFFIX)
_dup_rows = 0
_rs = CFG.recorder_path(SUFFIX)
if os.path.exists(_rs):
    for _ln in open(_rs, encoding='utf-8'):
        if _ln.strip() and json.loads(_ln)['round'] == R:
            _dup_rows += 1
if (os.path.exists(_out_state) or _dup_rows) and not args.force:
    print('⛔ 第 %d 轮已有产物（快照 %s / 记录员行 %d 条）。' %
          (R, '存在' if os.path.exists(_out_state) else '不存在', _dup_rows))
    print('   要重跑请显式加 --force，或换个轮次。')
    sys.exit(1)

T0 = json.load(open(CFG.t0_path, encoding='utf-8'))
PROF_RAW = json.load(open(CFG.persona_path, encoding='utf-8'))
PROF = {a['agent_id']: a for a in PROF_RAW['agents']}

# ── ★ R-41 跑前断言：议题键「上游给几个就用几个」------------------------------
#   仿真侧：「**上游给了多少 emotion_sensitivity，我们就用多少**，这是脱离这个事件的整体规则」。
#   ⇒ 代码**不写死任何议题名**，只要求**每人能取到一个**。取不出来就**硬报错，不静默回落**。
#   ⚠️ 跑前断言而不是等 `topic_val()` 在轮中途抛：早崩，且一次把所有出格的人列全。
_topic_audit = []
for _aid, _a in PROF.items():
    for _fld in ('stance_bias', 'emotion_sensitivity'):
        _d = _a.get(_fld)
        if isinstance(_d, (int, float)):        # ★ v8：裸 float 视作"单值"，等价于单键 dict
            continue
        _d = _d or {}
        if len(_d) != 1:
            _topic_audit.append('%s.%s 有 %d 个键 %s' % (_aid, _fld, len(_d), sorted(_d)))
if _topic_audit:
    print('⛔ R-41 跑前断言失败：以下 agent 的议题键**不是恰好 1 个**，取不出"本人议题"——')
    for _s in _topic_audit:
        print('   · ' + _s)
    print('   规则：上游给几个议题键就用几个；实测每人一个 ⇒ 单键直取。'
          '多键时请在画像侧补 `primary_issue` 字段，**仿真层不替上游猜**。')
    sys.exit(1)
_topic_dist = Counter()
for _a in PROF.values():
    _v = _a.get('stance_bias')
    if isinstance(_v, (int, float)):
        _topic_dist['<scalar>'] += 1
    else:
        for _k in (_v or {}):
            _topic_dist[_k] += 1
if set(_topic_dist) == {'<scalar>'}:
    print('   字段形态（R-41）：**单值 scalar** ｜ %d 人' % _topic_dist['<scalar>'])
else:
    print('   议题键（R-41，**由上游决定，代码不写死**）：%d 个 —— %s'
          % (len(_topic_dist), ' ／ '.join('%s %d 人' % (k, v) for k, v in _topic_dist.most_common())))
AG0 = {a['agent_id']: a for a in T0['agents']}
AGENTS = T0['agents']

# ── t0 六个输入 → 轮首效价/唤醒（v8：图谱只交 vb/ab/inv/es，S/base 在 params）──
#   旧快照没有 vb/ab/inv 时，回退到快照里已经算好的 valence/arousal（向后兼容）。
def _t0_start(a):
    if all(k in a for k in ('vb', 'ab', 'inv')):
        _p = T0.get('params') or {}
        _s = float(_p.get('S_event', 0.0))
        _b = float(_p.get('base', 0.0))
        _v = float(a['vb']) + float(a['inv']) * (_s - float(a['vb']))
        _ar = (0.30 + float(a['inv']) * 0.40 + _b) * 0.7 + float(a['ab']) * 0.3
        return _v, _ar
    return float(a.get('valence', 0.0)), float(a.get('arousal', 0.5))

for _a in AGENTS:
    _a['valence'], _a['arousal'] = _t0_start(_a)

# t0 帖的 P 在启动时算好：R>=2 的 recompute_P 还要拿它当 t0 基准，不能只在 R==1 分支算。
_T0_POSTS = {}
for _p in (T0.get('injected_posts') or []):
    _q = dict(_p)
    _q['t_post'] = T0_TIME
    _q['interactions'] = 0
    _T0_POSTS[_q['post_id']] = _q
for _a in AGENTS:
    _pm = {}
    for _pid in _T0_POSTS:
        _v = calc_P(_a['agent_id'], _pid, T0_TIME, posts=_T0_POSTS)
        if _v is not None:
            _pm[_pid] = _v
    _a['P'] = _pm

# ── R-02 名额三档：**从群规模推导**，不写死（2026-10-07 修）────────────────
#   N = 画像库实际人数（= t0 快照 agents 条数）。EVT-001 是 102 ⇒ 10 / 4 / 21。
#   换事件或换规模（如命题 6 的 500 人场）时，上下限自动跟着 N 走，不需要改代码。
#
# ★ R-50（2026-10-12 定案）：**名额基准由 `⌈N×12%⌉` 改为 `⌈N×9%⌉`**，上下限不动。
#   为什么改（`_probe_r50b.py` 实测，只读 35 轮快照现数）：
#     ① 实测 dev 中位 **0.2248**、max **0.2594**（此前口头用的 0.16 是错的，落在 p10 以下）；
#     ② 于是 `BASE=12%` 时峰槽 raw = 13×1.375×(1+2×dev) ≥ 21 在 **35/35 轮全部成立**
#        ⇒ 峰槽**恒被削平成 21**，dev 在早/午/晚三个峰槽完全失效；
#     ③ 无削顶条件是 `b × 1.375 × (1+2×dev_max) ≤ HI=21` ⇒ **b ≤ 10.06**；
#     ④ `⌈N×9%⌉ = 10` 是满足它的**最大取整值**，且日总量 **91.9 人次/天** ≈ 现跑实测
#        90.7（+1%）—— 即"换表不改量"，便于把曲线变化归因到表而不是量。
#   为什么不改 `HI` 而是改 `BASE`：两者是同一把尺子的两端（`LO/BASE/HI` 源自 500 人档的
#     `[60,100]` ⇒ 60/500=12%、100/500=20%）。改 `HI` 是"把桶加高"（量涨 +29%），
#     改 `BASE` 是"少装点水"（量持平）。2026-10-12 关心的是后者。
#   ⚠️ 余量仅 0.6%（raw_max 20.88 vs HI 21）：若新表 dev 上探过 0.2636 会重新触顶。
#     但触顶不是新病 —— 旧跑 36 轮里本来就有 12 轮 quota == 21。
N_AGENTS = len(AGENTS)
NAME_QUOTA_BASE = math.ceil(N_AGENTS * 0.09)
NAME_QUOTA_LO   = math.ceil(N_AGENTS * 0.03)
NAME_QUOTA_HI   = math.ceil(N_AGENTS * 0.20)
print('群规模 N = %d  ⇒ 名额基准 ⌈N×9%%⌉ = %d ／ 下限 ⌈N×3%%⌉ = %d ／ 上限 ⌈N×20%%⌉ = %d'
      % (N_AGENTS, NAME_QUOTA_BASE, NAME_QUOTA_LO, NAME_QUOTA_HI))
print('投诉资格（采集侧事件类型 = %s）= %s  ⇒ %s'
      % (EVENT_TYPE or '未给', COMPLAINT_POLICY,
         '允许投诉' if complaint_allowed() else '**不许投诉**（推荐集/候选栏都不会出现「投诉」）'))

# ── 上一轮**轮末**状态：R≥2 的唯一入口（R-22b：轮 r 轮首 ≡ 轮 r−1 轮末）──────
PREV = None
if R >= 2:
    # ★ 入口也带 SUFFIX：mock 链读 mock 快照，真跑链读真跑快照。
    #   否则 `--mock --round 3` 会读真跑的第 2 轮 ⇒ 两条链静默混起来。
    _prev_path = CFG.snap_path(R - 1, SUFFIX)
    if not os.path.exists(_prev_path):
        print('⛔ 找不到上一轮快照 %s —— 不能跨轮（必须先跑完第 %d 轮）。'
              % (os.path.basename(_prev_path), R - 1))
        sys.exit(1)
    PREV = json.load(open(_prev_path, encoding='utf-8'))
    if PREV['round'] != R - 1:
        print('⛔ 上轮快照里写的是第 %s 轮，对不上。' % PREV['round'])
        sys.exit(1)
PS = {a['agent_id']: a for a in PREV['agents']} if PREV else {}

# ── ★ R-39（2026-10-08 定案）：跨轮记忆段 —— 落地 **R-31 闸门 1** ────────
#   记什么：**只记「我自己做过什么」**（轮次 + 动作 + 目标 + 内容）。
#   不记什么：❌「我看到过什么帖子」—— 视野每轮由 Top-5 重新给一遍，记了纯浪费 token。
#   窗口口径：**「我自己最近 3 次有产出的动作」**，而**不是**「最近 3 轮」。
#     ⚠️ R-31 原文写的是「滑动窗口 = 最近 3~5 轮」，但那是**按轮**——实测（现成 30 轮快照）：
#        出场稀疏（101 人 / 497 人次 / 30 轮），按轮人均只看到 **0.36 条**，绝大多数字段全空；
#        按"最近 3 次"才人均 **1.77 条**。仿真侧口径：「非第一次出场…给一段历史自述」⇒ 取"次"。
#   沉默不入记忆：497 条里 51 条是 DO_NOTHING，无产出；进窗口只会把真内容挤掉。
#   来源：`t{r}状态_第{r}轮.json` 的 `actions[]`（**不是** `round_snapshot.jsonl` —— 那是全局聚合，无 per-agent 动作）。
MEMORY_K = 3
SKIP_MEM = {'DO_NOTHING'}
MEMORY = {}
for _r in range(0, R):
    _mf = CFG.snap_path(_r, SUFFIX)
    if not os.path.exists(_mf):
        continue          # 缺哪轮就少哪轮的记忆（续跑 / 中间缺件不致命）
    try:
        _acts = json.load(open(_mf, encoding='utf-8')).get('actions', [])
    except Exception:
        continue
    for _a in _acts:
        if _a.get('action') in SKIP_MEM:
            continue
        MEMORY.setdefault(_a['agent_id'], []).append(
            (_r, _a.get('action'), _a.get('target_post_id'), (_a.get('content') or '').strip()))
print('   跨轮记忆（R-39）：%d 人带历史，窗口 = 本人最近 %d 次有产出动作，沉默不入'
      % (len(MEMORY), MEMORY_K))


# t0 帖的"发帖时刻"：publish_time 缺时分的按 t0 处理（复现第 0 轮 Δt=0 的实测口径）
#   ★ 2026-10-08「T0 时间对齐」后，**三条 t0 帖都带时分** ⇒ EVT-001 已不走下面这个兜底分支。
#     保留它是为**换事件**时上游只给日期的情况（那时 Δt 仍按 0 处理，与 R-10 的 t0 语义一致）。
def post_time(p):
    s = p['publish_time']
    if len(s) <= 10:
        return T0_TIME
    return datetime.strptime(s, '%Y-%m-%d %H:%M')

# ── ★ 2026-10-14「显示名」口径：**显示名一律是 agent_id 的函数，且只有这一个出口** ──
#   两条口径（配置键 `display_name`，缺省 auto）：
#     auto     = 画像 persona_bio 里第一对「…」；抠不到退回 agent_id   ← **现行口径**
#     agent_id = 一律用 agent_id
#   ★ 关键约束：**池里所有帖都走这个函数，包括 t0 注入帖**。
#     原因：t0 帖的 poster_name 上游 t0 文件里写死了、自产帖由本函数生成；两边若各用一套，
#     **同一个人会在同一屏视野里挂两个名字**（如 t0 写「刘嘻嘻」、他的新帖写「抖音_网民_8764」）。
#     所以这里**不区分帖的来源**，一律按 poster_agent_id 重算 —— 口径永远自洽。
#   ★ `auto` 下重算的结果与已存值逐位相同（EVT-001 那 3 条 t0 帖的正则产出恰好等于上游写的
#     名字）⇒ 默认配置下 EVT-001 产物**逐位不变**；换 `agent_id` 才动。
DISPLAY_NAME_MODE = str(CFG.get('display_name') or 'auto').strip().lower()
if getattr(args, 'display_name', None):        # 调试/试跑用：命令行临时覆盖，不改配置
    DISPLAY_NAME_MODE = str(args.display_name).strip().lower()
if DISPLAY_NAME_MODE not in ('auto', 'agent_id'):
    sys.exit('✗ `display_name` 只认 `auto` / `agent_id`，收到 `%s`' % DISPLAY_NAME_MODE)


def display_name(aid):
    """agent 的显示名 —— 唯一出口。口径见上面 DISPLAY_NAME_MODE。"""
    if DISPLAY_NAME_MODE == 'agent_id':
        return aid
    m = re.search(r'「([^」]+)」', PROF[aid]['persona_bio'])
    return m.group(1) if m else aid


if R == 1:
    # ── 第 1 轮的池子 = t0 注入的 3 条帖（唯一一次"凭空"起池）──────────
    POSTS = {p['post_id']: dict(p) for p in T0['injected_posts']}
    for _p in POSTS.values():
        _p['t_post'] = post_time(_p)
        _p['author_valence_at_post'] = AG0[_p['poster_agent_id']]['valence']
        _p['author_arousal_at_post'] = AG0[_p['poster_agent_id']]['arousal']
        _p['channels'] = {_p['channel']}          # R-27 转发语义
        _p['quoted_id'] = None                    # R-27a 引用链（t0 帖无上游）
        # ★ 2026-10-14：t0 帖的显示名**也走 display_name()**（不照抄上游写的那个串）。
        #   与自产帖同一出口 ⇒ 同一个人在任何时候只有一个显示名。
        #   口径 `auto` 下重算结果 == 上游写的名字（EVT-001 三条实测相同）⇒ 产物不变。
        _p['poster_name'] = display_name(_p['poster_agent_id'])
        # ★ R-08 方案 A（2026-10-07 定案）：采集侧 like_cnt/comment_cnt **不进仿真**
        #   （capture_date 是事件后 4 个月，展示即泄漏）。保留原值仅作 provenance；
        #   仿真只认 *_sim 三件套，t0 一律从 0 起算 ⇒ interaction_total 第 0 轮 = 0。
        _p['like_cnt_sim'] = 0
        _p['comment_cnt_sim'] = 0
        _p['repost_cnt_sim'] = 0
        _p['interactions'] = 0                    # F-07 ④ 互动量 = like_cnt_sim + comment_cnt_sim
else:
    # ── R≥2：池子 = 上一轮快照里的 `posts` 原样搬回（含本轮之前自产的引用帖）──
    #   ★ 2026-10-08 定案「T0 时间对齐」：t0 三条注入帖的 `publish_time` **全部显式
    #      写成最后一个发帖人的时刻** `2026-05-22 22:00`。原先只有刘嘻嘻那条写了时分，
    #      另两条只写日期 `2026-05-22`，靠 `post_time()` 的"缺时分 → 按 t0"分支兜到 T0_TIME
    #      —— 值虽然一样，但**对齐是隐式的**，看数据看不出来。
    #      ⇒ 现在三条一律走 `strptime` 正常分支，Δt 恒为 0；缺时分分支保留作兜底。
    #   ⚠️ 已跑 30 轮的**池里仍是旧戳**：`EV1_DY_001` = `2026-05-21 22:00`（早一整天，
    #      刘嘻嘻那条）—— 那是续跑继承下来的，**只有从轮 0 重跑才会生效**。
    POSTS = {}
    for p in PREV['posts']:
        POSTS[p['post_id']] = dict(
            post_id=p['post_id'], channel=p['channel'], poster_agent_id=p['poster_agent_id'],
            poster_name=display_name(p['poster_agent_id']),   # ★ 2026-10-14：名字是 agent_id 的函数
            content=p['content'], publish_time=p['publish_time'],
            quoted_id=p.get('quoted_id'),
            author_valence_at_post=p['author_valence_at_post'],
            author_arousal_at_post=p.get('author_arousal_at_post'),
            like_cnt=p.get('like_cnt', 0), comment_cnt=p.get('comment_cnt', 0),
            like_cnt_sim=p['like_cnt_sim'], comment_cnt_sim=p['comment_cnt_sim'],
            repost_cnt_sim=p['repost_cnt_sim'], channels=set(p['channels']),
            t_post=post_time(p),
            interactions=p['like_cnt_sim'] + p['comment_cnt_sim'],
        )

PREV_CHANNELS = {pid: set(p['channels']) for pid, p in POSTS.items()}   # 记录员 A 跨轮差集用（轮首快照）

print('=' * 74)
print('%s · 第 %d 轮   窗口 = %s → %s  (%dh)' % (EVENT_ID, R, fmt_t(W_START), fmt_t(W_END), round_len(R)))
if PREV is not None:
    print('   入口：%s（上一轮轮末）  池内 %d 条帖'
          % (os.path.basename(_prev_path), len(POSTS)))
print('=' * 74)

# ══════════════════════════════════════════════════════════════
# 3. ① 情绪刷新（R-22 / R-07）
# ══════════════════════════════════════════════════════════════
# ★ 四路随机流（C-4：不用全局 seed）。2026-10-07 起**跨轮续跑**：
#   每轮把 4 条流的 `getstate()` 存进快照，下一轮 `setstate()` 恢复。
#   不这么做的话，"每轮另起一个进程"会从种子原点重新抽 ⇒ 第 2 轮抽中的是另一批人，
#   与"一口气连跑 30 轮"的结果分叉（详见登记表 §3.7）。
rng_ev     = random.Random(SEED + OFF_EV)
rng_ea     = random.Random(SEED + OFF_EA)
rng_sample = random.Random(SEED + OFF_SAMPLE)
# ★ 2026-10-07 定案：**删除** `rng_cover`（原 R-23 Ⅱ 覆盖率软门 / 登记表 F-02）。
#   三路流 ⇒ 四路流。旧快照的 `rng_state` 里仍有 `cover` 键，`_STREAMS` 只取自己要的
#   三个 ⇒ 旧快照照样能读，不需要迁移。


def rng_dump(g):
    """`getstate()` → **紧凑字符串**，不是 list。

    625 个整数若按 list 存、快照又是 `indent=1`，就会各占一行 ⇒ 单个快照凭空多 2500 行、
    涨到 ~100 KB，人眼再也看不了。压成一行后单条流约 6 KB。
    """
    v, st, gn = g.getstate()
    return dict(version=v, state=','.join(map(str, st)), gauss_next=gn)


def rng_load(g, d):
    """兼容两种写法：紧凑字符串（现在）与 list（2026-10-07 当天早些时候落的 `t2状态_第2轮.json`）。"""
    st = d['state']
    if isinstance(st, str):
        st = tuple(int(x) for x in st.split(','))
    else:
        st = tuple(st)
    g.setstate((d['version'], st, d['gauss_next']))


_STREAMS = dict(ev=rng_ev, ea=rng_ea, sample=rng_sample)
_RNG_SRC, _RNG_TAG = None, '全新播种'
if args.rng_from:
    # 给轮号 → 用该轮的 **mock** 快照（随机流消耗量与真跑一致，见登记表 §3.7）；
    # 给路径 → 直接用。（轮号形式是为了躲开 PowerShell 传中文文件名的编码坑。）
    if args.rng_from.isdigit():
        _p = CFG.snap_path(int(args.rng_from), '.mock')
    else:
        _p = args.rng_from if os.path.isabs(args.rng_from) else os.path.join(BASE, args.rng_from)
    _RNG_SRC, _RNG_TAG = json.load(open(_p, encoding='utf-8')), os.path.basename(_p)
elif PREV is not None:
    _RNG_SRC, _RNG_TAG = PREV, os.path.basename(CFG.snap_path(R - 1, SUFFIX))
if _RNG_SRC is not None and _RNG_SRC.get('rng_state'):
    for _k, _g in _STREAMS.items():
        rng_load(_g, _RNG_SRC['rng_state'][_k])
    print('\n随机流：已从 %s 恢复（保证"从任意轮恢复" ≡ "连跑"）' % _RNG_TAG)
elif _RNG_SRC is not None:
    print('\n⚠️ %s 里**没有** `rng_state` —— 四路随机流只能从种子原点重新播种。'
          '\n   这会让本轮抽到的人与"连跑"结果不同。第 1→2 轮请用'
          '\n   `--rng-from 1`（依据：随机流消耗量与 LLM 返回无关）。' % _RNG_TAG)
else:
    print('\n随机流：%s（第 1 轮，基准口径）' % _RNG_TAG)


def hard_online(r):
    """R-22b 统一口径：在线 = 第 r 轮窗口 ∩ active_hours ≠ ∅。

    ★ R-35（2026-10-07 定案）：**监管去特例化** —— 原先这里写死了
      `role_type != '监管'`（监管不进在线 ⇒ 不进池 / 不占名额 / 不进效价均值分母）。
      仿真侧：「监管现在也在那个抽样人选里面，那么抽到谁谁就可以做这个事情。」
      ⇒ 监管**完全等同普通角色**，谁被抽到谁发通报，不再有第二套选取规则。

    ★ 第 0 轮**唯一例外**：t0 快照里 `is_online_forced` 的人（t0 发帖人）也算在线 ——
      t0 那三条帖总得有人发得出来（`小红书_媒体_012` 的 active_hours 不含 22/23）。
      第 1 轮起**没有这个例外**。46 + 1 = 47（第 0 轮）／16（第 1 轮）。
      （监管 active_hours = [9,10,11,15,16,17] 与轮 0 窗口 22–24 不相交 ⇒ 不影响轮 0。）
    """
    s, _ = window_of(r)
    hrs = {(s.hour + k) % 24 for k in range(round_len(r))}
    ids = {a['agent_id'] for a in AGENTS
           if set(PROF[a['agent_id']]['active_hours']) & hrs}
    if r == 0:
        ids |= {a['agent_id'] for a in AGENTS if a.get('is_online_forced')}
    return ids


def decay_only(prev_states, dt_hours):
    """★ R-45：**轮初(r) = 衰减(轮末(r−1), L_{r−1})** —— 只衰减，不传染、不加噪声。

    2026-10-12：「我要每轮末和**轮初都记一遍**的原因是：记，记录员应该是记录**轮末**的数据，
    而**轮初**数据是为了每轮**抽样、做动作时作为基准**。」

    ⇒ 一轮的衰减归位**只做一次**，做在「进入这一轮的过渡」上，用的是**上一轮的长度**
      （`L_{r−1}` = 上一轮真实流逝的时间）。于是：
        · 轮初(r) = 衰减(轮末(r−1), L_{r−1})   ← 本轮**抽样 / 表2b / 名额偏离**的基准
        · 轮末(r) = 轮初(r) + [传染项 + 噪声]   ← 见 `refresh()`，记录员读这个
      ⇒ 每一轮的衰减总量仍是 `λ_hour ^ L_r`，**一次性、不重不漏**（与旧实现的衰减链逐位等价）。
      ⚠️ 旧实现的差别只在"衰减挂在哪一头"：旧版把衰减塞在**轮末** `refresh()` 里，
        且轮首直接复用上一轮轮末（未衰减）当基准 ⇒ **动作决策用的是未衰减值**。
        现在决策基准改成轮初（已衰减），这是本次改动**唯一会动数值**的地方。

    `DECAY_ANCHOR` / `DECAY_V` / `DECAY_A` 与 `refresh()` 同源（同一份常量，防两处口径漂移）。
    """
    out = {}
    for a in AGENTS:
        aid = a['agent_id']
        prof = PROF[aid]
        role = a['role_type']
        if DECAY_ANCHOR == 'base_0':
            base_v, base_a = prof['valence_base_0'], prof['arousal_base_0']
        else:
            base_v, base_a = prof['valence_baseline'], prof['arousal_baseline']
        p = prev_states[aid]
        out[aid] = dict(
            valence=round(clamp(base_v + (p['valence'] - base_v) * (DECAY_V[role] ** dt_hours),
                                -1.0, 1.0), 6),
            arousal=round(clamp(base_a + (p['arousal'] - base_a) * (DECAY_A[role] ** dt_hours),
                                0.0, 1.0), 6))
    return out


def refresh(prev_states, dt_hours, online_ids, p_map=None):
    """R-22 情绪刷新 —— **每轮只调用一次，在轮末**（R-22b）。

    二项目（★ R-45 起**衰减已移出**，见 `decay_only()`）：
      ①b 传染项（R-42 起 = 渲染式相加）②噪声
      ⇒ **轮末 = 轮初 + 传染项 + 噪声**（逐字兑现 2026-10-12 的规则）

    ⚠️ `dt_hours` **已不再是衰减时长**（衰减搬到轮初了）。参数保留是为了：
      · 旧调用点 / 旧快照不必改签名；
      · 审计打印仍报得出"这一轮多长"。
      传进来的值**不参与任何计算**。

    online_ids  本轮在线集合（硬门口径，由 hard_online(r) 给定）
    p_map       可选 {aid: {pid: {..., 'P': w}}}；缺省退回 prev_states[aid]['P']。
                轮末刷新**必须传**它 —— 池里已多了本轮自产的引用帖。
                参数只打印、不参与计算。

    ★ R-42 / R-45：返回的 dict 除 `valence/arousal/online` 外，**另存四项审计字段**
      （`v_decay` / `v_bar` / `d_contagion` / 及唤醒的三个对应项）。
      `v_decay` 现在 = **本轮轮初值**（= `decay_only()` 的输出，也是快照里的 `valence_start`）——
      衰减已经发生过了，这里只是把它记下来，使恒等式
        **轮末 = `v_decay` + `d_contagion` + 噪声**
      仍能逐人逐项验算。**纯增字段，不改任何数值。**
    """
    out = {}
    for a in AGENTS:
        aid = a['agent_id']
        prof = PROF[aid]
        prev = prev_states[aid]
        role = a['role_type']
        online = aid in online_ids
        # ★ R-45：轮初值直接取用（衰减已在 `decay_only()` 里做完）
        v = prev['valence']
        ar = prev['arousal']
        v_de, a_de = v, ar                     # R-42 审计：衰减后的中间值（= 轮初）
        v_bar = a_bar = None
        d_v = d_a = 0.0
        if online:
            # 看到的帖（R-28a 渠道门）：取 **贴子承载的情绪**（= 发帖人发帖那一刻的冻结值）
            pm = (p_map or {}).get(aid) or prev.get('P') or {}
            pairs = []
            for pid, d in pm.items():
                if pid not in POSTS:
                    continue
                if channel_reach(prof['primary_channel'], POSTS[pid]['channel']) <= 0:
                    continue
                pairs.append((d['P'], POSTS[pid]['author_valence_at_post'],
                              POSTS[pid].get('author_arousal_at_post')))
            tot = sum(w for w, _, _ in pairs)
            if tot > 0:
                sense = (float(prev['es']) if prev.get('es') is not None
                         else topic_val(prof['emotion_sensitivity'], aid))
                v_bar = sum(w * vm for w, vm, _ in pairs) / tot
                _aw = [(w, am) for w, _, am in pairs if am is not None]
                a_bar = (sum(w * am for w, am in _aw) / sum(w for w, _ in _aw)) if _aw else None
                # ★ R-42 V3：**渲染式相加**（方向随对方正负、强度随对方强弱、留余地）
                d_v = CONTAGION_GAIN * sense * v_bar * (1.0 - abs(v))
                v += d_v
                if AROUSAL_MODE == 'gap':
                    d_a = CONTAGION_GAIN_A * sense * ((a_bar - ar) if a_bar is not None else 0.0)
                elif AROUSAL_MODE == 'mirror':
                    d_a = (CONTAGION_GAIN_A * sense * a_bar * (1.0 - ar)
                           if a_bar is not None else 0.0)
                else:                                   # 'legacy'：逐位复现 R-42 之前的唤醒
                    d_a = CONTAGION_GAIN_A * sense * (v_bar - v)
                ar += d_a
            v += rng_ev.gauss(0, EPS_SIGMA)
            ar += rng_ea.gauss(0, EPS_SIGMA)
        out[aid] = dict(valence=round(clamp(v, -1.0, 1.0), 6),
                        arousal=round(clamp(ar, 0.0, 1.0), 6),
                        online=online,
                        # ★ R-42 审计三件套（**不改数值**）：轮末值 = v_decay + d_contagion + 噪声
                        v_decay=round(v_de, 6), v_bar=(round(v_bar, 6) if v_bar is not None else None),
                        d_contagion=round(d_v, 6),
                        a_decay=round(a_de, 6), a_bar=(round(a_bar, 6) if a_bar is not None else None),
                        d_arousal_contagion=round(d_a, 6))
    return out


if R == 1:
    ONLINE_0 = hard_online(0)
    t0_states = {a['agent_id']: dict(valence=a['valence'], arousal=a['arousal'],
                                     P=a['P'], es=a.get('es'))
                 for a in AGENTS}
    # ★ 第 0 轮**轮末** = 轮初(0)（= t0 初始值）+ 传染 + 噪声 —— 轮初(0) 由 t0 快照给定
    END0 = refresh(t0_states, round_len(0), ONLINE_0)
    print('① 情绪刷新（第 0 轮轮末）  DECAY_ANCHOR=%s  CONTAGION_GAIN=%s  Δt=%dh  在线 %d 人'
          % (DECAY_ANCHOR, CONTAGION_GAIN, round_len(0), len(ONLINE_0)))
    online_ids = [aid for aid, s in END0.items() if s['online']]  # 第 0 轮的在线者（含 forced）
    # ★ R-45：**轮初(1) = 衰减(轮末(0), L_0)** —— 第 1 轮的抽样 / 表2b / 名额偏离都用它
    cur = decay_only(END0, round_len(0))
    print('   ★ R-45 轮初(1) = 衰减(轮末(0), %dh)：效价 %+.4f → %+.4f'
          % (round_len(0),
             sum(END0[i]['valence'] for i in online_ids) / len(online_ids),
             sum(cur[i]['valence'] for i in online_ids) / len(online_ids)))
else:
    # ── R≥2：★ R-45 —— **轮初(r) = 衰减(轮末(r−1), L_{r−1})**，不再直接复用上一轮轮末 ──
    #   （旧写法 `轮初 ≡ 轮末(r−1)` 是"扫一遍就接着算"，衰减全挂在轮末 ⇒ 决策用未衰减值）
    online_ids = sorted(hard_online(R - 1))       # 上一轮的在线者 = 本轮偏离的分母口径
    cur = decay_only(PS, round_len(R - 1))
    print('① ★ R-45 轮初(%d) = 衰减(轮末(%d), %dh)：在线者效价 %+.4f → %+.4f'
          % (R, R - 1, round_len(R - 1),
             sum(PS[i]['valence'] for i in online_ids) / len(online_ids),
             sum(cur[i]['valence'] for i in online_ids) / len(online_ids)))

# R-17 情绪偏离（口径 = **本轮名额要用的那一份**）
#   ★ R-45（2026-10-12）后：这一份 = **轮初(r)**（已衰减），不再是上一轮轮末的未衰减值。
#   分母仍是「上一轮的在线者」（口径丙 / hard_online(R−1)），这一条没动。
cur_v = sum(cur[i]['valence'] for i in online_ids) / len(online_ids)
cur_a = sum(cur[i]['arousal'] for i in online_ids) / len(online_ids)
base_v = sum(PROF[i]['valence_baseline'] for i in online_ids) / len(online_ids)
base_a = sum(PROF[i]['arousal_baseline'] for i in online_ids) / len(online_ids)
deviation = 0.6667 * (base_v - cur_v) + 0.3333 * (cur_a - base_a)
print('\n① 情绪偏离（**轮初(%d)** 口径，分母 = 上一轮在线者 %d 人）' % (R, len(online_ids)))
print('   轮初效价 %+.4f  画像基线 %+.4f  偏离 %+.4f  (w=0.6667 → %+.4f)'
      % (cur_v, base_v, base_v - cur_v, 0.6667 * (base_v - cur_v)))
print('   轮初唤醒 %+.4f  画像基线 %+.4f  偏离 %+.4f  (w=0.3333 → %+.4f)'
      % (cur_a, base_a, cur_a - base_a, 0.3333 * (cur_a - base_a)))
print('   → 情绪偏离_%d（第 %d 轮名额的输入） = %+.6f' % (R - 1, R, deviation))

# ══════════════════════════════════════════════════════════════
# ★ 2026-10-07 R-34：表2c 的两根轴（供 媒体 / 监管 用）
#   轴① 群体效价档 = band(本轮在线者效价均值, V_BANDS) —— 口径**丙**
#        ⚠️ `cur_v` 就是"本轮在线者均值"，上面刚算完，**不另起一份**（否则系统里会有两套"群体情绪"）。
#        口径丙的保险门：在线 < GROUP_V_MIN 人 ⇒ 沿用上一轮存下来的效价档。
#   轴② 传播范围档 = band(K*, SPREAD_BANDS)，K* = 第 r−1 轮轮末帖子池的去重平台数（结构量）。
# ══════════════════════════════════════════════════════════════
def spread_of(post_iter):
    """K* = 帖子池的去重平台数（`channel` ∪ `channels`）。结构量 ⇒ 与 LLM 输出无关，可跨真跑回归。"""
    chs = set()
    for p in post_iter:
        chs.add(p['channel'])
        chs.update(p['channels'])
    return len(chs)


def spread_band(k):
    for nm, lo, hi in SPREAD_BANDS:
        if lo <= k <= hi:
            return nm
    return '全网'


SPREAD_K = spread_of(POSTS.values())          # 第 r−1 轮轮末的帖子池
SP_BAND = spread_band(SPREAD_K)
GV_BAND_RAW = band(cur_v, V_BANDS)
GV_BAND = GV_BAND_RAW
GV_CARRIED = False
if len(online_ids) < GROUP_V_MIN:
    _prev_band = (PREV or {}).get('params', {}).get('group_v_band')
    if _prev_band is not None:
        GV_BAND, GV_CARRIED = _prev_band, True
print('   表2c 两根轴：群体效价 %+.4f → 【%s】%s   传播范围 K* = %d → 【%s】'
      % (cur_v, GV_BAND, '（沿用上一轮，在线 %d < %d）' % (len(online_ids), GROUP_V_MIN)
         if GV_CARRIED else '（本轮实算，在线 %d 人）' % len(online_ids),
         SPREAD_K, SP_BAND))
print('   → 媒体倾向 = 表2c 阈值A[%s][%s]   监管 = 阈值B[%s][%s]（只有「强负×全网」给通报）'
      % (GV_BAND, SP_BAND, GV_BAND, SP_BAND))
# 监管 prompt 里对"面上情绪"的措辞（**用档、不用数字** —— 口径丙沿用档时数字会自相矛盾）
GV_WORD = {'强负': '非常负面', '负': '明显偏负面', '中': '大致中立',
           '正': '偏正面', '强正': '非常正面'}[GV_BAND]
if PREV is not None:
    _stored = PREV['params'].get('deviation_round_end')
    _ok = _stored is not None and abs(_stored - deviation) < 1e-6
    print('   交叉校验：上轮快照记的 deviation_round_end = %s  %s'
          % (_stored, '✓ 一致' if _ok else '✗ 不一致 —— 先查口径再往下走'))

# ── 附：第 0 轮**轮末**状态落盘 ────────────────────────────────
# 记录员 A 的第 0 轮行必须记**轮末**（R-22b）。t0状态_第0轮.json 里存的是**轮首**值
# （= t0 初始），拿它出第 0 轮行会得到旧的 −0.2502。故这里单独落一份。
# ★ R-45（2026-10-12）：落的是 `END0`（**轮末** = 轮初 + 传染 + 噪声），
#   不再是 `cur`（`cur` 现在是**轮初(1)**，比轮末又衰减了一格）。
_t0w = window_of(0)
if R == 1 and args.dump_prompt is None:   # ★ --dump-prompt 是**只读预览**，不落盘
    # ★ 2026-10-07 修：本行原先**漏了 `SUFFIX`** ⇒ `--mock --round 1` 会把真跑的
    #   `t0轮末状态.json` 覆盖掉（内容恒等所以没出事，但这是运气不是设计 ——
    #   同 §"mock 产物隔离" 那条老教训）。mock 链现在写 `t0轮末状态.mock.json`。
    _t0e = CFG.t0_end_path(SUFFIX)
    json.dump(dict(
        spec='%s 第 0 轮 **轮末**状态 —— 由 t1_runner 产出（R-22b：轮 1 轮首 ≡ 轮 0 轮末）' % EVENT_ID,
        event_id=EVENT_ID, round=0,
        window=[fmt_t(_t0w[0]), fmt_t(_t0w[1])],
        online=sorted(ONLINE_0),
        online_cnt=len(ONLINE_0),
        agents={aid: dict(valence=s['valence'], arousal=s['arousal']) for aid, s in END0.items()},
        posts=[dict(post_id=p['post_id'], channel=p['channel'], channels=sorted(p['channels']),
                    like_cnt_sim=p['like_cnt_sim'], comment_cnt_sim=p['comment_cnt_sim'],
                    repost_cnt_sim=p['repost_cnt_sim']) for p in POSTS.values()],
    ), open(_t0e, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('   （第 0 轮轮末状态已落盘 → %s，供记录员 A 第 0 轮行使用）' % os.path.basename(_t0e))

# ══════════════════════════════════════════════════════════════
# 4. ② 名额（R-02 / R-03 / R-17）
# ══════════════════════════════════════════════════════════════
G, gbar, hrs = G_of(R)
quota_raw = NAME_QUOTA_BASE * G * (1 + GAIN * deviation)
quota = int(math.ceil(clamp(quota_raw, NAME_QUOTA_LO, NAME_QUOTA_HI)))
print('\n② 名额   G(%d) = 1+(%.3f−1)×%.1f = %.4f   窗口整点 %s' % (R, gbar, w_peak(R), G, hrs))
print('   名额 = clamp(⌈%d×%.4f×(1+%.1f×%+.4f)⌉, %d, %d) = clamp(%.3f, %d, %d) = %d'
      % (NAME_QUOTA_BASE, G, GAIN, deviation, NAME_QUOTA_LO, NAME_QUOTA_HI,
         quota_raw, NAME_QUOTA_LO, NAME_QUOTA_HI, quota))

# ══════════════════════════════════════════════════════════════
# 5. ③ 抽样（R-23 三步）
# ══════════════════════════════════════════════════════════════
# ★ 随机流（rng_sample 等）已在 §3 顶部建好并恢复，此处**不得**重新播种。
hrs_set = set(h % 24 for h in hrs)

# ★ R-35（2026-10-07）：监管**不再被剔出池** —— 原来这里 `if role_type == '监管': regs.append(...); continue`
#   把监管整批挪走，现在一视同仁过硬门。
pool_hard, cut_offline = [], 0
for a in AGENTS:
    prof = PROF[a['agent_id']]
    if not (set(prof['active_hours']) & hrs_set):
        cut_offline += 1
        continue
    pool_hard.append(a['agent_id'])

# ★ 2026-10-07 定案：**删除 R-23 Ⅱ 覆盖率软门**（原文见正式文档 §5.3 Ⅱ / 旧 F-02）。
#   仿真侧：「我本来想的是**没有覆盖率这个问题**，在直接用 `active_hours` 选完之后
#   就开始随机抽了。」
#   实测理由（只读画像库，2h 窗口、非监管）：
#     · `active_hours` 是**整点粒度** ⇒ coverage 只有 {0, 0.5, 1.0} 三档，而 **0 已被硬门剔除**
#       ⇒ 只剩 0.5 / 1.0 两档，**77–89% 的人是纯抛硬币**（轮 4：55 人里仅 6 人 = 1.0）。
#     · 那 6 人**全是微博** ⇒ 长时段渠道被系统性优待，抖音/小红书被随机压掉
#       （轮 4 抖音在线 8 人只过 1 人，整轮缺席）。
#   ⇒ 名义上的"覆盖率门"实际是与内容无关的一层噪声。硬门已决定"这个人此刻醒着"，
#     窗口内醒 1 小时还是 2 小时不再影响他"能不能开口"。
pool_cover = pool_hard

# ══════════════════════════════════════════════════════════════
# 6. 视野（R-08 四因子 / R-28a 渠道可达 / R-09 Top-5）
# ══════════════════════════════════════════════════════════════
def recompute_P(aid, t_i, pool):
    """重算 P：兴趣匹配/传播范围优先用 t0 快照，渠道可达与新鲜度按本轮算。

    ★ R-27a / P-17（topic 旁表）🔴 未定 ⇒ 新帖（本轮自产）没有 t0 的 P 条目，按此补：
      兴趣匹配 = 沿 `quoted_id` 上行找到的那条帖的兴趣匹配（同一话题，代理值）；
                 找不到则退 ε = 0.1（R-08 ① 兜底）。
      传播范围 = 发帖人的 `influence_weight`（R-08 ② 精确值，不是热度）。
      ⚠️ 这是占位，P-17 落地后要换成真值。
    """
    a = AG0[aid]
    prof = PROF[aid]
    out = {}
    for pid in pool:
        p = POSTS[pid]
        d0 = (a.get('P') or {}).get(pid)
        if d0 is None:
            if p.get('topics'):
                interest = interest_match(prof['interest_topics'], p['topics'])
            else:
                interest = None
                qid = p.get('quoted_id')
                walked = set()
                while qid and qid not in walked:
                    walked.add(qid)
                    src = a['P'].get(qid)
                    if src:
                        interest = src['兴趣匹配']
                        break
                    qid = POSTS.get(qid, {}).get('quoted_id')
                if interest is None:
                    interest = EPSILON
            spread = PROF[p['poster_agent_id']]['influence_weight']
        else:
            interest, spread = d0['兴趣匹配'], d0['传播范围']
        reach = channel_reach(prof['primary_channel'], p['channel'])
        if reach <= 0:
            continue
        dt_h = max(0.0, (t_i - p['t_post']).total_seconds() / 3600.0)
        decay = max(math.log((271.8 - dt_h) / 100.0), 0.05)
        heat = 1.0 + K_HEAT * math.log(1 + p['interactions'])
        fresh = decay * heat
        P = interest * spread * reach * fresh
        # ★ R-36（2026-10-07 定案）：**热度时间衰减，只作用于视野排序**。
        #   排序分 P_view = P × exp(−LAMBDA_HEAT × Δt_h)；`P` 本身**一字不改**
        #   ⇒ ① 进 `refresh()` 的情绪传染权重 Σ_m 用的仍是 P（call site 见下方 refresh）
        #      ② 快照里的 'P' 仍是 P（老基准数在"传染侧"完全可比）
        #   另存 P_view / 衰减_热度 两个字段，否则快照解释不了 Top-5 是按什么排的。
        #   ⚠️ 复用上面刚算的 dt_h，**不重算一遍**（防两处口径漂移）。
        decay_heat = math.exp(-LAMBDA_HEAT * dt_h)
        out[pid] = dict(兴趣匹配=interest, 传播范围=spread,
                        渠道可达=reach, 新鲜度=round(fresh, 4),
                        P=round(P, 6),
                        衰减_热度=round(decay_heat, 6),
                        P_view=round(P * decay_heat, 6))
    return out

POOL_POSTS = list(POSTS.keys())
vision, P_new, cut_novision = {}, {}, 0
cut_reg_vision = 0          # 监管人数（不看帖子 ⇒ 视野为空，见下）
for aid in pool_cover:
    # ★ R-35（2026-10-07）：监管**本来就不看帖子**（`build_user` 走 `【你掌握的情况】`，
    #   输入是面上两根轴）⇒ 直接给它空视野，不进 Top-5 计算。
    #   ⚠️ 这条**不是**"豁免视野门"，而是"监管没有视野这个量" ——
    #      视野门本身已在 ★ R-46（2026-10-12）被**整条删除**，见下。
    if PROF[aid]['role_type'] == '监管':
        vision[aid] = []
        P_new[aid] = {}
        cut_reg_vision += 1
        continue
    P = recompute_P(aid, W_START, POOL_POSTS)
    P_new[aid] = P
    # ★ R-46（2026-10-12 定案）：**删除视野门**。
    #   原逻辑：`if not P: cut_novision += 1; continue` —— Top-5 为空就不许上场。
    #   删它的三条依据：
    #     ① **实测是空操作**：36 轮**累计剔除 0 人**（理由见登记表 §5.15 —— R-08 ① 的
    #        `ε = 0.1` 兜底保证 `P > 0`，只要池非空，人人都有 ≥1 条帖可看）。
    #     ② **它是个陷阱**："池为空 ⇒ 人人被剔 ⇒ 无人上场 ⇒ 池永远为空" —— 开跑前后
    #        靠的是"t0 注入 3 条帖"这个巧合，不是设计。真要保底该写"池空时全体放行"。
    #     ③ 2026-10-12：视野门与唤醒界值、衰减一起改掉（"连同上面的视野门删除
    #        什么的我们确认的东西一起改"）。
    #   ⇒ 现在：`pool_cover` 的人**一律进池**，视野 = 该渠道可达域内按 `P_view` 排的 Top-5
    #      （池空则是空视野，仍上场 —— 与"软门已删"后的两步抽样口径一致：
    #       **现行抽样 = 硬门（active_hours）→ 等权 random.sample**）。
    #   ⚠️ `cut_novision` 变量保留（恒 0）只为对账打印不断行；`P` 为空时 `sorted()` 得空
    #      列表 ⇒ `vision[aid] = []`，`build_user` 渲染成"【你看到的帖子】（共 0 条）"。
    # ★ R-36（2026-10-07）：排序键 = **P_view**（含热度时间衰减），不再是 P。
    #   `P` 仍在，只是不再决定"谁排前面"。
    order = sorted(P.items(), key=lambda kv: (-kv[1]['P_view'], kv[0]))[:N_VIEW]
    # ★ R-27a ②③ 视野绑定：看到引用帖 q ⇒ **必须同时看到** q.quoted_id 那条原帖。
    #   这一对是原子单位 —— 不论渠道、不论 P 排名；被引原帖**不占 N_view 名额**、去重。
    #   ⚠️ 2026-10-07 修：原来写的是 `for pid in sel:`（边遍历边 append），
    #      那会沿 quoted_id **全链展开**，违反 R-27a ③「只展开一层，不递归」
    #      （定案原话：「**他每一次只能看到两个帖子**，不会说也能一直延续地看到引用的帖子的引用帖」；
    #        §3.8 的验证判据 ③ 就是「视野里从不出现'引用的引用'，深度恒为 1」）。
    #      ⇒ 只遍历 **Top-5 原本选出的那几条**，补进来的不再往下展开。
    order_pids = [pid for pid, _ in order]
    sel = list(order_pids)
    for pid in order_pids:
        qid = POSTS[pid].get('quoted_id')
        if qid and qid in POSTS and qid not in sel:
            sel.append(qid)
    vision[aid] = sel

pool_final = sorted(vision.keys())
print('\n③ 抽样')
print('   硬门（active_hours ∩ 窗口 ≠ ∅）：%d 人过，剔除 %d 人' % (len(pool_hard), cut_offline))
print('   覆盖率软门：**已删除**（2026-10-07 定案）')
print('   ⇒ 现行抽样 = **硬门（active_hours ∩ 窗口）→ 等权 `random.sample`**，中间不再有门')
print('   视野门：★ **已删除**（R-46，2026-10-12）⇒ 硬门后的人一律进池，剔除 %d 人'
      % cut_novision)
if cut_reg_vision:
    print('      └ 其中监管 %d 人**视野为空**（不看帖子，输入是面上两根轴；R-35/R-46）'
          % cut_reg_vision)
print('   池内渠道构成：', dict(Counter(PROF[i]['primary_channel'] for i in pool_final)))
quota_eff = quota if args.quota is None else args.quota
picked = rng_sample.sample(pool_final, min(quota_eff, len(pool_final)))
print('   等权随机抽出 %d 人 / 名额 %d%s' % (
    len(picked), quota_eff, '（--quota 覆盖）' if args.quota is not None else ''))
for i in picked:
    print('      %-22s %s×%s  视野 %s' % (i, PROF[i]['role_type'], PROF[i]['primary_channel'], vision[i]))

# ★ R-35（2026-10-07 定案）：**监管去特例化 —— 删掉整条 R-19 例外通道**。
#   仿真侧：「监管现在也在那个抽样人选里面，那么抽到谁谁就可以做这个事情。」
#   ⇒ 监管已进 `hard_online()` / 候选池，**谁被抽到谁上**；阈值 B 只决定抽到的那位
#     的【本轮倾向】是「发布通报」还是「沉默」，不再另开第二套选取规则。
#   ⇒ `ACTING = picked`（不再 `picked + regs_act`）。
#   ⚠️ 连带改动：监管**计入**在线 ⇒ 进群体效价均值的分母 ⇒ 轮 5 起基准数会变（见登记表 §3.4d）。
ACTING = picked
regs_act = [i for i in picked if PROF[i]['role_type'] == '监管']   # 仅记录：本轮抽中的监管

# ══════════════════════════════════════════════════════════════
# 7. ④ 档位（R-24）
# ══════════════════════════════════════════════════════════════
def gear_of(aid):
    role, ch = PROF[aid]['role_type'], PROF[aid]['primary_channel']
    t1 = TABLE1[(role, ch)]
    if t1['delta'] is None:
        return None, t1, (None, None)
    v = cur[aid]['valence']
    ar = cur[aid]['arousal']
    vb, ab = band(v, V_BANDS), a_band(role, ar)      # ★ R-44：唤醒格按角色查界值
    # ★ 2026-10-19：表2 已删除 ⇒ 不再算"单个档位"；返回 None 只为保持三元签名。
    return None, t1, (vb, ab)


def rec_set(aid):
    """★ 2026-10-07 新增（R-24 表2b）：本轮 `【本轮倾向】` 的**推荐集合**（有序 list of 动作名）。

    四步（顺序很重要）：
      ① 情绪给的集合   = TABLE2B[(效价档, 唤醒格)]
      ② **δ 平移（乙）**：每项档位 +δ，clamp 到 1..4；
         ⚠️ **落点不在本身份的动作集里 ⇒ 保留原位**（黑猫没有「转发」档 ⇒ 评论被顶到转发会
            两头落空、评论凭空消失）。**乙 只在"落点不可用"时生效 ⇒ 15 格里只有黑猫那格会变**，
            其余 14 格与"直接平移"逐位相同（媒体 δ=+2 也不会膨胀）。
      ③ ∩ 表1 的动作集 ⇒ 身份不给的动作，推了也不推（顺手治掉"倾向说引用、选项里没引用"，
         以及监管格 `GEAR_CN[None]` 的 KeyError —— 监管 δ=None ⇒ 不平移）
      ④ 低唤醒列补「沉默」（中/高列不给）—— 沉默永远合法，这里定的只是"推不推"
      ⑤ ★ R-33：记号 5（投诉）**跳过 ② 的 δ 平移**，由表2b 直接控制；仍要过 ③ 身份过滤
      为空 ⇒ 退化 {沉默}
    """
    role, ch = PROF[aid]['role_type'], PROF[aid]['primary_channel']
    t1 = TABLE1[(role, ch)]
    acts = set(t1['act'])

    # ★ 2026-10-07 R-34：**机构走表2c，不走表2b**（表2b 从此只管个体角色）。
    #   表2c 直接给档位集 ⇒ **δ 平移整段跳过**（媒体的 δ 已退休为 None）。
    #   仍要过两道：③ 身份过滤（∩ 表1 动作集）、④ 补沉默。
    #   ⚠️ 表2c 已经**逐格写死**了要不要沉默（低档列才有），所以这里**不再按唤醒列补** ——
    #      否则"中 × 全网 = 评论 · 转发"会被硬塞一个沉默进去，与表不符。
    if role in INST_ROLES:
        tbl = TABLE2C_B if role == '监管' else TABLE2C_A
        out = [a for a in tbl.get((GV_BAND, SP_BAND), ['DO_NOTHING']) if a in acts]
        return out or ['DO_NOTHING']

    delta = t1['delta'] if t1['delta'] is not None else 0     # 监管：不平移
    v, ar = cur[aid]['valence'], cur[aid]['arousal']
    vb, ab = band(v, V_BANDS), a_band(role, ar)               # ★ R-44：唤醒格按角色查界值

    gears, out = TABLE2B[(vb, ab)], []
    for gg in gears:                                          # ② δ 平移，落空则保留原位
        if gg == COMPLAINT_GEAR:
            # ★ R-33：「投诉」**不参与 δ 平移**（它没有档位可言，δ 不是它的开关）。
            #   开关 = 表2b 的这两格 + 身份过滤（`COMPLAINT` 只进客户的动作集）。
            # ★ 2026-10-20：类型闸 —— 本场不许投诉时，这一格直接作废。
            if 'COMPLAINT' in acts and complaint_allowed(aid) and 'COMPLAINT' not in out:
                out.append('COMPLAINT')
            continue
        tgt = int(clamp(gg + delta, 1, 4))
        act = GEAR[tgt] if GEAR[tgt] in acts else GEAR[gg]
        if act in acts and act not in out:                    # ③ 身份过滤（原位也可能不被允许）
            out.append(act)
    if ab == '低' and 'DO_NOTHING' in acts:                   # ④ 低唤醒列补沉默
        out.append('DO_NOTHING')
    if not out:
        out = ['DO_NOTHING']
    return out


def rec_set_cn(aid):
    """推荐集合的中文渲染（`【本轮倾向】` 一行）。多个用「 或 」连。

    ★ R-47 ③：与【你可以做什么】的候选栏**同一套词** —— 除监管的「发布通报」
      （那真的不是"引用"，是不指向任何帖子的通报，R-34）外，引用一律渲染成通用名
      「引用」，**不再用 `t1['word']`**（客户"讲述遭遇" / 媒体"发稿"），
      这是"引用去角色个性化"的落地。
    """
    role = PROF[aid]['role_type']
    return ' 或 '.join(action_name(aid, a) for a in rec_set(aid))

# ══════════════════════════════════════════════════════════════
# 8. ⑤ prompt 组装（R-29 / R-31，prompt模板_v1.md）
# ══════════════════════════════════════════════════════════════
SYSTEM = """你要扮演一个社交平台上的账号，在下面给你的身份设定下，对本轮看到的内容做出一个反应。

你要做的事情只有一件：**用你的身份，做出最像"你这个人"会做出的那个反应。**

【三条红线】
1. 你只能对【下面明确列出、你本轮看到的内容】做出反应。不得引用、暗示、或凭空编造
   任何没给你的信息。
2. 你只知道下面给你的信息 —— 没给你的，就是你不知道的。不要替自己脑补别人的反应，
   也不要假设"大家"怎么想。
3. 你是在扮演一个真人，不是在写分析报告。用日常说话的方式，不要用书面语、不要用
   术语、不要总结归纳、不要"综上所述"。

【事件背景】
__EVENT_BRIEF__
目前你只能确认这件事本身，其余情况你并不了解。

【动作名（英文枚举，填 JSON 时用）】
  "QUOTE_POST"      引用：把一条帖子引过来，同时写上你自己的话
  "REPOST"          转发：把一条帖子原样搬过来，一个字都不加
  "CREATE_COMMENT"  评论：在某条帖子下面留言
  "LIKE_POST"       点赞：默默点个赞，不留下任何文字
  "DO_NOTHING"      沉默：什么都不做（这也是完全正常、完全合理的选择）
  "COMPLAINT"       投诉：把这件事**拿到【黑猫投诉】去正式投诉**
  ⚠️ 本轮你**具体能选哪些，以【你可以做什么】为准**；没列出来的一律不选。

【输出格式】
只输出一个 JSON 对象，前后不要有任何其他文字、不要用代码块包裹：

{
  "action": "上面那几个英文动作名之一",
  "target_post_id": "你要反应的帖子的编号；除 DO_NOTHING 外必填",
  "content": "你要发出来的文字；只有 QUOTE_POST、COMPLAINT 和 CREATE_COMMENT 需要，≤80 字",
  "valence": -1.0 到 1.0 之间的一个数，负数代表情绪负面，正数代表正面；只有 QUOTE_POST、COMPLAINT 和 CREATE_COMMENT 需要,
  "arousal": 0.0 到 1.0 之间的一个数，越高代表情绪越激动；只有 QUOTE_POST、COMPLAINT 和 CREATE_COMMENT 需要,
  "topic": "这段内容围绕的话题，一个短语；只有 QUOTE_POST、COMPLAINT 和 CREATE_COMMENT 需要"
}"""

# ★ 事件背景只在提示词里出现这一处；占位符在 SYSTEM 定完之后**立刻**换掉 ——
#   必须在这里换：快照里存的 `system_prompt=SYSTEM` 也要是换好的正文（否则落盘带占位符）。
SYSTEM = SYSTEM.replace('__EVENT_BRIEF__', EVENT_BRIEF)

ACT_CN = {'QUOTE_POST': '引用', 'REPOST': '转发', 'CREATE_COMMENT': '评论',
          'LIKE_POST': '点赞', 'DO_NOTHING': '沉默', 'COMPLAINT': '投诉'}

# ══════════════════════════════════════════════════════════════
# ★ R-48（2026-10-12 定案）：**动作名不再分裂** —— 提示词、跨轮记忆段、快照
#   `action_cn` 三处共用**同一个取名字的函数**，不再各写一套。
# ══════════════════════════════════════════════════════════════
# 旧状态（三套名字并存）：
#   · 提示词候选栏 /【本轮倾向】：通用名（引用/转发/…）                    ← R-47 改的
#   · 跨轮记忆段：`t1['word']` 本人身份的词（客户"讲述遭遇" / 媒体"发稿"）  ← R-39 的写法
#   · 快照 `action_cn`：`t1['word'] if QUOTE_POST else ACT_CN[act]`        ← 落盘口径
# 2026-10-12：「**动作不要分裂**」⇒ 统一走下面这个函数。
#
# ⚠️ **唯一保留的差别不是分裂**：监管的 `QUOTE_POST` 叫「发布通报」—— 它**不指向任何帖子**
#    （是对面上情况出结论，R-34），提示词与快照**用的是同一个词**，只是与别人的「引用」不同名。
# ⚠️ `TABLE1` 的 `word` 字段（"讲述遭遇"/"发稿"）保留但**不再参与任何渲染**，
#    只作为历史字段随快照落盘（该字段本就已知不可靠，见「统计只用 action / action_cn」）。
def action_name(aid, act):
    """动作的**唯一显示名**（提示词候选栏 / 跨轮记忆段 / 快照 `action_cn` 共用）。"""
    if act == 'QUOTE_POST' and PROF[aid]['role_type'] == '监管':
        return '发布通报'
    return ACT_CN.get(act, act)

# ══════════════════════════════════════════════════════════════
# ★ R-47（2026-10-12 定案）：**身份 = 动作的上限，不是准入**
# ══════════════════════════════════════════════════════════════
# 定案原话（2026-10-12，两次纠错后的定稿）：
#   「LLM 能通过自己的**特定的角色画像**入手（人设以及事件的相关度），决定自己的动作，
#     让 LLM **多思考一下**」
#   「我说的是**网民你就是网民的视角干动作**，客户**不是消费者就不要投诉**，
#     KOL **不是那个领域的就不要过分行动** —— **没说一定是沉默**，你过头了」
#   「**那就选沉默**本身又是强制性的 prompt」
# ⇒ 逐条对上「动作阶梯 0 沉默 < 1 点赞 < 2 评论 < 3 转发 < 4 引用 < 5 投诉」：
#     网民 无限制 ｜ 客户 非消费者 ⇒ 上限 = 引用（投诉出局，其余全在）
#     KOL  领域不相关 ⇒ 不过分行动（可看、可随口说，但不摆"这领域我定调"的架势）
#     媒体 范围外 ⇒ 不以自己名义发稿 ｜ 监管 不够严重 ⇒ 不介入
#   ⇒ **每一句都只封顶，不封路**。判据原话照给 LLM，**不替它判**（仿真侧：
#      「我不需要你的建议判断，**本身把表述给 LLM 即可，让 LLM 做判断**」）。
#
# 为什么这是必要的（实测依据，见 §5.5）：
#   ① 客户的「亲身经历」在画像里**基本不存在** —— 23 个客户的 `persona_bio` 里只有 1 个（4%）
#      有购买/理赔证据，而旧 prompt 明写「写**你自己的具体遭遇**（时间 / 保单 / 结果）」
#      ⇒ 实测 154 条客户文字里 **127 条（82%）**声称自己买过/有理赔，且**编造具体数字、
#        跨轮自相矛盾**（"一年一百八" / "一年一百六" / "一个月十几块"）。这是凭空造消费者
#        投诉证据，**合规上不能进材料**。⇒ 判据改为「事件类型 + 兴趣相关度」，并把
#        「不许编金额/日期/天数/保单号」写死。
#   ② KOL / 媒体 的领域与本案的相关度是现成的（`interest_topics` 已注入 prompt），
#      但旧 prompt 没有任何一句话让模型去**用**它决定动作。
def _customer_identity_rule():
    """客户的身份段。★ 2026-10-20：投诉资格不再由 prompt 自由裁量，
    而是按**采集侧 8 类事件类型**（`COMPLAINT_POLICY`）生成 —— 与 `rec_set` 的
    硬闸同一口径。旧文本里「广告、政治这类…就不必投诉」是手写的自由文本，
    没有代码在管（EVT-001 营销类仍投诉了 1 起），已删除。"""
    head = '- 你是**客户**：你跟这件事的关系，决定你这一步能走到哪。\n'
    if COMPLAINT_POLICY == 'allow':
        body = ('- 本事件是【%s】，属于**消费纠纷**：只要这件事跟你自己的兴趣'
                '高度相关，你**就是**这件事的消费者，可以**作为消费者**去投诉。\n'
                % EVENT_TYPE)
    elif COMPLAINT_POLICY == 'scene' and PARTY_ENTITIES:
        body = ('- 本事件是【%s】：涉事主体明确，只有你**确实使用过**它的产品/服务时'
                '才可以投诉；否则**不许投诉**。\n' % EVENT_TYPE)
    else:
        body = ('- 本事件是【%s】，**不属于**消费纠纷（不涉及具体商品或服务的购买与使用）。'
                '**不许投诉** —— 推荐集和候选栏里都不会出现「投诉」，'
                '也不要为了投诉把自己说成受害者。\n'
                % (EVENT_TYPE or '未登记类型'))
    tail = ('- 说话时**不要编造具体的金额、日期、天数、保单号** —— '
            '你是在说你对这件事的看法，不是在举证。')
    return head + body + tail


IDENTITY_RULE = {
    '网民': (
        '- 你是**普通网民**：看到什么说什么，身份上没有任何额外限制。'),
    '客户': _customer_identity_rule(),
    'KOL': (
        '- 你是**KOL**：先看你自己的领域跟这件事**相不相关**。\n'
        '  相关，你有话要说、可以正常表态；不相关，就不必摆出"这个领域我说了算"的架势。'),
    '媒体': (
        '- 你是**媒体**：先看你自己的**体量**和**关注的话题**。\n'
        '  在你覆盖得到的范围里，按你的口径报；不在你的范围里，'
        '就不要**以自己的名义**发稿。'),
    '监管': (
        '- 你是**监管**：先判断事态**到底有多严重**。\n'
        '  够严重，就介入；不够严重，**不介入** —— 不介入本身也是一个结论。'),
}

# ★ R-47 ②：只加"想清楚再动手"的提示，**不加 reasoning 输出字段**
#   2026-10-12：「你让 LLM 做 reasoning 输出，会不会消耗太多时间和钱？
#     我觉得出发点是好的但是不值得 …… 那就**加一下想清楚再动手的提示就行，不要输出**。」
#   实测依据（成本侧）见 §5.5：输入侧 ≈1,039,000 tokens（96%）/ 输出侧 ≈44,000（4%），
#   加 100 字 reasoning × 653 人次只让总量 +3.6%、成本 ¥2.5 → ¥2.75 —— **钱不是理由**。
#   不做的真理由：① 不可复现且是 post-hoc rationalization ② 会往 `content` 里渗书面语腔
#   ③ 低自由度任务推理增益低 ④ 多一个必填自由文本字段 = 多一个 JSON 解析失败面。
THINK_RULE = (
    '【先想清楚，再动手】\n'
    '选之前，先在**心里**过一遍这两个问题（**不用写出来**，也不要出现在 `"content"` 里）：\n'
    '  1. 这件事跟我**这个身份**到底是什么关系？凭我这个身份，这一步我**能走到哪**？\n'
    '  2. 我这么做了之后，我这条动态放在别人眼里是什么样子？\n'
    '  想清楚了，再从下面选**一件**做。')

# ★ R-47 ③：动作说明补全 —— **每个动作都给一句说明**（旧版只有"投诉"/"引用"两行有，
#   其余四个光秃秃，模型看不出动作之间的力度差）。说明语沿用 SYSTEM 段那一套，逐字一致。
ACT_DESC = {
    'LIKE_POST':     '点赞 —— 默默点个赞，不留下任何文字',
    'CREATE_COMMENT': '评论 —— 在某条帖子下面留一句话',
    'REPOST':        '转发 —— 把一条帖子原样搬过来，一个字都不加',
    'DO_NOTHING':    '沉默 —— 什么都不做（这也是完全正常、完全合理的选择）',
}

WRITE_RULE = {
    '网民': '没有特别要求 —— 就像你平时在平台上说话那样。',
    # ★ R-47 ④：旧文案 =「写**你自己的具体遭遇**（时间 / 保单 / 结果）」—— 实测客户画像里
    #   只有 4% 有经历证据 ⇒ prompt 在**要一个画像里不存在的东西**，逼出 82% 的编造（见上）。
    #   改为"写看法"，并把虚构细节的禁令写死。
    '客户': '写你自己想说的话，不要转述新闻、不要泛泛而谈。'
            '**不要出现具体的金额、日期、天数、保单号** —— 上面没给你的细节，不要自己编。',
    'KOL':  '发表观点时要有你自己的判断角度，但不要写成正经文章。',
    '媒体': '**引述可信来源 + 事实陈述**，不要表达个人情绪。',
    '监管': '必须写**无情绪的处置结论**（约谈 / 立案 / 整改要求 / 风险提示），'
            '严禁出现"我也很生气""太离谱了"这类情绪表态。',
}

def render_interest(d):
    if isinstance(d, str):
        return d
    if isinstance(d, (list, tuple, set)):
        return '、'.join(str(x) for x in d)
    items = sorted(d.items(), key=lambda kv: -kv[1])
    return '、'.join('%s（关注度 %.2f）' % (k, v) for k, v in items)

# ★ R-40（2026-10-08 定案）：「监管也得看到内容」⇒ 舆情快报附送条数。
REPORT_N = 3


def render_post(pid):
    """帖子在 prompt 里的两行渲染（**视野段 / 快报段共用**，避免两处格式漂移）。"""
    p = POSTS[pid]
    dt_h = max(0.0, (W_START - p['t_post']).total_seconds() / 3600.0)
    when = '刚刚' if dt_h < 1 else '%d 小时前' % int(dt_h)
    return ['[%s] ｜ %s ｜ %s ｜ %s' % (pid, p['channel'], when, p['poster_name']),
            '    ' + p['content'].replace('\n', ' ')[:160]]


def build_user(aid):
    prof = PROF[aid]
    role, ch = prof['role_type'], prof['primary_channel']
    t1 = TABLE1[(role, ch)]
    s = cur[aid]
    L = []
    L.append('【你是谁】')
    L.append(prof['persona_bio'])
    L.append('你主要活动的地方是 **%s**，你在这里说话。' % ch)
    L.append('你平时最关心的话题：%s' % render_interest(prof['interest_topics']))
    sb = topic_val(prof['stance_bias'], aid)
    L.append('你对眼下这件事的态度：%s（%+.2f）'
             % ('偏负面' if sb < -0.2 else ('偏正面' if sb > 0.2 else '中立'), sb))
    L.append('')
    # ★ R-47 ①：**身份 = 动作上限**（不是准入，不强制沉默）—— 见 `IDENTITY_RULE` 的注释
    L.append('【你的身份意味着什么】')
    L.append(IDENTITY_RULE[role])
    L.append('')
    L.append('【你此刻的状态】')
    v, ar = s['valence'], s['arousal']
    vs = '非常负面' if v < -0.6 else '明显偏负面' if v < -0.2 else '略微负面' if v < 0 else '大致中立' if v < 0.2 else '偏正面'
    # ★ R-44：定性词改用**本人角色的唤醒界值**（不再钉死 0.4 / 0.7）——
    #   否则 KOL 唤醒 0.52 会渲染成"中等偏高"，而表2b 已按 KOL 的 0.50 判成「高」，
    #   同一份 prompt 里自相矛盾。
    as_ = {'低': '很低', '中': '中等偏高', '高': '很高'}[a_band(role, ar)]
    L.append('情绪底色：%s（效价 %.2f，-1 最负面，+1 最正面）' % (vs, v))
    L.append('情绪强度：%s（唤醒 %.2f，越高越激动）' % (as_, ar))
    L.append('')
    # ★ R-39（2026-10-08 定案）：**跨轮记忆段** —— 落地 R-31 闸门 1「只记我自己做过什么」。
    #   ⚠️ 抬头必须写死「**不是你本轮看到的帖子**」：否则模型会把记忆里的编号当成视野内容，
    #      直接违反 SYSTEM 红线 1（只能对【你看到的帖子】/【你掌握的情况】里的条目做反应）。
    #   首次出场（本人历史为空）⇒ **不出这一段**（不出空段，免得诱导模型说"我没说过话"）。
    _mem = MEMORY.get(aid, [])[-MEMORY_K:]
    if _mem:
        L.append('【你最近说过的话】（你自己的历史发言，**不是你本轮看到的帖子**）')
        for _rr, _act, _tgt, _txt in _mem:
            # ★ R-48（2026-10-12）：改用**全局唯一**的动作名 `action_name()`。
            #   旧写法用 `t1['word']`（客户"讲述遭遇" / 媒体"发稿"），与提示词候选栏的
            #   「引用」**同名不同词** —— 仿真侧：「动作不要分裂」。记忆段也是自己在说话，
            #   同一个动作在同一个 prompt 里必须只叫一个名字。
            _v = action_name(aid, _act)
            _line = '  第 %d 轮：%s' % (_rr, _v)
            if _tgt:
                _line += '（%s）' % _tgt        # 转发 / 点赞没有内容，目标编号是唯一信息
            if _txt:
                _line += ' —— 「%s」' % _txt
            L.append(_line)
        L.append('')
    # ★ 2026-10-07 R-34：监管**不看具体帖子**（R-19：不靠视野，直接读面上的全局数值）。
    #   它的输入只有两根轴：整体情绪有多负面 / 传得有多广。
    if role == '监管':
        L.append('【你掌握的情况】')
        L.append('（你是主管部门，**不靠刷帖** —— 你收到的是一份面上的**舆情快报**。）')
        L.append('眼下这件事在网络上的**整体情绪**：%s' % GV_WORD)
        L.append('话题的**扩散范围**：已蔓延到 %d 个平台（%s）' % (SPREAD_K, SP_BAND))
        L.append('')
        # ★ R-40（2026-10-08 定案，方案**乙**）：「一个监管也得看到内容才能做评判」
        #   ⇒ 快报附送**报送条目**：按**传播范围**（`channels` 可达域 → 被转发数 → 互动量）取 Top-N。
        #   ⚠️ **不能用视野权重 `P` 排**：监管 `primary_channel = 监管通报` 是出口型（R-28c 0 条入向边）
        #      ⇒ `渠道可达 ≡ 0` ⇒ `P ≡ 0`，根本排不出来（这也正是它有视野门豁免的原因）。
        #      排序键全是**结构量 + 仿真内部计数**，不含任何 LLM 文本 ⇒ 可跨跑回归。
        #   ✅ **只读**：不入池、不产生渠道边 ⇒ 监管通报**仍是出口型**，渠道图不动。
        _ranked = sorted(POSTS.values(),
                         key=lambda p: (-len(p['channels']), -p['repost_cnt_sim'],
                                        -p['interactions'], p['t_post'], p['post_id']))
        # ★ 来源渠道去重（2026-10-08 认可）：**每个来源渠道最多取 1 条**。
        #   不去重时实测第 21 轮报出来的 3 条**全是媒体自产帖**（与"媒体自我接龙成 10 节点长链"
        #   那个已知失真同源），信息量严重同质。去重后 3 条覆盖 3 个不同平台，才像跨平台快报。
        #   ⚠️ 池子里去重平台数 < REPORT_N 时就只报几条（如实，不凑数）。
        _rep, _seen_ch = [], set()
        for _p in _ranked:
            if _p['channel'] in _seen_ch:
                continue
            _seen_ch.add(_p['channel'])
            _rep.append(_p)
            if len(_rep) >= REPORT_N:
                break
        L.append('快报附送的头 %d 条（按**传播范围**排，不是"你刷到的"）：' % len(_rep))
        for p in _rep:
            L.extend(render_post(p['post_id']))
        L.append('')
    else:
        _vis = vision.get(aid, [])
        L.append('【你看到的帖子】（共 %d 条，按编号排）' % len(_vis))
        for pid in sorted(_vis):
            L.extend(render_post(pid))
        L.append('')
    # ★ R-47 ②：「想清楚再动手」的提示，**放在选项之前**（先定上限，再挑动作）
    L.append(THINK_RULE)
    L.append('')
    L.append('【你可以做什么】')
    L.append('本轮你只能从下面这些里选**一件**做：')
    # ★ 2026-10-07 R-33（仿真侧选「乙」= **真门槛**）：
    #   「投诉」(`COMPLAINT`) **只有出现在本轮推荐集合里时才列进候选栏**。
    #   表2b 只在 ('强负','高') / ('负','高') 两格含它 ⇒ **只有"已经很负面 + 已经被激怒"的客户
    #   才会看到这个选项**。⇒ 第 4 轮那例（负 × 中）根本看不到「投诉」，只能走「讲述遭遇」落本渠道。
    #   ⚠️ 旧 R-27b 的 `complaint_ok` 岔路（把 QUOTE_POST 拆成"讲述遭遇 / 发起投诉"）**整体删除**，
    #      `to_channel` 字段一并删除 —— 落点现在只由**动作**决定。
    _rec = rec_set(aid)
    _opts = [a for a in t1['act'] if a != 'DO_NOTHING']
    if 'DO_NOTHING' in t1['act']:
        _opts.append('DO_NOTHING')                 # 沉默排在"常规动作"之后（投诉若出现则在更后）
    # ★ R-47 ③：**每个动作都带一句说明**，且**引用不写角色个性化的说明** ——
    #   旧版把引用写成「把一条帖子引过来，讲**你自己**的遭遇（就发在 %s）」，于是渲染到
    #   媒体身上变成「发稿 —— 讲你自己的遭遇」（媒体哪来的"自己的遭遇"），
    #   渲染到客户身上变成「讲述遭遇 —— 讲你自己的遭遇」，**正是逼出编造的那一句**。
    #   ⇒ 现在：说明语对全角色统一（与 SYSTEM 段逐字一致）；只有「监管」保留专属动作名，
    #     因为监管的引用**真的不是引用**（不指向任何帖子，是就面上情况出通报，R-34）。
    #   ⚠️ 动作**标签**：除监管外一律用通用名 `ACT_CN`（引用/转发/…），不用 `t1['word']`
    #      （客户"讲述遭遇" / 媒体"发稿"）—— 这是"引用去角色个性化"的落地。
    #   ★ R-48（2026-10-12）：落盘口径 `action_cn` **也已统一**（同一函数 `action_name()`）
    #      ⇒ 提示词与快照**同词**，不再是"说引用、记讲述遭遇"。
    letters, idx = 'ABCDEFG', 0
    for act in _opts:
        if act == 'COMPLAINT' and ('COMPLAINT' not in _rec or not complaint_allowed(aid)):
            continue                               # ★ 乙：未被推荐或本场不许投诉 ⇒ 不列出来
        if act == 'COMPLAINT':
            # ★ R-47 ③：删掉旧的「（落点在黑猫投诉，不在 %s）」小括号 ——
            #   投诉落黑猫是**机制**，对发言的模型是噪声（且对驻黑猫的客户那句是自相矛盾的）。
            L.append('  %s. 投诉 —— 把这件事**拿到【黑猫投诉】去正式投诉**' % letters[idx])
        elif act == 'QUOTE_POST' and role == '监管':
            # ★ R-34：监管的「通报」**不引用任何帖子** —— 它是对面上的情况出结论，
            #   不是对某一条帖子的回应（R-19：不靠视野）。落池时 `quoted_id = None`。
            L.append('  %s. 发布通报 —— 就本事件**发布一份处置通报**（发布在 %s，'
                     '不需要指向任何帖子）' % (letters[idx], ch))
        elif act == 'QUOTE_POST':
            L.append('  %s. %s —— 把一条帖子引过来，同时写上你自己的话（就发在 %s）'
                     % (letters[idx], ACT_CN['QUOTE_POST'], ch))
        elif act == 'REPOST':
            # ★ R-49（2026-10-12 定案）：**补落点，其余一字不动**。
            #   原状态：引用行写了「（就发在 X）」，转发行**没有** —— 而代码里转发
            #   **同样**把该渠道带进内容的 `channels`（见下方落池块 `pt['channels'].add(...)`）
            #   ⇒ 提示词只在引用那边给了落点，等于暗示"引用会发出去、转发只是搬"，
            #     与机制不符。这是**呈现层的信息不对称**，不是设计选择。
            #   ⚠️ `ACT_DESC['REPOST']` 的「一个字都不加」**保留** —— 它是 REPOST 不产生
            #      `content` 的真实描述，也是两个动作在提示词里的**唯一实质分界**。
            #   ⚠️ **否掉**「为让跨渠道边指标好看而调整措辞」——那是拿指标配行为。
            #      口径问题改**指标**（登记表 F-08b 的两个候选口径），不改提示词。
            #   取证：`_probe_menu.py`（从快照 `prompt_user` 抠实测候选栏，新旧对照）。
            L.append('  %s. %s（就转到 %s）' % (letters[idx], ACT_DESC['REPOST'], ch))
        else:
            L.append('  %s. %s' % (letters[idx], ACT_DESC.get(act, ACT_CN[act])))
        idx += 1
    L.append('')
    # ★ R-24 表2b（2026-10-07 定案）：倾向由**单个档位**改为**推荐集合**（可多个动作）。
    #   渲染前已 ∩ 该身份的动作集 ⇒ 不会出现"倾向说引用、选项里没有引用"（旧黑猫 bug）。
    # ★ R-47 ⑤（2026-10-12）：**降级为参考**。仿真侧：「监管，要判断事态的严重性，
    #   我觉得**让 LLM 自己判断，比我们从硬公式给倾向更重要**」。
    #   抬头直接写「仅供参考」，并重申"上面列出的都能选"（旧句只把沉默拎成替代项，
    #   暗示"不照做 = 沉默"，架空另外四个选项）。
    L.append('【本轮倾向】（**仅供参考，不是命令**）')
    L.append('如果只按情绪和身份推，你更倾向于：**%s**' % rec_set_cn(aid))
    L.append('但上面列出的**每一个你都能选**，沉默也在内 —— 结合【你的身份意味着什么】'
             '自己判断，选哪个都合法。')
    L.append('')
    L.append('【角色写作要求】' if role in ('客户', '媒体', '监管') else '【写作要求】')
    L.append(WRITE_RULE[role])
    L.append('')
    L.append('【补充】')
    if role == '监管':
        L.append('- 走「发布通报」时，`"target_post_id"` 留空，`"content"` 写通报正文。')
        L.append('- 通报**不超过 80 字**，只写处置结论，不写情绪、不引用网民发言。')
    else:
        L.append('- 引用 / 转发 / 评论 / 点赞 **都必须指向上面列出的某一条帖子**，不许指向别处。')
        L.append('- 引用 和 评论 要写文字，**不超过 80 字**，像真人在网上说话。')
    if 'COMPLAINT' in _rec:
        L.append('- 走「投诉」时，`"action"` 填 `"COMPLAINT"`，`"target_post_id"` 填你要投诉的那条'
                 '帖子，`"content"` 写你的投诉诉求。')
    return '\n'.join(L)

# ══════════════════════════════════════════════════════════════
# 9. LLM
# ══════════════════════════════════════════════════════════════
def load_env():
    """读 LLM 凭据。`.env` 的位置见配置 `llm.env_path`（`_sim_config` 已解析成绝对路径）。

    ★ 2026-10-13 修死结：**`.env` 不再是必需品**。
      旧版第一句就是「`ENV_PATH` 不存在 → sys.exit」，等于在**读配置覆盖之前**先把人赶走：
      审核方哪怕把 `llm.base_url / api_key / model_name` 三样在事件配置里填齐，照样跑不起来。
      而 `.env.example` 第 2 行写着「也可以不建 .env，直接在事件配置里填」——
      文档承诺了，代码没兑现（2026-10-13 交付包里只有 `.env.example`、没有 `.env`，
      所以**任何**跑法都是当场退出）。

    现行口径 —— 三样凭据是一个整体，只有「齐」和「不齐」两种状态：
      ① 有 `.env` 就读；**没有也不报错**（那是合法配置，不是故障）；
      ② 配置里非空的三项**覆盖** `.env` 的同名项（审核方不必改 `.env`）；
      ③ 拼完之后三项必须齐 —— 缺哪项报哪项，并给出两条供应路径。
      键名 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL_NAME；任何 OpenAI 格式的 `base_url`
      都行（如 DeepSeek、内网自建）。

    **缺凭据仍然是硬失败**：绝不退回一个空 key 或假 endpoint 把请求打出去 ——
    那等于把「没配钥匙」伪装成「网络不通」，审核方会朝错的方向查半天。
    """
    cfg = {}
    if ENV_PATH and os.path.exists(ENV_PATH):      # ← 有就读，没有不算错
        for line in open(ENV_PATH, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            cfg[k.strip()] = v.strip()
    for _k, _ck in (('LLM_BASE_URL', 'base_url'), ('LLM_API_KEY', 'api_key'),
                    ('LLM_MODEL_NAME', 'model_name')):
        _v = str(CFG.llm.get(_ck) or '').strip()
        if _v:
            cfg[_k] = _v                        # 配置覆盖 .env

    miss = [k for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL_NAME')
            if not (cfg.get(k) or '').strip()]
    if miss:
        sys.exit('⛔ LLM 凭据不全，缺 %d 项：%s\n'
                 '   查过的 `.env`：%s\n'
                 '   两条路任选（填满三样就行，两处键名一致）：\n'
                 '     ① 【推荐】改事件配置 JSON 的 `llm` 段 ——\n'
                 '          "llm": {"base_url": "https://api.deepseek.com/v1",\n'
                 '                  "api_key": "sk-…", "model_name": "deepseek-chat"}\n'
                 '     ② 建一份 `.env`（照抄同目录的 `.env.example`），放 run_dir 或配置目录下；\n'
                 '        或在本配置里写 `llm.env_path`、或设环境变量 SIM_ENV 指到别处。\n'
                 '   `base_url` 任何 OpenAI 格式的服务都行（DeepSeek / 内网自建皆可）。'
                 % (len(miss), ' / '.join(miss), ENV_PATH or '（没找到 .env，也没配 env_path）'))
    return cfg

def call_llm(system, user, cfg_raw, retries=1):
    body = json.dumps({
        'model': cfg_raw['LLM_MODEL_NAME'],
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'temperature': 0.9, 'max_tokens': 300,
    }).encode('utf-8')
    req = urllib.request.Request(
        cfg_raw['LLM_BASE_URL'].rstrip('/') + '/chat/completions', data=body,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + cfg_raw['LLM_API_KEY']})
    last = None
    for _ in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                j = json.loads(r.read().decode('utf-8'))
            return j['choices'][0]['message']['content'], j.get('usage', {})
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last

def parse_reply(txt):
    t = txt.strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    m = re.search(r'\{.*\}', t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════
# 10. 执行本轮
# ══════════════════════════════════════════════════════════════
if args.dump_prompt:                      # 调试：只看提示词
    _d = args.dump_prompt
    if _d not in vision:
        print('\n⚠️ %s 未过视野门，渲染不出提示词。本轮可上场者：%s' % (_d, pool_final))
    else:
        print('\n' + '=' * 74)
        print('USER 提示词 · %s' % _d)
        print('=' * 74)
        print(build_user(_d))
    sys.exit(0)

cfg_raw = load_env()
print('\n④ 执行  模式 = %s  模型 = %s' % ('MOCK（不调 LLM）' if args.mock else 'REAL', cfg_raw['LLM_MODEL_NAME']))
print('-' * 74)

actions, tok_in, tok_out = [], 0, 0
for aid in ACTING:                      # ★ R-34：picked（等权随机）+ regs_act（监管例外通道）
    role, ch = PROF[aid]['role_type'], PROF[aid]['primary_channel']
    t1 = TABLE1[(role, ch)]
    g, _, (vb, ab) = gear_of(aid)
    # ★ R-34：机构没有"单个档位" ⇒ 快照里记表2c 的两根轴。
    # ★ 2026-10-19：判据由 `g is None` 改成 `delta is None` —— 表2 删除后**个体角色的 g 也是 None**，
    #   再靠 g 区分会把个体强行套到机构的群体轴上。
    if t1['delta'] is None:
        vb, ab = GV_BAND, SP_BAND
    user = build_user(aid)
    # ★ 2026-10-07：把**真正发出去的 USER 段**存进动作记录。
    #   此前快照里只有 vision / valence_start / gear —— 想审计"你到底跟模型说了什么"
    #   必须靠现场重渲，而重渲就是第二份 build_user ⇒ 必然漂移。
    #   存原文是唯一的零漂移解（SYSTEM 段全员共享，存 state 头部一次即可）。
    rec = dict(round=R, agent_id=aid, role_type=role, channel=ch,
               valence=cur[aid]['valence'], arousal=cur[aid]['arousal'],
               vision=vision.get(aid, []), gear=g,
               gear_cn=('表2c' if t1['delta'] is None else '表2b'),  # ★ 2026-10-19：表2 删除后无"单档位"
               rec_set=rec_set(aid),                              # ★ 表2b/表2c 推荐集合（原始动作名）
               band=(vb, ab), delta=t1['delta'], word=t1['word'], prompt_user=user)

    if args.mock:
        rep, usage = None, {}
        if args.mock_gear is not None:
            gg = args.mock_gear
        else:
            # ★ R-34 / 2026-10-19：没有"单个档位"（表2 已删）⇒ mock 取推荐集合里的**最高档**
            _rs = [GEAR_REV[a] for a in rec['rec_set'] if a in GEAR_REV]
            gg = max(_rs) if _rs else 0
        act = GEAR[gg]
        if act not in t1['act']:
            act = t1['act'][0]
        _mt = vision.get(aid, [])
        parsed = dict(action=act, target_post_id=_mt[0] if _mt else '',
                      content='（mock）', valence=cur[aid]['valence'], arousal=cur[aid]['arousal'])
        if args.mock_complaint and 'COMPLAINT' in t1['act']:
            # ★ 调试：逼出 R-33 的投诉落点。`_mock_force` 让下面那道
            #   「推荐集合里没有就降级」的闸门放行（否则 mock 永远到不了落点）。
            parsed['action'] = 'COMPLAINT'
            parsed['_mock_force'] = True
    else:
        txt, usage = call_llm(SYSTEM, user, cfg_raw)
        tok_in += usage.get('prompt_tokens', 0)
        tok_out += usage.get('completion_tokens', 0)
        parsed = parse_reply(txt)
        if parsed is None:
            parsed = dict(action='DO_NOTHING', target_post_id='', content='',
                          valence=None, arousal=None, _raw=txt[:200])

    act = parsed.get('action', 'DO_NOTHING')
    if act not in t1['act']:
        rec['illegal'] = 'LLM 给了 %s，不在可用集 %s → 记 DO_NOTHING' % (act, t1['act'])
        act = 'DO_NOTHING'
    # ★ R-33 乙：候选栏里没列出的「投诉」也可能被模型硬选（SYSTEM 段提到了这个动作名）。
    #   动作集里有它 ≠ 本轮可见 —— **可见性由表2b 的推荐集合决定**。
    #   兜底：降级为他本来就想做的那件事（发一条引用帖），但**落回本渠道**。
    elif (act == 'COMPLAINT' and 'COMPLAINT' not in rec['rec_set']
          and not parsed.get('_mock_force')):
        rec['complaint_downgrade'] = '本轮推荐集合无「投诉」（表2b 未给）→ 降级 QUOTE_POST 落本渠道'
        act = 'QUOTE_POST'
    rec['action'] = act
    # ★ R-48（2026-10-12）：`action_cn` 也改走 `action_name()` —— **落盘口径与提示词同词**。
    #   旧写法 `t1['word'] if QUOTE_POST else ACT_CN[act]` 让快照记「讲述遭遇」/「发稿」，
    #   而提示词里模型看到的是「引用」⇒ 审计时对不上。改后：客户/媒体落「引用」，
    #   监管落「发布通报」（提示词里也是这四个字）。
    #   ⚠️ 这会让**新旧快照的 `action_cn` 不同词** ⇒ 跨版本对账请用 `action`（不变）。
    rec['action_cn'] = action_name(aid, act)
    rec['target_post_id'] = parsed.get('target_post_id', '') or ''
    rec['content'] = (parsed.get('content') or '')[:120]
    rec['out_valence'] = parsed.get('valence')
    rec['out_arousal'] = parsed.get('arousal')
    rec['topic'] = parsed.get('topic')
    # ★ R-33 落点渠道：**由动作决定**（不再是 `to_channel` 字段）。
    #   `COMPLAINT` → 黑猫投诉；其余一律本渠道（R-27 层① 不变）。
    #   驻黑猫的客户本来就发在黑猫 ⇒ 他的 COMPLAINT 落点 = 本渠道，不算跨渠道。
    rec['landing_channel'] = '黑猫投诉' if act == 'COMPLAINT' else ch
    if rec['landing_channel'] != ch:
        rec['cross_channel_post'] = True
    rec['usage'] = usage
    actions.append(rec)

    # ★ 2026-10-07 改：原来打印 `GEAR_CN[g]`，监管格 `g = None` ⇒ KeyError（监管一上场就崩）。
    #   现在打的是**推荐集合**（`rec_set_cn`），与 prompt 里那一行逐字一致，便于对账。
    _gr = '表2c' if t1['delta'] is None else '表2b'
    print('%-20s %-4s×%-5s %-6s 倾向[%s] → %s %s%s' %
          (aid, role, ch, _gr, rec_set_cn(aid), rec['action_cn'],
           ('→' + rec['target_post_id']) if rec['target_post_id'] else '',
           ('  落地【%s】' % rec['landing_channel']) if rec.get('cross_channel_post') else ''))
    if rec['content']:
        print('      「%s」' % rec['content'])
    if parsed.get('_raw'):
        print('      ⚠️ 解析失败，原文：%s' % parsed['_raw'])

# ══════════════════════════════════════════════════════════════
# 11. ⑤ 把动作落到帖子池（R-27 三层 / R-27a 引用帖进池 / R-27b 投诉落点）
# ══════════════════════════════════════════════════════════════
new_seq = 0
for r_ in actions:
    tgt = r_['target_post_id']
    # ★ 2026-10-07 R-34：监管的「通报」**不引用任何帖子**（R-19：不靠视野）
    #   ⇒ 唯一允许 `target_post_id` 为空的引用型动作。其余动作目标不在池里 = 丢弃（原逻辑）。
    _reg_release = (r_['role_type'] == '监管' and r_['action'] == 'QUOTE_POST')
    if tgt not in POSTS and not _reg_release:
        continue
    pt = POSTS.get(tgt)
    if r_['action'] == 'LIKE_POST':
        pt['like_cnt_sim'] += 1
    elif r_['action'] == 'CREATE_COMMENT':
        pt['comment_cnt_sim'] += 1
    elif r_['action'] == 'REPOST':
        pt['repost_cnt_sim'] += 1
        pt['channels'].add(r_['channel'])          # R-27 转发语义：帖被带进本渠道
    elif r_['action'] in ('QUOTE_POST', 'COMPLAINT'):
        # ★ R-27 层① + R-27a：引用帖是**真新帖**，必须进池。
        #   旧代码只做了 `interactions += 1`，根本没建帖 ⇒ 传播链在 t1 就断掉（BUG 1，已修）。
        # ★ R-33：`COMPLAINT`（投诉）与引用**同形态**（引一条帖 + 写自己的话），
        #   同样进池；区别只在 `landing_channel`（投诉落黑猫投诉）。
        new_seq += 1
        land = r_['landing_channel']
        npid = 'EV1_Q%02d_%03d' % (R, new_seq)
        POSTS[npid] = dict(
            post_id=npid, channel=land, channels={land},
            publish_time=fmt_t(W_START), t_post=W_START,
            poster_agent_id=r_['agent_id'], poster_name=display_name(r_['agent_id']),
            content=r_['content'], quoted_id=(tgt if tgt in POSTS else None),
            author_valence_at_post=r_['valence'],      # R-22c 发帖那一刻的冻结核
            author_arousal_at_post=r_['arousal'],
            like_cnt_sim=0, comment_cnt_sim=0, repost_cnt_sim=0, interactions=0,
            like_cnt=0, comment_cnt=0,                 # 采集侧字段对新帖无意义
        )
        r_['new_post_id'] = npid

# 互动量刷到最新（F-07 ④ = like_cnt_sim + comment_cnt_sim）—— 下一轮 recompute_P 的热度因子读它
for _p in POSTS.values():
    _p['interactions'] = _p['like_cnt_sim'] + _p['comment_cnt_sim']

print('\n⑤ 帖子池  %d → %d 条（本轮自产引用帖 %d 条）'
      % (len(POSTS) - new_seq, len(POSTS), new_seq))
for r_ in actions:
    if r_.get('new_post_id'):
        print('   新帖 %s  落【%s】  %s  「%s」'
              % (r_['new_post_id'], r_['landing_channel'],
                 ('引用 %s' % r_['target_post_id']) if r_['target_post_id'] else '（无引用 · 通报）',
                 r_['content'][:38]))

# ══════════════════════════════════════════════════════════════
# 12. ⑥ 第 R 轮**轮末**情绪刷新（R-22b：刷新点从轮首整体挪到轮末，每轮只此一次）
# ══════════════════════════════════════════════════════════════
ONLINE = hard_online(R)
POOL_POSTS = list(POSTS.keys())                    # 池里已含本轮自产的新帖
P_end = {aid: recompute_P(aid, W_END, POOL_POSTS) for aid in ONLINE}
tail = {aid: dict(valence=s['valence'], arousal=s['arousal']) for aid, s in cur.items()}
cur_end = refresh(tail, round_len(R), ONLINE, p_map=P_end)

_eon = sorted(ONLINE)
end_v = sum(cur_end[i]['valence'] for i in _eon) / len(_eon)
end_a = sum(cur_end[i]['arousal'] for i in _eon) / len(_eon)
eb_v = sum(PROF[i]['valence_baseline'] for i in _eon) / len(_eon)
eb_a = sum(PROF[i]['arousal_baseline'] for i in _eon) / len(_eon)
dev_end = 0.6667 * (eb_v - end_v) + 0.3333 * (end_a - eb_a)
print('⑥ 轮末刷新  在线 %d 人  效价 %+.4f  唤醒 %+.4f  偏离 %+.6f'
      % (len(_eon), end_v, end_a, dev_end))
# 🗑️ 2026-10-07 R-34：**P-06 判定整段删除**（原「群体效价 < −0.6 是否触发」—— 它只 print、
#   不产生动作，删掉不影响任何实测数）。监管的出场判据已由**表2c 阈值 B** 接管（见 ③ 抽样段）。
print('   表2c 收尾：本轮 群体效价档【%s】× 传播范围档【%s】（K*= %d）⇒ 阈值B %s'
      % (GV_BAND, SP_BAND, SPREAD_K,
         '命中，倾向为「发布通报」' if TABLE2C_B.get((GV_BAND, SP_BAND)) else '未命中（倾向为「沉默」）'))
print('   R-35 监管去特例化：本轮抽中监管 %d 人 %s' % (len(regs_act), regs_act or '（无）'))

# ══════════════════════════════════════════════════════════════
# 13. ⑦ 落盘：状态快照 + 记录员 A
# ══════════════════════════════════════════════════════════════
state = dict(
    spec='%s 第 %d 轮状态快照 —— 仿真层自产' % (EVENT_ID, R),
    event_id=EVENT_ID, round=R,
    window=[fmt_t(W_START), fmt_t(W_END)],
    params=dict(G=G, gbar=gbar, quota=quota, gain=GAIN, deviation=deviation,
                deviation_round_end=round(dev_end, 6),
                contagion_gain=CONTAGION_GAIN, contagion_gain_a=CONTAGION_GAIN_A,
                # ★ R-42：把"用的是哪一版传染式"写进快照，否则旧基准数无法解释
                contagion_rule='V3_render', arousal_mode=AROUSAL_MODE,
                decay_anchor=DECAY_ANCHOR, interaction_source='sim',
                # ★ R-45：衰减拆分口径 —— 轮初 = 衰减(轮末(r−1), L_{r−1})，轮末 = 轮初 + Δv
                decay_split='R-45',
                # ★ R-44：唤醒界值（低界统一 / 中高界分角色），否则表2b 的档位无法复算
                a_band_lo=A_BAND_LO, a_band_hi=dict(A_BAND_HI),
                vision_gate='removed(R-46)',
                identity_rule='R-47',
                # ★ 2026-10-07 R-34：表2c 的两根轴 —— 下一轮口径丙「沿用上一轮档」要读 `group_v_band`
                group_v=round(cur_v, 6), group_v_band=GV_BAND,
                group_v_band_raw=GV_BAND_RAW, group_v_carried=GV_CARRIED,
                group_v_online=len(online_ids),
                spread_k=SPREAD_K, spread_band=SP_BAND,
                regs_act=regs_act,
                seed=SEED, offsets=dict(sample=OFF_SAMPLE, eps_v=OFF_EV, eps_a=OFF_EA),
                mode='mock' if args.mock else 'real', model=cfg_raw['LLM_MODEL_NAME']),
    # ★ 2026-10-07：SYSTEM 段**全员共享**（R-29 的设计目的就是前缀缓存），故只在头部存一次。
    #   USER 段因人而异，存在各自的 actions[] 里（`prompt_user`）。
    system_prompt=SYSTEM,
    # ★ 2026-10-07：四路随机流**本轮轮末**的状态 —— 下一轮靠它续跑（详见登记表 §3.7）。
    #   没有它，第 r+1 轮就会从种子原点重抽 ⇒ 抽到另一批人。
    rng_state={k: rng_dump(g) for k, g in _STREAMS.items()},
    posts=[dict(post_id=p['post_id'], channel=p['channel'], channels=sorted(p['channels']),
                publish_time=p['publish_time'], poster_agent_id=p['poster_agent_id'],
                poster_name=p['poster_name'], content=p['content'][:400],
                quoted_id=p.get('quoted_id'),
                author_valence_at_post=p['author_valence_at_post'],
                author_arousal_at_post=p.get('author_arousal_at_post'),
                like_cnt=p['like_cnt'], comment_cnt=p['comment_cnt'],
                like_cnt_sim=p['like_cnt_sim'], comment_cnt_sim=p['comment_cnt_sim'],
                repost_cnt_sim=p['repost_cnt_sim'],
                interactions=p['interactions'])
           for p in POSTS.values()],
    agents=[dict(agent_id=a['agent_id'],
                 role_type=a['role_type'], primary_channel=a['primary_channel'],
                 # ★ R-22b / BUG 3 已修：is_online 统一 = **硬门口径**（不是 pool_final 的 5 人）
                 is_online=a['agent_id'] in ONLINE,
                 # ★ R-22b：快照的 `valence` / `arousal` = **本轮轮末**（下一轮消费的即此）
                 valence=cur_end[a['agent_id']]['valence'],
                 arousal=cur_end[a['agent_id']]['arousal'],
                 # ★ R-45（2026-10-12）：`*_start` = **本轮轮初** = 衰减(上一轮轮末, 上一轮轮长)。
                 #   仿真侧要"每轮末和轮初都记一遍"：**记录员读轮末，轮初是抽样/动作的基准**。
                 #   ⚠️ 与轮末的关系 = 轮末 = 轮初 + 传染项 + 噪声（`d_contagion` 的恒等式）。
                 valence_start=cur[a['agent_id']]['valence'],
                 arousal_start=cur[a['agent_id']]['arousal'],
                 # ★ R-42 审计：轮末 = v_decay + d_contagion + 噪声（逐人逐项可验算）
                 v_decay=cur_end[a['agent_id']].get('v_decay'),
                 v_bar=cur_end[a['agent_id']].get('v_bar'),
                 d_contagion=cur_end[a['agent_id']].get('d_contagion'),
                 a_decay=cur_end[a['agent_id']].get('a_decay'),
                 a_bar=cur_end[a['agent_id']].get('a_bar'),
                 d_arousal_contagion=cur_end[a['agent_id']].get('d_arousal_contagion'),
                 P=P_new.get(a['agent_id'], {}),
                 view=vision.get(a['agent_id'], []),
                 picked=a['agent_id'] in picked)
            for a in AGENTS],
    actions=actions,
)
out_state = _out_state                      # 路径已在 §2 定好（含 .mock 后缀），此处复用
json.dump(state, open(out_state, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\n⑦ 落盘  %s' % out_state)

# ── 记录员 A（7 行）—— 实现在 recorder_a.py（单一实现）──────────
import recorder_a

rows = recorder_a.build_rows(dict(
    round=R, window=[fmt_t(W_START), fmt_t(W_END)],
    agents=[dict(agent_id=a['agent_id'], role_type=a['role_type'],
                 primary_channel=a['primary_channel']) for a in AGENTS],
    states=cur_end,                     # ★ 轮末值（R-22b）
    online_ids=ONLINE,                  # ★ 硬门口径（46/16，不是 t0 快照 47、也不是 5）
    vision=vision,
    posts=POSTS,
    prev_channels=PREV_CHANNELS,        # ★ 新增：跨轮差集口径（BUG 2 已修）
    actions=actions,
    tok_in=tok_in, tok_out=tok_out,
))
with open(CFG.recorder_path(SUFFIX), 'a', encoding='utf-8') as f:
    for r_ in rows:
        f.write(json.dumps(r_, ensure_ascii=False) + '\n')
print('⑦ 记录员 A 追加 %d 行 → %s' % (len(rows), CFG.with_suffix(CFG['recorder_file'], SUFFIX)))

print('\n本轮动作分布：', dict(Counter(a['action_cn'] for a in actions)))
print('提示词 tokens %d ｜ 补全 tokens %d' % (tok_in, tok_out))
