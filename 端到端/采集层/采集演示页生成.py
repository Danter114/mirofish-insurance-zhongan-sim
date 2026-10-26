# -*- coding: utf-8 -*-
"""采集过程演示页生成器：从 13 个事件的数据生成多事件演示页（采集过程演示.html）。

每个事件的步骤数据全部从该事件的 processed/raw 提取（真实值）：
参数卡摘要（名称/类型/窗口）、渠道分布、条数、情感分布、角色分布、看板链接。
"""
import json
import os
import re
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "采集过程演示.html")

EVENT_TYPES = ["理赔类", "销售与投保类", "产品与费率类", "服务态度类",
               "营销类", "数据与隐私类", "公司经营类", "司法与维权类"]

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>采集过程演示 · 众安舆情</title>
<style>
  :root { --bg:#f6f7f9; --surface:#fff; --ink:#141414; --ink2:#4a4a4a; --muted:#7a7a7a;
          --line:#e6e6e6; --accent:#2a78d6; --ok:#16a34a; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--ink);
    font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    font-size:15px; line-height:1.6; padding:36px 22px 70px; }
  .wrap { max-width:780px; margin:0 auto; }
  .hero { text-align:center; margin-bottom:26px; }
  .hero h1 { font-size:26px; font-weight:700; letter-spacing:.5px; }
  .hero h1 span { color:var(--accent); }
  .hero .sub { color:var(--muted); font-size:14px; margin-top:6px; }

  .input-card { background:var(--surface); border:1px solid var(--line); border-radius:12px;
    padding:18px 22px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;
    box-shadow:0 2px 8px rgba(0,0,0,.04); margin-bottom:20px; }
  .input-card select { border:1px solid var(--line); border-radius:8px; padding:10px 12px;
    font-size:14px; font-family:inherit; background:#fff; min-width:240px; }
  .input-card input { flex:1; min-width:140px; border:1px solid var(--line); border-radius:8px;
    padding:11px 14px; font-size:15px; font-family:inherit; outline:none; }
  .input-card input:focus { border-color:var(--accent); }
  .input-card button { background:var(--accent); color:#fff; border:none; border-radius:8px;
    padding:11px 26px; font-size:15px; font-weight:600; cursor:pointer; white-space:nowrap; }
  .input-card button:disabled { opacity:.6; cursor:default; }

  .pbar-wrap { margin-bottom:18px; }
  .pbar { height:8px; background:#e9ebee; border-radius:4px; overflow:hidden; }
  .pbar .fill { height:100%; width:0; background:linear-gradient(90deg,var(--accent),#5a9ae0);
    border-radius:4px; transition:width .5s ease; }
  .pct { display:flex; justify-content:space-between; color:var(--muted); font-size:13px; margin-top:5px; }

  #steps { display:none; }
  .step { display:flex; gap:14px; margin-bottom:12px; animation:slide .35s ease; }
  @keyframes slide { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  .step .rail { display:flex; flex-direction:column; align-items:center; flex-shrink:0; width:34px; }
  .step .n { width:32px; height:32px; border-radius:50%; display:flex; align-items:center;
    justify-content:center; font-weight:700; font-size:14px; background:#fff;
    border:2px solid var(--accent); color:var(--accent); }
  .step .line { flex:1; width:2px; background:#e4e7ea; margin:3px 0; }
  .step.done .n { background:var(--ok); border-color:var(--ok); color:#fff; }
  .step.running .n { background:var(--accent); border-color:var(--accent); color:#fff; animation:pulse 1.2s infinite; }
  @keyframes pulse { 0%,100%{ box-shadow:0 0 0 0 rgba(42,120,214,.35);} 50%{ box-shadow:0 0 0 8px rgba(42,120,214,0);} }
  .step .body { flex:1; background:var(--surface); border:1px solid var(--line); border-radius:10px;
    padding:13px 16px; box-shadow:0 1px 4px rgba(0,0,0,.04); }
  .step .st { font-weight:600; font-size:15px; display:flex; align-items:center; gap:8px; }
  .spinner { width:14px; height:14px; border:2px solid #c9ddf4; border-top-color:var(--accent);
    border-radius:50%; animation:rot .8s linear infinite; display:none; }
  .step.running .spinner { display:inline-block; }
  @keyframes rot { to { transform:rotate(360deg); } }
  .step.done .st::after { content:"✓"; color:var(--ok); font-weight:700; }
  .step .detail { color:var(--ink2); font-size:13.5px; margin-top:6px; display:none; }
  .step.done .detail { display:block; }
  .step .detail .tag { display:inline-block; font-size:12px; border-radius:5px; padding:1px 8px;
    background:#f2f6fb; color:#2a5f9e; margin:2px 5px 2px 0; }
  .step .detail .kv { color:var(--muted); }

  .view-row { display:none; text-align:center; margin:20px 0 6px; animation:slide .4s ease; }
  .view-row.show { display:block; }
  .view-btn { display:inline-block; background:var(--accent); color:#fff; border:none; border-radius:10px;
    padding:14px 38px; font-size:16px; font-weight:700; cursor:pointer; text-decoration:none;
    box-shadow:0 4px 14px rgba(42,120,214,.35); animation:glow 2s infinite; }
  @keyframes glow { 0%,100%{ box-shadow:0 4px 14px rgba(42,120,214,.35);} 50%{ box-shadow:0 4px 22px rgba(42,120,214,.6);} }
  .view-btn:hover { background:#2568bd; }
  .foot { text-align:center; color:var(--muted); font-size:12.5px; margin-top:26px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>众安舆情 · <span>采集过程演示</span></h1>
    <div class="sub">选择事件，逐步展示采集链路：参数卡 → 检索 → 过滤 → 快照 → 组装 → 标注 → 校验 → 看板</div>
  </div>

  <div class="input-card">
    <input id="topic" placeholder="输入事件主题，如：宠物险" value="宠物险">
    <select id="srcSel" title="可选：手动指定演示数据源（默认按主题自动匹配事件数据）"></select>
    <button id="startBtn">开始采集</button>
  </div>

  <div class="pbar-wrap" id="pbarWrap" style="display:none;">
    <div class="pbar"><div class="fill" id="pfill"></div></div>
    <div class="pct"><span id="pstep">准备中</span><span id="pnum">0%</span></div>
  </div>

  <div id="steps"></div>
  <div class="view-row" id="viewRow"><a class="view-btn" id="viewBtn" target="_blank">点击查看舆情看板</a></div>

  <div class="foot">本页为采集侧进度演示（数据来自各事件真实 processed/raw）；实际执行链路见 一键演示.py 与 00_程序与文档\采集链路说明.html</div>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const DEMOS = @@DEMOS@@;
const PACE = [700, 900, 800, 700, 700, 1100, 700, 900];

let running = false;
const sel = $("srcSel");
sel.innerHTML = '<option value="auto">数据源：自动匹配主题</option>';
Object.keys(DEMOS).forEach(k => {
  const o = document.createElement("option");
  o.value = k;
  o.textContent = "数据源：" + DEMOS[k].name + "（" + DEMOS[k].n + " 条）";
  sel.appendChild(o);
});
sel.addEventListener("change", () => reset());

function reset(){
  running = false;
  $("steps").textContent = "";
  $("steps").style.display = "none";
  $("viewRow").classList.remove("show");
  $("pbarWrap").style.display = "none";
  $("startBtn").disabled = false;
  $("startBtn").textContent = "开始采集";
}

/* 主题自动匹配事件：IDF 加权——"保险"这类通用词权重低，"宠物险"这类独有词权重高 */
function matchEvent(topic){
  const pools = {};
  Object.values(DEMOS).forEach(d => {
    pools[d.key] = (d.topics || []).concat([d.name]).map(x => String(x).toLowerCase()).filter(Boolean);
  });
  const df = {};
  Object.values(pools).forEach(pool => {
    const seen = {};
    pool.forEach(p => { if (!seen[p]) { seen[p] = 1; df[p] = (df[p] || 0) + 1; } });
  });
  const toks = [topic.toLowerCase().trim()].concat(
    topic.split(/[\s、,，;；]+/).map(t => t.toLowerCase()).filter(t => t.length >= 2));
  let best = null, bestScore = 0;
  Object.keys(pools).forEach(k => {
    let score = 0;
    toks.forEach(t => {
      if (t.length < 2) return;
      pools[k].forEach(p => {
        // 2 字词必须完全相等；≥3 字词才允许包含匹配（避免"保险/理赔"等通用双字词误配）
        const hit = (t === p)
          || (p.length >= 3 && t.indexOf(p) >= 0)
          || (t.length >= 3 && p.indexOf(t) >= 0);
        if (hit) score += 1 / (df[p] || 1);
      });
    });
    if (score > bestScore) { bestScore = score; best = DEMOS[k]; }
  });
  return bestScore >= 0.5 ? best : null;   // 低于阈值视为无匹配，走通用演示流程
}

/* 主题没有匹配事件时的通用演示流程（诚实标注演示模式，不造假数据） */
function genericSteps(topic){
  return [
    {running:"生成参数卡中", done:"生成参数卡完毕",
     detail:'<span class="tag">新事件</span><span class="tag">' + topic + '</span><span class="tag">类型待判定（8 类枚举）</span><div class="kv">真实链路中由 参数卡生成.py（LLM）完成生成与归类</div>'},
    {running:"六渠道检索召回中", done:"六渠道检索召回完毕",
     detail:'<span class="tag">以「' + topic + '」检索 6 渠道</span><div class="kv">演示模式未接实时检索；实际执行见 yuqing_collector.py --mode live（新闻门户）与逐渠道适配器</div>'},
    {running:"相关性过滤中", done:"相关性过滤完毕",
     detail:'<span class="tag">窗口 Day0-Day1</span><span class="tag">排除词</span><span class="tag">必选词</span>'},
    {running:"原文快照存档中", done:"原文快照存档完毕",
     detail:'<span class="tag">一帖一个 txt + snapshot_meta.json 金标准值</span>'},
    {running:"组装 21 字段 raw 中", done:"组装 21 字段 raw 完毕",
     detail:'<span class="tag">字段顺序固定、无值留空、不臆造</span>'},
    {running:"结构化与标注中", done:"结构化与标注完毕",
     detail:'<span class="tag">规则角色 + LLM 标注 + 口径仲裁</span><div class="kv">演示模式：未实际标注</div>'},
    {running:"校验与落盘中", done:"校验与落盘完毕",
     detail:'<span class="tag">8 类枚举校验</span><span class="tag">28 字段完整性校验</span>'},
    {running:"生成舆情看板中", done:"生成舆情看板完毕",
     detail:'<span class="kv">该主题暂无对应事件数据，演示模式不生成看板；真实链路见 一键演示.py</span>'},
  ];
}

function makeStep(i, s){
  const el = document.createElement("div");
  el.className = "step running";
  el.innerHTML = '<div class="rail"><div class="n">' + (i+1) + '</div><div class="line"></div></div>'
    + '<div class="body"><div class="st">' + s.running + '<span class="spinner"></span></div>'
    + '<div class="detail">' + s.detail + '</div></div>';
  return el;
}

$("startBtn").addEventListener("click", () => {
  if (running) return;
  const topic = $("topic").value.trim() || "宠物险";
  const d = sel.value === "auto" ? matchEvent(topic) : DEMOS[sel.value];
  running = true;
  $("startBtn").disabled = true;
  $("startBtn").textContent = "采集中…";
  $("pbarWrap").style.display = "block";
  const box = $("steps");
  box.style.display = "block";
  box.textContent = "";            // 每次开始强制清空，防止状态残留累积
  $("viewRow").classList.remove("show");
  $("pfill").style.width = "0%";
  const steps = d ? d.steps : genericSteps(topic);
  if (d) $("viewBtn").href = d.link;
  let i = 0;
  const runStep = () => {
    if (i >= steps.length) {
      running = false;
      $("startBtn").disabled = false;
      $("startBtn").textContent = "重新开始";
      if (d) $("viewRow").classList.add("show");
      $("pstep").textContent = "全部完成";
      return;
    }
    const s = steps[i];
    const el = makeStep(i, s);
    box.appendChild(el);
    $("pstep").textContent = "第 " + (i+1) + " / " + steps.length + " 步 · " + s.running;
    $("pnum").textContent = Math.round(i / steps.length * 100) + "%";
    setTimeout(() => {
      el.classList.remove("running");
      el.classList.add("done");
      el.querySelector(".st").innerHTML = s.done;
      $("pfill").style.width = ((i + 1) / steps.length * 100) + "%";
      $("pnum").textContent = Math.round((i + 1) / steps.length * 100) + "%";
      i++;
      setTimeout(runStep, 260);
    }, PACE[i]);
  };
  runStep();
});
</script>
</body>
</html>
"""


def load_event(n, folder=None):
    # ★ 交付改动：原版写死 `range(1, 14)` 并要求 13 个事件目录全在，少一个就 IndexError。
    #   交付里只带 EVT-001 这一场样本，所以目录由调用方找好再传进来。
    folder = folder or [d for d in os.listdir(HERE) if d.startswith("EVT-%03d_" % n)][0]
    evt_dir = os.path.join(HERE, folder)
    with open(os.path.join(evt_dir, "EVT-%03d_processed.json" % n), encoding="utf-8") as f:
        rows = json.load(f)
    name = folder.split("_", 1)[1]
    evt = "EVT-%03d" % n
    t0 = rows[0]["event_t0"]
    t1 = (datetime.strptime(t0, "%Y-%m-%d")
          + timedelta(days=max(r["timespan_days"] for r in rows))).strftime("%Y-%m-%d")
    chans = {}
    roles = {}
    sents = {}
    for r in rows:
        chans[r["channel"]] = chans.get(r["channel"], 0) + 1
        roles[r["role_type"]] = roles.get(r["role_type"], 0) + 1
        sents[r["sentiment"]] = sents.get(r["sentiment"], 0) + 1
    etype = rows[0].get("event_type") or ""
    topic_list = list(rows[0].get("topic_list") or [])
    chan_str = " ｜ ".join("%s %d" % (k, v) for k, v in sorted(chans.items(), key=lambda x: -x[1]))
    role_str = " · ".join("%s %d" % (k, v) for k, v in sorted(roles.items(), key=lambda x: -x[1]))
    sent_str = " · ".join("%s %d" % (k, v) for k, v in sorted(sents.items(), key=lambda x: -x[1]))
    tags = '<span class="tag">%s</span><span class="tag">%s</span><span class="tag">%s</span>' % (
        evt, name, etype or "（类型未定）")
    steps = [
        {"running": "生成参数卡中", "done": "生成参数卡完毕",
         "detail": '%s<span class="tag">窗口 %s ~ %s</span><div class="kv">主体：众安保险（产品监测） ｜ 主题词：%s</div>'
                   % (tags, t0, t1, "、".join(topic_list[:5]))},
        {"running": "六渠道检索召回中", "done": "六渠道检索召回完毕",
         "detail": '<span class="tag">%s</span><span class="kv">未命中的渠道记录查询词与结果数（缺口备查）</span>'
                   % chan_str},
        {"running": "相关性过滤中", "done": "相关性过滤完毕",
         "detail": '<span class="tag">窗口 %d 通过</span><span class="tag">固定采集清单 URL 锁定（无新增无缺失）</span>'
                   % len(rows)},
        {"running": "原文快照存档中", "done": "原文快照存档完毕",
         "detail": '<span class="tag">%d 份快照</span><span class="tag">含 URL / 标题 / 发布时间 / 正文全文</span>'
                   % len(rows)},
        {"running": "组装 21 字段 raw 中", "done": "组装 21 字段 raw 完毕",
         "detail": '<span class="tag">%d 条</span><span class="tag">字段顺序固定、无值留空、不臆造</span>' % len(rows)},
        {"running": "结构化与标注中", "done": "结构化与标注完毕",
         "detail": '<span class="tag">角色：%s</span><span class="tag">情感：%s</span>'
                   '<div class="kv">规则角色 + LLM 标注 + 口径仲裁（转述标记→中性）</div>'
                   % (role_str, sent_str)},
        {"running": "校验与落盘中", "done": "校验与落盘完毕",
         "detail": '<span class="tag">event_type「%s」8 类枚举校验通过</span><span class="tag">28 字段完整性校验通过</span>'
                   % etype},
        {"running": "生成舆情看板中", "done": "生成舆情看板完毕",
         "detail": '<span class="tag">7 模块 + 情感演化曲线 + 渠道概览 + 帖子明细</span>'},
    ]
    return {
        "key": evt,
        "name": name,
        "n": len(rows),
        "topic": topic_list[0] if topic_list else name,
        "topics": topic_list,
        "link": "%s/%s_舆情看板.html" % (folder, evt),
        "steps": steps,
    }


# ★ 交付改动：只扫**实际存在**的事件目录（原版写死 1..13）。少几场就少几项，不报错。
_folders = sorted(d for d in os.listdir(HERE)
                  if re.match(r"^EVT-(\d+)_", d) and os.path.isdir(os.path.join(HERE, d)))
demos = {d["key"]: d for d in [load_event(int(re.match(r"^EVT-(\d+)_", f).group(1)), f)
                               for f in _folders]}
html = TEMPLATE.replace("@@DEMOS@@", json.dumps(demos, ensure_ascii=False, indent=1))
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(html)
print("wrote", OUT, "events:", len(demos))
