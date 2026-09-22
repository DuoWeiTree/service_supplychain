/* ============================================================
   store.js 库存推演 —— headless 自检

   ★ 为什么要有这个文件：
     `node --check` 只看语法。本项目已两次栽在「语法过了、变量没定义」上
     （submitPlan 的 demandAtSubmit、seed() 返回值被污染），
     两次都是**跑起来**才发现的。所以改完推演必须真调一次。

   跑法:  node test_forecast.js      （0 = 全绿）
   ============================================================ */
'use strict';

// ── localStorage 垫片 ─────────────────────────────────────────
var mem = {};
global.window = {
  localStorage: {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
    setItem: function (k, v) { mem[k] = String(v); },
    removeItem: function (k) { delete mem[k]; }
  }
};
global.localStorage = global.window.localStorage;
global.console.debug = function () {};      // 静音 dbg，保留 error

require('./assets/store.js');
var Store = global.window.Store;

var fails = [];
function ok(cond, name, detail) {
  if (cond) { console.log('  ✓ ' + name); }
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}
function eq(got, want, name) {
  ok(got === want, name, '实际 ' + JSON.stringify(got) + '，应为 ' + JSON.stringify(want));
}

console.log('\n── 1. 参数：默认值与边界 ──');
var P = Store.params();
eq(P.make_days, 30, '默认生产 30 天');
eq(P.ship_days, 60, '默认货运 60 天（海外海运）');
eq(P.total_days, 90, '全程 90 天');
ok(Array.isArray(Store.LEAD_PRESETS) && Store.LEAD_PRESETS.length >= 3, '有预设可选');

var bad = Store.setParams({ make_days: -1, ship_days: 60 });
ok(!bad.ok && bad.error === 'bad_make_days', '负天数被拒', JSON.stringify(bad));
ok(/-1/.test(bad.detail || ''), '★ 拒绝时点名了收到的值', bad.detail);
bad = Store.setParams({ make_days: 30, ship_days: 'abc' });
ok(!bad.ok && bad.error === 'bad_ship_days', '非数字被拒', JSON.stringify(bad));
eq(Store.params().make_days, 30, '★ 被拒后参数没被改坏');

console.log('\n── 2. 天 → 月：国内环境必须落在下个月 ──');
// ★ 锚在月中（15 号）：计划只说「10 月下单」，没说几号；
//   真实下单日在当月大致均匀分布，期望值就是月中。
eq(Store.monthAfterDays('2026-10', 30), '2026-11', '★ 10 月下单 + 生产 30 天 → 11 月完工');
eq(Store.monthAfterDays('2026-10', 90), '2027-01', '10 月 +90 天 → 次年 1 月');
eq(Store.monthAfterDays('2026-10', 3), '2026-10', '★ 国内调拨 3 天 → 当月就到');
eq(Store.monthAfterDays('2026-10', 0), '2026-10', '+0 天 → 当月');
eq(Store.monthAfterDays('2026-12', 90), '2027-03', '跨年正确');

console.log('\n── 3. 建计划、加货品、填数、推演 ──');
// ★ 第一版这里挑到了一张**空计划**，于是第 4/5 节「因为没数据所以全绿」——
//   正是「写完先把它弄失败一次」要防的那种假绿。现在改成：挑不到就自己加货品。
var plans = (Store.get().plans || []).filter(function (p) { return !(p.revs || []).length; });
ok(plans.length > 0, '剧本里有可编辑的计划', '一张都没有');
// ★ 用我们自己那张空计划（种子里第二张是「别人的计划」，不该动）
var plan = plans.filter(function (p) { return !(p.claims || []).length; })[0] || plans[0];

// ★ 必须挑**跨多个店铺**的货号 —— 只有一个店铺的话，分配逻辑等于没被测到
var hit = Store.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
var group = {};
(hit.rows || []).filter(function (r) { return r.selectable; })
  .forEach(function (r) { (group[r.sku] = group[r.sku] || []).push(r); });
var multiSku = Object.keys(group).filter(function (k) {
  var sids = {}; group[k].forEach(function (r) { sids[r.seller_id] = 1; });
  return Object.keys(sids).length >= 2;
})[0];
ok(!!multiSku, '目录里有跨店铺的货号', '一个都没有 —— 分配逻辑将无法被测到');

