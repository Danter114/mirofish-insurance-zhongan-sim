# -*- coding: utf-8 -*-
r"""检索作业 —— 把「去 6 个渠道检索」这一段变成**可开单、可验收**的作业。

    开单：python 检索作业.py 开单 --card 参数卡.json --out 作业目录
    收单：python 检索作业.py 收单 --job 作业目录 --card 参数卡.json --out 输出目录 [--collect]

为什么要这一支
------------------------------------------------
采集侧的检索是**人工 + AI 辅助检索**（`采集侧全流程说明.md` §七 原文：平台官方 API 全量采集 = 无），
不是爬虫程序。所以「去渠道检索」这一段永远有人/模型在里面，但**它可以被规范化**：

    开单 → 按单检索 → 落档（三件套 + 查询记录）→ 收单校验 → 采集链 → 21 字段 raw

本文件负责**首尾两头**（开单、收单校验），中间那一段照 `采集规则.md` §八 由人或 AI 执行；
产出的快照目录直接喂已经验过的 `采集链.py --mode snapshot`，过滤规则一行不重写。

开单产出（作业目录）
------------------------------------------------
    检索作业单.md                这一刻该干什么：检索词、逐渠道入口、落档格式、收单命令
    查询记录.csv                 6 个渠道预填好的空表（★ 采集侧文档要求记、但历史产物里没有的那张）
    采集清单.json                空的 []，按作业单 §4.3 的 8 个键逐帖填
    原文快照\snapshot_meta.json   空的 []，按作业单 §4.2 填
    原文快照\_格式说明.txt        一帖一个 txt，四行头 + 正文

收单检查什么（硬性问题 exit 1）
------------------------------------------------
    H1 snapshot_meta.json 存在且是数组
    H1d ★ 快照非空 —— 0 条不算收单成功（空集在下游每道闸上都"能过"，会静默变成 raw = []）
    H2 每条 meta 的 file 真的存在
    H3 channel 全在 6 类枚举内
    H4 post_id 非空且唯一
    H5 采集清单.json 存在且是数组
    H6 查询记录.csv 存在且 8 列齐全
    H7 6 个渠道每个至少一行（没有结果也要写一行、kept_count=0）
    H8 ★ 查询记录的 kept_count 合计 == snapshot_meta 实际条数（对账命门）
    H9 ★ 采集清单覆盖 snapshot_meta 的每一帖（采集规则 §六：清单即全集）

警告项（不影响退出码）：txt 缺四行头、query/result_count 没填、缺 post_url、清单里有多余条目。

边界（照实写）
------------------------------------------------
· 收单**不判断检索做得对不对**（相关性是人的判断），只判断「交上来的东西是不是齐的、自洽的」。
· `--collect` 只是替你调一次 `采集链.py --mode snapshot`，过滤口径仍然只有那一份。
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CHAIN = os.path.join(HERE, "采集链.py")

CHANNELS = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]
BODY_MARK = "========" + " 正文 " + "========"

# 采集规则.md §二：新闻门户是封闭白名单，清单外不收录
NEWS_WHITELIST = ["新浪财经", "凤凰网", "澎湃新闻", "界面新闻", "蓝鲸财经", "财新网", "36氪", "虎嗅",
                  "每日经济新闻", "证券时报", "第一财经", "21世纪经济报道", "中国网财经",
                  "荔枝网（江苏广电）", "中国吉林网", "深圳新闻网", "腾讯新闻", "中华网",
                  "金融界", "红星资本局", "都市快报", "新京报", "大众日报", "上海热线", "财联社"]

# 逐渠道的检索入口与已知坑（采集侧全流程说明 阶段2 失败分支）
ENTRY = {
    "微博": ("站内搜索 / 话题页", "需登录；无稳定日期筛选，靠结果里的时间判断"),
    "抖音": ("站内搜索 / 话题", "需登录；**无日期筛选**（阶段2 实测），排序不可靠"),
    "小红书": ("站内搜索 / 笔记", "需登录；**无日期筛选**（阶段2 实测）"),
    "新闻门户": ("百度资讯 + 白名单媒体站内", "有日期筛选；只收白名单里的媒体"),
    "黑猫投诉": ("站内搜索", "**无日期筛选**；多数投诉与本次事件无关，注意区分"),
    "监管通报": ("金融监管总局 / 消协官网栏目", "监管机构自己发的才算；媒体转述归「新闻门户」"),
}

QUERY_HEADER = ["channel", "query", "queried_at", "result_count", "opened_count",
                "kept_count", "drop_reason", "note"]


def resolve_snap_file(job, snap, fn):
    """`snapshot_meta[i].file` 是相对**谁**写的 —— 三种都收。

    作业单 §4.2 的例子写的是**纯文件名**（相对 `原文快照\\`），但 2026-10-21 采集侧
    回的 EV14 样本写成了 `原文快照/EV14_HM_001_黑猫投诉.txt`（相对**作业目录**）。
    这不是谁写错 —— 是我们那张表没写清基址。照基名解析会让 4 条全判"缺文件"、
    整个 H2 红，而文件其实就在盘上。所以按「原文快照目录 → 作业目录 → 绝对路径」
    依次试，命中即用；三种都不中才报缺。
    """
    if not fn:
        return ""
    p = str(fn).replace("\\", "/").strip().strip("/")
    parts = [x for x in p.split("/") if x]
    cands = [os.path.join(snap, os.path.basename(p)),      # 基名：作业单的写法
             os.path.join(snap, *parts),                   # 相对原文快照目录
             os.path.join(job, *parts),                    # 相对作业目录
             p]                                            # 绝对路径 / 当前目录
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0]


def find_job_file(job, name):
    """作业目录里的固定文件名 —— 先找本名，再认带事件前缀的写法。

    采集侧回传常把 `采集清单.json` 命名成 `EV14_采集清单.json`（同理查询记录）。
    带前缀不改语义，兜底认一个；有多个候选则仍按本名报错（不猜）。
    """
    p = os.path.join(job, name)
    if os.path.exists(p):
        return p, False
    stem, ext = os.path.splitext(name)
    try:
        hits = sorted(g for g in os.listdir(job)
                      if g.endswith(ext) and g[:-len(ext)].endswith(stem) and len(g) > len(name))
    except OSError:
        hits = []
    if len(hits) == 1:
        return os.path.join(job, hits[0]), True
    return p, False


def die(msg):
    print("✘ " + msg, file=sys.stderr)
    sys.exit(1)


def norm_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").replace("、", " ").replace("，", " ").replace(",", " ").replace("；", " ").replace(";", " ")
    return [x.strip() for x in s.split() if x.strip()]


def load_json(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(p, obj):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ── 开单 ────────────────────────────────────────────────────────────────
def suggest_queries(card):
    """把参数卡拼成 6 条可粘贴的检索串。拼装口径写在作业单里，允许人按平台习惯改。"""
    subj = (card.get("subject") or "").strip()
    kws = norm_list(card.get("keywords"))
    return {
        "微博": " ".join([x for x in [subj] + kws[:3] if x]),
        "抖音": " ".join([x for x in [subj] + kws[:3] if x]),
        "小红书": " ".join([x for x in [subj] + kws if x]),
        "新闻门户": " ".join([x for x in [subj] + kws[:2] if x]),
        "黑猫投诉": subj,
        "监管通报": " ".join([x for x in [subj, "通报", "处罚", "风险提示"] if x]),
    }


TEMPLATE = """# 检索作业单 · EVT-@@EID3@@ @@ENAME@@

