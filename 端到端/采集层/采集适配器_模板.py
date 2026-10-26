# -*- coding: utf-8 -*-
r"""采集适配器模板 —— 把「你自己的平台 API / 爬虫」接进采集链的那一个面。

用法（采集链会 import 这个文件并调用 `fetch`）：

    python 采集链.py --mode api --card 参数卡.json --out 输出目录 --adapter 采集适配器_模板.py

    # 本文件自带一个**真能跑**的样例数据源：读一个本地 JSON（{ "posts": [...] }）。
    python 采集链.py --mode api --card ..\EVT-001_...\EVT-001_参数卡.json --out 输出目录 `
        --adapter 采集适配器_模板.py          # 默认读同目录 adapter_sample.json（没有就返回空）

契约（只有两条，别加第三条）
------------------------------------------------
1. **必须有 `fetch(card) -> list[dict]`**
   `card` 是参数卡 dict（event_id / event_name / event_type / start_date / end_date /
   subject / aliases / keywords / exclude_words / topic_tag / topic_list）。
   返回的每一条至少要带：

        content       正文全文（**必填**，空 = 这条一定被丢）
        publish_time  `YYYY-MM-DD HH:MM`（**必填**，窗口过滤靠它）
        channel       6 类枚举之一：微博 / 抖音 / 小红书 / 新闻门户 / 黑猫投诉 / 监管通报

   其余字段（post_id / title / post_url / author_* / 四类互动量 / is_repost /
   keyword_match / topic_tag / topic_list / source_platform）**能取就填，取不到就别编** ——
   缺的由采集链按参数卡兜底，填错的会原样进数据。

2. **过滤不归你管**
   窗口 / 排除词 / 必选词 / 去重 / 隐私 由采集链统一做（`采集规则.md` §五、§六）。
   适配器只管「把候选帖拿回来」，阈值与口径只有一份，别在适配器里再写一遍。

建议实现（可选，采集链不强制）
------------------------------------------------
`probe()` 返回 `{"ok": bool, "note": str}`，给控制台显示「这个数据源现在通不通」。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "adapter_sample.json")

CHANNELS = ("微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报")


# ── 契约 1：把候选帖拿回来 ──────────────────────────────────────────────
def fetch(card):
    """返回候选帖列表。**不做过滤**，拿回来多少交多少。

    ★ 换成你自己的数据源时，只需要换掉这个函数体：
       · 平台 API：requests.get(api, params={"q": 词, "start": ..., "end": ...})
       · 现成爬虫：subprocess 跑它，读它落盘的 JSON
       · 数据库：SELECT ... WHERE publish_time BETWEEN ...
    """
    posts = _read_sample()
    # 拿参数卡里的检索词去问你的数据源（样例数据源只是本地文件，这里只做演示）
    q = (card.get("keywords") or []) + (card.get("aliases") or [])
    print("   [适配器] 检索词：%s" % "、".join(q))
    print("   [适配器] 返回候选 %d 条（未过滤）" % len(posts))
    return posts


def probe():
    """这个数据源现在通不通（控制台用）。"""
    if not os.path.exists(SAMPLE):
        return {"ok": False, "note": "样例数据源 %s 不存在" % os.path.basename(SAMPLE)}
    try:
        with open(SAMPLE, encoding="utf-8-sig") as f:
            n = len(json.load(f).get("posts") or [])
        return {"ok": True, "note": "样例数据源可读：%d 条" % n}
    except Exception as e:
        return {"ok": False, "note": "样例数据源读不动：%s" % e}


# ── 样例数据源（本地 JSON，替换掉即可）────────────────────────────────
def _read_sample():
    if not os.path.exists(SAMPLE):
        return []
    with open(SAMPLE, encoding="utf-8-sig") as f:
        blob = json.load(f)
    posts = blob.get("posts") if isinstance(blob, dict) else blob
    out = []
    for i, p in enumerate(posts or [], 1):
        if not isinstance(p, dict):
            continue
        ch = p.get("channel")
        if ch not in CHANNELS:
            # 渠道不在 6 类枚举内：**不收**，但要喊一声（采集规则 §二）
            print("   [适配器] 跳过 channel=%r（不在 6 类枚举内）" % (ch,))
            continue
        out.append(p)
    return out


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
    print(json.dumps({"fetch_on_empty_card": len(fetch({"keywords": ["宠物险"]}))},
                     ensure_ascii=False))
