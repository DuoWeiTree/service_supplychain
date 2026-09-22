/* ============================================================
   排货员工作台 —— Store 层自检

   ★ 线框上负责人那句红字是整块设计的依据：
     **「基于采购计划操作，这样会让人更容易理解」**
     所以排货网格和计划网格**同构** —— 店铺·货号 × 月，每格摆着计划的那几个数，
     外加一个「安排发货」。三个角色共用同一个心智模型。

   ★ 店铺是在**排货这一步**才定的（裁定 C4）。

   跑法:  node test_dispatch.js
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

/* ── 0. 造一张已提交的计划 ───────────────────────────────────── */
console.log('\n── 0. 前置 ──');
var plan = (Store.get().plans || []).filter(function (p) { return !(p.claims || []).length; })[0];
var hit = Store.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
var group = {};
(hit.rows || []).filter(function (r) { return r.selectable; })
  .forEach(function (r) { (group[r.sku] = group[r.sku] || []).push(r); });
var sku = Object.keys(group).filter(function (k) {
  var t = {}; group[k].forEach(function (r) { t[r.seller_id] = 1; });
  return Object.keys(t).length >= 2;
})[0];
Store.addClaims(plan.plan_id, group[sku].map(function (r) {
  return { seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid };
}));
plan = Store.plan(plan.plan_id);
var period = plan.periods[0];
plan.claims.filter(function (c) { return c.sku === sku; }).forEach(function (c, i) {
  plan.periods.forEach(function (m, j) {
    Store.setDemand(plan.plan_id, c.seller_id, c.seller_sku, m, 40 + i * 10 + j * 5);
  });
});
// ★ 每个月都填，否则后面「跨月重复安排」那一节没有第二个月的计划记录可用
plan.periods.forEach(function (m) { Store.setPurchase(plan.plan_id, sku, m, 300); });
ok(Store.submitPlan(plan.plan_id).ok, '计划已提交');

/* ── 1. 销售计划完成度 ───────────────────────────────────────── */
console.log('\n── 1. 销售计划完成度（首页）──');
var pp = Store.planProgress();
ok(pp.rows.length > 0, '有计划行', '一行都没有');
var me = pp.rows.filter(function (r) { return r.plan_id === plan.plan_id; })[0];
ok(!!me, '找得到刚提交那张', JSON.stringify(pp.rows.map(function (r) { return r.plan_id; })));
ok(me.demand > 0, '★ 需求量是真数（否则下面比例全是 0/0）', '需求量 ' + me.demand);
eq(me.arrived, 0, '还没生产入库');
eq(me.pct, 0, '比例 0%');
ok(me.skus >= 1, '货品数算得出', '' + me.skus);
eq(pp.demand_total, pp.rows.reduce(function (t, r) { return t + r.demand; }, 0),
   '★ 总计 ≡ 各行求和（不独立算一遍）');

/* ── 2. 排货网格与计划网格同构 ───────────────────────────────── */
console.log('\n── 2. 排货网格（与计划网格同构）──');
var g = Store.dispatchGrid(null);
var rows = g.filter(function (r) { return r.sku === sku; });
ok(rows.length >= 2, '★ 同一货号按店铺展开成多行', '只有 ' + rows.length + ' 行');
var row = rows[0];
eq(row.cells.length, plan.periods.length, '列数 = 计划周期月数');
var c0 = row.cells[0];
ok(c0.system !== undefined && c0.demand !== undefined && c0.closing !== undefined
   && c0.purchase !== undefined,
   '★ 每格带齐四个数：销售预估 / 期望销量 / 库存预估 / 计划采购', JSON.stringify(c0));
ok(c0.demand !== null, '★ 期望销量不是空的（否则下面安排发货是空转）', JSON.stringify(c0));
ok(c0.line_id, '★ 格子指得到计划记录（安排发货要靠它）', JSON.stringify(c0));

/* ── 3. 本地仓货源按货号过滤 ─────────────────────────────────── */
console.log('\n── 3. 本地仓货源 ──');
// ★ 种子里本地仓初值全是 0，这是**刻意的**：必须先下单、先到货才排得了货。
//   所以这里要走完整链路，而不是往库存里塞一个数 ——
//   塞数能让测试变绿，但绿的是一条现实中不存在的路。
ok(Store.localSources(sku).length === 0, '★ 起点：本地仓一件都没有（种子刻意为 0）',
   '居然有货 —— 种子被改了？');

