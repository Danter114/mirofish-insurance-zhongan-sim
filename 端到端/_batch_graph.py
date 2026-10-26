# -*- coding: utf-8 -*-
"""批量跑图谱段（只到 --to 图谱），逐场记一条结果，不因单场失败中断。"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KG = os.path.join(os.path.dirname(HERE), "kgcopy", "输入")
PY = sys.executable
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

keys = sys.argv[1:] or ["EVT-%03d" % i for i in range(1, 13)]
rows = []
for k in keys:
    n = int(k.split("-")[1])
    ev = os.path.join(KG, "event%d.json" % n)
    if not os.path.exists(ev):
        rows.append((k, "SKIP", "no event file"))
        print("%s SKIP 没有 %s" % (k, ev), flush=True)
        continue
    p = subprocess.run([PY, os.path.join(HERE, "跑端到端_v3.py"), "--key", k,
                        "--event", ev, "--to", "图谱"],
                       cwd=HERE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    tag, why = ("OK", "")
    if p.returncode != 0:
        tag = "FAIL"
        why = next((l.strip() for l in out.splitlines() if l.strip().startswith("✗")), out[-300:])
    else:
        why = next((l.strip() for l in out.splitlines() if "捏人" in l and "✔" in l), "")
    rows.append((k, tag, why))
    print("%s %s %s" % (k, tag, why), flush=True)

print("\n=== 汇总 ===")
for k, t, w in rows:
    print("%-8s %-5s %s" % (k, t, w))
