/* ============================================================
   采购员工作台 —— Store 层自检

   ★ 核心裁定（负责人 2026-09-18）：**「看的时候分店，下单的时候合并」**
     候选按 `计划记录 × 店铺` 展开给人看，勾选后按货号合并成采购单一行。

   ★ 这条能成立的关键：**店铺归属是人勾出来的，不是系统摊的。**
     所以 `已下单量[店铺]` 是事实，不是分摊假设 —— 这也是它和库存预测里
     那个「未定向量」的根本区别。

   跑法:  node test_buyer.js      （0 = 全绿）
   ============================================================ */
'use strict';

var mem = {};
global.window = {
  localStorage: {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
    setItem: function (k, v) { mem[k] = String(v); },
    removeItem: function (k) { delete mem[k]; }
  }
};
global.localStorage = global.window.localStorage;
global.console.debug = function () {};

require('./assets/store.js');
var Store = global.window.Store;

var fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}
function eq(got, want, name) {
  ok(got === want, name, '实际 ' + JSON.stringify(got) + '，应为 ' + JSON.stringify(want));
}

/* ── 0. 造一张已提交的计划，否则一条候选都没有 ───────────────── */
console.log('\n── 0. 前置：提交一张计划 ──');
var plan = (Store.get().plans || []).filter(function (p) { return !(p.claims || []).length; })[0];
var hit = Store.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
var group = {};
(hit.rows || []).filter(function (r) { return r.selectable; })
  .forEach(function (r) { (group[r.sku] = group[r.sku] || []).push(r); });
var sku = Object.keys(group).filter(function (k) {
  var s = {}; group[k].forEach(function (r) { s[r.seller_id] = 1; });
  return Object.keys(s).length >= 2;
})[0];
ok(!!sku, '目录里有跨店铺的货号（否则「分店看」测不到）');

Store.addClaims(plan.plan_id, group[sku].map(function (r) {
  return { seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid };
}));
plan = Store.plan(plan.plan_id);
var period = plan.periods[0];
// ★ 每个店铺填不同的期望销量，才看得出「分店」是不是真的分对了
var perSeller = {};
plan.claims.filter(function (c) { return c.sku === sku; }).forEach(function (c, i) {
  var v = 40 + i * 20;
  Store.setDemand(plan.plan_id, c.seller_id, c.seller_sku, period, v);
  perSeller[c.seller_id] = (perSeller[c.seller_id] || 0) + v;
});
Store.setPurchase(plan.plan_id, sku, period, 200);
var sub = Store.submitPlan(plan.plan_id);
ok(sub.ok, '计划提交成功', JSON.stringify(sub).slice(0, 200));

var line = Store.lines().filter(function (l) { return l.sku === sku && l.period === period; })[0];
ok(!!line, '铸出了计划记录');
ok(line && line.seller_id === null, '★ 计划记录**不带店铺**（P2 保持不变）',
   '实际 seller_id = ' + JSON.stringify(line && line.seller_id));
ok(line && Object.keys(line.demand_by_seller || {}).length >= 2,
   '★ 但冻结了各店期望销量（分店看的依据）', JSON.stringify(line && line.demand_by_seller));
// ★ null 是有含义的合法值，不是查找失败 —— 拿它去查店铺不该报错。
//   原来每渲染一条计划记录就吼一声 seller.unknown，
//   真正的「店铺号写错了」反而被淹没在噪音里。
eq(Store.sellerName(null), '不分店铺', '★ 没有店铺的记录有自己的说法，不是「未知店铺(null)」');
ok(/未知店铺/.test(Store.sellerName('9999999')), '★ 真的写错店铺号时仍然报未知（两头都验）',
   Store.sellerName('9999999'));

/* ── 1. 候选：按店铺展开 ─────────────────────────────────────── */
console.log('\n── 1. 候选按「计划记录 × 店铺」展开 ──');
var c1 = Store.poCandidates({ sku: sku });
var mine = c1.rows.filter(function (r) { return r.line_id === line.line_id; });
ok(mine.length >= 2, '★ 一条记录展开成了多个店铺行', '只有 ' + mine.length + ' 行');
ok(mine.every(function (r) { return r.seller_name && r.seller_name !== r.seller_id; }),
   '每行带店铺名（不是光秃秃的 id）', JSON.stringify(mine[0]));

