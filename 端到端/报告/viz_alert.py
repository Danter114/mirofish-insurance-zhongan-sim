# -*- coding: utf-8 -*-
"""在 _viz_data.json 上追加【报告层风险等级】合成结果。

口径声明（★ 2026-10-13 第十七轮改版，此前的「九项观测量固定权重」口径作废）：

  ① 归属：风险等级是【报告层】由三项结果合成的产物 —— 传播路径图谱 / 情绪演化曲线 /
     关键影响节点；仿真层本身不产出预警等级（仿真层 grep 风险等级/预警等级/四级预警 无匹配）。

  ② 三项维度、八项观测量，全部在**长度为 5 轮的滑动窗口**（末尾对齐当前轮）上聚合。
     窗口一律取 W = 5 轮 —— 每自然日约 6 轮，5 轮 ≈ 20 小时 ≈ 1 个仿真日。

        维度一 传播广度（G1，权重 0.35） ← 传播路径图谱
           · 窗口内渠道加权帖量        权重 0.60
           · 窗口内发帖渠道种数（去重） 权重 0.25
           · 窗口内新增跨渠道边数      权重 0.15   ← 跨渠道加成
        维度二 情绪态势（G2，权重 0.30） ← 情绪演化曲线
           · |全场效价| / 0.50         权重 0.60
           · 负面占比                  权重 0.40
        维度三 参与强度（G3，权重 0.35） ← 传播路径图谱 / 关键影响节点
           · 窗口内发声人数            权重 0.35
           · 窗口内动作强度加权互动量  权重 0.35
           · 窗口内新生帖介数峰值      权重 0.30

      S = 0.35·G1 + 0.30·G2 + 0.35·G3

  ③ 归一化：每个窗口量都**除以该量在本跑全程的窗口最大值**（逐项独立归一），
     再按上表加权。基准全部由本跑自身算出，不含任何外部标定集、不含人工判断，逐轮可复算。
     （改版前 G1 是「累计量」形式，三项都单调不减 ⇒ S 只升不降；改为窗口口径后
       S 有升有降，等级能回落。）

  ④ 渠道权重是**基础预警规则**（规则级常量），六个渠道全部有定义，与某一次跑是否真有帖无关：

         监管通报 3.0 ｜ 黑猫投诉 2.5 ｜ 新闻门户 2.0 ｜ 微博 1.5 ｜ 小红书 1.5 ｜ 抖音 1.5
     理由：官方通报落地＝风险已进入监管视野（最高一级）；投诉聚合平台是投保人维权集中地；
     新闻门户是媒体背书与二次转载源，把个案变成公共议题；微博/小红书/抖音为大众舆论场。
     本跑监管通报与黑猫投诉均无内容产出，这两档权重**定义了但未被触发**。

  ⑤ 动作强度排行（规则级常量，同上）：

         通报 (POST_NOTICE) 5 ｜ 投诉引用 (COMPLAINT) 4 ｜ 转发 (REPOST) 3
                                        ｜ 引用 (QUOTE_POST) 4 ｜ 评论 2 ｜ 点赞 1
     —— 仿真侧给定序：通报 > 投诉引用 > 转发 > 评论 > 点赞。本跑的引用即产帖动作，
     与「投诉引用」同档记为 4（引用会新增一条帖，是唯一能把内容带到新平台的动作）。
     COMPLAINT 与通报两类动作在本跑**从未出现**（0 次），权重预留。

  ⑥ 分级阈值：红 ≥ 0.80 ｜ 橙 0.65–0.80 ｜ 黄 0.50–0.65 ｜ 蓝 < 0.50。
"""
import json, io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ★ 2026-10-13 第十八轮：路径走**事件配置**（口径见 _report_config.py）。默认同目录。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _report_config import load as _load_cfg

CFG = _load_cfg(argv=sys.argv[1:])
D = CFG.run_dir
P = CFG.data_path
d = json.load(open(P, encoding='utf-8'))

# ==================== 规则常量（基础预警规则，非本跑专属） ====================
CHAN_W = {"监管通报": 3.0, "黑猫投诉": 2.5, "新闻门户": 2.0,
          "微博": 1.5, "小红书": 1.5, "抖音": 1.5}