var picks = (group[multiSku] || []).map(function (r) {
  return { seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid };
});
var addRes = Store.addClaims(plan.plan_id, picks);
ok(addRes.ok && addRes.added > 0, '加上了货品（' + multiSku + '，' + picks.length + ' 个 msku）',
   JSON.stringify({ matched: hit.matched, res: addRes }));
plan = Store.plan(plan.plan_id);

var bySku = {};
(plan.claims || []).forEach(function (c) {
  (bySku[c.sku] = bySku[c.sku] || {})[c.seller_id] = true;
});
var sku = multiSku;
ok(!!sku, '计划里有货号', 'claims 是空的');
ok(sku && Object.keys(bySku[sku]).length >= 2, '★ 选中的货号跨多个店铺（否则分配没被测到）',
   sku ? sku + ' 只有 ' + Object.keys(bySku[sku]).length + ' 个店铺' : '');

// ★ 填上期望销量 —— 全空的话推演一律标未知，等于什么都没算
var months = plan.periods || [];
(plan.claims || []).filter(function (c) { return c.sku === sku; }).forEach(function (c, i) {
  months.forEach(function (m, j) {
    Store.setDemand(plan.plan_id, c.seller_id, c.seller_sku, m, 40 + i * 7 + j * 3);
  });
});
plan = Store.plan(plan.plan_id);

var f = Store.forecastSku(plan.plan_id, sku);
ok(!!f, 'forecastSku 返回了结果');
ok(f && f.params && f.params.total_days === 90, '★ 结论回显了它用的参数',
   JSON.stringify(f && f.params));
ok(f && Array.isArray(f.mskus) && f.mskus.length > 0, '有 msku 行');
ok(f && Array.isArray(f.pipeline) && f.pipeline.length === f.periods.length, '管道逐月都有');

console.log('\n── 4. ★ 恒等式：期末 = 期初 − 期望销量 + 当期入库 ──');
var badRows = [];
f.mskus.forEach(function (m) {
  m.months.forEach(function (x) {
    if (x.unknown || x.closing === null) return;
    var want = x.opening - x.demand + x.inbound_confirmed + x.inbound_planned;
    if (x.closing !== want) {
      badRows.push(m.seller_sku + ' ' + x.period + ': ' + x.closing + ' ≠ ' + want);
    }
  });
});
ok(badRows.length === 0, '每一格都满足主公式', badRows.slice(0, 5).join('; '));

var rollover = [];
f.mskus.forEach(function (m) {
  for (var i = 1; i < m.months.length; i++) {
    var prev = m.months[i - 1], cur = m.months[i];
    if (prev.closing === null) break;
    if (cur.opening !== prev.closing) {
      rollover.push(m.seller_sku + ' ' + cur.period + ': 期初 ' + cur.opening + ' ≠ 上月期末 ' + prev.closing);
    }
  }
});
ok(rollover.length === 0, '★ 期初[m] ≡ 期末[m−1]', rollover.slice(0, 5).join('; '));

console.log('\n── 5. ★ 推算入库 ≡ 其构成明细之和（有结论必有依据）──');
var byCell = {};
(f.traces || []).forEach(function (t) {
  byCell[t.seller_sku + '|' + t.seller_id + '|' + t.arrive_month] =
    (byCell[t.seller_sku + '|' + t.seller_id + '|' + t.arrive_month] || 0) + t.units;
});
var traceBad = [];
f.mskus.forEach(function (m) {
  m.months.forEach(function (x) {
    var k = m.seller_sku + '|' + m.seller_id + '|' + x.period;
    var want = byCell[k] || 0;
    if (x.inbound_planned !== want) {
      traceBad.push(k + ': 推算入库 ' + x.inbound_planned + ' ≠ 明细合计 ' + want);
    }
  });
});
ok(traceBad.length === 0, '每一笔推算入库都能追到依据', traceBad.slice(0, 5).join('; '));
(f.traces || []).slice(0, 1).forEach(function (t) {
  ok(t.basis && t.basis.rule, '★ 依据里写明了用的哪条规则', JSON.stringify(t.basis));
});

console.log('\n── 6. ★ 改采购量，库存必须动（这是最初的那个 BUG）──');
var period0 = f.periods[0];
var before = Store.forecastSku(plan.plan_id, sku);
var sumBefore = before.mskus.reduce(function (t, m) {
  return t + m.months.reduce(function (u, x) { return u + (x.closing || 0); }, 0); }, 0);
