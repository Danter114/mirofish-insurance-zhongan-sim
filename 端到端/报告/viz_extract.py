# -*- coding: utf-8 -*-
"""抽取 event1 仿真推演结果，输出可视化所需的数据包 JSON。不写入交付目录。

口径要点：
  · 曲线与计数（`series` / `actions`）走 `round_snapshot.jsonl`，一律【轮末】口径；
    逐轮**快照**（`rounds`）取 `t{轮}状态_第{轮}轮.json`，轮 0 是 `t0状态_第0轮.json`
    ＝**轮首**。★ 轮 0 这一处**轮首 / 轮末 分裂是既有的**，见 `_snap_path()` 的注释，
    本脚本只标注不动。
    ★ R-50（2026-10-12）：轮数**不再写死 36**，改为从快照文件自身推。
      否则轮次表一改（36 → 42）本脚本会**静默截断到第 35 轮**，报告数字全错。
      这与 `rerun_recorder.py` 的做法一致（同样是"外部复制轮次表 = 漂移点"）。
  · 跨渠道边 = 某帖的落地渠道集合在某一轮新增了渠道（记录员 new_cross_channel_edges 同定义）
  · 关键影响节点三项指标：出度（被引用）、介数（经过该帖的传播链数）、中心性（特征向量）
"""
import json, io, sys, os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ★ 2026-10-13 第十八轮：路径与文件名一律走**事件配置**，代码里不再出现事件目录。
#   存量调用 `python viz_extract.py` 行为不变（默认读同目录的 报告配置.json）；
#   换事件时 `python viz_extract.py --config 别的配置.json` 即可。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _report_config import load as _load_cfg

CFG = _load_cfg(argv=sys.argv[1:])
D = CFG.run_dir
OUT = CFG.data_path
print(CFG.describe())


def _snap_path(r):
    """第 r 轮的**注入前**快照。

    ⚠ 遗留（R-50 只标注、**不动**）：轮 0 这里取的是 `t0状态_第0轮.json`，那是**轮首**；
      而记录员与下方的 `series` 走的是 `round_snapshot.jsonl` 的**轮末**口径
      （轮 0 效价 −0.2434 vs −0.2919，见 `_probe_ag.py`）。两个文件连结构都不同：
      轮首 `agents` 是 **list** 且只有 `injected_posts`，轮末 `agents` 是 **dict** 且有 `posts`。
      本脚本的 `rounds[0]` 只喂 `chan_of[0]`（建跨渠道边）与「未入视野」两处，
      所以这个分裂**没有**污染 series / posts / agents。
      ⇒ 换口径要单独走一次：先跟仿真侧确认轮 0 按轮首还是轮末，再一起改。
        我试过顺手换成 `t0轮末状态.json`，结果是——轮末的 `agents` 是 dict，
        「未入视野」那段当场 `AttributeError`（`for a in dict` 拿到的是字符串键），
        而且轮 0 的跨渠道边会跟着变。**别顺手换。**
    """
    return CFG.snap_path(r)


# ---------- 1. 逐轮快照 ----------
# ★ R-50：轮数从快照文件自身推（连续编号，遇到第一个缺口即停）
NR = 0
while os.path.exists(_snap_path(NR)):
    NR += 1
rounds = {r: json.load(open(_snap_path(r), encoding='utf-8')) for r in range(NR)}
missing = [r for r in range(NR) if r not in rounds]
print("轮数(NR) =", NR, "｜ rounds loaded:", len(rounds), "missing:", missing)

last = rounds[max(rounds)]
posts = last["posts"]
agents = {a["agent_id"]: a for a in last["agents"]}
posts_by_id = {p["post_id"]: p for p in posts}

# ---------- 2. 引用森林：深度 / 出度 / 介数 ----------
children = defaultdict(list)
for p in posts:
    if p.get("quoted_id"):
        children[p["quoted_id"]].append(p["post_id"])


def ancestry(pid):
    """沿 quoted_id 上溯，返回 [根, ..., pid]。"""
    chain, cur, guard = [], pid, 0
    while cur and guard < 60:
        chain.append(cur)
        par = posts_by_id[cur].get("quoted_id")
        cur = par if par in posts_by_id else None
        guard += 1
    return list(reversed(chain))


