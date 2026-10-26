#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py —— 上游全代码测试台

★ 为什么要它：2026-10-06 之前，make_personas_v3.py 的自检长期报
  「冲突数 ~3.7/人（必须为 0）」并 exit=1，但因为【没人看退出码】，
  50k 画像库一直带着这个错误出厂。这个测试台把「看退出码」固化成流程。

测什么（两种配置布局各一遍）：
  A 模块导入     src/ 全部模块能否 import
  B --help       全部可执行脚本的 argparse 是否正常
  C 全管线       建图+定槽位+选人 -> 相关度 -> t0 初始化 -> 真实人物 -> 组队校验 -> 打包
  D 辅助工具     生成库 / 适配 / 报告 / 词表 / mock / 回改 / post 规则 / 采集适配

两种配置布局（见《GraphRAG上游全流程规范 v1.0》附录 E）：
  dev     config/<name>.json 逐文件（27 个）
  merged  config/_all.json 单文件合并（交付包布局）

用法：
    python tools/run_tests.py                  # 两种布局都测
    python tools/run_tests.py --layout merged  # 只测交付布局
    python tools/run_tests.py --quick          # 只跑 A/B
    python tools/run_tests.py --json r.json    # 同时输出机器可读结果

退出码：0 = 全通过；1 = 有失败项
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CONFIG = os.path.join(ROOT, "config")
CONFIG_DEV = os.path.join(ROOT, "config_dev")
TMP = os.path.join(ROOT, "out_test")
# ★ 自动解析最新的交付包（避免写死版本号 —— 每次重打包都要改测试台）
def _latest_pkg():
    import glob as _g
    # ★ 按【修改时间】排序，不能用字典序 —— "…_500人" 字典序排在 "…_v14" 之后，
    #   会把旧的 500 人包误当成最新（实测：merged 布局报 gender_topic 缺失）。
    _c = _g.glob(os.path.join(ROOT, "交付包", "EVT-001_50k库_100人*"))
    _c = sorted(_c, key=lambda p: os.path.getmtime(p))
    if not _c:
        raise SystemExit("交付包目录下找不到 EVT-001_50k库_*")
    return _c[-1]


_PKG = _latest_pkg()
PERSONAS = "out/persona_v3std_10k_adapted.json"
MERGED_SRC = os.path.join(_PKG, "配置_全集.json")
ROSTER_PKG = os.path.join(_PKG, "D_Agent名单.json")


def _ensure_personas():
    """★ 测试库不存在就自动生成 —— 2026-10-07 踩过：清缓存时把 out/ 一起删了，
    导致 C 段（全管线）12 项全部失败，但报错信息只显示「X C/1」看不出原因。"""
    if os.path.exists(os.path.join(ROOT, PERSONAS)):
        return
    print("  [setup] 测试画像库不存在，正在生成 %s ..." % PERSONAS)
    _p = os.path.join(ROOT, "out", "persona_v3std_10k.json")
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    for _a in (["tools/make_personas_v3.py", "--n", "10000", "--out", _p],
               ["tools/adapt_persona_library.py", "--src", _p,
                "--out", os.path.join(ROOT, PERSONAS)]):
        rc, out = run(_a, timeout=600)
        if rc != 0:
            raise SystemExit("生成测试画像库失败：%s\n%s" % (" ".join(_a), out[-800:]))
    print("  [setup] 完成")


