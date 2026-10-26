#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
众安舆情数据处理 Pipeline v4（通用版，适用于任意新事件）
输入：raw_posts.json（纯采集数据，21 字段，含 source_platform/event_type）
输出：event{N}.json（结构化数据，28 字段）

说明：
  - 不绑定任何特定事件：事件号、t0、话题标签、主题词均由命令行参数传入
  - 新增透传 source_platform、event_type 字段
  - is_repost直接从raw取，不再反推
  - role_type：规则判断监管/媒体/KOL → 走LLM判断客户/网民
  - sentiment / topic_hit / extract_entity 走LLM标注
  - 不处理评论（评论字段已从数据格式中删除）
"""

import json
import re
import os
import time
import argparse
import concurrent.futures
import requests
from datetime import datetime, date
from collections import Counter, defaultdict
from typing import Any


# ============================================================
# 一、常量定义（只保留6个渠道）
# ============================================================

PLATFORM_MAP = {
    "微博": "WB",
    "抖音": "DY",
    "小红书": "XHS",
    "新闻门户": "NEWS",
    "黑猫投诉": "HM",
    "监管通报": "GOV",
}

VALID_CHANNELS = {"微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"}
VALID_ROLE_TYPES = {"监管", "媒体", "KOL", "客户", "网民"}
VALID_SENTIMENTS = {"正面", "中性", "负面"}

# 事件类型枚举（8 类，event_type 字段只能从这里面取值）
EVENT_TYPES = {
    "理赔类", "销售与投保类", "产品与费率类", "服务态度类",
    "营销类", "数据与隐私类", "公司经营类", "司法与维权类",
}

# LLM 关闭/失败时的兜底负面词表
FALLBACK_NEGATIVE_WORDS = [
    "拒赔", "投诉", "欺诈", "曝光", "维权", "坑", "骗",
    "违规", "处罚", "恶意", "误导", "隐瞒", "欺骗", "耍流氓",
]

# 口径仲裁规则（与统一情感口径对应：客观陈述=中性、直接定性=负面、转述标记不算立场）
ARB_NEG_DIRECT = ["恶意", "李鬼", "套路", "陷阱", "坑", "骗", "欺诈", "霸王", "高息返利", "耍流氓", "误导"]
ARB_QUOTE_MARKERS = ["被指", "被曝", "被质疑", "被投诉", "网友称", "网友表示", "消费者称", "消费者反映",
                     "有网友", "遭质疑", "反映称", "投诉称", "维权者称", "律师称", "记者获悉", "客服回应"]
ARB_EMOTION = ["没想到", "太坑", "无奈", "气愤", "无语", "离谱", "耍流氓", "愤怒", "太气人", "令人难安"]


def arbitrate_sentiment(title: str, content: str, role: str, sentiment: str) -> str:
    """情感口径仲裁：标题带转述标记的贬义词 -> 中性；标题直接定性贬义词 -> 负面；
    客户帖含投诉情绪词 -> 负面。其余保持原判定。"""
    has_direct = any(w in title for w in ARB_NEG_DIRECT)
    has_marker = any(m in title for m in ARB_QUOTE_MARKERS)
    if has_direct and has_marker:
        return "中性"
    if has_direct:
        return "负面"
    if role == "客户" and any(e in content for e in ARB_EMOTION) and any(
            w in content for w in ["拒赔", "扣费", "投诉", "退款"]):
        return "负面"
    return sentiment

# 规则判断用的关键词（只用于监管/媒体/KOL）
CENTRAL_MEDIA = {
    "人民日报", "新华社", "央视", "央广", "央广网", "中新社", "中国新闻网",
    "光明网", "光明日报", "经济日报", "中国妇女报", "半月谈", "环球时报",
    "中国网", "人民网", "央视新闻", "看看新闻", "东方网", "法治日报",
}

REGULATOR_KEYWORDS = {
    "银保监", "金融监管", "监管局", "消保委", "消费者协会", "市场监管",
    "网信办", "通管局", "法院", "检察", "中国人民银行", "外汇管理",
}

MEDIA_SIGNALS = [
    "报", "网", "财经", "新闻", "头条", "日报", "晚报", "周刊",
    "视点", "观察", "资讯", "频道", "融媒", "广播", "电视台",
    "凤凰", "澎湃", "界面", "蓝鲸", "财新", "36氪", "虎嗅",
    "律师", "法律", "科普", "行业观察", "评论员", "法制", "法治",
]

# LLM配置
LLM_BASE_URL = "https://api.deepseek.com"
LLM_MODEL = "deepseek-v4-flash"
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MAX_RETRIES = 2
LLM_WORKERS = 4          # 并发标注线程数

# 全局失败记录
llm_failed_posts = []
time_parse_failed = []


def load_llm_cache(cache_path):
    """按 post_id 缓存 LLM 标注结果，重跑不重复调用 API。"""
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_llm_cache(cache_path, cache):
    if not cache_path:
        return
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


# ============================================================
# 二、LLM调用（带重试）
# ============================================================

def call_llm(prompt: str, system: str = "", temperature: float = 0) -> str:
    """调用DeepSeek API，带重试"""
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system if system else "你是舆情分析助手，只输出JSON，不要输出其他内容。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{LLM_BASE_URL}/v1/chat/completions",
                headers=headers, json=payload, timeout=30
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < LLM_MAX_RETRIES:
                time.sleep(1)
            else:
                return ""


def llm_annotate_post(post_id: str, content: str, title: str, author_name: str, channel: str, topic_list: list[str]) -> dict:
    """
    LLM标注单条帖子：sentiment、topic_hit、extract_entity，以及判断是客户还是网民
    """
    prompt = f"""请对以下舆情帖子进行标注，只输出JSON：

