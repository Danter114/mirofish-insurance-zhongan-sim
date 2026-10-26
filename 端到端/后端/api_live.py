# -*- coding: utf-8 -*-
"""在线版附加接口：看板数据 / 图谱数据 / 深度互动对话。

后端主文件 `app.py` 只做一件事：在 `route()` 定义好之后调用 `register(route, ApiError)`。
三条路由都只**读**运行期产物，不写盘：

  GET  /api/events/{id}/board   看板整包（中间产物\\图谱\\<KEY>\\board.json）
  GET  /api/events/{id}/graph   图谱整包（…\\graph.json）
  POST /api/events/{id}/chat    深度互动（有 key 走模型，没 key 走规则，数字都现算）
  GET  /api/events/{id}/chat    这个事件当前能不能调模型（前端用来显示徽标）

两个 JSON 是 `_p280_make_live_data.mjs` 从前端烘死的数据导出的，与前端源码同源；
所以「页面先 fetch，失败回落到烘死数据」两边是**同一份数**，不是两套。
"""
import json
import os
import re
import urllib.error
import urllib.request

import paths
import pipeline

# ── 读产物 ───────────────────────────────────────────────────────────────
def _dir(eid):
    return paths.event_dir(paths.safe_event_id(eid))


def _load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _read(name, eid, what):
    p = os.path.join(_dir(eid), name)
    if not os.path.exists(p):
        raise ApiError(404, "这一场还没有 %s（%s）。" % (what, name))
    try:
        return _load_json(p)
    except Exception as e:
        raise ApiError(500, "%s 读不出来：%s" % (name, e))


# 角色类型的显示顺序（取自画像文件的声明顺序，与 `gen_roster.py` 的 `TYPES` 同源）。
_ROLE_ORDER = ("网民", "客户", "KOL", "媒体", "监管")


# ── LLM 凭据（只读，不回显）─────────────────────────────────────────────
_ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_NAME")
_DEFAULT_BASE = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"


def _parse_env_file(p):
    out = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() in _ENV_KEYS and v:
                    out[k.strip()] = v
    except Exception:
        pass
    return out


def _creds(cfg):
    """事件级凭据：`SIM_ENV` > 配置里的 `llm.env_path` > `<run_dir>\\.env` > 进程环境变量。

    和后端跑仿真的顺序保持同一取向：**先事件自己的，再进程的**。
    读不到 key 就返回 None —— 调用方降级到规则回答，不报错。
    """
    llm = (cfg or {}).get("llm") or {}
    # ① 界面上填的 key（`POST /api/events/<KEY>/llm` 写进事件配置，事件级最优先）
    if (llm.get("api_key") or "").strip():
        return {"base": (llm.get("base_url") or _DEFAULT_BASE).rstrip("/"),
                "key": llm["api_key"].strip(),
                "model": llm.get("model_name") or _DEFAULT_MODEL}
    # ② `.env` 文件；③ 进程环境变量
    cand = []
    if os.environ.get("SIM_ENV"):
        cand.append(os.environ["SIM_ENV"])
    if llm.get("env_path"):
        cand.append(llm["env_path"])
    run = os.path.dirname((cfg or {}).get("_path") or "")
    if run:
        cand.append(os.path.join(run, ".env"))
    env = {}
    for p in cand:
        if p and os.path.exists(p):
            env = _parse_env_file(p)
            if env.get("LLM_API_KEY"):
                break
    key = env.get("LLM_API_KEY") or os.environ.get("LLM_API_KEY")
    if not key:
        return None
    base = (env.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL")
            or llm.get("base_url") or _DEFAULT_BASE)
    model = (env.get("LLM_MODEL_NAME") or os.environ.get("LLM_MODEL_NAME")
             or llm.get("model_name") or _DEFAULT_MODEL)
    return {"base": base.rstrip("/"), "key": key, "model": model}


