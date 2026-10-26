# -*- coding: utf-8 -*-
"""事件配置加载器（报告层共用）。

    from _report_config import load
    cfg = load()                      # 自动定位 报告配置.json
    cfg = load(path)                  # 显式指定
    cfg = load(argv)                  # 传 sys.argv[1:] 时，自动认 --config PATH

解析顺序：命令行 `--config PATH`  >  环境变量 `REPORT_CONFIG`  >  `<本文件目录>/报告配置.json`

暴露：`cfg`（原始 dict + 派生属性）、`cfg.run_dir`、`cfg.data_path`、`cfg.out_html`、
      `cfg.snap_path(r)`、`cfg.in_dir(name)`。

★ 设计要点（对应仿真侧「框架冻结 + 事件参数化」）：
  · 路径全部从配置来，代码里不再出现任何事件目录 / 文件名。
  · `run_dir` 之内一律**相对文件名**，换事件只改一份 JSON。
  · 缺键有**明确报错**（拿不到就立刻停），不静默回落到旧事件的路径 ——
    静默回落会让「报告生成成功」变成谎话（印的是上一个事件的数据）。
"""
import argparse, json, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NAME = "报告配置.json"
# ★ 引擎参数化（2026-10-13）：`事件配置.json` 是引擎 + 报告共用的**单事件配置**，
#   报告层也认它。优先级仍是 `报告配置.json` 在前 ⇒ 现役目录（两份都在）行为**零变化**；
#   只有 `事件配置.json` 的交付包里，报告层照样能跑起来（换事件只改一份 JSON）。
FALLBACK_NAMES = ("事件配置.json",)
ENV_VAR = "REPORT_CONFIG"

# 必备键：缺了直接报错（其余键有合理默认）
REQUIRED = ("event_id", "report_title", "run_dir", "out_html")

# 可选键的默认值
DEFAULTS = {
    "doc_title": None,            # None ⇒ 由 event_id 派生
    "eyebrow": None,              # None ⇒ 由 event_id 派生
    "event_type": "",
    # ★ 2026-10-21 第三十二轮（模块五改造）：以下三键升为一等字段。
    #   `event_type_source` / `party_entities` 引擎侧本来就有，报告层此前**完全没读**；
    #   `topic_title` 是「舆情主题」的短标签（= 主体 + 事件短语），报告标题由它派生。
    "event_type_source": "",
    "party_entities": [],
    "subject": "",               # ★ 涉事主体（众安保险）。**不是** party_entities——后者是「被侵害方」
    "topic_title": "",
    "run_label": None,            # None ⇒ 取 run_dir 的末级目录名（附录 B 里显示用）
    "channel_order": [],          # 图 1-2 堆叠顺序的优先序；未列出的渠道追加在后
    "persona_file": "清洗后画像.json",
    "snapshot_pattern": "t{round}状态_第{round}轮.json",
    "t0_end_file": "t0轮末状态.json",
    "recorder_file": "round_snapshot.jsonl",
    "data_file": "_viz_data.json",
}


class Config(dict):
    """配置字典 + 派生路径属性。"""

    # --- 路径 ---
    @property
    def run_dir(self):
        return self["run_dir"]

    @property
    def data_path(self):
        return os.path.join(self["run_dir"], self["data_file"])

    @property
    def out_html(self):
        return self["out_html"]

    def in_dir(self, name):
        """run_dir 内的任意文件名 → 绝对路径。"""
        return os.path.join(self["run_dir"], name)

    def snap_path(self, r):
        """第 r 轮快照。模板用 `{round}` 占位（如 `t{round}状态_第{round}轮.json`）。"""
        return self.in_dir(self["snapshot_pattern"].format(round=r))

    # --- 身份串（缺省时派生）---
    @property
    def doc_title(self):
        return self["doc_title"] or (self["event_id"] + " 多智能体仿真推演报告")

    @property
    def eyebrow(self):
        return self["eyebrow"] or ("Prediction Report · ID: " + self["event_id"])

    @property
    def run_label(self):
        """附录 B「数据来源」里显示的目录名。"""
        return self["run_label"] or os.path.basename(os.path.normpath(self["run_dir"]))

    @property
    def snap_pattern_display(self):
        """快照模板的**展示形**：`t{round}状态_第{round}轮.json` → `t{轮}状态_第{轮}轮.json`。"""
        return self["snapshot_pattern"].replace("{round}", "{轮}")

    def describe(self):
        return ("事件配置: %s\n  report_title : %s\n  event_type   : %s\n"
                "  run_dir      : %s\n  data_file    : %s\n  out_html     : %s"
                % (self["event_id"], self["report_title"], self["event_type"] or "（未填）",
                   self["run_dir"], self["data_file"], self["out_html"]))