var g1 = c1.groups.filter(function (g) { return g.line_id === line.line_id; })[0];
ok(!!g1, '有货号合计行');
eq(g1.demand_total, mine.reduce(function (t, r) { return t + r.demand; }, 0),
   '★ 合计 ≡ 各店铺行求和（不独立算一遍）');
eq(g1.purchase_planned, 200, '★ 合计行另外显示「计划采购量」');
ok(g1.purchase_planned !== g1.demand_total,
   '★ 计划采购量 与 期望销量合计 是两个数，界面必须都给',
   '这次恰好相等了，换组数再测：' + g1.purchase_planned + ' vs ' + g1.demand_total);

/* ── 2. 建单 → 加货品 ────────────────────────────────────────── */
console.log('\n── 2. 新建空单（弹窗）→ 挑货品 ──');
var bad = Store.createPo({ supplier: '', wid: 1, opt_uid: 'u1', purchaser_id: 'p1' });
ok(!bad.ok && bad.error === 'missing_supplier', '供应商必填被拦在建单时', JSON.stringify(bad));
bad = Store.createPo({ supplier: 'S', wid: 1, opt_uid: 'u1' });
ok(!bad.ok && bad.error === 'missing_purchaser_id',
   '★ 领星必填项缺了就在建单时炸，不等下单吃 500', JSON.stringify(bad));

var wid = Store.get().warehouses[0].wid;
var made = Store.createPo({ supplier: '东莞宠具', wid: wid, opt_uid: '8801', purchaser_id: '2001' });
ok(made.ok, '建出空采购单', JSON.stringify(made));
var poId = made.po_id;
eq(Store.po(poId).items.length, 0, '★ 建出来就是空的（先定供应商/收货仓，再挑货）');

var s0 = mine[0], s1 = mine[1];
var add = Store.addPoItems(poId, [
  { line_id: line.line_id, seller_id: s0.seller_id, units: 25, price: 12.02 }
]);
ok(add.ok && add.added === 1, '加了一项', JSON.stringify(add));

var c2 = Store.poCandidates({ sku: sku });
var r0 = c2.rows.filter(function (r) { return r.line_id === line.line_id && r.seller_id === s0.seller_id; })[0];
var rOther = c2.rows.filter(function (r) { return r.line_id === line.line_id && r.seller_id === s1.seller_id; })[0];
eq(r0.drafted, 25, '★ 该店铺的「已组单」涨了 25');
eq(r0.open, s0.demand - 25, '★ 该店铺的「未组单」跟着降');
eq(rOther.open, s1.demand, '★★ 另一个店铺**没被动过**（不是按比例摊的）');

var g2 = c2.groups.filter(function (g) { return g.line_id === line.line_id; })[0];
eq(g2.open, c2.rows.filter(function (r) { return r.line_id === line.line_id; })
  .reduce(function (t, r) { return t + r.open; }, 0),
  '★ 恒等式：Σ_店铺 未组单 ≡ 货号级未组单');

/* ── 3. 超量必须被拒，且说清为什么 ───────────────────────────── */
console.log('\n── 3. 超量 ──');
var over = Store.addPoItems(poId, [
  { line_id: line.line_id, seller_id: s0.seller_id, units: 9999, price: 1 }
]);
ok(over.ok && over.rejected.length === 1, '超量那项被拒（其余仍可加）', JSON.stringify(over));
ok(/未组单/.test(over.rejected[0]) && /9999/.test(over.rejected[0]),
   '★ 拒绝时点名了「本次多少 / 还剩多少」', over.rejected[0]);
eq(Store.po(poId).items.length, 1, '被拒的没混进去');

/* ── 4. 改量 / 移除 —— 退回边必须真的退得回去（P9）────────────── */
console.log('\n── 4. 改量与移除 ──');
var setr = Store.setPoItem(poId, 0, { units: 10 });
ok(setr.ok, '改量成功', JSON.stringify(setr));
eq(Store.poCandidates({ sku: sku }).rows.filter(function (r) {
  return r.line_id === line.line_id && r.seller_id === s0.seller_id; })[0].open,
  s0.demand - 10, '★ 改小之后未组单跟着回来');

var rm = Store.removePoItem(poId, 0);
ok(rm.ok && rm.left === 0, '移除成功', JSON.stringify(rm));
eq(Store.poCandidates({ sku: sku }).rows.filter(function (r) {
  return r.line_id === line.line_id && r.seller_id === s0.seller_id; })[0].open,
  s0.demand, '★★ 移除后完全退回「未组单」—— 画得出的退回边真的退得回去');

