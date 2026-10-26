# -*- coding: utf-8 -*-
"""报告生成器 · 端到端入口（一份事件配置 → 一份报告 HTML）。

    python 生成报告.py [--config PATH] [--check] [--skip-extract]

三步（顺序不可换）：
    1. viz_extract.py   快照 / 记录员 / 画像 → `_viz_data.json`（事件数据包）
    2. viz_alert.py     算预警等级，把 `alert` 块回写进数据包
    3. viz_build.py     渲染自包含报告 HTML

⚠ 第 1、2 步的顺序是**硬约束**：`viz_extract.py` 会**整体重写**数据包，把上一步算好的
   `alert` 键冲掉。所以「只跑 extract 不跑 alert」会让报告缺掉整个模块四 —— 必须连跑。

`--check` 会在生成后顺带跑一遍报告层体检（`_go.py`：viz_build → mkchk → smoke3 → 六探针）。
体检只回答「有没有问题」，**不判断「数字对不对」** —— 数字请对着正文核。

换事件：复制 `报告配置.example.json` → 填好 → `python 生成报告.py --config 那份.json`。
注意：仿真产物（快照 / 记录员 / 画像）须由仿真层先在 `run_dir` 里跑出来，本脚本只负责报告层。
"""
import argparse, io, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from _report_config import load as _load_cfg

STEPS = [("viz_extract.py", "抽取数据包"), ("viz_alert.py", "算预警等级"), ("viz_build.py", "渲染报告")]


def run_step(script, label, cfg_path, extra=()):
    cmd = [sys.executable, os.path.join(HERE, script)]
    if cfg_path:
        cmd += ["--config", cfg_path]
    cmd += list(extra)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=HERE)
    dt = time.time() - t0
    ok = (r.returncode == 0)
    print("  [%s] %-14s %-10s %5.1fs" % ("OK" if ok else "FAIL", script, label, dt))
    return ok


def main():
    ap = argparse.ArgumentParser(description="由事件配置生成仿真推演报告")
    ap.add_argument("--config", default=None, help="事件配置 JSON（默认同目录 报告配置.json）")
    ap.add_argument("--check", action="store_true", help="生成后顺带跑报告层体检（_go.py）")
    ap.add_argument("--skip-extract", action="store_true",
                    help="跳过第 1 步（沿用已有数据包）。★ 仍会跑 alert，故不会丢模块四")
    a = ap.parse_args()

    cfg = _load_cfg(argv=sys.argv[1:])
    cfg_path = cfg["_path"]
    print("=" * 78)
    print(cfg.describe())
    print("  配置文件 :", cfg_path)
    print("=" * 78)

    # 前置检查：仿真产物是否齐
    missing = [p for p in (cfg.data_path,) if not os.path.exists(p)]
    has_snap = os.path.exists(cfg.snap_path(0))
    print("\n[前置] 快照 t0 :", "有" if has_snap else "** 缺 **", cfg.snap_path(0))
    print("[前置] 记录员   :",
          "有" if os.path.exists(cfg.in_dir(cfg["recorder_file"])) else "** 缺 **")
    print("[前置] 画像库   :",
          "有" if os.path.exists(cfg.in_dir(cfg["persona_file"])) else "** 缺 **（extract 会跳过名册联表）")

    t0 = time.time()
    print("\n[生成]")
    steps = STEPS[1:] if a.skip_extract else STEPS
    if a.skip_extract:
        print("  -- 跳过 viz_extract（沿用已有数据包）")
    for script, label in steps:
        if not run_step(script, label, cfg_path):
            sys.exit("\n✗ 卡在 %s，后续步骤未跑。" % script)

    print("\n[产物]")
    if os.path.exists(cfg.out_html):
        print("  报告 HTML :", cfg.out_html, os.path.getsize(cfg.out_html), "bytes")
    else:
        sys.exit("✗ 没看到报告：%s" % cfg.out_html)
    if os.path.exists(cfg.data_path):
        print("  数据包    :", cfg.data_path, os.path.getsize(cfg.data_path), "bytes")

    if a.check:
        print("\n[体检] _go.py")
        rc = subprocess.run([sys.executable, os.path.join(HERE, "_go.py"),
                             "--prefix", "_regGen_"], cwd=HERE).returncode
        print("  体检退出码:", rc)

    print("\n完成，用时 %.1fs。报告：%s" % (time.time() - t0, cfg.out_html))


if __name__ == "__main__":
    main()
