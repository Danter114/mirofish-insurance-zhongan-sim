# -*- coding: utf-8 -*-
"""通用舆情看板生成器：EVT-001~012 processed.json + 文案包 -> 单文件 HTML 看板。

用法：python evt_dash_builder.py <事件目录> <文案包json> <输出html>
"""
import json
import os
import re
import sys

CANON = ["微博", "抖音", "小红书", "新闻门户", "黑猫投诉", "监管通报"]


def md_bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def est_w(s, fs):
    """估算文本像素宽度：CJK 按全宽，ASCII 按 0.58 倍宽。"""
    return sum(fs if ord(c) > 127 else fs * 0.58 for c in s)


def fit_line(s, fs, maxw):
    """按框宽截断文本，超长加省略号；返回 (文本, 是否被截断)。"""
    if est_w(s, fs) <= maxw:
        return s, False
    out = ""
    for c in s:
        if est_w(out + c, fs) > maxw - fs * 1.6:
            break
        out += c
    return out.rstrip() + "…", True


# ---------- 传播链条 SVG ----------
def chain_svg(pack):
    srcs = pack["chain"].get("sources") or []
    origin = pack["chain"].get("origin")
    spread = (pack["chain"].get("spread") or [])[:8]
    weibo = pack["chain"].get("weibo") or []

    def wrap_lines(s, fs, maxw, max_lines):
        """按框宽把文本折成多行，超出 max_lines 的最后一行加省略号。"""
        lines = []
        rem = s
        while rem and len(lines) < max_lines:
            ln, trunc = fit_line(rem, fs, maxw)
            lines.append(ln)
            if not trunc:
                break
            rem = rem[len(ln.rstrip("…")):].lstrip()
        return lines

    def box_h(w, label, subs):
        maxw = w - 28
        nl = len(wrap_lines(label, 14, maxw, 2))
        ns = 0
        for sub in (subs or [])[:4]:
            ns += len(wrap_lines(sub, 12.5, maxw, 2))
        ns = min(ns, 4)
        return 24 + 19 * (nl + ns) + 10

    def box(x, y, w, day0, dashed, label, subs):
        fill = "#2a78d6" if day0 else ("#ffffff" if dashed else "#f4f4f4")
        stroke = "#8a8a8a" if dashed else ("#2a78d6" if day0 else "#bdbdbd")
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        tcolor = "#ffffff" if day0 else "#111111"
        scolor = "#d9d9d9" if day0 else "#3f3f3f"
        maxw = w - 28
        h = box_h(w, label, subs)
        lab_lines = wrap_lines(label, 14, maxw, 2)
        sub_lines = []
        for sub in (subs or [])[:4]:
            sub_lines.extend(wrap_lines(sub, 12.5, maxw, 2))
        sub_lines = sub_lines[:4]
        out = ['<rect x="%d" y="%d" width="%d" height="%d" rx="5" fill="%s" stroke="%s"%s/>'
               % (x, y, w, h, fill, stroke, dash)]
        ty = y + 24
        for i, ln in enumerate(lab_lines):
            out.append('<text x="%d" y="%d" fill="%s" font-size="14" font-weight="600">%s</text>'
                       % (x + 14, ty + 19 * i, tcolor, esc(ln)))
        ty += 19 * len(lab_lines)
        for ln in sub_lines:
            out.append('<text x="%d" y="%d" fill="%s" font-size="12.5">%s</text>'
                       % (x + 14, ty, scolor, esc(ln)))
            ty += 19
        if any(line.endswith("…") for line in lab_lines + sub_lines):
            full = label if not sub_lines else (label + "｜" + "；".join(subs or []))
            out[1] = out[1].replace("</text>", "<title>%s</title></text>" % esc(full))
        return "\n".join(out), y, y + h

    # 先排 spread 列，确定中线与画布高度
    spread_htmls, spread_centers = [], []
    sy = 40
    for s in spread:
        html, ytop, ybot = box(560, sy, 224, bool(s.get("day0")), False,
                               s["label"], [s.get("sub") or ""])
        spread_htmls.append(html)
        spread_centers.append((ytop + ybot) / 2)
        sy = ybot + 14
    mid = (spread_centers[0] + spread_centers[-1]) / 2 if spread_centers else 260

    # weibo 列（先排，用于算画布高度）
    weibo_boxes = []
    wy = 130
    for w in weibo:
        html, ytop, ybot = box(868, wy, 252, bool(w.get("day0")), bool(w.get("dashed")),
                               w["label"], w.get("subs") or [])
        weibo_boxes.append((html, ytop, ybot))
        wy = ybot + 16

    H = int(max(520, sy + 30, wy + 30))
    parts = ['<svg viewBox="0 0 1140 %d" role="img" aria-label="传播链条示意图">' % H]

    # sources（1~2 个，围绕中线垂直居中）
    last_cy = mid
    if srcs:
        heights = [box_h(190, s["label"], s.get("subs") or []) for s in srcs]
        total = sum(heights) + 12 * (len(srcs) - 1)
        yy = int(mid - total / 2)
        for i, s in enumerate(srcs):
            html, ytop, ybot = box(14, yy, 190, bool(s.get("day0")), False,
                                   s["label"], s.get("subs") or [])
            parts.append(html)
            last_cy = (ytop + ybot) / 2
            yy = ybot + 12

    # origin
    ocy = mid
    if origin:
        html, ytop, ybot = box(272, int(mid - box_h(216, origin["label"], origin.get("subs") or []) / 2),
                               216, bool(origin.get("day0")), False,
                               origin["label"], origin.get("subs") or [])
        parts.append(html)
        ocy = (ytop + ybot) / 2
        if srcs:
            parts.append('<line x1="204" y1="%d" x2="268" y2="%d" stroke="#9a9a9a" stroke-width="2.5"/>'
                         % (int(last_cy), int(ocy)))
            parts.append('<polygon points="268,%d 278,%d 268,%d" fill="#9a9a9a"/>'
                         % (int(ocy) - 6, int(ocy), int(ocy) + 6))

    # spread（含 origin→spread 扇出箭头）
    for i, html in enumerate(spread_htmls):
        parts.append(html)
        if origin:
            parts.append('<line x1="488" y1="%d" x2="556" y2="%d" stroke="#9a9a9a" stroke-width="2.5"/>'
                         % (int(ocy), int(spread_centers[i])))

    # weibo（含承接箭头）
    for i, (html, ytop, ybot) in enumerate(weibo_boxes):
        parts.append(html)
        w = weibo[i]
        cy = (ytop + ybot) / 2
        fs = w.get("from_spread")
        fw = w.get("from_weibo")
        if isinstance(fs, int) and 0 <= fs < len(spread):
            parts.append('<line x1="784" y1="%d" x2="864" y2="%d" stroke="#9a9a9a" stroke-width="2.5"/>'
                         % (int(spread_centers[fs]), int(cy)))
            parts.append('<polygon points="864,%d 874,%d 864,%d" fill="#9a9a9a"/>'
                         % (int(cy) - 5, int(cy), int(cy) + 5))
        if isinstance(fw, int) and 0 <= fw < i:
            py2 = weibo_boxes[fw][2]
            parts.append('<line x1="994" y1="%d" x2="994" y2="%d" stroke="#9a9a9a" stroke-width="2.5"/>'
                         % (int(py2), int(ytop - 6)))
            parts.append('<polygon points="988,%d 994,%d 1000,%d" fill="#9a9a9a"/>'
                         % (int(ytop - 6), int(ytop), int(ytop - 6)))
    parts.append('</svg>')
    return "\n".join(parts)


