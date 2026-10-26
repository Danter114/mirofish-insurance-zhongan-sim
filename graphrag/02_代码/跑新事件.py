# -*- coding: utf-8 -*-
"""一键跑新事件 —— 输入一个事件 .json，输出交付件 + 前端可看的产物。

用法：
    python 跑新事件.py --event 我的事件.json
    python 跑新事件.py --event 我的事件.json --name "某某事件" --t0-window 72
    python 跑新事件.py --event 我的事件.json --out runs/我的事件

做的事（全链路）：
    ① 建图        event_kg_<EVT>.json
    ② 定槽位      slots_<EVT>.json
    ③ 选人        <EVT>_roster.json
    ④ 相关度      relevance_<EVT>.json
    ⑤ t0 初始化   <EVT>_t0_init.json
    ⑥ t0 真实人物  <EVT>_t0_real_personas.json
    ⑦ 组队校验    group_check.json
    ⑧ 打包交付    D_Agent名单.json（14 字段）+ 全套交付件

输入格式：与 examples/evt1.json 相同的 JSON 数组（每帖 30 字段）。
    最少必需的字段：post_id, content, channel, publish_time, author_name
    推荐齐全：见 README.md「事件 JSON 格式」
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, shutil, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


WARN_ONLY = {"⑦ 组队校验"}


def sh(args, desc):
    t0 = time.time()
    p = subprocess.run([PY] + args, cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    ok = p.returncode == 0
    soft = desc in WARN_ONLY
    mark = "✅" if ok else ("⚠️ " if soft else "❌")
    print("  %s %-22s %5.1fs" % (mark, desc, time.time() - t0))
    if not ok and soft:
        print("       （多样性告警，不影响交付 —— 名单已正常产出）")
    if not ok and not soft:
        tail = ((p.stderr or "") + (p.stdout or "")).strip().split("\n")[-8:]
        for l in tail:
            print("       " + l[:150])
    return ok


def main():
    ap = argparse.ArgumentParser(description="一键跑新事件：建图 -> 定槽位 -> 选人 -> 打包")
    ap.add_argument("--event", required=True, help="事件 JSON 路径（数组，每帖 30 字段）")
    ap.add_argument("--name", default="", help="事件名称（写进交付件说明）")
    ap.add_argument("--t0-days", type=int, default=2,
                    help="t0 覆盖的自然日数（默认 2 = Day0+Day1；Day2 及以后不算 t0）")
    ap.add_argument("--event-key", default="EVT-001", help="事件编号，决定产物文件名")
    ap.add_argument("--seed", type=int, default=20261002, help="主种子（同种子必得同名册）")
    ap.add_argument("--total", default="auto", help="人数：auto=按热度自动算，或给整数")
    ap.add_argument("--personas", default="out/persona_v18_50k_adapted.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--skip-package", action="store_true", help="只跑到选人，不打包")
    a = ap.parse_args()

    ev = Path(a.event)
    if not ev.exists():
        print("❌ 找不到事件文件：" + str(ev)); sys.exit(1)
    try:
        arr = json.loads(ev.read_text(encoding="utf-8"))
        assert isinstance(arr, list) and arr
    except Exception as e:
        print("❌ 事件文件不是合法的 JSON 数组：%s" % e); sys.exit(1)
    need = ["post_id", "content", "channel", "publish_time", "author_name"]
    miss = [k for k in need if k not in arr[0]]
    if miss:
        print("❌ 缺少必需字段：%s" % "、".join(miss)); print("   见 README.md「事件 JSON 格式」"); sys.exit(1)

    K = a.event_key
    out = Path(a.out) if a.out else ROOT / "runs" / ev.stem
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    nm = a.name or ev.stem
    print("\n" + "=" * 70)
    print("  事件 %s ｜ %s ｜ %d 帖 ｜ t0 = Day0+Day%d" % (K, nm, len(arr), a.t0_days - 1))
    print("=" * 70)

    ok = sh(["run.py", "--event", str(ev), "--personas", a.personas, "--seed", str(a.seed),
             "--total", str(a.total), "--t0-mode", "days", "--t0-days", str(a.t0_days), "--out", str(out)], "① 建图/定槽位/选人")
    if not ok: sys.exit(1)
    R = out
    ok &= sh(["tools/compute_relevance.py", "--roster", "%s/downstream/%s_roster.json" % (R, K),
              "--kg", "%s/kg/event_kg_%s.json" % (R, K), "--t0", "%s/downstream/%s_t0_snapshot.json" % (R, K),
              "--event", str(ev), "--personas", a.personas, "--out", "%s/relevance" % R], "④ 相关度")
    ok &= sh(["tools/t0_init.py", "--roster", "%s/downstream/%s_roster.json" % (R, K),
              "--kg", "%s/kg/event_kg_%s.json" % (R, K), "--event-pack", "%s/packs/event_pack.json" % R,
              "--personas", a.personas, "--out", "%s/downstream/%s_t0_init.json" % (R, K)], "⑤ t0 初始化")
    ok &= sh(["tools/t0_real_personas.py", "--kg", "%s/kg/event_kg_%s.json" % (R, K),
              "--t0", "%s/downstream/%s_t0_snapshot.json" % (R, K), "--pack", "%s/packs/event_pack.json" % R,
              "--out", "%s/downstream/%s_t0_real_personas.json" % (R, K)], "⑥ t0 真实人物")
    if os.path.exists(str(R / "downstream" / (K + "_t0_init.json"))):
        sh(["tools/group_check.py", "--roster", "%s/downstream/%s_roster.json" % (R, K),
            "--personas", a.personas, "--t0-init", "%s/downstream/%s_t0_init.json" % (R, K),
            "--out", "%s/report/group_check.json" % R], "⑦ 组队校验")   # 告警级，不阻断
    if not a.skip_package:
        sh(["tools/package_delivery.py", "--run-dir", str(R), "--personas", a.personas,
            "--out", str(R / "交付包"), "--event-key", K, "--event-name", nm], "⑧ 打包交付")
    # 摘要
    rf = R / "downstream" / (K + "_roster.json")
    if rf.exists():
        mem = json.loads(rf.read_text(encoding="utf-8"))["members"]
        by = {}
        for x in mem: by[x["role_type"]] = by.get(x["role_type"], 0) + 1
        print("\n" + "-" * 70)
        print("  ✅ 完成 ｜ %d 人 ｜ %s" % (len(mem), " ".join("%s%d" % (k, v) for k, v in by.items())))
        print("  📁 产物目录： %s" % out)
        print("     downstream/%s_roster.json          选人名册" % K)
        print("     downstream/%s_t0_snapshot.json     ★ t0 事件脉络（给下游）" % K)
        print("     交付包/D_Agent名单.json             ★ 14 字段（给下游）" % ())
        print("     kg/event_kg_%s.json                知识图谱" % K)
        print("\n  💡 把 %s 加进前端：见 README.md「把新事件加进前端」" % (out / "交付包"))
    else:
        print("\n  ⚠️ 未生成名册，请检查上面的报错")


if __name__ == "__main__":
    main()
