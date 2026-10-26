# -*- coding: utf-8 -*-
r"""采集链 —— 一条命令走完「参数卡 → 渠道检索 → 三层过滤 → 原文快照 → 21 字段 raw」。

    python 采集链.py --check                                     # 这台机器现在能用哪些模式
    python 采集链.py --mode card     --topic 宠物险 --out card.json
    python 采集链.py --mode snapshot --card card.json --snapshot-dir 快照目录 --out 输出目录
    python 采集链.py --mode live     --card card.json --out 输出目录 [--collector 采集器路径]
    python 采集链.py --mode api      --card card.json --out 输出目录 --adapter 适配器.py

★ 这一支补的是**资料包里缺的那一段**
   采集侧的全流程在 `一键演示.py` 里写的是「第 1 步：快照回放（yuqing_collector.py --mode snapshot）」，
   但 `yuqing_collector.py` **没有随包交付**（资料包里只有调用它的代码，没有它本身）。
   本文件按 `00_程序与文档\采集规则.md` 与 `采集链路说明.md` 写死的规则，把「快照回放」
   这一段重建成可直接跑的实现；`live` / `api` 两种真抓方式接成**可插拔适配器**。

三层硬过滤（照抄 采集规则.md，不新增口径）
    ① 窗口：publish_time 的日期必须落在 [start_date, end_date] 内
    ② 排除词：正文命中 exclude_words 任一词 → 丢弃
    ③ 必选词：正文必须命中 keywords / aliases / subject 至少一个 → 否则丢弃
    另加两条**确定性**垃圾规则（垃圾 8 类里唯一不靠人判的两条）：
    ④ 正文为空 / 只有标题 → 丢弃   ⑤ 正文含手机号、身份证号 → 丢弃（隐私，采集规则 §五）
    拿不准的一律**保留**——采集规则原话：「只删除明确命中上表的内容」。

三种数据源
    snapshot  离线可跑，读 `snapshot_meta.json` + 每帖一个原文 txt（默认，现场演示主路径）
    live      委派外部采集器：`--collector <yuqing_collector.py 路径>` 或环境变量
              `YUQING_COLLECTOR`；找不到就明确报「缺这一层」并打印该放哪儿
    api       委派你自己写的适配器模块（契约见 `采集适配器_模板.py`）

产物
    <out>\EV{n}_raw.json    21 字段原始采集（字段顺序固定、无值留 null、不臆造）
    <out>\采集日志.jsonl    逐帖一行：保留/丢弃 + 理由 + 命中的词（可复现凭证）
"""
import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

# raw 21 字段，顺序固定（采集规则.md §三）
FIELDS = ["post_id", "title", "content", "channel", "post_url", "author_name",
          "author_uid", "author_fans", "author_ip_location", "publish_time",
          "like_cnt", "comment_cnt", "repost_cnt", "collect_cnt", "is_repost",
          "keyword_match", "topic_tag", "topic_list", "capture_date",
          "source_platform", "event_type"]

CHANNELS = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]
BODY_MARK = "========" + " 正文 " + "========"

# 隐私：手机号 / 18 位身份证（垃圾 8 类里唯一两条能靠正则判死的）
RX_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RX_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


# ── 参数卡 / 快照 ────────────────────────────────────────────────────────
def load_card(p):
    with open(p, encoding="utf-8-sig") as f:
        card = json.load(f)
    for k in ("event_id", "start_date", "end_date"):
        if card.get(k) in (None, ""):
            die("参数卡缺关键项 `%s`：%s" % (k, p))
    return card


def parse_snapshot_txt(p):
    """原文快照 → (正文, 头部字段)。没有 `======== 正文 ========` 标记就整篇当正文。"""
    with open(p, encoding="utf-8-sig") as f:
        text = f.read()
    head, _, body = text.partition(BODY_MARK)
    body = (body if _ else text).strip()
    meta = {}
    if _:
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return body, meta


def domain_platform(url):
    """链接域名 → 来源平台（映射不到返回空串，由调用方按渠道兜底）。"""
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)/", url)
    host = (m.group(1) if m else "").lower()
    table = (("douyin", "抖音"), ("iesdouyin", "抖音"), ("xiaohongshu", "小红书"),
             ("weibo", "微博"), ("heimao", "黑猫投诉"), ("12315", "黑猫投诉"),
             ("sina", "新浪财经"), ("ifeng", "凤凰网"), ("thepaper", "澎湃新闻"))
    for k, v in table:
        if k in host:
            return v
    return ""


