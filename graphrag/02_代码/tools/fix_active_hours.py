#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_active_hours.py —— 回改 active_hours 为真实作息（v1.0）

修的 3 个 bug：
  1. active_hours_list 在适配层被丢（交付包里全为空）
  2. 在线时段 10~16 小时（人不可能）—— 根因 rng.sample(range(6,24), 3~7) 纯随机 + 邻接填充每人 +2 小时
  3. 职业间毫无作息差异 —— 根本没查职业

口径：deterministic（按 agent_id 派生随机流），只改 active_hours* 字段，其余一律断言 0 改动。
"""
from __future__ import annotations
import argparse, hashlib, json, shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ALLOW = {"active_hours", "active_hours_online", "active_hours_edge",
         "active_hours_definition", "active_hours_list"}
# ★ 改完后只保留 active_hours 一个字段（旧的多字段形态全部删除）


class R:
    """确定性随机流：SHA-256 计数器模式，与画像库生成器同一套"""
    def __init__(self, seed, stream):
        self.b = ("%s|%s" % (seed, stream)).encode("utf-8")
        self.n = 0

    def _next(self):
        h = hashlib.sha256(self.b + b"|" + str(self.n).encode()).digest()
        self.n += 1
        return int.from_bytes(h[:8], "big") / float(1 << 64)

    def randint(self, a, b):
        return a + int(self._next() * (b - a + 1)) if b >= a else a

    def sample(self, seq, k):
        s = list(seq)
        for i in range(len(s) - 1, 0, -1):
            j = int(self._next() * (i + 1))
            s[i], s[j] = s[j], s[i]
        return s[:k]

    def uniform(self, a, b):
        return a + self._next() * (b - a)


def build_ah(x, prof, org_hours, seed):
    ch = x.get("channel") or x.get("primary_channel")
    role = x.get("role_type")
    occ = x.get("occupation")
    age = int(x.get("age") or 30)
    rng = R(seed, "ah:" + str(x.get("agent_id")))
    shift = int((prof.get("by_channel_shift") or {}).get(ch, 0))
    if age <= 24:
        shift += int((prof.get("_age_shift") or {}).get("le24", 0))
    elif age >= 60:
        shift += int((prof.get("_age_shift") or {}).get("ge60", 0))
    if role in ("媒体", "监管"):
        # org_persona 的 hours 是【列表的列表】——每个机构类型有多个候选值班表
        _oh = org_hours or [[9, 10, 11, 14, 15, 16, 17]]
        if _oh and isinstance(_oh[0], (list, tuple)):
            _oh = list(rng.sample([list(z) for z in _oh], 1)[0])
        pool = list(_oh)
    else:
        pool = list((prof.get("by_occ") or {}).get(occ) or prof.get("_default_occ") or [12, 13, 20, 21])
        pool = rng.sample(pool, rng.randint(2, len(pool)))
    # ★ active_hours 就是【在线小时的列表】—— 不再生成强度向量
    hours = sorted({(h + shift) % 24 for h in pool})
    return {"active_hours": hours}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--persona-data", default=None,
                    help="可选：单独的 persona_data.json 路径；不传则走 config/ 加载器")
    ap.add_argument("--org-persona", default=None)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    p = Path(a.file)
    doc = json.loads(p.read_text(encoding="utf-8"))
    from common import load_config  # noqa: E402
    if a.persona_data:
        prof = json.loads(Path(a.persona_data).read_text(encoding="utf-8")).get("active_hours_profile") or {}
    else:
        prof = (load_config("persona_data") or {}).get("active_hours_profile") or {}
    org = {}
    if a.org_persona and Path(a.org_persona).exists():
        org = json.loads(Path(a.org_persona).read_text(encoding="utf-8"))
    elif not a.org_persona:
        try:
            org = load_config("org_persona")
        except Exception:
            org = {}
    agents = doc["agents"] if isinstance(doc, dict) and "agents" in doc else doc
    before = json.loads(json.dumps(agents))
    nz0 = sum(len([v for v in (x.get("active_hours") or []) if isinstance(v, (int, float)) and v > 0])
              for x in agents)
    for x in agents:
        oh = ((org.get(x.get("role_type")) or {}) or {}).get("hours")
        new = build_ah(x, prof, oh, a.seed)
        # ★ 删掉旧的多字段形态，只留 active_hours
        for k in ("active_hours_online", "active_hours_edge", "active_hours_definition", "active_hours_list"):
            x.pop(k, None)
            ALLOW.add(k)
        x.update(new)
    nz1 = sum(len(x.get("active_hours") or []) for x in agents)
    on1 = [len(x["active_hours"]) for x in agents]
    bad = []
    for i in range(len(agents)):
        for k in set(before[i]) | set(agents[i]):
            if k in ALLOW:
                continue
            if json.dumps(before[i].get(k), sort_keys=True, ensure_ascii=False) != \
               json.dumps(agents[i].get(k), sort_keys=True, ensure_ascii=False):
                bad.append("%s.%s" % (agents[i].get("agent_id"), k))
    print("改 %d 人 ｜ 在线小时总数 %d -> %d ｜ 每人 %d~%d(均值%.1f)"
          % (len(agents), nz0, nz1, min(on1), max(on1), sum(on1) / float(len(on1))))
    print("允许清单外改动 %d 处 %s" % (len(bad), "OK" if not bad else "FAIL"))
    for b in bad[:20]:
        print("   X " + b)
    if bad:
        print("断言失败，不写盘。")
        return 1
    if not a.apply:
        print("（预览，未写盘）")
        return 0
    bak = p.with_name(p.stem + ".ahbak" + p.suffix)
    if not bak.exists():
        shutil.copy2(p, bak); print("备份 -> %s" % bak.name)
    p.write_text("\n" + json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写回 %s" % p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())