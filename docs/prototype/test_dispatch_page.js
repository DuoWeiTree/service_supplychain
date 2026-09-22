/* ============================================================
   dispatcher.html / sheet.html —— 真加载一遍页面的自检

   ★ 判据是「页面真的长出东西来」，不是「没有报错」。
     渲染期异常发生在 host.innerHTML='' 之后、appendChild 之前时，
     「渲染失败」和「本来就没数据」长得一模一样。

   ★ 种子走**完整链路**：提交计划 → 建采购单 → 下单 → 推进到货 → 本地仓才有货。
     不许直接往 stock 里塞数 —— 塞数能让测试变绿，但绿的是一条现实中不存在的路。

   跑法:  node test_dispatch_page.js
   依赖:  jsdom（没装会说跳过，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据（2026-09-18 实跑，20 条变异全部打红，基线 0 红）

   | 把页面故意改成                                   | 转红的断言                       |
   |-------------------------------------------------|---------------------------------|
   | dispatcher: kpiPct 写死 '38.2%'                  | 比例是算出来的 ×1                |
   | dispatcher: kpiFill 宽度写死 '38%'               | 进度条宽度＝比例 ×1              |
   | dispatcher: 状态过滤 kept 恒等                    | 过滤真的滤掉了 ×1、隐藏行数 ×1   |
   | dispatcher: 「隐藏 N 张」换成一句「已过滤」        | 隐藏行数写在页面上 ×1            |
   | dispatcher: 创建后不重渲                          | 新单进列表 ×1、看得到 FBA ×1     |
   | sheet: buildRows 不按 seller 分组（全塞第一个）    | 按店铺展开成多行 ×1、别店没被动 ×1|
   | sheet: 格子里去掉「计划采购」那一行                | 每格四个数都在 ×1                |
   | sheet: 货源表拼进别的货号（模拟不按货号过滤）      | 每行都是 skuA ×1、skuB 没混进 ×1 |
   | sheet: rejected 只列 [0]                          | 每一条都出现 ×1、第 2 条逐字 ×1  |
   | sheet: arranged 用 sumOf 代替 pickOne             | 「已安排 N」×1                   |
   | sheet: 撤回后不重渲                               | 撤回后归零 ×1                    |
   | sheet: 没有计划记录时不说原因                     | 说清为什么并给出路 ×1            |
   | sheet: summary 里塞一个 ★                         | 界面文案没有「★」×1              |
   | sheet: 「安排发货」按钮加箭头                     | 按钮后面没有箭头 ×1              |
   | sheet: 说明从 .fold 放回 .note                    | .note 为 0 ×1、.fold ≥1 ×1      |
   | sheet: 弹窗货源表渲染成 0 行                      | 货源至少 1 行 ×1、有输入框 ×1    |
   | sheet: 顶栏改回自搭 .masthead                     | 顶栏是 Shell.mount ×4            |
   | dispatcher: 不调 Shell.mount()                    | 顶栏是 Shell.mount ×3            |
   | sheet: Shell.gates 只喂没过的那几项                | 6 项全列 ×1、通过项也留着 ×1     |
   | sheet: 去掉 Shell.steps() 调用                     | 逐步列出 ×1、三步都列 ×1         |

   ★ 2026-09-18 排货页收版面之后新增的那批断言，证据**不写在这里**，
     整表存在 `evidence-sheet.tbl`，用 `./replay.sh --table evidence-sheet.tbl` 重跑
     （48 条全部出分、0 条被闸拒、0 条「没断言守着」）。
     ★ 散在文件头里的证据是**逐条、静默**过期的；整表跑一次，过期一次性可见。

   ★ 那次回放顺带抓出一件事：本文件的收尾行原本写的是「✗ N 项断言失败」，
     而 replay.sh 的闸 3 认的是全仓统一的「✗ N 项失败」——
     于是这张页面的**每一条变异都被判成「半路崩了」**，20 条一条也出不了分。
     ★ 措辞漂一个字，整张证据表就回放不动，而测试本身一直是绿的。

   ★ 靶子的**状态**也是靶子的一部分（2026-09-18 实测两次）：
     ① 同样的断言、同样的变异，池里一行还是两行，结论完全不同 ——
        「批量编入不传 seller_id」排在池子只剩一行时跑，**一条断言都没打红**，
        而它是个真 bug。现在批量和单行**各自**在「池里还有一行同键」时跑一次。
     ② 一格有五种状态（没排过 / 在池里 / 已编入单 / 池里和单里都有 / 已投送），
        只测前两种的话，后面那几种退回［安排发货］的 bug 一条断言都碰不到。
        ★ 同一个形状咬了三次（编入后、投送后、以及「只给说明不给出路」的死路），
          每次都是**「已经动过」和「从没排过」在屏幕上长成一个样**。

   ★ 种子里**两种渠道的排货单都要有**（2026-09-19）：
     「货件号」「装箱」两栏只在 FBA 单上存在。原来种子只造了一张海外仓单，
     于是 fbaRow 整段代码一次都渲染不到 —— 那一栏改成什么样都没人看得见，
     那个「让人填一个此刻还不存在的 FBA 货件号」的输入框就是这么活下来的。
     ★ 覆盖不到的代码不会失败，所以也不会报警。

   ★ 断言要落在**它真正想测的那块 DOM** 上：
     「格子上有『已投送 N』」写成对整格文本的正则，会被下面那句说明里的
     「已投送 5 件」托住 —— 把数字那一行整个删掉照样绿。实测假绿过一次，
     改成只看 `.cell__rows` 才打红。

   ★ 两条断言一开始是**空转**的，是变异跑出来的，不是读代码读出来的：
     ① 「货源已按货号过滤」原本只断言「每行都是 skuA」—— 而 localSources 本来就只返回
        skuA，遍历 0 行也算绿。补了「表里至少 1 行」+「另一个**同样有库存**的 skuB 不在表里」。
     ② 「已安排 N 不按 msku 求和」原本打红 0 条 —— 不是断言写错了，是**种子里每个店铺只有
        1 个 msku**，求和与取一个得数相同，靶子根本不存在。现在种子改成必须挑一个
        「跨 ≥2 店铺 且 某店挂 ≥2 msku」的货号，挑不到就硬失败。
     ★ 教训：变异跑出绿，先怀疑靶子不存在，别先怀疑断言。
   ============================================================ */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (e) {
  console.log('⚠ 没有 jsdom，这个测试跳过（不是通过）。');
  console.log('  装法：npm install jsdom   或   npm install -g jsdom 后设 NODE_PATH');
  console.log('  原因：' + (e && e.code === 'MODULE_NOT_FOUND' ? '模块未安装' : e && e.message));
  process.exit(0);
}

const DIR = __dirname;
const fails = [];
const errors = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}
function eq(got, want, name) {
  ok(got === want, name, '实际 ' + JSON.stringify(got) + '，应为 ' + JSON.stringify(want));
}
function n(txt) { return Number(String(txt == null ? '' : txt).replace(/[^\d.-]/g, '')); }

/* ============================================================
   0. 种子 —— 走完整链路，本地仓的货是**到货到出来的**
   ============================================================ */

function seedState() {
  const mem = {};
  const ls = {
    getItem: k => (Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: k => { delete mem[k]; }
  };
  const g = { localStorage: ls };
  const sb = { window: g, localStorage: ls,
               console: Object.assign({}, console, { debug() {}, error() {} }) };
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(DIR, 'assets/store.js'), 'utf8'), sb);
  const S = sb.window.Store;

  const plan = (S.get().plans || []).filter(p => !(p.claims || []).length)[0];
  const hit = S.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
  const group = {};
  (hit.rows || []).filter(r => r.selectable)
    .forEach(r => (group[r.sku] = group[r.sku] || []).push(r));

  /* skuA 要同时满足两件事，少一件下面就有断言空转：
       ① 跨 ≥2 个店铺 —— 网格要按店铺展开成多行
       ② 其中至少一个店铺挂着 ≥2 个 msku —— 「已安排不按 msku 求和」才有靶子
     ★ 找不到就硬失败，不许退而求其次挑一个「差不多」的。 */
  const perSeller = k => {
    const c = {};
    group[k].forEach(r => { c[r.seller_id] = (c[r.seller_id] || 0) + 1; });
    return c;
  };
  const skuA = Object.keys(group).filter(k => {
    const c = perSeller(k);
    const ss = Object.keys(c);
    return ss.length >= 2 && ss.some(s => c[s] >= 2);
  })[0];
  if (!skuA) {
    console.log('✗ 种子里找不到「跨店铺 且 某店挂多 msku」的货号 —— 目录变了，先修种子再跑');
    process.exit(1);
  }
  // skuB：另一个货号，且**也会有本地仓库存** —— 「按货号过滤」那条断言的反面证人
  const skuB = Object.keys(group).filter(k => k !== skuA)[0];
  [skuA, skuB].forEach(k => S.addClaims(plan.plan_id,
    group[k].map(r => ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid }))));

  const p2 = S.plan(plan.plan_id);
  const periods = p2.periods;
  (p2.claims || []).forEach((c, i) => periods.forEach((m, j) =>
    S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, m, 40 + i * 10 + j * 5)));
  // ★ 只给前两个月填计划采购量：第三个月因此**没有计划记录**，那一格必须拒绝安排发货
  S.setPurchase(p2.plan_id, skuA, periods[0], 300);
  S.setPurchase(p2.plan_id, skuA, periods[1], 200);
  S.setPurchase(p2.plan_id, skuB, periods[0], 150);
  const sub = S.submitPlan(p2.plan_id);

  const wid = S.get().warehouses[0].wid;
  const po = S.createPo({ supplier: '东莞宠具', wid: wid, opt_uid: '8801', purchaser_id: '2001' });
  const cands = S.poCandidates({}).rows.filter(r => r.line_id && r.open > 0);
  const pickA = cands.filter(r => r.sku === skuA)[0];
  const pickB = cands.filter(r => r.sku === skuB)[0];
  S.addPoItems(po.po_id, [pickA, pickB].filter(Boolean).map(r =>
    ({ line_id: r.line_id, seller_id: r.seller_id, units: Math.min(40, r.open), price: 12 })));
  S.placePo(po.po_id);

  /* ★★ skuA 必须在**两个**本地仓都有货，否则下面三条断言全是空转：
       ① 「改」预填时「只有那个仓填了、别的仓是空的」—— 只有一行时两者得数相同
       ② 「按本地仓名筛选真的隐藏了行」—— 只有一行时筛不掉任何东西
       ③ 「被筛掉那行填的量还活着」—— 没有被筛掉的行，就没有会被清零的东西
     ★ 本项目已四次栽在「靶子不存在」上，而不是断言写错。 */
  const wid2 = S.get().warehouses[1].wid;
  const po2 = S.createPo({ supplier: '东莞宠具', wid: wid2, opt_uid: '8801', purchaser_id: '2001' });
  const more = S.poCandidates({}).rows.filter(r => r.line_id && r.open > 0 && r.sku === skuA);
  if (!more.length) {
    console.log('✗ 种子：skuA 没有可再下单的量，第二个货源仓造不出来 —— 先修种子再跑');
    process.exit(1);
  }
  S.addPoItems(po2.po_id, [{ line_id: more[0].line_id, seller_id: more[0].seller_id,
                             units: Math.min(20, more[0].open), price: 12 }]);
  S.placePo(po2.po_id);

  for (let d = 0; d < 3; d++) S.tick();          // 下单后第 2 天部分到货

  const made = S.createSheet();
  const ov = S.createConsignment(made.sheet_id, { channel: 'oversea', dest: { wid: S.get().warehouses[1].wid } });
  /* ★ 还要一张 **FBA** 排货单：「货件号」「装箱」两栏只在 FBA 单上存在，
     只造海外仓单的话，fbaRow 整段代码一次都渲染不到 ——
     那一栏改成什么样都没人看得见（这正是它此前没被任何断言碰过的原因）。 */
  const fb = S.createConsignment(made.sheet_id, { channel: 'fba', dest: { sid: '11201', fc: 'ONT8' } });

  return {
    json: mem['scm_proto'], planId: p2.plan_id, periods: periods,
    skuA, skuB, sheetId: made.sheet_id, overseaCid: ov.cid, fbaCid: fb.cid,
    mskuMax: Math.max.apply(null, Object.keys(perSeller(skuA)).map(s => perSeller(skuA)[s])),
    submitted: !!(sub && sub.ok),
    srcA: S.localSources(skuA), srcB: S.localSources(skuB),
    progress: S.planProgress()
  };
}