def n_desc(pid):
    n, stack = 0, list(children.get(pid, []))
    while stack:
        x = stack.pop()
        n += 1
        stack.extend(children.get(x, []))
    return n


outdeg, chain_len, nanc, ndesc, between = {}, {}, {}, {}, {}
for p in posts:
    pid = p["post_id"]
    chain = ancestry(pid)
    outdeg[pid] = len(children.get(pid, []))
    chain_len[pid] = len(chain) - 1          # 深度
    nanc[pid] = len(chain) - 1               # 严格祖先数
    ndesc[pid] = n_desc(pid)                 # 严格后代数
    between[pid] = nanc[pid] * ndesc[pid]    # 经过该帖的「祖先→后代」传播链数

# 特征向量中心性（无向化，幂迭代）
ids = [p["post_id"] for p in posts]
idx = {k: i for i, k in enumerate(ids)}
n = len(ids)
adj = [[] for _ in range(n)]
for p in posts:
    q = p.get("quoted_id")
    if q in posts_by_id:
        a, b = idx[q], idx[p["post_id"]]
        adj[a].append(b); adj[b].append(a)
x = [1.0] * n
for _ in range(200):
    y = [0.0] * n
    for i in range(n):
        for j in adj[i]:
            y[i] += x[j]
    nrm = max(1e-12, max(y) if max(y) > 0 else 1e-12)
    y = [v / nrm for v in y]
    if max(abs(y[i] - x[i]) for i in range(n)) < 1e-10:
        x = y; break
    x = y
eig = {ids[i]: x[i] for i in range(n)}

layer_sizes = Counter(chain_len.values())
print("depth layers:", dict(sorted(layer_sizes.items())), "max:", max(chain_len.values()))
print("max betweenness:", max(between.values()), "max eig:", round(max(eig.values()), 4))

# ---------- 3. 动作（跨全部轮次累积） ----------
acts = []
for r in sorted(rounds):
    acts.extend(rounds[r].get("actions", []))
print("actions total:", len(acts))
act_cnt = Counter(a["action"] for a in acts)
print("action dist:", act_cnt.most_common())
by_round = defaultdict(Counter)
for a in acts:
    by_round[a["round"]][a["action"]] += 1

post_round = {}
for a in acts:
    if a.get("new_post_id"):
        post_round[a["new_post_id"]] = a["round"]
for p in posts:
    p["_round"] = post_round.get(p["post_id"], 0)

# ---------- 4. ★ 跨渠道传播事件（记录员同定义：channels 集合在某轮新增渠道） ----------
chan_of = {}
for r in sorted(rounds):
    for p in (rounds[r].get("posts") or rounds[r].get("injected_posts") or []):
        ch = p.get("channels")
        chan_of.setdefault(r, {})[p["post_id"]] = set(ch) if ch else {p["channel"]}

cross_events = []
for r in range(1, NR):
    prev, cur = chan_of.get(r - 1, {}), chan_of.get(r, {})
    for pid, chs in cur.items():
        if pid not in prev:          # 本轮新生的帖子不算跨渠道，只算「已存在的帖子又落到新平台」
            continue
        new = chs - prev[pid]
        if new:
            p = posts_by_id.get(pid, {})
            cross_events.append({
                "round": r, "window": rounds[r]["window"],
                "post": pid, "name": (p.get("poster_name") or p.get("poster_agent_id")),
                "role": agents.get(p.get("poster_agent_id"), {}).get("role_type", "?"),
                "origin": p.get("channel"),
                "from": sorted(chs - new), "to": sorted(new),
                "prev_n": len(prev[pid]), "now_n": len(chs),
            })
print("\n★ 跨渠道事件数:", len(cross_events))
for e in cross_events:
    print("   轮%2d %s  %s(%s)  %s → %s" % (e["round"], e["window"][0], e["post"],
          e["origin"], "+".join(e["from"]) or "-", "+".join(e["to"])))
