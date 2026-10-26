#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manifest.py —— 组队用的随机种子号与产物记录（组长 3.5）

组长 3.5 的三条硬要求，这个文件负责第 3 条「种子要写进产物（可溯源）」：
  · 不用全局 random.seed()，每个随机流用独立 random.Random(seed + offset)
  · 产物里要能说明「本次结果由种子 X 产生」
  · 报告与复现命令要能从产物直接读出来

记录内容：
  种子        主种子 + 每条随机流的派生种子
  输入        事件集 / 画像库 / 全部 config 的路径 + sha256（跑完就知道有没有被换过）
  产物        每个交付文件的路径 + sha256 + 字节数 + 行数
  版本        taxonomy / rules / slots / selection / contract 的版本号
  计数与自检   500 守恒、配额误差、缺口、泄漏闸、纯度偏差
  复现命令    一条能原样重跑出同一份名单的命令
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

MANIFEST_VERSION = "1.0"


def sha256_file(path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_record(path, rows=None) -> dict:
    p = Path(path)
    rec = {"path": p.as_posix(), "name": p.name}
    if p.exists():
        rec["bytes"] = p.stat().st_size
        rec["sha256"] = sha256_file(p)
    if rows is not None:
        rec["rows"] = rows
    return rec


def derive_seeds(main_seed: int, spec: dict) -> dict:
    streams = (spec or {}).get("streams") or {}
    return {name: int(main_seed) + int(streams.get(name, i + 1))
            for i, name in enumerate(("team", "jitter", "tiebreak"))}


def build(*, run_id, event_id, main_seed, seed_spec, inputs, outputs, versions,
          counts, checks, command, extra=None) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "event_id": event_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": {"python": sys.version.split()[0], "platform": platform.platform()},
        # ---- ① 种子（组长 3.5：种子要写进产物）
        "seed": {
            "main": int(main_seed),
            "streams": derive_seeds(main_seed, seed_spec),
            "jitter": (seed_spec or {}).get("jitter"),
            "rule": "每个随机流用独立 random.Random(seed + offset)；不使用全局 random.seed()",
            "stream_owner": {"team": "画像侧（组队抽样）", "jitter": "画像侧（候选分数抖动）",
                             "tiebreak": "画像侧（并列打破）",
                             "sim_round_draw": "仿真侧（R1 每轮点名）",
                             "sim_noise": "仿真侧（情绪噪声）"},
            "note": "同一 (seed, 事件集, 画像库, 配置) 必得同一份 500 人名单；改 seed 会换一批人。",
        },
        # ---- ② 输入指纹
        "inputs": inputs,
        # ---- ③ 产物清单（每个文件带 sha256）
        "outputs": outputs,
        # ---- ④ 版本
        "versions": versions,
        # ---- ⑤ 计数与自检
        "counts": counts,
        "checks": checks,
        # ---- ⑥ 复现
        "reproduce": {"command": command,
                      "expected": "重跑后 roster_<event>.json 的 sha256 与本记录一致"},
        "extra": extra or {},
    }


def write(path, obj) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