def config_path(path=None, argv=None):
    """定出配置文件路径（不读文件）。argv 给了就顺带认 --config。"""
    if path:
        return os.path.abspath(path)
    if argv is not None:
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--config", default=None)
        ns, _ = ap.parse_known_args(argv)
        if ns.config:
            return os.path.abspath(ns.config)
    env = os.environ.get(ENV_VAR)
    if env:
        return os.path.abspath(env)
    p = os.path.join(_HERE, DEFAULT_NAME)
    if os.path.exists(p):
        return p
    for name in FALLBACK_NAMES:          # ★ 见文件头：回退到引擎 + 报告共用的单事件配置
        q = os.path.join(_HERE, name)
        if os.path.exists(q):
            return q
    return p                             # 都不在 ⇒ 返回默认名，交给 load() 报「找不到」


def load(path=None, argv=None, verbose=False):
    """读配置 → Config。argv 传 sys.argv[1:] 时自动认 --config。"""
    p = config_path(path, argv)
    if not os.path.exists(p):
        sys.exit("✗ 找不到事件配置：%s\n"
                 "  复制 报告配置.example.json 为 报告配置.json 后填写，"
                 "或用 --config PATH / 环境变量 %s 指定。" % (p, ENV_VAR))
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        sys.exit("✗ 事件配置解析失败：%s\n  %s" % (p, e))
    if not isinstance(raw, dict):
        sys.exit("✗ 事件配置顶层必须是 JSON 对象：%s" % p)

    cfg = Config()
    for k, v in DEFAULTS.items():
        if k in raw and raw[k] is not None:
            cfg[k] = raw[k]
        elif v is not None:
            cfg[k] = v
        else:
            cfg[k] = raw.get(k)          # 允许显式 null
    for k in REQUIRED:
        if not raw.get(k):
            sys.exit("✗ 事件配置缺少必备键 `%s`：%s\n  必备键：%s" % (k, p, ", ".join(REQUIRED)))
        cfg[k] = raw[k]
    for k in ("event_type",):
        cfg[k] = raw.get(k, "")

    # ★ 2026-10-21 第三十二轮：**标题口径**。
    #   旧做法是 `report_title` 直接取起火点那条帖的 title（`bridge.py:404-425`）——
    #   EVT-001 于是印成「建议某些行业在挑选工作者的时候能够先考察人品舆情演化预测报告」，
    #   那是被曝光视频的标题，读者一眼看不出舆情主题是什么。
    #   现在：配置里给了 `topic_title`（舆情主题，= 主体 + 事件短语）就由它派生标题；
    #   没给则沿用 `report_title`，**旧配置行为逐位不变**。
    _tt = (raw.get("topic_title") or "").strip()
    if _tt:
        cfg["report_title"] = "%s 舆情演化预测报告" % _tt
    cfg["_path"] = p
    cfg["_raw"] = raw

    # 路径规范化（相对路径一律相对配置文件所在目录，避免依赖 CWD）
    base = os.path.dirname(p)
    for k in ("run_dir", "out_html"):
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.abspath(os.path.join(base, cfg[k]))

    if verbose:
        print(cfg.describe())
    return cfg


def add_config_arg(ap):
    """给某个脚本自己的 ArgumentParser 挂上 --config。"""
    ap.add_argument("--config", default=None,
                    help="事件配置 JSON（默认 %s，可用环境变量 %s）" % (DEFAULT_NAME, ENV_VAR))
    return ap


if __name__ == "__main__":
    _c = load(argv=sys.argv[1:], verbose=True)
    print("  config file  :", _c["_path"])
    print("  snap(0)      :", _c.snap_path(0))
    print("  t0轮末       :", _c.in_dir(_c["t0_end_file"]))
    print("  recorder     :", _c.in_dir(_c["recorder_file"]))
    print("  persona      :", _c.in_dir(_c["persona_file"]))
    for _k in ("run_dir", "data_path", "out_html"):
        _v = getattr(_c, _k) if _k != "run_dir" else _c.run_dir
        print("  %-12s : %s  %s" % (_k, _v, "存在" if os.path.exists(_v) else "** 不存在 **"))
