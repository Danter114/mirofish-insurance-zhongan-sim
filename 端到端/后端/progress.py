# -*- coding: utf-8 -*-
"""进度盘点 —— 不问引擎、直接看盘。

引擎没有"汇报进度"的接口，也不需要：它是**自愈循环**，每跑完一轮就在 `run_dir`
落一份快照。所以**进度 = 盘上健康快照的最大轮号**，重启、换进程、换个机器看都是同一个数。

健康判据（与 `跑全轮.py` 里的 `healthy()` 同一条规则，刻意保持一致）：
    文件存在 + > 1KB + 末 8 字节里有 `}`  ⇒ 这一轮写完整了。
防的是**半截写入被当成完成** —— 那种快照一读就 JSON 解析失败。
"""
import json
import os


def healthy_snapshot(path):
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        return False
    with open(path, "rb") as f:
        f.seek(-8, os.SEEK_END)
        return b"}" in f.read()


def rounds_done(cfg):
    """已完成的最大轮号（0 = 只有 t0，一轮没跑）。逐轮往后探，遇断即停。"""
    r = 0
    while r + 1 < cfg.n_rounds and healthy_snapshot(cfg.snap_path(r + 1)):
        r += 1
    return r


def recorder_lines(cfg):
    p = cfg.recorder_path()
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip())


def t0_ready(cfg):
    return os.path.exists(cfg.t0_path)


def inventory(cfg):
    """一份给前端 / 预检共用的产物盘点。数字全部现算，不缓存。"""
    done = rounds_done(cfg)
    n = cfg.n_rounds
    rec = recorder_lines(cfg)
    out_html = cfg.get("out_html") or ""
    if out_html and not os.path.isabs(out_html):
        out_html = os.path.join(os.path.dirname(cfg["_path"]), out_html)
    size = os.path.getsize(out_html) if out_html and os.path.exists(out_html) else 0
    return {
        "t0_ready": t0_ready(cfg),
        "rounds_done": done,                       # 已完成的最大轮号（0 = 只跑了 t0）
        "n_rounds": n,
        "rounds_total": n,                         # 轮 0 … n-1，共 n 轮
        "rounds_executed": n - 1,                  # 轮 1 起才调 LLM
        "percent": round(100.0 * done / (n - 1), 1) if n > 1 else 0.0,
        "complete": done >= n - 1,
        "recorder_lines": rec,
        "recorder_expected": n * 7,                # 每轮 7 行；轮 0 由 t0 轮末状态出，也出 7 行
        "report_path": out_html,
        "report_exists": bool(size),
        "report_bytes": size,
    }


def load_json_tail(path, n=60):
    """读一段 JSONL 的尾部（给前端看最近几轮）。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out
