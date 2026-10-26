# -*- coding: utf-8 -*-
r"""预检自检：造一份**故意坏**的事件，看每一类错误是否都被抓住。

在系统临时目录里搭一个假的交付根，把 `paths` 的目录常量指过去，
跑 9 种坏法 + 1 种提示例 + 1 种好法，逐条断言。跑完删掉临时目录。

坏法断言「必须出该 code 的 **error**」；提示例断言「该 code 只出 **warn**、
不得升级成 error，整体仍须通过」—— 例：图谱侧画像库的话题权重和按设计就 ≠ 1.00，
把它判成 error 等于把每一份图谱产出的画像库全拒掉。

    python -I _selftest_precheck.py

为什么要有它：预检器**不报错**比报错危险得多 —— 一个永远说 OK 的校验器
等于没有校验器，但看起来像有。所以坏例必须覆盖到每一类。
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths      # noqa: E402
import precheck   # noqa: E402
import progress   # noqa: E402

ROOT = tempfile.mkdtemp(prefix="_precheck_selftest_")
paths.EVENTS_DIR = os.path.join(ROOT, "事件")

AID = "抖音_网民_0001"


def mk(d, name, obj):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def good_event(d):
    """一份完全合法的最小事件。"""
    mk(d, "事件配置.json", {
        "event_id": "T-OK", "event_brief": "测试事件背景。", "t0_time": "2026-05-22 22:00",
        "run_dir": ".", "persona_file": "画像.json", "n_rounds": 4, "round_hours": 4,
        "llm": {"base_url": "https://x/v1", "api_key": "sk-xxx", "model_name": "m"},
    })
    mk(d, "画像.json", {
        "event_id": "T-OK", "count": 1, "agents": [{
            "agent_id": AID, "role_type": "网民", "primary_channel": "抖音",
            "persona_bio": "你是抖音上的「张三」。", "interest_topics": {"宠物与养宠": 1.0},
            "influence_weight": 0.4, "active_hours": [22, 23], "stance_bias": {"宠物险争议": -0.5},
            "emotion_sensitivity": {"营销类": 1.2}, "valence_base_0": -0.6, "arousal_base_0": 0.5,
            "valence_baseline": -0.03, "arousal_baseline": 0.35,
        }],
    })
    mk(d, "t0状态_第0轮.json", {
        "event_id": "T-OK", "round": 0, "window": ["2026-05-22 22:00", "2026-05-23 02:00"],
        "window_slots": [22, 23, 0, 1],
        "agents": [{"agent_id": AID, "role_type": "网民", "primary_channel": "抖音"}],
        "injected_posts": [{"post_id": "P1", "poster_agent_id": AID, "content": "正文。"}],
    })


CASES = []


def case(name, want_code, mutate):
    CASES.append((name, want_code, mutate))


# ── 坏法 1：event_brief 里残留占位符
case("event_brief 残留 __EVENT_BRIEF__", "cfg.brief.placeholder",
     lambda d: _patch_json(d, "事件配置.json", {"event_brief": "前面 __EVENT_BRIEF__ 后面"}))
# ── 坏法 2：t0_time 写坏（配置本身坏了 ⇒ 加载阶段就硬报错，预期到不了预检）
case("t0_time 解析不了", "@load:t0_time",
     lambda d: _patch_json(d, "事件配置.json", {"t0_time": "2026年5月22日"}))
# ── 坏法 3：画像库缺字段
case("画像库某人缺 stance_bias", "persona.fields",
     lambda d: _del_field(d, "画像.json", "stance_bias"))
# ── 坏法 4：角色不在 5 类枚举
case("role_type 不在 5 类枚举", "persona.role",
     lambda d: _set_field(d, "画像.json", "role_type", "KOC"))
# ── 坏法 5：话题字段不是非空 dict（★ 权重和 ≠ 1 不在此列：图谱侧口径本就 ≠ 1，
#            见 `WARN_CASES` 与 precheck._check_persona 的注释）
case("interest_topics 是空 dict", "persona.topics",
     lambda d: _set_field(d, "画像.json", "interest_topics", {}))
# ── 坏法 6：active_hours 越界
case("active_hours 有 25 点", "persona.hours",
     lambda d: _set_field(d, "画像.json", "active_hours", [22, 25]))
# ── 坏法 7：注入帖没绑发帖人
case("注入帖 poster_agent_id 缺失", "t0.posts.binding",
     lambda d: _del_post_field(d, "poster_agent_id"))
# ── 坏法 8：t0 里的人不在画像库
case("t0 扮演者不在画像库", "t0.ghost",
     lambda d: _patch_json(d, "t0状态_第0轮.json",
                           {"agents": [{"agent_id": "微博_网民_9999", "role_type": "网民",
                                        "primary_channel": "微博"}]}))
# ── 坏法 9：没有任何 LLM 凭据
case("没有任何 LLM 凭据", "llm.none",
     lambda d: _patch_json(d, "事件配置.json",
                           {"llm": {"base_url": "", "api_key": "", "model_name": ""}}))
# ── 坏法 10：角色、渠道**各自合法**，但组合不在 15 格里
#    （好例子是 网民@抖音；监管@抖音 两个枚举都过，组合非法 ⇒ 引擎 build_user KeyError）
case("(监管, 抖音) 不在 15 格", "persona.cell",
     lambda d: _set_field(d, "画像.json", "role_type", "监管"))


# ── 提示（warn）级：能跑、只是结果可疑 —— 这些**不许**判成 error，
#    否则每一份图谱侧产出的画像库都会被拒之门外（交付的上游 12 事件包全是这个形态）。
WARNS = []


def warn_case(name, want_code, mutate):
    WARNS.append((name, want_code, mutate))


warn_case("interest_topics 权重和 ≠ 1.00（图谱侧口径）", "persona.topics.sum",
          lambda d: _set_field(d, "画像.json", "interest_topics", {"宠物与养宠": 0.5}))


def _rd(d, n):
    with open(os.path.join(d, n), encoding="utf-8") as f:
        return json.load(f)


def _wr(d, n, o):
    with open(os.path.join(d, n), "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)


def _patch_json(d, name, patch):
    o = _rd(d, name)
    o.update(patch)
    _wr(d, name, o)


def _set_field(d, name, k, v):
    o = _rd(d, name)
    o["agents"][0][k] = v
    _wr(d, name, o)


def _del_field(d, name, k):
    o = _rd(d, name)
    o["agents"][0].pop(k, None)
    _wr(d, name, o)


def _del_post_field(d, k):
    o = _rd(d, "t0状态_第0轮.json")
    o["injected_posts"][0].pop(k, None)
    _wr(d, "t0状态_第0轮.json", o)


def main():
    fails = []
    print("=" * 78)
    print("预检自检  —— 临时的假交付根：%s" % ROOT)
    print("=" * 78)

    # 0）好例子必须通过
    d0 = os.path.join(paths.EVENTS_DIR, "T-OK")
    good_event(d0)
    rep = precheck.run(paths.load_config("T-OK"), progress.inventory(paths.load_config("T-OK")))
    ok = rep["ok"]
    print("%s 好例子           → ok=%s（error %d）" % ("✔" if ok else "✘", ok, rep["counts"]["error"]))
    if not ok:
        fails.append("好例子")
        for it in rep["items"]:
            if it["level"] == "error":
                print("      %s ｜ %s" % (it["code"], it["title"]))

    # 1）逐条坏法
    for i, (name, want, mutate) in enumerate(CASES, 1):
        eid = "T-%02d" % i
        d = os.path.join(paths.EVENTS_DIR, eid)
        good_event(d)
        mutate(d)
        # `want` 以 "@load:" 开头 = 期望**加载阶段**就硬报错（配置本身坏了，
        # 到不了预检那一步）；否则期望预检返回一条该 code 的 error 条目。
        try:
            cfg = paths.load_config(eid)
        except paths.ConfigError as e:
            ok = want.startswith("@load:") and want[6:] in str(e)
            print("%s 坏法%-2d %-34s → 期望 %-22s %s"
                  % ("✔" if ok else "✘", i, name, want,
                     "加载阶段就已拦下" if ok else "** 漏了 **（%s）" % e))
            if not ok:
                fails.append(name)
            continue
        rep = precheck.run(cfg, progress.inventory(cfg))
        codes = [it["code"] for it in rep["items"] if it["level"] == "error"]
        hit = (not want.startswith("@load:")) and want in codes
        print("%s 坏法%-2d %-34s → 期望 %-22s %s"
              % ("✔" if hit else "✘", i, name, want, "抓到" if hit else "** 漏了 **（实得 %s）" % codes))
        if not hit:
            fails.append(name)

    # 2）提示级：要出现该 warn，且**不许**升级成 error、整体仍须通过
    for i, (name, want, mutate) in enumerate(WARNS, 1):
        eid = "W-%02d" % i
        d = os.path.join(paths.EVENTS_DIR, eid)
        good_event(d)
        mutate(d)
        rep = precheck.run(paths.load_config(eid), progress.inventory(paths.load_config(eid)))
        warns = [it["code"] for it in rep["items"] if it["level"] == "warn"]
        errs = [it["code"] for it in rep["items"] if it["level"] == "error"]
        hit = want in warns and want not in errs and rep["ok"]
        print("%s warn%-2d %-38s → 期望 %-22s %s"
              % ("✔" if hit else "✘", i, name[:38], want,
                 "提示且仍通过" if hit else "** 不对 **（warn %s / error %s）" % (warns, errs)))
        if not hit:
            fails.append(name)

    shutil.rmtree(ROOT, ignore_errors=True)
    print("=" * 78)
    if fails:
        print("✘ 自检不通过：%s" % "；".join(fails))
        sys.exit(1)
    print("✔ 全部 %d 例通过（1 好例 + %d 坏例 + %d 提示例）"
          % (1 + len(CASES) + len(WARNS), len(CASES), len(WARNS)))


if __name__ == "__main__":
    main()
