/* ============================================================
   版面自检 —— 说明不许占屏，但也不许消失

   ★ 为什么要有这个文件：
     2026-09-18 返工的要求是「说明文字属于文档，不属于屏幕」。
     但这句话有**两个方向**，而它们长得一模一样地「清爽」：

       版面干净   ← 要的
       信息变少   ← 不要的

     ★ 只守前一个，我们会一路把负责人需要的东西删光，而测试全绿。
       所以每一页都要**同时**守住两条：
         · `.note` / `.gate` 一个不留（段落式说明不许上屏）
         · 页面级那个说明 fold **按 id 点名**必须在，且 `.fold__body` 有货

   ★ 为什么按 id 点名而不是数 `.fold` 的个数：
     数个数是个假地板。plan.html 有 4 个 fold（页面级 2 个 + 两个嵌在
     leadSay / planMonthsSay 里的），把页面级那个整个删掉，「≥ 1」照样成立 ——
     实测过，全绿。
   ★ 为什么还要验字数：
     光验「元素在」，把 `.fold__body` 掏空也能绿 ——
     **「收起来」和「掏空」又是一对长得一样的东西。**

   ★ 分工（不是重复）：
     plan.html 归 test_page.js 第 7 节 —— 它验的是**提交之后**那个状态
     （#banner / #result 有内容，那两处最容易顺手写回 .note）。
     这里验的是四页**装载完的初始状态**，plan.html 也过一遍，
     因为两个状态里「谁会写回 .note」的风险点不一样。

   跑法:  node test_prose.js
   依赖:  jsdom（没装会告诉你怎么装，并以「跳过」退出，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据（2026-09-18，用 ./replay.sh 实测）
   | 故意改成                                      | 转红的断言              |
   |----------------------------------------------|------------------------|
   | plan-rev.html 里塞一个 <div class="gate">     | 「一个都没有」×1        |
   | ops.html 的 id="whyBarrel" 改名               | 「#whyBarrel 还在」×1   |
   | plan.html 留外壳、把 .fold__body 掏空          | 「里面有货」×1（§7）    |
   | plan-add.html 的 fold 加 open                 | 「默认是收起的」×1      |
   ★ 「改名」与「掏空」是一对，各自只打红对侧 ——
     只验前者的话，把说明掏空也能全绿，而那正是我们要防的那一半。
   ★ 回放用 ./replay.sh：它会拦住「变异没生效」和「变异把页面打炸了」这两种假证据，
     后者尤其要紧 —— 页面一炸，本文件的 `.note 一个都没有` 会**假绿**（真的一个都没有，
     因为整页都没渲染）。所以上面每一页都先验两条前置：装载无错 + 主体真的画出来了。
   ============================================================ */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (e) {
  // ★ 装不上就明说跳过 —— 静默当成通过是最坏的一种
  console.log('⚠ 没有 jsdom，这个测试跳过（不是通过）。');
  console.log('  装法：npm install jsdom   或   npm install -g jsdom 后设 NODE_PATH');
  console.log('  原因：' + (e && e.code === 'MODULE_NOT_FOUND' ? '模块未安装' : e && e.message));
  process.exit(0);
}

const DIR = __dirname;
const WHY_MIN = 200;          // 页面级说明的字数下限。当前实际远大于它 —— 是地板，不是目标

/* 每页点名一个页面级说明 fold。★ 加页面就在这里加一行，
   不许用「找第一个 .fold」之类的推断 —— 那又回到数个数了。 */
const PAGES = [
  { file: 'ops.html',       why: 'whyBarrel', must: 'planTable' },
  { file: 'plan.html',      why: 'whyGrid',   must: 'blocks' },
  { file: 'plan-add.html',  why: 'whyClaim',  must: 'cand' },
  { file: 'plan-rev.html',  why: 'whyRev',    must: 'revTable' }
];

const fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

/* ── 种一份**有数据**的状态 ──
   空计划渲染不出格子、也长不出 #banner / #result，
   拿空状态验版面等于在验一张白纸。 */
