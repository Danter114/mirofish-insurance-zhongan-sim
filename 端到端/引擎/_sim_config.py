# -*- coding: utf-8 -*-
"""引擎侧（仿真推演）事件配置加载器 —— 与报告层的 `_report_config.py` 成对。

    from _sim_config import load
    cfg = load()                 # 自动定位 事件配置.json（其次 报告配置.json）
    cfg = load(path)             # 显式指定
    cfg = load(argv=sys.argv[1:])  # 自动认 --config PATH

解析顺序：命令行 `--config PATH`  >  环境变量 `SIM_CONFIG`  >  逐个试
          `<本文件目录>/事件配置.json` → `<本文件目录>/报告配置.json`

暴露：`cfg['event_id']` / `cfg.event_brief` / `cfg.t0`（datetime）/ `cfg.n_rounds` /
      `cfg.round_hours` / `cfg.run_dir`（绝对路径）/ `cfg.snap_path(r, suffix)` /
      `cfg.in_dir(name, suffix)` / `cfg.llm`。

★ 设计要点（对应「后端不是针对单事件的：换个事件的知识图谱数据也能跑」）：
  · 代码里**不再出现任何**事件目录 / 文件名 / 事件身份串 / 事件内容 —— 全部走这份配置。
  · `run_dir` 之内一律**相对文件名**；整个文件夹挪到别的盘、别的机器，只要配置在，
    **不用改一行代码**。
  · **没有配置文件时**才回落到内置的 EVT-001 默认值（保证现有流水线零改动照跑），
    且**大声打一条横幅**。一旦给了配置文件，`event_id` / `event_brief` / `t0_time` /
    `persona_file` / `run_dir` **缺一个就硬报错** —— 与 R-41 同哲学：
    「取不出来就报错，不静默回落」。静默回落会让模型拿着**上一个事件的背景**去演下一场，
    是这类管线里最贵的一类错误。
"""
import argparse, json, os, sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

# 候选配置文件（按优先级）。`报告配置.json` 是第十八轮报告层用的名字，保留作兼容别名；
# 新事件统一写 `事件配置.json`（引擎 + 报告两层的键放同一份）。
CANDIDATES = ("事件配置.json", "报告配置.json")
ENV_VAR = "SIM_CONFIG"

# 给了配置文件时**必须**齐的键（缺一即报错）
REQUIRED = ("event_id", "event_brief", "t0_time", "persona_file", "run_dir")

# 通用默认值（结构性约定，与具体事件无关）
DEFAULTS = {
    "t0_file": "t0状态_第0轮.json",              # t0 快照（轮首）
    "snapshot_pattern": "t{round}状态_第{round}轮.json",
    "t0_end_file": "t0轮末状态.json",
    "recorder_file": "round_snapshot.jsonl",
    "round_hours": 4,
    "n_rounds": 42,
    "llm": {},
}

# ── 只在「完全没有配置文件」时使用的内置回落（= EVT-001 现状值）────────────
LEGACY_ENV = os.environ.get("SIM_ENV", "")  # 原为本机绝对路径
LEGACY = {
    "event_id": "EVT-001",
    "event_brief": "2026 年 5 月下旬，众安保险的一则宠物险广告被指内容低俗，最早出现在抖音上。",
    "t0_time": "2026-05-22 22:00",
    "persona_file": "清洗后event1画像.json",
    "run_dir": _HERE,
    "llm": {"env_path": LEGACY_ENV},
}

_TIME_FMTS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
              "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M")