const seed = seedState();
console.log('\n── 0. 种子（完整链路，不是塞数）──');
ok(seed.submitted, '销售计划已提交');
ok(!!seed.skuA && !!seed.skuB, '造了两个货号', seed.skuA + ' / ' + seed.skuB);
ok(seed.srcA.length > 0, '★ 到货之后 skuA 在本地仓有货（走的是真实链路）',
   JSON.stringify(seed.srcA));
ok(seed.srcB.length > 0, '★★ skuB 也有货 —— 「按货号过滤」那条断言需要一个反面证人',
   JSON.stringify(seed.srcB));
ok(seed.srcA.length >= 2,
   '★★ 前置：skuA 在 ' + seed.srcA.length + ' 个本地仓有货 —— 只有一个仓的话，'
   + '「只有那个仓预填了」「筛选隐藏了行」「被筛掉的量还活着」三条全是空转',
   seed.srcA.map(x => x.warehouse + ':' + x.valid).join(' | '));
ok(seed.progress.demand_total > 0, '★ 需求总量是真数（否则首页 KPI 全是 0/0）',
   '' + seed.progress.demand_total);
ok(seed.mskuMax >= 2,
   '★★ 前置：skuA 下有一个店铺挂了 ' + seed.mskuMax + ' 个 msku —— '
   + '没有这个，「已安排不按 msku 求和」那条断言就是空转（这正是它第一次跑变异时发生的事）',
   '每个店铺只有 ' + seed.mskuMax + ' 个 msku');

/* ============================================================
   页面加载器
   ============================================================ */

function loadPage(file, query) {
  const vc = new VirtualConsole();
  const errs = [];
  vc.on('jsdomError', e => errs.push('jsdomError: ' + (e.stack || e.message)));
  vc.on('error', (...a) => {
    const s = a.map(String).join(' ');
    if (/cells-backfilled/.test(s)) return;                  // 装载期回填提示，良性
    if (/arrange\.rejected/.test(s)) { return; }              // 拒绝有声，是**对的**
    if (/param\.missing|sheet\.not-found/.test(s)) { return; }
    errs.push('console.error: ' + s);
  });
  const dom = new JSDOM(fs.readFileSync(path.join(DIR, file), 'utf8'), {
    runScripts: 'outside-only',
    url: 'http://localhost/' + file + (query || ''),
    virtualConsole: vc, pretendToBeVisual: true
  });
  const w = dom.window, doc = w.document;
  w.scrollTo = function () {};
  Object.defineProperty(w.HTMLElement.prototype, 'scrollIntoView',
    { value: function () {}, writable: true, configurable: true });
  w.localStorage.setItem('scm_proto', seed.json);

  function run(code, label) {
    try { w.eval(code); }
    catch (e) { errs.push('★ ' + label + ' 抛出: ' + (e.stack || e)); }
  }
  [...doc.querySelectorAll('script[src]')].forEach(sc => {
    const p = path.join(DIR, sc.getAttribute('src'));
    if (!fs.existsSync(p)) { errs.push('缺少脚本 ' + sc.getAttribute('src')); return; }
    run(fs.readFileSync(p, 'utf8'), sc.getAttribute('src'));
  });
  [...doc.querySelectorAll('script:not([src])')].forEach((sc, i) =>
    run(sc.textContent, file + ' 内联脚本 #' + i));

  function fire(el, type) {
    const ev = doc.createEvent('HTMLEvents');
    ev.initEvent(type, true, true);
    el.dispatchEvent(ev);
  }
  /* ★ 页面把输入框的值收在自己的 ctx 里（换筛选要重画表，值只活在 DOM 上会被清零）——
     所以只写 .value 是**没人听见**的。测试要像人一样敲，事件必须发出去。 */
  function setVal(el, v) { el.value = String(v); fire(el, 'input'); fire(el, 'change'); }
  return { w, doc, errs, fire, setVal,
           txt: el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '') };
}

/* ★ 「页面上的文案」不等于 body.textContent —— <script> 也在 body 里，
   把源码一起读进来，断言就成了对注释的断言（这条一开始就是这么错的）。
   ★ 同时剔除 shell.js 造的公共外壳（顶栏 / toast / 确认框）：那是**共用部件**，
     它的措辞不归这两页管，混进来会让「这一页写了什么」查不清楚。 */
/* .steps 也算外壳：里面的措辞来自 store.js 的投送步骤（含它自己的 ★），不归这两页管。
   ★ .gate__detail 同理 —— 闸的结论文案是 store.js 里 mk() 拼的（G2 那条就带 ★），
     混进来会让「这一页写了什么」查不清楚。Shell.gates 的**结构**照样由
     noProseCheck / gateItems 那几条守着，这里剔的只是措辞。 */
const CHROME = 'script, style, .shell, .toasts, .modal-backdrop, .steps, .gate__detail';
function visibleText(doc) {
  const clone = doc.body.cloneNode(true);
  /* ★ Shell.steps() 在有 manual 步时会在 ol.steps 旁边吐一个 .note（含它自己的 ★）——
     那是**共用部件**的措辞，和 .steps 本身是一回事，判据也和 noProseCheck 用的同一条。
     用 CSS 选择器表达不了「和 ol.steps 同父」，所以在这里按同样的谓词剔。
     ★ 必须**赶在**下面剔 CHROME 之前做：CHROME 里有 .steps，
       ol 先被删掉的话，这个谓词就再也匹配不上了 —— 而它会静默地什么都不剔。 */
  [...clone.querySelectorAll('.note')]
    .filter(nd => nd.parentNode && nd.parentNode.querySelector('ol.steps'))
    .forEach(nd => nd.parentNode.removeChild(nd));
  [...clone.querySelectorAll(CHROME)].forEach(n => n.parentNode.removeChild(n));
  return clone.textContent.replace(/\s+/g, ' ').trim();
}
function ownClickables(doc) {
  return [...doc.querySelectorAll('button, a.btn')]
    .filter(b => !b.closest('.shell, .toasts, .modal-backdrop'));
}

function noProseCheck(doc, where) {
  /* ★ Shell.steps() 在有 manual 步时会自己吐一个 .note（说明「前几步是真的发生了」）——
     那是共用部件的措辞，不是本页写的散文。只数**本页自己的** .note。 */
  const ownNotes = [...doc.querySelectorAll('.note')]
    .filter(nd => !(nd.parentNode && nd.parentNode.querySelector('ol.steps')));
  eq(ownNotes.length, 0, where + '：本页自己的 .note 说明块个数为 0',
     ownNotes.map(nd => nd.textContent.slice(0, 40)).join(' | '));
  /* ★ .gate 不能一刀切成 0 —— Shell.gates() 渲染的闸结论正是 .gate，那是**部件**不是说明块。
     判据换成：页面上出现的每个 .gate 都必须是 Shell.gates() 造的（带 .gate__sum）。 */
  const gates = [...doc.querySelectorAll('.gate')];
  ok(gates.every(g => g.querySelector('.gate__sum')),
     where + '：没有裸 .gate 说明块（只有 Shell.gates() 的闸结论）',
     gates.map(g => g.className + ':' + g.textContent.slice(0, 30)).join(' | '));
  const folds = doc.querySelectorAll('details.fold').length;
  ok(folds >= 1, where + '：说明收进了 .fold（至少 1 个）', '实际 ' + folds);
}

/* ★ 顶栏必须是 Shell.mount() 造的那一个。
   角色条是用户判断「我现在是谁」的唯一依据 —— 它在某一页换个样子，比没样式更糟。 */
function shellCheck(doc, where) {
  eq(doc.querySelectorAll('.shell__bar').length, 1, where + '：顶栏是 Shell.mount() 造的');
  eq(doc.querySelectorAll('.shell__role').length, 3, where + '：角色条三个按钮，和别的页一模一样');
  eq(doc.querySelectorAll('.shell__crumb').length, 1, where + '：面包屑也在');
  eq(doc.querySelectorAll('.masthead__in, .roles, .role__badge').length, 0,
     where + '：没有自己另搭一套顶栏');
}