var po = Store.createPo({ supplier: '东莞宠具', wid: Store.get().warehouses[0].wid,
                          opt_uid: '8801', purchaser_id: '2001' });
ok(po.ok, '建出采购单');
var cand = Store.poCandidates({ sku: sku }).rows
  .filter(function (r) { return r.line_id && r.open > 0; });
ok(cand.length > 0, '有可组单的候选', '一条都没有');
Store.addPoItems(po.po_id, cand.slice(0, 2).map(function (r) {
  return { line_id: r.line_id, seller_id: r.seller_id, units: Math.min(20, r.open), price: 12 };
}));
ok(Store.placePo(po.po_id).ok, '已下单');
for (var d = 0; d < 3; d++) Store.tick();     // 下单后第 2 天部分到货

var src = Store.localSources(sku);
ok(src.length > 0, '★ 到货之后本地仓才有货（走的是真实链路，不是塞数）',
   '到货了却没进本地仓 —— bumpStock 没生效？');
ok(src.every(function (r) { return r.sku === sku; }), '★ 只返回这个货号的仓（自动过滤）');
ok(src.every(function (r) { return r.valid > 0 || r.locked > 0; }), '零库存的仓不占位');

/* ── 4. 安排发货 ─────────────────────────────────────────────── */
console.log('\n── 4. 安排发货 ──');
var made = Store.createSheet();
ok(made.ok, '建出排货表');
var sheetId = made.sheet_id;

var noLine = Store.arrangeShipment(sheetId, { seller_id: row.seller_id, picks: [{ wid: src[0].wid, units: 1 }] });
ok(!noLine.ok && noLine.error === 'no_line', '没有计划记录的格子排不了', JSON.stringify(noLine));
ok(/回销售计划补上/.test(noLine.detail || ''), '★ 拒绝时给了出路，不是一句「不行」', noLine.detail);

var noSeller = Store.arrangeShipment(sheetId, { line_id: c0.line_id, picks: [{ wid: src[0].wid, units: 1 }] });
ok(!noSeller.ok && noSeller.error === 'no_seller', '★ 不说发给哪个店铺就排不了', JSON.stringify(noSeller));

var overStock = Store.arrangeShipment(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: src[0].wid, units: 999999 }]
});
ok(!overStock.ok, '超量被拒', JSON.stringify(overStock));
ok(overStock.rejected && overStock.rejected.length === 2,
   '★★ 两条限制都撞到了，就两条都报 —— 不让人玩打地鼠',
   '只报了 ' + JSON.stringify(overStock.rejected));
ok(/待排量/.test(overStock.detail) && /只能安排/.test(overStock.detail),
   '★ 「还能排多少」和「仓里有多少」都点名了', overStock.detail);

var take = Math.min(5, src[0].valid);
var arr = Store.arrangeShipment(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: src[0].wid, units: take }]
});
ok(arr.ok && arr.units === take, '安排成功', JSON.stringify(arr));

var g2 = Store.dispatchGrid(sheetId);
var row2 = g2.filter(function (r) { return r.sku === sku && r.seller_id === row.seller_id; })[0];
eq(row2.cells[0].arranged, take, '★ 这一格显示「已安排 N 件」');
var other = g2.filter(function (r) { return r.sku === sku && r.seller_id !== row.seller_id; })[0];
eq(other.cells[0].arranged, 0, '★★ 另一个店铺那格没被动过（店铺是这一步才定的）');

/* ── 4b. ★★ 共享池：同一批库存不许被安排两次 ─────────────────────
   负责人 2026-09-18 实测报的 bug：
     「货品库存只有 14 个，10 月安排了 10 个，11 月还能再安排 10 个 ——
       按理来说 10 月安排后只剩 4 个，但 11 月能安排的仍然是 14 个。」
   根因：valid 只在**投送**那一刻才扣，从「安排发货」到「投送」这段窗口里，
   这批货物理上还在仓里、账上却已经有主了，而没有任何地方把它减掉。 */
console.log('\n── 4b. ★★ 共享池不许被重复承诺 ──');
var wid0 = src[0].wid;
var s0 = Store.localSources(sku).filter(function (r) { return r.wid === wid0; })[0];
var physical = s0.valid, free0 = s0.free;
ok(free0 >= 4, '★ 前置：这个仓现在还能安排几件（否则下面是空转）',
   '物理 ' + physical + '，已被占 ' + s0.committed + '，还能安排 ' + free0);