print("   按轮计数:", dict(Counter(e["round"] for e in cross_events)))

rec = [json.loads(l) for l in open(CFG.in_dir(CFG["recorder_file"]), encoding='utf-8') if l.strip()]
g = sorted([x for x in rec if x["scope_type"] == "global"], key=lambda x: x["round"])
ch_rows = [x for x in rec if x["scope_type"] == "channel"]
print("\n记录员 new_cross_channel_edges 逐轮:", [x["new_cross_channel_edges"] for x in g],
      "合计", sum(x["new_cross_channel_edges"] for x in g))
print("记录员 channel 行数:", len(ch_rows),
      "渠道:", sorted(set(x["scope"] for x in ch_rows)))

# ---------- 5. 名册 ----------
roster = {}
try:
    for a in json.load(open(CFG.in_dir(CFG["persona_file"]), encoding='utf-8'))["agents"]:
        roster[a["agent_id"]] = a
except Exception as e:
    print("roster join failed:", e)

# ---------- 6. 关键影响节点（账号级） ----------
ag = defaultdict(lambda: {"posts": 0, "out": 0.0, "betw": 0.0, "eig": 0.0,
                          "inter": 0.0, "like": 0, "cmt": 0, "rep": 0, "acts": 0,
                          "plist": []})
for p in posts:
    a = ag[p["poster_agent_id"]]
    a["posts"] += 1
    a["out"] += outdeg[p["post_id"]]
    a["betw"] += between[p["post_id"]]
    a["eig"] += eig[p["post_id"]]
    # ★ 2026-10-12 口径修正：原取 p["interactions"]，而它 == like+comment（**不含转发**，
    #   实测 126/126 帖成立）—— 与报告里「互动量 = 评论+转发+点赞」的口径不一致（差 22 次）。
    #   改为三项相加，使 Σ 账号级 inter == 全期互动量 496。
    a["inter"] += (p.get("like_cnt_sim", 0) + p.get("comment_cnt_sim", 0)
                   + p.get("repost_cnt_sim", 0))
    a["like"] += p.get("like_cnt_sim", 0)
    a["cmt"] += p.get("comment_cnt_sim", 0)
    a["rep"] += p.get("repost_cnt_sim", 0)
    a["plist"].append(p["post_id"])
for a in acts:
    ag[a["agent_id"]]["acts"] += 1


def role_of(aid):
    return agents.get(aid, {}).get("role_type") or (aid.split("_")[1] if "_" in aid else "网民")


def chan_of_agent(aid):
    return agents.get(aid, {}).get("primary_channel") or (aid.split("_")[0] if "_" in aid else "?")


nodes_rank = []
for aid, s in ag.items():
    nm = next((p.get("poster_name") for p in posts if p["poster_agent_id"] == aid), None)
    nodes_rank.append({
        "agent_id": aid, "name": nm or aid, "role": role_of(aid), "channel": chan_of_agent(aid),
        "posts": s["posts"], "out": s["out"], "betw": s["betw"],
        "eig": round(s["eig"], 5), "inter": s["inter"], "acts": s["acts"],
        "like": s["like"], "cmt": s["cmt"], "rep": s["rep"],
        "w": (roster.get(aid) or {}).get("influence_weight"),
        "v_end": agents.get(aid, {}).get("valence"),
        "posts_list": s["plist"],
    })
nodes_rank.sort(key=lambda x: (-x["out"], -x["betw"], -x["eig"]))
print("\n账号数:", len(nodes_rank))
for x in nodes_rank[:10]:
    print("  %-22s %-4s %-6s 出度%3.0f 介数%6.0f 特征%.4f 帖%2d 互动%3.0f w=%s" % (
        x["name"], x["role"], x["channel"], x["out"], x["betw"], x["eig"], x["posts"],
        x["inter"], x["w"]))