/* ============================================================
   1. dispatcher.html —— 排货员首页
   ============================================================ */

console.log('\n── 1. dispatcher.html · 销售计划完成度 ──');
const A = loadPage('dispatcher.html');
A.errs.forEach(e => errors.push('[dispatcher] ' + e));

const planRows = A.doc.querySelectorAll('#plans tbody tr');
ok(planRows.length > 0, '★ 完成度表真的长出行来（不是「没报错」）',
   '实际 ' + planRows.length + ' 行；#plans=' + A.txt(A.doc.getElementById('plans')).slice(0, 80));

const kArr = n(A.txt(A.doc.getElementById('kpiArrived')));
const kDem = n(A.txt(A.doc.getElementById('kpiDemand')));
const kPct = A.txt(A.doc.getElementById('kpiPct'));
ok(kDem > 0, '★ 分母是真数（否则下面那条比例断言是空转）', '分母 ' + kDem);
const want = (Math.round(kArr * 1000 / kDem) / 10) + '%';
eq(kPct, want, '★ KPI 的比例是那两个数算出来的，不是写死的');
eq(A.doc.getElementById('kpiFill').style.width, Math.min(100, n(want)) + '%',
   '★ 进度条宽度＝比例（同一个数，不是另画一个）');

// 过滤：先证明两种状态都在，再证明过滤真的滤掉了东西
const states = [...A.doc.querySelectorAll('#plans tbody tr')]
  .map(tr => (A.txt(tr).match(/已提交|草稿/) || [''])[0]);
ok(new Set(states.filter(Boolean)).size >= 2,
   '★ 前置：列表里确实有两种计划状态（否则过滤无从证明）', states.join('/'));
const fState = A.doc.getElementById('fState');
fState.value = '已提交';
A.fire(fState, 'change');
const after = A.doc.querySelectorAll('#plans tbody tr').length;
ok(after > 0 && after < planRows.length,
   '★ 按状态过滤真的滤掉了东西', '过滤前 ' + planRows.length + '，过滤后 ' + after);
ok(/隐藏 \d+ 张/.test(A.txt(A.doc.getElementById('planFilterSay'))),
   '★ 被隐藏的行数写在了页面上（过滤掉却不说，人不知道那行为什么没了）',
   A.txt(A.doc.getElementById('planFilterSay')));
fState.value = '';
A.fire(fState, 'change');

console.log('\n── 2. dispatcher.html · 排货单 ──');
const consBefore = A.doc.querySelectorAll('#cons tbody tr').length;
ok(consBefore > 0, '★ 排货单表有行（种子里建过一张海外仓单）', '实际 ' + consBefore);

A.fire(A.doc.getElementById('btnNew'), 'click');
const dlgNew = A.doc.getElementById('dlgNew');
ok(dlgNew.open === true, '★ 「新建排货单」弹窗打开了', 'open=' + dlgNew.open);
const nwCh = A.doc.getElementById('nwChannel');
nwCh.value = 'fba';
A.fire(nwCh, 'change');
const destOpts = [...A.doc.querySelectorAll('#nwDest option')].map(o => o.textContent);
ok(destOpts.length > 0 && destOpts.every(t => /·/.test(t)),
   '★ 换成 FBA 后，收货仓下拉换成「店铺 · FC」（两种渠道形状不同）', destOpts.join(' | '));
A.fire(A.doc.getElementById('nwOk'), 'click');
eq(dlgNew.open, false, '创建后弹窗关上');
const consAfter = A.doc.querySelectorAll('#cons tbody tr').length;
eq(consAfter, consBefore + 1, '★ 新建的排货单真的进了列表');
ok(/FBA/.test(A.txt(A.doc.getElementById('cons'))), '列表里看得到 FBA 那张');
ok(/已建排货单/.test(A.txt(A.doc.getElementById('flash'))),
   '★ 当场发生的结果用 .flash 说了一句', A.txt(A.doc.getElementById('flash')));

noProseCheck(A.doc, 'dispatcher.html');
shellCheck(A.doc, 'dispatcher.html');

/* ============================================================
   2. sheet.html —— 排货单详情
   ============================================================ */

console.log('\n── 3. sheet.html · 核心网格 ──');
const B = loadPage('sheet.html', '?sheet=' + seed.sheetId);
B.errs.forEach(e => errors.push('[sheet] ' + e));

const blocks = B.doc.querySelectorAll('.sheet');
ok(blocks.length > 0, '★ 网格真的长出货号块来', '实际 ' + blocks.length);
ok(B.doc.querySelectorAll('table.grid').length === blocks.length,
   '★ 用的是设计系统的 table.grid（和计划网格同构）',
   blocks.length + ' 块 / ' + B.doc.querySelectorAll('table.grid').length + ' 张网格');

const period0 = seed.periods[0];
const arrBtns = [...B.doc.querySelectorAll('[data-arrange]')];
ok(arrBtns.length > 0, '★ 前置：网格里有「安排发货」按钮', '实际 ' + arrBtns.length);
const mine = arrBtns.filter(b => {
  const p = b.getAttribute('data-arrange').split('|');
  return p[0] === seed.skuA && p[2] === period0;
});
const sellers = new Set(mine.map(b => b.getAttribute('data-arrange').split('|')[1]));
ok(sellers.size >= 2, '★★ 同一货号按店铺展开成多行（店铺是排货这一步才定的）',
   seed.skuA + ' 在 ' + period0 + ' 只有 ' + sellers.size + ' 个店铺行');

const cell0 = mine[0].closest('td');
const cellTxt = B.txt(cell0);
['销售预估', '期望销量', '计划采购'].forEach(k =>
  ok(cellTxt.indexOf(k) >= 0, '★ 每格四个数都在：' + k, cellTxt));
ok(cell0.querySelector('.cell__close'), '★ 结论数（库存预估）用 .cell__close 摆大字', cellTxt);
ok(cell0.querySelector('.cell__rows .i-pencil') && cell0.querySelector('.cell__rows .i-ink'),
   '★ 墨色分了「机器估的」和「人写的」（不靠一句说明）', cell0.innerHTML.slice(0, 200));

/* ★★ 反向证人：这条必须在**任何一次安排之前**跑。
   「改」「撤回」恒显示也能让后面「安排过就出现」那两条绿 ——
   恒显示的代价是：点下去只能告诉人「本来就没安排过」，那是一次白跑。 */
const untouched = [...B.doc.querySelectorAll('td.cell')];
ok(untouched.length > 0, '★ 前置：网格上真有格子可查（遍历 0 个也算绿是本项目栽过的坑）',
   '实际 ' + untouched.length + ' 格');
ok(untouched.every(td => B.txt(td).indexOf('已安排') < 0),
   '★ 前置：此刻一格都还没安排过（否则下面那条反向断言测的是别的东西）',
   untouched.filter(td => B.txt(td).indexOf('已安排') >= 0).map(td => B.txt(td)).join(' | '));
const strayEdit = untouched.filter(td => td.querySelector('[data-edit]') || td.querySelector('[data-unarrange]'));
eq(strayEdit.length, 0, '★★ 没安排过的格子上没有［改］［撤回］',
   strayEdit.slice(0, 2).map(td => B.txt(td)).join(' | '));
ok(untouched.every(td => !!td.querySelector('[data-arrange]')),
   '★ 而［安排发货］每一格都有 —— 它才是唯一入口');

console.log('\n── 3b. sheet.html · 网格是唯一入口，池子收起来 ──');
const poolFold = B.doc.getElementById('poolFold');
ok(!!poolFold, '★ 待编排池在页面上（0 件也不许整块消失 —— 消失了「没安排过」和「没画出来」就一个样）');
eq(poolFold && poolFold.tagName.toLowerCase(), 'details', '待编排池收成了一块可展开的');
ok(poolFold && !poolFold.hasAttribute('open'),
   '★★ 待编排池默认是收起的 —— 平时只看到网格，网格是唯一的「安排」入口',
   poolFold ? poolFold.outerHTML.slice(0, 80) : '');
const poolSum = B.txt(poolFold && poolFold.querySelector('summary'));
ok(/待编排池是空的|已安排待编排/.test(poolSum), '★ 摘要那一行就把有多少说清了', poolSum);
ok(!!B.doc.getElementById('flowLine'),
   '★ 网格和排货单之间有一句话说清顺序');
ok(/网格.*待编排池.*排货单.*投送/.test(B.txt(B.doc.getElementById('flowLine'))),
   '　那句话把四步按顺序点了名', B.txt(B.doc.getElementById('flowLine')));

const pageSay = visibleText(B.doc);
ok(pageSay.length > 400, '★ 前置：读到了成篇的页面文案（读空字符串也算绿）', '只读到 ' + pageSay.length + ' 字');
ok(pageSay.indexOf('全手动') >= 0,
   '★★ 页面上写明了这是「全手动」—— 谁在排货这个问题，页面必须自己回答');
['发哪些', '从哪个仓出', '给哪个店'].forEach(k =>
  ok(pageSay.indexOf(k) >= 0, '　「全手动」那段点名了：' + k));

console.log('\n── 4. sheet.html · 本地仓货品筛选器 ──');
B.fire(mine[0], 'click');
const dlgAr = B.doc.getElementById('dlgArrange');
ok(dlgAr.open === true, '★ 点「安排发货」弹窗打开了', 'open=' + dlgAr.open);
const srcCells = [...B.doc.querySelectorAll('#arTable [data-src-sku]')];
ok(srcCells.length > 0, '★ 前置：货源表里至少有一行（遍历 0 行也算绿是本项目栽过的坑）',
   '实际 ' + srcCells.length + ' 行');
ok(srcCells.every(td => td.getAttribute('data-src-sku') === seed.skuA),
   '★ 货源已按货号过滤：每一行都是 ' + seed.skuA,
   srcCells.map(td => td.getAttribute('data-src-sku')).join('/'));
ok(!srcCells.some(td => td.getAttribute('data-src-sku') === seed.skuB),
   '★★ 另一个**同样有库存**的货号 ' + seed.skuB + ' 没混进来（这条才让上一条不是空转）',
   srcCells.map(td => td.getAttribute('data-src-sku')).join('/'));