ACT_W  = {"NOTICE": 5.0, "COMPLAINT": 4.0, "QUOTE_POST": 4.0,
          "REPOST": 3.0, "CREATE_COMMENT": 2.0, "LIKE_POST": 1.0}
WIN_W  = 5                       # 窗口长度（轮）；每自然日约 6 轮
G1_W   = (0.60, 0.25, 0.15)      # 加权帖量 / 发帖渠道种数 / 新增跨渠道边
G2_W   = (0.60, 0.40)            # |效价|/0.50 / 负面占比
G3_W   = (0.35, 0.35, 0.30)      # 发声人数 / 加权互动量 / 新生帖介数峰值
DIM_W  = {"G1": 0.35, "G2": 0.30, "G3": 0.35}
CUT    = {"红色": 0.80, "橙色": 0.65, "黄色": 0.50, "蓝色": 0.0}
VAL_CAP = 0.50                   # 效价归一化分母（|效价| 达到 0.50 即记满）

# ==================== 逐轮原始量 ====================
posts, series = d["posts"], d["series"]
NR = d["meta"]["rounds"]
by_r = {}
for p in posts:
    by_r.setdefault(p["r"], []).append(p)

raw = []
for x in series:
    r = x["round"]
    act = x.get("act") or {}
    newp = by_r.get(r, [])
    raw.append({
        "r": r, "window": x["window"],
        "postw": sum(CHAN_W.get(p["ch"], 1.0) for p in newp),   # 渠道加权帖量
        "chs":   {p["ch"] for p in newp},                       # 本轮发帖渠道（去重用）
        "newx":  x["newcross"],                                 # 新增跨渠道边
        "nag":   len({p["agent"] for p in newp}),               # 发声人数
        "actw":  sum(ACT_W.get(k, 0.0) * act.get(k, 0) for k in ACT_W),  # 加权互动
        "mb":    max([p["betw"] for p in newp] or [0]),         # 新生帖介数峰值
        "npost": len(newp),                                     # 窗口内的帖子数（备查）
    })

# ==================== 窗口聚合（末尾对齐当前轮） ====================
def wsum(key):
    return [sum(z[key] for z in raw[max(0, i - WIN_W + 1):i + 1]) for i in range(NR)]

def wmax(key):
    return [max(z[key] for z in raw[max(0, i - WIN_W + 1):i + 1]) for i in range(NR)]

def wuniq(key):
    """窗口内**去重**的集合大小 —— 「窗口内发帖渠道数」按字面即此口径。"""
    return [len(set().union(*[z[key] for z in raw[max(0, i - WIN_W + 1):i + 1]]))
            for i in range(NR)]

W_POSTW, W_NCH, W_CROSS = wsum("postw"), wuniq("chs"), wsum("newx")
W_NAG,   W_ACTW, W_BETW = wsum("nag"), wsum("actw"), wmax("mb")

def norm_max(v):
    """按本跑全程窗口最大值归一；全零时返回全 0（而不是除零）。"""
    m = max(v) if v else 0.0
    return [z / m for z in v] if m else [0.0] * len(v)

N_POSTW, N_NCH, N_CROSS = norm_max(W_POSTW), norm_max(W_NCH), norm_max(W_CROSS)
N_NAG,   N_ACTW, N_BETW = norm_max(W_NAG), norm_max(W_ACTW), norm_max(W_BETW)
VAL_MAX = max((abs(x["valence"]) for x in series), default=0.0)

