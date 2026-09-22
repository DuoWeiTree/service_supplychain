/* test_doc_index.js —— `00e` §5.2 的文档地图必须覆盖 docs/ 下每一份文档。

   ★ 为什么需要它：`00e` 是实施时的导航（「这个问题谁说了算」）。
     导航漏一份，那一份的权威范围就无人知晓 —— 于是下一个人**自己编一个**。
     而漏登记**不会有任何东西失败**。

   ★ 判据是**双向等式**：
       docs/ 里有、地图里没有  → 红（新文档没登记）
       地图里有、docs/ 里没有  → 红（文档删了或改名，地图在骗人）
*/
'use strict';
var fs = require('fs'), path = require('path');

var DOCS = path.join(__dirname, '..');
var PLAN = '00e-分阶段实施计划.md';

var plan = fs.readFileSync(path.join(DOCS, PLAN), 'utf8');
var i = plan.indexOf('### 5.2');
var j = plan.indexOf('### 5.3', i);
if (i < 0 || j < 0) {
  console.error('✗ 00e 里找不到 §5.2 文档地图 —— 它是实施导航的唯一依据，删了等于关掉门禁');
  process.exit(1);
}

/* ── 地图里登记了哪些 ─────────────────────────────────── */
var mapped = {};
plan.slice(i, j).split('\n').forEach(function (ln) {
  if (ln.indexOf('|') !== 0) { return; }
  var c = ln.split('|').slice(1, -1).map(function (x) { return x.trim(); });
  if (c.length < 4 || c[0] === '文档') { return; }
  var m = c[0].match(/`([^`]+\.md)`/);
  if (!m) { return; }
  /* ★ 权威范围那一列不许空 —— 登记了却不说管什么，等于没登记 */
  if (!c[2] || c[2] === '—') {
    console.error('✗ 地图里 ' + m[1] + ' 没写「对什么说了算」—— 登记而不说管什么，等于没登记');
    process.exit(1);
  }
  mapped[m[1]] = true;
});
if (!Object.keys(mapped).length) {
  console.error('✗ §5.2 解析出 0 行 —— 解析坏了，不是「没有文档」');
  process.exit(1);
}

/* ── docs/ 下实际有哪些 ───────────────────────────────── */
var actual = fs.readdirSync(DOCS).filter(function (f) {
  return /\.md$/.test(f) && fs.statSync(path.join(DOCS, f)).isFile();
});

var bad = [];
actual.forEach(function (f) {
  if (!mapped[f]) {
    bad.push('★ ' + f + ' 在 docs/ 里，但 00e §5.2 的地图**没登记它**\n'
           + '      —— 实施时没人知道它对什么说了算，于是会自己编一个');
  }
});
Object.keys(mapped).forEach(function (f) {
  if (actual.indexOf(f) < 0) {
    bad.push('★ 地图登记了 ' + f + '，但 docs/ 下**没有这个文件**\n'
           + '      —— 导航指向不存在的地方，比没有导航更坏');
  }
});

if (bad.length) {
  console.error('\n✗ 文档地图门禁：' + bad.length + ' 处\n');
  bad.forEach(function (b, n) { console.error('  ' + (n + 1) + ') ' + b); });
  console.error('\n★ 加了文档就去 00e §5.2 登记，并写清它**对什么说了算**。\n');
  process.exit(1);
}
console.log('✓ test_doc_index：' + actual.length + ' 份文档全部在地图里，且各自写明了权威范围');