// ★ 跨月：两个月份各自安排一次，打的是同一批物理库存
var m0 = row.cells[0], m1 = row.cells[1] || row.cells[0];
ok(m0.line_id && m1.line_id && m0.period !== m1.period,
   '★ 前置：拿到了两个不同月份的计划记录（这才是负责人报的那个场景）',
   m0.period + ' / ' + m1.period);
var first = free0 - 2;
var a1 = Store.arrangeShipment(sheetId, {
  line_id: m0.line_id, seller_id: row.seller_id, picks: [{ wid: wid0, units: first }]
});
ok(a1.ok, '第一次安排 ' + first + ' 件成功', JSON.stringify(a1));

var after1 = Store.localSources(sku).filter(function (r) { return r.wid === wid0; })[0];
eq(after1.valid, physical, '★ 物理库存没变（还没发货，货还在仓里）');
eq(after1.committed, s0.committed + first, '★ 但「已被别处安排」记上了');
eq(after1.free, 2, '★★ 可安排量降到了 2 —— 这才是 bug 的修复点');

// ★ 换一个月再安排，不许再拿到整个 physical
var a2 = Store.arrangeShipment(sheetId, {
  line_id: m1.line_id, seller_id: row.seller_id, picks: [{ wid: wid0, units: free0 }]
});
ok(!a2.ok, '★★ ' + m1.period + ' 想再安排 ' + free0 + ' 件 —— 被拒（修复前这里会放行）',
   JSON.stringify(a2));
ok(/已被别处安排/.test(a2.detail || ''), '★ 拒绝时说清了「谁占掉的」', a2.detail);

// ★ 反向：剩下的那点还是能安排的（不能一刀切全挡）
var a3 = Store.arrangeShipment(sheetId, {
  line_id: m1.line_id, seller_id: row.seller_id, picks: [{ wid: wid0, units: 2 }]
});
ok(a3.ok, '★ 剩下的 2 件仍然排得了（两头都验，不能一刀切全挡）', JSON.stringify(a3));
eq(Store.localSources(sku).filter(function (r) { return r.wid === wid0; })[0].free, 0,
   '★ 排完之后可安排量归零');

// 清干净，不影响后面几节
Store.unarrangeShipment(sheetId, m0.line_id, row.seller_id);
Store.unarrangeShipment(sheetId, m1.line_id, row.seller_id);
// ★ 撤的是「格子」= (计划记录 × 店铺)，所以这一格上**全部**的安排都退了 ——
//   包括第 4 节先安排的那 5 件（它们在同一格上）。所以这里回到的是物理量。
eq(Store.localSources(sku).filter(function (r) { return r.wid === wid0; })[0].free, physical,
   '★ 撤回之后可安排量全部还回来（撤的是整格，不是单次）');

// ★ 把状态还原成进这一节之前的样子 —— 第 5 节要靠第 4 节安排的那 take 件。
//   ★ 一节测试改动了共享状态就得自己收拾干净，否则后面那节红了，
//     人会去查那一节的代码，而真因在这里。
Store.arrangeShipment(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: src[0].wid, units: take }]
});
eq(Store.dispatchGrid(sheetId).filter(function (r) {
  return r.sku === sku && r.seller_id === row.seller_id; })[0].cells[0].arranged,
  take, '★ 已还原到第 4 节结束时的状态');

/* ── 4c. ★★ 「安排 / 改 / 撤回」是同一个原语 ─────────────────────
   负责人 2026-09-18：「又没有撤销？又不能修改！」
   ★ 改数量原来要两步（撤回 → 重排）。收成 setArrangement 之后，
     一次操作**整体决定**这个格子的安排：
       安排 = 从空替换成 N ·  改 = 从 N 替换成 M ·  撤回 = 替换成空
     三件事同一条校验、同一条日志、同一条回退边。 */
console.log('\n── 4c. ★★ 安排 / 改 / 撤回 收成一个原语 ──');
var cellOf = function () {
  return Store.dispatchGrid(sheetId).filter(function (r) {
    return r.sku === sku && r.seller_id === row.seller_id; })[0].cells[0].arranged;
};
var was = cellOf();
var w2 = src[0].wid;

