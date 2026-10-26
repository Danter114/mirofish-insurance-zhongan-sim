#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapt_collect.py —— 采集侧「扁平一事件一文件」格式 -> 本管线标准事件包

采集侧格式（事件1建图尝试/event1.json）：一个 JSON 数组，每条 = 一条帖子
本管线格式：{meta, events:[...], docs:[...]}

只做字段名映射 + 角色/渠道归一，不做任何内容加工 —— 内容加工在建图阶段。

与 adapt_pilot.py 的差别（这是通用版）：
  1. author_label 词表扩到「网民 / 媒体（新闻）/ 媒体（中央）/ KOL / 监管 / 客户 ...」
  2. author_uid 大面积缺失时，用 渠道+昵称 生成**代理主体 ID**（并在字段里标明是代理），
     否则发声主体全部丢失，M4 的「发声主体」这一类会整类消失
  3. 保留采集侧自带的 event_t0 / timespan_days / is_origin / 互动量，供 t0 口径与热度使用
"""
from __future__ import annotations

import argparse
import json
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from common import load_config  # noqa: E402

# 采集侧 source_channel -> 标准 6 渠道
CHANNEL_MAP = {
    "微博": "微博", "抖音": "抖音", "小红书": "小红书", "新闻门户": "新闻门户",
    "黑猫投诉": "黑猫投诉", "消费保": "黑猫投诉", "监管通报": "监管通报",
    "国家金融监督管理总局": "监管通报", "金融监管总局": "监管通报",
    # ★ 2026-10-13：采集侧把中央级媒体标成「党央媒」，原先没映射 -> 全部落进「其他」渠道
    "党央媒": "新闻门户", "中央媒体": "新闻门户", "央媒": "新闻门户",
    # ★★ 2026-10-14 端到端口径统一：采集侧的「监管渠道」-> 监管通报（**不是**新闻门户）。
    #   实证（全量普查 12 事件 / 313 帖）：采集侧成品的 channel 只有 6 个取值 ——
    #     微博 190 / 新闻门户 52 / **监管渠道 33** / 抖音 22 / 黑猫投诉 13 / 小红书 3。
    #     除「监管渠道」外全部已在上表，所以这就是**唯一**的词表缺口（原先静默落「其他」）。
    #   这 33 条里 32 条 role_type=监管，作者是 央广网/新华社/央视网/央视财经/人民日报/光明网/
    #     经济日报/人民网 等 —— 即采集侧用它标【监管口】。
    #   依据：交付 config/slots.json v6.0 的 role_channel_note ——
    #     「3 个专属场：黑猫投诉=客户 / 新闻门户=媒体 / 监管通报=监管」，且 role_channel 里
    #     监管只有「监管通报」一格（媒体才占新闻门户）。映射到新闻门户会同时造出两个毛病：
    #     ① 33 条帖被搬错场；② 产出 (监管, 新闻门户) 这种矩阵外组合。
    #   ⚠️ 别与上面那条「党央媒 -> 新闻门户」混为一谈：那条说的是【内容上属于中央媒体】，
    #      本条说的是【发布口属于监管通道】。两者在采集侧是两个不同的词，不要互相顶替。
    "监管渠道": "监管通报",
}

# 采集侧 author_label -> 6 类标准角色（v2.0：KOC 已删，其放大职能由 KOL 承接 —— 组长 5.2 ①）
LABEL_TO_ROLE = {
    "网民": "网民", "普通用户": "网民", "用户": "网民", "黄V": "KOL",
    "KOL": "KOL", "达人": "KOL", "大V": "KOL",
    "KOC": "KOL",
    "媒体": "媒体", "媒体（新闻）": "媒体", "媒体(新闻)": "媒体",
    # ★ 2026-10-13 用户口径：中央级媒体 / 司法 / 监管机构 -> 监管
    "媒体（中央）": "监管", "媒体(中央)": "监管", "媒体（地方）": "媒体",
    "党央媒": "监管", "中央媒体": "监管", "央媒": "监管",
    "监管": "监管", "监管机构": "监管", "政务": "监管", "法院": "监管", "检察院": "监管",
    "客户": "客户", "当事人": "客户", "消费者": "客户",
    # ★ 组长决定：专业背书方角色删除（与其他角色共线性过大），归入 KOL
    #   理由：KOC 与 KOL 已合并，该档本已含财经/法律/医疗类专业型达人
    "专业背书方": "KOL", "律师": "KOL", "学者": "KOL", "专家": "KOL", "医生": "KOL",
}

MEDIA_NAME_RX = re.compile(r"(网|报|周刊|新闻|日报|晚报|时报|广电|财经|观察|频道|电视台|通讯社)")

# ★★ 2026-10-13 用户口径：现实里的【重要机构】—— 中央级媒体 / 司法机关 / 监管机构 —— 角色 = 监管。
#   起因：采集侧把「新华社」标成 role_type=监管，但 infer_poster_role 只读 author_label（数据里根本没有
#        这个字段），于是新华社按「昵称不含媒体关键词 + 0 粉」的兜底逻辑被判成【网民】。
#   这里加一层【昵称白名单】作为双保险：即使采集侧漏标，也能识别出来。
CENTRAL_ORG_RX = re.compile(
    r"(新华社|新华财经|新华网|人民日报|央视|中央电视台|中央广播电视总台|央广|"
    r"中国新闻网|中新社|中新网|经济日报|光明日报|中国青年报|中国青年网|中国经济网|"
    r"中国日报|环球时报|半月谈|求是|旗帜|大众日报|"
    r"人民法院|人民检察院|检察院|法院|"
    r"金融监督管理总局|金融监管总局|银保监|证监会|人民银行|外汇局|"
    r"网信办|市场监管总局|消费者权益保护委员会|消保委|总工会|纪委|监察委)")
PARSE_RX = re.compile(r"(消息|日电|记者|编辑：|报道|官网显示|据.{0,6}统计)")


def _as_text(v):
    """★ 2026-10-14 端到端：采集侧同一字段在不同事件里可能是 str / list / None。

    「昵称/标签类」字段一律取**第一个非空元素**作为文本 —— 采集侧个别事件把 author_name
    写成了 ['昵称', '【标题】']（event5.json 的 EV5_WB_019…022，共 4 条），原先直接
    `.strip()` 会 AttributeError，**当场崩掉整场事件的建图**（EVT-005 实测）。
    取首元素是对的语义：第 0 位才是发帖人昵称，第 1 位是标题。
    """
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        for x in v:
            s = _as_text(x)
            if s:
                return s
        return ""
    return str(v).strip()


def norm_channel(raw):
    return CHANNEL_MAP.get(_as_text(raw), "其他")


def norm_fans(v):
    """'32.5万' -> 325000；'' -> 0"""
    s = str(v or "").strip().replace(",", "").replace("+", "")
    if not s:
        return 0
    m = re.match(r"^([0-9.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    try:
        return int(float(s))
    except Exception:
        return 0


def infer_poster_role(rec, channel):
    """优先用采集侧的 author_label；没有或没覆盖到才按渠道/昵称/粉丝量推。"""
    # ① 采集侧 author_label（数据里目前不存在，保留兼容）
    label = _as_text(rec.get("author_label"))
    if label in LABEL_TO_ROLE:
        return LABEL_TO_ROLE[label], "author_label"
    # ★ ② 采集侧 role_type —— 2026-10-13 新增。
    #   数据里实际用的是 role_type（不是 author_label），原先没读 -> 采集侧的标注被完全忽略。
    rt = _as_text(rec.get("role_type"))
    if rt in LABEL_TO_ROLE:
        return LABEL_TO_ROLE[rt], "role_type"
    name = _as_text(rec.get("author_name"))
    # ★ ③ 重要机构昵称白名单 -> 监管 —— 双保险，即使采集侧漏标也能识别
    if CENTRAL_ORG_RX.search(name):
        return "监管", "central_org"
    # ④ 原有兜底逻辑
    if channel == "监管通报":
        return "监管", "channel"
    if channel == "新闻门户":
        return "媒体", "channel"
    if MEDIA_NAME_RX.search(name):
        return "媒体", "author_name"
    fans = norm_fans(rec.get("author_fans"))
    if fans >= 100000:
        return "KOL", "fans"
    if fans >= 10000:
        return "KOL", "fans"
    if label == "professional_user":
        return "KOL", "author_label_professional"
    return "网民", "default"


def surrogate_author_id(channel, name, post_id):
    """没有 platform uid 时的代理主体 ID。★ 标明是代理，下游可据此降级信任。"""
    n = _as_text(name)
    if n:
        return "AN_%s_%s" % (channel, n)
    return "AN_%s_%s" % (channel, post_id)


def classify_note(channel, text):
    """标注这段文字是不是第三方转述 —— build_kg.classify_text 直接读这个 note。"""
    if channel == "新闻门户":
        return "第三方转述（门户转载/记者转述，未逐字核对平台原文）"
    if PARSE_RX.search(text or ""):
        return "第三方转述（含记者/编辑转述措辞，未逐字核对平台原文）"
    return ""


def _as_list(v):
    """★ 2026-10-07：采集侧字段类型不统一 —— topic_hit / topic_list / comments 等
    在不同事件里可能是 list / str / bool / None。统一安全化，避免 TypeError。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v]
    if isinstance(v, bool):
        return []
    if isinstance(v, str):
        return [x for x in re.split(r"[；;、,，|]", v) if x.strip()]
    return [v]


