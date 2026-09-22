/* test_retired_claims.js —— 被推翻的说法不许复活，欠债不许悄悄长大。

   ★ 为什么需要它：
     N-5 判掉 FC 之后，「一个目的地一张」在 `02` 和原型里又活了一周，
     直到外部的数据部门指出来才发现。**文档里的错说法不会自己失败。**

   ★ 清单不写在这里，从 `00c-货件分层改造计划.md` §5 读 ——
     测试和文档各存一份清单，两份必然分叉（那正是上面那个 bug 的形状）。

   ★ 判据是**等式**，不是上限：
       多一条 → 新违规
       少一条 → 基线陈旧（去更新表、勾掉那一步）
     第一版用的是「允许名单 + 子串匹配」，`allow` 里写了 `html`，
     于是**每个 .html 文件都被放过**，sheet.html 里 20 处一处没报 ——
     断言比目标宽，就会被旁边的东西托住。
*/
'use strict';
var fs = require('fs'), path = require('path');

var DOCS = path.join(__dirname, '..');
var PLAN = '00c-货件分层改造计划.md';
/* 整份作废，不扫；见 00c §5a */
var SKIP_DIR = 'html';

var plan = fs.readFileSync(path.join(DOCS, PLAN), 'utf8');
var bad = [];

function section(from, to) {
  var i = plan.indexOf(from), j = plan.indexOf(to, i);
  if (i < 0 || j < 0) {
    console.error('✗ 00c 里找不到「' + from + '」—— 这张表是本断言的唯一依据，删了等于关掉门禁');
    process.exit(1);
  }
  return plan.slice(i, j);
}
function rows(txt, minCells) {
  var out = [];
  txt.split('\n').forEach(function (ln) {
    if (ln.indexOf('|') !== 0) { return; }
    var c = ln.split('|').slice(1, -1).map(function (x) { return x.trim(); });
    if (c.length < minCells) { return; }
    if (/^-+$/.test(c[0].replace(/:/g, '')) || c[0].indexOf('`') < 0) { return; }   // 表头/分隔
    out.push(c);
  });
  return out;
}
function tick(s) { return (s.match(/`([^`]+)`/) || [])[1]; }

/* ── 5a 宇宙 ─────────────────────────────────────────── */
var claims = rows(section('### 5a', '### 5b'), 2).map(function (c) {
  return { text: tick(c[0]), why: c[1] };
});
if (!claims.length) { console.error('✗ 5a 解析出 0 条 —— 解析坏了，不是「没有作废说法」'); process.exit(1); }

/* ── 5b 基线 ─────────────────────────────────────────── */
var base = {};                       // "file|claim" -> {n, kind, raw}
rows(section('### 5b', '\n## 6.'), 4).forEach(function (c) {
  var f = tick(c[0]), t = tick(c[1]);
  var n = c[2] === '—' ? null : parseInt(c[2], 10);
  var perm = c[3].indexOf('永久') >= 0;
  if (!f || !t) { bad.push('5b 有一行的文件或说法没用反引号：' + c.join(' | ')); return; }
  if (!perm && !(n > 0)) { bad.push('5b「' + f + ' / ' + t + '」标了清理步骤却没写条数'); return; }
  base[f + '|' + t] = { n: perm ? null : n, kind: c[3], seen: 0 };
});

/* ── 扫描 ───────────────────────────────────────────── */
function walk(dir, out) {
  fs.readdirSync(dir, { withFileTypes: true }).forEach(function (e) {
    var p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name === '.git' || path.relative(DOCS, p) === SKIP_DIR) { return; }
      walk(p, out);
    } else if (/\.(md|html|js|css)$/.test(e.name)) { out.push(p); }
  });
  return out;
}
var SELF = path.relative(DOCS, __filename);
var scanned = 0;

walk(DOCS, []).forEach(function (f) {
  var rel = path.relative(DOCS, f).split(path.sep).join('/');
  if (rel === SELF || rel === PLAN) { return; }
  scanned++;
  var body = fs.readFileSync(f, 'utf8');
  claims.forEach(function (cl) {
    var n = body.split(cl.text).length - 1;
    if (!n) { return; }
    var row = base[rel + '|' + cl.text];
    if (!row) {
      bad.push('★ 新违规：' + rel + ' 出现 ' + n + ' 处「' + cl.text + '」\n      被推翻：' + cl.why
             + '\n      要么改掉，要么在 00c §5b 登记（并说明是永久说明还是待清理欠债）');
      return;
    }
    row.seen = n;
    if (row.n !== null && n !== row.n) {
      bad.push((n > row.n ? '★ 欠债变多了：' : '基线陈旧：')
             + rel + ' 的「' + cl.text + '」实际 ' + n + ' 处，00c §5b 写的是 ' + row.n
             + '（' + row.kind + '）\n      ' + (n > row.n
                ? '新增的那几处是新违规'
                : '清掉了就更新这张表，清完了就勾掉那一步'));
    }
  });
});

/* ── 清完了要提醒勾步骤 ──────────────────────────────── */
Object.keys(base).forEach(function (k) {
  var row = base[k];
  if (row.seen === 0 && row.n !== null) {
    bad.push('★ ' + k.replace('|', ' 的 ') + ' 已经清干净了（' + row.kind
           + '）—— 去 00c 勾掉那一步并删掉这一行');
  }
});

/* ── 报告 ───────────────────────────────────────────── */
if (bad.length) {
  console.error('\n✗ 作废说法门禁：' + bad.length + ' 处\n');
  bad.forEach(function (b, n) { console.error('  ' + (n + 1) + ') ' + b); });
  console.error('');
  process.exit(1);
}
var debt = Object.keys(base).reduce(function (s, k) { return s + (base[k].n || 0); }, 0);
console.log('✓ test_retired_claims：' + claims.length + ' 条作废说法没复活；'
  + '已登记欠债 ' + debt + ' 处（扫了 ' + scanned + ' 个文件）');