var r = Store.setPurchase(plan.plan_id, sku, period0, 5000);
ok(r.ok, 'setPurchase 成功', JSON.stringify(r));
ok(!!r.arrives_at && !!r.made_at, '★ 回执告诉了「几月完工、几月可售」', JSON.stringify(r));
var after = Store.forecastSku(plan.plan_id, sku);
var sumAfter = after.mskus.reduce(function (t, m) {
  return t + m.months.reduce(function (u, x) { return u + (x.closing || 0); }, 0); }, 0);
var moved = sumAfter !== sumBefore;
var landsInWindow = after.periods.indexOf(r.arrives_at) >= 0;
ok(moved === landsInWindow,
   landsInWindow ? '★ 采购量涨了 5000，库存跟着动了' : '★ 到货落在周期外，库存不动且已列入 beyond',
   '到货月 ' + r.arrives_at + '，周期内=' + landsInWindow + '，库存变化=' + (sumAfter - sumBefore));
if (!landsInWindow) {
  ok((after.beyond || []).some(function (b) { return b.order_month === period0; }),
     '★ 周期外的采购被显式列出，没有悄悄丢掉', JSON.stringify(after.beyond));
}

console.log('\n── 7. ★ 切国内环境：同一笔采购提前到货 ──');
Store.setParams({ make_days: 30, ship_days: 3 });
var dom = Store.forecastSku(plan.plan_id, sku);
eq(dom.params.total_days, 33, '全程变成 33 天');
var arrSea = after.pipeline.filter(function (x) { return x.period === period0; })[0].arrives_at;
var arrDom = dom.pipeline.filter(function (x) { return x.period === period0; })[0].arrives_at;
ok(arrDom < arrSea, '★ 国内环境下到货月提前了', '海运 ' + arrSea + ' → 国内 ' + arrDom);

console.log('\n── 8. ★ 分配合计 ≡ 这批货（管道不许漏货）──');
// ★ 这里必须用「到货落在计划周期内」的参数，否则一笔都不会分配，
//   下面几条断言会**因为没数据而全绿**。
//   默认 30+60=90 天配 3 个月的计划周期，采购一律落在周期外 ——
//   这本身是个产品问题（见报告），但测试不能拿它当通过。
Store.setParams({ make_days: 30, ship_days: 3 });   // 国内调拨：当月就到
var f3 = Store.forecastSku(plan.plan_id, sku);
ok(f3.periods.indexOf(f3.pipeline[0].arrives_at) >= 0,
   '★ 前置条件：这组参数下采购确实落在周期内',
   '到货月 ' + f3.pipeline[0].arrives_at + ' 不在 ' + f3.periods.join(','));
var perBatch = {};
(f3.traces || []).forEach(function (t) {
  var k = (t.order_month || '现货') + '→' + t.arrive_month;
  perBatch[k] = (perBatch[k] || 0) + t.units;
});
var batchBad = [];
Object.keys(perBatch).forEach(function (k) {
  var om = k.split('→')[0];
  if (om === '现货') return;
  var cell = f3.pipeline.filter(function (x) { return x.period === om; })[0];
  if (cell && cell.purchase && perBatch[k] !== cell.purchase) {
    batchBad.push(k + ': 分出去 ' + perBatch[k] + ' ≠ 采购 ' + cell.purchase);
  }
});
ok(batchBad.length === 0, '每批货分配合计等于它自己', batchBad.join('; '));

// ★ 非空断言：上面几节全是「遍历后没发现问题」型的，遍历到 0 行也会绿。
//   本项目已三次栽在「测试看起来过了、其实没测到目标」上，这里堵住。
ok((f3.traces || []).length > 0, '★ 确实有分配发生（否则第 5/8 节是空转）',
   'traces 是空的 —— 上面的绿是假的');
var plannedTotal = 0;
f3.mskus.forEach(function (m) {
  m.months.forEach(function (x) { plannedTotal += x.inbound_planned; });
});
ok(plannedTotal > 0, '★ 确实有「按计划推算」的入库量', '全是 0 —— 采购量还是没进库存');
var sellersHit = {};
(f3.traces || []).forEach(function (t) { sellersHit[t.seller_id] = 1; });
ok(Object.keys(sellersHit).length >= 2, '★ 货确实分给了不止一个店铺',
   '只分到了 ' + Object.keys(sellersHit).length + ' 个店铺');