var set1 = Store.setArrangement(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: w2, units: 6 }] });
ok(set1.ok && set1.units === 6 && set1.was === was,
   '★ 整体替换：回执同时给「原来多少」和「现在多少」', JSON.stringify(set1));
eq(cellOf(), 6, '格子上确实变成 6');

// ★ 改小 —— 修复前这里会被**自己占的量**挡住
var set2 = Store.setArrangement(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: w2, units: 3 }] });
ok(set2.ok, '★★ 改小成功（自己占的量不能算进「别人占的」）', JSON.stringify(set2));
eq(cellOf(), 3, '格子上变成 3');

// ★ 改大到超出可安排量 —— 仍然要拒，且说清谁占掉的
var freeNow = Store.localSources(sku).filter(function (r) { return r.wid === w2; })[0].free;
var set3 = Store.setArrangement(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: w2, units: freeNow + 3 + 99 }] });
ok(!set3.ok, '★ 改大到超量仍然被拒（不能因为是「改」就放行）', JSON.stringify(set3));
eq(cellOf(), 3, '★ 被拒之后格子没被改坏');

// ★ 撤回 = 替换成空，走同一条路
var set4 = Store.setArrangement(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [] });
ok(set4.ok && set4.units === 0, '★ 撤回 = 替换成空', JSON.stringify(set4));
eq(cellOf(), 0, '格子归零');

// 还原成第 4 节结束时的样子，别毒化第 5 节
Store.setArrangement(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: w2, units: was }] });
eq(cellOf(), was, '★ 已还原');

/* ── 5. 撤回 —— 画得出的退回边必须真的退得回去 ───────────────── */
console.log('\n── 5. 撤回 ──');
var undo = Store.unarrangeShipment(sheetId, c0.line_id, row.seller_id);
ok(undo.ok && undo.released === take, '撤回成功', JSON.stringify(undo));
eq(Store.dispatchGrid(sheetId).filter(function (r) {
  return r.sku === sku && r.seller_id === row.seller_id; })[0].cells[0].arranged,
  0, '★★ 撤回后这一格归零，量退回待排');
var undo2 = Store.unarrangeShipment(sheetId, c0.line_id, row.seller_id);
ok(!undo2.ok && undo2.error === 'nothing_to_undo', '重复撤回被拒且说清原因', JSON.stringify(undo2));

/* ── 5b. ★★ 编入排货单时，店铺归属不许丢、歧义不许猜 ─────────────
   排货**就是**「给货指定店铺」的那一步（裁定 C4）。
   原来 moveToConsignment 按 (line_id, from_wid, sku) 匹配、不看 seller_id，
   于是同一条记录下两个店铺从同一个仓各安排一笔就撞成同键，代码取第一条 ——
   ★ 货会被编给错的店，而界面上两行长得一模一样，没有任何东西会说破。 */
console.log('\n── 5b. ★★ 编入排货单：店铺跟着货走 ──');
var sellerA = row.seller_id;
var sellerB = (rows.filter(function (r) { return r.seller_id !== sellerA; })[0] || {}).seller_id;
ok(!!sellerB, '★ 前置：拿到了第二个店铺（否则歧义这条测不到）');

/* ★ 这一节要建排货单，而第 6 节要求 SHEET-01 以「建了没编排」的形态出现 ——
   所以**另开一张表**，不要在共用的那张上留痕。
   ★ 我第一版就在共用表上建了单，第 6 节当场红了：
     一节测试改动了共享状态就得自己收拾干净，否则后面那节红了，
     人会去查那一节的代码，而真因在这里。（这句话我上一节自己写过，然后又犯了。） */
var sheet5b = Store.createSheet().sheet_id;
Store.setArrangement(sheet5b, { line_id: c0.line_id, seller_id: sellerA,
  picks: [{ wid: src[0].wid, units: 3 }] });
Store.setArrangement(sheet5b, { line_id: c0.line_id, seller_id: sellerB,
  picks: [{ wid: src[0].wid, units: 4 }] });
var consB = Store.createConsignment(sheet5b, { channel: 'fba', dest: { sid: '11201', fc: 'ONT8' } });
ok(consB.ok, '建出一张排货单（★ 在**另一张**排货表上，不污染 SHEET-01）');

