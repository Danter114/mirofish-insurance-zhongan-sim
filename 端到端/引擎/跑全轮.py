# -*- coding: utf-8 -*-
"""R-50 等长 4h × 42 轮：自愈式重跑循环。

设计要点
--------
1. **自愈**：每一圈先扫「已存在的最大轮」，再跑**它的下一轮**。
   跑挂了直接重启本脚本即可，不会重复跑、也不会漏。
2. **不用管道**：直接 subprocess 调 `t1_runner.py`，输出原样写日志文件。
   （旧账：`t1_runner.py | Select-Object -First N` 会掐断管道杀掉 python 进程。）
3. **健康判据**：状态文件存在、>1KB、且末字符是 `}`（防半截写入被当成完成）。
4. 轮 0 不跑（它是 `t0状态_第0轮.json` 输入，没有独立执行、不调 LLM）。
5. **★ 2026-10-14 修退出码**：原先任何分支都 `break` 到底后**无条件返回 0**
   ⇒ 轮次跑挂时进程仍退 0，调用方（后端「开始推演」、CI、脚本）会把失败读成「完成」，
   界面显示绿灯而快照其实是断的。改为**失败即 `sys.exit(1)`**（退出码 0 = 42 轮全齐）。
   **只改退出码，不改任何产物** —— 快照 / 日志 / 记录员逐位不变。
"""
import argparse, io, json, os, subprocess, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sim_config

# ★ 引擎参数化：目录 / 轮数 / 轮长 全从 `事件配置.json` 来。
#   用法：python _run_r50_42.py [--config PATH]
_ap = argparse.ArgumentParser()
_sim_config.add_config_arg(_ap)
_a = _ap.parse_args()
CFG = _sim_config.load(path=_a.config, quiet=True)

BASE = CFG.run_dir
NR = CFG.n_rounds
LOG = CFG.in_dir('_rerun.log')
PY = sys.executable
# ★ 2026-10-13 修死结（第二处）：原先这里是**相对名** `'t1_runner.py'` + `cwd=BASE`，
#   隐含假设"引擎脚本与 run_dir 同层"—— 工作目录里确实同层，**交付目录里不是**：
#   交付是 `引擎\t1_runner.py` + `事件\EVT-001\`（= run_dir），于是解释器去
#   `事件\EVT-001\t1_runner.py` 找，起步就退 2，后端「开始推演」按下去必失败。
#   改成从**本文件所在目录**取绝对路径。旧布局下两者本来就是同一层，
#   所以这一改对旧布局**逐位等价**；`cwd` 仍留给 run_dir，没动。
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
T1 = os.path.join(ENGINE_DIR, 't1_runner.py')
CONFIG_ARG = (['--config', CFG['_path']] if CFG.get('_path') else [])


def state_path(r):
    return CFG.snap_path(r)


def healthy(r):
    """文件存在、够大、末字符是 } ⇒ 认为这一轮跑完了。"""
    p = state_path(r)
    if not os.path.exists(p) or os.path.getsize(p) < 1024:
        return False
    with open(p, 'rb') as f:
        f.seek(-8, os.SEEK_END)
        tail = f.read()
    return b'}' in tail


def max_done():
    r = 0
    while r + 1 < NR and healthy(r + 1):
        r += 1
    return r


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def main():
    failed = False
    t_all = time.time()
    log('=' * 88)
    log('R-50 等长 4h × %d 轮 重跑开始 ｜ python = %s' % (NR, PY))
    log('=' * 88)

    while True:
        r0 = max_done()
        if r0 >= NR - 1:
            log('★ 全部 %d 轮已完成（轮 0 … 轮 %d）' % (NR, NR - 1))
            break
        r = r0 + 1
        log('-' * 88)
        log('▶ 第 %d 轮 开始（已完成最大轮 = %d）' % (r, r0))
        t0 = time.time()
        with open(LOG, 'a', encoding='utf-8') as lf:
            lf.write('---- t1_runner --round %d ----\n' % r)
            lf.flush()
            p = subprocess.run([PY, T1, '--round', str(r), '--force'] + CONFIG_ARG,
                               cwd=BASE, stdout=lf, stderr=subprocess.STDOUT)
        dt = time.time() - t0
        if p.returncode != 0:
            log('❌ 第 %d 轮 退出码 = %d（%.1fs）—— 停止循环，修好后重跑本脚本' % (r, p.returncode, dt))
            failed = True
            break
        if not healthy(r):
            log('❌ 第 %d 轮 退出码 0 但快照不健康 —— 停止循环' % r)
            failed = True
            break
        sz = os.path.getsize(state_path(r))
        log('✔ 第 %d 轮 完成  用时 %.1fs  快照 %d B' % (r, dt, sz))

    log('=' * 88)
    log('总用时 %.1f min ｜ 已完成轮数 = %d / %d' % ((time.time() - t_all) / 60.0,
                                                 max_done(), NR - 1))
    log('=' * 88)

    # ★ 2026-10-14：失败必须让调用方看得见（原先无条件退 0 ⇒ 界面显示「完成」）。
    #   退出码语义：0 = 42 轮全齐且快照健康；1 = 中途失败停住（快照是断的，需重跑本脚本续）。
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