# ==================== 合成 ====================
rows = []
for i, x in enumerate(series):
    g1_postw = G1_W[0] * N_POSTW[i]
    g1_nch   = G1_W[1] * N_NCH[i]
    g1_cross = G1_W[2] * N_CROSS[i]
    G1 = g1_postw + g1_nch + g1_cross

    g2_val = G2_W[0] * min(1.0, abs(x["valence"]) / VAL_CAP)
    g2_neg = G2_W[1] * x["neg"]
    G2 = g2_val + g2_neg

    g3_num  = G3_W[0] * N_NAG[i]
    g3_act  = G3_W[1] * N_ACTW[i]
    g3_betw = G3_W[2] * N_BETW[i]
    G3 = g3_num + g3_act + g3_betw

    score = DIM_W["G1"] * G1 + DIM_W["G2"] * G2 + DIM_W["G3"] * G3
    lvl = ("红色" if score >= CUT["红色"] else "橙色" if score >= CUT["橙色"]
           else "黄色" if score >= CUT["黄色"] else "蓝色")
    lo = max(0, i - WIN_W + 1)
    rows.append({
        "round": raw[i]["r"], "window": x["window"],
        "score": round(score, 4), "level": lvl,
        "G1": round(G1, 4), "G2": round(G2, 4), "G3": round(G3, 4),
        # 归一化观测量（表 4-2「归一化」列直接读这几个）
        "o1_postw": round(N_POSTW[i], 4), "o1_nch": round(N_NCH[i], 4),
        "o1_cross": round(N_CROSS[i], 4),
        "o2_val": round(min(1.0, abs(x["valence"]) / VAL_CAP), 4),
        "o2_neg": round(x["neg"], 4),
        "o3_num": round(N_NAG[i], 4), "o3_act": round(N_ACTW[i], 4),
        "o3_betw": round(N_BETW[i], 4),
        # 窗口原始量（表 4-2「观测量」列直接读这几个）
        "w_postw": round(W_POSTW[i], 2), "w_nch": W_NCH[i], "w_cross": W_CROSS[i],
        "w_nag": W_NAG[i], "w_actw": round(W_ACTW[i], 1), "w_betw": W_BETW[i],
        "w_posts": sum(z["npost"] for z in raw[lo:i + 1]),
        "win_lo": raw[lo]["r"], "win_hi": raw[i]["r"],
        # 备查全局量
        "cross_cum": sum(z["newx"] for z in raw[:i + 1]),
        "posts_cum": sum(z["npost"] for z in raw[:i + 1]),
    })

lead = max(rows, key=lambda x: x["score"])
first = {}
for x in rows:
    first.setdefault(x["level"], x["round"])
d["alert"] = {
    "formula": "S = 0.35·G1 + 0.30·G2 + 0.35·G3",
    "dim_names": {"G1": "传播广度", "G2": "情绪态势", "G3": "参与强度"},
    "weights": dict(DIM_W),
    "sub_weights": {"G1": {"postw": G1_W[0], "nch": G1_W[1], "cross": G1_W[2]},
                    "G2": {"val": G2_W[0], "neg": G2_W[1]},
                    "G3": {"num": G3_W[0], "act": G3_W[1], "betw": G3_W[2]}},
    "cut": dict(CUT),
    "window": WIN_W,
    "chan_weights": dict(CHAN_W),
    "action_weights": dict(ACT_W),
    "norm": {"postw": round(max(W_POSTW), 2), "nch": max(W_NCH),
             "cross": max(W_CROSS), "nag": max(W_NAG),
             "actw": round(max(W_ACTW), 1), "betw": max(W_BETW),
             "val_cap": VAL_CAP, "val_obs": round(VAL_MAX, 4)},
    "n_chan": len(CHAN_W),
    "n_poster": len({p["agent"] for p in posts}),
    "cross_total": d["meta"]["cross_n"],
    "max_betw": d["meta"]["max_betw"],
    "rows": rows,
    "peak": {"round": lead["round"], "score": lead["score"], "level": lead["level"]},
    "first_round": first,
}
json.dump(d, open(P, "w", encoding='utf-8'), ensure_ascii=False)

# ==================== 输出 ====================
print("轮  时间               G1     G2     G3     S      等级   ｜窗口加权帖 渠道 跨渠道 发声人 加权互动 介数")
for i, x in enumerate(rows):
    print("%2d  %s  %.3f  %.3f  %.3f  %.3f  %-4s ｜%8.1f %5d %6d %6d %8.1f %5d" % (
        x["round"], x["window"][0], x["G1"], x["G2"], x["G3"], x["score"], x["level"],
        x["w_postw"], x["w_nch"], x["w_cross"], x["w_nag"], x["w_actw"], x["w_betw"]))
print("\n首次出现轮次:", first)
print("峰值:", lead["round"], lead["score"], lead["level"])
print("归一化基准（本跑窗口最大值）:", d["alert"]["norm"])
print("窗口 W=%d 轮 ｜ 规则常量：渠道权重 %s ｜ 动作权重 %s" % (WIN_W, CHAN_W, ACT_W))
print("payload ->", P, os.path.getsize(P), "bytes")