帖子渠道：{channel}
作者昵称：{author_name}
帖子标题：{title}
帖子正文：{content[:2000]}

该事件的主题词列表：{json.dumps(topic_list, ensure_ascii=False)}

请输出JSON：
{{
  "is_customer": true/false,
  "sentiment": "正面/中性/负面",
  "topic_hit": [命中的主题词列表，从上面的列表里选],
  "extract_entity": [帖子中提到的公司名、产品名、人名、监管机构名列表]
}}

判定规则：
- is_customer：true=帖子作者本人就是购买/投保/被拒赔/被投诉的消费者，作者以"我买了""我投保""我被拒赔"等第一人称叙述自己的经历；false=其他普通用户。注意：正文里引用的他人第一人称（如记者报道中的"施先生说……"）和转载内容不算，只看作者本人是不是当事人
- sentiment：正面=支持/赞扬/认可；中性=单纯报道或陈述事件、客观信息、无明显情感色彩（媒体报道负面事件若只做客观陈述也记中性；正文引用的他人说法不计发帖者立场）；负面=批评/质疑/投诉/不满等有明显负面情感色彩的内容——注意：贬义表述带转述标记（被指/被曝/网友称/消费者称/律师称等）是引用他人说法，记中性；但发布者自己的评论性表述（如编者按语"算计消费者"）有明显情感色彩，记负面
- topic_hit：只从给定主题词列表里选，不要自己造词
- extract_entity：提取保险公司名、保险产品名、当事人姓名、监管机构名"""

    result = call_llm(prompt)
    if not result:
        llm_failed_posts.append(post_id)
        return {"is_customer": False, "sentiment": "中性", "topic_hit": [], "extract_entity": []}
    try:
        result = re.sub(r"^```json\s*|\s*```$", "", result.strip())
        data = json.loads(result)
        sentiment = data.get("sentiment", "中性")
        if sentiment not in VALID_SENTIMENTS:
            sentiment = "中性"
        return {
            "is_customer": bool(data.get("is_customer", False)),
            "sentiment": sentiment,
            "topic_hit": data.get("topic_hit", []),
            "extract_entity": data.get("extract_entity", []),
        }
    except Exception as e:
        llm_failed_posts.append(post_id)
        return {"is_customer": False, "sentiment": "中性", "topic_hit": [], "extract_entity": []}


# ============================================================
# 三、规则判断（只判断监管/媒体/KOL，客户交给LLM）
# ============================================================

def parse_fans(fans_raw: Any) -> int | None:
    """解析粉丝数，支持"1.2万"格式"""
    if fans_raw is None or fans_raw == "" or fans_raw == "null":
        return None
    if isinstance(fans_raw, (int, float)):
        return int(fans_raw)
    fans_str = str(fans_raw).strip()
    if not fans_str:
        return None
    # 支持"1.2万"、"3.5w"格式
    m = re.match(r"([\d.]+)\s*[wW万]", fans_str)
    if m:
        return int(float(m.group(1)) * 10000)
    try:
        return int(float(fans_str))
    except ValueError:
        return None


def infer_role_type_by_rule(author_name: str, author_fans: int | None, content: str, channel: str) -> str | None:
    """规则判断role_type：能判断出 监管/媒体/KOL 就返回，判断不出来返回None"""
    name = author_name or ""

    # 1. 监管
    for kw in REGULATOR_KEYWORDS:
        if kw in name:
            return "监管"
    for media in CENTRAL_MEDIA:
        if media in name:
            return "监管"

    # 2. 媒体
    if any(sig in name for sig in MEDIA_SIGNALS):
        return "媒体"

    # 3. KOL
    if author_fans and author_fans >= 100000:
        return "KOL"

    # 判断不出来，交给LLM
    return None


# ============================================================
# 四、硬处理函数
# ============================================================

def parse_publish_time(raw_time: str) -> datetime | None:
    """解析发布时间，失败返回None"""
    if not raw_time:
        return None
    raw_time = raw_time.strip()
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(raw_time, fmt)
        except ValueError:
            continue
    return None


def calc_timespan_days(pub_dt: datetime, t0: str) -> int:
    t0_date = datetime.strptime(t0, "%Y-%m-%d").date()
    pub_date = pub_dt.date()
    return (pub_date - t0_date).days


def generate_post_id(event_id: int, channel: str, seq: int) -> str:
    prefix = PLATFORM_MAP.get(channel, "XX")
    return f"EV{event_id}_{prefix}_{seq:03d}"


def infer_title(content: str, channel: str) -> str:
    content = content.strip()
    if channel == "微博":
        return content[:30]
    first_line = content.split("\n")[0].strip()
    return first_line[:50] if len(first_line) > 50 else first_line


# ============================================================
# 五、校验函数
# ============================================================

REQUIRED_FIELDS = [
    "post_id", "event_id", "title", "content", "channel", "post_url",
    "source_platform", "event_type",
    "author_uid", "author_name", "author_ip_location", "author_fans",
    "role_type", "sentiment", "publish_time", "comment_cnt", "like_cnt",
    "collect_cnt", "repost_cnt", "keyword_match", "timespan_days",
    "is_origin", "event_t0", "topic_tag", "topic_list",
    "topic_hit", "extract_entity", "capture_date",
]


def validate_post(post: dict) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in post:
            errors.append(f"缺少字段: {field}")

    if post.get("channel") not in VALID_CHANNELS:
        errors.append(f"非法channel: {post.get('channel')}")
    if post.get("role_type") not in VALID_ROLE_TYPES:
        errors.append(f"非法role_type: {post.get('role_type')}")
    if post.get("sentiment") not in VALID_SENTIMENTS:
        errors.append(f"非法sentiment: {post.get('sentiment')}")
    et = post.get("event_type") or ""
    if et and et not in EVENT_TYPES:
        errors.append(f"非法event_type: {et}（只能取 8 类枚举：{sorted(EVENT_TYPES)}）")
    return errors


# ============================================================
# 六、主Pipeline
# ============================================================

def build_event(
    raw_posts: list[dict],
    event_id: int,
    t0: str,
    topic_tag: str,
    global_topic_list: list[str],
    capture_date: str = None,
    use_llm: bool = True,
    cache_path: str = None,
) -> list[dict]:
    if capture_date is None:
        capture_date = date.today().isoformat()

    channel_counter = defaultdict(int)
    processed = []

    print(f"\n[Step1] 开始结构化处理，共 {len(raw_posts)} 条")

    # LLM 标注：并发 + 按 post_id 缓存（重跑不重复调用 API，秒回）
    llm_results = {}
    if use_llm and LLM_API_KEY:
        cache = load_llm_cache(cache_path) if cache_path else {}
        tasks = []
        cc = defaultdict(int)
        for raw in raw_posts:
            ch = raw["channel"]
            cc[ch] += 1
            pid = raw.get("post_id") or generate_post_id(event_id, ch, cc[ch])
            tasks.append((pid, raw))
        todo = [(pid, raw) for pid, raw in tasks if pid not in cache]
        if todo:
            print(f"  LLM 标注（并发 {LLM_WORKERS} 线程）：{len(todo)} 条待标注，"
                  f"{len(tasks) - len(todo)} 条命中缓存")
            with concurrent.futures.ThreadPoolExecutor(max_workers=LLM_WORKERS) as ex:
                futs = {}
                for pid, raw in todo:
                    c = (raw.get("content") or "").strip()
                    t = raw.get("title") or infer_title(c, raw["channel"])
                    futs[ex.submit(llm_annotate_post, pid, c, t,
                                   raw.get("author_name", ""), raw["channel"],
                                   global_topic_list)] = pid
                for fut in concurrent.futures.as_completed(futs):
                    cache[futs[fut]] = fut.result()
            save_llm_cache(cache_path, cache)
        for pid, _ in tasks:
            llm_results[pid] = cache.get(pid) or {
                "is_customer": False, "sentiment": "中性", "topic_hit": [], "extract_entity": []}

    for idx, raw in enumerate(raw_posts):
        channel = raw["channel"]
        channel_counter[channel] += 1
        auto_seq = channel_counter[channel]

        post_id = raw.get("post_id") or generate_post_id(event_id, channel, auto_seq)

        # 解析时间（容错）
        raw_pub = raw.get("publish_time", "")
        pub_dt = parse_publish_time(raw_pub)
        if pub_dt is None:
            time_parse_failed.append(post_id)
            timespan = 0
        else:
            timespan = calc_timespan_days(pub_dt, t0)

        content = (raw.get("content") or "").strip()
        title = raw.get("title") or infer_title(content, channel)
        author_name = raw.get("author_name", "")

        # author_fans解析（支持"1.2万"）
        author_fans_raw = raw.get("author_fans")
        author_fans_num = parse_fans(author_fans_raw)

        # role_type：先用规则判断监管/媒体/KOL
        rule_result = infer_role_type_by_rule(author_name, author_fans_num, content, channel)

        if rule_result:
            role_type = rule_result
            # sentiment/topic_hit/extract_entity走LLM
            if use_llm and LLM_API_KEY:
                llm_result = llm_results.get(post_id) or {
                    "is_customer": False, "sentiment": "中性", "topic_hit": [], "extract_entity": []}
                sentiment = llm_result["sentiment"]
                topic_hit = llm_result["topic_hit"]
                extract_entity = llm_result["extract_entity"]
            else:
                sentiment = "负面" if any(w in content for w in FALLBACK_NEGATIVE_WORDS) else "中性"
                topic_hit = [t for t in global_topic_list if t in content]
                extract_entity = []
        else:
            # 规则判断不出来，走LLM：判断是客户还是网民
            if use_llm and LLM_API_KEY:
                llm_result = llm_results.get(post_id) or {
                    "is_customer": False, "sentiment": "中性", "topic_hit": [], "extract_entity": []}
                role_type = "客户" if llm_result["is_customer"] else "网民"
                sentiment = llm_result["sentiment"]
                topic_hit = llm_result["topic_hit"]
                extract_entity = llm_result["extract_entity"]
            else:
                role_type = "客户" if channel == "黑猫投诉" else "网民"
                sentiment = "负面" if any(w in content for w in FALLBACK_NEGATIVE_WORDS) else "中性"
                topic_hit = [t for t in global_topic_list if t in content]
                extract_entity = []

        # 情感口径仲裁（转述标记/直接定性/投诉情绪）
        sentiment = arbitrate_sentiment(title, content, role_type, sentiment)

        # is_origin：直接从raw的is_repost取反
        is_origin = not raw.get("is_repost", False)

        post = {
            "post_id": post_id,
            "event_id": event_id,
            "title": title,
            "content": content,
            "channel": channel,
            "post_url": raw.get("post_url", ""),
            "source_platform": raw.get("source_platform", ""),
            "event_type": raw.get("event_type", ""),
            "author_uid": raw.get("author_uid"),
            "author_name": author_name,
            "author_ip_location": raw.get("author_ip_location"),
            "author_fans": author_fans_num,  # 统一转成整数输出
            "role_type": role_type,
            "sentiment": sentiment,
            "publish_time": raw_pub,
            "comment_cnt": raw.get("comment_cnt"),
            "like_cnt": raw.get("like_cnt"),
            "collect_cnt": raw.get("collect_cnt"),
            "repost_cnt": raw.get("repost_cnt"),
            "keyword_match": raw.get("keyword_match", ""),
            "timespan_days": timespan,
            "is_origin": is_origin,
            "event_t0": t0,
            "topic_tag": raw.get("topic_tag") or topic_tag,
            "topic_list": raw.get("topic_list") or global_topic_list,
            "topic_hit": topic_hit,
            "extract_entity": extract_entity,
            "capture_date": raw.get("capture_date") or capture_date,
        }
        processed.append(post)

        if (idx + 1) % 5 == 0:
            print(f"  已处理 {idx+1}/{len(raw_posts)} 条")

    print(f"[Step1] 结构化处理完成: {len(processed)} 条")

    # 报告失败项
    if time_parse_failed:
        print(f"\n[警告] 时间解析失败 {len(time_parse_failed)} 条: {time_parse_failed}")
    if llm_failed_posts:
        print(f"[警告] LLM标注失败 {len(llm_failed_posts)} 条: {llm_failed_posts}")

    # Step 2: 校验
    all_errors = []
    for p in processed:
        errors = validate_post(p)
        if errors:
            all_errors.extend([f"{p['post_id']}: {e}" for e in errors])

    if all_errors:
        print(f"\n[Step2] 校验发现 {len(all_errors)} 个问题:")
        for e in all_errors[:20]:
            print(f"  [!] {e}")
        if len(all_errors) > 20:
            print(f"  ... 还有 {len(all_errors)-20} 个")
        raise ValueError("结构化帖子校验未通过")
    else:
        print("[Step2] 校验通过 OK")

    return processed


# ============================================================
# 七、CLI入口
# ============================================================

def main():
    global llm_failed_posts, time_parse_failed
    llm_failed_posts = []
    time_parse_failed = []

    parser = argparse.ArgumentParser(description="众安保险舆情数据处理Pipeline v3")
    parser.add_argument("--raw", required=True, help="原始数据JSON文件路径")
    parser.add_argument("--event-id", type=int, required=True, help="事件编号")
    parser.add_argument("--t0", required=True, help="事件起始日期 YYYY-MM-DD")
    parser.add_argument("--topic-tag", required=True, help="事件话题标签")
    parser.add_argument("--topic-list", nargs="+", required=True, help="全局主题词列表")
    parser.add_argument("--output", required=True, help="输出JSON文件路径")
    parser.add_argument("--capture-date", default=None, help="采集日期 YYYY-MM-DD")
    parser.add_argument("--no-llm", action="store_true", help="不调用LLM，用规则兜底")
    args = parser.parse_args()

    with open(args.raw, "r", encoding="utf-8-sig") as f:
        raw_posts = json.load(f)

    print(f"输入原始帖子: {len(raw_posts)} 条")
    print(f"事件ID: {args.event_id}, t0: {args.t0}")
    print(f"LLM标注: {'关闭（规则兜底）' if args.no_llm else '开启'}")
    if not args.no_llm and not LLM_API_KEY:
        print("[警告] 未设置环境变量 LLM_API_KEY，将自动退回规则兜底")
    print()

    result = build_event(
        raw_posts=raw_posts,
        event_id=args.event_id,
        t0=args.t0,
        topic_tag=args.topic_tag,
        global_topic_list=args.topic_list,
        capture_date=args.capture_date,
        use_llm=not args.no_llm,
        cache_path=args.output + ".cache.json",
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n输出: {args.output}")
    print(f"最终帖子数: {len(result)}")

    day_counts = Counter(p["timespan_days"] for p in result)
    print("\n按天分布:")
    for d in sorted(day_counts.keys()):
        print(f"  Day{d}: {day_counts[d]}条")

    ch_counts = Counter(p["channel"] for p in result)
    print("\n按渠道分布:")
    for ch, cnt in ch_counts.most_common():
        print(f"  {ch}: {cnt}条")

    sent_counts = Counter(p["sentiment"] for p in result)
    print("\n按情感分布:")
    for s, cnt in sent_counts.most_common():
        print(f"  {s}: {cnt}条")

    role_counts = Counter(p["role_type"] for p in result)
    print("\n按角色分布:")
    for r, cnt in role_counts.most_common():
        print(f"  {r}: {cnt}条")


if __name__ == "__main__":
    main()