// ★ 不说店铺 → 两行都对得上 → 必须拒，不许挑一条
var amb = Store.moveToConsignment(sheet5b, consB.cid,
  { line_id: c0.line_id, from_wid: src[0].wid, units: 2 });
ok(!amb.ok && amb.error === 'pool_entry_ambiguous',
   '★★ 不说是哪个店铺就编入 —— 被拒（修复前会静默取第一条）', JSON.stringify(amb));
ok(/件/.test(amb.detail) && amb.detail.split('、').length >= 2,
   '★ 拒绝时把两个候选都点名了', amb.detail);

// ★ 说清店铺 → 成功，且店铺跟着货进了排货单行
var okMove = Store.moveToConsignment(sheet5b, consB.cid,
  { line_id: c0.line_id, from_wid: src[0].wid, seller_id: sellerB, units: 4 });
ok(okMove.ok, '说清店铺就能编入', JSON.stringify(okMove));
var lineB = Store.sheet(sheet5b).consignments
  .filter(function (c) { return String(c.cid) === String(consB.cid); })[0].lines[okMove.idx];
eq(String(lineB.seller_id), String(sellerB), '★★ 排货单行带上了店铺（归属没在半路丢掉）');
eq(lineB.units, 4, '数量对');

// ★ 反向：另一个店铺那笔没被动过
var poolA = Store.sheet(sheet5b).pool.filter(function (p) {
  return p.line_id === c0.line_id && String(p.seller_id) === String(sellerA); });
eq(sum2(poolA), 3, '★ 另一个店铺那 3 件原封不动（两头都验）');
function sum2(a) { return a.reduce(function (t, x) { return t + x.units; }, 0); }

// 清干净
Store.setArrangement(sheet5b, { line_id: c0.line_id, seller_id: sellerA, picks: [] });
Store.setArrangement(sheet5b, { line_id: c0.line_id, seller_id: sellerB, picks: [] });

/* ── 5c. ★★ 「已安排」拆成两个状态 ───────────────────────────────
   原来格子上的 arranged 只数待编排池 —— 一编入排货单就归零、按钮变回
   「安排发货」。★ **「还没排过」和「排了且已编进单」长得一模一样**，
   而两者的处置完全相反：前者去安排，后者要去排货单里动。 */
console.log('\n── 5c. ★★ 待编排 vs 已编入，不许压成一个数 ──');
var sh5c = Store.createSheet().sheet_id;
Store.setArrangement(sh5c, { line_id: c0.line_id, seller_id: sellerA,
  picks: [{ wid: src[0].wid, units: 5 }] });
var g5c = function () {
  return Store.dispatchGrid(sh5c).filter(function (r) {
    return r.sku === sku && r.seller_id === sellerA; })[0].cells[0];
};
var before5c = g5c();
eq(before5c.pooled, 5, '★ 前置：安排了 5 件，都在待编排池里');
eq(before5c.consigned, 0, '还没编入任何排货单');
eq(before5c.arranged, 5, '合计 5');

// ★ 用海外仓那条链：FBA 首投**刻意**会部分成功（断点续跑的靶子，另有测试守）。
//   这一节测的是格子状态，不该被那个场景搅进来。
var cons5c = Store.createConsignment(sh5c, { channel: 'oversea',
  dest: { wid: Store.get().warehouses[0].wid } });
Store.moveToConsignment(sh5c, cons5c.cid,
  { line_id: c0.line_id, from_wid: src[0].wid, seller_id: sellerA, units: 5 });
var after5c = g5c();
eq(after5c.pooled, 0, '池子空了（货挪走了）');
eq(after5c.consigned, 5, '★★ 但「已编入排货单 5 件」看得见 —— 修复前这里整格归零');
eq(after5c.arranged, 5, '★★ 合计仍然是 5 —— 这一格**动过**，不是「还没排过」');

// ★ 反向：真的没排过的格子，三个数都是 0（否则「恒非零」也能绿）
var virgin = Store.dispatchGrid(sh5c).filter(function (r) {
  return r.sku === sku && r.seller_id === sellerB; })[0].cells[0];
eq(virgin.arranged, 0, '★ 没排过的格子合计是 0（两头都验）');
eq(virgin.pooled, 0, '池里也是 0');
eq(virgin.dispatched, 0, '也没投送过');