/* ★★ 三个数都要给：仓里 / 已被别处占 / 现在能安排。
   只给一个「可发 24」，「仓里就 24」和「仓里 60、其中 44 已被别处安排」
   在屏幕上长得一模一样 —— 上一个 bug 正是这么来的。 */
const srcRows = [...B.doc.querySelectorAll('#arTable tbody tr')];
ok(srcRows.length > 0, '★ 前置：货源表有 tbody 行', '实际 ' + srcRows.length);
const arHead = B.txt(B.doc.querySelector('#arTable thead'));
['仓里', '已被别处占', '现在能安排'].forEach(k =>
  ok(arHead.indexOf(k) >= 0, '★ 表头点名了：' + k, arHead));
const missing3 = srcRows.filter(tr => !(tr.querySelector('[data-src-valid]')
  && tr.querySelector('[data-src-committed]') && tr.querySelector('[data-src-free]')));
eq(missing3.length, 0, '★★ 每一行三个数都在（缺一个，两种状态就长成一个样）',
   missing3.slice(0, 2).map(tr => B.txt(tr)).join(' | '));
// ★ 三列不能是空壳：至少有一行的「仓里」是个真数
ok(srcRows.some(tr => n(tr.querySelector('[data-src-valid]').getAttribute('data-src-valid')) > 0),
   '★★ 三列里至少一行有真数（三列空壳也能让上一条绿）',
   srcRows.map(tr => tr.querySelector('[data-src-valid]').getAttribute('data-src-valid')).join('/'));

console.log('\n── 5. sheet.html · 分拨超量：每一条拒绝都要出现 ──');
const arInputs = [...B.doc.querySelectorAll('#arTable input.inp')];
ok(arInputs.length > 0, '★ 前置：有可填的分拨数量输入框', '实际 ' + arInputs.length);
B.setVal(arInputs[0], '999999');
B.fire(B.doc.getElementById('arOk'), 'click');
const errLis = [...B.doc.querySelectorAll('#arErr li')].map(li => li.textContent);
// Store 侧对这次分拨给的全部理由 —— 页面必须一条不落
// ★ 用 setArrangement，和页面走的是同一个原语；换成 arrangeShipment 的话
//   两边的措辞不一样，「逐字出现」会因为对错了参照而假红。
const truth = B.w.Store.setArrangement(seed.sheetId, {
  line_id: B.w.Store.dispatchGrid(seed.sheetId)
    .filter(r => r.sku === seed.skuA)[0].cells[0].line_id,
  seller_id: [...sellers][0],
  picks: [{ wid: seed.srcA[0].wid, units: 999999 }]
}).rejected || [];
ok(truth.length >= 2, '★ 前置：这次超量确实同时撞了两条限制（否则下面那条是空转）',
   JSON.stringify(truth));
eq(errLis.length, truth.length, '★★ rejected 的每一条都出现在页面上（不是只显示第一条）');
truth.forEach((t, i) => ok(errLis.indexOf(t) >= 0, '　第 ' + (i + 1) + ' 条原因逐字出现', t));
ok(dlgAr.open === true, '被拒后弹窗不关 —— 人要在原地改数，不是重开一遍');

console.log('\n── 6. sheet.html · 安排 / 改 / 撤回（同一条路）──');
const take = Math.min(5, seed.srcA[0].valid);
ok(take > 0, '★ 前置：仓里确实有可发的量', '可发 ' + seed.srcA[0].valid);
B.setVal(arInputs[0], String(take));
B.fire(B.doc.getElementById('arOk'), 'click');
eq(dlgAr.open, false, '安排成功后弹窗关上');
ok(/已安排 \d+ 件/.test(B.txt(B.doc.getElementById('flash'))),
   '★ 结果用 .flash 说了一句', B.txt(B.doc.getElementById('flash')));

const sid0 = mine[0].getAttribute('data-arrange').split('|')[1];
/* ★ 一格**一个主动作**，而状态有四种：
     没安排过        → ［安排发货］           data-arrange
     在待编排池里    → ［改］［撤回］          data-edit / data-unarrange
     已编入排货单    → 一句话 + 跳转锚点       data-consigned-note
     已投送          → 只读的一句话 + 锚点     data-dispatched-note
   所以找格子五个标记都要认。★ 少认一个，那种状态的格子就**直接找不到**，
   一串断言会一起假红 —— 而「格子不存在」和「格子长错了」下一步完全不同。
   （实测栽过一次：加第三种状态时忘了加 data-consigned-note，七条断言一起假红。） */
function cellOf(sku, sid, period) {
  const k = sku + '|' + sid + '|' + period;
  const b = B.doc.querySelector(
    '[data-arrange="' + k + '"], [data-edit="' + k + '"], [data-unarrange="' + k + '"], '
    + '[data-consigned-note="' + k + '"], [data-dispatched-note="' + k + '"], '
    + '[data-arrange-more="' + k + '"]');
  return b ? b.closest('td') : null;
}
const c1 = cellOf(seed.skuA, sid0, period0);
const arrangedRe = new RegExp('已安排\\s*' + take + '(?!\\d)');
ok(c1 && arrangedRe.test(B.txt(c1)),
   '★★ 这一格显示「已安排 ' + take + '」—— 按 msku 求和会翻倍，所以这个数必须是原值',
   c1 ? B.txt(c1) : '找不到格子');
const other = [...sellers].filter(s => s !== sid0)[0];
const c2 = cellOf(seed.skuA, other, period0);
ok(c2 && B.txt(c2).indexOf('已安排') < 0,
   '★★ 另一个店铺那格没被动过（店铺是这一步才定的）', c2 ? B.txt(c2) : '找不到格子');

ok(!!c1.querySelector('[data-edit]'), '★ 安排过的格子上出现［改］（改个数量不该要两步）');
ok(!!c1.querySelector('[data-unarrange]'), '★ 也出现［撤回］（画得出的退回边必须真的退得回去）');
ok(!c1.querySelector('[data-arrange]'),
   '★★ 安排过的格子上**没有**［安排发货］了 —— 一格一个主动作，'
   + '两个按钮做同一件事、还要读说明才知道差别，正是这一轮要收掉的那个形态',
   c1.innerHTML.slice(0, 200));
ok(c2 && !c2.querySelector('[data-edit]') && !c2.querySelector('[data-unarrange]'),
   '★★ 没安排过的那格仍然没有［改］［撤回］（「恒显示」也能让上面两条绿）',
   c2 ? c2.innerHTML.slice(0, 160) : '');

/* ── 改：同一个弹窗，但**预填当前值**。
   ★ setArrangement 是整体替换 —— 打开时是空的，「确定」就等于清零，
     「改数量」会变成一次误删，而误删和「本来就想清空」长得一模一样。 */
B.fire(c1.querySelector('[data-edit]'), 'click');
eq(dlgAr.open, true, '★［改］打开的是同一个本地仓弹窗');
const editInputs = [...B.doc.querySelectorAll('#arTable input.inp')];
ok(editInputs.length > 0, '★ 前置：弹窗里有输入框', '实际 ' + editInputs.length);
const prefilled = editInputs.filter(i => String(i.value || '').trim() !== '');
eq(prefilled.length, 1, '★★ 只有刚才那个仓预填了，别的仓是空的（全填满也算「预填了」）',
   editInputs.map(i => i.getAttribute('data-src-wid') + '=' + i.value).join(' '));
/* ★ 预填没了的时候（正是 C / D 两条变异要抓的）这里不许崩 ——
   测试半路崩掉会被回放器判成「没跑完」而不是「抓到了」，两者的下一步完全相反。 */
const pre0 = prefilled[0] || editInputs[0];
eq(n(pre0 ? pre0.value : ''), take,
   '★★ ［改］预填的是**当前值** —— 空着打开的话，「确定」就是一次清零');
ok(/现在安排了 \d+ 件/.test(B.txt(B.doc.getElementById('arWhy'))),
   '★ 弹窗顶上说明了这是整体替换，不是往上加', B.txt(B.doc.getElementById('arWhy')));

/* ★★ 换一次「本地仓名」筛选，表就整个重画一次。
   整体替换的语义下，被筛掉那行填过的量要是没活下来，
   「确定」时它就被悄悄清零了 —— 而屏幕上只会少一截数字，没有任何地方说过话。 */
const widSel = B.doc.getElementById('arWid');
const filledWid = pre0 ? pre0.getAttribute('data-src-wid') : String(seed.srcA[0].wid);
const otherWid = seed.srcA.map(x => String(x.wid)).filter(w => w !== filledWid)[0];
ok(!!otherWid, '★ 前置：确实有第二个货源仓（只有一个仓时筛选隐藏不掉任何东西）',
   seed.srcA.map(x => x.wid).join('/') + '，已填的是 ' + filledWid);
widSel.value = otherWid;
B.fire(widSel, 'change');
eq(B.doc.querySelectorAll('#arTable input.inp').length, 1, '★ 筛到另一个仓，表上只剩一行');
const hiddenSay = B.txt(B.doc.getElementById('arTable'));
ok(/隐藏 1 行/.test(hiddenSay), '★ 隐藏了几行写在页面上', hiddenSay);
ok(/已经填了量/.test(hiddenSay),
   '★★ 而且说清了被隐藏的那行**还填着量**、确定时照样算数（不说的话人会以为它被排除了）',
   hiddenSay);
widSel.value = '';
B.fire(widSel, 'change');
const back = [...B.doc.querySelectorAll('#arTable input.inp')]
  .filter(i => String(i.value || '').trim() !== '');
eq(back.length, 1, '★★ 换回「全部」之后，刚才被筛掉那行填的量还在（重画一次就清零是静默丢失）',
   [...B.doc.querySelectorAll('#arTable input.inp')]
     .map(i => i.getAttribute('data-src-wid') + '=' + i.value).join(' '));
// ★ 同上：值没活下来时也不许崩 —— 挑一个兜底的输入框接着往下走
const back0 = back[0] || B.doc.querySelector('#arTable input[data-src-wid="' + filledWid + '"]');
eq(n(back0 ? back0.value : ''), take, '　而且还是原来那个数');

