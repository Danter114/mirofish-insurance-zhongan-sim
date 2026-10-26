#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stance_value.py —— t0 立场值标定：把「这条帖子对【事件主体】的公开行为承诺强度」
标成一个连续量 stance ∈ [-1, +1]。

★ 为什么要有这个模块（组长要 t0 初始立场，上游原本只给了 sentiment）：
  · sentiment 是【帖子的情绪色彩】，stance 是【帖子对主体的站队方向】，两者可以完全脱钩。
    事件1 实测：长城网那条 sentiment=中性，内容却在报道批评、高位评论全是骂公司的。
    若直接拿 sentiment 当立场，传播链上的放大器就被判成了中立者。
  · 三值（反对/中立/支持）是【输出层】，不能当状态层。给点值会让所有人在同一轮集体越线，
    曲线出现刀切一样的台阶；也没有强度可以累加，方向一致率退化成一个对/错。
    所以：内部连续、外部三值，中间用带滞回的阈值连接。

★ 标定式（全部参数在 config/stance.json，代码里不写死）：

    方向   = sign(负向词加权和 - 正向词加权和)     # 对主体的极性，不直接采用 sentiment
    基座   = 表态方式（self_loss / ask / relay / report，有序匹配、第一个命中）
    强度   = clip(基座 + Σ幅度修正, 0, 1)
    stance = 方向 x 强度

★ 四个刻意的设计（都是自测里踩出来的）：
  ① 幅度修正只改【强度】不改【方向】—— 否则「正文含公司回应」这种回收项会把符号翻过来
     （媒体帖会从 -0.17 变成 +0.17，正好判反）。
  ② 幅度修正命中多次【只算一次】，方向词命中多次才按 min(2, n) 计分 ——
     「公然挑衅、公然侮辱」要能体现强度，但一个词不能把分数刷满。
  ③ 询问 / 客观报道是【未表态形式】：即使 sentiment 有极性，也不得用来兜底方向。
     这正是「情绪 != 立场」的落点——求证帖 sentiment=负面，立场仍然是 0。
  ④ 已在 t0 发帖 = 已公开表态，刚性与步长都要按 committed 条款调整（承诺一致性成本）。