# ---------- 7. 渠道维度 / 角色维度 影响力 ----------
def group_stats(keyfn):
    """agents = 有任一动作者；posters = 有发帖者。两者口径不同，分开计。"""
    out = defaultdict(lambda: {"agents": set(), "posters": set(), "posts": 0, "out": 0.0,
                               "betw": 0.0, "inter": 0.0, "zero": 0})
    for x in nodes_rank:
        o = out[keyfn(x)]
        o["agents"].add(x["agent_id"])
        o["posts"] += x["posts"]
        o["out"] += x["out"]
        o["betw"] += x["betw"]
        o["inter"] += x["inter"]
        if x["posts"] > 0:
            o["posters"].add(x["agent_id"])
        if x["out"] == 0:
            o["zero"] += 1
    return {k: {"agents": len(v["agents"]), "posters": len(v["posters"]), "posts": v["posts"],
                "out": v["out"], "betw": v["betw"], "inter": v["inter"], "zero": v["zero"],
                "out_per_post": round(v["out"] / max(1, v["posts"]), 3)}
            for k, v in out.items()}


ch_stat = group_stats(lambda x: x["channel"])
ro_stat = group_stats(lambda x: x["role"])
print("\n渠道维度:", json.dumps(ch_stat, ensure_ascii=False, indent=1))
print("角色维度:", json.dumps(ro_stat, ensure_ascii=False, indent=1))

# ---------- 8. 角色×轮次、渠道×轮次 情绪矩阵 ----------
ROLES = ["网民", "客户", "KOL", "媒体", "监管"]
role_mat = {r: [] for r in ROLES}
for x in g:
    rs = x.get("role_slices") or {}
    for r in ROLES:
        role_mat[r].append((rs.get(r) or {}).get("valence_mean"))

chans = sorted(set(x["scope"] for x in ch_rows))
ch_mat = {c: [None] * NR for c in chans}
for x in ch_rows:
    if 0 <= x["round"] < NR:
        ch_mat[x["scope"]][x["round"]] = x["valence_mean"]
print("\n角色×轮次矩阵非空:", {r: sum(1 for v in v_ if v is not None) for r, v_ in role_mat.items()})
print("渠道×轮次矩阵非空:", {c: sum(1 for v in v_ if v is not None) for c, v_ in ch_mat.items()})

# ---------- 8c. 分类法守卫（第十八轮新增） ----------
# ★ 载荷是**事件无关**的：角色 5 个 / 渠道 6 个是平台口径，不随事件变；但**本跑实际产出**
#   只覆盖其中一部分（EVT-001 里 监管通报、黑猫投诉全程 0 帖）。报告层的渲染是**按名字直取**
#   这些键的，缺位时会取到 undefined 甚至崩溃。所以：
#     · 在这里把「谁缺位」显式打出来（供人核对），
#     · 缺到没法出图（整个维度为空）时**直接报错停机** ——
#       绝不能静默塞 0 进报告，那会让「报告生成成功」变成一句谎话。
TAXO_ROLES = ["网民", "客户", "KOL", "媒体", "监管"]
# 「缺位」有两种，必须分开说，否则会把「有情绪曲线但没发过帖」误报成「没缺位」：
miss_role_seq  = [r for r in TAXO_ROLES
                  if not role_mat.get(r) or all(v is None for v in role_mat[r])]  # 连曲线都没有
zero_role_post = [r for r in TAXO_ROLES
                  if not ro_stat.get(r) or not ro_stat[r]["posts"]]               # 有曲线但 0 帖
miss_ch = [c for c in chans if not ch_stat.get(c) or not ch_stat[c]["posts"]]
if not ch_stat:
    sys.exit("✗ 渠道维度为空：本跑没有任何帖子落在已知渠道上，报告层无法出图。")
if not ro_stat:
    sys.exit("✗ 角色维度为空：本跑没有任何角色产出，报告层无法出图。")
if not chans:
    sys.exit("✗ 记录员没有任何渠道行（scope_type=channel），报告层无法出图。")
print("角色 · 无情绪序列:", miss_role_seq or "无")
print("角色 · 0 帖      :", zero_role_post or "无")
print("渠道 · 0 帖      :", miss_ch or "无")

