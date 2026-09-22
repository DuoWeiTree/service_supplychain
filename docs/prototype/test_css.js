/* ============================================================
   样式覆盖自检

   ★ 为什么要有这个文件：
     2026-09-18 重写 shell.css 时漏掉了 shell.js 生成的那一整批类名 ——
     顶栏、toast、确认弹窗、待办全是裸的。而**「样式没写」和「元素没渲染」
     在屏幕上长得一模一样**，两者都只是「那里什么都没有」。

     ★ 判据：**生成方用到的每个类名，样式表里都要有规则。**

   跑法:  node test_css.js
   ============================================================ */
'use strict';

var fs = require('fs');
var path = require('path');
var DIR = __dirname;

var fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

var css = fs.readFileSync(path.join(DIR, 'assets/shell.css'), 'utf8');
var js = fs.readFileSync(path.join(DIR, 'assets/shell.js'), 'utf8');

console.log('\n── 1. CSS 本身 ──');
var depth = 0, stray = 0;
for (var i = 0; i < css.length; i++) {
  if (css[i] === '{') depth++;
  else if (css[i] === '}') { depth--; if (depth < 0) { stray++; depth = 0; } }
}
ok(depth === 0 && stray === 0, '花括号平衡', 'depth=' + depth + ' 多余右括号=' + stray);
ok(!/composes\s*:/.test(css), '★ 没有 composes（那是 CSS Modules 语法，纯 CSS 里静默无效）');
ok(/--paper/.test(css) && /--vermilion/.test(css), '墨色变量在');

console.log('\n── 2. ★ shell.js 生成的类名，样式表里都要有 ──');
/* shell.js 用 el(tag, className, text) 造元素 —— 把第二个参数全抓出来 */
var used = {};
var re = /el\(\s*'[a-z]+'\s*,\s*'([^']+)'/g, m;
while ((m = re.exec(js))) {
  m[1].split(/\s+/).forEach(function (c) {
    c = c.replace(/[^A-Za-z0-9_-]+$/, '');   // 去掉拼接残留（如 `btn--sm' +`）
    if (c) used[c] = 1;
  });
}
/* 还有 classList / className 两种写法 */
var re2 = /class(?:List\.(?:add|remove|toggle)\(|Name\s*=\s*)'([^']+)'/g;
while ((m = re2.exec(js))) {
  m[1].split(/\s+/).forEach(function (c) { if (c) used[c] = 1; });
}

var names = Object.keys(used).sort();
ok(names.length > 20, '★ 前置：确实抓到了一批类名（否则下面是空转）',
   '只抓到 ' + names.length + ' 个：' + names.join(' '));

/* 类名可能带修饰后缀（btn--danger），只要基类或本身有规则就算覆盖 */
function covered(c) {
  if (css.indexOf('.' + c) >= 0) return true;
  var base = c.split('--')[0];
  return base !== c && css.indexOf('.' + base) >= 0;
}
var missing = names.filter(function (c) { return !covered(c); });
ok(missing.length === 0,
   '★ 每个类名都有样式（' + names.length + ' 个）',
   '缺样式：' + missing.join(' ') + '\n      —— 这些元素会渲染出来但没长相，'
   + '而「没样式」和「没渲染」在屏幕上分不出来');

console.log('\n── 2b. ★ 修饰类不许退回素面 ──');
/* ★ covered() 会因为基类 .chip 存在而放行 .chip--fail —— 于是「失败」和「通过」
   长得一模一样，而那正是 chip 存在的唯一理由。这一类遗漏只能靠列举修饰符本身。 */
/* ★ 第一版这里手列了一份组件名单（chip|btn|flash|todo|cell|gate__item）——
     结果漏了 toast，而 `.toast--fail` 全原型用了 30 处，
     **这个缺口是在「测试全绿」的状态下漏的**。
   ★ 手列的宇宙必漏第 N+1 种 —— 改成**推导**的：
     凡是基类已经有样式的，它的每个修饰都必须有样式。 */
var mods = {};
[js].concat(fs.readdirSync(DIR).filter(function (f) { return /\.html$/.test(f); })
  .map(function (f) { return fs.readFileSync(path.join(DIR, f), 'utf8'); }))
  .forEach(function (src) {
    var r = /\b([a-z][a-z0-9]*(?:__[a-z0-9-]+)?--[a-z0-9-]+)\b/g, x;
    while ((x = r.exec(src))) {
      var base = x[1].split('--')[0];
      // 只管「基类真的是个组件」的那些，否则会把 JS 里的 a--b 碎片拉进来
      if (css.indexOf('.' + base) >= 0) mods[x[1]] = 1;
    }
  });
var modNames = Object.keys(mods).sort();
ok(modNames.length >= 12, '★ 前置：抓到了一批修饰类（否则这节是空转）',
   '只抓到 ' + modNames.join(' '));
var modMissing = modNames.filter(function (c) { return css.indexOf('.' + c) < 0; });
ok(modMissing.length === 0, '★ 每个修饰类都有自己的规则（' + modNames.length + ' 个）',
   '退回素面：' + modMissing.join(' '));

console.log('\n── 3. ★ 页面里不许再有裸类名 ──');
var pages = fs.readdirSync(DIR).filter(function (f) { return /\.html$/.test(f); });
var pageMissing = [], skippedTotal = 0;
pages.forEach(function (f) {
  var html = fs.readFileSync(path.join(DIR, f), 'utf8');
  var seen = {}, skipped = 0;
  var r = /class="([^"]+)"/g, mm;
  while ((mm = r.exec(html))) {
    /* ★ 带模板拼接的 class 属性整条跳过 —— 里面的 token 是 JS 表达式碎片，
       当类名查会刷出一屏假失败。**而假失败的代价是有人把整条断言关掉。**
       ★ 但跳过了多少要报出来：静默丢弃正是这个项目一路在防的。 */
    if (/['\"`+(?:]/.test(mm[1])) { skipped++; continue; }
    mm[1].split(/\s+/).forEach(function (c) {
      /* ★ 只认**长得像类名**的 token。
         页面里有大量 `class="..."` 其实是 JS 模板字符串的一部分，
         里面混着 `+`、`?`、`(n`、`'i-ink')` 这类碎片 ——
         把它们当类名会刷出一屏假失败，而假失败会让人把整条断言关掉。 */
      if (!/^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$/.test(c)) return;
      seen[c] = 1;
    });
  }
  // 页面自己的 <style> 块也算数
  var own = (html.match(/<style[\s\S]*?<\/style>/g) || []).join('\n');
  Object.keys(seen).forEach(function (c) {
    if (covered(c) || own.indexOf('.' + c) >= 0) return;
    pageMissing.push(f + ' → .' + c);
  });
  skippedTotal += skipped;
});
console.log('  （' + skippedTotal + ' 处带模板拼接的 class 未检查 —— 它们的 token 是 JS 碎片）');
ok(pageMissing.length === 0, '★ 页面用到的类名也都有样式',
   pageMissing.slice(0, 20).join('\n      ')
   + (pageMissing.length > 20 ? '\n      …共 ' + pageMissing.length + ' 个' : ''));

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败' : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
