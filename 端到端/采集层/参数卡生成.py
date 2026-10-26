# -*- coding: utf-8 -*-
"""参数卡生成器：给一个模糊主题（如「宠物险」），自动生成事件参数卡 JSON。

用法：
  python 参数卡生成.py --topic "宠物险" --out card.json
  python 参数卡生成.py --topic "宠物险拒赔投诉多" --out card.json --start 2026-10-20 --end 2026-10-21
  python 参数卡生成.py --topic "..." --out card.json --event-id 14

说明：
  - event_type 只从 8 类枚举里选（与 build_event.py 校验一致）；
  - event_id 默认取数据包现有事件的最大编号 + 1；
  - start/end 默认取今天/明天（采集后可按最早可核验帖回溯更新 t0）；
  - 需要 LLM_API_KEY 环境变量（DeepSeek）；无 key 或调用失败时用规则兜底生成最小卡，
    并提示人工补充。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
EVENT_TYPES = ["理赔类", "销售与投保类", "产品与费率类", "服务态度类",
               "营销类", "数据与隐私类", "公司经营类", "司法与维权类"]

SYSTEM = "你是保险行业舆情分析师，负责为舆情采集任务填写事件参数卡。只输出 JSON，不要输出其他内容。"
PROMPT_TPL = """请根据主题生成事件参数卡，只输出 JSON（不要 markdown 代码块）：

主题：{topic}

字段要求：
- event_name：事件简称，一句话说清主体和争议点；争议点未知时用「{topic}舆情监测」式命名
- event_type：从 8 类枚举中选一个：理赔类 / 销售与投保类 / 产品与费率类 / 服务态度类 / 营销类 / 数据与隐私类 / 公司经营类 / 司法与维权类
- subject：核心主体（公司全称或规范名）
- aliases：主体别名、产品名、关联公司，顿号分隔，3~8 个
- keywords：检索词，含产品别名＋事件特征词＋独特事实词，顿号分隔，5~12 个
- exclude_words：撞名企业、无关产品、招聘广告等排除词，顿号分隔，3~6 个（没有可给空串）
- topic_tag：话题标签，格式 #xxx#
- topic_list：主题词列表，5~10 个

输出格式：
{{"event_name": "...", "event_type": "...", "subject": "...", "aliases": "...", "keywords": "...", "exclude_words": "...", "topic_tag": "#...#", "topic_list": ["...", "..."]}}"""


def next_event_id():
    mx = 0
    for d in os.listdir(HERE):
        m = re.match(r"^EVT-(\d+)_", d)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def call_llm(prompt):
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        return ""
    import requests
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json={"model": "deepseek-v4-flash",
                      "messages": [{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": prompt}],
                      "temperature": 0},
                timeout=30)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            if attempt < 2:
                time.sleep(1)
    return ""


def fallback_card(topic):
    return {
        "event_name": topic + "舆情监测",
        "event_type": "",
        "subject": topic,
        "aliases": topic,
        "keywords": topic,
        "exclude_words": "",
        "topic_tag": "#%s#" % topic.strip("#"),
        "topic_list": [topic],
    }


def build_card(topic, event_id, start, end):
    raw = call_llm(PROMPT_TPL.format(topic=topic))
    llm_used = False
    if raw:
        raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip())
        try:
            data = json.loads(raw)
            llm_used = True
        except Exception:
            data = {}
    else:
        data = {}

    def norm_list(v):
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        return [x.strip() for x in re.split(r"[、,，;；]", str(v or "")) if x.strip()]

    card = {
        "event_id": event_id,
        "event_name": str(data.get("event_name") or "").strip() or fallback_card(topic)["event_name"],
        "event_type": str(data.get("event_type") or "").strip(),
        "start_date": start,
        "end_date": end,
        "subject": str(data.get("subject") or "").strip() or topic,
        "aliases": norm_list(data.get("aliases")) or [topic],
        "keywords": norm_list(data.get("keywords")) or [topic],
        "exclude_words": norm_list(data.get("exclude_words")),
        "topic_tag": str(data.get("topic_tag") or "").strip() or fallback_card(topic)["topic_tag"],
        "topic_list": norm_list(data.get("topic_list")) or [topic],
    }
    if card["event_type"] not in EVENT_TYPES:
        card["event_type"] = ""
    return card, llm_used


def main():
    ap = argparse.ArgumentParser(description="参数卡生成器：主题 -> 事件参数卡 JSON")
    ap.add_argument("--topic", required=True, help="模糊主题/关键词，如 宠物险")
    ap.add_argument("--out", required=True, help="参数卡输出路径")
    ap.add_argument("--event-id", type=int, default=None, help="事件编号，默认自动取最大编号+1")
    ap.add_argument("--start", default=None, help="采集窗口起始日 YYYY-MM-DD，默认今天")
    ap.add_argument("--end", default=None, help="采集窗口结束日 YYYY-MM-DD，默认明天")
    args = ap.parse_args()

    today = date.today()
    start = args.start or today.isoformat()
    end = args.end or (today + timedelta(days=1)).isoformat()
    event_id = args.event_id or next_event_id()

    card, llm_used = build_card(args.topic, event_id, start, end)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)

    print("参数卡已生成：%s" % args.out)
    print("  event_id : %s" % card["event_id"])
    print("  event_name : %s" % card["event_name"])
    print("  event_type : %s%s" % (card["event_type"] or "（未定，请从 8 类枚举补齐）",
                                   "" if card["event_type"] else ""))
    print("  window   : %s ~ %s" % (card["start_date"], card["end_date"]))
    print("  aliases  : %s" % "、".join(card["aliases"]))
    print("  keywords : %s" % "、".join(card["keywords"]))
    print("  excludes : %s" % ("、".join(card["exclude_words"]) or "（无）"))
    if not llm_used:
        print("  [警告] LLM 不可用（未设 LLM_API_KEY 或调用失败），本卡为规则兜底最小版，建议人工补充后重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
