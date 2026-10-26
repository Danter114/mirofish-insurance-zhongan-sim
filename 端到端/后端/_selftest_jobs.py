# -*- coding: utf-8 -*-
r"""任务状态机自检：起一个**空转**的仿真作业，验证 子进程 / 落盘 / 探活 / 日志尾 都通。

    python -I _selftest_jobs.py

为什么是空转：EVT-001 的 42 轮已经跑完了，`跑全轮.py` 是自愈循环，
一进来就发现"全部完成"直接退出 —— 这是**唯一一种**能在不烧 LLM 的前提下
把子进程链路整条走通的测法。
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
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


print("=" * 78)
print("任务状态机自检 ｜ 事件 %s" % EID)
print("=" * 78)

cfg = paths.load_config(EID)
inv = progress.inventory(cfg)
print("  轮次：%d / %d   报告：%s" % (inv["rounds_done"], inv["rounds_total"],
                                     "有" if inv["report_exists"] else "无"))

check(jobs.status(EID).get("state") in ("idle", "done", "failed", "stopped"),
      "起始状态可读", "= %s" % jobs.status(EID).get("state"))

print("\n[1] 启动（预期：发现 42 轮已跑完，秒退 0）")
d = jobs.start(EID, "sim")
check(d["state"] == "running", "启动返回 running", "pid=%s" % d["pid"])
check(jobs.is_busy(EID), "is_busy() 为真")

# 立刻再起一个应当被拒
try:
    jobs.start(EID, "sim")
    check(False, "并发启动被拒")
except jobs.JobError as e:
    check(True, "并发启动被拒", str(e)[:40])

print("\n[2] 等它退")
t0 = time.time()
while time.time() - t0 < 60:
    st = jobs.status(EID)
    if st["state"] != "running":
        break
    time.sleep(0.3)
st = jobs.status(EID)
check(st["state"] == "done", "退出状态 done", "= %s（%.1fs）" % (st["state"], time.time() - t0))
check(st["exit_code"] == 0, "退出码 0", "= %s" % st["exit_code"])

print("\n[3] 落盘 / 日志尾")
jf = os.path.join(paths.runtime_dir(EID), "job.json")
check(os.path.exists(jf), "job.json 已落盘", jf)
on_disk = json.load(open(jf, encoding="utf-8"))
check(on_disk["state"] == "done" and on_disk["ended_at"], "落盘内容与实际一致")
tl = jobs.tail(EID, "sim", 40)
check(bool(tl), "日志尾非空", "%d 行" % len(tl))
for ln in tl[:6]:
    print("      | %s" % ln[:110])
print("      ...")
for ln in tl[-3:]:
    print("      | %s" % ln[:110])

print("\n[4] 结果正确性：日志里应出现「全部 42 轮已完成」")
check(any("全部" in ln and "轮已完成" in ln for ln in tl), "空转判定正确")

print("\n[5] 停一个没在跑的任务应报错")
try:
    jobs.stop(EID)
    check(False, "stop() 对空闲任务报错")
except jobs.JobError as e:
    check(True, "stop() 对空闲任务报错", str(e)[:40])

print("=" * 78)
if fails:
    print("✘ 不通过：%s" % "；".join(fails))
    sys.exit(1)
print("✔ 全部通过")