def norm_list(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[、,，;；\s]+", str(v or "")) if x.strip()]


# ── 三层硬过滤 ──────────────────────────────────────────────────────────
def screen(row, card):
    """返回 (keep, reason)。reason 在保留时是命中的词，丢弃时是理由。"""
    t = (row.get("publish_time") or "").strip()
    d = t[:10]
    if len(d) != 10:
        return False, "publish_time 不可解析"
    if not (card["start_date"] <= d <= card["end_date"]):
        return False, "窗口外（%s 不在 %s ~ %s）" % (d, card["start_date"], card["end_date"])

    content = row.get("content") or ""
    if not content.strip():
        return False, "无有效内容（正文为空）"
    if RX_PHONE.search(content) or RX_IDCARD.search(content):
        return False, "隐私（正文含手机号/身份证号）"

    low = content.lower()
    for w in norm_list(card.get("exclude_words")):
        if w and w.lower() in low:
            return False, "命中排除词「%s」" % w

    hits = [w for w in norm_list(card.get("keywords")) + norm_list(card.get("aliases"))
            + norm_list(card.get("subject")) if w and w.lower() in low]
    if not hits:
        return False, "未命中任何必选词/别名/主体"
    return True, " ".join(dict.fromkeys(hits))     # 去重且保序


# ── 组装 21 字段 ────────────────────────────────────────────────────────
def assemble(item, meta, body, card, capture_date):
    """`meta`（snapshot_meta 的金标准值）优先，参数卡只兜底 —— 采集链路说明 §四.2。"""
    url = meta.get("post_url") or ""
    ch = meta.get("channel") or ""
    if ch not in CHANNELS:
        ch = ch or "新闻门户"
    row = {
        "post_id": meta.get("post_id"),
        "title": meta.get("title") or (body.splitlines()[0][:50] if body else ""),
        "content": body,
        "channel": ch,
        "post_url": url,
        "author_name": meta.get("author_name") or "",
        # 空串是**采集到的空**（页面上没显示），与「没有这个字段」不是一回事，原样留着
        "author_uid": meta.get("author_uid", None),
        "author_fans": meta.get("author_fans", None),
        "author_ip_location": meta.get("author_ip_location", None),
        "publish_time": meta.get("publish_time") or "",
        "like_cnt": meta.get("like_cnt"),
        "comment_cnt": meta.get("comment_cnt"),
        "repost_cnt": meta.get("repost_cnt"),
        "collect_cnt": meta.get("collect_cnt"),
        "is_repost": meta.get("is_repost"),
        "keyword_match": meta.get("keyword_match") or item.get("_hits") or "",
        "topic_tag": meta.get("topic_tag") or card.get("topic_tag") or "",
        "topic_list": meta.get("topic_list") or norm_list(card.get("topic_list")),
        "capture_date": capture_date,
        "source_platform": (meta.get("source_platform") or domain_platform(url)
                            or (card.get("subject") if ch == "监管通报" else ch)),
        "event_type": meta.get("event_type") or card.get("event_type") or "",
    }
    return {k: row.get(k) for k in FIELDS}


# ── 三种数据源 ──────────────────────────────────────────────────────────
def collect_snapshot(card, snap_dir):
    meta_p = os.path.join(snap_dir, "snapshot_meta.json")
    if not os.path.exists(meta_p):
        die("快照目录里没有 snapshot_meta.json：%s" % snap_dir)
    with open(meta_p, encoding="utf-8-sig") as f:
        metas = json.load(f)
    if isinstance(metas, dict):
        metas = metas.get("posts") or metas.get("items") or []
    out = []
    for m in metas:
        fname = m.get("file") or ""
        p = os.path.join(snap_dir, fname)
        if not fname or not os.path.exists(p):
            out.append({"meta": m, "body": "", "file": fname})
            continue
        body, head = parse_snapshot_txt(p)
        merged = dict(head)                      # txt 头部字段打底
        # 只跳过 None：`""` 是「采集到的空」，要盖回去（meta 是金标准）
        merged.update({k: v for k, v in m.items() if v is not None})
        out.append({"meta": merged, "body": body, "file": fname})
    return out


def collect_live(card, collector):
    """委派外部采集器。**没有它就是没有** —— 明确报错，不假装成功。"""
    cand = [collector, os.environ.get("YUQING_COLLECTOR"),
            os.path.join(HERE, "yuqing_collector.py"),
            r"D:\数据采集\yuqing_collector.py"]
    p = next((x for x in cand if x and os.path.exists(x)), None)
    if not p:
        die("实时采集器 yuqing_collector.py 不在包里，也不在这台机器上。\n"
            "  它是采集侧真正的爬虫程序（平台登录态 / 验证码退避 / 各渠道解析都在它里面），\n"
            "  本次交付没有随包拿到。要接上，二选一：\n"
            "    ① python 采集链.py --mode live --collector <yuqing_collector.py 的路径> ...\n"
            "    ② 设环境变量 YUQING_COLLECTOR=<同一路径>\n"
            "  它需要支持：--mode live --card <参数卡> --output-dir <输出目录>（与快照回放同一套参数）。\n"
            "  在此之前，能跑的是 --mode snapshot（离线回放）与 --mode api（你自己的适配器）。")
    return run_delegate([sys.executable, p, "--mode", "live", "--card", card["_path"],
                         "--output-dir", card["_out"]])


def collect_api(card, adapter, extra):
    """委派自己写的适配器：模块里必须有 fetch(card) -> [21 字段 dict, ...]。"""
    if not adapter or not os.path.exists(adapter):
        die("--mode api 需要 --adapter <适配器.py>（契约见 采集适配器_模板.py）")
    sys.path.insert(0, os.path.dirname(os.path.abspath(adapter)))
    mod = __import__(os.path.splitext(os.path.basename(adapter))[0])
    if not hasattr(mod, "fetch"):
        die("适配器里没有 fetch(card) 函数：%s" % adapter)
    rows = mod.fetch(dict(card, **extra)) or []
    return [{"meta": r, "body": r.get("content") or "", "file": ""} for r in rows]


def run_delegate(argv):
    import subprocess
    print("→ 委派：%s" % " ".join(argv))
    r = subprocess.run(argv)
    if r.returncode != 0:
        die("采集器返回 %d，未产出数据。" % r.returncode)
    return None


# ── 主流程 ──────────────────────────────────────────────────────────────
def die(msg):
    print("✘ " + msg, file=sys.stderr)
    sys.exit(1)


def cmd_check():
    import importlib.util

    def has(m):
        return importlib.util.find_spec(m) is not None
    live = None
    for x in (os.environ.get("YUQING_COLLECTOR"), os.path.join(HERE, "yuqing_collector.py"),
              r"D:\数据采集\yuqing_collector.py"):
        if x and os.path.exists(x):
            live = x
            break
    snaps = sorted(d for d in os.listdir(HERE)
                   if re.match(r"^EVT-\d+_", d)
                   and os.path.isdir(os.path.join(HERE, d, "原文快照")))
    info = {
        "snapshot": {"ok": bool(snaps), "samples": snaps,
                     "note": "离线快照回放，零依赖；样本 %d 场" % len(snaps)},
        "live": {"ok": bool(live), "collector": live,
                 "note": ("找到采集器：%s" % live) if live else
                         "缺 yuqing_collector.py（采集侧真正的爬虫，未随包交付）"},
        "api": {"ok": True, "note": "自带适配器契约，见 采集适配器_模板.py"},
        "llm": {"ok": has("requests") and bool(os.environ.get("LLM_API_KEY")),
                "note": "参数卡生成 / build_event 标注用；无 key 时走规则兜底"},
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return info


def main():
    ap = argparse.ArgumentParser(description="采集链：参数卡 →（快照回放/实时/适配器）→ 21 字段 raw")
    ap.add_argument("--mode", choices=["card", "snapshot", "live", "api"], default="snapshot")
    ap.add_argument("--check", action="store_true", help="打印这台机器上各模式的可用性")
    ap.add_argument("--card", help="参数卡 JSON")
    ap.add_argument("--topic", help="--mode card 用：模糊主题")
    ap.add_argument("--snapshot-dir", help="--mode snapshot 用：含 snapshot_meta.json 的快照目录")
    ap.add_argument("--out", "--output-dir", dest="out", help="输出目录（或 --mode card 的参数卡落点）")
    ap.add_argument("--collector", help="--mode live 用：yuqing_collector.py 路径")
    ap.add_argument("--adapter", help="--mode api 用：适配器 .py")
    ap.add_argument("--capture-date", default=None, help="抓取日期，默认今天")
    a = ap.parse_args()

    if a.check:
        cmd_check()
        return 0

    out = os.path.abspath(a.out or ".")

    if a.mode == "card":
        if not a.topic:
            die("--mode card 需要 --topic")
        import subprocess
        gen = os.path.join(HERE, "参数卡生成.py")
        argv = [sys.executable, gen, "--topic", a.topic, "--out", out + ("" if out.endswith(".json") else ".json")]
        print("→ 参数卡：%s" % " ".join(argv))
        sys.exit(subprocess.run(argv).returncode)

    if not a.card or not os.path.exists(a.card):
        die("--mode %s 需要 --card <参数卡.json>" % a.mode)
    card = load_card(a.card)
    card["_path"], card["_out"] = os.path.abspath(a.card), out
    os.makedirs(out, exist_ok=True)
    cap = a.capture_date or date.today().isoformat()

    if a.mode == "snapshot":
        if not a.snapshot_dir:
            die("--mode snapshot 需要 --snapshot-dir <快照目录>")
        items = collect_snapshot(card, a.snapshot_dir)
    elif a.mode == "live":
        collect_live(card, a.collector)       # 采集器自己写 raw，下面按文件收口
        p = os.path.join(out, "EV%d_raw.json" % int(card["event_id"]))
        if not os.path.exists(p):
            die("采集器跑完了，但 %s 没有产出 —— 它必须按 21 字段写这个文件。" % p)
        with open(p, encoding="utf-8") as f:
            rows = json.load(f)
        print("✅ 实时采集：%d 条 → %s" % (len(rows), p))
        return 0
    else:
        items = collect_api(card, a.adapter, {})

    # 过滤 + 组装 + 落盘 + 日志
    kept, dropped, log = [], [], []
    seen = set()
    for it in items:
        meta, body = it["meta"], it["body"]
        row = {"publish_time": meta.get("publish_time"), "content": body}
        keep, why = screen(row, card)
        pid = meta.get("post_id") or ""
        if keep and (pid in seen or (meta.get("post_url") and meta["post_url"] in seen)):
            keep, why = False, "重复（同一 post_id / URL 已收录）"
        if keep:
            seen.add(pid)
            if meta.get("post_url"):
                seen.add(meta["post_url"])
            it["_hits"] = why
            kept.append(assemble(it, meta, body, card, cap))
            log.append({"post_id": pid, "file": it["file"], "kept": True, "hits": why})
        else:
            dropped.append((pid or it["file"], why))
            log.append({"post_id": pid, "file": it["file"], "kept": False, "reason": why})

    kept.sort(key=lambda r: (r.get("publish_time") or "", r.get("post_id") or ""))
    raw_p = os.path.join(out, "EV%d_raw.json" % int(card["event_id"]))
    with open(raw_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    log_p = os.path.join(out, "采集日志.jsonl")
    with open(log_p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"ts": date.today().isoformat(), "mode": a.mode,
                            "event_id": card["event_id"], "source": a.snapshot_dir or a.adapter,
                            "window": [card["start_date"], card["end_date"]],
                            "kept": len(kept), "dropped": len(dropped)}, ensure_ascii=False) + "\n")
        for x in log:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    chans = {}
    for r in kept:
        chans[r["channel"]] = chans.get(r["channel"], 0) + 1
    print("✅ 采集完成（%s）：保留 %d 条 / 丢弃 %d 条" % (a.mode, len(kept), len(dropped)))
    print("   渠道：%s" % ("、".join("%s %d" % kv for kv in sorted(chans.items(), key=lambda x: -x[1])) or "（无）"))
    print("   raw  : %s  （21 字段 / 字段顺序固定）" % raw_p)
    print("   日志 : %s" % log_p)
    for pid, why in dropped:
        print("   - 丢弃 %s：%s" % (pid, why))
    return 0 if kept else 1


if __name__ == "__main__":
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