> 这张单子是**给人或 AI 照着做**的。做完把产物交回同一目录，跑最后那条 `收单` 命令。
> 依据：`00_程序与文档\\采集规则.md` §八（7 步）+ `采集侧全流程说明.md` 阶段 2/3。
> **不要改这张单子**（它是开单那一刻的快照），参数变了请重新开单。

## 0. 这一单要什么

| 项 | 值 |
|---|---|
| 事件号 | `@@EID@@`（产物文件名固定为 `@@RAWNAME@@`） |
| 事件名 | @@ENAME@@ |
| 事件类型（8 类之一） | @@ETYPE@@ |
| 检索窗口 | **@@START@@ ~ @@END@@** |
| 主体 | @@SUBJECT@@ |
| 别名 | @@ALIASES@@ |
| 必选词 | @@KEYWORDS@@ |
| 排除词 | @@EXCLUDE@@ |
| 话题标签 | @@TOPICTAG@@ |
| 作业目录 | `@@JOB@@` |

## 1. 参数卡原文（你只需读，不用改）

```json
@@CARDJSON@@
```

## 2. 检索词怎么拼

默认口径：`主体 + 前 3 个必选词`，空格分隔（见 §3 每个渠道的「建议检索串」）。
**允许按平台习惯改写**（加引号、缩短、换同义词），但改写后要能回到参数卡的必选词上 ——
采集链收单时会查正文有没有命中必选词，命不中就会被丢。

