/* ============================================================
   plan.html 的时间位移线 —— 坐标自检

   ★ 为什么要有这个文件：
     位移线是这一版唯一的大胆之处，也是唯一**没有任何文字兜底**的东西 ——
     它一旦悄悄不画了，屏幕上什么都不会说。而
     **「线没画」和「没有单要画」长得一模一样**，两者都只是「那里什么都没有」。

     ★ 判据因此分两层：
       ① 先证明**这一轮确实有单要画线**（非空前置）——
          没有这一条，下面九项全绿也可能只是因为一条线都没有；
       ② 再验坐标：起点落在下单那一格、终点落在**到货那一格**、
          周期外的冲出右边缘且桩是朱红。

   ★ 两头都要验：只验「周期外冲出边缘」的话，「恒为 out」也能全绿 ——
     所以同一批里必须**既有周期内也有周期外**，且各自的条数对得上按参数算出来的数。

   ★ jsdom 不排版，getBoundingClientRect 全是 0 —— 页面**应该**一条都不画，
     并留下 shift.no-geometry 的 debug 痕。这一点本身要先验（第 0 节）：
     不验的话，「画对了」和「压根没跑起来」分不开。
     坐标那几节靠喂一套**假的几何**来跑。

   跑法:  node test_shift.js
   依赖:  jsdom（没装会告诉你怎么装，并以「跳过」退出，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据（2026-09-18）

   | 把 plan.html 故意改成                    | 转红的断言                    |
   |-----------------------------------------|------------------------------|
   | refreshBlock 末尾不再调 drawShifts        | 「各一条线」×1                |
   | resize 那个 setTimeout 换成空操作          | 「resize 之后自己长回来」×1     |
   | 终点 x2 一律取 right-2（不看到货格）        | 「终点落在到货那一格」×1        |
   | `inside` 恒真                            | 「朱红正好是那 N 条」等 ×8      |
   | `inside` 恒假                            | 「朱红正好是那 N 条」等 ×3      |

   ★ 前两行是**一对，不能省掉任何一个**：只用 resize 触发的话，
     「refreshBlock 忘了调 drawShifts」打不红任何一条 —— 而改 refreshBlock 的人
     远比改 resize 的人多，那正是最可能发生的回归。
   ★ 后两行也是**一对**：两个方向都打红「既有朱红也有非朱红」那条，
     说明红的原因是 inside 这个字段本身，不是连坐。只验一头的话，
     「恒为 out」能让「周期外冲出边缘」全绿 —— 而那时候**每一条**都冲出边缘了。
     （`inside` 恒真会连坐到 8 条：到货格子不存在 → 那条线被跳过、还打了
       shift.to-missing，于是「各一条线」和「没有未预期的错误」一起红。
       连坐是真的，但靶子也确实中了。）

   ★ 回放这张表时**必须先校验变异真的落到文件里**。这一节的证据被同一形态
     骗过两次：
       一次是 from 串写错，命中 0 次 → 全绿，绿的原因是变异没生效；
       一次是替换串里的转义没生效，注进去把内联脚本打成语法错 → 整页没渲染，
         红的是「网格长出 3 个块」这种无关断言，**目标断言反而没红**。
     ★ 「变异没生效」「页面炸了」「断言够强」三者在红/绿上长得一模一样。
       回放器要自己把它们分开：from 命中数 ≠ 1 就拒绝出分，
       变异后内联脚本 `node --check` 不过也拒绝出分。
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
const fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

/* ── 造状态：一个货号、6 个月、**每个月都填计划采购量** ──
   提前期刻意压短（10+20=30 天），这样前几个月的到货落在周期内、
   末尾的必然落在周期外 —— 一次跑里两种形态都有，才验得了两头。 */
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
  const sku = Object.keys(group)[0];
  S.addClaims(plan.plan_id, group[sku].map(r =>
    ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid })));
  S.setPlanMonths(plan.plan_id, 6);
  S.setParams({ make_days: 10, ship_days: 20 });
  const p2 = S.plan(plan.plan_id);
  p2.periods.forEach(m => S.setPurchase(p2.plan_id, sku, m, 100));
  return { json: mem['scm_proto'], planId: p2.plan_id, sku, periods: p2.periods.slice() };
}
const seeded = seedState();

/* ── 装载页面 ── */
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.stack || e.message)));
vc.on('error', (...a) => {
  const s = a.map(String).join(' ');
  if (/cells-backfilled/.test(s)) return;   // 装载期回填提示，良性
  errors.push('console.error: ' + s);
});

