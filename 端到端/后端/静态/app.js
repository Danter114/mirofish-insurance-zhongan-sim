/* 三个页面共用的取数与渲染小工具。原生 ES 模块，浏览器直接跑，无构建步骤。 */

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const ct = r.headers.get('Content-Type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = (data && data.error) || ('HTTP ' + r.status);
    const e = new Error(msg);
    e.status = r.status;
    throw e;
  }
  return data;
}

/* ── DOM ─────────────────────────────────────────────────── */
export function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

export const $ = (sel, root = document) => root.querySelector(sel);

export function mount(sel, ...kids) {
  const host = $(sel);
  host.replaceChildren(...kids.flat().filter(Boolean));
  return host;
}

/* ── 格式化 ──────────────────────────────────────────────── */
export function fmtBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(2) + ' MB';
}

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h} 时 ${m} 分`;
  if (m) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

/** 两个时间字符串（YYYY-MM-DD HH:MM:SS）相差多少秒；算不出来回 null。 */
export function since(a, b) {
  const p = (t) => {
    const m = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)/.exec(t || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
  };
  const x = p(a), y = p(b);
  return x && y ? (y - x) / 1000 : null;
}

export const nowStamp = () =>
  new Date().toLocaleTimeString('zh-CN', { hour12: false });

/* ── 状态语义（三页共用，措辞只此一处） ─────────────────── */
export const JOB_BADGE = {
  idle:    { cls: '',    text: '没跑过' },
  running: { cls: 'run', text: '运行中' },
  done:    { cls: 'ok',  text: '跑完了' },
  failed:  { cls: 'err', text: '失败了' },
  stopped: { cls: 'warn', text: '已停止' },
};

export function badge(cls, text, dot = false) {
  return el('span', { class: 'badge ' + (cls || '') },
    dot ? el('i', { class: 'dot' }) : null, text);
}

export function jobBadge(job) {
  const s = (job && job.state) || 'idle';
  const b = JOB_BADGE[s] || JOB_BADGE.idle;
  return badge(b.cls, (job && job.label ? job.label + ' · ' : '') + b.text, s === 'running');
}

/* ── 进度条 + 轮次格子 ───────────────────────────────────── */
export function bar(done, total, complete) {
  const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  return el('div', { class: 'bar' + (complete ? ' done' : '') },
    el('i', { style: `width:${pct.toFixed(1)}%` }));
}

export function cells(roundsDone, nRounds, running) {
  const out = el('div', { class: 'cells' });
  for (let r = 0; r < nRounds; r++) {
    const cls = r <= roundsDone ? 'done'
      : (running && r === roundsDone + 1 ? 'now' : '');
    out.append(el('i', { class: cls, title: '第 ' + r + ' 轮（共 ' + (nRounds - 1) + ' 轮要跑）' }));
  }
  return out;
}

/* ── 日志染色（只认前缀，不改内容） ─────────────────────── */
export function paintLog(pre, lines) {
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
  pre.replaceChildren(...lines.map((ln) => {
    let cls = '';
    if (/^\[[^\]]+\]\s*(✔|OK|完成|通过)/.test(ln) || /✔/.test(ln)) cls = 'l-ok';
    if (/Traceback|Error|错误|失败|✘|不通过/.test(ln)) cls = 'l-err';
    if (/^=+$|^─+$|^\[\d+\/\d+\]|^轮\s*\d+/.test(ln)) cls = 'l-hd';
    return el('div', { class: cls, text: ln || ' ' });
  }));
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

/* ── 事件 id 的 URL 参数 ────────────────────────────────── */
export function queryEventId() {
  return new URLSearchParams(location.search).get('event') || '';
}

/* ── 一次性提示条（失败时顶上弹一条，不打断操作） ───────── */
export function note(host, kind, text) {
  const n = el('div', { class: 'note ' + (kind || ''), text });
  host.prepend(n);
  setTimeout(() => n.remove(), kind === 'err' ? 12000 : 5000);
  return n;
}