# ── 现算事实（数字一律从产物算，不写死）────────────────────────────────
def _facts(cfg):
    eid = cfg["event_id"]
    d = _dir(eid)
    # ★ 2026-10-21：没跑过「报告」段的场次没有 `_viz_data.json`。原来这里直接 open，
    #   `FileNotFoundError` 冒到 HTTP 层变 **500 + traceback**（实测 EVT-003…012 十场）。
    #   这不是"连不上"，是"还没数据" —— 用 409 + 人话回，前端据此说清楚，
    #   而不是 catch 完去播别的场次烘死的问答。
    p = os.path.join(d, cfg.get("data_file") or "_viz_data.json")
    if not os.path.exists(p):
        raise ApiError(409, "这一场还没有推演数据（缺 %s）。"
                            "先在「一键全链」里跑完仿真 → 报告，再用深度互动。"
                            % os.path.basename(p))
    v = _load_json(p)
    meta = v.get("meta") or {}
    alt = v.get("alert") or {}
    rows = alt.get("rows") or []
    lv = {}
    for r in rows:
        lv[r.get("level")] = lv.get(r.get("level"), 0) + 1
    first = alt.get("first_round") or {}
    peak = alt.get("peak") or {}
    cut = alt.get("cut") or {}
    # 首红那一轮自己的 S —— 不能拿峰值轮的分数冒充（模型会把两个数拼成一句话）
    first_row = next((r for r in rows if r.get("round") == first.get("红色")), None)

    def tbl(src, key, n=5, order="posts"):
        items = [(k, val) for k, val in (src or {}).items()]
        items.sort(key=lambda kv: kv[1].get(order, 0), reverse=True)
        return [{"key": k, "v": val} for k, val in items[:n]]

    nodes = sorted(v.get("nodes") or [], key=lambda x: x.get("inter", 0), reverse=True)[:5]
    return {
        "meta": meta,
        "alert_levels": lv,
        "alert_first": first,
        "alert_first_row": first_row,
        "alert_peak": peak,
        "alert_cut": cut,
        "channels": tbl(v.get("ch_stat"), "ch", 6),
        "roles": tbl(v.get("ro_stat"), "role", 6),
        "top_nodes": [{"name": n.get("name"), "role": n.get("role"),
                       "channel": n.get("channel"), "inter": n.get("inter"),
                       "posts": n.get("posts"), "betw": n.get("betw")} for n in nodes],
    }


def _fact_sheet(cfg, f):
    m, cut = f["meta"], f["alert_cut"]
    first, peak = f["alert_first"], f["alert_peak"]
    fris = f["alert_first_row"] or {}
    lines = [
        "事件：%s（%s）" % (cfg.get("report_title") or cfg.get("event_id"),
                          cfg.get("event_type") or ""),
        "仿真窗：%s 起，%s 轮 × %s 小时" % (cfg.get("t0_time"), cfg.get("n_rounds"),
                                         cfg.get("round_hours")),
        "规模：%s 位智能体 / %s 条帖 / %s 条引用边 / %s 次动作" % (
            m.get("agents"), m.get("posts"), m.get("edges"), m.get("actions")),
        "动作分布：%s" % json.dumps(m.get("action_dist") or {}, ensure_ascii=False),
        "风险等级阈值：红 %s / 橙 %s / 黄 %s" % (cut.get("红色"), cut.get("橙色"), cut.get("黄色")),
        # ★ 2026-10-21：这一场可能**根本没有红色预警**（EVT-004 实测：最高只到橙色）。
        #   原来无论有没有都拼「首次红色预警：轮 None」，模型照着复述就成了印错话。
        ("首次红色预警：轮 %s（S=%s，窗口 %s）" % (
            first.get("红色"), fris.get("score"), (fris.get("window") or ["?"])[0]))
        if first.get("红色") is not None else
        "首次红色预警：全程没有出现（最高到%s）" % (peak.get("level") or "—"),
        "峰值：轮 %s（S=%s，%s）" % (peak.get("round"), peak.get("score"), peak.get("level")),
        "各级轮数：%s" % json.dumps(f["alert_levels"], ensure_ascii=False),
    ]
    for r in rows_of(f):
        lines.append(r)
    return "\n".join(lines)


def rows_of(f):
    out = []
    for c in f["channels"]:
        x = c["v"]
        out.append("渠道 %s：智能体 %s / 发帖 %s / 互动 %s / 跨渠道中介 %s" % (
            c["key"], x.get("agents"), x.get("posts"), x.get("inter"), x.get("betw")))
    for r in f["roles"]:
        x = r["v"]
        out.append("角色 %s：智能体 %s / 发帖 %s / 互动 %s" % (
            r["key"], x.get("agents"), x.get("posts"), x.get("inter")))
    for n in f["top_nodes"]:
        out.append("关键节点 %s（%s · %s）：互动 %s / 发帖 %s / 中介 %s" % (
            n["name"], n["role"], n["channel"], n["inter"], n["posts"], n["betw"]))
    return out