/* ── 5. 下单后锁死 ───────────────────────────────────────────── */
console.log('\n── 5. 下单 ──');
Store.addPoItems(poId, [
  { line_id: line.line_id, seller_id: s0.seller_id, units: 20, price: 12.02 },
  { line_id: line.line_id, seller_id: s1.seller_id, units: 15, price: 12.02 }
]);
eq(Store.po(poId).items.length, 2, '★ 两个店铺的份，是两行（下单时可按货号合并展示）');
var placed = Store.placePo(poId);
ok(placed.ok && placed.order_sn, '下单成功，拿到领星单号', JSON.stringify(placed));

var c3 = Store.poCandidates({ sku: sku });
var r3 = c3.rows.filter(function (r) { return r.line_id === line.line_id && r.seller_id === s0.seller_id; })[0];
eq(r3.placed, 20, '★ 已下单量按店铺归属（人勾出来的，所以是事实）');
eq(r3.drafted, 0, '已组单转成了已下单');

var lockAdd = Store.addPoItems(poId, [{ line_id: line.line_id, seller_id: s0.seller_id, units: 1, price: 1 }]);
ok(!lockAdd.ok && lockAdd.error === 'po_locked', '下单后不能再加货品', JSON.stringify(lockAdd));
var lockRm = Store.removePoItem(poId, 0);
ok(!lockRm.ok && lockRm.error === 'po_locked', '★ 下单后不能移除（货可能在做、钱可能已付）',
   JSON.stringify(lockRm));
ok(/货可能在做/.test(lockRm.detail || ''), '★ 拒绝时说清了为什么，不是一句「不允许」', lockRm.detail);

/* ── 6. 到货进度 ─────────────────────────────────────────────── */
console.log('\n── 6. 本地仓到货进度 ──');
var prog = Store.poProgress(poId);
ok(!!prog, 'poProgress 有返回');
eq(prog.total, 35, '总量 = 各行之和');
eq(prog.arrived, 0, '刚下单，还没到货');
ok(prog.lx_status !== undefined, '带领星状态（镜像，我们不写）', JSON.stringify(prog.lx_status));

// ★ 推进到到货，验单号真的取得到。
//   我第一版把字段名写成了 receipt_no，而写入方用的是 doc_no —— 取不到就是 undefined，
//   而且不吭声。「单号空着」和「这批货没单号」在界面上长得一模一样。
for (var d = 0; d < 4; d++) Store.tick();
var prog2 = Store.poProgress(poId);
ok(prog2.receipts.length > 0, '★ 前置条件：确实到货了（否则下面几条是空转）',
   '到了 ' + prog2.arrived + ' 件，收货记录 ' + prog2.receipts.length + ' 条');
ok(prog2.receipts.every(function (r) { return !!r.receipt_no; }),
   '★ 每条入库记录都有单号（字段名要以写入方为准）',
   JSON.stringify(prog2.receipts.slice(0, 2)));
ok(prog2.receipts.every(function (r) { return !!r.at && !!r.warehouse; }),
   '★ 入库日期和仓库名也取得到', JSON.stringify(prog2.receipts[0]));
ok(prog2.arrived > 0 && prog2.pct > 0, '到货进度算出来了',
   prog2.arrived + '/' + prog2.total + ' = ' + prog2.pct + '%');

/* ── 7. 销售计划查询 ─────────────────────────────────────────── */
console.log('\n── 7. 销售计划查询 ──');
var pq = Store.planQuery({});
ok(pq.length > 0, '查得到计划');
var me = pq.filter(function (p) { return p.plan_id === plan.plan_id; })[0];
ok(!!me && me.state === '已提交', '刚提交的那张状态是「已提交」', JSON.stringify(me));
ok(me && me.rev, '带版本号', JSON.stringify(me));
var drafts = Store.planQuery({ state: '草稿' });
ok(drafts.every(function (p) { return p.state === '草稿'; }), '按状态过滤生效');
ok(drafts.length < pq.length, '★ 过滤真的滤掉了东西（否则这条断言是空转）',
   '全部 ' + pq.length + ' 张，草稿 ' + drafts.length + ' 张');

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败：\n  - ' + fails.join('\n  - ')
                                 : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