const dom = new JSDOM(fs.readFileSync(path.join(DIR, 'plan.html'), 'utf8'), {
  runScripts: 'outside-only',
  url: 'http://localhost/plan.html?plan=' + seeded.planId,
  virtualConsole: vc, pretendToBeVisual: true
});
const w = dom.window, doc = w.document;
w.scrollTo = function () {};
w.localStorage.setItem('scm_proto', seeded.json);

function run(code, label) {
  try { w.eval(code); }
  catch (e) { errors.push('★ ' + label + ' 抛出: ' + (e.stack || e)); }
}
[...doc.querySelectorAll('script[src]')].forEach(sc => {
  const p = path.join(DIR, sc.getAttribute('src'));
  if (!fs.existsSync(p)) { errors.push('缺少脚本 ' + sc.getAttribute('src')); return; }
  run(fs.readFileSync(p, 'utf8'), sc.getAttribute('src'));
});
[...doc.querySelectorAll('script:not([src])')].forEach((sc, i) =>
  run(sc.textContent, '内联脚本 #' + i));

/* ── 0. ★ 非空前置：这一轮到底有没有单要画线 ──
   ★ 没有这一节，下面每一条都可能是空转 —— 一条线都没有的时候，
     「条数对得上」「周期外是朱红」全都会自动成立。 */
console.log('\n── 0. ★ 前置：确实有单要画线 ──');
const params = w.Store.params();
const want = seeded.periods.filter(p => {
  const c = w.Store.plan(seeded.planId).cells_purchase[seeded.sku + '|' + p];
  return c && c.purchase;
});
const inPeriod = want.filter(p =>
  seeded.periods.indexOf(w.Store.monthAfterDays(p, params.total_days)) >= 0);
const outPeriod = want.filter(p =>
  seeded.periods.indexOf(w.Store.monthAfterDays(p, params.total_days)) < 0);

ok(want.length === 6, '★ 6 个月全都填了计划采购量（有 6 条线要画）',
   '只有 ' + want.length + ' 个月有数 —— 下面全是空转');
ok(inPeriod.length > 0 && outPeriod.length > 0,
   '★ 这一批里**既有**周期内到货（' + inPeriod.length + ' 条）**也有**周期外（' + outPeriod.length + ' 条）',
   '只有一种形态 —— 那「恒为 out」或「恒不为 out」都能全绿，两头就没验成');
ok(!errors.length, '装载没有未预期的错误', errors.slice(0, 2).join(' | '));

/* ── 1. ★ 没有布局时：不画，而不是画在 (0,0) ── */
console.log('\n── 1. 没有布局时（jsdom 不排版）──');
ok(doc.querySelectorAll('.shift').length === 0,
   '★ 一条线都没画 —— 坐标算不出来就不画，不是画一堆压在左上角',
   '画了 ' + doc.querySelectorAll('.shift').length + ' 条：坐标是假的？');

/* ── 喂一套假几何 ──
   列宽 100，第一列从 x=200 起；下单行 y=[40,70)，到货行 y=[70,100)。
   ★ 只给这三类元素真尺寸，别的仍返回 0 —— 免得页面靠别的元素蒙对。 */
const COL = 100, X0 = 200, RIGHT = X0 + COL * seeded.periods.length;
const idxOf = p => seeded.periods.indexOf(p);
w.Element.prototype.getBoundingClientRect = function () {
  const pcell = this.getAttribute && this.getAttribute('data-pcell');
  const arrive = this.getAttribute && this.getAttribute('data-arrive');
  if (this.classList && this.classList.contains('sheet__scroll')) {
    return { left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400, x: 0, y: 0 };
  }
  if (pcell) {
    const l = X0 + COL * idxOf(pcell);
    return { left: l, right: l + COL, top: 40, bottom: 70, width: COL, height: 30, x: l, y: 40 };
  }
  if (arrive) {
    const l = X0 + COL * idxOf(arrive);
    return { left: l, right: l + COL, top: 70, bottom: 100, width: COL, height: 30, x: l, y: 70 };
  }
  return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
};
Object.defineProperty(w.Element.prototype, 'scrollWidth',
  { configurable: true, get() { return RIGHT; } });

function fire(el, type) {
  const ev = doc.createEvent('HTMLEvents');
  ev.initEvent(type, true, true);
  el.dispatchEvent(ev);
}

/* ★ 触发重画只走页面**自己的**入口，不在背后调 drawShifts ——
   隔着一层去问，问到的永远是下一层的能力，而缺陷长在这一层的调用上。
   ★ 这里刻意用**填数**那条路（input → onGridInput → refreshBlock → drawShifts）：
     只用 resize 的话，「refreshBlock 忘了调 drawShifts」这种回归打不红任何一条 ——
     而那恰恰是最可能发生的一种（改 refreshBlock 的人多，改 resize 的人少）。 */
