# -*- coding: utf-8 -*-
r"""报告作业自检：从后端起一次真正的「生成报告」，并验证产物**逐位不变**。

    python -I _selftest_report.py [EVID]

这条用例比 `_selftest_jobs.py` 走得深一层：那个只验证子进程链路（空转秒退），
这个真的让报告层跑完 extract → alert → build 三步。

关键断言是**逐位比对**：报告层是确定性的（同一份数据包出同一份 HTML），
所以重跑一次的 sha256 必须与跑之前一模一样。不相等说明这次改动动了渲染结果 ——
那正是本轮最怕出的事。跑完把原文件放回去，不把测试产物留在交付包里。
"""
import hashlib
import io
import json
import os
import shutil
import sys
import time

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobs      # noqa: E402
import paths     # noqa: E402
import progress  # noqa: E402

EID = sys.argv[1] if len(sys.argv) > 1 else "EVT-001"
fails = []


def check(cond, label, extra=""):
    print("%s %s%s" % ("✔" if cond else "✘", label, ("   " + extra) if extra else ""))
    if not cond:
        fails.append(label)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


print("=" * 78)
print("报告作业自检 ｜ 事件 %s" % EID)
print("=" * 78)

cfg = paths.load_config(EID)
inv = progress.inventory(cfg)
html = inv["report_path"]
data = os.path.join(cfg.run_dir, cfg["data_file"])

check(bool(html) and os.path.exists(html), "已有报告可作基准", html)
before_html = sha(html)
before_data = sha(data) if os.path.exists(data) else None
before_size = os.path.getsize(html)
print("  基准：报告 %d bytes  sha256 %s…" % (before_size, before_html[:16]))

bak = html + ".selftest.bak"
shutil.copy2(html, bak)

print("\n[1] 起任务")
d = jobs.start(EID, "report")
check(d["state"] == "running", "启动返回 running", "pid=%s" % d["pid"])
check(d["kind"] == "report", "任务类型是 report")

print("\n[2] 等它跑完")
t0 = time.time()
while time.time() - t0 < 300:
    st = jobs.status(EID)
    if st["state"] != "running":
        break
    time.sleep(0.5)
st = jobs.status(EID)
check(st["state"] == "done", "状态 done", "= %s（%.1fs）" % (st["state"], time.time() - t0))
check(st["exit_code"] == 0, "退出码 0", "= %s" % st["exit_code"])

print("\n[3] 三步都跑到了")
tl = jobs.tail(EID, "report", 400)
txt = "\n".join(tl)
for s in ("viz_extract.py", "viz_alert.py", "viz_build.py"):
    check(any(s in ln and "OK" in ln for ln in tl), "日志里有 %s OK" % s)
check("卡在" not in txt, "没有卡在中途")

print("\n[4] 产物逐位不变（报告层是确定性的）")
after_html = sha(html)
check(after_html == before_html, "报告 sha256 与跑之前一致",
      "%s… vs %s…" % (after_html[:16], before_html[:16]))
check(os.path.getsize(html) == before_size, "报告字节数与跑之前一致",
      "%d = %d" % (os.path.getsize(html), before_size))
if before_data:
    check(sha(data) == before_data, "数据包 sha256 与跑之前一致")

print("\n[5] 还原（不把测试产物留在交付包里）")
shutil.move(bak, html)
check(sha(html) == before_html, "已还原成基准报告")

shutil.rmtree(paths.runtime_dir(EID), ignore_errors=True)
check(not os.path.exists(os.path.join(paths.runtime_dir(EID), "job.json")),
      "顺手清掉本次作业状态（演示要从干净状态开始）")

print("=" * 78)
if fails:
    print("✘ 不通过：%s" % "；".join(fails))
    sys.exit(1)
print("✔ 全部通过")