// ★ 投送之后：不能再退回「从没排过」的样子 —— 同一个病的第四种触发点
// ★ 走真实链路：确认闸要求装箱数据齐全。
//   海外仓不需要货件归属，只要装箱数据。
Store.setLineBox(sh5c, cons5c.cid, 0, { count: 1, l: 40, w: 30, h: 20, weight: 5, per: 5 });
var conf = Store.confirmSheet(sh5c);
ok(conf.ok, '★ 前置：排货表过了确认闸', JSON.stringify(conf).slice(0, 200));
var dis = Store.dispatch(cons5c.cid);     // ★ dispatch 只收 cid
ok(dis.ok, '★ 前置：投送成功（前置失败的话下面那条会假绿）',
   JSON.stringify(dis).slice(0, 160));
var after = g5c();
// ★ 必须验 dispatched 本身 —— 只验「consigned 或 dispatched 有一个非零」的话，
//   投送根本没跑成时 consigned 还在，断言照样绿。前置失败 → 断言假绿。
ok(after.dispatched === 5, '★★ 投送之后「已投送 5 件」看得见 —— 不许回到「从没排过」的样子',
   JSON.stringify({ pooled: after.pooled, consigned: after.consigned,
                    dispatched: after.dispatched, arranged: after.arranged }));
eq(after.consigned, 0, '★ 而「待编入排货单」清零了（货已经出手）');

/* ── 5d. ★★ FBA 货件号由投送取回，不是人填的 ─────────────────────
   负责人 2026-09-19：
     「先创建货件后，才能关联货件！你现在是让我提供 FBA 的货件编码！怎么可能有呢！
       这是要去 FBA 创建后、读取回来信息才能知道。货件号都没创建，怎么关联？」

   ★ 文档早就写清了（docs/02 §488、docs/07 §337）：
     货件号在 confirmPlacementOption 之后才返回，不轮询 taskId 就不知道。
   ★ 原来的错：确认闸 G2 在**投送之前**就要求每行有 shipment_no ——
     那一刻这个号根本不存在，于是界面长出一个没人填得出来的输入框。
   ★ A-6 说的是「**发货前**必须已归属货件」，而发货是投送链的最后一步。 */
console.log('\n── 5d. ★★ 货件号：系统取回，不是人填 ──');
var shF = Store.createSheet().sheet_id;
Store.setArrangement(shF, { line_id: c0.line_id, seller_id: sellerA,
  picks: [{ wid: src[0].wid, units: 2 }] });
var consF = Store.createConsignment(shF, { channel: 'fba', dest: { sid: '11072', fc: 'FBA9' } });
Store.moveToConsignment(shF, consF.cid,
  { line_id: c0.line_id, from_wid: src[0].wid, seller_id: sellerA, units: 2 });
Store.setLineBox(shF, consF.cid, 0, { count: 1, l: 40, w: 30, h: 20, weight: 5, per: 2 });

var lineF = function () {
  return Store.sheet(shF).consignments
    .filter(function (c) { return String(c.cid) === String(consF.cid); })[0].lines[0];
};
eq(lineF().shipment_no, '', '★ 前置：还没投送，行上没有货件号（本来就不该有）');

// ★★ 确认闸不许再要求货件号 —— 那是在要一个还不存在的东西
var confF = Store.confirmSheet(shF);
ok(confF.ok, '★★ 没填货件号也能过确认闸（修复前这里会卡死，而没人填得出来）',
   JSON.stringify(confF).slice(0, 260));

// ★ 但「建货件需要的东西」还是要齐 —— G2 现在查的是这个
var shBad = Store.createSheet().sheet_id;
Store.setArrangement(shBad, { line_id: c0.line_id, seller_id: sellerA,
  picks: [{ wid: src[0].wid, units: 1 }] });
var consBad = Store.createConsignment(shBad, { channel: 'fba', dest: { sid: '11072', fc: 'FBA9' } });
Store.moveToConsignment(shBad, consBad.cid,
  { line_id: c0.line_id, from_wid: src[0].wid, seller_id: sellerA, units: 1 });
Store.setLineBox(shBad, consBad.cid, 0, { count: 1, l: 1, w: 1, h: 1, weight: 1, per: 1 });
Store.sheet(shBad).consignments[0].dest = { sid: '11072' };      // ★ 故意把 FC 抹掉
var confBad = Store.confirmSheet(shBad);
ok(!confBad.ok && /FC/.test(JSON.stringify(confBad)),
   '★ 反向：目的地不全（缺 FC）仍然过不了闸 —— G2 不是被删掉了，是换了该查的东西',
   JSON.stringify(confBad).slice(0, 200));