const block0 = doc.querySelector('[data-block]');
const anyInput = block0.querySelector('input[data-pin]');
fire(anyInput, 'input');

setTimeout(() => {
  console.log('\n── 2. ★ 有布局之后：线真的画出来了（走填数那条路）──');
  const block = doc.querySelector('[data-block]');
  const shifts = [...block.querySelectorAll('.shift')];
  ok(shifts.length === want.length,
     '★ 每个填了计划采购量的月份各一条线（' + want.length + ' 条）',
     '实际 ' + shifts.length + ' 条 —— refreshBlock 是不是没调 drawShifts？');
  if (!shifts.length) return finish();

  const caps = shifts.map(s => s.querySelector('.shift__cap'));
  const outs = caps.filter(c => c.className.indexOf('shift__cap--out') >= 0);

  console.log('\n── 3. ★ 周期内 / 周期外，两头都验 ──');
  ok(outs.length === outPeriod.length,
     '★ 朱红的桩正好是落在周期外的那 ' + outPeriod.length + ' 条',
     '实际 ' + outs.length + ' 条朱红');
  ok(outs.length < shifts.length && outs.length > 0,
     '★ 同一批里既有朱红也有非朱红（否则「恒为 out」也能绿）',
     '朱红 ' + outs.length + ' / 共 ' + shifts.length);

  console.log('\n── 4. ★ 坐标落在正确的格子里 ──');
  // 第一条：周期内 —— 终点必须落在**到货那一格**，不是随便一个右边
  const first = shifts[0];
  const line = first.querySelector('.shift__line');
  const x1 = parseFloat(line.style.left);
  const x2 = x1 + parseFloat(line.style.width);
  const arr0 = w.Store.monthAfterDays(seeded.periods[0], params.total_days);
  ok(Math.abs(x1 - (X0 + COL * 0.72)) < 0.01,
     '★ 起点落在「' + seeded.periods[0] + '」那一格里（下单的地方）', 'left=' + x1);
  ok(Math.abs(x2 - (X0 + COL * idxOf(arr0) + COL * 0.5)) < 0.01,
     '★★ 终点落在「' + arr0 + '」那一格的中间 —— 位移指向的是**到货月**，不是随便往右画',
     'x2=' + x2 + '，期望 ' + (X0 + COL * idxOf(arr0) + COL * 0.5));
  ok(x2 > x1, '★ 线是向右的（时间只会往前走）', 'x1=' + x1 + ' x2=' + x2);
  ok(caps[0].textContent === arr0,
     '★ 桩上写的就是到货月', '桩=' + caps[0].textContent + '　期望=' + arr0);

  // 最后一条：周期外 —— 冲出右边缘，桩要收得回来
  const last = shifts[shifts.length - 1];
  const ll = last.querySelector('.shift__line');
  const lx2 = parseFloat(ll.style.left) + parseFloat(ll.style.width);
  const lcap = parseFloat(last.querySelector('.shift__cap').style.left);
  ok(last.querySelector('.shift__cap').className.indexOf('shift__cap--out') >= 0,
     '★ 最后一个月的单落在周期外（提前期决定了它一定越界）');
  ok(lx2 >= RIGHT - 2.01, '★ 它的线冲到了网格右边缘', 'x2=' + lx2 + '，右边缘 ' + RIGHT);
  ok(lcap < lx2, '★ 桩往回收了一点，不会被 overflow 裁掉', 'cap=' + lcap + ' 线尾=' + lx2);

  /* ── 5. ★ 改了窗口宽度也要重画 ──
     列的位置随宽度变，线不跟着重画就会指向**上一次**的列 ——
     那比不画还糟：它看起来是对的。 */
  console.log('\n── 5. ★ resize 之后线还在（去抖 150ms）──');
  block.querySelectorAll('.shift').forEach(n => n.parentNode.removeChild(n));
  ok(block.querySelectorAll('.shift').length === 0, '（先把线清空，好证明下面是重画出来的）');
  fire(w, 'resize');
  setTimeout(() => {
    ok(block.querySelectorAll('.shift').length === want.length,
       '★ resize 之后 ' + want.length + ' 条线自己长回来了',
       '实际 ' + block.querySelectorAll('.shift').length + ' 条 —— resize 监听掉了？');

    console.log('\n── 6. 控制台 ──');
    ok(!errors.length, '全程没有未预期的错误',
       errors.slice(0, 3).map(e => e.split('\n').slice(0, 4).join('\n      ')).join('\n      '));
    finish();
  }, 300);
}, 300);

function finish() {
  console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败' : '★ 全绿'));
  process.exit(fails.length ? 1 : 0);
}
