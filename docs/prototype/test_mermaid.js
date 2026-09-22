/* test_mermaid.js —— 流程图里不许有悬空节点 / 没标签的节点。

   ★ 为什么需要它：2026-09-21 我改 `15` 的投送图时，把一个节点的标签删了、
     另一个节点接不上任何边，**没有任何东西失败** —— 而这些图正是经理和
     负责人真正在看的东西。图错了，评审过的就是错的。

   ★ 只管 flowchart / graph 块，**不碰时序图**（sequenceDiagram 的语法完全不同，
     硬套会得到一堆假失败 —— 第一版快扫就是这么错的：把 `create` `lock` `ship`
     这些时序消息名报成悬空节点）。

   检查两件事：
     ① 悬空：有标签却不出现在任何边上
     ② 无名：出现在边上却从没给过标签（mermaid 会渲染成一个裸 id，读图的人看不懂）
*/
'use strict';
var fs = require('fs'), path = require('path');

var DOCS = path.join(__dirname, '..');
var SKIP_DIR = 'html';                 // 整份作废，见 00c §5a
var ID = '[A-Za-z_][A-Za-z0-9_]*';
var bad = [], stat = { blocks: 0, nodes: 0, edges: 0 };

/* 把标签整段挖掉，只留骨架 —— 标签里什么字符都可能有 */
function strip(line) {
  var s = line;
  /* ★★ 先剥引号内的整段。2026-09-22 实测：`17` 的公式节点
        FB["… D̂[sku, node, t] … B(t) = max(0, B(t−1) …) … min{t : u(t) > 0}"]
     标签**内部**就有 `]` 与 `(`，而下面那些 `[^\]]*` 会在第一个内层 `]`
     提前收尾，剩下的公式文本被当成节点定义 —— 于是 max / min / u / A / B
     全被报成「悬空节点」，一次 10 处假失败。
     ★ 假失败的代价是有人把整条断言关掉，所以这里必须先剥引号。 */
  s = s.replace(/"[^"]*"/g, '""');
  s = s.replace(/\[\[[^\]]*\]\]/g, '[]').replace(/\[\([^\]]*\)\]/g, '[]')
       .replace(/\(\[[^\]]*\]\)/g, '()').replace(/\{\{[^}]*\}\}/g, '{}');
  s = s.replace(/\[[^\]]*\]/g, '[]').replace(/\([^)]*\)/g, '()').replace(/\{[^}]*\}/g, '{}');
  s = s.replace(/\|[^|]*\|/g, '|');                        // -->|文字|
  s = s.replace(/-\.[^.>]*\.->/g, '-.->');                 // -.文字.->
  s = s.replace(/==[^=>]*==>/g, '==>');                    // ==文字==>
  s = s.replace(/--\s[^->]*\s-->/g, '-->');                // -- 文字 -->
  return s;
}

var CONNECT = /-->|==>|-\.->|---|===|~~~/;

function lintBlock(file, idx, body) {
  var first = body.split('\n').map(function (x) { return x.trim(); })
                  .filter(Boolean)[0] || '';
  if (!/^(flowchart|graph)\b/.test(first)) { return; }      // 只管流程图
  stat.blocks++;

  var labeled = {}, subgraphs = {}, onEdge = {}, seen = {};

  body.split('\n').forEach(function (raw) {
    var t = raw.trim();
    if (!t || t.indexOf('%%') === 0) { return; }
    if (/^(end|direction|classDef|class|style|linkStyle|flowchart|graph)\b/.test(t)) { return; }

    if (/^subgraph\b/.test(t)) {
      var ms = t.match(new RegExp('^subgraph\\s+(' + ID + ')'));
      if (ms) { subgraphs[ms[1]] = 1; }
      return;
    }

    var s = strip(t);

    /* 带形状的定义 = 有标签 */
    var re = new RegExp('(' + ID + ')\\s*(?:\\[\\]|\\(\\)|\\{\\})', 'g'), m;
    while ((m = re.exec(s))) { labeled[m[1]] = 1; seen[m[1]] = 1; }

    /* 边：拆掉连接符，两端都是节点 */
    if (CONNECT.test(s)) {
      stat.edges++;
      s.split(/-->|==>|-\.->|---|===|~~~|\|/).forEach(function (part) {
        var mm = part.trim().match(new RegExp('^(' + ID + ')'));
        if (mm) { onEdge[mm[1]] = 1; seen[mm[1]] = 1; }
      });
    }
  });

  var where = file + ' 图#' + idx;
  Object.keys(labeled).forEach(function (n) {
    if (subgraphs[n] || onEdge[n]) { return; }
    bad.push('★ 悬空节点：' + where + ' 的 `' + n + '` 有标签但不接任何边'
           + '\n      —— 读图的人看不到它，改图时最容易漏掉的就是这种');
  });
  Object.keys(onEdge).forEach(function (n) {
    if (labeled[n] || subgraphs[n]) { return; }
    bad.push('★ 无名节点：' + where + ' 的 `' + n + '` 出现在边上却从没给标签'
           + '\n      —— mermaid 会渲染成一个裸 id');
  });
  stat.nodes += Object.keys(seen).length;
}

function walk(dir) {
  fs.readdirSync(dir, { withFileTypes: true }).forEach(function (e) {
    var p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name === '.git'
          || path.relative(DOCS, p) === SKIP_DIR) { return; }
      return walk(p);
    }
    if (!/\.md$/.test(e.name)) { return; }
    var body = fs.readFileSync(p, 'utf8');
    var rel = path.relative(DOCS, p).split(path.sep).join('/');
    var re = /```mermaid\n([\s\S]*?)```/g, m, i = 0;
    while ((m = re.exec(body))) { lintBlock(rel, i++, m[1]); }
  });
}
walk(DOCS);

if (bad.length) {
  console.error('\n✗ mermaid 门禁：' + bad.length + ' 处\n');
  bad.forEach(function (b, n) { console.error('  ' + (n + 1) + ') ' + b); });
  console.error('');
  process.exit(1);
}
console.log('✓ test_mermaid：' + stat.blocks + ' 个流程图 · '
  + stat.nodes + ' 个节点 · ' + stat.edges + ' 条边，无悬空、无无名');