CSS = """
  :root {
    --bg: #fafafa;
    --surface: #ffffff;
    --ink: #111111;
    --ink-2: #3f3f3f;
    --muted: #6e6e6e;
    --line: #e2e2e2;
    --baseline: #b0b0b0;
    --mark: #1f1f1f;
    --mark-soft: #efefef;
    --topic: #141414;
    --entity: #6b6b6b;
    --accent: #2a78d6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 14px; line-height: 1.6; padding: 28px 24px 60px;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  .head { margin-bottom: 18px; }
  .head h1 { font-size: 22px; font-weight: 600; letter-spacing: .5px; padding-left: 12px; border-left: 4px solid var(--accent); }
  .head .sub { color: var(--ink-2); font-size: 13px; margin-top: 4px; }
  .head .sub b { color: var(--ink); font-weight: 600; }
  .filters { display: flex; align-items: center; gap: 8px; margin: 14px 0 18px; flex-wrap: wrap; }
  .filters .flabel { color: var(--muted); font-size: 12px; margin-right: 4px; }
  .fchip {
    border: 1px solid var(--line); background: var(--surface); color: var(--ink-2);
    border-radius: 999px; padding: 5px 14px; font-size: 13px;
  }
  .fchip.on { background: var(--accent); color: #ffffff; border-color: var(--accent); }
  .card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 8px; padding: 20px 24px; margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }
  .card h2 { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
  .card h2::before {
    content: ""; display: inline-block; width: 8px; height: 8px;
    border-radius: 2px; background: var(--accent); margin-right: 8px; vertical-align: 1px;
  }
  .card .note { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
  .kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-top: 14px; }
  @media (max-width: 860px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
  .kpi { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: #ffffff; }
  .kpi .v { font-size: 24px; font-weight: 600; font-variant-numeric: proportional-nums; }
  .kpi .l { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .tl { position: relative; padding-left: 18px; }
  .tl::before { content: ""; position: absolute; left: 5px; top: 6px; bottom: 6px; width: 1px; background: var(--baseline); }
  .tl-day { color: var(--ink-2); font-weight: 600; font-size: 13px; margin: 14px 0 6px; }
  .tl-day:first-child { margin-top: 0; }
  .tl-item { position: relative; padding: 7px 0 7px 22px; }
  .tl-item::before {
    content: ""; position: absolute; left: -16px; top: 13px;
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); box-shadow: 0 0 0 2px var(--surface);
  }
  .tl-item.d1::before { background: #8f8f8f; }
  .tl-item .t { font-weight: 600; font-variant-numeric: tabular-nums; margin-right: 8px; }
  .tl-item .plat { color: var(--ink-2); }
  .tl-item .ttl { color: var(--ink); margin: 1px 0 2px; }
  .tag {
    display: inline-block; font-size: 11px; border-radius: 4px; padding: 0 6px;
    border: 1px solid var(--line); color: var(--ink-2); background: #ffffff; margin-right: 6px;
  }
  .tag.key { background: var(--accent); color: #fff; border-color: var(--accent); }
  .tag.warn { border-style: dashed; }
  .brow { display: flex; align-items: center; margin: 10px 0; gap: 10px; }
  .brow .bl { width: 84px; color: var(--ink-2); font-size: 13px; text-align: right; flex-shrink: 0; }
  .brow .track { flex: 1; height: 18px; background: var(--mark-soft); border-radius: 4px; position: relative; }
  .brow .fill { height: 18px; background: var(--mark); border-radius: 4px 0 0 4px; min-width: 0; }
  .brow .val { width: 76px; font-size: 13px; color: var(--ink); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .brow .val .pct { color: var(--muted); font-size: 12px; margin-left: 4px; }
  .seg { display: flex; height: 26px; border-radius: 4px; overflow: hidden; border: 1px solid var(--line); }
  .seg div { display: flex; align-items: center; justify-content: center; font-size: 12px; color: #fff; }
  .seg .zero { background: #f3f3f3; color: var(--muted); }
  .seg .neg { background: var(--mark); }
  .daychart { margin-top: 10px; }
  .drow { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
  .drow .dl { width: 60px; color: var(--ink-2); font-size: 13px; flex-shrink: 0; }
  .drow .track { flex: 1; height: 18px; background: var(--mark-soft); border-radius: 4px; position: relative; }
  .drow .fill { height: 18px; background: var(--mark); border-radius: 4px 0 0 4px; }
  .drow .val { width: 96px; font-size: 13px; flex-shrink: 0; }
  .cloud { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: baseline; padding: 8px 0; }
  .cloud span { cursor: default; line-height: 1.25; }
  .cloud .topic { color: var(--topic); font-weight: 600; }
  .cloud .entity { color: var(--entity); }
  .cloud-legend { display: flex; gap: 18px; color: var(--muted); font-size: 12px; margin-top: 8px; border-top: 1px solid var(--line); padding-top: 10px; }
  .cloud-legend .sw { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
  .cloud-legend .sw.t { background: var(--topic); }
  .cloud-legend .sw.e { background: var(--entity); }
  .op-group { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
  .op-group h3 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
  .op-group .cnt { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
  .op-group ul { list-style: none; }
  .op-group li { font-size: 13px; color: var(--ink-2); padding: 2px 0 2px 14px; position: relative; }
  .op-group li::before { content: ""; position: absolute; left: 0; top: 10px; width: 6px; height: 6px; border-radius: 50%; background: var(--baseline); }
  .points { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; }
  .point { flex: 1 1 240px; border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; font-size: 13px; background: #ffffff; }
  .point b { display: block; margin-bottom: 4px; }
  .point .cov { color: var(--muted); font-size: 12px; }
  .chain svg { width: 100%; height: auto; display: block; }
  .chain .legend { display: flex; gap: 18px; color: var(--muted); font-size: 12px; margin-top: 10px; flex-wrap: wrap; }
  .chain .legend span::before {
    content: ""; display: inline-block; width: 10px; height: 10px;
    border-radius: 3px; margin-right: 5px; vertical-align: middle;
  }
  .chain .legend .ld0::before { background: var(--accent); }
  .chain .legend .ld1::before { background: #f4f4f4; border: 1px solid var(--baseline); }
  .chain .legend .ldx::before { background: #fff; border: 1px dashed var(--muted); }
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; white-space: nowrap; }
  td.num, th.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
  td a { color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--baseline); }
  td a:hover { color: #1c5cab; border-color: #1c5cab; }
  #tip {
    position: fixed; display: none; background: #ffffff; border: 1px solid var(--baseline);
    border-radius: 6px; padding: 6px 10px; font-size: 12px; color: var(--ink);
    box-shadow: 0 2px 8px rgba(0,0,0,.08); pointer-events: none; z-index: 10; max-width: 260px;
  }
  #tip .tv { font-weight: 600; }
  .foot { color: var(--muted); font-size: 12px; margin-top: 20px; line-height: 1.8; }

  /* —— 字号整体加大（v2 修订） —— */
  body { font-size: 15px; }
  .head h1 { font-size: 24px; }
  .head .sub { font-size: 14px; }
  .filters .flabel { font-size: 13px; }
  .fchip { font-size: 14px; }
  .card h2 { font-size: 16px; }
  .card .note { font-size: 13px; }
  .kpi .v { font-size: 26px; }
  .kpi .l { font-size: 13px; }
  .tl-day { font-size: 14px; }
  .tag { font-size: 12px; }
  .brow .bl, .brow .val { font-size: 14px; }
  .drow .dl, .drow .val { font-size: 14px; }
  .seg div { font-size: 13px; }
  .cloud-legend { font-size: 13px; }
  .op-group h3 { font-size: 15px; }
  .op-group li { font-size: 14px; }
  .op-group .cnt { font-size: 13px; }
  .point { font-size: 14px; }
  .point .cov { font-size: 13px; }
  .chain .legend { font-size: 13px; }
  table { font-size: 14px; }
  th { font-size: 13px; }
  #tip { font-size: 13px; }
  .foot { font-size: 13px; }

  /* —— 情感演化曲线 —— */
  .trend-legend { display: flex; gap: 16px; align-items: center; color: var(--muted); font-size: 13px; margin-bottom: 6px; flex-wrap: wrap; }
  .trend-legend .lk { display: inline-block; width: 16px; height: 2px; margin-right: 5px; vertical-align: middle; border-radius: 1px; }
  .trend svg { width: 100%; height: auto; display: block; }
"""