# 时点维度：全场最低轮 + 各角色最低轮
low = min(g, key=lambda x: x["valence_mean"])
print("\n★ 全场情绪最低轮: 轮%d %s  %.4f" % (low["round"], low["window"][0], low["valence_mean"]))
for r in ROLES:
    vv = [(g[i]["round"], role_mat[r][i]) for i in range(len(role_mat[r])) if role_mat[r][i] is not None]
    if vv:
        m = min(vv, key=lambda t: t[1])
        print("   角色 %-4s 最低 轮%2d  %.4f   （末轮 %.4f）" % (r, m[0], m[1], vv[-1][1]))
for c in chans:
    vv = [(i, ch_mat[c][i]) for i in range(NR) if ch_mat[c][i] is not None]
    if vv:
        m = min(vv, key=lambda t: t[1])
        print("   渠道 %-6s 最低 轮%2d  %.4f  （末轮 %.4f）  非空%d" % (c, m[0], m[1], vv[-1][1], len(vv)))

# ---------- 8b. 角色 × 渠道 × 轮次 效价矩阵 + 逐轮钟点增益 ----------
# ★ 2026-10-12 模块二改版新增（仿真侧要求 2.2 出「角色与渠道的交叉洞察」）。
#   两张热力图换成折线图之后，正文需要回答"是谁在哪个渠道上把全场情绪拖下去的"，
#   这要求角色与渠道**交叉**后的逐轮曲线 —— 而 role_mat（按角色）与 ch_mat（按渠道）
#   都是单维边际量，答不了。故补一张二维矩阵。
#   取数口径：逐轮**轮末**快照的 agents[] 同时带 role_type 与 primary_channel。
#     · r >= 1 用 t{r}状态_第{r}轮.json（轮末口径，与 series 一致）
#     · r == 0 用 t0轮末状态.json（该文件 agents 是 dict 且只有 valence/arousal，无 role/channel，
#       故角色/渠道身份回落到 `role_of()/chan_of_agent()` 按 id 解析 —— 与 role_mat 同法）
#   实测：按此现算的 role_mat 与载荷 role_slices 口径 **0/210 不一致**（见 _probe_mod2rc.py），
#   即矩阵与既有两个边际量同源，可直接并列渲染。非空单元 15 个，逐轮构成恒定。
T0END = CFG.in_dir(CFG["t0_end_file"])
_t0end = None
if os.path.exists(T0END):
    _t0end = json.load(open(T0END, encoding="utf-8")).get("agents") or {}
rc_mat = {r: {c: [None] * NR for c in chans} for r in ROLES}
rc_cnt = {r: {c: 0 for c in chans} for r in ROLES}
for _r in range(NR):
    if _r == 0:
        if not _t0end:
            continue
        _src = [(aid, v.get("valence")) for aid, v in _t0end.items()]
    else:
        _src = [(a.get("agent_id"), a.get("valence")) for a in rounds[_r]["agents"]]
    _acc = defaultdict(list)
    for _aid, _v in _src:
        if _aid is None or _v is None:
            continue
        _acc[(role_of(_aid), chan_of_agent(_aid))].append(_v)
    for (_ro, _ch), _vs in _acc.items():
        if _ro in rc_mat and _ch in rc_mat[_ro]:
            rc_mat[_ro][_ch][_r] = round(sum(_vs) / len(_vs), 4)
            if _r == NR - 1:
                rc_cnt[_ro][_ch] = len(_vs)
print("\n角色×渠道矩阵非空单元:", sum(1 for r in ROLES for c in chans
      if any(v is not None for v in rc_mat[r][c])), "/", len(ROLES) * len(chans))
print("末轮各单元人数:", {r: {c: n for c, n in rc_cnt[r].items() if n} for r in ROLES})

# ★ 逐轮钟点增益 Ḡ：直接读各轮快照**自带**的 params.gbar（轮 0 无 G/gbar ⇒ null）。
#   报告层用它来甄别"日内锯齿"：本跑 corr(Ḡ_r, Δ效价_r) ≈ −0.88，
#   即每夜覆盖 02—05 点的轮（谷格 0.3 倍增益）恒伴随效价回升 —— 那是机制节律，不是事件。
#   **不在报告层复刻钟点表**（复刻 = 又一处随规则改动而漂移的硬编码）。
gbar = [None] * NR
for _r in range(1, NR):
    _pr = rounds[_r].get("params") or {}
    _g = _pr.get("gbar")
    if _g is None:
        _g = _pr.get("G")
    gbar[_r] = _g