class Config(dict):
    """配置字典 + 派生属性 / 路径工具。"""

    # --- 基本 ---
    @property
    def run_dir(self):
        return self["run_dir"]

    @property
    def event_id(self):
        return self["event_id"]

    @property
    def event_brief(self):
        return self["event_brief"]

    @property
    def n_rounds(self):
        return int(self["n_rounds"])

    @property
    def round_hours(self):
        return self["round_hours"]

    @property
    def llm(self):
        return self["llm"]

    @property
    def t0(self):
        """t0 时刻（datetime）。每次现算，避免 dict 里塞非 JSON 类型。"""
        return parse_time(self["t0_time"])

    # --- 路径 ---
    @staticmethod
    def with_suffix(name, suffix):
        """`round_snapshot.jsonl` + `.mock` → `round_snapshot.mock.jsonl`
           `t0轮末状态.json`       + `.mock` → `t0轮末状态.mock.json`"""
        if not suffix:
            return name
        base, ext = os.path.splitext(name)
        return base + suffix + ext

    def in_dir(self, name, suffix=""):
        """run_dir 内的任意文件名 → 绝对路径（可选加 `.mock` 这类后缀）。"""
        return os.path.join(self["run_dir"], self.with_suffix(name, suffix))

    def snap_path(self, r, suffix=""):
        """第 r 轮快照路径。模板用 `{round}` 占位。"""
        return self.in_dir(self["snapshot_pattern"].format(round=r), suffix)

    @property
    def t0_path(self):
        return self.in_dir(self["t0_file"])

    def t0_end_path(self, suffix=""):
        return self.in_dir(self["t0_end_file"], suffix)

    def recorder_path(self, suffix=""):
        return self.in_dir(self["recorder_file"], suffix)

    @property
    def persona_path(self):
        return self.in_dir(self["persona_file"])

    # --- 展示 ---
    def describe(self):
        return ("事件配置: %s   （%s）\n"
                "  event_brief  : %s\n"
                "  t0_time      : %s   轮长 %sh × %d 轮 = %sh\n"
                "  run_dir      : %s\n"
                "  画像库        : %s\n"
                "  快照模板      : %s"
                % (self["event_id"], self.get("_source", "?"),
                   self["event_brief"][:40] + ("…" if len(self["event_brief"]) > 40 else ""),
                   self["t0_time"], self["round_hours"], self.n_rounds,
                   self["round_hours"] * self.n_rounds,
                   self["run_dir"], self["persona_file"], self["snapshot_pattern"]))


def parse_time(s):
    """把配置里的时间串解析成 datetime。认 `2026-05-22 22:00` / ISO / 带秒 三种写法。"""
    if isinstance(s, datetime):
        return s
    t = str(s).strip()
    for f in _TIME_FMTS:
        try:
            return datetime.strptime(t, f)
        except ValueError:
            pass
    sys.exit("✗ 事件配置里的 `t0_time` 解析不了：%r\n  可用格式：%s"
             % (s, " / ".join(_TIME_FMTS)))


def _find(path=None, argv=None):
    """定出配置文件路径。找不到返回 None（⇒ 走内置回落）。"""
    if path:
        p = os.path.abspath(path)
        if not os.path.exists(p):
            sys.exit("✗ 指定的配置文件不存在：%s" % p)
        return p
    if argv is not None:
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--config", default=None)
        ns, _ = ap.parse_known_args(argv)
        if ns.config:
            p = os.path.abspath(ns.config)
            if not os.path.exists(p):
                sys.exit("✗ --config 指定的配置文件不存在：%s" % p)
            return p
    env = os.environ.get(ENV_VAR)
    if env:
        p = os.path.abspath(env)
        if not os.path.exists(p):
            sys.exit("✗ 环境变量 %s 指向的配置文件不存在：%s" % (ENV_VAR, p))
        return p
    for name in CANDIDATES:
        p = os.path.join(_HERE, name)
        if os.path.exists(p):
            return p
    return None


