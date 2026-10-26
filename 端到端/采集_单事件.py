# -*- coding: utf-8 -*-
r"""采集侧**单事件**入口 —— `raw_posts_*.json` → `event*.json`（30 字段）。

为什么补这一支
--------------
采集侧资料包自带的一键入口（`run_all.py` / `reproduce.py`）是**批量驱动**：事件清单
写死在 `gen_trend_charts.py` 的 `EVENTS` 里，只认交付的那 12 场。端到端链上「喂一个新
事件」需要**单场**入口，所以这里补一个。

★ 不新增任何规则：只是把 `run_all.py` 里那 5 行（`build_event(...)` + 落盘）单独拎出来。
  `build_event.py` / `llm_annotate.py` **一个字节不改**，本文件只 `import` 它们。

用法
----
    python -I 采集_单事件.py --id 13 --title "某事件" --t0 2026-06-01 \
           --raw  <raw_posts_event13.json> --out  <event13.json>

    # 新批次 raw 若还没带 LLM 标注，先补标注（需 `openai` 包 + LLM_API_KEY）
    python -I 采集_单事件.py --id 13 --title "..." --t0 ... --raw ... --out ... --annotate

★ 边界（很重要）
  · `build_event` 默认**不调 LLM**：标注（`sentiment` / `topic_hit` / `extract_entity`）
    是随 raw 逐帖透传的 —— 这是交付的那 12 场能逐字节复现的原因。
  · 新批次 raw 若没带标注，`build_event` 会按**规则**兜底；那份兜底口径实测一致率约 44%
    （`sentiment` 判的是「发布者本人的褒贬」，是**判断**不是字符串匹配，规则顶不住）。
    ⇒ **新批次要保真，必须加 `--annotate`。**
  · `--annotate` 走的是采集侧自己的 `llm_annotate.annotate_event(n, ...)`，它把
    `raw_posts_event{n}.json` 和 `llm_cache/event{n}.json` 都定位在**采集侧脚本目录**下
    ⇒ 用 `--annotate` 时，raw 文件得放在那个目录里（那是采集侧的既有约定，本文件不改它）。
"""
import argparse
import io
import json
import os
import sys

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
# 交付布局：`初赛提交\端到端\` 与 `初赛提交\采集侧_可复现资料包\` 是兄弟目录。
_DEF_COLLECT = os.path.join(os.path.dirname(_HERE), "采集侧_可复现资料包", "01_代码与脚本")


def main():
    ap = argparse.ArgumentParser(description="采集侧单事件：raw → event（30 字段）")
    ap.add_argument("--raw", required=True, help="原始帖子 JSON（数组，一条一帖）")
    ap.add_argument("--out", required=True, help="成品 event*.json 落点")
    ap.add_argument("--id", required=True, help="事件号（整数，写进 event_id 字段）")
    ap.add_argument("--title", required=True, help="事件标题（仅作 topic_tag 兜底，raw 逐帖透传优先）")
    ap.add_argument("--t0", required=True, help="事件起始日 YYYY-MM-DD")
    ap.add_argument("--collect-dir", default=_DEF_COLLECT,
                    help="采集侧脚本目录（含 build_event.py）。默认按交付布局找")
    ap.add_argument("--annotate", action="store_true",
                    help="先用 LLM 补标注再建事件（需 openai 包 + LLM_API_KEY；见文件头「边界」）")
    a = ap.parse_args()

    base = os.path.abspath(a.collect_dir)
    if not os.path.exists(os.path.join(base, "build_event.py")):
        sys.exit("✗ 在 %s 里找不到 build_event.py。用 --collect-dir 指一下采集侧脚本目录。" % base)
    sys.path.insert(0, base)
    import build_event as be  # noqa: E402

    def load():
        with open(a.raw, encoding="utf-8-sig") as f:
            return json.load(f)

    raw = load()
    print("原始 %d 条 ← %s" % (len(raw), a.raw))

    if a.annotate:
        import llm_annotate  # noqa: E402
        print("── LLM 标注中（写回 raw + 缓存）")
        llm_annotate.annotate_event(int(a.id), a.title, write=True)
        raw = load()

    posts = be.build_event(
        raw_posts=raw,
        event_id=int(a.id),
        t0=a.t0,
        topic_tag=a.title,        # 仅兜底，raw 逐帖透传优先
        global_topic_list=[],     # 仅兜底
        capture_date=None,        # 仅兜底
    )
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    chans = {}
    for p in posts:
        chans[str(p.get("channel"))] = chans.get(str(p.get("channel")), 0) + 1
    print("✅ 成品 %d 条 / %d 字段 → %s" % (len(posts), len(posts[0]) if posts else 0, out))
    print("   渠道分布：%s" % "、".join("%s %d" % kv for kv in sorted(chans.items(),
                                                                  key=lambda x: -x[1])))
    print("   ⚠️ 采集侧与知识图谱侧的渠道词表已在 2026-10-14 对齐（`监管渠道` → 监管通报），"
          "见 ..\\端到端\\README.md §三")


if __name__ == "__main__":
    main()