## 3. 6 个渠道，逐个过

@@CHANNELS_BLOCK@@

> 新闻门户是**封闭白名单**，清单外媒体一律不收录（进待确认，不擅自扩充）：
> @@WHITELIST@@

## 4. 落档三件套（格式必须一字不差，采集链按这个读）

### 4.1 `原文快照\\<post_id>_<channel>.txt` —— 一帖一个文件

```
URL: https://www.iesdouyin.com/share/video/7642344709613162937
页面标题: 建议某些行业在挑选工作者的时候能够先考察人品
发布时间: 2026-05-21 22:00
@@BODYMARK@@
见不见啊？前有女性下体隐私手术被直播，后有众安保险把女性下体p身上。……
#女生保护好自己 #创作者中心 #创作灵感 #女性安全 #女性健康
```

**四行头不能少**（`URL:` / `页面标题:` / `发布时间:` / 正文标记），之后是**正文全文原样**，
不去表情、不摘要、不翻译。

### 4.2 `原文快照\\snapshot_meta.json` —— 数组，每帖一条

```json
[
  {
    "file": "EV1_DY_001_抖音.txt",
    "post_id": "EV1_DY_001",
    "title": "建议某些行业在挑选工作者的时候能够先考察人品",
    "channel": "抖音",
    "source_platform": "抖音",
    "author_name": "刘嘻嘻",
    "author_uid": "MS4wLjABAAAA...",
    "author_fans": "3612",
    "post_url": "https://www.iesdouyin.com/share/video/7642344709613162937",
    "publish_time": "2026-05-21 22:00",
    "like_cnt": 2977,
    "comment_cnt": 884,
    "repost_cnt": 0,
    "collect_cnt": 99,
    "is_repost": false,
    "keyword_match": "众安保险 宠物险 广告低俗",
    "author_ip_location": "广西",
    "topic_tag": "#女生保护好自己 #女性安全 #女性健康",
    "topic_list": ["众安保险", "宠物险", "广告", "低俗营销", "女性", "私密部位", "P图", "AI", "致歉", "朋友圈"]
  }
]
```

> 三种「空」不同：`null` = 平台没这个概念 / 没抓到；`""` = 页面上就是空；
> `0` = 真的是 0 个赞。**不要统一成一种**，作者 IP 属地那种就常常是 `""`。

### 4.3 `采集清单.json` —— 数组，一条 = 一个检索目标

```json
{
  "post_id": "EV1_DY_001",
  "channel": "抖音",
  "source_platform": "抖音",
  "platform_native_id": "7642344709613162937",
  "post_url": "https://www.iesdouyin.com/share/video/7642344709613162937",
  "author_name": "刘嘻嘻",
  "publish_time": "2026-05-21 22:00",
  "note": "检索目标锁定到该 URL/原生 ID：复现时打开该页面核验并存快照"
}
```

**清单即全集**（`采集规则.md` §六）：名单上的每一帖都要有对应 `.txt` 与 meta 条目。

## 5. `查询记录.csv` —— 每平台记查询词、查询时间、结果数

8 列，6 个渠道各一行（**窗口内确实没有也要写一行，`kept_count` 填 0**）：

