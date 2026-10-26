#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t0_real_personas.py v2 —— 由 GraphRAG 推导 t0 真实人物

★ v1 的问题（用户指出）：只是个字段搬运工 —— author_fans -> followers，
  其余骨架从画像库里【随机认领一个同角色同渠道的人】。那些属性与帖文毫无关系。

★ v2 的原则：**属性能从【帖文 + 图谱】推出来的，就推；推不出来的标 unknown，不从画像库借。**

推导链（全部来自 GraphRAG 对事件的理解）：
  ① KG.post_issues[post_id]        他这条帖涉及哪些议题
        -> 47 话题（general_bridge）-> interest_topics（按议题 heat 加权）
        -> concern_tier（按 heat 分档）
  ② KG.issues[].heat_hist          议题在本场的权重（含历史 32 事件的加成）
  ③ publish_time 的【时分】         发布时刻 = 他在线的铁证 -> active_hours
  ④ 帖文 stance_value               -> stance_base / stance_bias
  ⑤ 源数据 sentiment                -> sentiment_baseline
  ⑥ 正文 / 昵称                     从业身份 -> occupation（仅当有明确线索）
  ⑦ 机构账号 + 报道正文              -> org_type / org_scope
  ⑧ topic_tag                      hashtag 是强话题信号，与 ① 交叉验证

推不出的字段一律 None，并写进 unknown_fields —— 事件没给，就不编。
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config  # noqa: E402

# 昵称/正文 -> 从业身份（只在有明确线索时才推）
OCC_HINTS = [
    (r"宠物", "宠物行业"),
    (r"律师|法务", "法律"),
    (r"医生|医师|药师|护士", "医护"),
    (r"教师|老师|教授", "教师"),
    (r"记者|媒体|新闻|日报|晚报|网$|电视台|广播", "新闻媒体"),
    (r"保险", "保险行业"),
    (r"程序员|开发|工程师|IT", "IT"),
]
# 机构账号：报道范围（从正文推）
SCOPE_HINTS = [
    (r"央视|人民日报|新华社|中新社|环球时报", "央媒/全国"),
    (r"省|市|区|县|长城网|日报|晚报|都市报", "地方/省级"),
    (r"自媒体|博主|号", "自媒体"),
]


def parse_fans(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    m = re.match(r"^([\d.]+)\s*(万|亿)?", s) if s else None
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2)
    if u == "万":
        n *= 10000
    elif u == "亿":
        n *= 100000000
    return int(n)


def fmt_fans(n):
    if n is None:
        return "—"
    if n >= 100000000:
        return "%.1f亿" % (n / 100000000.0)
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    return str(n)


def parse_hour(pt):
    """从 publish_time 抽小时。只有落在 [1,23] 的时分才算铁证（00 点可能是缺省值）。"""
    if not pt:
        return None
    m = re.search(r"\s(\d{1,2}):(\d{2})", str(pt))
    if not m:
        return None
    h = int(m.group(1))
    return h if 1 <= h <= 23 else None


# ★ 真实人物 gender 推断阈值（|得分| 超过才判定，中间仍标 unknown）
# ★★ 2026-10-07 新增：从【帖文】推断 t0 真实人物的年龄段。
#   起因：「刘嘻嘻」的帖文写「妈妈给我买个宠物险吧」（在对妈妈说话），
#        却被画像库随机抽成 64 岁退休教师 —— 人设与帖文直接冲突。
#   规则：按【从具体到宽泛】的顺序匹配，命中即用；一条都不命中则返回 None，
#        由 inject_real_personas 用「年轻默认段」兜底（不在这里硬猜）。
AGE_HINTS = [
    (r"我孙子|我孙女|我外孙|我外孙女|孙子上|孙女上",            55, 72, "提到孙辈"),
    (r"退休|退了休|办退休|养老金",                             58, 72, "自称退休"),
    (r"我儿子|我女儿|我家孩子|孩子上(学|初中|高中|小学)|带娃|娃上学", 30, 45, "提到子女"),
    (r"我们学校|我们班|同学|宿舍|大四|大三|大二|大一|读研|考研|应届|刚毕业|实习", 19, 26, "学生/应届"),
    (r"妈妈给我|爸爸给我|我妈给我|我爸给我|家里给我|让我妈|跟我妈说", 18, 26, "对父母说话（尚未独立）"),
    (r"上班|工位|加班|我们领导|同事|KPI|周会|通勤",              24, 40, "职场人"),
]
GENDER_INFER_TH = 0.30