const take2 = (seed.srcA[0].free > take) ? take + 1 : take - 1;
ok(take2 > 0 && take2 !== take,
   '★ 前置：有一个**不同的**量可以改成（改成一样的数，「数变了」那条就是空转）',
   'take=' + take + ' take2=' + take2 + ' free=' + seed.srcA[0].free);
B.setVal(back0, String(take2));

/* ★★ 最后一步故意**在筛选状态下**点「确定」：填了量的那一行此刻被筛掉了。
   只读「当前画出来的输入框」的话，这次确定会把它当成一个都没填 ——
   而整体替换的语义下，那就是把这一格清零。 */
widSel.value = otherWid;
B.fire(widSel, 'change');
ok([...B.doc.querySelectorAll('#arTable input.inp')]
     .every(i => String(i.value || '').trim() === ''),
   '★ 前置：填了量的那一行此刻确实被筛掉了（没被筛掉的话下面这条是空转）',
   [...B.doc.querySelectorAll('#arTable input.inp')]
     .map(i => i.getAttribute('data-src-wid') + '=' + i.value).join(' '));
B.fire(B.doc.getElementById('arOk'), 'click');
eq(dlgAr.open, false, '改完弹窗关上（被筛掉那行填的量照样算进了这次安排）');
ok(/已改：\d+ → \d+ 件/.test(B.txt(B.doc.getElementById('flash'))),
   '★ 「改」说的是「M → N」，不是「已安排 N」—— 两件事别长成一个样',
   B.txt(B.doc.getElementById('flash')));
const c4 = cellOf(seed.skuA, sid0, period0);
ok(c4 && new RegExp('已安排\\s*' + take2 + '(?!\\d)').test(B.txt(c4)),
   '★★ ［改］之后格子上的数真的变了（' + take + ' → ' + take2 + '）',
   c4 ? B.txt(c4) : '找不到格子');

/* ── 撤回：要二次确认（它会让下游的量退回待排）── */
const unBtn = c4.querySelector('[data-unarrange]');
ok(!!unBtn, '★ 改完之后［撤回］还在');
B.fire(unBtn, 'click');
const unBack = B.doc.querySelector('.modal-backdrop');
ok(!!unBack, '★★ 撤回弹了二次确认 —— 它会让下游的量退回待排，不许一键触发');
ok(!!unBack && /撤回/.test(unBack.textContent), '　确认框里点名了是在撤回哪一格',
   unBack ? unBack.textContent.slice(0, 80) : '');
const c4b = cellOf(seed.skuA, sid0, period0);
ok(c4b && B.txt(c4b).indexOf('已安排') >= 0,
   '★★ 只是弹了确认、还没点确认时，这一格**没被动过**（不问就做也能让下面那条绿）',
   c4b ? B.txt(c4b) : '找不到格子');
// ★ 用 if 兜一下：没弹确认时（正是上面那条要抓的变异）这里不该整个崩掉 ——
//   测试半路崩了会被回放器判成「没跑完」，而不是「抓到了」，两者的下一步完全不同。
if (unBack) B.fire(unBack.querySelector('.btn--danger'), 'click');
const c3 = cellOf(seed.skuA, sid0, period0);
ok(c3 && B.txt(c3).indexOf('已安排') < 0, '★★ 确认之后这一格归零', c3 ? B.txt(c3) : '找不到格子');
ok(c3 && !c3.querySelector('[data-edit]') && !c3.querySelector('[data-unarrange]'),
   '★ 归零之后［改］［撤回］也跟着收回去', c3 ? c3.innerHTML.slice(0, 160) : '');
ok(/已撤回 \d+ 件/.test(B.txt(B.doc.getElementById('flash'))),
   '撤回的结果也说了一句', B.txt(B.doc.getElementById('flash')));

console.log('\n── 7. sheet.html · 没有计划记录的格子 ──');
const lastPeriod = seed.periods[seed.periods.length - 1];
const noLineBtn = B.doc.querySelector('[data-arrange="' + seed.skuA + '|' + sid0 + '|' + lastPeriod + '"]');
ok(!!noLineBtn, '★ 前置：最后一个月那一格也在网格上（没有计划记录不等于不显示）');
// ★ 兜一下：按钮没了的时候（正是某几条变异要抓的）测试不该整个崩掉 ——
//   半路崩会被回放器判成「没跑完」，而不是「抓到了」。
if (noLineBtn) B.fire(noLineBtn, 'click');
ok(/没有计划记录/.test(B.txt(B.doc.getElementById('flash'))),
   '★ 没有计划记录时说清为什么、并给出路，不是弹一个空筛选器',
   B.txt(B.doc.getElementById('flash')));
eq(B.doc.getElementById('dlgArrange').open, false, '这种情况不该打开筛选器');

console.log('\n── 8. sheet.html · 保留的老功能与版面规矩 ──');
const bodyTxt = B.txt(B.doc.getElementById('body'));
['待编排池', '排货单', '确认闸', '已排未发台账'].forEach(k =>
  ok(bodyTxt.indexOf(k) >= 0, '老功能还在：' + k));
ok(!!B.doc.getElementById('btnSubmit') && !!B.doc.getElementById('btnSave')
   && !!B.doc.getElementById('btnPlan'),
   '★ 头部三个动作都在：保存草稿 / 提交排货 / 打开供应链采购计划表');
noProseCheck(B.doc, 'sheet.html');
shellCheck(B.doc, 'sheet.html');

console.log('\n── 9. sheet.html · 编入排货单（单行 + 批量）→ 提交排货 → 投送 ──');
/* 安排两格，落成池里两行 —— 一行走单行「编入」，一行走「把勾中的编入排货单」。

   ★★ 两格故意取**同一条计划记录、同一个源仓、两个不同店铺** ——
     池里于是是两行「同键」，正是 Store.moveToConsignment 会判成歧义的形态。
     页面不把 seller_id 传下去的话，两次编入都会被拒。
   ★ 上一版这里用「两个不同的月份」绕开过。绕开的那个洞
     （排货单行根本不带 seller_id、匹配时也不看它）**2026-09-18 已经修掉**，
     所以现在正面测它。留着绕开的写法会让下一个人以为洞还在。 */
[sid0, other].forEach(sid => {
  const btn = B.doc.querySelector('[data-arrange="' + seed.skuA + '|' + sid + '|' + period0 + '"]');
  ok(!!btn, '★ 前置：店铺 ' + sid + ' 那一格有「安排发货」');
  if (btn) B.fire(btn, 'click');            // ★ 同上：按钮没了也不许崩
  const ins = [...B.doc.querySelectorAll('#arTable input.inp')];
  ok(ins.length > 0, '★ 前置：筛选器打开了，有可填的格（店铺 ' + sid + '）', '实际 ' + ins.length);
  if (ins.length) B.setVal(ins[0], String(take));
  B.fire(B.doc.getElementById('arOk'), 'click');
});
const poolWho = B.w.Store.sheet(seed.sheetId).pool
  .concat([{ seller_id: '?', line_id: '?', from_wid: '?' },
           { seller_id: '?', line_id: '?', from_wid: '?' }]);   // ★ 占位：池子没长出来时下面几条也不许崩
eq(B.w.Store.sheet(seed.sheetId).pool.length, 2, '★ 前置：池里落了两行');
ok(String(poolWho[0].seller_id) !== String(poolWho[1].seller_id),
   '★★ 前置：两行是**不同店铺**（同店铺的话「歧义」根本不存在，下面全是空转）',
   poolWho.map(p => p.seller_id).join('/'));
eq(String(poolWho[0].line_id), String(poolWho[1].line_id),
   '★★ 前置：两行是**同一条计划记录**（不同记录就撞不上同键）');
eq(String(poolWho[0].from_wid), String(poolWho[1].from_wid),
   '★★ 前置：两行是**同一个源仓**');
const whoFirst = String(poolWho[0].seller_id), whoSecond = String(poolWho[1].seller_id);

const poolFold2 = B.doc.getElementById('poolFold');
ok(!!poolFold2 && /已安排待编排 \d+ 件 \/ 2 行/.test(B.txt(poolFold2.querySelector('summary'))),
   '★★ 摘要那一行数出了 2 行（数不对的话，展开之前根本没法判断该不该展开）',
   poolFold2 ? B.txt(poolFold2.querySelector('summary')) : '');
ok(poolFold2 && !poolFold2.hasAttribute('open'),
   '★ 池里有货了，它仍然是收起的 —— 收起是常态，不是「空了才收」');

/* ★★ 两条路（批量 / 单行）**都**要在「池里还有一行同键」的时候跑一次。
   只让其中一条撞上歧义的话，另一条漏传 seller_id 也全绿 ——
   实测：第一版把批量排在池子只剩一行时跑，「批量不传 seller_id」那条变异
   **一条断言都没打红**，而它是个真的 bug。 */
function poolOf() { return B.w.Store.sheet(seed.sheetId).pool; }
function consLines() { return B.w.Store.sheet(seed.sheetId).consignments[0].lines; }
function lastSeller() {
  const ls = consLines();
  return ls.length ? String(ls[ls.length - 1].seller_id) : '（一行都没编进去）';
}

const poolRowsBefore = [...B.doc.querySelectorAll('[data-pool-target]')];
eq(poolRowsBefore.length, 2, '★ 前置：池里两行都在（两条路都要在这个状态下各跑一次）');

// ── 批量编入：此刻池里有两行同键，勾第一行
const picks = [...B.doc.querySelectorAll('[data-pool-pick]')];
eq(picks.length, 2, '★ 前置：两行都有勾选框', '实际 ' + picks.length);
const batchBtn = B.doc.getElementById('btnPoolBatch');
ok(!!batchBtn && !!B.doc.getElementById('poolBatchTarget'),
   '★ 批量编入的入口在展开后的池里（选一张排货单 + 一个按钮）');
B.fire(batchBtn, 'click');
ok(/一行都没勾/.test(B.txt(B.doc.getElementById('flash'))),
   '★★ 一行都没勾就点批量，说清楚是「没勾」不是「失败了」',
   B.txt(B.doc.getElementById('flash')));
eq(B.doc.querySelectorAll('[data-pool-target]').length, 2,
   '　而且什么都没挪走（空勾选被当成「全选」是最坏的一种）');