console.log('\n── 9. ★ 计划周期也是变量 ──');
eq(Store.params().plan_months, 3, '默认计划周期 3 个月');
var bad2 = Store.setPlanMonths(plan.plan_id, 0);
ok(!bad2.ok && bad2.error === 'bad_months', '0 个月被拒', JSON.stringify(bad2));
bad2 = Store.setPlanMonths(plan.plan_id, 3.5);
ok(!bad2.ok && bad2.error === 'bad_months', '小数被拒', JSON.stringify(bad2));

// ★ 拉长：新月份必须长出格子，且能填
var longer = Store.setPlanMonths(plan.plan_id, 9);
ok(longer.ok && longer.periods.length === 9, '拉长到 9 个月', JSON.stringify(longer).slice(0, 160));
eq(longer.dropped.length, 0, '★ 拉长不该丢任何东西');
var p9 = Store.plan(plan.plan_id);
eq(p9.periods.length, 9, '计划上的 periods 跟着变了');
var newMonth = p9.periods[7];
var setNew = Store.setPurchase(plan.plan_id, sku, newMonth, 700);
ok(setNew.ok, '★ 新长出来的月份能填计划采购量（' + newMonth + '）', JSON.stringify(setNew));

var f9 = Store.forecastSku(plan.plan_id, sku);
eq(f9.pipeline.length, 9, '推演跟着变成 9 个月');

// ★ 这才是拉长的意义：90 天的采购终于能落在周期内
Store.setParams({ make_days: 30, ship_days: 60 });
var f9b = Store.forecastSku(plan.plan_id, sku);
Store.setPurchase(plan.plan_id, sku, f9b.periods[0], 900);
f9b = Store.forecastSku(plan.plan_id, sku);
ok(f9b.periods.indexOf(f9b.pipeline[0].arrives_at) >= 0,
   '★ 9 个月周期下，默认 90 天的采购落在周期内了（' + f9b.pipeline[0].arrives_at + '）',
   '还是落在周期外：' + f9b.pipeline[0].arrives_at + ' 不在 ' + f9b.periods.join(','));
var planned9 = 0;
f9b.mskus.forEach(function (m) { m.months.forEach(function (x) { planned9 += x.inbound_planned; }); });
ok(planned9 > 0, '★ 而且真的变成了某些店铺的入库量', '推算入库合计还是 0');

// ★ 系统预估只有 3 个月，后面的必须**标明是外推**，不许冒充预估
var p9b = Store.plan(plan.plan_id);
var later = Object.keys(p9b.cells_demand).map(function (k) { return p9b.cells_demand[k]; })
  .filter(function (c) { return c.period === p9b.periods[6]; });
ok(later.length > 0, '第 7 个月有期望销量格子', '一个都没有');
ok(later.every(function (c) { return c.system_extrapolated === true; }),
   '★ 超出系统预估范围的月份被标成外推', JSON.stringify(later[0]));
var early = Object.keys(p9b.cells_demand).map(function (k) { return p9b.cells_demand[k]; })
  .filter(function (c) { return c.period === p9b.periods[0]; });
ok(early.every(function (c) { return c.system_extrapolated === false; }),
   '★ 前 3 个月不是外推（否则这个标记等于恒真，白标）', JSON.stringify(early[0]));

// ★ 出处必须跟着数走：页面渲染的是 forecastSku 的返回值，
//   标记只挂在原格子上的话，页面就得回头读格子 —— 同一个数两个来源，迟早分叉。
var fx = Store.forecastSku(plan.plan_id, sku);
var m0 = fx.mskus[0];
ok(m0.months[0].system_extrapolated === false &&
   m0.months[6].system_extrapolated === true,
   '★ 外推标记随推演结果一起返回（不用回头读格子）',
   '第 1 月=' + m0.months[0].system_extrapolated + '，第 7 月=' + m0.months[6].system_extrapolated);

// ★ 缩短：丢掉的必须逐项点名，不许静默清空
var shorter = Store.setPlanMonths(plan.plan_id, 3);
ok(shorter.ok && shorter.periods.length === 3, '缩回 3 个月');
ok(shorter.dropped.length > 0, '★ 缩短把填过的格子点名列出来了（不是静默清空）',
   'dropped 是空的 —— 但我们刚在第 8 个月填过 700');
ok(shorter.dropped.some(function (d) { return /700/.test(d); }),
   '★ 点名里能看到具体丢了哪个数', JSON.stringify(shorter.dropped));
eq(Store.plan(plan.plan_id).periods.length, 3, '格子表跟着缩回去了');

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败：\n  - ' + fails.join('\n  - ')
                                 : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
