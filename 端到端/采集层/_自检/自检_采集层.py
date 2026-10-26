# -*- coding: utf-8 -*-
r"""采集层自检 —— 把 README 里每一句"能跑"都跑一遍。

    python -X utf8 _自检\自检_采集层.py

它不读结论、只跑命令再读产物，六组断言：

  [1] 快照回放：`采集链.py --mode snapshot` → 与采集侧正式 `EVT-001_raw.json` **逐字段比对**
  [2] 三层过滤：`--mode api` 跑样例适配器 → 保留 1 条，窗口外 / 排除词两条被丢，理由对得上
  [3] 全链：`一键演示.py --no-llm` → raw 21 字段 → processed 28 字段 → 看板 HTML 产出
  [4] 缺件如实报错：`--mode live` 在没放爬虫时必须**非 0 退出并说明缺什么**（不能静默返回空）
  [5] 能力自述：`--check` 的 JSON 与磁盘实际情况一致（样本数、爬虫在不在）
  [6] 检索作业：`检索作业.py 开单 → 填 → 收单` 走通，产物与正式 raw 逐字段一致；
      再故意漏一个渠道行 / 改坏 kept_count，收单必须**非 0 退出并点名**

全部用临时目录，跑完就删；除本脚本外**不改交付里的任何文件**。
退出码 0 = 全部通过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 采集层\
LINK = os.path.join(HERE, "采集链.py")
DEMO = os.path.join(HERE, "一键演示.py")
CARD = os.path.join(HERE, "EVT-001_宠物险广告低俗", "EVT-001_参数卡.json")
SNAP = os.path.join(HERE, "EVT-001_宠物险广告低俗", "原文快照")
RAW = os.path.join(HERE, "EVT-001_宠物险广告低俗", "EVT-001_raw.json")
ADAPTER = os.path.join(HERE, "采集适配器_模板.py")
JOB = os.path.join(HERE, "检索作业.py")

FAILS = []


def ok(cond, label, detail=""):
    print("%s %s%s" % ("✔" if cond else "✘", label, ("   " + detail) if detail else ""))
    if not cond:
        FAILS.append(label)
    return cond


def head(t):
    print("\n" + "-" * 74)
    print(t)
    print("-" * 74)


def run(args, cwd=None):
    r = subprocess.run([sys.executable] + args, capture_output=True,
                       encoding="utf-8", errors="replace", cwd=cwd,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def jload(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="采集层自检_")
    try:
        # ── [1] 快照回放 vs 正式数据 ──────────────────────────────────
        head("[1] 快照回放：回放产物 vs 采集侧正式数据（21 字段）")
        d1 = os.path.join(tmp, "s1")
        rc, out = run([LINK, "--mode", "snapshot", "--card", CARD,
                       "--snapshot-dir", SNAP, "--out", d1, "--capture-date", "2026-10-01"])
        ok(rc == 0, "采集链 exit 0", out.strip().splitlines()[0] if out.strip() else "")
        got, want = jload(os.path.join(d1, "EV1_raw.json")), jload(RAW)
        ok(len(got) == len(want), "条数一致", "%d / %d" % (len(got), len(want)))
        diff = [(i, k) for i, (a, b) in enumerate(zip(got, want)) for k in b if a.get(k) != b.get(k)]
        ok(not diff, "逐字段一致（%d 条 × 21 字段）" % len(want),
           "、".join("第%d条.%s" % (i + 1, k) for i, k in diff[:6]))
        ok(list(got[0].keys()) == list(want[0].keys()), "字段顺序一致", " / ".join(list(got[0].keys())[:4]) + " …")
        log = [json.loads(x) for x in open(os.path.join(d1, "采集日志.jsonl"), encoding="utf-8").read().splitlines() if x.strip()]
        ok(log[0].get("kept") == len(got) and len(log) == len(got) + 1,
           "采集日志：1 行汇总 + 逐帖一行", "%d 行" % len(log))

        # ── [2] 三层过滤真的在过滤 ────────────────────────────────────
        head("[2] 过滤：样例适配器 4 条 → 只留 1 条")
        d2 = os.path.join(tmp, "s2")
        rc, out = run([LINK, "--mode", "api", "--card", CARD, "--out", d2,
                       "--adapter", ADAPTER, "--capture-date", "2026-10-01"])
        ok(rc == 0, "适配器模式 exit 0")
        rows = jload(os.path.join(d2, "EV1_raw.json"))
        ok(len(rows) == 1 and rows[0].get("post_id") == "EV1_WB_901",
           "保留的正是窗口内那条", "post_id=%s" % (rows[0].get("post_id") if rows else None))
        ok("窗口外" in out, "窗口外的被丢，理由写明")
        ok("排除词" in out and "宠物店" in out, "命中排除词的被丢，理由写明")
        ok("知乎" in out, "不在 6 类枚举的渠道被拒收，并喊了一声")

        # ── [3] 全链：raw → processed → 看板 ─────────────────────────
        head("[3] 全链（快照回放 → 结构化 → 看板，--no-llm）")
        d3 = os.path.join(tmp, "s3")
        rc, out = run([DEMO, "--card", CARD, "--snapshot-dir", SNAP, "--out", d3, "--no-llm"])
        ok(rc == 0, "一键演示 exit 0")
        raw3 = jload(os.path.join(d3, "EV1_raw.json"))
        ok(len(raw3) > 0, "raw 产出", "%d 条" % len(raw3))
        proc = jload(os.path.join(d3, "EV1_processed.json"))
        ok(len(proc) == len(raw3) and len(proc[0]) == 28,
           "processed 28 字段（且行数与 raw 相同）",
           "%d 条 / %d 字段" % (len(proc), len(proc[0]) if proc else 0))
        dash = os.path.join(d3, "EV1_舆情看板.html")
        ok(os.path.exists(dash) and os.path.getsize(dash) > 10000, "看板 HTML 产出",
           "%.1f KB" % (os.path.getsize(dash) / 1024.0 if os.path.exists(dash) else 0))
        html = open(dash, encoding="utf-8").read()
        # 「零外链」说的是**资源**零外链（脚本/样式/字体/图片），帖子自己的原文链接不算
        import re as _re
        # `<a href="原文链接">` 是帖子出处，本来就该外链；这里只抓**会被浏览器自动加载**的资源
        ext = _re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*'
                          r'(?:src|href)\s*=\s*["\']https?://'
                          r'|@import\s+(?:url\()?\s*["\']?https?://', html, _re.I)
        ok(not ext, "看板资源零外链（断网可开）", "%d 处" % len(ext))

        # ── [4] 缺件必须报错，不能静默成功 ───────────────────────────
        head("[4] 实时抓取缺程序时：必须报错并说清缺什么")
        d4 = os.path.join(tmp, "s4")
        env = dict(os.environ)
        for k in ("YUQING_COLLECTOR",):
            env.pop(k, None)
        r = subprocess.run([sys.executable, LINK, "--mode", "live", "--card", CARD, "--out", d4],
                           capture_output=True, encoding="utf-8", errors="replace",
                           env=dict(env, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
        blob = (r.stdout or "") + (r.stderr or "")
        have = os.path.exists(os.path.join(HERE, "yuqing_collector.py"))
        if have:
            ok(True, "本目录已放爬虫，跳过这条（换机器验证时再跑）")
        else:
            ok(r.returncode != 0, "exit 非 0（没有假装成功）", "rc=%d" % r.returncode)
            ok("yuqing_collector.py" in blob, "报错里点名缺哪个文件")
            ok("--mode api" in blob or "适配器" in blob, "顺带给替代路线")

        # ── [5] 能力自述与磁盘一致 ───────────────────────────────────
        head("[5] --check 的自述与磁盘实际情况")
        rc, out = run([LINK, "--check"])
        ok(rc == 0, "--check exit 0")
        info = json.loads(out[out.index("{"):])
        n_snap = len([d for d in os.listdir(HERE)
                      if d.startswith("EVT-") and os.path.isdir(os.path.join(HERE, d, "原文快照"))])
        ok(len(info["snapshot"]["samples"]) == n_snap, "样本场次数一致", "%d 场" % n_snap)
        ok(info["live"]["ok"] == os.path.exists(os.path.join(HERE, "yuqing_collector.py")),
           "live 可用性 == 爬虫在不在", "ok=%s" % info["live"]["ok"])
        ok(info["api"]["ok"] is True, "api 模式恒可用（契约自带）")

        # ── [6] 检索作业：开单 → 填 → 收单 ───────────────────────────
        head("[6] 检索作业：开单 → 按单落档 → 收单 → raw（与正式数据逐字段比）")
        j = os.path.join(tmp, "j6")
        rc, out = run([JOB, "开单", "--card", CARD, "--out", j])
        ok(rc == 0, "开单 exit 0")
        need = ["检索作业单.md", "查询记录.csv", "采集清单.json",
                os.path.join("原文快照", "snapshot_meta.json")]
        ok(all(os.path.exists(os.path.join(j, x)) for x in need),
           "四个空模板都生成了", "、".join(os.path.basename(x) for x in need))
        md = open(os.path.join(j, "检索作业单.md"), encoding="utf-8").read()
        ok("@@" not in md, "作业单没有漏替换的占位符", "%d 字" % len(md))
        ok(all(c in md for c in ("微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报")),
           "6 个渠道都在作业单里")

        # 把 EVT-001 的现成快照当成"这次检索的成果"填进去（等价于有人照着单子做完）
        for fn in os.listdir(SNAP):
            shutil.copy(os.path.join(SNAP, fn), os.path.join(j, "原文快照", fn))
        shutil.copy(os.path.join(HERE, "EVT-001_宠物险广告低俗", "EVT-001_采集清单.json"),
                    os.path.join(j, "采集清单.json"))
        with open(os.path.join(j, "查询记录.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("channel,query,queried_at,result_count,opened_count,kept_count,drop_reason,note\n")
            f.write("微博,众安保险 宠物险,2026-10-01 15:05,0,0,0,,需登录，登录后无结果\n")
            f.write("抖音,众安保险 宠物险 广告低俗 P图,2026-10-01 14:32,约 40,7,1,窗口外 3；无关 2；营销 1,无日期筛选\n")
            f.write("小红书,众安保险 宠物险 广告低俗,2026-10-01 14:40,约 120,9,2,无关 4；营销 2；重复 1,\n")
            f.write("新闻门户,众安保险 宠物险 广告低俗,2026-10-01 15:20,约 15,4,0,窗口外 4,只收白名单媒体\n")
            f.write("黑猫投诉,众安保险,2026-10-01 15:30,约 3,3,0,与本次事件无关,无日期筛选\n")
            f.write("监管通报,众安保险 通报 处罚 风险提示,2026-10-01 15:40,0,0,0,,本期无相关通报\n")

        d6 = os.path.join(tmp, "s6")
        rc, out = run([JOB, "收单", "--job", j, "--card", CARD, "--out", d6, "--collect"])
        ok(rc == 0, "收单 + 采集链 exit 0")
        for h in ("H7 6 个渠道每个都有查询记录", "H8 查询记录 kept_count 合计 == 快照条数",
                  "H9 采集清单覆盖快照里的每一帖"):
            ok(h in out and ("✘ " + h) not in out, "对账项通过：%s" % h.split(" ", 1)[1])
        got6, want6 = jload(os.path.join(d6, "EV1_raw.json")), jload(RAW)
        diff6 = [(i, k) for i, (a, b) in enumerate(zip(got6, want6))
                 for k in b if k != "capture_date" and a.get(k) != b.get(k)]
        ok(len(got6) == len(want6) and not diff6,
           "作业收上来的 raw 与正式数据一致（除 capture_date）",
           "%d 条 × 20 字段" % len(got6) if not diff6 else "、".join("第%d条.%s" % (i + 1, k) for i, k in diff6[:5]))

        # 负例：漏一个渠道行 + 改坏 kept_count ⇒ 必须非 0 退出并点名
        jb = os.path.join(tmp, "j6bad")
        shutil.copytree(j, jb)
        lines = open(os.path.join(jb, "查询记录.csv"), encoding="utf-8-sig").read().splitlines()
        bad = [x for x in lines if not x.startswith("监管通报")]
        bad = [x.replace(",2,无关", ",1,无关") if x.startswith("小红书") else x for x in bad]
        with open(os.path.join(jb, "查询记录.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(bad) + "\n")
        rc, out = run([JOB, "收单", "--job", jb, "--card", CARD, "--out", os.path.join(tmp, "s6b")])
        ok(rc != 0, "漏渠道 / 条数对不上 ⇒ 收单 exit 非 0", "rc=%d" % rc)
        ok("H7" in out and "监管通报" in out, "点名缺哪个渠道")
        ok("H8" in out, "点名 kept_count 与快照对不上")

        # ── 汇总 ─────────────────────────────────────────────────────
        print("\n" + "=" * 74)
        if FAILS:
            print("✘ %d 项没过：%s" % (len(FAILS), "；".join(FAILS)))
            return 1
        print("✔ 采集层全部通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.exit(main())