print("逐轮 gbar 非空:", sum(1 for v in gbar if v is not None), "/", NR,
      "｜取值集合:", sorted({v for v in gbar if v is not None}))

# ---------- 9. 未入视野 ----------
ever = set()
for r in sorted(rounds):
    for a in rounds[r].get("agents", []):
        for v in a.get("view", []) or []:
            ever.add(v)
pool = set(posts_by_id)
seen = pool & ever
print("\npool:", len(pool), "ever:", len(seen), "never:", len(pool - seen),
      "%.1f%%" % (100.0 * len(pool - seen) / len(pool)))

# ---------- 10. 按自然日分摊（帖子量用实际发布时间；互动量按轮次窗口小时数分摊） ----------
FMT = "%Y-%m-%d %H:%M"
_day = defaultdict(lambda: {"h": 0.0, "post": 0.0, "comment": 0.0, "repost": 0.0,
                            "like": 0.0, "inter": 0.0, "flow": 0.0,
                            "vw": 0.0, "vwn": 0.0, "online": 0.0})
for x in g:
    t0 = datetime.strptime(x["window"][0], FMT)
    t1 = datetime.strptime(x["window"][1], FMT)
    span = (t1 - t0).total_seconds() / 3600.0
    # ★ 2026-10-12 口径修正：`interaction_total` 是**存量**（当前在视野内的帖子累计被赞+评论，
    #   见 recorder_a.py:132），帖子离开视野时它会下降 ⇒ 按日分摊再求和 = 把同一批互动反复重计
    #   （本跑 Σ=5224，而真实互动动作只有 423+22+51=496）。故新增 `flow` = 本轮三类互动动作之和，
    #   是**流量**，可加总。两者都留在载荷里：`flow` 供报告渲染，`inter` 仅留作审计。
    fields = {"post": x["post_cnt"], "comment": x["comment_cnt"],
              "repost": x["repost_cnt"], "like": x["like_cnt"],
              "inter": x["interaction_total"],
              "flow": x["comment_cnt"] + x["repost_cnt"] + x["like_cnt"]}
    cur, left = t0, span
    while left > 1e-9:
        nxt = min(cur + timedelta(hours=left),
                  datetime(cur.year, cur.month, cur.day) + timedelta(days=1))
        h = (nxt - cur).total_seconds() / 3600.0
        o = _day[cur.strftime("%Y-%m-%d")]
        o["h"] += h
        for f, val in fields.items():
            o[f] += val * h / span
        o["vw"] += x["valence_mean"] * h
        o["vwn"] += x["negative_ratio"] * h
        o["online"] += x["online_cnt"] * h
        left -= h
        cur = nxt

daily = [{
    "date": k, "hours": round(v["h"], 1),
    "post_w": round(v["post"], 2), "comment": round(v["comment"], 2),
    "repost": round(v["repost"], 2), "like": round(v["like"], 2),
    "inter": round(v["inter"], 2),          # 存量（视野内累计赞+评），**不可加总**，仅审计
    "flow": round(v["flow"], 2),            # 流量（评论+转发+点赞动作数），可加总
    "valence": round(v["vw"] / v["h"], 4) if v["h"] else None,
    "neg": round(v["vwn"] / v["h"], 4) if v["h"] else None,
    "online": round(v["online"] / v["h"], 1) if v["h"] else None,
} for k, v in sorted(_day.items())]
print("\n每日分摊:", json.dumps(daily, ensure_ascii=False))