# ── 规则回答（无 key 时用；有 key 时也当兜底）──────────────────────────
_RULES = (
    (r"(风险|等级|预警|红|橙|黄)", ("alert",)),
    (r"(峰|最高|最严重|拐点)", ("peak",)),
    (r"(渠道|平台|在哪|微博|抖音|小红书|黑猫|监管|新闻)", ("channel",)),
    (r"(谁|节点|KOL|媒体|大V|关键|影响)", ("node",)),
    (r"(角色|网民|客户|代理|专业)", ("role",)),
    (r"(建议|怎么办|应对|预案|处置)", ("advice",)),
    (r"(多少|规模|人数|帖|动作|轮)", ("scale",)),
)


def _rule_answer(cfg, f, question):
    q = question or ""
    m = f["meta"]
    first, peak, cut = f["alert_first"], f["alert_peak"], f["alert_cut"]
    hits = []
    for rx, keys in _RULES:
        if re.search(rx, q):
            hits.extend(keys)
    if not hits:
        hits = ["alert", "scale"]
    out = []
    if "scale" in hits:
        out.append("这场推演是 %s 轮（%s 小时一轮），%s 位智能体、%s 条帖、%s 条引用边，"
                   "一共产生 %s 次动作。" % (m.get("rounds"), cfg.get("round_hours"),
                                        m.get("agents"), m.get("posts"),
                                        m.get("edges"), m.get("actions")))
    if "alert" in hits:
        out.append("风险等级按 S 值分四档（红 ≥%s / 橙 ≥%s / 黄 ≥%s）。"
                   "全程红色 %s 轮、橙色 %s 轮、黄色 %s 轮、蓝色 %s 轮；%s"
                   % (cut.get("红色"), cut.get("橙色"), cut.get("黄色"),
                      f["alert_levels"].get("红色", 0), f["alert_levels"].get("橙色", 0),
                      f["alert_levels"].get("黄色", 0), f["alert_levels"].get("蓝色", 0),
                      # ★ 没有红色预警时不能印「落在轮 None」（EVT-004 实测）
                      ("首次红色预警落在**轮 %s**，这是预案该启动的时间点。" % first.get("红色"))
                      if first.get("红色") is not None else
                      "**全程没有触发红色预警**，最高到%s —— 预案不必启动红色那一档。"
                      % (peak.get("level") or "—")))
    if "peak" in hits:
        out.append("全程峰值是**轮 %s**，S = %s（%s）。峰值和首次预警不是一回事："
                   "峰值说明最严重到什么程度，首次预警说明什么时候该动。"
                   % (peak.get("round"), peak.get("score"), peak.get("level")))
    if "channel" in hits and f["channels"]:
        c = f["channels"][0]["v"], f["channels"][0]["key"]
        top = "、".join("%s 发帖 %s 条 / 互动 %s" % (x["key"], x["v"].get("posts"), x["v"].get("inter"))
                       for x in f["channels"][:4])
        cross = sorted(f["channels"], key=lambda x: x["v"].get("betw", 0), reverse=True)[0]
        out.append("渠道分布：%s。跨渠道中介最强的是**%s**（betw %s），"
                   "它才是扩散的跳板，不是发帖最多的那个。"
                   % (top, cross["key"], cross["v"].get("betw")))
    if "node" in hits and f["top_nodes"]:
        out.append("按互动量排前几位的关键节点：%s。"
                   % "、".join("%s（%s · %s，互动 %s）" % (n["name"], n["role"], n["channel"], n["inter"])
                              for n in f["top_nodes"][:3]))
    if "role" in hits and f["roles"]:
        out.append("角色侧：%s。"
                   % "、".join("%s %s 人 / 发帖 %s 条" % (x["key"], x["v"].get("agents"), x["v"].get("posts"))
                              for x in f["roles"][:5]))
    if "advice" in hits:
        # ★ 同上：没有红色预警时「轮 None 前挂红色预案」是句错话 —— 改成按实际最高档给
        out.append("处置建议按时间轴排：① %s；"
                   "② 峰值轮 %s 前后把澄清物料铺到中介最强的那个渠道；"
                   "③ 事件后半程效价会向基线回落，别把资源一直压在高峰配置上。"
                   % (("轮 %s 前把红色预案挂到值班口（首次预警）" % first.get("红色"))
                      if first.get("红色") is not None else
                      "全程没有红色预警，按%s档挂橙色预案即可" % (peak.get("level") or "橙"),
                      peak.get("round")))
    out.append("（以上数字均由后端从本场 `_viz_data.json` 现算；配好 LLM key 后同一问题改由模型组织语言。）")
    return "\n\n".join(out)