picks[0].checked = true;
B.fire(picks[0], 'change');
ok(/勾中 1 行/.test(B.txt(B.doc.getElementById('poolBatchSay'))),
   '★ 勾了几行、多少件，当场数出来', B.txt(B.doc.getElementById('poolBatchSay')));
B.fire(B.doc.getElementById('btnPoolBatch'), 'click');
eq(B.doc.querySelectorAll('[data-pool-target]').length, 1,
   '★★ 批量编入之后池里剩 1 行');
ok(/1 行 \/ \d+ 件编入排货单 #\d+/.test(B.txt(B.doc.getElementById('flash'))),
   '★ 批量的结果点名了挪了几行几件、进了哪张单', B.txt(B.doc.getElementById('flash')));
ok(!/都对得上|请说清是哪个店铺/.test(B.txt(B.doc.getElementById('flash'))),
   '★★ 批量没撞上歧义拒绝 —— 页面把 seller_id 传下去了',
   B.txt(B.doc.getElementById('flash')));
eq(lastSeller(), whoFirst,
   '★★ 批量编进去的是**勾的那一行**那个店铺，不是「池里第一条」',
   JSON.stringify({ got: lastSeller(), want: whoFirst, other: whoSecond }));
eq(String((poolOf()[0] || {}).seller_id), whoSecond,
   '★★ 反向：没勾的那个店铺那一笔原封不动留在池里',
   JSON.stringify(poolOf().map(p => p.seller_id)));

/* ── ★★ 编入之后那一格是「第三种状态」：池里没有了，但量在排货单上 ──
   pooled === 0 && consigned > 0。★ 这时**绝不许退回［安排发货］** ——
   「还没排过」和「排了且已编进单」长成一个样，而两者的下一步完全相反。 */
const cDone = cellOf(seed.skuA, whoFirst, period0);
ok(!!cDone, '★ 前置：编入之后那一格还在网格上（按钮换了，格子不该消失）');
ok(cDone && !cDone.querySelector('[data-arrange]'),
   '★★ 编入排货单之后那一格**没有**退回［安排发货］',
   cDone ? cDone.innerHTML.slice(0, 260) : '');
ok(cDone && !cDone.querySelector('[data-edit]') && !cDone.querySelector('[data-unarrange]'),
   '★★ 也没有［改］［撤回］—— 池里已经没有它了，这两个按钮动不了它',
   cDone ? cDone.innerHTML.slice(0, 260) : '');
const doneNote = cDone && cDone.querySelector('[data-consigned-note]');
ok(!!doneNote, '★★ 换成一句「已编入排货单 #N，要改请去那张单上动」',
   cDone ? B.txt(cDone) : '');
ok(doneNote && /已编入排货单 #\d+/.test(B.txt(doneNote)),
   '　那句话点名了是哪一张单', doneNote ? B.txt(doneNote) : '');
const doneJump = cDone && cDone.querySelector('[data-consigned-jump]');
ok(!!doneJump && /^#cons-/.test(doneJump.getAttribute('href')),
   '★ 给了跳到那张排货单的锚点（说了在哪张，就得能过去）',
   doneJump ? doneJump.getAttribute('href') : '');
/* ★★ 但不许把这一格做成死路：这条计划记录多半还有「还能排」的量，
   只给一句说明的话，人就再也没法为它排下一批了 —— 比「看起来没排过」更糟。
   ★ 按钮标成「再安排一批」、标记也换一个，两种状态仍然一眼分得开。 */
const doneMore = cDone && cDone.querySelector('[data-arrange-more]');
ok(!!doneMore, '★★ 仍然留了一条往下走的路：［再安排一批］（不许做成死路）',
   cDone ? cDone.innerHTML.slice(0, 260) : '');
eq(doneMore && doneMore.textContent.trim(), '再安排一批',
   '★★ 而且它不叫「安排发货」—— 「从没排过」和「排过、再排一批」必须一眼分得开');
ok(cDone && /已安排\s*\d+/.test(B.txt(cDone)) && /其中已编入单\s*\d+/.test(B.txt(cDone)),
   '★★ 格子上两个数分开写：已安排 N / 其中已编入单 M',
   cDone ? B.txt(cDone) : '');

/* ★ 反向证人：另一个店铺那一格**还在池里**，所以它仍然是［改］［撤回］。
   没有这一条的话，「编入后没有改/撤回」用「所有格子都没有改/撤回」也能绿。 */
const cStillPooled = cellOf(seed.skuA, whoSecond, period0);
ok(cStillPooled && cStillPooled.querySelector('[data-edit]')
   && cStillPooled.querySelector('[data-unarrange]'),
   '★★ 反向：还在池里的那一格仍然是［改］［撤回］',
   cStillPooled ? cStillPooled.innerHTML.slice(0, 200) : '');
ok(cStillPooled && !cStillPooled.querySelector('[data-consigned-note]'),
   '★★ 反向：它没有那句「已编入排货单」（恒显示那句也能让上面几条绿）',
   cStillPooled ? B.txt(cStillPooled) : '');

/* ── 再凑一对「同键」，好让单行「编入」也在这个状态下跑一次 ──
   ★ 不能拿刚编入的那一格重来：它已经是第三种状态、没有［安排发货］了（这正是上面刚验的）。
     所以换**第二个月**的两格 —— 它们俩之间同 line_id、同源仓、不同店铺。 */
const period1 = seed.periods[1];
[sid0, other].forEach(sid => {
  const btn = B.doc.querySelector('[data-arrange="' + seed.skuA + '|' + sid + '|' + period1 + '"]');
  ok(!!btn, '★ 前置：' + period1 + ' 店铺 ' + sid + ' 那一格有「安排发货」');
  if (btn) B.fire(btn, 'click');
  const ins2 = [...B.doc.querySelectorAll('#arTable input.inp')];
  ok(ins2.length > 0, '★ 前置：筛选器又打开了（' + period1 + ' / ' + sid + '）', '实际 ' + ins2.length);
  if (ins2.length) B.setVal(ins2[0], String(take));
  B.fire(B.doc.getElementById('arOk'), 'click');
});
eq(poolOf().length, 3, '★ 前置：池里现在 3 行（上一轮剩的 1 行 + 这一轮 2 行）');

/* 找出「有同键兄弟」的那一行 —— 不假设池子的排列顺序。
   ★ 找不到就硬失败：没有同键行的话，下面整段测的就不是歧义那件事了。 */
const parr = poolOf();
const idxSame = parr.findIndex((p, i) => parr.some((q, j) =>
  j !== i && q.line_id === p.line_id
  && String(q.from_wid) === String(p.from_wid)
  && String(q.seller_id) !== String(p.seller_id)));
ok(idxSame >= 0, '★★ 前置：池里确实有一对「同键不同店铺」的行（没有的话下面全是空转）',
   JSON.stringify(parr.map(p => p.line_id + '/' + p.from_wid + '/' + p.seller_id)));
const whoSingle = idxSame >= 0 ? String(parr[idxSame].seller_id) : '（没凑出同键行）';

// ── 单行「编入」：同样在两行同键的状态下跑
const poolSels = [...B.doc.querySelectorAll('[data-pool-target]')];
eq(poolSels.length, 3, '★ 待编排池里三行都画出来了');
const poolSel = idxSame >= 0 ? poolSels[idxSame] : null;
ok(!!poolSel, '★ 找得到那一行（data-pool-pick 的序号就是池里的序号）');
const poolBtn = poolSel ? poolSel.parentNode.querySelector('button') : null;
ok(!!poolBtn, '★ 池里那行有「编入」按钮（单行编入没被批量取代）');
const linesBefore = consLines().length;
if (poolBtn) B.fire(poolBtn, 'click');
eq(poolOf().length, 2, '★ 单行编入之后池里剩 2 行（量挪进了排货单）');
ok(/编入排货单 #\d+/.test(B.txt(B.doc.getElementById('flash'))),
   '编入的结果说了一句', B.txt(B.doc.getElementById('flash')));
ok(!/都对得上|请说清是哪个店铺/.test(B.txt(B.doc.getElementById('flash'))),
   '★★ 单行也没撞上歧义拒绝 —— 这条路同样传了 seller_id',
   B.txt(B.doc.getElementById('flash')));
eq(consLines().length, linesBefore + 1, '　排货单上真多了一行');
eq(lastSeller(), whoSingle,
   '★★ 单行编进去的是**点的那一行**那个店铺，不是「池里第一条」',
   JSON.stringify({ got: lastSeller(), want: whoSingle,
                    pool: poolOf().map(p => p.seller_id) }));
ok(!poolOf().some(p => String(p.line_id) === String(parr[idxSame >= 0 ? idxSame : 0].line_id)
                    && String(p.seller_id) === whoSingle),
   '★★ 反向：被挪走的正是那一笔，池里再也没有它',
   JSON.stringify(poolOf().map(p => p.line_id + '/' + p.seller_id)));

/* ── ★★ 第四种情形：同一格 pooled > 0 **且** consigned > 0 ──
   单行「编入」可以只编入一部分（那一行有数量输入框）。
   这时［改］［撤回］动的只是池里那部分 —— 不说的话，「改成 3」会被理解成
   这一格总共 3 件，而单里那几件一动不动。 */
ok(take >= 3, '★ 前置：take 够大，才能只编入一部分还剩下东西', 'take=' + take);
const pArr2 = poolOf();
const iPart = pArr2.findIndex(p => String(p.seller_id) === whoSecond
  && String(p.line_id) === String(poolWho[0].line_id));
ok(iPart >= 0, '★ 前置：找得到 ' + period0 + ' 那条还留在池里的行',
   JSON.stringify(pArr2.map(p => p.line_id + '/' + p.seller_id)));
const partSel = [...B.doc.querySelectorAll('[data-pool-target]')][iPart];
const partRow = partSel ? partSel.parentNode : null;
const partInp = partRow ? partRow.querySelector('input.inp') : null;
ok(!!partInp, '★ 前置：单行那一行有数量输入框（「只编入一部分」靠它）');
if (partInp) B.setVal(partInp, '2');
const partBtn = partRow ? partRow.querySelector('button') : null;
if (partBtn) B.fire(partBtn, 'click');