// ★★ 投送之后，货件号由系统回填到行上
var disF = Store.dispatch(consF.cid);
var noF = lineF().shipment_no;
if (!disF.ok) { Store.dispatch(consF.cid); noF = lineF().shipment_no; }   // FBA 首投可能部分成功
ok(/^FBA/.test(noF || ''), '★★ 投送之后行上有了真的货件号（系统取回并回填）',
   '实际 ' + JSON.stringify(noF));
var refs = Store.sheet(shF).consignments
  .filter(function (c) { return String(c.cid) === String(consF.cid); })[0].ext_refs;
ok(refs.some(function (r) { return r.kind === 'shipment' && r.doc_no === noF; }),
   '★ 行上的号和 ext_refs 里那个是同一个（不是各记各的）',
   JSON.stringify(refs));

/* ── 6. 排货单列表 ───────────────────────────────────────────── */
console.log('\n── 6. 排货单列表 ──');
var list = Store.sheetList({});
ok(list.length > 0, '列表有行');
var empty = list.filter(function (r) { return r.sheet_id === sheetId && r.pooled_only; })[0];
ok(!!empty, '★ 空排货表也出现在列表里（「建了没编排」≠「没建过」）',
   JSON.stringify(list.map(function (r) { return r.sheet_id + '/' + r.cid; })));

Store.arrangeShipment(sheetId, {
  line_id: c0.line_id, seller_id: row.seller_id, picks: [{ wid: src[0].wid, units: take }]
});
// ★ 我第一版传的是 dest_wid —— store 只读 opt.dest，于是**静默变成 {}**，
//   一路到界面才显示成「店铺（缺）· FC（缺）」。而我那条断言只验了
//   「不是 undefined」，刚好放过。★ 断言弱到能放过 bug，等于没有断言。
var badDest = Store.createConsignment(sheetId, { channel: 'fba', dest_wid: 'FBA-US' });
ok(!badDest.ok && badDest.error === 'bad_dest',
   '★★ 传错字段名（dest_wid）当场被拒，不再静默吞成空目的地', JSON.stringify(badDest));
ok(/dest_wid/.test(badDest.detail || ''), '★ 拒绝时点名了「你写的是哪个字段」', badDest.detail);

var cons = Store.createConsignment(sheetId, { channel: 'fba', dest: { sid: '11201', fc: 'ONT8' } });
ok(cons.ok, '建出排货单', JSON.stringify(cons));
var list2 = Store.sheetList({});
var mine2 = list2.filter(function (r) { return r.sheet_id === sheetId && r.cid; })[0];
ok(!!mine2, '排货单进了列表');
eq(mine2.channel_label, 'FBA', '渠道显示成人话，不是 fba');
// ★ 目的地按渠道有两种形状：FBA 是 {sid, fc}，海外仓是 {wid}。
//   当成同一种读会得到 undefined，界面上只看到「—」，而且不吭声。
ok(/11201/.test(mine2.dest_label) && /ONT8/.test(mine2.dest_label),
   '★ 目的地标签是**真的那个值**（不是「（缺）」也不是 undefined）', mine2.dest_label);
// ★ 再建一张别的渠道，否则「过滤」无从证明滤掉了东西
Store.createConsignment(sheetId, { channel: 'oversea',
  dest: { wid: Store.get().warehouses[0].wid } });
var list3 = Store.sheetList({});
var fba = Store.sheetList({ channel: 'fba' });
ok(list3.length >= 2, '★ 前置：列表里确实有两种渠道', '只有 ' + list3.length + ' 行');
ok(fba.length < list3.length, '★ 按渠道过滤真的滤掉了东西（否则这条是空转）',
   '全部 ' + list3.length + '，FBA ' + fba.length);
var os = list3.filter(function (r) { return r.channel === 'oversea'; })[0];
ok(os && os.dest_label && !/undefined|（缺）/.test(os.dest_label),
   '★ 海外仓那张也读到真值（两种形状都验过才算数）', os && os.dest_label);

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败：\n  - ' + fails.join('\n  - ')
                                 : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
