// 报告「真实渲染」审计：在最小 DOM 沙箱里执行报告内联 JS，
// 统计 34 个渲染单元里谁被填了、谁空着、SEC_ERR 有几条。只读，不写任何交付物。
// 用法：node _p196_render_check.js <报告.html> [--text [id…]]
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = process.argv[2];
const html = fs.readFileSync(HTML, 'utf8');

// ① 取内联脚本（跳过带 src 的外链，以及 `data-vendor` 的第三方库内联段）
// ★ 2026-10-21：报告可以产成**自包含单文件**（`REPORT_ECHARTS` 把 echarts 内联进来），
//   那一段是库不是报告逻辑，塞进 DOM 沙箱只会假报错，按 `data-vendor` 跳过。
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*\bdata-vendor=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const code = scripts.join('\n;\n');

// ② HTML 里真实存在的 id 集合
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));

// ③ 最小 DOM
const filled = new Map();          // id -> 最后写入的 HTML 长度
const content = new Map();         // id -> 最后写入的 HTML 原文
function mkEl(id) {
  const el = {
    _id: id, tagName: 'DIV', innerHTML: '', textContent: '', value: '',
    style: new Proxy({}, { get: () => '', set: () => true }),
    dataset: {}, children: [], checked: false, disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {}, insertBefore(c) { return c; },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, click() {},
    querySelector: () => null, querySelectorAll: () => [],
    getContext: () => null, getBoundingClientRect: () => ({ width: 800, height: 320, top: 0, left: 0 }),
    scrollIntoView() {}, offsetWidth: 800, offsetHeight: 320, clientWidth: 800, clientHeight: 320,
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html || ''; },
    set(v) { this._html = String(v); filled.set(id, this._html.length); content.set(id, this._html); },
  });
  Object.defineProperty(el, 'textContent', {
    get() { return this._txt || ''; },
    set(v) {
      this._txt = String(v);
      if (!filled.has(id)) filled.set(id, this._txt.length);
      if (!content.has(id)) content.set(id, this._txt);
    },
  });
  return el;
}
const elCache = new Map();
const document = {
  getElementById(id) {
    if (!ids.has(id)) { missing.push(id); return null; }     // 真实行为：不存在就 null
    if (!elCache.has(id)) elCache.set(id, mkEl(id));
    return elCache.get(id);
  },
  querySelector(s) {
    if (typeof s === 'string' && s[0] === '#') return this.getElementById(s.slice(1));
    return null;
  },
  querySelectorAll: () => [],
  createElement(tag) { const e = mkEl('@' + tag); e.tagName = tag.toUpperCase(); return e; },
  createElementNS(ns, tag) { return document.createElement(tag); },
  addEventListener() {}, removeEventListener() {},
  body: mkEl('@body'), head: mkEl('@head'), documentElement: mkEl('@html'),
  readyState: 'complete', title: '',
};
const missing = [];
const charts = [];
const echarts = {
  init(el) {
    const c = { el, setOption() {}, resize() {}, dispose() {}, on() {}, off() {}, clear() {},
                getWidth: () => 800, getHeight: () => 320, getOption: () => ({}),
                getZr: () => ({ on() {} }), dispatchAction() {} };
    charts.push(c); return c;
  },
  graphic: { LinearGradient: function () { return {}; } },
  registerTheme() {}, connect() {}, dispose() {},
};
const sandbox = {
  document, echarts, console,
  window: null, navigator: { userAgent: 'node' }, location: { href: 'file:///report.html', search: '' },
  setTimeout: (f) => { try { f(); } catch (e) { throw e; } return 0; },
  clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  requestAnimationFrame: (f) => { f(0); return 0; }, cancelAnimationFrame() {},
  addEventListener() {}, removeEventListener() {},
  encodeURIComponent, decodeURIComponent, JSON, Math, Date, String, Number, Object, Array,
  isNaN, parseInt, parseFloat, RegExp, Error, Map, Set, Promise, Intl,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

let fatal = null;
try {
  vm.runInNewContext(code, sandbox, { filename: 'report-inline.js' });
} catch (e) {
  fatal = e;
}

// ④ 报告自称的渲染单元（从脚本里抓 setHTML 目标）
const targets = [...new Set([...code.matchAll(/setHTML\(\s*'([^']+)'/g)].map(m => m[1]))];

// ★ 2026-10-15：抬头原先写死「EVT-004」—— 这工具对每场都跑，印死一个事件名会误导。
//   改成从 HTML 文件名现取（`EVT-00N_仿真运行可视化报告.html` → `EVT-00N`）。
const evtName = (path.basename(process.argv[2] || '').match(/^(EVT-\d+)/) || [])[1]
  || (path.basename(process.argv[2] || '') || '（未命名）');
console.log('==================== %s 报告真实渲染审计 ====================', evtName);
console.log('内联脚本 %d 段 / %d 字符 ｜ HTML 里 id %d 个', scripts.length, code.length, ids.size);
console.log('顶层执行：%s', fatal ? '❌ 抛错 → ' + fatal.message : '✔ 未抛错');
console.log('SEC_ERR = %d 条', (sandbox.SEC_ERR || []).length);
(sandbox.SEC_ERR || []).forEach(x => console.log('   · %s', x));
console.log('getElementById 命中不存在的 id：%s', missing.length ? [...new Set(missing)].join(', ') : '✔ 无');
console.log('');
console.log('setHTML 目标 %d 个 → 填没填：', targets.length);
let ok = 0, empty = [];
targets.forEach(t => {
  const n = filled.has(t) ? filled.get(t) : -1;
  if (n > 0) { ok++; } else { empty.push(t + '(' + (n < 0 ? '从未写入' : '空串') + ')'); }
});
console.log('   ✔ 有内容 %d / %d', ok, targets.length);
console.log('   ✗ 空着：%s', empty.length ? empty.join('  ') : '无');
console.log('');
console.log('全部被写入过的 id（含非 setHTML 的，如 warning 横幅）：%d 个', filled.size);
const nonTarget = [...filled.keys()].filter(k => !targets.includes(k));
console.log('   非 setHTML 但在写的 id：%s', nonTarget.join(', ') || '无');

// ⑤ 正文 dump（供人工核对"报告到底写了什么"）
if (process.argv[3] === '--text') {
  const strip = s => s
    .replace(/<br\s*\/?>/gi, '\n').replace(/<\/(p|div|li|tr|h[1-6])>/gi, '\n')
    .replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n').trim();
  const want = process.argv.slice(4);
  const list = want.length ? want : targets;
  list.forEach(t => {
    const s = strip(content.get(t) || '');
    console.log('\n' + '='.repeat(100));
    console.log('### ' + t + '　（渲染后 ' + s.length + ' 字）');
    console.log('='.repeat(100));
    console.log(s);
  });
}
