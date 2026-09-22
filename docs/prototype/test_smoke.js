/* ============================================================
   全页装载烟测

   ★ 为什么要有这个文件：
     十个页面里只有五个有专属测试。另外五个（index / trace / ops /
     plan-add / plan-rev）**从来没有人验过它们加载时会不会抛** ——
     而页面脚本一抛，剩下的就不执行了，屏幕上只剩静态骨架。
     ★ 「渲染到一半就停了」和「这里本来就没内容」长得一模一样。

   判据：每一页都要
     ① 装载期 0 个未预期错误
     ② 主体容器里**真的有内容**（不是空壳）

   跑法:  node test_smoke.js       依赖 jsdom（没装就跳过，不冒充通过）
   ============================================================ */
'use strict';

var fs = require('fs');
var path = require('path');
var vm = require('vm');

var JSDOM, VirtualConsole;
try {
  var j = require('jsdom'); JSDOM = j.JSDOM; VirtualConsole = j.VirtualConsole;
} catch (e) {
  console.log('⚠ 没有 jsdom，这个测试跳过（不是通过）。装法：npm install jsdom');
  process.exit(0);
}

var DIR = __dirname;
var fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

/* ── 造一份**有数据**的状态：空剧本下每页都是空状态，等于什么都没测 ── */
function seed() {
  var mem = {};
  var ls = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
    setItem: function (k, v) { mem[k] = String(v); }, removeItem: function (k) { delete mem[k]; }
  };
  var sb = { window: { localStorage: ls }, localStorage: ls,
             console: Object.assign({}, console, { debug: function () {}, error: function () {} }) };
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(DIR, 'assets/store.js'), 'utf8'), sb);
  var S = sb.window.Store;

  var plan = (S.get().plans || []).filter(function (p) { return !(p.claims || []).length; })[0];
  var hit = S.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
  var g = {};
  (hit.rows || []).filter(function (r) { return r.selectable; })
    .forEach(function (r) { (g[r.sku] = g[r.sku] || []).push(r); });
  var sku = Object.keys(g).filter(function (k) {
    var t = {}; g[k].forEach(function (r) { t[r.seller_id] = 1; });
    return Object.keys(t).length >= 2;
  })[0];
  S.addClaims(plan.plan_id, g[sku].map(function (r) {
    return { seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid };
  }));
  var p2 = S.plan(plan.plan_id);
  (p2.claims || []).forEach(function (c, i) {
    p2.periods.forEach(function (m, k) {
      S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, m, 40 + i * 8 + k * 4);
    });
  });
  S.setPurchase(p2.plan_id, sku, p2.periods[0], 300);
  S.submitPlan(p2.plan_id);

  // 走到有采购单、有到货、有排货表 —— 后面几页才有东西可渲染
  var po = S.createPo({ supplier: '东莞宠具', wid: S.get().warehouses[0].wid,
                        opt_uid: '8801', purchaser_id: '2001' });
  var cand = S.poCandidates({ sku: sku }).rows.filter(function (r) { return r.line_id && r.open > 0; });
  S.addPoItems(po.po_id, cand.slice(0, 2).map(function (r) {
    return { line_id: r.line_id, seller_id: r.seller_id, units: Math.min(20, r.open), price: 12 };
  }));
  S.placePo(po.po_id);
  for (var d = 0; d < 3; d++) S.tick();
  var sheet = S.createSheet();
  return { json: mem['scm_proto'], plan_id: p2.plan_id, po_id: po.po_id, sheet_id: sheet.sheet_id };
}

var st = seed();
console.log('\n── 0. 种子 ──');
ok(!!st.json && st.plan_id && st.po_id && st.sheet_id,
   '★ 造出了有数据的剧本（空剧本下每页都是空状态，等于什么都没测）',
   JSON.stringify({ plan: st.plan_id, po: st.po_id, sheet: st.sheet_id }));

/* ── 逐页装载 ───────────────────────────────────────────────── */
var PAGES = [
  { f: 'index.html',     q: '',                          host: 'body' },
  { f: 'ops.html',       q: '',                          host: 'body' },
  { f: 'plan.html',      q: '?plan=' + st.plan_id,       host: '#blocks' },
  { f: 'plan-add.html',  q: '?plan=' + st.plan_id,       host: 'body' },
  { f: 'plan-rev.html',  q: '?plan=' + st.plan_id,       host: 'body' },
  { f: 'buyer.html',     q: '',                          host: 'body' },
  { f: 'po.html',        q: '?po=' + st.po_id,           host: 'body' },
  { f: 'dispatcher.html', q: '',                         host: 'body' },
  { f: 'sheet.html',     q: '?id=' + st.sheet_id,        host: 'body' },
  { f: 'trace.html',     q: '',                          host: 'body' }
];

console.log('\n── 1. 每一页：0 个未预期错误 + 主体真的有内容 ──');
PAGES.forEach(function (pg) {
  var errs = [];
  var vc = new VirtualConsole();
  vc.on('jsdomError', function (e) { errs.push('jsdomError: ' + (e.message || e)); });
  vc.on('error', function () {
    var s = Array.prototype.slice.call(arguments).map(String).join(' ');
    // 这些是**页面主动报出来的**诊断，不是崩溃 —— 它们有声是对的
    if (/cells-backfilled|no-geometry|rejected|schema-stale|missing-doc-no/.test(s)) return;
    errs.push(s);
  });

  var dom = new JSDOM(fs.readFileSync(path.join(DIR, pg.f), 'utf8'), {
    runScripts: 'outside-only', url: 'http://localhost/' + pg.f + pg.q,
    virtualConsole: vc, pretendToBeVisual: true
  });
  var w = dom.window, doc = w.document;
  w.localStorage.setItem('scm_proto', st.json);
  w.scrollTo = function () {};

  function run(code, label) {
    try { w.eval(code); } catch (e) { errs.push(label + ' 抛出: ' + (e && e.message)); }
  }
  Array.prototype.forEach.call(doc.querySelectorAll('script[src]'), function (sc) {
    var p = path.join(DIR, sc.getAttribute('src'));
    if (!fs.existsSync(p)) { errs.push('缺少脚本 ' + sc.getAttribute('src')); return; }
    run(fs.readFileSync(p, 'utf8'), sc.getAttribute('src'));
  });
  Array.prototype.forEach.call(doc.querySelectorAll('script:not([src])'), function (sc, i) {
    run(sc.textContent, '内联脚本 #' + i);
  });

  var host = doc.querySelector(pg.host);
  var text = host ? host.textContent.replace(/\s+/g, '') : '';
  var shell = doc.querySelector('.shell__bar');

  ok(errs.length === 0, pg.f + '：装载无错', errs.slice(0, 3).join('\n      '));
  ok(text.length > 120, pg.f + '：主体有内容（不是空壳）',
     '只有 ' + text.length + ' 个字符 —— 脚本可能在渲染前就停了');
  ok(!!shell, pg.f + '：★ 顶栏在（十页顶栏必须是同一个）',
     '没有 .shell__bar —— 这一页自己搭了顶栏？');
});

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败' : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
