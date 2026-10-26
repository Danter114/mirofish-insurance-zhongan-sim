#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py —— 配置加载与公共工具。所有魔法数字都在 config/ 里，代码里不写死。"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"


_ALL_CACHE = None


def load_config(name: str) -> dict:
    """name 不带 .json，例如 taxonomy / rules / slots / ontology

    ★ 支持两种配置布局，自动适配：
      ① 开发布局：config/<name>.json 逐文件（优先）
      ② 交付布局：config/_all.json 单文件合并（①不存在时回退）

    交付包只带一个 配置_全集.json 时，把它放到 config/_all.json 即可直接跑，
    不需要把 27 个文件摊开。
    """
    p = CONFIG_DIR / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    global _ALL_CACHE
    if _ALL_CACHE is None:
        allp = CONFIG_DIR / "_all.json"
        if not allp.exists():
            raise FileNotFoundError(
                "找不到配置 %s：既没有 config/%s.json，也没有 config/_all.json" % (name, name))
        _ALL_CACHE = json.loads(allp.read_text(encoding="utf-8"))
    if name not in _ALL_CACHE:
        raise KeyError("配置 %s 不在 config/_all.json 里（可用键：%s）"
                       % (name, ", ".join(sorted(_ALL_CACHE)[:8]) + " ..."))
    return _ALL_CACHE[name]


def write_json(path, obj) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def largest_remainder(weights: dict, total: int) -> dict:
    """最大余数法。定槽位每一步收敛都必须用它 —— 四舍五入会导致求和 != 配额。"""
    out = {k: 0 for k in weights}
    if total <= 0 or not weights:
        return out
    clean = {k: max(0.0, float(v)) for k, v in weights.items()}
    s = sum(clean.values())
    if s <= 0:
        clean = {k: 1.0 for k in weights}
        s = float(len(clean))
    raw = {k: v / s * total for k, v in clean.items()}
    for k, x in raw.items():
        out[k] = int(math.floor(x))
    rem = total - sum(out.values())
    frac = sorted(raw.items(), key=lambda kv: (-(kv[1] - math.floor(kv[1])), kv[0]))
    i = 0
    while rem > 0 and frac:
        out[frac[i % len(frac)][0]] += 1
        rem -= 1
        i += 1
    return out


def norm_minmax(vals: dict) -> dict:
    if not vals:
        return {}
    lo, hi = min(vals.values()), max(vals.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in vals}
    return {k: (v - lo) / (hi - lo) for k, v in vals.items()}


def parse_time(s):
    """容错解析 ISO8601 / 'YYYY-MM-DD HH:MM'；解析不出来返回 None。"""
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(t)
    except Exception:
        return None


def leaf_index(taxonomy: dict) -> dict:
    """叶子 -> 大类"""
    idx = {}
    for top, v in taxonomy["categories"].items():
        for lf in v["leaves"]:
            idx[lf["leaf"]] = top
    return idx
