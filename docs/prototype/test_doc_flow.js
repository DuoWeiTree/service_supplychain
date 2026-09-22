/* test_doc_flow.js —— 文档写的操作流程，原型里必须真走得通。

   ★ 为什么要这条断言：
     `10-前端交互界面.md` 曾经整份描述的是另一套原型（docs/html/，8 个页面全不存在），
     而**没有任何东西会因此失败** —— 文档和原型各自都「好好的」。
     漂移不会报警，所以必须有人按着两边比。

   核对三件事（都从 §2.5 的表里推，不是手写清单）：
     ① 表里出现的每个 X.html 都存在
     ② 「做什么」列里的**粗体 = 按钮原文**，必须在「在哪」那一页的源码里逐字出现
     ③ 「去到」列里的页面，必须能从「在哪」那一页跳过去（源码里有这个 href/location）

   ★ ② 是这里最有用的一条：按钮改了名而文档没改，人照着文档点就点不到。
*/
'use strict';
var fs = require('fs'), path = require('path');

/* 可传入文档副本，用来在沙箱里打变异验证这几条断言自己还活着 */
var DOC = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(__dirname, '..', '10-前端交互界面.md');
var md = fs.readFileSync(DOC, 'utf8');

var bad = [], checked = { pages: 0, labels: 0, edges: 0 };
function no(what, detail) { bad.push(what + (detail ? '\n      ' + detail : '')); }

/* ── 取 §2.5 ────────────────────────────────────────────── */
var i = md.indexOf('## 2.5');
var j = md.indexOf('\n## 3.', i);
if (i < 0 || j < 0) {
  console.error('✗ 10-前端交互界面.md 里找不到 §2.5 操作流程 —— 这一节是规格，不许删');
  process.exit(1);
}
var sec = md.slice(i, j);

/* ── 解析：只认表头带「在哪」的表 ─────────────────────────── */
var rows = [], inFlow = false, lastPage = null;
sec.split('\n').forEach(function (ln) {
  /* ★ 表一结束就退出流程表模式 —— 不复位的话，后面任何四列表
     都会继承「这是流程表」的身份，于是 §2.6 的 delta 表被当成按钮原文核对。
     （2026-09-21 实测撞上：报了 5 处假失败） */
  if (ln.indexOf('|') !== 0) { inFlow = false; lastPage = null; return; }
  var cells = ln.split('|').slice(1, -1).map(function (c) { return c.trim(); });
  if (cells.length < 4) { inFlow = false; lastPage = null; return; }
  if (cells[1] === '在哪') { inFlow = true; lastPage = null; return; }   // 表头
  if (/^-+$/.test(cells[0].replace(/:/g, ''))) { return; }               // 分隔行
  if (!inFlow) { return; }

  var page = (cells[1].match(/`([a-z-]+\.html)`/) || [])[1] || lastPage;
  if (!page) { no('§2.5 有一行既没写「在哪」，前面也没有可继承的页面', ln); return; }
  lastPage = page;
  rows.push({ page: page, act: cells[2], to: cells[3], raw: ln });
});

if (rows.length < 3) { no('§2.5 解析出的流程行只有 ' + rows.length + ' 条 —— 解析多半坏了'); }

/* ── 读页面源码 ─────────────────────────────────────────── */
var src = {};
function read(p) {
  if (!(p in src)) {
    var f = path.join(__dirname, p);
    src[p] = fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : null;
  }
  return src[p];
}

rows.forEach(function (r) {
  /* ① 页面存在 */
  if (read(r.page) === null) {
    no('§2.5 写了不存在的页面：' + r.page, r.raw);
    return;
  }
  checked.pages++;

  /* ② 按钮原文 */
  var labels = (r.act.match(/\*\*([^*]+)\*\*/g) || []).map(function (m) {
    return m.slice(2, -2).replace(/^[⛔★\s]+|[\s]+$/g, '');
  });
  labels.forEach(function (L) {
    checked.labels++;
    if (read(r.page).indexOf(L) < 0) {
      no('按钮「' + L + '」在文档里写着，但 ' + r.page + ' 的源码里没有这几个字',
         '★ 人照文档点，是点不到的。' + r.raw);
    }
  });

  /* ③ 跳转真的存在 */
  var tos = r.to.match(/`?([a-z-]+)\.html/g) || [];
  tos.forEach(function (t) {
    var target = t.replace(/`/g, '');
    if (target === r.page) { return; }
    checked.edges++;
    if (read(r.page).indexOf(target) < 0) {
      no('文档说 ' + r.page + ' → ' + target + '，但 ' + r.page + ' 的源码里根本没提到它',
         r.raw);
    }
  });
});

/* ── mermaid 的节点页面也要存在（边是跨角色交接，不校验） ──── */
var mm = sec.match(/```mermaid[\s\S]*?```/);
if (!mm) { no('§2.5 的整条链 mermaid 图不见了'); }
else {
  var seen = {};
  (mm[0].match(/\[([a-z-]+) [^\]]*\]/g) || []).forEach(function (n) {
    var name = n.slice(1).split(' ')[0];
    if (seen[name]) { return; }
    seen[name] = 1;
    if (read(name + '.html') === null) {
      no('mermaid 图里的节点 ' + name + ' 没有对应页面 ' + name + '.html');
    }
  });
}

/* ── 报告 ──────────────────────────────────────────────── */
if (bad.length) {
  console.error('\n✗ 文档与原型不符：' + bad.length + ' 处\n');
  bad.forEach(function (b, n) { console.error('  ' + (n + 1) + ') ' + b); });
  console.error('\n★ 改的是哪一边都行，但两边必须同时改。\n');
  process.exit(1);
}
console.log('✓ test_doc_flow：§2.5 的 ' + rows.length + ' 条流程全部走得通'
  + '（页面 ' + checked.pages + ' · 按钮 ' + checked.labels + ' · 跳转 ' + checked.edges + '）');
