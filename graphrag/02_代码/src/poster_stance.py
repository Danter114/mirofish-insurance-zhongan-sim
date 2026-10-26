#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poster_stance.py —— t0 首发发帖者类型判定：当事人型（客户）还是围观型（网民）

组长要求：t0 状态要标注**首发发帖者的类型**，并传给下游推理演化层。
判据不是账号标签，而是**帖子内容本身**：

    自己受损 / 自己要投诉   -> 当事人型 -> 角色取【客户】
    仅评论事件 / 宣泄情绪   -> 围观型   -> 角色取【网民】

为什么必须按内容判：
  · 采集侧给的 author_label 是账号画像标签，不是这一帖的姿态。
    「网民」标签的人在理赔维权帖里就是当事人；「客户」标签的人在转通稿时也只是围观。
  · 下游推理演化层要用它决定初值：当事人型的人起始效价更低、动作阈值走客户那一档
    （组长 2.3 ④：投诉阈值只对客户有意义）；围观型只走转发/评论那一档。

两个必须处理的坑（事件1 实测踩到）：
  ① **评论块要先切掉**。EV1_XHS_001 里真正的当事人信号「我买的就是众安的…我会选择退保」
     躺在【高赞评论3】里 —— 那是网友说的，不是发帖人说的。不切掉就会把长城网判成客户。
  ② **引用广告文案要排除**。EV1_DY_001 的「妈妈给我买个宠物险吧」是广告原文，
     含「我买」二字。所以投保类证据必须排除「给/让/帮/替 + 我买」这种被使役句。

实现：纯正则 + 加权计分，离线、可复现、零成本。词表在 config/poster_stance.json。
"""
from __future__ import annotations

import re

# 采集侧的角色标签 -> 6 类标准角色（与 tools/adapt_collect.py 保持同一套口径）。
# 判定前先归一，否则「媒体（新闻）」这种带括注的标签会漏进自然人分支被判成网民。
ROLE_ALIAS = {
    "网民": "网民", "普通用户": "网民", "用户": "网民", "黄V": "KOL",
    "KOL": "KOL", "达人": "KOL", "大V": "KOL", "KOC": "KOL",
    "媒体": "媒体", "媒体（新闻）": "媒体", "媒体(新闻)": "媒体",
    "媒体（中央）": "媒体", "媒体(中央)": "媒体", "媒体（地方）": "媒体",
    "监管": "监管", "监管机构": "监管", "政务": "监管", "法院": "监管", "检察院": "监管",
    "客户": "客户", "当事人": "客户", "消费者": "客户",
    "专业背书方": "专业背书方", "律师": "专业背书方", "学者": "专业背书方",
}


def normalize_role(label):
    return ROLE_ALIAS.get(str(label or "").strip(), str(label or "").strip())


def _rules(cfg, key):
    return [(re.compile(r["re"], re.I), r.get("w", 1), r.get("why", ""), r["re"])
            for r in cfg.get(key, [])]


def strip_comment_blocks(text, cfg):
    """切掉【高赞评论】【热门评论】这类**别人说的话**，只留发帖人自己的文本。"""
    t = text or ""
    for marker in cfg.get("comment_blocks", []):
        i = t.find(marker)
        if i >= 0:
            t = t[:i]
    return t.strip()


def classify(doc, cfg, declared_role=None):
    """返回发帖姿态判定结果。doc 需要 title / content（有 raw_content 优先）。"""
    declared = normalize_role(declared_role or doc.get("poster_role") or "")
    text = " ".join([(doc.get("title") or ""), strip_comment_blocks(
        doc.get("raw_content") or doc.get("content") or "", cfg)])
    text = " ".join(text.split())

    if declared in (cfg.get("institutional_roles") or []):
        return {"stance": "institutional", "role_declared": declared,
                "role_effective": declared, "role_overridden": False, "confidence": "high",
                "self_score": 0, "commentary_score": 0, "evidence": [],
                "reason": "机构号（%s）：按账号身份定角色，不按帖子内容改判" % declared}

    ev, s = [], 0
    for rx, w, why, raw in _rules(cfg, "self_interest"):
        m = rx.search(text)
        if m:
            ev.append({"side": "self_interest", "match": m.group(0), "why": why, "w": w})
            s += w
    c, cev = 0, []
    for rx, w, why, raw in _rules(cfg, "commentary"):
        m = rx.search(text)
        if m:
            cev.append({"side": "commentary", "match": m.group(0), "why": why, "w": w})
            c += w

    th = cfg.get("thresholds") or {}
    strong, alone = th.get("self_strong", 2), th.get("self_alone", 1)
    if s >= strong or (s >= alone and c == 0):
        stance, role, conf = "self_interest", "客户", "high"
        reason = "当事人型：帖子里有自己受损/自己维权的证据（self %d > commentary %d）" % (s, c)
    elif c >= 1:
        stance, role, conf = "commentary", "网民", "high"
        reason = "围观型：只有评论/宣泄/转述证据，没有自身利益受损（self %d, commentary %d）" % (s, c)
    else:
        # 两侧都没命中：按组长给的那条二分法（「自己受损 -> 客户，否则 -> 网民」）落到基线，
        # 但把 confidence 标成 low —— 下游看得到这是「默认值」而不是「判定值」。
        stance, role, conf = "commentary", "网民", "low"
        reason = ("证据不足：既没有自身受损证据、也没有明显评论措辞，"
                  "按基线判围观型（网民）；confidence=low，请下游按低置信处理")
    return {"stance": stance, "role_declared": declared, "role_effective": role,
            "role_overridden": bool(declared) and role != declared,
            "confidence": conf, "self_score": s, "commentary_score": c,
            "evidence": (ev + cev)[:12], "reason": reason}


def self_test(cfg):
    """回归样例：三正三负，跑一遍就知道词表有没有被改坏。"""
    cases = [
        ({"title": "", "content": "我买的众安宠物险，狗子髌骨手术他们拒赔，我要投诉"},
         "网民", "客户", "self_interest", "当事人自述被拒赔"),
        ({"title": "", "content": "众安这个广告太低俗了，建议严查，抵制这家公司"},
         "客户", "网民", "commentary", "客户标签但只是围观评论"),
        ({"title": "", "content": "妈妈给我买个宠物险吧，每月一瓶矿泉水"},
         "网民", "网民", "commentary", "引用广告文案的『给我买』不能当自述投保（低置信基线）"),
        ({"title": "", "content": "众安回应了。正文略。【高赞评论1】@某人：我买的就是众安的，我退保"},
         "媒体", "媒体", "institutional", "评论里的当事人信号不属于发帖人（且媒体是机构号）"),
        ({"title": "", "content": "闲而无事翻朋友圈已圆寂，赔我点钱吧"},
         "网民", "网民", "commentary", "玩笑式『赔我』不算自述受损（低置信基线）"),
        ({"title": "", "content": "我在黑猫投诉了众安，保单号 xxx，一直没人理我"},
         "网民", "客户", "self_interest", "自述在投诉平台立案"),
    ]
    ok = 0
    for doc, declared, want_role, want_stance, why in cases:
        r = classify(doc, cfg, declared)
        good = (r["role_effective"] == want_role and r["stance"] == want_stance)
        ok += good
        print("   [%s] %-28s 声明=%-5s -> %-5s / %-14s  (%s)"
              % ("OK" if good else "FAIL", why[:28], declared, r["role_effective"],
                 r["stance"], r["reason"][:44]))
    print("   自测：%d/%d 通过" % (ok, len(cases)))
    return ok == len(cases)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_config
    raise SystemExit(0 if self_test(load_config("poster_stance")) else 1)