| 列 | 填什么 |
|---|---|
| `channel` | 6 类之一（已预填） |
| `query` | 实际发出去的查询串 |
| `queried_at` | `YYYY-MM-DD HH:MM` |
| `result_count` | 平台声称的结果数 / 首屏可见条数（在 `note` 里写清是哪个） |
| `opened_count` | 实际点进去看了几条 |
| `kept_count` | 最后进了快照几条（★ 收单会拿它和文件数对账） |
| `drop_reason` | 没留下的主要原因，分号分隔，如 `窗口外 3；无关 2；营销 1` |
| `note` | 缺口 / 异常，如 `无日期筛选，靠滚动人工翻` |

## 6. 交给 AI 的话（**我方拟的通用版**，不是采集侧原文）

> 采集侧实际用的指令原文没有随资料包给出（已在 `端到端逻辑\\采集侧爬取接口需求\\` 里要过）。
> 下面这段是照 `采集规则.md` 重写的**等价指令**，能直接粘进带浏览器的会话；
> 等采集侧回了原文，以他们的为准。

```
你在替我做一个舆情事件的公开帖子检索。

@@AI_BLOCK@@

规则：
1. 只在以下 6 类渠道里检索：微博、抖音、小红书、新闻门户、黑猫投诉、监管通报。
   新闻门户只收这些媒体：@@WHITELIST@@。
2. 每个渠道单独告诉我：你用的查询串、查询时间、结果数、你打开了几条、最后留下几条、
   没留下的主要原因。
3. 遇到需要登录或验证码的平台，**停下来告诉我**，等我登录完再继续；不要反复刷新重试。
4. 打开原文后，把**正文全文原样**抄给我，不要去表情、不要摘要、不要翻译；
   同时给出 URL、页面标题、发布时间、作者昵称、粉丝数、IP 属地、点赞/评论/转发/收藏数。
5. 拿不准是不是同一事件的**不要丢**，标出来让我判断。
6. 不要编造任何数值；页面不显示就写「无」。
```

## 7. 收单

```powershell
python 检索作业.py 收单 --job "@@JOB@@" --card 参数卡.json --out 输出目录 --collect
```