def load(path=None, argv=None, verbose=False, quiet=False):
    """读配置 → Config。找不到配置文件时回落到内置 EVT-001 默认值（打横幅）。"""
    p = _find(path, argv)

    if p is None:
        cfg = Config()
        for k, v in DEFAULTS.items():
            cfg[k] = dict(v) if isinstance(v, dict) else v
        for k, v in LEGACY.items():
            cfg[k] = dict(v) if isinstance(v, dict) else v
        cfg["run_dir"] = os.path.abspath(cfg["run_dir"])
        cfg["_source"] = "内置默认（EVT-001）"
        cfg["_path"] = None
        cfg["_legacy"] = True
        if not quiet:
            print("⚠️  未找到 %s —— 按**内置的 EVT-001 默认值**运行。\n"
                  "    换事件请在本目录放一份 %s（或用 --config / 环境变量 %s）。"
                  % (" 或 ".join(CANDIDATES), CANDIDATES[0], ENV_VAR))
        return cfg

    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        sys.exit("✗ 事件配置解析失败：%s\n  %s" % (p, e))
    if not isinstance(raw, dict):
        sys.exit("✗ 事件配置顶层必须是 JSON 对象：%s" % p)

    miss = [k for k in REQUIRED if not raw.get(k)]
    if miss:
        sys.exit("✗ 事件配置缺少必备键：%s\n  文件：%s\n  必备键：%s\n"
                 "  （不静默回落到上一个事件的取值 —— 那会让仿真拿着别人的事件背景去演。）"
                 % (", ".join("`%s`" % k for k in miss), p, ", ".join(REQUIRED)))

    cfg = Config()
    for k, v in DEFAULTS.items():
        cfg[k] = dict(v) if isinstance(v, dict) else v
    for k, v in raw.items():
        if k.startswith("_"):            # `_说明` 这类注释键不带进配置
            continue
        if k == "llm":
            d = dict(DEFAULTS["llm"])
            d.update(v or {})
            cfg["llm"] = d
        else:
            cfg[k] = v

    # 解析校验（早崩：t0_time 写错要在这里就炸，不能等跑起来）
    parse_time(cfg["t0_time"])
    cfg["_source"] = p
    cfg["_path"] = p
    cfg["_legacy"] = False

    # 路径规范化：相对路径一律相对**配置文件所在目录**（不依赖 CWD）
    base = os.path.dirname(p)
    if not os.path.isabs(cfg["run_dir"]):
        cfg["run_dir"] = os.path.abspath(os.path.join(base, cfg["run_dir"]))
    else:
        cfg["run_dir"] = os.path.abspath(cfg["run_dir"])

    # LLM 凭据的 `.env` 位置：`llm.env_path` > 环境变量 SIM_ENV > `<run_dir>/.env`
    # > `<配置目录>/.env`。都不在就留空，`load_env()` 会给出明确报错（不猜路径）。
    ep = (cfg["llm"].get("env_path") or "").strip()
    if ep and not os.path.isabs(ep):
        ep = os.path.abspath(os.path.join(base, ep))
    if not ep:
        ep = (os.environ.get("SIM_ENV") or "").strip()
    if not ep:
        for cand in (os.path.join(cfg["run_dir"], ".env"), os.path.join(base, ".env")):
            if os.path.exists(cand):
                ep = cand
                break
    cfg["llm"]["env_path"] = ep

    if verbose:
        print(cfg.describe())
    return cfg


def add_config_arg(ap):
    """给某个脚本自己的 ArgumentParser 挂上 --config。"""
    ap.add_argument("--config", default=None,
                    help="事件配置 JSON（默认 %s，可用环境变量 %s）"
                         % (" 或 ".join(CANDIDATES), ENV_VAR))
    return ap


if __name__ == "__main__":
    _c = load(argv=sys.argv[1:], verbose=True)
    for _r in (0, 1, _c.n_rounds):
        print("  snap(%-3d)   : %s" % (_r, _c.snap_path(_r)))
    print("  t0           :", _c.t0_path)
    print("  t0 时刻       :", _c.t0)
    print("  t0轮末        :", _c.t0_end_path())
    print("  记录员        :", _c.recorder_path())
    print("  画像库        :", _c.persona_path,
          "存在" if os.path.exists(_c.persona_path) else "** 不存在 **")
    print("  LLM .env     :", _c.llm.get("env_path"),
          "存在" if os.path.exists(_c.llm.get("env_path", "")) else "** 不存在 **")