const cPart = cellOf(seed.skuA, whoSecond, period0);
ok(cPart && cPart.querySelector('[data-edit]') && cPart.querySelector('[data-unarrange]'),
   '★★ 池里还有剩，所以［改］［撤回］还在（这一格不是「第三种状态」）',
   cPart ? cPart.innerHTML.slice(0, 200) : '');
const partNote = cPart && cPart.querySelector('[data-consigned-note]');
ok(!!partNote && /另有 2 件已编入排货单 #\d+/.test(B.txt(partNote)),
   '★★ 同时说清：另有 2 件已经编入排货单（合成一个数就看不出这一半动不了）',
   partNote ? B.txt(partNote) : (cPart ? B.txt(cPart) : '找不到格子'));
ok(partNote && new RegExp('只动待编排的那 ' + (take - 2) + ' 件').test(B.txt(partNote)),
   '　连「这两个按钮只动几件」都点名了', partNote ? B.txt(partNote) : '');
// ★ 按钮旁边说了、点进去不说，等于没说 —— 弹窗里也要有
B.fire(cPart.querySelector('[data-edit]'), 'click');
ok(/已经编入排货单/.test(B.txt(B.doc.getElementById('arWhy'))),
   '★★ ［改］的弹窗里也说清了「另有 N 件已编入排货单，这次替换动不到」',
   B.txt(B.doc.getElementById('arWhy')));
B.fire(B.doc.getElementById('arCancel'), 'click');
eq(B.doc.getElementById('dlgArrange').open, false, '　取消之后弹窗关上，什么都没改');

/* ── 把剩下的行全勾上编进 **FBA 那张单**，池子清空 ──
   ★ 编进 FBA 单才走得到 fbaRow：「货件号」「装箱」两栏只在那张单上存在。 */
const rest = [...B.doc.querySelectorAll('[data-pool-pick]')];
eq(rest.length, 2, '★ 前置：剩下两行都有勾选框');
rest.forEach(x => { x.checked = true; B.fire(x, 'change'); });
ok(/勾中 2 行/.test(B.txt(B.doc.getElementById('poolBatchSay'))),
   '★ 勾了两行就数两行', B.txt(B.doc.getElementById('poolBatchSay')));
const batchTarget = B.doc.getElementById('poolBatchTarget');
ok(!!batchTarget && [...batchTarget.options].some(o => String(o.value) === String(seed.fbaCid)),
   '★ 前置：批量的下拉里选得到那张 FBA 单',
   batchTarget ? [...batchTarget.options].map(o => o.value).join('/') : '');
if (batchTarget) { batchTarget.value = String(seed.fbaCid); B.fire(batchTarget, 'change'); }
// ★ 池子空了的时候整块批量条根本不渲染，btnPoolBatch 是 null —— 兜一下，别崩
const lastBatch = B.doc.getElementById('btnPoolBatch');
if (lastBatch) B.fire(lastBatch, 'click');
ok(!B.doc.querySelector('[data-pool-target]'), '★★ 批量编入之后池子空了');

/* ============================================================
   9c. ★★ FBA 的「货件号」那一栏：投送**之前**
   ★ 原来这里是个让人填 FBA16XXXXXX 的输入框 —— 流程是反的：
     那个号是亚马逊在 confirmPlacementOption 之后才返回的，
     人看到这张表的那一刻它根本不存在，谁也填不出来。
   ============================================================ */
console.log('\n── 9c. sheet.html · 货件号那一栏（投送前）──');
const fbaTrs = [...B.doc.querySelectorAll('[id^="row-' + seed.fbaCid + '-"]')];
eq(fbaTrs.length, 2, '★ 前置：FBA 排货单上真有两行（没有的话下面全是空转）',
   fbaTrs.map(t => t.id).join('/'));
const shipTds = fbaTrs.map(tr => tr.querySelector('[data-shipment-cell]'));
ok(shipTds.every(td => !!td), '★ 每一行都有「货件号」那一格');
ok(shipTds.every(td => !td.querySelector('input')),
   '★★ 投送**前**那一格里没有 input —— 让人填一个此刻不存在的号，是流程反了',
   shipTds.map(td => td && td.innerHTML.slice(0, 80)).join(' | '));
ok(shipTds.every(td => /投送时由亚马逊分配/.test(B.txt(td))),
   '★★ 写的是「投送时由亚马逊分配」（不是空白 —— 空白说不出「为什么这里没有号」）',
   shipTds.map(td => B.txt(td)).join(' | '));
ok(!fbaTrs.some(tr => /未归属货件/.test(B.txt(tr))),
   '★★ 没有「未归属货件」红标 —— 投送前没有号是**正常状态**，报成错误是在骂用户',
   fbaTrs.map(tr => B.txt(tr)).join(' | '));
ok(shipTds.every(td => !B.txt(td).match(/FBA16/)),
   '★ 也没有拿占位符冒充真号', shipTds.map(td => B.txt(td)).join(' | '));

/* 装箱那 6 个格子**是**人工填的（裁定 A-8），没被一起删掉 —— 而且只剩这 6 个 */
fbaTrs.forEach((tr, i) => {
  const ins = [...tr.querySelectorAll('input')];
  eq(ins.length, 6, '★★ 第 ' + (i + 1) + ' 行只剩 6 个输入框（装箱那 6 个，货件号那个已经不是输入框）',
     ins.map(x => x.getAttribute('aria-label')).join(' | '));
  ins.forEach(x => B.setVal(x, '2'));
});
ok(B.w.Store.sheet(seed.sheetId).consignments
    .filter(c => String(c.cid) === String(seed.fbaCid))[0].lines.every(l => !!l.box),
   '★ 装箱数据真的写进去了（下面 G1 要靠它过闸）');

B.fire(B.doc.getElementById('btnSubmit'), 'click');
const gateItems = [...B.doc.querySelectorAll('.gate__item')];
eq(gateItems.length, 6, '★★ 6 项闸全部列出来（过的也列）—— 用的是 Shell.gates()');
ok(!!B.doc.querySelector('.gate__sum'), '★ 闸有汇总行（Shell.gates 的形状）');
const passed = gateItems.filter(i => /gate__item--pass/.test(i.className));
ok(passed.length > 0, '★ 通过的那几项也留在列表里（只列没过的就不叫「全列」）',
   gateItems.map(i => i.className).join(' | '));
eq(passed.length, 6, '★ 前置：这张排货表 6 项全过（否则下面投送那段无从跑起）',
   gateItems.filter(i => !/pass/.test(i.className)).map(i => i.textContent.slice(0, 50)).join(' | '));

const shipBtn = [...B.doc.querySelectorAll('button')].filter(b => /^投送/.test(b.textContent.trim()))[0];
ok(!!shipBtn, '★ 过闸之后排货单上才出现「投送」');
if (shipBtn) B.fire(shipBtn, 'click');    // ★ 没过闸就没有这个按钮 —— 不许在这里崩
const backdrop = B.doc.querySelector('.modal-backdrop');
ok(!!backdrop, '★ 投送前弹二次确认（Shell.confirmDanger，不可逆动作不许一键触发）');
ok(!!backdrop && !!backdrop.querySelector('.irreversible'),
   '★ 确认框里写明了不可撤销', backdrop ? backdrop.textContent.slice(0, 60) : '');
const modalSteps = backdrop ? backdrop.querySelectorAll('.modal__steps li').length : 0;
eq(modalSteps, 3, '★ 海外仓是 3 步（FBA 是 5 步）—— 两条链的形状不同，确认框里就看得见');
// ★ 没过闸就没有「投送」按钮、也就没有确认框 —— 那时这里不该整个崩掉
if (backdrop) B.fire(backdrop.querySelector('.btn--danger'), 'click');

const stepNames = [...B.doc.querySelectorAll('.steps__name')].map(x => x.textContent);
ok(stepNames.length > 0, '★★ 投送结果逐步列出来 —— 用的是 Shell.steps()',
   '一步都没列：' + B.txt(B.doc.getElementById('flash')));
eq(stepNames.length, 3, '★ 三步都列了（中途停下时，前几步是真发生过的，不许只说「失败」）',
   stepNames.join(' / '));
ok(/投送成功|部分成功|投送失败/.test(B.txt(B.doc.getElementById('flash'))),
   '投送结果也说了一句', B.txt(B.doc.getElementById('flash')));

/* ── ★★ 第五种状态：已投送 ──
   投送之后 consigned 归零（货不在我们手上了）。这时格子要是只看 arranged，
   又会回到 0 —— **「已经发出去了」和「从没排过」再一次长成一个样**。
   严重程度低一档（货真出手了、pending 那边有账），但**形状完全相同**，
   而形状相同的 bug 会以同样的方式再咬一次。 */
console.log('\n── 9b. sheet.html · 投送之后那一格 ──');
const shipped = B.w.Store.dispatchGrid(seed.sheetId)
  .filter(x => x.sku === seed.skuA && String(x.seller_id) === whoFirst)[0];
const sCell = shipped ? shipped.cells[0] : null;
ok(sCell && sCell.dispatched > 0,
   '★ 前置：Store 侧这一格确实有 dispatched（没有的话下面全是空转）',
   JSON.stringify(sCell && { pooled: sCell.pooled, consigned: sCell.consigned,
                             dispatched: sCell.dispatched, arranged: sCell.arranged }));
eq(sCell && sCell.consigned, 0, '★ 前置：投送之后 consigned 归零（这正是会骗人的地方）');
eq(sCell && sCell.arranged, 0,
   '★★ 前置：arranged 也归零了 —— 所以页面**不能**拿它当「动过没有」');

const cShip = cellOf(seed.skuA, whoFirst, period0);
ok(!!cShip, '★ 前置：那一格还在网格上');
/* ★ 断言要落在**数字区**（.cell__rows）上，不是整个格子：
   下面那句说明里也有「已投送 5 件」四个字，拿整格文本一测，
   把数字那一行整个删掉照样绿 —— 实测就是这么假绿过一次。 */
const shipNums = cShip && cShip.querySelector('.cell__rows');
ok(!!shipNums, '★ 前置：格子里有数字区');
ok(shipNums && /已投送\s*\d+/.test(B.txt(shipNums)),
   '★★ 格子的**数字区**里有「已投送 N」这一行 —— 不写的话这一格看起来就是从没排过',
   shipNums ? B.txt(shipNums) : '');