def _person_answer(f, agent, question):
    name = agent.get("display_name") or agent.get("agent_id")
    role = agent.get("role_type")
    ch = agent.get("primary_channel")
    bio = (agent.get("persona_bio") or "").strip()
    q = question or ""
    head = "我是%s，在%s上的%s。" % (name, ch, role)
    body = []
    if re.search(r"(为什么|怎么会|怎么看|态度|立场|情绪)", q):
        v0 = agent.get("valence_base_0")
        body.append("我对这件事的初始态度是负面的（效价基线 %s），"
                    "所以一看到就忍不住要说话。" % (round(v0, 3) if isinstance(v0, (int, float)) else "偏低"))
    if re.search(r"(做什么|发了|动作|转|评|赞)", q):
        body.append("这次推演里我主要是在自己常待的渠道上参与讨论，"
                    "该评论评论、该转发转发 —— 具体到每轮做了什么，"
                    "左栏报告里的逐轮明细能查到。")
    if not body:
        body.append("你问的这件事，我按自己的处境说：%s" % (bio[:120] + "…" if len(bio) > 120 else bio))
    body.append("（这条是按画像与 t0 快照拼出来的；后端配上 LLM key 后，这个角色会用自己的口吻现场回答。）")
    return head + "".join(body)


# ── LLM 调用 ─────────────────────────────────────────────────────────────
def _chat_llm(creds, system, user, history):
    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-6:]:
        r = h.get("role")
        c = (h.get("content") or "").strip()
        if r in ("user", "assistant") and c:
            msgs.append({"role": r, "content": c})
    msgs.append({"role": "user", "content": user})
    payload = json.dumps({"model": creds["model"], "messages": msgs,
                          "temperature": 0.6, "max_tokens": 700},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        creds["base"] + "/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + creds["key"]})
    with urllib.request.urlopen(req, timeout=90) as r:
        blob = json.loads(r.read().decode("utf-8"))
    return (blob["choices"][0]["message"]["content"] or "").strip()


# ── 路由 ─────────────────────────────────────────────────────────────────
ApiError = None      # 由 app.py 注入


def register(route, api_error_cls, file_response_cls=None):
    """由 `app.py` 在定义好 `route()` / `ApiError` 之后调用。"""
    global ApiError
    ApiError = api_error_cls

    @route("GET", r"/api/events/([^/]+)/board")
    def _(h, m, query, body, eid):
        cfg = paths.load_config(eid)
        return {"ok": True, "event_id": cfg["event_id"],
                "board": _read("board.json", cfg["event_id"], "看板数据包")}

    @route("GET", r"/api/events/([^/]+)/graph")
    def _(h, m, query, body, eid):
        cfg = paths.load_config(eid)
        g = _read("graph.json", cfg["event_id"], "图谱数据包")
        g.pop("findNode", None)          # 函数导不出来，也是唯一的不可序列化字段
        return {"ok": True, "event_id": cfg["event_id"], "graph": g}

    @route("GET", r"/api/events/([^/]+)/chat")
    def _(h, m, query, body, eid):
        cfg = paths.load_config(eid)
        c = _creds(cfg)
        return {"ok": True, "event_id": cfg["event_id"],
                "llm": bool(c), "model": (c or {}).get("model")}

    # ★ 2026-10-21：第 05 页选人的花名册。原来页面上烤死的是 **EVT-001 那 262 个人**，
    #   切到别的场次还拿它选人 ⇒ 后端 `/chat` 以 404「名册里没有这个智能体」拒掉，
    #   页面上看着就是"选了人却问不了"。名册必须跟着事件走。
    #   格式与 `_web_sim_src\tools\gen_roster.py` 烘出来的 `roster.js` **逐字段一致**
    #   （id / channel / label / bio，label = role_type），前端两条路共用一套渲染。
    @route("GET", r"/api/events/([^/]+)/roster")
    def _(h, m, query, body, eid):
        cfg = paths.load_config(eid)
        roster = _read("D_Agent名单.json", cfg["event_id"], "智能体名册")
        seen, extra = {}, []
        for a in roster.get("agents") or []:
            t = a.get("role_type") or "其他"
            if t not in seen:
                seen[t] = []
                if t not in _ROLE_ORDER:
                    extra.append(t)
            seen[t].append({
                "id": a.get("agent_id"),
                "channel": a.get("primary_channel"),
                "label": t,
                "bio": a.get("persona_bio") or "",
            })
        # 组序与 `gen_roster.py` 一致：按声明顺序，不按人数（人数会随事件变，顺序不该晃）
        groups = [{"type": t, "people": seen[t]}
                  for t in list(_ROLE_ORDER) + extra if seen.get(t)]
        return {"ok": True, "event_id": cfg["event_id"],
                "total": len(roster.get("agents") or []), "groups": groups}

    # ── 一键全链：采集 → 图谱 → 仿真 → 报告 → 前端（子进程跑编排器）────────
    @route("POST", r"/api/events/([^/]+)/pipeline")
    def _(h, m, query, body, eid):
        key = paths.safe_event_id(eid)
        if key is None:
            raise ApiError(400, "事件键不合法：%r" % (eid,))
        try:
            return pipeline.start(key, body)
        except pipeline.PipeError as e:
            raise ApiError(400, str(e))

    @route("GET", r"/api/events/([^/]+)/pipeline")
    def _(h, m, query, body, eid):
        key = paths.safe_event_id(eid)
        if key is None:
            raise ApiError(400, "事件键不合法：%r" % (eid,))
        return pipeline.status(key)

    @route("POST", r"/api/events/([^/]+)/pipeline/stop")
    def _(h, m, query, body, eid):
        key = paths.safe_event_id(eid)
        if key is None:
            raise ApiError(400, "事件键不合法：%r" % (eid,))
        try:
            return pipeline.stop(key)
        except pipeline.PipeError as e:
            raise ApiError(400, str(e))

    @route("POST", r"/api/events/([^/]+)/chat")
    def _(h, m, query, body, eid):
        cfg = paths.load_config(eid)
        body = body or {}
        mode = body.get("mode") or "agent"
        q = (body.get("question") or "").strip()
        if not q:
            raise ApiError(400, "问题是空的。")
        if len(q) > 800:
            q = q[:800]
        hist = body.get("history") or []
        f = _facts(cfg)
        creds = _creds(cfg)
        agent = None
        if mode == "person":
            roster = _read("D_Agent名单.json", cfg["event_id"], "智能体名册")
            pid = body.get("person_id") or ""
            pname = body.get("person_name") or ""
            for a in roster.get("agents") or []:
                if (pid and a.get("agent_id") == pid) or (pname and a.get("display_name") == pname):
                    agent = a
                    break
            if agent is None:
                raise ApiError(404, "名册里没有这个智能体：%s" % (pid or pname or "（未给）"))
        if creds:
            try:
                if mode == "person":
                    system = ("你在多智能体舆情推演里扮演一个具体的人，现在被用户追问。\n"
                              "人设（只当背景，不要逐字复述）：%s\n\n"
                              "本场推演的客观事实（只能用这些数字，不许编）：\n%s"
                              % (agent.get("persona_bio") or "", _fact_sheet(cfg, f)))
                    system += ("\n\n规则：用第一人称、口语、2–4 句话；可以带情绪；"
                               "不要提「我是 AI / 模型 / 智能体」；不要念人设原文；"
                               "问到你没参与的部分就说你不太清楚。")
                else:
                    system = ("你是「风控盾」的舆情分析助手。基于下面这份**真跑产物**的事实回答用户。\n\n"
                              "%s\n\n规则：① 只用上面出现过的数字，一个都不许自己造；"
                              "② 先给结论再给依据，控制在 4 句话以内；"
                              "③ 用户问的和本场事件无关就直说不掌握。"
                              % _fact_sheet(cfg, f))
                text = _chat_llm(creds, system, q, hist)
                if text:
                    return {"ok": True, "text": text, "source": "llm",
                            "model": creds["model"], "event_id": cfg["event_id"]}
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                    ValueError, OSError) as e:
                # 调用失败**不静默吞掉结论**：降级到规则，并把原因带回前端
                fallback = (_person_answer(f, agent, q) if mode == "person"
                            else _rule_answer(cfg, f, q))
                return {"ok": True, "text": fallback, "source": "rules",
                        "degraded": "模型调用失败：%s" % (str(e)[:120]),
                        "event_id": cfg["event_id"]}
        text = (_person_answer(f, agent, q) if mode == "person"
                else _rule_answer(cfg, f, q))
        return {"ok": True, "text": text, "source": "rules", "event_id": cfg["event_id"]}
