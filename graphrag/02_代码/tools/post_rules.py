#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_rules.py —— post 触发规则 v3.0 参考实现（5 角色）

推演层可直接移植 can_post()。核心：
    post 不受 action_threshold 约束，由任务驱动；
    个人角色走各自 trigger；媒体走六帖型加权随机；监管走程序阶段。

用法：
    python tools/post_rules.py --agents 给推演层_50人_v5/05_agents_50.json --rounds 30
"""
from __future__ import annotations
import argparse, json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src")) if str(Path(__file__).resolve().parent.parent / "src") not in sys.path else None
from common import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ORG_ROLES = ("媒体", "监管")


def load_cfg():
    return load_config("post_rules")


def bridge(leaf):
    """KG 议题名 -> 47 话题名"""
    try:
        gb = (load_config("topic_alias") or {}).get("general_bridge") or {}
    except Exception:
        return None
    for concern, arr in gb.items():
        if leaf in (arr or []):
            return concern
    return None


def domains_of(role, org_type):
    try:
        o = load_config("org_persona")
    except Exception:
        return []
    return ((o.get(role) or {}).get("domains") or {}).get(org_type) or []


def _mcfg(cfg, org_type):
    return (cfg["media"]["by_org_type"].get(org_type) or {})


def can_post(agent, world, r, cfg):
    """返回 (能不能发, 帖型/驱动, 原因)"""
    role = agent.get("role_type")
    ch = agent.get("primary_channel")
    aid = agent["agent_id"]

    # 0. 基础闸门
    if "post" not in ((agent.get("action_sets") or {}).get(ch) or []):
        return False, None, "该渠道动作集无 post"
    if world.hour(r) not in (agent.get("active_hours") or []):
        return False, None, "不在值班时段"
    if world.global_post_budget() <= 0:
        return False, None, "本轮全局名额已满"

    hot = world.hottest_topic()
    h = hot[1] if hot else 0.0

    # ---- 机构账号：领域闸门 + 各自驱动
    if role in ORG_ROLES:
        ot = agent.get("org_type")
        mc = _mcfg(cfg, ot)
        if not mc:
            return False, None, "机构类型不在配置：%s" % ot
        dom = domains_of(role, ot)
        in_dom = [t for t in world.active_topics() if t in dom]
        cross = any(h >= t["heat_above"] and ot in t["allow"]
                    for t in cfg["media"]["cross_domain_tiers"])
        if not in_dom and not cross:
            return False, None, "领域不命中，热度 %.2f 未达跨领域线" % h
        cd = mc["cooldown"] if role == "媒体" else cfg["regulator"]["cooldown_per_stage"]
        last = world.last_post_round(aid)
        if last is not None and (r - last) < cd:
            return False, None, "冷却中"
        cap = mc["max_per_event"] if role == "媒体" else cfg["regulator"]["max_per_event"]
        if world.post_count(aid) >= cap:
            return False, None, "已达每场上限"

        if role == "监管":
            stage = world.regulator_stage(agent)
            if stage not in cfg["regulator"]["stages_producing_post"]:
                return False, None, "程序阶段「%s」不发声" % stage
            return True, "监管·" + stage, "程序阶段：%s" % stage

        # 媒体：驱动 A 六帖型
        bt = in_dom[0] if in_dom else None
        ok = []
        for st in cfg["media"]["stage_types"]:
            n = st["name"]
            if n in ("快讯", "首报") and in_dom:
                if not world.issue_has_media_coverage(bt):
                    if n == "首报" and not world.has_source(agent):
                        continue
                    ok.append((st, "领域内 + %s" % ("有信源" if n == "首报" else "尚无媒体报道")))
            if n == "跟进":
                t = world.issue_with_coverage(in_dom) if in_dom else None
                if t and not world.agent_covered(aid, bt):
                    ok.append((st, "「%s」已有报道，本机构未报过" % t))
            if n == "短评" and h > 0.5 and (in_dom or cross):
                ok.append((st, "热度 %.2f > 0.5" % h))
            if n == "评论" and world.topic_in_top3(hot[0] if hot else None):
                ok.append((st, "「%s」热度进前三" % (hot[0] if hot else "")))
            if n == "深度" and world.issue_age(r) >= 3 and world.has_new_dispute():
                ok.append((st, "持续 >= 3 轮且出现新争议"))
        if ok:
            p = random.choices(ok, weights=[x[0]["weight"] for x in ok], k=1)[0]
            return True, "媒体·" + p[0]["name"], p[1]
        # 驱动 B 节奏
        if h >= mc["heat_gate"]:
            return True, "媒体·快讯", "节奏驱动（热度 %.2f >= 门槛 %.2f）" % (h, mc["heat_gate"])
        return False, None, "无触发条件成立"

    # ---- 个人角色
    ic = cfg["individual"].get(role)
    if not ic:
        return False, None, "角色不在配置：%s" % role
    last = world.last_post_round(aid)
    if last is not None and (r - last) < ic["cooldown"]:
        return False, None, "冷却中"
    if world.post_count(aid) >= ic["max_per_event"]:
        return False, None, "已达每场上限"
    if role == "网民":
        # ★ first_action_round 含 t0 注入（他在 t0 发了帖 = 已经参与进来）
        fa = world.first_action_round(aid)
        if fa is not None and 0 < (r - fa) <= ic["params"]["M"]:
            return True, "网民·跟帖", "首次动作后 %d 轮内（首次动作在第 %d 轮）" % (ic["params"]["M"], fa)
        if world.valence(aid) < -0.39:
            return True, "网民·自己的帖", "效价跌破中度线"
        return False, None, "无触发"
    if role == "客户":
        # convergent logic: first time below heavy line; afterwards must be LOWER than last post
        v = world.valence(aid)
        v_last = world.valence_at_last_post(aid)
        if v < -0.61:
            return True, "客户-维权自述", "valence %.2f below heavy line" % v
        if v_last is not None and v < v_last:
            return True, "客户-维权自述", "valence %.2f < last-post %.2f" % (v, v_last)
        if world.has_new_dispute():
            return True, "客户-诉求进展", "new dispute"
        return False, None, "no trigger"
    if role == "KOL":
        # KOL trigger switched to OWN state (group average was a category error)
        p = ic["params"]
        thr = p["唤醒阈值"]
        if world.negative_ratio() > p["氛围阈值"]:
            thr -= p["氛围放宽"]
        own_aro, own_val = world.arousal(aid), world.valence(aid)
        hh = hot[1] if hot else 0.0
        if own_aro > thr and own_val < p["效价阈值"] and hh > p["热度线"]:
            return True, "KOL-表态", "own_arousal %.2f>%.2f valence %.2f heat %.2f" % (own_aro, thr, own_val, hh)
        return False, None, "own %.2f / val %.2f / heat %.2f" % (own_aro, own_val, hh)
    return False, None, "未处理角色"


# ---------------------------------------------------------------- 玩具世界
class ToyWorld:
    """★ t0 播种的语义（推演层必须照做）：
         first_action_round : t0 注入【算】—— 他在 t0 就把帖子发出来了，是"参与进来了"
         last_post_round    : t0 注入【不算】—— 那是数据注入，不是他自主发的，不应吃掉冷却期
       否则会出现：网民 t0 发过帖 -> 冷却 4 轮 -> 与"首次互动后 2 轮内"窗口互斥，永远发不出。"""

    def __init__(self, issues, hours=(9, 10, 11, 14, 15, 16, 17), seed_t0=None):
        self.issues = dict(issues); self.hours = hours; self.cur = 1
        self._last, self._cnt, self._cov, self._acov = {}, {}, {t: 0 for t in issues}, {}
        self._fa = {}
        self._vlast = {}
        self.aro = {}
        # ★ 用 t0 快照播种：注入的发帖者写入 first_action_round=0，但不写 last_post_round
        for aid in (seed_t0 or []):
            self._fa[aid] = 0
    def hour(self, r): return self.hours[(r - 1) % len(self.hours)]
    def last_post_round(self, a): return self._last.get(a)
    def post_count(self, a): return self._cnt.get(a, 0)
    def global_post_budget(self): return 99
    def active_topics(self): return [bridge(t) or t for t in self.issues]
    def hottest_topic(self): return max(self.issues.items(), key=lambda kv: kv[1]) if self.issues else None
    def issue_has_media_coverage(self, t): return self._cov.get(t, 0) > 0
    def issue_with_coverage(self, dom): 
        c = [t for t in dom if self._cov.get(t, 0) > 0]; return c[0] if c else None
    def agent_covered(self, a, t): return self._acov.get((a, t), False)
    def topic_in_top3(self, t): return t is not None and t in list(self.issues)[:3]
    def issue_age(self, r): return r
    def has_new_dispute(self): return self.cur % 5 == 0
    def has_source(self, a): return True
    def regulator_stage(self, a): return ["监测", "约谈", "立案", "通报", "处罚"][min(self.cur // 8, 4)]
    def first_action_round(self, a): return self._fa.get(a)
    def valence(self, a): return -0.20
    def has_active_complaint(self, a): return self.cur % 6 == 0
    def arousal(self, a): return self.aro.get(a, 0.45)
    def valence_at_last_post(self, a): return self._vlast.get(a)
    def mean_arousal(self): return 0.55 + 0.02 * self.cur
    def negative_ratio(self): return 0.50 + 0.01 * self.cur
    def fire(self, a, t):
        self._last[a] = self.cur; self._cnt[a] = self._cnt.get(a, 0) + 1
        self._vlast[a] = self.valence(a)
        self._cov[t] = self._cov.get(t, 0) + 1; self._acov[(a, t)] = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", required=True)
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--t0", default=None, help="t0 快照 —— 用初始发帖者播种 first_action_round")
    a = ap.parse_args()
    cfg = load_cfg()
    agents = json.loads(Path(a.agents).read_text(encoding="utf-8"))["agents"]
    issues = {"宠物险争议": 0.351, "广告内容低俗": 0.305, "退保损失": 0.222, "性别与价值观争议": 0.207}
    seed = []
    if a.t0 and Path(a.t0).exists():
        t0 = json.loads(Path(a.t0).read_text(encoding="utf-8"))
        seed = [p["poster_agent_id"] for p in (t0.get("initial_posts") or []) if p.get("poster_agent_id")]
        print("t0 播种 %d 个发帖者：%s\n" % (len(seed), ", ".join(seed)))
    w = ToyWorld(issues, seed_t0=seed); log, per, byrole = [], {}, {}
    for r in range(1, a.rounds + 1):
        w.cur = r
        for ag in agents:
            ok, stage, why = can_post(ag, w, r, cfg)
            if ok:
                w.fire(ag["agent_id"], w.hottest_topic()[0])
                per[ag["agent_id"]] = per.get(ag["agent_id"], 0) + 1
                byrole[ag["role_type"]] = byrole.get(ag["role_type"], 0) + 1
                log.append((r, ag["agent_id"], ag["role_type"], stage, why))
    print("%d 人 / %d 轮 -> 共 %d 条 post\n" % (len(agents), a.rounds, len(log)))
    print("按角色：", {k: v for k, v in sorted(byrole.items(), key=lambda x: -x[1])})
    print("\n发帖最多的前 8 人：")
    for aid, c in sorted(per.items(), key=lambda kv: -kv[1])[:8]:
        ro = next(x["role_type"] for x in agents if x["agent_id"] == aid)
        print("  %-20s %-4s %2d 条" % (aid, ro, c))
    print("\n前 10 条：")
    for r, aid, ro, st, why in log[:10]:
        print("  第%2d轮 %-20s %-4s [%s] %s" % (r, aid, ro, st, why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