JS_TEMPLATE = r"""
"use strict";
const POSTS = @@POSTS@@;
POSTS.sort(function(a,b){ return a.time < b.time ? -1 : 1; });
const CHANNELS = @@CHANNELS@@;
const tip = document.getElementById("tip");
function showTip(ev, strong, rest){
  tip.textContent = "";
  const s = document.createElement("div"); s.className = "tv"; s.textContent = strong;
  const r = document.createElement("div"); r.textContent = rest;
  tip.appendChild(s); tip.appendChild(r);
  tip.style.display = "block";
  const x = Math.min(ev.clientX + 14, window.innerWidth - 280);
  const y = ev.clientY + 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip(){ tip.style.display = "none"; }
const chanCount = function(list){
  const c = {}; CHANNELS.forEach(function(ch){ c[ch] = 0; });
  list.forEach(function(p){ c[p.channel] = (c[p.channel]||0) + 1; });
  return c;
};
function renderOverview(){
  const el = document.getElementById("chan-overview");
  const c = chanCount(POSTS);
  CHANNELS.forEach(function(ch){
    const s = document.createElement("span");
    s.className = "fchip on";
    s.textContent = ch + " " + c[ch] + " 条";
    el.appendChild(s);
  });
  const detail = document.getElementById("chan-detail");
  CHANNELS.forEach(function(ch){
    const d = document.createElement("div");
    const items = [];
    POSTS.forEach(function(p){
      if (p.channel !== ch) return;
      const v = (ch === "微博") ? p.author : p.platform;
      if (v && items.indexOf(v) < 0) items.push(v);
    });
    d.textContent = ch + "：" + items.join("、");
    detail.appendChild(d);
  });
  const mx = document.createElement("div");
  mx.style.marginTop = "4px";
  const parts = [];
  CHANNELS.forEach(function(ch){
    const posts = POSTS.filter(function(p){ return p.channel === ch; });
    const roles = {};
    posts.forEach(function(p){ roles[p.role] = (roles[p.role] || 0) + 1; });
    Object.keys(roles).forEach(function(r){
      parts.push(ch + " " + posts.length + " → " + r + " " + roles[r]);
    });
  });
  mx.textContent = "渠道 × 角色：" + parts.join("；");
  detail.appendChild(mx);
}
function renderChans(list){
  const c = chanCount(list);
  const max = Math.max.apply(null, CHANNELS.map(function(ch){ return c[ch]; })) || 1;
  const el = document.getElementById("chan-bars");
  el.textContent = "";
  CHANNELS.forEach(function(ch){
    const n = c[ch];
    const row = document.createElement("div"); row.className = "brow";
    const lab = document.createElement("div"); lab.className = "bl"; lab.textContent = ch;
    const track = document.createElement("div"); track.className = "track";
    const fill = document.createElement("div"); fill.className = "fill";
    fill.style.width = (n / max * 100) + "%";
    track.appendChild(fill);
    const val = document.createElement("div"); val.className = "val";
    val.textContent = n + " 条";
    const pct = document.createElement("span"); pct.className = "pct";
    pct.textContent = list.length ? Math.round(n / list.length * 100) + "%" : "0%";
    val.appendChild(pct);
    row.appendChild(lab); row.appendChild(track); row.appendChild(val);
    row.addEventListener("pointermove", function(ev){
      showTip(ev, ch + " · " + n + " 条", "占全部帖子 " + (list.length ? Math.round(n/list.length*100) : 0) + "%");
    });
    row.addEventListener("pointerleave", hideTip);
    el.appendChild(row);
  });
}
function renderTrend(list){
  const el = document.getElementById("trend-box");
  el.textContent = "";
  const times = list.map(function(p){ return new Date(p.time.replace(" ", "T")); })
                   .filter(function(t){ return !isNaN(t); });
  if (!times.length) {
    el.appendChild(document.createTextNode("暂无数据"));
    return;
  }
  const t0 = new Date(Math.min.apply(null, times));
  const t1 = new Date(Math.max.apply(null, times));
  const BUCKET = 6 * 3600 * 1000;
  const n = Math.max(1, Math.floor((t1 - t0) / BUCKET) + 1);
  const counts = [];
  for (let i = 0; i < n; i++) counts.push({neg:0, neu:0, pos:0, label:""});
  const day0 = new Date(t0.getFullYear(), t0.getMonth(), t0.getDate());
  counts.forEach(function(c, i){
    const b0 = new Date(t0.getTime() + i * BUCKET);
    const day = Math.floor((b0 - day0) / 86400000);
    c.label = "D" + day + "·" + b0.getHours() + "-" + Math.min(b0.getHours() + 6, 24) + "h";
  });
  list.forEach(function(p){
    const t = new Date(p.time.replace(" ", "T"));
    if (isNaN(t)) return;
    let i = Math.floor((t - t0) / BUCKET);
    if (i < 0) i = 0; if (i >= n) i = n - 1;
    if (p.sentiment === "负面") counts[i].neg++;
    else if (p.sentiment === "正面") counts[i].pos++;
    else counts[i].neu++;
  });
  const maxC = Math.max.apply(null, counts.map(function(c){ return c.neg + c.neu + c.pos; })) || 1;
  const W = 460, H = 170, PADL = 26, PADR = 8, PADT = 12, PADB = 26;
  const px = function(i){ return n === 1 ? PADL + (W - PADL - PADR) / 2 : PADL + i * (W - PADL - PADR) / (n - 1); };
  const py = function(v){ return PADT + (H - PADT - PADB) * (1 - v / maxC); };
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  const gy = 4;
  for (let g = 0; g <= gy; g++) {
    const v = Math.round(maxC * g / gy);
    const y = py(v);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", PADL); line.setAttribute("x2", W - PADR);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", "#e8e8e8"); line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", PADL - 6); t.setAttribute("y", y + 3);
    t.setAttribute("text-anchor", "end"); t.setAttribute("font-size", "10");
    t.setAttribute("fill", "#8a8a8a"); t.textContent = v;
    svg.appendChild(t);
  }
  counts.forEach(function(c, i){
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", px(i)); t.setAttribute("y", H - 8);
    t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "10");
    t.setAttribute("fill", "#8a8a8a"); t.textContent = c.label;
    svg.appendChild(t);
  });
  /* 悬停热区（每桶一条竖直透明带，先画，线条压在上面） */
  counts.forEach(function(c, i){
    const hw = n === 1 ? 30 : Math.max(18, (W - PADL - PADR) / (n - 1) / 2);
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", px(i) - hw); r.setAttribute("y", PADT);
    r.setAttribute("width", hw * 2); r.setAttribute("height", H - PADT - PADB);
    r.setAttribute("fill", "transparent");
    r.addEventListener("pointermove", function(ev){
      showTip(ev, c.label + " · 合计 " + (c.neg + c.neu + c.pos) + " 条",
              "负面 " + c.neg + " · 中性 " + c.neu + " · 正面 " + c.pos);
    });
    r.addEventListener("pointerleave", hideTip);
    svg.appendChild(r);
  });
  const series = [["neg", "#1f1f1f"], ["neu", "#9a9a9a"], ["pos", "#cfcfcf"]];
  series.forEach(function(s){
    const key = s[0], color = s[1];
    const pts = counts.map(function(c, i){ return px(i) + "," + py(c[key]); });
    const pl = document.createElementNS(NS, "polyline");
    pl.setAttribute("points", pts.join(" "));
    pl.setAttribute("fill", "none"); pl.setAttribute("stroke", color);
    pl.setAttribute("stroke-width", "2"); pl.setAttribute("stroke-linejoin", "round");
    svg.appendChild(pl);
    counts.forEach(function(c, i){
      const mk = document.createElementNS(NS, "circle");
      mk.setAttribute("cx", px(i)); mk.setAttribute("cy", py(c[key]));
      mk.setAttribute("r", c[key] ? 4 : 0);
      mk.setAttribute("fill", color); mk.setAttribute("stroke", "#ffffff");
      mk.setAttribute("stroke-width", "2");
      svg.appendChild(mk);
    });
  });
  el.appendChild(svg);
}
function normEnt(e){ return e.replace(/（/g,"(").replace(/）/g,")"); }
function renderCloud(list){
  const el = document.getElementById("cloud");
  el.textContent = "";
  const tf = {}, ef = {};
  list.forEach(function(p){
    (p.topics||[]).forEach(function(t){ tf[t] = (tf[t]||0) + 1; });
    (p.ents||[]).forEach(function(e){
      const k = normEnt(e); ef[k] = (ef[k]||0) + 1;
    });
  });
  const words = [];
  Object.keys(tf).forEach(function(k){ words.push({w:k, n:tf[k], kind:"topic"}); });
  Object.keys(ef).forEach(function(k){
    if (tf[k]) return;
    words.push({w:k, n:ef[k], kind:"entity"});
  });
  words.sort(function(a,b){ return b.n - a.n || (a.kind === "topic" ? -1 : 1); });
  const maxN = Math.max.apply(null, words.map(function(x){ return x.n; })) || 1;
  words.forEach(function(x){
    const s = document.createElement("span");
    s.className = x.kind;
    s.textContent = x.w;
    s.style.fontSize = (15 + (x.n - 1) / (maxN - 1 || 1) * 19) + "px";
    s.addEventListener("pointermove", function(ev){
      showTip(ev, x.w + " · " + x.n + " 条", x.kind === "topic" ? "主题词（topic_hit）" : "实体（extract_entity）");
    });
    s.addEventListener("pointerleave", hideTip);
    el.appendChild(s);
  });
}
function renderTable(list){
  const tb = document.querySelector("#post-table tbody");
  tb.textContent = "";
  list.forEach(function(p){
    const tr = document.createElement("tr");
    [p.id, p.time, "Day" + p.day, p.channel, p.platform, p.author, p.role, p.sentiment, p.title].forEach(function(t, i){
      const td = document.createElement("td");
      if (i === 1) td.className = "num";
      td.textContent = t;
      tr.appendChild(td);
    });
    const td = document.createElement("td");
    if (p.url) {
      const a = document.createElement("a");
      a.href = p.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = "原文";
      td.appendChild(a);
    } else {
      td.textContent = "—";
    }
    tr.appendChild(td); tb.appendChild(tr);
  });
}
const seg = document.getElementById("seg-neg");
const negTotal = POSTS.filter(function(p){ return p.sentiment === "负面"; }).length;
seg.style.width = Math.round(negTotal / POSTS.length * 100) + "%";
seg.textContent = "负面 " + Math.round(negTotal / POSTS.length * 100) + "%";
renderOverview();
renderChans(POSTS);
renderTrend(POSTS);
renderCloud(POSTS);
renderTable(POSTS);
"""