"""
from __future__ import annotations

import re

BIN_LABELS = ("反对", "中立", "支持")

_RX = {}


def _rx(pattern):
    """正则编译缓存。500 人 x 20 条规则，不缓存会明显变慢。"""
    r = _RX.get(pattern)
    if r is None:
        r = _RX[pattern] = re.compile(pattern, re.I)
    return r


# ---------------- 文本准备 ----------------

def text_of(doc, comment_cfg=None):
    """只留【发帖人自己说的话】：标题 + 正文，并先切掉【高赞评论】这类别人的文本。

    切评论块复用 poster_stance 的同一份词表（config/poster_stance.json 的 comment_blocks）——
    不能各写一份，否则两处口径会漂移。
    """
    body = doc.get("raw_content") or doc.get("content") or ""
    if comment_cfg:
        from poster_stance import strip_comment_blocks
        body = strip_comment_blocks(body, comment_cfg)
    text = " ".join([(doc.get("title") or ""), body])
    return " ".join(text.split())


def _scan(rules, text, doc=None, role=None, multiplicity=False):
    """按规则表扫描，返回命中明细。

    multiplicity=True（只给方向词表用）：同一规则命中多次按 min(2, n) x w 计分。
    multiplicity=False（默认，给幅度修正用）：命中就只算一次 w —— 幅度修正是
    「有没有出现这种语气」，不是「出现了几次」，乘次数会让一条帖子直接顶到饱和。

    规则可选条件：
      roles  只对这些角色生效
      when   字段条件，如 {"field": "is_repost", "eq": true}
    """
    out = []
    for r in (rules or []):
        rl = r.get("roles")
        if rl and role not in rl:
            continue
        cond = r.get("when")
        if cond:
            v = doc.get(cond.get("field")) if doc else None
            if bool(v) != bool(cond.get("eq", True)):
                continue
        pat = r.get("re")
        matches = []
        if pat:
            matches = _rx(pat).findall(text)
            if not matches:
                continue
        elif not cond and not rl:
            continue                      # 正则/条件/角色三样都没有 -> 不生效（防配置写空规则）
        n = max(1, min(2, len(matches))) if (multiplicity and matches) else 1
        out.append({"id": r.get("id"), "w": float(r.get("w", 0.0)),
                    "times": n, "score": float(r.get("w", 0.0)) * n,
                    "match": (matches[0] if matches else ""), "why": r.get("why", "")})
    return out


# ---------------- 方向 ----------------

def _direction_text(text, cfg):
    """只看文本的方向。返回 (sign, source, neg, pos, score)。"""
    d = cfg.get("direction") or {}
    neg = _scan(d.get("negative"), text, multiplicity=True)
    pos = _scan(d.get("positive"), text, multiplicity=True)
    ns = sum(h["score"] for h in neg)
    ps = sum(h["score"] for h in pos)
    if ns > ps:
        return -1, "text", neg, pos, ns
    if ps > ns:
        return 1, "text", neg, pos, ps
    if ns > 0:                            # 正负抵消 -> 无净方向
        return 0, "tie", neg, pos, ns
    return 0, "none", neg, pos, 0.0


def _sentiment_fallback(doc, cfg):
    fb = ((cfg.get("direction") or {}).get("sentiment_fallback") or {}).get(
        str(doc.get("sentiment") or "").strip())
    return int(fb) if fb else 0


# ---------------- 基座（表态方式） ----------------

def _base(text, cfg):
    """有序匹配，第一个命中即用。返回 (规则, 命中词, 是否显式命中)。

    显式命中 vs 兜底：只有「显式命中且属于未表态形式」才禁止 sentiment 兜底方向。
    """
    b = cfg.get("base") or {}
    for r in (b.get("rules") or []):
        pat = r.get("re")
        m = _rx(pat).search(text) if pat else None
        if m:
            return ({"id": r.get("id"), "w": float(r.get("w", 0.0)), "why": r.get("why", "")},
                    m.group(0), True)
    return (b.get("fallback_directed") or {"id": "allege", "w": 0.62, "why": ""}), "", False


# ---------------- 主入口 ----------------

def calibrate(doc, cfg, stance_res=None, comment_cfg=None):
    """标定一条帖子的 t0 立场值。

    doc         采集侧文档（需要 raw_content / title / sentiment，可选 is_repost）
    cfg         config/stance.json
    stance_res  poster_stance.classify 的结果（取 role_effective 判机构账号修正）
    comment_cfg config/poster_stance.json（用于切评论块，缺省则不切）
    """
    text = text_of(doc, comment_cfg)
    role = ((stance_res or {}).get("role_effective") or doc.get("poster_role") or "")

    sign, dsrc, neg, pos, score = _direction_text(text, cfg)
    base, bmatch, base_explicit = _base(text, cfg)

    # ★ 未表态形式（询问 / 客观报道）即使 sentiment 有极性也不兜底方向
    if sign == 0 and not neg and not pos:
        bcfg = cfg.get("base") or {}
        noncommittal = base.get("id") in (bcfg.get("non_committal") or [])
        if not (base_explicit and noncommittal):
            fb = _sentiment_fallback(doc, cfg)
            if fb:
                sign, dsrc = fb, "sentiment_fallback"

    adjs = _scan((cfg.get("adjustments") or {}).get("rules"), text, doc, role)
    mag = float(base.get("w", 0.0)) + sum(a["score"] for a in adjs)
    mag = max(0.0, min(1.0, mag))         # ★ 只夹强度，不夹方向
    value = round(sign * mag, 4)

    hi = float((cfg.get("confidence") or {}).get("high_min_score", 1.2))
    conf = "low" if dsrc != "text" else ("high" if score >= hi else "medium")

    ev = ([{"side": "negative", "match": h["match"], "why": h["why"]} for h in neg]
          + [{"side": "positive", "match": h["match"], "why": h["why"]} for h in pos])
    reason = ("方向=%s(%s, 证据分 %.1f)；表态方式=%s(%s)%s；强度 %.2f -> stance %.2f"
              % (sign, dsrc, score, base.get("id"), bmatch or "无命中词",
                 ("" if not adjs else "；修正 " + ", ".join(
                     "%s%+.2f" % (a["id"], a["score"]) for a in adjs)),
                 mag, value))
    if dsrc == "sentiment_fallback":
        reason += "；★ 正文无方向信号，方向靠 sentiment 兜底，请按低置信处理"

    return {
        "value": value,
        "bin": to_bin(value, cfg),
        "direction": sign,
        "direction_source": dsrc,
        "magnitude": round(mag, 4),
        "base_id": base.get("id"), "base_w": float(base.get("w", 0.0)),
        "base_match": bmatch, "base_explicit": base_explicit,
        "adjustments": [{"id": a["id"], "w": a["score"], "match": a["match"],
                         "why": a["why"]} for a in adjs],
        "confidence": conf,
        "evidence": ev[:8],
        "reason": reason,
        "provenance": "extracted",
        "subject_axis": "对事件主体的公开行为承诺强度（-1=站质疑方 / +1=站辩护方）",
    }


def to_bin(value, cfg, prev_bin=None):
    """连续值 -> 三值分档，带【滞回】。

    进入用 enter(±0.35)，退出用 exit(±0.25)。单向阈值会让 value 在 ±0.35 附近抖动，
    同一个人连续几轮换标签，LLM 的措辞跟着抽风。t0 没有上一轮，直接按 enter 分档。
    """
    if value is None:
        return None
    b = cfg.get("bins") or {}
    enter = float(b.get("enter", 0.35))
    exit_ = float(b.get("exit", 0.25))
    if prev_bin == "支持":
        return "中立" if value < exit_ else "支持"
    if prev_bin == "反对":
        return "中立" if value > -exit_ else "反对"
    if value >= enter:
        return "支持"
    if value <= -enter:
        return "反对"
    return "中立"


# ---------------- 没有真实帖文的人：槽位采样 ----------------

def sample_from_slot(side, stance_bias, role_type, cfg, rng):
    """给【没有真实帖文】的 roster 成员采样一个 t0 立场值（provenance=generated）。

    side        槽位立场侧：pro / counter / neutral
    stance_bias 画像库给的先天倾向：倾向维权 / 中立 / 倾向企业
    rng         prng.DeterministicRNG 实例（必须传，否则不可复现）

    三条取值纪律（config/stance.json 的 slot_sampling.discipline）：
      · 不能给点值 —— 全员 -0.6 会在同一轮集体越线
      · 不能给满值 —— ±1.0 没有上浮空间，官方回应永远推不动他
      · 官方角色向 0 收缩 —— 媒体/监管首轮不表态
    """
    s = cfg.get("slot_sampling") or {}
    mu = float((s.get("mu") or {}).get(side, 0.0))
    sigma = float((s.get("sigma") or {}).get(side, 0.15))
    shift = float((s.get("bias_shift") or {}).get(stance_bias or "中立", 0.0))
    shrink = float((s.get("role_shrink") or {}).get(role_type, 1.0))
    v = (mu + shift + rng.gauss(0.0, sigma)) * shrink
    return round(max(-1.0, min(1.0, v)), 4)


def aggregate(values, cfg=None):
    """同一作者同一渠道的多帖合并成一个发帖动作时，立场取【均值】。

    与 topic_list 的处理方式一致：合并值 + 逐帖明细都留下，便于溯源。
    """
    vs = [v for v in values if v is not None]
    if not vs:
        return None
    return round(sum(vs) / len(vs), 4)


def committed_params(cfg, rigidity=None, step_cap=None):
    """已在 t0 公开表态的人：刚性上调、单轮步长下调（承诺一致性成本）。

    不加这条会出现「t0 发帖骂完，T2 就替公司说话」的荒谬演化。
    """
    c = cfg.get("committed") or {}
    out = {"committed": True}
    if rigidity is not None:
        out["stance_rigidity"] = round(
            min(float(c.get("max_rigidity", 0.95)),
                float(rigidity) + float(c.get("rigidity_bonus", 0.20))), 4)
    if step_cap is not None:
        out["stance_step_cap"] = round(
            float(step_cap) * float(c.get("step_cap_factor", 0.6)), 4)
    return out


# ---------------- 自测 ----------------

def self_test(cfg, comment_cfg=None):
    """回归样例：事件1 的**三条真实 t0 帖** + 三条构造样例。

    前三条是实测踩到的坑，必须逐条对上 —— 它们同时证明了 sentiment != stance：
      长城网 sentiment=中性，标定出的立场是弱负向 -0.17，而不是 0。
      （注意它落在【中立】档里：这正是为什么需要连续值 ——
        0.00 与 -0.17 在分档上看不出差别，但离翻转的距离完全不同。）
    """
    cases = [
        ({"title": "", "sentiment": "负面", "poster_role": "网民", "raw_content":
          "见不见啊？前有女性下体隐私手术被直播，后有众安保险把女性下体p身上。妈妈给我买个宠物险吧。"
          "每月一瓶矿泉水，生病就多了一份保障了。众安保险：每月1.5元，赔付总限额15000。你没事吧？"
          "你难道不知道宠物保险大多数都是女生在买吗？你这就是属于公然挑衅、公然侮辱。"
          "\n【高赞评论1】@刚刚才退：我买的就是众安的，我会选择退保"},
         "网民", -0.77, "反对", "high",
         "刘嘻嘻：明确指控 + 人格否定（评论块里的当事人信号必须切掉）"),

        ({"title": "", "sentiment": "中性", "poster_role": "媒体", "raw_content":
          "5月21日，某宠物险广告将女性私密部位P图至狗身上，网友怒批“低俗出格”，涉事公司回应："
          "第三方误发AI生成图，已下线。#宠物 （编辑：涵涵）"
          "\n【高赞评论3】@氧气yyyyy：操，我买的就是众安的，我会选择退保"},
         "媒体", -0.17, "中立", "medium",
         "长城网：★ sentiment=中性，但立场是弱负向 —— 媒体是载体，不是中立者"),

        ({"title": "", "sentiment": "负面", "poster_role": "网民", "raw_content":
          "保险公司好歹也是金融公司，做金融的严谨性合规性呢？犯这么低级的错误实在令人无语！"
          "而且这种时候甩锅给第三方，也真的是一点担当也没有了\n#宠物险 #宠物保险"},
         "网民", -0.77, "反对", "high",
         "豆豆大侠：直接指控 + 推卸责任 + 负面情绪"),

        ({"title": "", "sentiment": "正面", "poster_role": "网民", "raw_content":
          "众安这次处理挺及时的，图确实是第三方误发的，已经下架整改了。别被带节奏，人家按条款办事没毛病"},
         "网民", 0.77, "支持", "high",
         "正向帖：澄清/带节奏 + 按条款 + 没毛病 -> 明确支持（与负向对称）"),

        ({"title": "", "sentiment": "负面", "poster_role": "网民", "raw_content":
          "有人知道众安这个广告到底是怎么回事吗？求科普"},
         "网民", 0.0, "中立", "low",
         "求证帖：sentiment=负面 但还没站队 -> 立场 0（★ 情绪 != 立场）"),

        ({"title": "", "sentiment": "中性", "poster_role": "监管", "raw_content":
          "关于某宠物险广告事件的通报：已关注到相关情况，正在核实中。"},
         "监管", 0.0, "中立", "low",
         "监管通报：未表态形式，无方向信号 -> 0（首轮不表态）"),
    ]

    ok = 0
    print("  %-5s %-6s %-18s %-8s %-6s %-7s %s"
          % ("", "角色", "期望值/分档", "实得值", "分档", "置信", "说明"))
    for doc, role, want_v, want_bin, want_conf, why in cases:
        r = calibrate(doc, cfg, {"role_effective": role}, comment_cfg)
        good = (abs(r["value"] - want_v) < 1e-6 and r["bin"] == want_bin
                and r["confidence"] == want_conf)
        ok += good
        print("  [%s] %-4s %-18s %-8s %-6s %-7s %s"
              % ("OK" if good else "FAIL", role,
                 "%.2f/%s/%s" % (want_v, want_bin, want_conf),
                 "%.2f" % r["value"], r["bin"], r["confidence"], why))
        if not good:
            print("        实际: %s" % r["reason"])
    print("  自测：%d/%d 通过" % (ok, len(cases)))
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config
    print("stance_value 自测：")
    raise SystemExit(0 if self_test(load_config("stance"), load_config("poster_stance")) else 1)
