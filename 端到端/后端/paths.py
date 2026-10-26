# -*- coding: utf-8 -*-
"""后端的路径定位与配置读取。

后端**不改引擎 / 报告任何一行代码**，只做三件事：

  1. 定位交付根目录（本文件的上上级）
  2. 定位某事件的 `事件\\<EVENT_ID>\\事件配置.json`
  3. 借用引擎侧 `_sim_config` 读这份配置 —— 配置解析只有**一个真源**，
     后端不另写一份，避免"两处解析、两处漂移"。

分层：后端 → 引擎（读配置、驱动 `跑全轮.py`）。
报告层只通过**子进程**调 `生成报告.py`，不 import —— 报告层自带 `_report_config.py`，
两层保持零耦合（这是交付包的分层约定，别打破）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # 交付根目录
ENGINE_DIR = os.path.join(ROOT, "引擎")
REPORT_DIR = os.path.join(ROOT, "报告")
# ★ 2026-10-20：端到端 v3 把成品写在 `中间产物\图谱\<KEY>\`（跑端到端_v3.py 的 WORK 目录），
#   而后端原先只认 `事件\<KEY>\` ⇒ v3 跑完，后端列表里看不到那场事件。
#   这里改成"哪个布局存在就用哪个"：`中间产物\图谱\` 优先（v3），否则回落 `事件\`（初赛交付布局）。
_GRAPH_OUT = os.path.join(ROOT, "中间产物", "图谱")
EVENTS_DIR = _GRAPH_OUT if os.path.isdir(_GRAPH_OUT) else os.path.join(ROOT, "事件")
STATIC_DIR = os.path.join(HERE, "静态")

# 运行期产物（任务状态、日志尾）—— 不进版本库
RUNTIME_DIR = os.path.join(ROOT, "_运行")

# 引擎侧的配置加载器
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
import _sim_config  # noqa: E402


class ConfigError(Exception):
    """配置读不出来（缺键 / 坏 JSON / 文件不存在）。"""


def safe_event_id(s):
    """事件 id 白名单：字母数字下划线短横 + CJK。挡掉 `..` / `\\` / `:` 这类路径穿越。"""
    s = (s or "").strip()
    if not s or len(s) > 64:
        return None
    for ch in s:
        if ch.isalnum() or ch in "_-" or "一" <= ch <= "鿿":
            continue
        return None
    return s


def config_path(event_id):
    return os.path.join(EVENTS_DIR, event_id, "事件配置.json")


def event_dir(event_id):
    return os.path.join(EVENTS_DIR, event_id)


def runtime_dir(event_id):
    d = os.path.join(RUNTIME_DIR, event_id)
    os.makedirs(d, exist_ok=True)
    return d


def load_config(event_id):
    """读某事件的配置 → `_sim_config.Config`。读不出来抛 `ConfigError`。

    `_sim_config.load()` 在缺必备键时走 `sys.exit()`（那是给命令行用的硬报错），
    这里把它接住转成异常 —— 同一条「取不出来就报错，不静默回落」的规则，
    只是换成服务端能返回 400 而不是把进程干掉。
    """
    eid = safe_event_id(event_id)
    if eid is None:
        raise ConfigError("事件 id 不合法：%r" % (event_id,))
    p = config_path(eid)
    if not os.path.exists(p):
        raise ConfigError("没有这个事件：%s\n  期望的配置文件：%s" % (eid, p))
    try:
        return _sim_config.load(path=p, quiet=True)
    except SystemExit as e:
        raise ConfigError(str(e)) from None


def list_events():
    """扫 `事件\\*\\事件配置.json` → 事件 id 列表（按名字排序）。"""
    if not os.path.isdir(EVENTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(EVENTS_DIR)):
        if safe_event_id(name) and os.path.exists(config_path(name)):
            out.append(name)
    return out


def report_url_path(cfg):
    """报告 HTML 在 HTTP 上的取用路径（后端有专门路由直出，不暴露磁盘绝对路径）。"""
    return "/api/events/%s/report" % cfg["event_id"]