def build(event_dir, pack_path, out_html):
    with open(pack_path, encoding="utf-8") as f:
        pack = json.load(f)
    proc_files = [x for x in os.listdir(event_dir) if x.endswith("_processed.json")]
    if not proc_files:
        raise SystemExit("目录里找不到 *_processed.json")
    with open(os.path.join(event_dir, proc_files[0]), encoding="utf-8") as f:
        rows = json.load(f)
    rows.sort(key=lambda r: r["publish_time"])

    m = re.match(r"^EVT?-?(\d+)_", proc_files[0])
    evt_id = "EVT-%03d" % int(m.group(1)) if m else "EVT"
    folder_base = os.path.basename(event_dir)
    if folder_base.startswith("EVT-") and "_" in folder_base:
        evt_name = folder_base.split("_", 1)[1]
    else:
        evt_name = "事件"
    t0 = rows[0]["event_t0"]
    days = sorted({r["timespan_days"] for r in rows})
    window_end_day = days[-1]
    from datetime import datetime, timedelta
    t0_date = datetime.strptime(t0, "%Y-%m-%d")
    end_date = (t0_date + timedelta(days=window_end_day)).strftime("%Y-%m-%d")

    # KPI
    n = len(rows)
    neg = sum(1 for r in rows if r["sentiment"] == "负面")
    pos = sum(1 for r in rows if r["sentiment"] == "正面")
    neu = sum(1 for r in rows if r["sentiment"] == "中性")
    dom = max([("负面", neg), ("中性", neu), ("正面", pos)], key=lambda x: x[1])
    roles = {}
    for r in rows:
        roles[r["role_type"]] = roles.get(r["role_type"], 0) + 1
    top_roles = sorted(roles.items(), key=lambda x: -x[1])[:2]
    chans = {}
    for r in rows:
        chans[r["channel"]] = chans.get(r["channel"], 0) + 1

    kpi_html = "\n".join([
        '<div class="kpi"><div class="v">%d</div><div class="l">采集帖子总数</div></div>' % n,
        '<div class="kpi"><div class="v">%d 天</div><div class="l">观测窗口 Day0–Day%d</div></div>' % (window_end_day + 1, window_end_day),
        '<div class="kpi"><div class="v">%d</div><div class="l">覆盖渠道类别</div></div>' % len(chans),
        '<div class="kpi"><div class="v">%d%%</div><div class="l">%s情感占比</div></div>' % (round(dom[1] / n * 100), dom[0]),
        '<div class="kpi"><div class="v">%s</div><div class="l">主要发声角色</div></div>' % " / ".join(
            "%d" % c for _, c in top_roles),
    ])

    # 时间线
    by_id = {r["post_id"]: r for r in rows}
    tl_parts = []
    day_counts = {}
    for r in rows:
        day_counts[r["timespan_days"]] = day_counts.get(r["timespan_days"], 0) + 1
    for d in sorted(day_counts):
        tl_parts.append('<div class="tl-day">Day%d · %s（%d 条）</div>' % (
            d, "曝光" if d == 0 else ("发酵" if d == 1 else "传播"), day_counts[d]))
        for r in rows:
            if r["timespan_days"] != d:
                continue
            te = pack["timeline"] and [t for t in pack["timeline"] if t["post_id"] == r["post_id"]]
            plat = te[0]["plat"] if te else r["source_platform"]
            badges = list(te[0].get("badges") or []) if te else []
            warn = te[0].get("warn") if te else None
            # 口径修正：监管通报的首发帖标「权威发布」而非「权威转载」
            if rows[0]["post_id"] == r["post_id"] and "key:权威转载" in badges:
                badges = [("key:权威发布" if b == "key:权威转载" else b) for b in badges]
            tags = []
            for b in badges:
                if b.startswith("key:"):
                    tags.append('<span class="tag key">%s</span>' % esc(b[4:]))
            if warn:
                tags.append('<span class="tag warn">%s</span>' % esc(warn))
            tags.append('<span class="tag">%s · %s</span>' % (esc(r["role_type"]), esc(r["sentiment"])))
            cls = "tl-item" + ("" if d == 0 else " d1")
            tl_parts.append(
                '<div class="%s"><span class="t">%s</span><span class="plat">%s</span> %s'
                '<div class="ttl">%s</div></div>'
                % (cls, esc(r["publish_time"][5:16]), esc(plat), " ".join(tags), esc(r["title"])))
    timeline_html = "\n".join(tl_parts)

    # 媒体观点
    op = pack["opinion"]
    op_html = []
    for g in op.get("groups", []):
        op_html.append('<div class="op-group"><h3>%s（%d 篇）</h3><div class="cnt">%s</div><ul>%s</ul></div>'
                       % (esc(g["name"]), g.get("count", len(g.get("members", []))), esc(g.get("desc", "")),
                          "".join("<li>%s</li>" % esc(m) for m in g.get("members", []))))
    pts = []
    for p in op.get("points", []):
        pts.append('<div class="point"><b>%s</b>%s<span class="cov">%s</span></div>'
                   % (esc(p["title"]), esc(p["text"]), esc(p.get("cov", ""))))
    if op.get("note"):
        op_html.append('<p class="note" style="margin-top:10px;">%s</p>' % esc(op["note"]))
    op_html.append('<div class="points">%s</div>' % "".join(pts))
    opinion_html = "\n".join(op_html)

    # 渠道分布模块内嵌的 note
    senti_note = "口径：单纯报道/陈述事件、无明显情感色彩记「中性」；全部 %d 条中负面 %d、中性 %d、正面 %d" % (n, neg, neu, pos)

    # 明细表
    def usable_url(u):
        if not u:
            return ""
        # 裸平台首页（如 https://weibo.com/）不算有效原文链接
        m = re.match(r"^https?://([^/]+)/?$", u)
        if m and m.group(1) in ("weibo.com", "m.weibo.cn", "www.weibo.com"):
            return ""
        return u

    table_rows = []
    for r in rows:
        url = usable_url(r.get("post_url"))
        cell = ('<a href="%s" target="_blank" rel="noopener">原文</a>' % esc(url)) if url else "—"
        table_rows.append(
            "<tr><td>%s</td><td class=\"num\">%s</td><td>Day%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(r["post_id"]), esc(r["publish_time"]), r["timespan_days"], esc(r["channel"]),
               esc(r["source_platform"]), esc(r["author_name"]), esc(r["role_type"]),
               esc(r["sentiment"]), esc(r["title"]), cell))
    table_html = "\n".join(table_rows)

    # 内嵌数据
    posts_js = []
    for r in rows:
        js_url = usable_url(r.get("post_url"))
        posts_js.append(
            '{id:%s,time:%s,day:%s,channel:%s,platform:%s,author:%s,role:%s,sentiment:%s,title:%s,url:%s,'
            'topics:%s,ents:%s}' % (
                json.dumps(r["post_id"], ensure_ascii=False), json.dumps(r["publish_time"], ensure_ascii=False),
                r["timespan_days"], json.dumps(r["channel"], ensure_ascii=False),
                json.dumps(r["source_platform"] or "", ensure_ascii=False),
                json.dumps(r["author_name"] or "", ensure_ascii=False),
                json.dumps(r["role_type"], ensure_ascii=False), json.dumps(r["sentiment"], ensure_ascii=False),
                json.dumps(r["title"], ensure_ascii=False), json.dumps(js_url, ensure_ascii=False),
                json.dumps(list(r.get("topic_hit") or []), ensure_ascii=False),
                json.dumps(list(r.get("extract_entity") or []), ensure_ascii=False)))
    channels_present = [c for c in CANON if c in chans]
    js = (JS_TEMPLATE
          .replace("@@POSTS@@", "[\n" + ",\n".join(posts_js) + "\n]")
          .replace("@@CHANNELS@@", json.dumps(channels_present, ensure_ascii=False)))

    footer = """
    <div><b>口径说明</b></div>
    <div>· 情感/角色/主题/实体标注：事件 1-12 沿用参考包（众安舆情_采集与复现包）的 LLM 标注与规则判定结果，本看板只做格式转换；渠道与来源平台按新口径重新整理。</div>
    <div>· 渠道为 6 类标准口径；监管通报=中央发文级权威渠道（中央部委/监管机构官方渠道与党央媒官方渠道），地方、商业媒体转载中央报道的归新闻门户。</div>
    <div>· 主题词与实体分别取自 topic_hit / extract_entity 标注字段；词云字号按出现条数缩放。</div>
    <div>· 无原文链接的帖子（采集时未记录 URL）明细表链接列显示「—」。</div>
    <div>· 本看板由 EVT-XXX_processed.json 静态生成，无外部依赖，可离线打开。</div>
    """.replace("EVT-XXX", evt_id)

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%s · 舆情看板</title>
<style>%s</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>%s · 舆情看板</h1>
    <div class="sub">观测窗口 <b>%s ~ %s</b>（Day0 ~ Day%d）｜数据源 <b>%s_processed.json</b>（%d 条）｜生成日期 <b>2026-10-19</b></div>
  </div>
  <div class="filters" id="chan-overview"><span class="flabel">渠道</span></div>

  <div class="card">
    <h2>一、事件概述</h2>
    <p class="note">一句话界定：涉事主体/产品 ＋ 争议行为 ＋ 一个可区分相似案例的事实</p>
    <p>%s</p>
    <div class="kpis">%s</div>
  </div>

  <div class="card">
    <h2>二、时间线 / 关键节点</h2>
    <p class="note">按发布时间排序；Day0 = 事件首次曝光日（%s），Day1 = 次日</p>
    <div class="tl">%s</div>
  </div>

  <div class="grid2">
    <div class="card">
      <h2>三、传播渠道分布</h2>
      <p class="note">6 类标准渠道口径；悬停查看数值，明细见文末表格</p>
      <div id="chan-bars"></div>
      <div class="note" id="chan-detail" style="margin-top:14px;"></div>
    </div>
    <div class="card">
      <h2>四、情感分布与变化</h2>
      <p class="note">%s</p>
      <div class="seg"><div class="neg" id="seg-neg" style="width:100%%;">负面</div></div>
      <div class="trend">
        <div class="note" style="margin-bottom:2px;">情感演化曲线（6 小时分桶，悬停查看数值）</div>
        <div class="trend-legend">
          <span><span class="lk" style="background:#1f1f1f"></span>负面</span>
          <span><span class="lk" style="background:#9a9a9a"></span>中性</span>
          <span><span class="lk" style="background:#cfcfcf"></span>正面</span>
        </div>
        <div id="trend-box"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>五、关键词词云</h2>
    <p class="note">主题词（深灰）＝ 帖子命中的事件主题词；实体（浅灰）＝ 帖子提到的公司 / 产品 / 人物 / 机构。字号按出现频次缩放</p>
    <div class="cloud" id="cloud"></div>
    <div class="cloud-legend">
      <span><span class="sw t"></span>主题词（topic_hit）</span>
      <span><span class="sw e"></span>实体（extract_entity）</span>
      <span>悬停查看频次</span>
    </div>
  </div>

  <div class="card">
    <h2>六、媒体观点分类</h2>
    <p class="note">按标题措辞与内容立场分组；观点要点为媒体帖共同观点的归纳</p>
    %s
  </div>

  <div class="card chain">
    <h2>七、传播链条</h2>
    <p class="note">当事人/信源 → 首发 → 转载扩散 → 微博扩散</p>
    %s
    <div class="legend">
      <span class="ld0">Day0 曝光（蓝）</span>
      <span class="ld1">Day1 发酵（浅灰）</span>
      <span class="ldx">不可见 / 待复核（虚线）</span>
    </div>
  </div>

  <div class="card">
    <h2>帖子明细（表格视图）</h2>
    <p class="note">全量 %d 条；所有数值与图表一致，可逐条回溯原文（无链接的显示「—」）</p>
    <div class="tbl-wrap">
      <table id="post-table">
        <thead><tr>
          <th>post_id</th><th>发布时间</th><th>Day</th><th>渠道</th><th>来源平台</th>
          <th>作者</th><th>角色</th><th>情感</th><th>标题</th><th>原文链接</th>
        </tr></thead>
        <tbody>%s</tbody>
      </table>
    </div>
  </div>

  <div class="foot">%s</div>
</div>
<div id="tip"></div>
<script>
%s
</script>
</body>
</html>""" % (
        esc(evt_id + " " + evt_name), CSS,
        esc(evt_id + " " + evt_name), esc(t0), esc(end_date), window_end_day,
        evt_id, n,
        md_bold(pack.get("overview", "")), kpi_html,
        esc(t0), timeline_html,
        esc(senti_note),
        opinion_html,
        chain_svg(pack),
        n, table_html,
        footer,
        js)

    with open(out_html, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("wrote %s (%d posts)" % (out_html, n))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], sys.argv[3])