def build(kg, t0, pack, alias=None, org=None):
    """从 t0 帖文推导真实人物 —— 返回 list[dict]，每条一个人的全部推导结果。

    ★ 可复用：run.py 通过 inject_real_personas 调用它，CLI 也调用它。
    alias / org 不传则走 config/ 加载器（支持两种配置布局）。
    """
    if alias is None:
        alias = load_config("topic_alias")
    if org is None:
        org = load_config("org_persona")
    # ---- 图谱侧：议题 heat -> 47 话题
    l2t = {}
    for tp, ls in (alias.get("general_bridge") or {}).items():
        for lf in (ls or []):
            l2t.setdefault(lf, tp)
    VOCAB = load_config("topic_vocab")
    heat_of = {}
    for it in (kg.get("issues") or []):
        heat_of[it["leaf"]] = float(it.get("heat_hist") if it.get("heat_hist") is not None
                                    else (it.get("heat") or 0.0))
    post_issues = kg.get("post_issues") or {}
    post_issues_src = kg.get("post_issues_src") or {}
    out = []
    docs = {d["doc_id"]: d for d in (pack.get("docs") or [])}
    for ip in (t0.get("initial_posts") or []):
        name = ip.get("poster_name") or "匿名"
        role = ip.get("poster_role_effective") or ip.get("poster_role_declared") or "网民"
        ch = ip.get("channel")
        is_org = role in ("媒体", "监管")
        pid = (ip.get("post_ids") or [None])[0]
        d = docs.get(pid) or {}
        derived, unknown = {}, []

        # ① 议题 -> interest_topics / concern_tier
        # ★★ 修正：权重来自【这条帖自己提到了什么】，不是事件热度。
        #    帖文侧的三路信号，按强度加权：
        #      from_topic_hit  采集侧标注的关键词命中（最贴"他这条帖在说什么"）  权重 3
        #      topic_tag       hashtag（作者主动打的标签）                      权重 2
        #      from_text       正文/标题全文抽取                                 权重 1
        #    事件热度 heat 不再参与 —— 那是【事件】的属性，不是【这个人】的。
        src = post_issues_src.get(pid) or {}
        # ★★ 三路信号，全部来自【这条帖自己提到了什么】—— 事件热度不参与
        #    ① topic_vocab 命中：作者明确写到的词（title/content/topic_tag/extract_entity）  权重 3
        #    ② KG.from_topic_hit：采集侧标注的关键词归一出的议题                            权重 2
        #    ③ KG.from_text：正文全文抽取归一出的议题                                       权重 1
        SCAN = VOCAB.get("scan_fields") or ["title", "content", "topic_tag", "extract_entity"]
        blob = " ".join(
            (d.get(f) if not isinstance(d.get(f), list) else " ".join(str(z) for z in d[f])) or ""
            for f in SCAN)
        tw, sig = {}, {}
        for L in (VOCAB.get("layers") or []):
            for t_ in (L.get("terms") or []):
                feeds = t_.get("feeds") or []
                if not feeds:
                    continue
                pat = t_.get("evidence") or t_.get("term") or ""
                ok = False
                try:
                    ok = bool(re.search(pat, blob))
                except re.error:
                    ok = pat in blob
                if not ok:
                    continue
                for lf in feeds:
                    tp47 = l2t.get(lf)
                    if tp47:
                        tw[tp47] = tw.get(tp47, 0.0) + 3.0
                        sig.setdefault(tp47, []).append("帖文提到「%s」" % t_.get("term"))
        for lf in (src.get("from_topic_hit") or []):
            tp47 = l2t.get(lf)
            if tp47:
                tw[tp47] = tw.get(tp47, 0.0) + 2.0
                sig.setdefault(tp47, []).append("采集侧 topic_hit")
        for lf in (src.get("from_text") or []):
            tp47 = l2t.get(lf)
            if tp47:
                tw[tp47] = tw.get(tp47, 0.0) + 1.0
                sig.setdefault(tp47, []).append("正文抽取")
        topics = sorted(tw, key=lambda x: -tw[x])
        tot = sum(tw.values()) or 1.0
        it = {t_: round(tw[t_] / tot, 3) for t_ in topics}
        order = topics
        tier = {}
        for i, t_ in enumerate(order):
            tier[t_] = "key" if i == 0 else ("mild" if i == 1 else "weak")
        derived["interest_topics"] = it
        derived["concern_tier"] = tier
        derived["_interest_topics_source"] = ("帖文自身三路信号加权：topic_vocab 明确提到的词×3 ／ "
                                             "采集侧 topic_hit×2 ／ 正文抽取×1。与事件热度无关。")
        derived["_interest_topics_signals"] = {k: sorted(set(v)) for k, v in sig.items()}

        # ② 发布时刻 -> active_hours（铁证）
        # ★★ active_hours = 【在线小时的列表】（24 小时制，本地时间）—— 不是强度向量
        h = parse_hour(ip.get("publish_time"))
        if h is not None:
            derived["active_hours"] = [h]
            derived["_active_hours_source"] = ("publish_time 的时分 = 该时刻必然在线（%s 发的帖）"
                                               % ip.get("publish_time"))
        elif is_org:
            hrs = ((org.get(role) or {}).get("hours") or [[9, 10, 11, 14, 15, 16, 17]])[0]
            derived["active_hours"] = sorted(int(z) for z in hrs)
            derived["_active_hours_source"] = "源数据只有日期无时分；机构账号按值班时段推定"
        else:
            unknown.append("active_hours")
            derived["_active_hours_source"] = ("源数据只有日期无时分（%s）—— 无法推定，标 unknown"
                                               % ip.get("publish_time"))

        # ③ 立场 / 情绪（实测）
        if ip.get("stance_value") is not None:
            derived["stance_base"] = ip["stance_value"]
            derived["stance_bias"] = {str(x): ip["stance_value"] for x in (kg.get("issues") and [i["leaf"] for i in kg["issues"]] or [])}
            derived["_stance_source"] = "帖文实测 stance_value"
        if d.get("sentiment"):
            derived["sentiment_baseline"] = d["sentiment"]

        # ④ 粉丝 / IP（实测）
        fans = parse_fans(d.get("author_fans"))
        if fans is not None:
            derived["followers"] = fans
        if d.get("author_ip_location"):
            derived["region"] = d["author_ip_location"]

        # ⑤ 从业身份（仅当有明确线索）
        # ★ 从业身份【只从昵称推】—— 扫正文会把「提到宠物险的媒体」误判成宠物行业（实测踩过）
        #   且机构账号本来就不该有 occupation（组长要的是 org_type / org_scope）
        if is_org:
            unknown.append("occupation")
        else:
            for pat, occ in OCC_HINTS:
                if re.search(pat, name):
                    derived["occupation"] = occ
                    derived["_occupation_source"] = "昵称线索「%s」" % pat
                    break
            else:
                unknown.append("occupation")

        # ⑥ 机构账号：报道范围
        if is_org:
            blob = "%s %s" % (name, ip.get("content") or "")
            for pat, sc in SCOPE_HINTS:
                if re.search(pat, blob):
                    derived["org_scope"] = sc
                    derived["_org_scope_source"] = "报道正文线索「%s」" % pat
                    break
            derived["org_type"] = "新闻媒体" if role == "媒体" else "监管机构"
            derived["_org_type_source"] = "角色 = %s" % role

        # ⑦ hashtag 交叉验证
        tt = d.get("topic_tag")
        if tt:
            derived["_topic_tag"] = tt

        # ★★ 2026-10-07 真实人物：gender 从【帖文命中的话题】推断（用户口径）
        #   判据：该人帖文涉及的话题里，女性高关注议题占多少（FEM_TOPIC 加权得分）
        #   得分 > 阈值 -> 女；< -阈值 -> 男；中间 -> 仍标 unknown（不硬猜）
        if not is_org:
            _ft = (load_config("gender_topic") or {}).get("map") or {}
            _nz = [(t, w) for t, w in (derived.get("interest_topics") or {}).items()
                   if t != "其他" and (w or 0) > 0]
            _sw = sum(w for _, w in _nz)
            _gscore = (sum(w * _ft.get(t, 0.0) for t, w in _nz) / _sw) if _sw > 0 else 0.0
            if _gscore >= GENDER_INFER_TH:
                derived["gender"] = "女"
            elif _gscore <= -GENDER_INFER_TH:
                derived["gender"] = "男"
            if "gender" in derived:
                derived["_gender_source"] = ("帖文话题性别倾向 %+.2f（阈值 ±%.2f）" % (_gscore, GENDER_INFER_TH))

        # ★★ 2026-10-07 新增 ⑦b：从帖文推年龄（机构号不推）
        if not is_org:
            _blob = "%s %s %s" % (name, ip.get("content") or "", d.get("title") or "")
            for _pat, _lo, _hi, _why in AGE_HINTS:
                _m = re.search(_pat, _blob)
                if _m:
                    derived["age"] = (_lo + _hi) // 2
                    # ★★ 2026-10-07：显式标记【硬证据】—— 表示「此人不可能年轻」，
                    #   注入阶段的 24~30 岁钳制要放行。
                    #   不要用「_age_source 里是否含退休二字」来判断 —— 那句话本身
                    #   就带「退休教师」字样，会把普通人全误判成老年人（已踩过）。
                    if _why in ("提到孙辈", "自称退休"):
                        derived["_age_senior"] = True
                    derived["_age_source"] = ("帖文线索「%s」（%s）-> 推定 %d~%d 岁，取中位 %d"
                                              % (_m.group(0), _why, _lo, _hi, (_lo + _hi) // 2))
                    break
            else:
                unknown.append("age")
                derived["_age_source"] = "帖文无年龄线索 —— 交由注入阶段用【年轻默认段】补全，不从画像库随机借（否则会出现「64 岁退休教师说妈妈给我买保险」）"

        # ⑧ 事件里没有的字段，一律标 unknown，不从画像库借
        for f in ("gender", "education", "income_band", "marital_status",
                  "urban_tier", "interest_circle", "city"):   # age 已在 ⑦b 处理
            unknown.append(f)

        # ★ ⑨ 已被推断出来的字段，从 unknown 里摘掉（上面的 ⑧ 是无条件加的）
        for _f in ("gender", "age"):
            if _f in derived and _f in unknown:
                unknown.remove(_f)

        out.append({
            "agent_id": "%s_%s_%s" % (ch, role, name),
            "role_type": role, "primary_channel": ch,
            "is_t0_real": True,
            # ★★ 2026-10-13 修 bug：t0 真实人物 = 在 t0 发帖的真实的人，他扮演的就是【他自己】。
            #   原先取 ip.get("plays_entity") —— 那是 t0_init 把 roster 的 plays_entity 回填到
            #   initial_posts 上的值，语义是【该帖被绑定到的槽位实体】，可能是帖文里被提到的
            #   第三方（EVT-002 的「谭先生」）。于是冒出幽灵记录：
            #     agent_id=新闻门户_媒体_千思律师事务所  plays_entity=谭先生  ← 名册里根本没这人
            "plays_entity": ip.get("poster_name") or name,
            "t0_author_uid": ip.get("poster_author_id"),
            "t0_publish_time": ip.get("publish_time"),
            "t0_content": ip.get("content"),
            "t0_title": d.get("title"),
            "t0_source_urls": ip.get("source_urls") or [],
            "t0_engagement": ip.get("engagement"),
            "t0_stance_bin": ip.get("stance_bin"),
            "t0_stance_value": ip.get("stance_value"),
            "t0_poster_stance": ip.get("poster_stance"),
            "derived": derived,
            "unknown_fields": sorted(set(unknown)),
            "_note": "全部属性由 GraphRAG 从【帖文 + 知识图谱】推导；unknown_fields 是事件数据里确实没有的，不编。",
        })
    out = mentioned_personas(kg, out)
    return out


def mentioned_personas(kg, out):
    """★★ 2026-10-13 新增：为「被帖文提到、但自己没发帖」的当事人补画像。

    为什么需要：EVT-002 的「谭先生」是整场拒赔事件的当事人（被 4 条帖文提到），
    但他不发帖，旧 build() 抓不到他 —— 结果他的 3 个客户槽位由画像库里随机的人
    扮演，属性与事件毫无关系。推演里最关键的角色却在裸奔。
    """
    have = {x.get("plays_entity") for x in out}
    added = 0
    for e in (kg.get("named_entities") or []):
        nm = e.get("name")
        if not nm or nm in have:
            continue
        if e.get("kind") != "person" or e.get("is_author"):
            continue
        if not e.get("role"):
            continue
        out.append({
            "agent_id": nm,
            "role_type": e.get("role"),
            "primary_channel": e.get("channel"),
            "is_t0_real": True,
            "t0_mentioned": True,
            "plays_entity": nm,
            "t0_author_uid": None, "t0_publish_time": None, "t0_content": None,
            "t0_source_urls": [], "t0_engagement": 0,
            "derived": {"interest_topics": {}, "concern_tier": {}},
            "unknown_fields": ["age", "city", "education", "gender", "income_band",
                               "interest_circle", "marital_status", "occupation", "urban_tier"],
            "_note": "被帖文提到的当事人（自己没发帖）。属性由人工推断表提供，未命中走默认补全。",
        })
        added += 1
    if added:
        print("  ★ 补出 %d 个「被提到的当事人」：%s" % (added, "、".join(
            x["plays_entity"] for x in out if x.get("t0_mentioned"))))
    return out


def main():
    ap = argparse.ArgumentParser(description="从 t0 帖文推导真实人物（不注入名册）")
    ap.add_argument("--kg", required=True)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--topic-alias", default=None,
                    help="可选：单独的 topic_alias.json 路径；不传则走 config/ 加载器")
    ap.add_argument("--org-persona", default=None,
                    help="可选：单独的 org_persona.json 路径；不传则走 config/ 加载器")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    J = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    alias = J(a.topic_alias) if a.topic_alias else None
    org = J(a.org_persona) if a.org_persona else None
    out = build(J(a.kg), J(a.t0), J(a.pack), alias, org)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({
        "artifact": "t0_real_personas", "version": "2.0", "count": len(out),
        "principle": "属性能从帖文+图谱推出来的就推；推不出来的标 unknown，不从画像库借",
        "agents": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    _print(out)
    return 0


def _print(out):
    """CLI 打印推导结果"""
    for x in out:
        dv = x["derived"]
        print("\n【%s】%s / %s" % (x["plays_entity"], x["role_type"], x["primary_channel"]))
        print("    interest_topics  %s   <- %s" % (json.dumps(dv.get("interest_topics"), ensure_ascii=False),
                                                   dv.get("_interest_topics_source", "")))
        print("    concern_tier     %s" % json.dumps(dv.get("concern_tier"), ensure_ascii=False))
        if "active_hours" in dv:
            print("    active_hours     %s   <- %s" % (dv["active_hours"], dv.get("_active_hours_source")))
        else:
            print("    active_hours     unknown   <- %s" % dv.get("_active_hours_source"))
        print("    stance_base      %s  (%s)" % (dv.get("stance_base"), dv.get("_stance_source", "")))
        print("    followers        %s   region %s" % (fmt_fans(dv.get("followers")), dv.get("region") or "—"))
        if dv.get("occupation"):
            print("    occupation       %s   <- %s" % (dv["occupation"], dv.get("_occupation_source")))
        if dv.get("org_scope"):
            print("    org_type/scope   %s / %s" % (dv.get("org_type"), dv["org_scope"]))
        if dv.get("_topic_tag"):
            print("    topic_tag        %s" % dv["_topic_tag"])
        print("    ★ unknown        %s" % ", ".join(x["unknown_fields"]))


if __name__ == "__main__":
    raise SystemExit(main())