def run(args, timeout=900):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 把 src/ 放进 PYTHONPATH —— 这样测试脚本不用在 -c 里拼 sys.path（省掉引号转义）
    env["PYTHONPATH"] = os.path.join(ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "TIMEOUT"


def _switch(name):
    """切到指定配置布局。dev = 原样不动。"""
    if name == "dev":
        return
    if not os.path.exists(MERGED_SRC):
        raise SystemExit("找不到合并配置：%s" % MERGED_SRC)
    if os.path.exists(CONFIG_DEV):
        shutil.rmtree(CONFIG_DEV)
    os.rename(CONFIG, CONFIG_DEV)
    os.makedirs(CONFIG)
    shutil.copy2(MERGED_SRC, os.path.join(CONFIG, "_all.json"))


def _restore():
    """无论成败都要还原开发布局。"""
    if os.path.exists(CONFIG_DEV):
        if os.path.exists(CONFIG):
            shutil.rmtree(CONFIG)
        os.rename(CONFIG_DEV, CONFIG)


def section(t):
    print("")
    print("-" * 66)
    print("  " + t)
    print("-" * 66)


def test_layout(name, quick, results):
    print("")
    print("=" * 66)
    print("  配置布局: " + name)
    print("=" * 66)
    _switch(name)
    fails = []

    def rec(sec, item, ok, msg=""):
        results.append({"layout": name, "section": sec, "item": item,
                        "ok": bool(ok), "msg": msg[:200]})
    try:
        section("[A] 模块导入")
        for f in sorted(x for x in os.listdir(os.path.join(ROOT, "src")) if x.endswith(".py")):
            rc, out = run(["-c", "import %s" % f[:-3]], 120)
            ok = rc == 0
            print("    %-22s %s" % (f[:-3], "OK" if ok else "FAIL"))
            rec("A", f[:-3], ok, out)
            if not ok:
                fails.append("A/import " + f[:-3])

        section("[B] --help")
        exes = ["run.py"] + ["tools/" + x for x in sorted(os.listdir(os.path.join(ROOT, "tools")))
                             if x.endswith(".py")]
        for f in exes:
            rc, out = run([f, "--help"], 120)
            ok = rc == 0 and "usage" in out.lower()
            print("    %-36s %s" % (f, "OK" if ok else "FAIL"))
            rec("B", f, ok, out)
            if not ok:
                fails.append("B/help " + f)

        if quick:
            print("    (--quick：跳过 C/D)")
            return fails

        section("[C] 全管线")
        d = os.path.join(TMP, "r_" + name)
        if os.path.exists(d):
            shutil.rmtree(d)
        steps = [
            ("1 建图+定槽位+选人", ["run.py", "--event", "examples/event1.json", "--personas", PERSONAS,
                 "--seed", "20261002", "--total", "100", "--out", d],
             d + "/downstream/EVT-001_roster.json"),
            ("2 相关度", ["tools/compute_relevance.py", "--roster", d + "/downstream/EVT-001_roster.json",
                 "--kg", d + "/kg/event_kg_EVT-001.json", "--t0", d + "/downstream/EVT-001_t0_snapshot.json",
                 "--event", "examples/event1.json", "--personas", PERSONAS, "--out", d + "/relevance"],
             d + "/relevance/relevance_EVT-001.json"),
            ("3 t0 初始化", ["tools/t0_init.py", "--roster", d + "/downstream/EVT-001_roster.json",
                 "--kg", d + "/kg/event_kg_EVT-001.json", "--event-pack", d + "/packs/event_pack.json",
                 "--personas", PERSONAS, "--out", d + "/downstream/EVT-001_t0_init.json"],
             d + "/downstream/EVT-001_t0_init.json"),
            ("4 t0 真实人物", ["tools/t0_real_personas.py", "--kg", d + "/kg/event_kg_EVT-001.json",
                 "--t0", d + "/downstream/EVT-001_t0_snapshot.json", "--pack", d + "/packs/event_pack.json",
                 "--out", d + "/downstream/EVT-001_t0_real_personas.json"],
             d + "/downstream/EVT-001_t0_real_personas.json"),
            ("5 组队校验", ["tools/group_check.py", "--roster", d + "/downstream/EVT-001_roster.json",
                 "--personas", PERSONAS, "--t0-init", d + "/downstream/EVT-001_t0_init.json",
                 "--out", d + "/report/group_check.json"], d + "/report/group_check.json"),
            ("6 打包", ["tools/package_delivery.py", "--run-dir", d, "--personas", PERSONAS,
                 "--out", d + "_pkg", "--event-name", "测试事件"], d + "_pkg/D_Agent名单.json"),
        ]
        for label, args, artifact in steps:
            rc, out = run(args)
            ok = os.path.exists(artifact)
            print("    %-22s %s" % (label, "OK" if ok else ("FAIL rc=%d" % rc)))
            if not ok:
                print("        " + out.strip().split(chr(10))[-1][:110])
            rec("C", label, ok, out)
            if not ok:
                fails.append("C/" + label)

        section("[D] 辅助工具")
        aux = [
            ("make_personas 200", ["tools/make_personas_v3.py", "--n", "200", "--out", TMP + "/p.json"]),
            ("adapt_persona_library", ["tools/adapt_persona_library.py", "--src", TMP + "/p.json", "--out", TMP + "/pa.json"]),
            ("concern_topics_report", ["tools/concern_topics_report.py", "--report", TMP + "/ctr.md"]),
            ("taxonomy_gap", ["tools/taxonomy_gap.py", "--kg-dir", os.path.join(ROOT, "out_10k100", "kg"), "--report", TMP + "/tg.md"]),
            ("rebuild_topic_list", ["tools/rebuild_topic_list.py", "--src", "examples/event1.json", "--out", TMP + "/tl.json", "--no-backup"]),
            ("mock_persona_library", ["tools/mock_persona_library.py", "--n", "50", "--out", TMP + "/m.json"]),
            ("mock_persona_library_v3spec", ["tools/mock_persona_library_v3spec.py", "--n", "50", "--out", TMP + "/m3.json"]),
            ("fix_active_hours (dry)", ["tools/fix_active_hours.py", "--file", TMP + "/p.json"]),
            ("fix_bio_consistency (dry)", ["tools/fix_bio_consistency.py", "--roster", ROSTER_PKG]),
            ("post_rules", ["tools/post_rules.py", "--agents", ROSTER_PKG, "--rounds", "5"]),
            ("adapt_collect", ["tools/adapt_collect.py", "--src", "examples/event1.json", "--out", TMP + "/pack.json"]),
        ]
        for label, args in aux:
            rc, out = run(args, 600)
            tb = "Traceback" in out
            ok = (rc == 0 and not tb)
            print("    %-30s %s" % (label, "OK" if ok else ("FAIL rc=%d" % rc)))
            if not ok:
                print("        " + out.strip().split(chr(10))[-1][:110])
            rec("D", label, ok, out)
            if not ok:
                fails.append("D/" + label)
    finally:
        _restore()
    return fails


def main():
    _ensure_personas()
    ap = argparse.ArgumentParser(description="上游全代码测试台（两种配置布局）")
    ap.add_argument("--layout", choices=["dev", "merged", "both"], default="both")
    ap.add_argument("--quick", action="store_true", help="只跑 A/B")
    ap.add_argument("--json", default=None, help="把逐项结果写到这个 JSON")
    a = ap.parse_args()
    os.makedirs(TMP, exist_ok=True)
    layouts = ["dev", "merged"] if a.layout == "both" else [a.layout]
    t0 = time.time()
    results, allf = [], {}
    for n in layouts:
        allf[n] = test_layout(n, a.quick, results)
    print("")
    print("=" * 66)
    print("  汇总")
    print("=" * 66)
    total = len(results)
    bad = sum(1 for r in results if not r["ok"])
    for n, f in allf.items():
        cnt = sum(1 for r in results if r["layout"] == n)
        print("  %-8s 检查 %3d 项  失败 %d 项" % (n, cnt, len(f)))
        for x in f:
            print("      X " + x)
    print("  " + "-" * 60)
    print("  ★ 共 %d 项，失败 %d 项，耗时 %.0f 秒" % (total, bad, time.time() - t0))
    if bad == 0:
        print("  ★ 全部通过")
    if a.json:
        json.dump({"root": ROOT, "total": total, "failed": bad, "layouts": allf, "results": results},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  逐项结果 -> %s" % a.json)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())