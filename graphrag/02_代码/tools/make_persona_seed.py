# -*- coding: utf-8 -*-
"""persona_bio 生成器 —— 方案③：上游放【人设种子】，下游 LLM 运行时展开。

为什么不逐人精写 178 字：
  · persona_bio 是 14 个交付字段里唯一【不携带结构化信息】的字段 —— 年龄/职业/立场都在别的字段里
  · 它唯一的作用是给下游 LLM 一个「声音」，而这完全可以由下游拿着属性现场推导
  · 上游静态 bio 对不上运行时状态（当前情绪/看到什么/跟谁互动过）
  · 名单一变就全废（已返工 3 次）

为什么不能没有：
  · 只给 LLM 一堆数字 -> 292 个 agent 产出 292 条结构雷同的话（population collapse）
  · 种子提供「语域锚点」（66 岁退休教师 vs 22 岁学生 vs 42 岁财务）

字段名保持不变，仍叫 persona_bio。
"""
import json, sys, os

def _num(v, suffix=""):
    if v is None: return ""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v) + suffix
    if n >= 10000:
        s = "%.1f万" % (n / 10000.0) if (n/10000.0) != int(n/10000.0) else "%d万" % (n//10000)
    else:
        s = "{:,}".format(n)
    return s + suffix

CNT = {"3千以下": "月入 3 千以下", "3-6千": "月入 3-6 千", "6-10千": "月入 6-10 千",
       "10-20千": "月入 1-2 万", "2万以上": "月入 2 万以上"}

def seed_of(x, top_n=3):
    """从属性生成人设种子。字段名仍叫 persona_bio。"""
    # ★ 画像库用 channel，交付件用 primary_channel —— 两者都认
    _CH = x.get("primary_channel") or x.get("channel")
    role = x.get("role_type") or ""
    if role in ("媒体", "监管"):
        # 机构号：第三人称机构画像
        p = []
        ot = x.get("org_type"); op = x.get("org_position"); osc = x.get("org_scope")
        head = " · ".join([z for z in (ot, osc, op) if z]) or "机构账号"
        p.append("【机构号】" + head)
        if x.get("region"): p.append("驻" + str(x["region"]))
        ch = _CH
        fans = _num(x.get("followers"))
        # ★ 粉丝为 0 时省略（写「0粉」很难看，且 0 多半表示源数据缺失）
        if ch: p.append(("%s %s粉" % (ch, fans)) if (fans and fans not in ("0", "0.0")) else str(ch))
        it = x.get("interest_topics") or {}
        ks = [k for k, v in sorted(it.items(), key=lambda kv: -kv[1]) if k != "其他" and v > 0][:top_n]
        if ks: p.append("关注领域：" + "、".join(ks))
        return "；".join(p) + "。"
    # 个人 / KOL
    p = []
    if x.get("is_t0_real") or x.get("real_persona_of"):
        p.append("【t0 真实人物】")
    a = x.get("age"); g = x.get("gender")
    seg = []
    _G = {"女": "女性", "男": "男性"}
    if a is not None and g: seg.append("%s 岁%s" % (a, _G.get(g, g)))
    elif a is not None: seg.append("%s 岁" % a)
    if x.get("region"):
        _r = str(x["region"]) + (("（" + str(x["urban_tier"]) + "）") if x.get("urban_tier") else "")
        seg.append(_r)
    if x.get("education"): seg.append(str(x["education"]))
    if x.get("occupation"): seg.append(str(x["occupation"]))
    if x.get("marital_status"): seg.append(str(x["marital_status"]))
    if x.get("income_band"): seg.append(CNT.get(x["income_band"], str(x["income_band"])))
    if seg: p.append("，".join(seg))
    seg2 = []
    if _CH:
        f = _num(x.get("followers"))
        seg2.append("常驻%s%s" % (_CH, ("，" + f + " 粉") if (f and f not in ("0", "0.0")) else ""))
    if x.get("interest_circle"): seg2.append("圈层" + str(x["interest_circle"]))
    if seg2: p.append("；".join(seg2))
    it = x.get("interest_topics") or {}
    ks = [k for k, v in sorted(it.items(), key=lambda kv: -kv[1]) if k != "其他" and v > 0][:top_n]
    if ks: p.append("关注：" + "、".join(ks))
    s = "；".join(p[:-1]) if len(p) > 1 else ""
    tail = "；".join(p[-1:]) if len(p) > 1 else p[0] if p else ""
    head = p[0] if p and p[0].startswith("【") else ""
    body = [z for z in p if not z.startswith("【")]
    return (head + ("；".join(body) if head else "；".join(body))) + "。"

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top-n", type=int, default=3)
    a = ap.parse_args()
    d = json.load(open(a.src, encoding="utf-8"))
    arr = d if isinstance(d, list) else (d.get("members") or d.get("agents") or d.get("pool") or d)
    n = 0
    for x in arr:
        x["persona_bio"] = seed_of(x, a.top_n)
        n += 1
    json.dump(d, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = sorted(len(x["persona_bio"]) for x in arr)
    print("  %d 条 ｜ 长度 min %d / p50 %d / max %d" % (n, L[0], L[len(L)//2], L[-1]))
    for x in arr[:3]: print("    " + x["agent_id"] + " -> " + x["persona_bio"])