ok(cShip && !cShip.querySelector('[data-arrange]'),
   '★★ 没有退回［安排发货］（这是同一个形状的第三次）',
   cShip ? cShip.innerHTML.slice(0, 260) : '');
ok(cShip && !cShip.querySelector('[data-edit]') && !cShip.querySelector('[data-unarrange]'),
   '★★ 也没有［改］［撤回］—— 货已出手，改不了',
   cShip ? cShip.innerHTML.slice(0, 260) : '');
const shipNote = cShip && cShip.querySelector('[data-dispatched-note]');
ok(!!shipNote && /已投送 \d+ 件，货已出手/.test(B.txt(shipNote)),
   '★★ 换成一句只读的「已投送 N 件，货已出手」', shipNote ? B.txt(shipNote) : (cShip ? B.txt(cShip) : ''));
ok(shipNote && /排货单 #\d+/.test(B.txt(shipNote)),
   '　点名了是哪一张排货单', shipNote ? B.txt(shipNote) : '');
const shipJump = cShip && cShip.querySelector('[data-dispatched-jump]');
ok(!!shipJump && /^#cons-/.test(shipJump.getAttribute('href')),
   '★ 给了跳过去的锚点', shipJump ? shipJump.getAttribute('href') : '');
ok(cShip && !!cShip.querySelector('[data-arrange-more]'),
   '★★ 仍然能［再安排一批］—— 投送 5 件不等于这条记录的 300 件都排完了，不许做成死路',
   cShip ? cShip.innerHTML.slice(0, 260) : '');

/* ★ 反向证人：一个**从没排过**的格子仍然是［安排发货］、也没有那两句话。
   没有这一条的话，「已投送的格子有 N」用「所有格子都有 N」也能绿。 */
const cVirgin = cellOf(seed.skuB, (B.w.Store.dispatchGrid(seed.sheetId)
  .filter(x => x.sku === seed.skuB)[0] || {}).seller_id, period0);
ok(!!cVirgin, '★ 前置：找得到一个从没排过的格子（skuB 的）');
ok(cVirgin && !!cVirgin.querySelector('[data-arrange]'),
   '★★ 反向：没排过的那一格仍然是［安排发货］', cVirgin ? cVirgin.innerHTML.slice(0, 200) : '');
ok(cVirgin && !cVirgin.querySelector('[data-dispatched-note]')
   && !cVirgin.querySelector('[data-consigned-note]')
   && !cVirgin.querySelector('[data-arrange-more]'),
   '★★ 反向：它没有「已投送 / 已编入单 / 再安排一批」（恒显示也能让上面几条绿）',
   cVirgin ? B.txt(cVirgin) : '');
ok(cVirgin && B.txt(cVirgin).indexOf('已投送') < 0,
   '★★ 反向：它上面也没有「已投送」那个数', cVirgin ? B.txt(cVirgin) : '');

// 合计列与货号块头也要看得见 —— 整块看起来「没动过」是同一个毛病换个尺度
const shipRow = cShip ? cShip.closest('tr') : null;
const gsum = shipRow ? shipRow.querySelector('td.gsum') : null;
ok(gsum && /已投送\s*\d+/.test(B.txt(gsum)),
   '★★ 合计列里也有「已投送」那一行', gsum ? B.txt(gsum) : '找不到合计列');
const shipBlock = cShip ? cShip.closest('.sheet') : null;
const blockHead = shipBlock ? shipBlock.querySelector('.sheet__stat') : null;
ok(blockHead && /已投送 \d+ 件/.test(B.txt(blockHead)),
   '★★ 货号块头上也写了「已投送 N 件」', blockHead ? B.txt(blockHead) : '找不到块头');

/* ============================================================
   10. ★★ FBA 的货件号：投送**之后**才有，而且是系统回填的那个
   ★ 只验「投送前没有输入框」是不够的 —— 一格恒显示「投送时由亚马逊分配」
     也能让那条绿。两头都要验：投送后必须换成**真的那个号**。
   ============================================================ */
console.log('\n── 10. sheet.html · 货件号：投送之后由系统回填 ──');
function fbaConsOf() {
  return B.w.Store.sheet(seed.sheetId).consignments
    .filter(c => String(c.cid) === String(seed.fbaCid))[0];
}
const fbaBefore = fbaConsOf();
ok(fbaBefore && fbaBefore.lines.length > 0, '★ 前置：FBA 单上有行',
   JSON.stringify(fbaBefore && fbaBefore.lines.length));
ok(fbaBefore && fbaBefore.lines.every(l => !l.shipment_no),
   '★★ 前置：投送前 Store 侧一行都没有货件号（所以界面上也不可能填得出来）',
   JSON.stringify(fbaBefore && fbaBefore.lines.map(l => l.shipment_no)));

const fbaPanel = B.doc.getElementById('cons-' + seed.fbaCid);
ok(!!fbaPanel, '★ 前置：页面上有那张 FBA 排货单的面板');
const fbaShipBtn = fbaPanel
  ? [...fbaPanel.querySelectorAll('button')].filter(b => /^投送/.test(b.textContent.trim()))[0] : null;
ok(!!fbaShipBtn, '★ FBA 单上也出现了「投送」');
if (fbaShipBtn) B.fire(fbaShipBtn, 'click');
const bd2 = B.doc.querySelector('.modal-backdrop');
eq(bd2 ? bd2.querySelectorAll('.modal__steps li').length : 0, 5,
   '★ FBA 是 5 步（海外仓 3 步）—— 中间那一步「STA建货件」正是号的来源');
if (bd2) B.fire(bd2.querySelector('.btn--danger'), 'click');

/* ★★ 这张单的 cid 是偶数，store.js 故意让它**首次投送时最后一步超时**。
   于是这一段顺带验到一件更强的事：货件号在「STA建货件」那一步就已经有了，
   **不依赖整条链跑完** —— 回填要是挂在「全部成功」上，停在最后一步的这种单
   就会既发不出去、又不知道自己的货件号是哪个。 */
const fbaSteps = [...(B.doc.getElementById('cons-' + seed.fbaCid) || B.doc)
  .querySelectorAll('.steps li')];
eq(fbaSteps.length, 5, '★ 五步都列出来了', fbaSteps.map(x => B.txt(x)).join(' / '));
ok(fbaSteps.some(li => li.getAttribute('data-state') === 'manual'),
   '★ 前置：这一次确实停在了最后一步（偶数 cid 的 FBA 单首投必然如此）',
   fbaSteps.map(x => x.getAttribute('data-state')).join('/'));

const fbaAfter = fbaConsOf();
const realNo = fbaAfter && fbaAfter.lines[0] ? String(fbaAfter.lines[0].shipment_no || '') : '';
ok(/^FBA16/.test(realNo),
   '★★ 投送之后 Store 侧真的把货件号回填到行上了（不回填的话，这一行发的是哪个货件永远没人知道）',
   realNo || '（空）');
ok(fbaAfter && fbaAfter.lines.every(l => !!l.shipment_no), '　每一行都回填了',
   JSON.stringify(fbaAfter && fbaAfter.lines.map(l => l.shipment_no)));

const shownNo = B.doc.querySelector('[data-shipment-no="row-' + seed.fbaCid + '-0"]');
ok(!!shownNo, '★★ 页面上那一格换成了货件号',
   B.txt(B.doc.querySelector('[data-shipment-cell="row-' + seed.fbaCid + '-0"]')));
eq(shownNo ? shownNo.textContent.trim() : '', realNo,
   '★★ 显示的就是 Store 里那个号 —— 不是占位符、也不是页面自己编的');
ok(!B.doc.querySelector('[data-shipment-wait="row-' + seed.fbaCid + '-0"]'),
   '★★ 反向：「投送时由亚马逊分配」不见了（恒显示它也能让投送前那几条绿）',
   B.txt(B.doc.querySelector('[data-shipment-cell="row-' + seed.fbaCid + '-0"]')));
const fbaTrAfter = B.doc.getElementById('row-' + seed.fbaCid + '-0');
ok(fbaTrAfter && !fbaTrAfter.querySelector('[data-shipment-cell] input'),
   '★ 投送之后那一格里也没有输入框（号是事实，不是可改的字段）');

noProseCheck(B.doc, 'sheet.html（投送后）');

// 界面文案不出现 ★；按钮/链接文案后面不加箭头
const seenText = visibleText(B.doc) + ' ' + visibleText(A.doc);
ok(seenText.length > 400, '★ 前置：确实读到了成篇的界面文案（读空字符串也算绿是最坏的一种）',
   '只读到 ' + seenText.length + ' 字');
const starAt = seenText.indexOf('★');
ok(starAt < 0, '★ 界面文案里没有「★」', starAt < 0 ? '' : seenText.slice(Math.max(0, starAt - 60), starAt + 60));
const clickables = [...ownClickables(B.doc), ...ownClickables(A.doc)];
ok(clickables.length > 5, '★ 前置：确实有一批按钮可查', '只有 ' + clickables.length + ' 个');
const arrowed = clickables.map(b => b.textContent.trim()).filter(t => /→|➔|»/.test(t));
eq(arrowed.length, 0, '按钮文案后面没有箭头');

/* ============================================================ */

console.log('');
if (errors.length) {
  console.log('✗ 运行期错误 ' + errors.length + ' 条：');
  errors.forEach(e => console.log('  - ' + e.slice(0, 400)));
}
console.log(fails.length || errors.length
  /* ★ 收尾行的措辞必须是全仓统一的那一句「✗ N 项失败」——
     replay.sh 的闸 3 靠它分开「被打红了」和「半路崩了」。
     这里原本写的是「项断言失败」，于是**这张页面的每一条变异都被判成半路崩了**，
     20 条一条也出不了分。★ 措辞漂一个字，整张证据表就回放不动。 */
  ? '✗ ' + fails.length + ' 项失败' + (errors.length ? ' + ' + errors.length + ' 条运行期错误' : '')
    + (fails.length ? '：\n  - ' + fails.join('\n  - ') : '')
  : '★ 全绿');
process.exit(fails.length || errors.length ? 1 : 0);