def adapt(recs, t0_override=None):
    docs, by_event, extra = [], {}, []
    for r in recs:
        eid = "EVT-%s" % (str(r.get("event_id")).zfill(3) if r.get("event_id") is not None else "NA")
        doc_id = str(r.get("post_id") or r.get("doc_id"))
        by_event.setdefault(eid, []).append(doc_id)
        # ★ 兼容两种字段名：采集侧旧版用 source_channel，新版用 channel
        #   只读 source_channel 会导致新版数据全部落进「其他」渠道
        _ch_raw = r.get("source_channel") or r.get("channel")
        ch = norm_channel(_ch_raw)
        role, role_src = infer_poster_role(r, ch)
        uid = _as_text(r.get("author_uid"))
        text = r.get("content") or ""
# ★ 2026-10-07 兼容：extract_entity 在不同事件里可能是【字符串】或【数组】
        _ee = r.get("extract_entity")
        if isinstance(_ee, (list, tuple)):
            ents = [str(x).strip() for x in _as_list(_ee) if str(x).strip()]
        else:
            ents = [x.strip() for x in re.split(r"[；;、,，]", _ee or "") if x.strip()]
        docs.append({
            "doc_id": doc_id,
            "event_id": eid,
            # ---- 8 个必需字段的语义一一对应
            "raw_content": text,
            # ★ 逐帖的话题要素列表（采集侧字段），一路带到 t0 快照
            "topic_list": [str(x) for x in _as_list(r.get("topic_list"))],
            # ★ 组长 v3 §2.4：四个候选字段里【只有 topic_hit 逐帖不同】
            #   keyword_match 全事件同值 / topic_list 几乎全同 / topic_tag 约 1/6 为空
            #   没有它，可见性四因子的「兴趣匹配」直接塌掉一个
            "topic_hit": [str(x) for x in _as_list(r.get("topic_hit"))],
            "comment_sentiment": r.get("comment_sentiment") or [],
            "comments": r.get("comments") or [],
            "raw_content_note": classify_note(ch, text),
            "title": r.get("title") or "",
            "channel_std": ch,
            "channel_raw": _ch_raw,
            "source_url": r.get("post_url") or "",
            "publish_time": r.get("publish_time") or "",
            "author_id": uid or surrogate_author_id(ch, r.get("author_name"), doc_id),
            "author_id_is_surrogate": not bool(uid),
            "poster_role": role,
            "poster_role_source": role_src,
            "entities": ents,
            # ---- 采集侧多给的好字段，原样带过去
            # ★ 2026-10-14：str 一律**原样透传**（不加 strip，保证既有已通过事件的产物逐位不变）；
            #   只有采集侧写成 list 时才用 _as_text 取首元素 —— 否则 list 会一路带进
            #   B_t0 快照的 poster_name / 具名实体，下游谁按字符串处理谁中招。
            "author_name": (r.get("author_name") if isinstance(r.get("author_name"), str)
                            else _as_text(r.get("author_name"))),
            "author_fans": r.get("author_fans"),
            "author_fans_num": norm_fans(r.get("author_fans")),
            "author_label": r.get("author_label"),
            "author_ip_location": r.get("author_ip_location"),
            "sentiment": r.get("sentiment"),
            "comment_sentiment": r.get("comment_sentiment"),
            "comment_cnt": r.get("comment_cnt"),
            "like_cnt": r.get("like_cnt"),
            "collect_cnt": r.get("collect_cnt"),
            "repost_cnt": r.get("repost_cnt"),
            "keyword_match": r.get("keyword_match"),
            "topic_tag": r.get("topic_tag"),
            "is_origin": r.get("is_origin"),
            "root_post_id": r.get("root_post_id") or "",
            "timespan_days": r.get("timespan_days"),
            "event_t0_date": r.get("event_t0"),
            "capture_date": r.get("capture_date"),
            "baidu_index": r.get("Baidu_index"),
        })
        extra.append(r)

    events = []
    for eid, members in by_event.items():
        ms = [d for d in docs if d["event_id"] == eid]
        times = sorted(d["publish_time"] for d in ms if d.get("publish_time"))
        spans = [d.get("timespan_days") for d in ms
                 if isinstance(d.get("timespan_days"), (int, float))]
        t0_dates = [d.get("event_t0_date") for d in ms if d.get("event_t0_date")]
        events.append({
            "event_id": eid,
            "event_key": eid,
            "event_abstract": (ms[0].get("keyword_match") or ms[0].get("title") or eid),
            # ★ 事件原点 = 最早一条帖子的发布时间（引爆帖）。
            #   采集侧给了 event_t0 日期时一并保留，供 t0 口径核对。
            "first_seen_time": t0_override or (times[0] if times else ""),
            "event_t0_date_from_source": t0_dates[0] if t0_dates else None,
            "last_seen_time": times[-1] if times else "",
            "time_span_days": int(max(spans)) if spans else None,
            "ignition_doc": min(ms, key=lambda d: d.get("publish_time") or "")["doc_id"] if ms else None,
            "member_docs": members,
            "doc_count": len(members),
        })

    pack = {"meta": {"note": "由 tools/adapt_collect.py 从采集侧扁平格式转换",
                     "源文件": None, "事件数": len(events), "帖子数": len(docs),
                     "字段映射": "post_id->doc_id / content->raw_content / source_channel->channel_std / "
                                 "post_url->source_url / author_label->poster_role / extract_entity->entities"},
            "events": events, "docs": docs}
    # ★ A7 训练集/回测集标注（组长《交付清单》标为"防数据泄漏的红线"）
    #    阈值只能在【训练事件】上标定；不标这个字段就是在要考的题上复习。
    try:
        _sp = load_config("event_splits")
        pack["meta"]["dataset_split_rule"] = _sp.get("rule")
        pack["meta"]["dataset_split_frozen_at"] = _sp.get("frozen_at")
        for _e in pack.get("events", []):
            _eid = _e.get("event_id")
            _eid = (_sp.get("aliases") or {}).get(_eid, _eid)     # ★ 别名对齐
            _s = (_sp.get("splits") or {}).get(_eid)
            if _s:
                _e["dataset_split"] = _s["split"]
            else:
                # 不在 32 场历史集里 —— 默认按【回测集】处理（保守：绝不用未标注事件调参）
                _e["dataset_split"] = "backtest"
                _e["dataset_split_note"] = "不在冻结划分内，保守归入 backtest"
        pack["meta"]["dataset_split"] = {_e.get("event_id"): _e.get("dataset_split")
                                         for _e in pack.get("events", [])}
    except Exception as _ex:
        pack["meta"]["dataset_split_error"] = repr(_ex)
    # ★ A4 起火点标注：t0 = 最早一条帖子的 publish_time（该帖即起火点）
    _docs = pack.get("docs") or []
    if _docs:
        _first = min(_docs, key=lambda d: str(d.get("publish_time") or "9999"))
        pack["meta"]["ignition_point"] = {
            "t0": _first.get("publish_time"), "doc_id": _first.get("doc_id"),
            "author": _first.get("author_name"), "channel": _first.get("channel_std"),
            "note": "起火点 = t0 时刻最早的一条帖；所有进推演的帖子必须 publish_time <= cutoff"}
    return pack


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="采集侧扁平格式 JSON")
    ap.add_argument("--out", required=True, help="输出标准事件包")
    ap.add_argument("--t0", default=None, help="覆盖事件原点时间（不填则取最早一条帖子的 publish_time）")
    args = ap.parse_args()

    raw = json.loads(Path(args.src).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("posts") or raw.get("docs") or raw.get("data") or []
    pack = adapt(raw, t0_override=args.t0)
    pack["meta"]["源文件"] = str(Path(args.src).resolve())


    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    print("事件 %d 个 / 帖子 %d 条 -> %s" % (len(pack["events"]), len(pack["docs"]), p))
    for e in pack["events"]:
        print("  %s  帖数 %d  跨度 %s 天  t0(最早帖) %s  采集侧 event_t0 %s  引爆帖 %s"
              % (e["event_id"], e["doc_count"], e["time_span_days"], e["first_seen_time"],
                 e["event_t0_date_from_source"], e["ignition_doc"]))
    for d in pack["docs"]:
        print("    %-14s %-19s %-5s %-6s %-9s %s" %
              (d["doc_id"], d["publish_time"], d["channel_std"], d["poster_role"],
               "代理ID" if d["author_id_is_surrogate"] else "真实ID",
               (d["title"] or "")[:26]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