先把 §4、§5 的产物补齐，再跑这条。检查不过会逐条告诉你是哪一项、哪一条。
"""


def cmd_open(args):
    card = load_json(args.card)
    for k in ("event_id", "start_date", "end_date"):
        if card.get(k) in (None, ""):
            die("参数卡缺关键项 `%s`：%s" % (k, args.card))
    job = os.path.abspath(args.out)
    snap = os.path.join(job, "原文快照")
    os.makedirs(snap, exist_ok=True)

    eid = int(card["event_id"])
    queries = suggest_queries(card)
    ch_block = "\n".join(
        "### %s\n\n- **入口**：%s\n- **建议检索串**：`%s`\n- **已知坑**：%s\n"
        "- **这一格要回来**：查询时间、结果数、打开几条、留下几条、没留下的原因\n"
        % (ch, ENTRY[ch][0], queries[ch] or "（参数卡里没给检索词，请自己拟）", ENTRY[ch][1])
        for ch in CHANNELS)
    ai_block = ("事件：%s（%s）\n"
                "检索窗口：%s 至 %s。主体：%s。\n"
                "必选词：%s。\n"
                "排除词：%s。"
                % (card.get("event_name") or "", card.get("event_type") or "未定",
                   card["start_date"], card["end_date"], card.get("subject") or "（未给）",
                   "、".join(norm_list(card.get("keywords"))) or "（无）",
                   "、".join(norm_list(card.get("exclude_words"))) or "（无）"))
    repl = {
        "@@EID3@@": "%03d" % eid,
        "@@EID@@": str(eid),
        "@@RAWNAME@@": "EV%d_raw.json" % eid,
        "@@ENAME@@": card.get("event_name") or "",
        "@@ETYPE@@": card.get("event_type") or "（未定）",
        "@@START@@": card["start_date"],
        "@@END@@": card["end_date"],
        "@@SUBJECT@@": card.get("subject") or "（未给）",
        "@@ALIASES@@": "、".join(norm_list(card.get("aliases"))) or "（无）",
        "@@KEYWORDS@@": "、".join(norm_list(card.get("keywords"))) or "（无）",
        "@@EXCLUDE@@": "、".join(norm_list(card.get("exclude_words"))) or "（无）",
        "@@TOPICTAG@@": card.get("topic_tag") or "（无）",
        "@@JOB@@": job,
        "@@CARDJSON@@": json.dumps(card, ensure_ascii=False, indent=2),
        "@@CHANNELS_BLOCK@@": ch_block,
        "@@WHITELIST@@": "、".join(NEWS_WHITELIST),
        "@@BODYMARK@@": BODY_MARK,
        "@@AI_BLOCK@@": ai_block,
    }
    md = TEMPLATE
    for k, v in repl.items():
        md = md.replace(k, v)

    with open(os.path.join(job, "检索作业单.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)

    with open(os.path.join(job, "查询记录.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(QUERY_HEADER)
        for ch in CHANNELS:
            w.writerow([ch] + [""] * 7)

    write_json(os.path.join(job, "采集清单.json"), [])
    write_json(os.path.join(snap, "snapshot_meta.json"), [])
    with open(os.path.join(snap, "_格式说明.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("一帖一个 txt，文件名 <post_id>_<channel>.txt，四行头 + 正文全文：\n\n"
                "URL: <可点开看原帖的详情页>\n页面标题: <页面标题>\n发布时间: YYYY-MM-DD HH:MM\n"
                + BODY_MARK + "\n<正文全文原样>\n")

    print("✅ 已开单：EVT-%03d %s" % (eid, card.get("event_name") or ""))
    print("   作业目录：%s" % job)
    print("   窗口：%s ~ %s" % (card["start_date"], card["end_date"]))
    for ch in CHANNELS:
        print("   - %-4s 建议检索串：%s" % (ch, queries[ch] or "（自己拟）"))
    print("\n   下一步：照《检索作业单.md》检索并把产物填进本目录，然后跑")
    print("   python 检索作业.py 收单 --job \"%s\" --card \"%s\" --out 输出目录 --collect"
          % (job, os.path.abspath(args.card)))
    return 0


# ── 收单 ────────────────────────────────────────────────────────────────
def cmd_collect(args):
    job = os.path.abspath(args.job)
    snap = os.path.join(job, "原文快照")
    out = os.path.abspath(args.out or os.path.join(job, "_收单输出"))
    fails, warns = [], []

    def ok(cond, label, detail=""):
        print("%s %s%s" % ("✔" if cond else "✘", label, ("   " + detail) if detail else ""))
        if not cond:
            fails.append(label)
        return cond

    def warn(cond, label, detail=""):
        print("%s %s%s" % ("✔" if cond else "⚠", label, ("   " + detail) if detail else ""))
        if not cond:
            warns.append(label)
        return cond

    print("=" * 74)
    print("收单校验 ｜ %s" % job)
    print("=" * 74)

    # H1
    meta_p = os.path.join(snap, "snapshot_meta.json")
    metas = []
    if ok(os.path.exists(meta_p), "H1 原文快照\\snapshot_meta.json 存在", meta_p):
        try:
            blob = load_json(meta_p)
        except Exception as e:
            ok(False, "H1b 可解析", "%s: %s" % (type(e).__name__, e))
            blob = None
        if ok(isinstance(blob, list), "H1c 是 JSON 数组",
              "%d 条" % len(blob) if isinstance(blob, list) else "拿到的是 %s" % type(blob).__name__):
            metas = blob

    # H1d：**空快照不算收单成功**。0 条在下游每一道闸上都是"能过"的
    #      （条数对 0 == 0、覆盖集为空集），不在这里拦就会一路绿灯跑到
    #      `raw = []`，看起来像"成功采到 0 条"。没有帖，就是这一单没做完。
    ok(bool(metas), "H1d 快照非空（这一单至少交一帖）",
       "%d 条" % len(metas) if metas else "0 条 —— 照作业单检索完、落好档再来收单")

    # H2 / H3 / H4 + 警告
    missing_file, bad_ch, empty_id, dup_id, no_url, bad_head = [], [], [], [], [], []
    seen = set()
    for i, m in enumerate(metas, 1):
        if not isinstance(m, dict):
            continue
        fn = m.get("file") or ""
        fpath = resolve_snap_file(job, snap, fn)
        if not fn or not os.path.exists(fpath):
            missing_file.append((i, fn or "（空）"))
        else:
            with open(fpath, encoding="utf-8-sig") as f:
                txt = f.read()
            if BODY_MARK not in txt or "URL:" not in txt or "发布时间:" not in txt:
                bad_head.append(fn)
        ch = m.get("channel")
        if ch not in CHANNELS:
            bad_ch.append((i, ch))
        pid = str(m.get("post_id") or "").strip()
        if not pid:
            empty_id.append(i)
        elif pid in seen:
            dup_id.append(pid)
        else:
            seen.add(pid)
        if not str(m.get("post_url") or "").strip():
            no_url.append(i)

    ok(not missing_file, "H2 每条 meta 的 file 真的存在",
       ("%d 条缺文件，首个：%s" % (len(missing_file), missing_file[0][1])) if missing_file
       else "%d 条" % len(metas))
    ok(not bad_ch, "H3 channel 全在 6 类内",
       ("第 %d 条 = %r" % bad_ch[0]) if bad_ch else
       ("、".join("%s×%d" % (c, sum(1 for m in metas if isinstance(m, dict) and m.get("channel") == c))
                 for c in CHANNELS
                 if any(isinstance(m, dict) and m.get("channel") == c for m in metas)) or "（0 条）"))
    ok(not empty_id and not dup_id, "H4 post_id 非空且唯一",
       ("空：%s；重复：%s" % (empty_id[:3], dup_id[:3])) if (empty_id or dup_id)
       else "%d 个唯一" % len(seen))
    warn(not bad_head, "txt 四行头完整（警告）",
         ("%d 个缺头，首个：%s" % (len(bad_head), bad_head[0])) if bad_head else "全部完整")
    warn(not no_url, "每条都有 post_url（警告）",
         ("%d 条为空" % len(no_url)) if no_url else "全部有")

    # H5 / H9
    list_p, list_prefixed = find_job_file(job, "采集清单.json")
    if list_prefixed:
        warn(False, "H5c 采集清单用了带前缀的文件名（警告）",
             "%s —— 认了，但作业单要的是 `采集清单.json`" % os.path.basename(list_p))
    lst = []
    if ok(os.path.exists(list_p), "H5 采集清单.json 存在", list_p):
        try:
            blob = load_json(list_p)
        except Exception as e:
            ok(False, "H5b 可解析", "%s: %s" % (type(e).__name__, e))
            blob = None
        if ok(isinstance(blob, list), "H5c 是 JSON 数组",
              "%d 条" % len(blob) if isinstance(blob, list) else ""):
            lst = blob
    list_ids = {str(x.get("post_id") or "").strip() for x in lst if isinstance(x, dict)}
    miss_in_list = sorted(seen - list_ids)
    ok(not miss_in_list, "H9 采集清单覆盖快照里的每一帖（清单即全集）",
       ("缺 %d 个：%s" % (len(miss_in_list), "、".join(miss_in_list[:3]))) if miss_in_list
       else "%d 个对得上" % len(seen))
    extra = sorted(x for x in (list_ids - seen) if x)
    warn(not extra, "清单里没有多余条目（警告）",
         ("多 %d 个：%s" % (len(extra), "、".join(extra[:3]))) if extra else "无多余")

    # H6 / H7 / H8
    q_p, q_prefixed = find_job_file(job, "查询记录.csv")
    if q_prefixed:
        warn(False, "H6c 查询记录用了带前缀的文件名（警告）",
             "%s —— 认了，但作业单要的是 `查询记录.csv`" % os.path.basename(q_p))
    rows = []
    if ok(os.path.exists(q_p), "H6 查询记录.csv 存在", q_p):
        with open(q_p, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]
        head = list(rows[0].keys()) if rows else []
        lack = [k for k in QUERY_HEADER if k not in head]
        ok(rows and not lack, "H6b 8 列齐全",
           "（无数据行）" if not rows else ("缺：%s" % "、".join(lack) if lack else "8 列都对"))

    by_ch = {}
    for r in rows:
        by_ch.setdefault((r.get("channel") or "").strip(), []).append(r)
    absent = [c for c in CHANNELS if c not in by_ch]
    ok(not absent, "H7 6 个渠道每个都有查询记录",
       ("缺：%s（窗口内没有也要写一行，kept_count=0）" % "、".join(absent)) if absent else "6/6 齐")
    unknown = [c for c in by_ch if c not in CHANNELS]
    if unknown:
        warn(False, "查询记录里有 6 类之外的渠道", "、".join(unknown))

    def _int(v):
        try:
            return int(float(str(v).strip() or 0))
        except ValueError:
            return 0

    claimed = sum(_int(r.get("kept_count")) for r in rows)
    ok(claimed == len(metas), "H8 查询记录 kept_count 合计 == 快照条数",
       "记录里 %d 条，快照里 %d 条%s" % (claimed, len(metas),
                                    "" if claimed == len(metas) else "（对不上：漏填或漏交）"))
    blank_q = [r.get("channel") for r in rows if not str(r.get("query") or "").strip()]
    blank_r = [r.get("channel") for r in rows if not str(r.get("result_count") or "").strip()]
    warn(not blank_q, "每条都有 query（警告）", "、".join(blank_q) if blank_q else "全部有")
    warn(not blank_r, "每条都有 result_count（警告）", "、".join(blank_r) if blank_r else "全部有")

    os.makedirs(out, exist_ok=True)
    write_json(os.path.join(out, "收单校验.json"), {
        "job": job, "metas": len(metas), "query_rows": len(rows), "claimed_kept": claimed,
        "fails": fails, "warns": warns, "ts": date.today().isoformat()})

    print("\n" + "=" * 74)
    if fails:
        print("✘ %d 项硬性检查没过：%s" % (len(fails), "；".join(fails)))
        print("   补齐后重跑本命令。")
        return 1
    if warns:
        print("⚠ %d 项警告：%s" % (len(warns), "；".join(warns)))
    print("✔ 收单通过：快照 %d 条 / 查询记录 %d 行 / 6 渠道齐" % (len(metas), len(rows)))

    if not args.collect:
        print("\n   加 --collect 可顺手跑采集链（三层过滤 → 21 字段 raw）。")
        return 0

    if not args.card or not os.path.exists(args.card):
        die("--collect 需要 --card <参数卡.json>")
    card = load_json(args.card)
    argv = [sys.executable, CHAIN, "--mode", "snapshot", "--card", os.path.abspath(args.card),
            "--snapshot-dir", snap, "--out", out]
    print("\n→ %s" % " ".join(argv))
    sys.stdout.flush()          # 否则子进程的输出会抢在本函数的缓冲之前打印，报告顺序会错乱
    rc = subprocess.run(argv).returncode
    if rc != 0:
        print("\n✘ 采集链返回 %d（窗口内一条都没留下，或快照与参数卡对不上）" % rc)
        return rc
    print("\n✔ 收单完成：%s" % os.path.join(out, "EV%d_raw.json" % int(card["event_id"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description="检索作业：开单 / 收单")
    ap.add_argument("stage", choices=["开单", "收单", "open", "collect"])
    ap.add_argument("--card", help="参数卡 JSON")
    ap.add_argument("--out", help="开单 = 作业目录；收单 = 输出目录（不给则作业目录\\_收单输出）")
    ap.add_argument("--job", help="收单：作业目录")
    ap.add_argument("--collect", action="store_true", help="收单通过后顺手调 采集链.py --mode snapshot")
    a = ap.parse_args()

    stage = {"open": "开单", "collect": "收单"}.get(a.stage, a.stage)
    if stage == "开单":
        if not a.card or not os.path.exists(a.card):
            die("开单需要 --card <参数卡.json>")
        if not a.out:
            die("开单需要 --out <作业目录>")
        return cmd_open(a)

    if not a.job or not os.path.isdir(a.job):
        die("收单需要 --job <作业目录>")
    return cmd_collect(a)


if __name__ == "__main__":
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