# ---------- 汇总 ----------
payload = {
    "meta": {
        # ★ 2026-10-21 第 29 轮：`rounds` 是**推演轮数**，不是快照文件数。
        #   老跑：快照 t0…t41 共 42 份，记录员轮号 0…41 也有 42 轮 ⇒ 两数相等，看不出来。
        #   新跑（t0 = 初始态）：快照 t0…t42 共 43 份，记录员轮号 1…42 仍是 42 轮 ⇒ 必须分开。
        #   `viz_alert` 拿 rounds 去配 series 的行数，写 43 会当场 IndexError。
        "rounds": len(g), "snapshots": NR, "t0": rounds[0]["window"][0], "end": last["window"][1],
        "posts": len(posts), "edges": sum(outdeg.values()), "agents": len(last["agents"]),
        "actions": len(acts), "action_dist": dict(act_cnt), "max_depth": max(chain_len.values()),
        "pool_never_seen": len(pool - seen), "pool_ever_seen": len(seen),
        "cross_n": sum(x["new_cross_channel_edges"] for x in g),
        "max_betw": max(between.values()),
        # ★ 全期真实互动动作数（评论+转发+点赞）。报告里所有「互动量」都应加总到这个数，
        #   而不是加到 Σ series[].inter（那是存量的 42 次快照求和，本跑 5224）。
        "inter_flow": sum(x["comment_cnt"] + x["repost_cnt"] + x["like_cnt"] for x in g),
    },
    "series": [{
        "round": x["round"], "window": x["window"],
        "valence": x["valence_mean"], "valence_online": x["valence_mean_online"],
        "neg": x["negative_ratio"], "neg_online": x["negative_ratio_online"],
        "arousal": x["arousal_mean"], "online": x["online_cnt"], "post": x["post_cnt"],
        "comment": x["comment_cnt"], "repost": x["repost_cnt"], "like": x["like_cnt"],
        "silent": x["silent_cnt"],
        "inter": x["interaction_total"],    # 存量，**不可加总**，仅审计（见 §10 注释）
        "flow": x["comment_cnt"] + x["repost_cnt"] + x["like_cnt"],   # 流量
        "newcross": x["new_cross_channel_edges"], "act": dict(by_round.get(x["round"], {})),
        "pool": sum(1 for p in posts if p["_round"] <= x["round"]),
    } for x in g],
    "cross_events": cross_events,
    "posts": [{
        "id": p["post_id"], "ch": p["channel"], "chs": p.get("channels", [p["channel"]]),
        "role": role_of(p["poster_agent_id"]),
        "t": p["publish_time"], "agent": p["poster_agent_id"], "name": p.get("poster_name"),
        "q": p.get("quoted_id"), "v": p.get("author_valence_at_post"),
        "a": p.get("author_arousal_at_post"),
        "like": p.get("like_cnt", 0), "cmt": p.get("comment_cnt", 0),
        "ls": p.get("like_cnt_sim", 0), "cs": p.get("comment_cnt_sim", 0),
        "rs": p.get("repost_cnt_sim", 0),
        # ★ 口径修正：原先取 p["interactions"]（== like+comment，不含转发），
        #   与报告一律采用的「互动量 = 评论+转发+点赞」不一致。改为三项相加。
        "inter": (p.get("like_cnt_sim", 0) + p.get("comment_cnt_sim", 0)
                  + p.get("repost_cnt_sim", 0)),
        "txt": (p.get("content") or "")[:220],
        "indeg": outdeg[p["post_id"]], "depth": chain_len[p["post_id"]],
        "betw": between[p["post_id"]], "eig": round(eig[p["post_id"]], 5),
        "r": p["_round"],
    } for p in posts],
    "nodes": nodes_rank,
    "daily": daily,
    "ch_stat": ch_stat, "ro_stat": ro_stat,
    "role_mat": role_mat, "ch_mat": ch_mat, "chans": chans, "roles": ROLES,
    # ★ 模块二改版新增：角色×渠道二维矩阵（含末轮人数）+ 逐轮钟点增益
    "rc_mat": rc_mat, "rc_cnt": rc_cnt, "gbar": gbar,
}
json.dump(payload, open(OUT, "w", encoding='utf-8'), ensure_ascii=False)
print("\npayload ->", OUT, os.path.getsize(OUT), "bytes")