function seedState() {
  const mem = {};
  const ls = {
    getItem: k => (Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: k => { delete mem[k]; }
  };
  const sb = { window: { localStorage: ls }, localStorage: ls,
               console: Object.assign({}, console, { debug() {}, error() {} }) };
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(DIR, 'assets/store.js'), 'utf8'), sb);
  const S = sb.window.Store;
  const plan = (S.get().plans || []).filter(p => !(p.claims || []).length)[0];
  const hit = S.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
  const group = {};
  (hit.rows || []).filter(r => r.selectable)
    .forEach(r => (group[r.sku] = group[r.sku] || []).push(r));
  const skus = Object.keys(group).slice(0, 3);
  skus.forEach(k => S.addClaims(plan.plan_id, group[k].map(r =>
    ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid }))));
  const p2 = S.plan(plan.plan_id);
  (p2.claims || []).forEach((c, i) => p2.periods.forEach((m, j) =>
    S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, m, 30 + i * 5 + j * 4)));
  S.setPurchase(p2.plan_id, skus[0], p2.periods[0], 500);
  return { json: mem['scm_proto'], planId: p2.plan_id };
}
const seeded = seedState();

function load(page) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.stack || e.message)));
  vc.on('error', (...a) => {
    const s = a.map(String).join(' ');
    if (/cells-backfilled/.test(s)) return;      // 装载期回填提示，良性
    errors.push('console.error: ' + s);
  });
  const dom = new JSDOM(fs.readFileSync(path.join(DIR, page), 'utf8'), {
    runScripts: 'outside-only',
    url: 'http://localhost/' + page + '?plan=' + seeded.planId,
    virtualConsole: vc, pretendToBeVisual: true
  });
  const w = dom.window, doc = w.document;
  w.scrollTo = function () {};
  w.localStorage.setItem('scm_proto', seeded.json);
  const run = (code, label) => {
    try { w.eval(code); }
    catch (e) { errors.push('★ ' + label + ' 抛出: ' + (e.stack || e)); }
  };
  [...doc.querySelectorAll('script[src]')].forEach(sc => {
    const p = path.join(DIR, sc.getAttribute('src'));
    if (!fs.existsSync(p)) { errors.push('缺少脚本 ' + sc.getAttribute('src')); return; }
    run(fs.readFileSync(p, 'utf8'), sc.getAttribute('src'));
  });
  [...doc.querySelectorAll('script:not([src])')].forEach((sc, i) =>
    run(sc.textContent, '内联脚本 #' + i));
  return { doc, errors };
}

PAGES.forEach(spec => {
  console.log('\n── ' + spec.file + ' ──');
  const { doc, errors } = load(spec.file);

  /* ★ 前置：页面得**真的跑起来了**。
     页面一炸，.note 自然是 0、字数自然是 0 —— 前一条会假绿，后一条会真红，
     两条都不再说明版面的事。先把「页面炸了」这一种单独分出来。 */
  ok(!errors.length, '★ 前置：装载没有未预期的错误（页面炸了的话下面全不作数）',
     errors.slice(0, 2).map(e => e.split('\n').slice(0, 3).join(' ')).join(' ｜ '));
  const anchor = doc.getElementById(spec.must);
  ok(!!anchor, '★ 前置：页面主体渲染出来了（#' + spec.must + '）',
     '找不到 —— 这一页根本没画出来，下面是空转');

  const prose = doc.querySelectorAll('.note, .gate');
  ok(prose.length === 0, '一个 .note / .gate 说明块都没有',
     '还剩 ' + prose.length + ' 个：' +
     Array.prototype.map.call(prose, n =>
       '<' + n.tagName.toLowerCase() + ' class="' + n.className + '">' +
       n.textContent.replace(/\s+/g, ' ').trim().slice(0, 40)).join(' ｜ '));

  const why = doc.getElementById(spec.why);
  ok(!!why && why.className.indexOf('fold') >= 0,
     '页面级说明 #' + spec.why + ' 还在（点名，不数个数）',
     why ? '找到了但它不是 .fold：class=' + why.className
         : '找不到 #' + spec.why + ' —— 说明被删了，还是 id 被改了？');
  if (!why) return;

  const body = why.querySelector('.fold__body');
  const text = body ? body.textContent.replace(/\s+/g, '') : '';
  ok(text.length >= WHY_MIN,
     '★ 它里面有货（' + text.length + ' 字 ≥ ' + WHY_MIN + '）—— 光验元素在，掏空也能绿',
     body ? '只剩 ' + text.length + ' 字：是收起来了，还是被掏空了？' : '连 .fold__body 都没有');

  ok(!why.hasAttribute('open'), '默认是收起的（展开着就等于没收）');
});

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败' : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
