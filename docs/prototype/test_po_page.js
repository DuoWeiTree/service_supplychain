/* ============================================================
   po.html —— 采购单详情页的加载自检

   ★ 为什么要有这个文件（和 test_page.js 同一个理由）：
     渲染失败发生在 `body.innerHTML=''` 之后、appendChild 之前时，
     页面看起来「加载完了、只是没有内容」——「渲染失败」和「本来就没数据」
     长得一模一样。`node --check` 是绿的，读代码也看不出来。

     ★ 判据因此是：**表格必须真的长出行来**，不是「没有报错」。
     所有「遍历后没发现问题」型的断言，都配了一条**非空断言**：
     遍历了 0 行也算绿，是本项目已经踩过三次的坑。

   跑法:  node test_po_page.js
         PO_PAGE=/path/to/变异过的.html node test_po_page.js     ← 回放变异用
   依赖:  jsdom（没装会告诉你怎么装，并以「跳过」退出，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据（2026-09-18）—— 下面每一条都**真的被打红过一次**

   ⚠ 2026-09-18 03:47 的改版（chip 配色从 'ext' 改成 'dim'）让 #7' / #8' 的
     from 串**当场失效** —— 表还写着 `chip(r.receipt_no, 'ext')`，文件里已经是 'dim'。
     ★ 照着表回放会被闸 1 拒（命中 0 次），而不是静默跑出一片绿 —— 闸 1 确实接住了它。
     ★★ 教训：**变异证据会随页面改版过期，而过期的证据长得和有效的一模一样。**
        改了页面就要重跑一遍证据表，别只跑测试 —— 测试绿不代表证据还回放得动。
        （本轮已全部重跑，from 串已按当前文件订正。）

   ⚠ 表里原有的 #7 / #8 已作废并删除：它们守的是「页面自己回退去取 doc_no」
     这条分支，而根因已在 store.js 修掉（poProgress 改认写入方的字段名），
     那条分支不复存在。**修 A 的动作本身改变了什么算合法输入** ——
     所以不是把旧断言改到能过，而是连断言带证据一起换成 #7' / #8'。

   回放方法：用**共用回放器** `./replay.sh`（三个页面 agent 同一套闸）：

       ./replay.sh po.html test_po_page.js '标签' <<'MUT'
       要被替换掉的原文（原样贴，不用转义）
       --
       替换成什么
       MUT

     它有两道闸：闸 1 = from 命中数 ≠ 1 就拒绝出分（挡「变异没生效」），
     闸 2 = 变异后内联脚本 node --check 不过就拒绝出分（挡「页面被打炸了，
     红的全是装载级噪声、目标断言根本没跑到」）。
     ★ 跑之前先 `./replay.sh --self-test po.html test_po_page.js`，
       证明两道闸是活的 —— 否则只是把信任从断言转移到了闸上。

   跑之前请 `export NODE_PATH=<jsdom 所在的 node_modules>`。没设也不会出假结论 ——
   闸 3 会拦住并说「测试根本没跑起来（jsdom 没装？NODE_PATH 没设？）」。
   （这条闸是 2026-09-18 从本页报上去的缺口补的，实测复现已改口，见文件末尾。）

   ★ 整表重跑：`./replay.sh --table evidence-po.tbl`
     —— 证据会随页面改版过期，**改完 po.html 请跑这一条**，别只跑测试。

   ★ 下表每一条都用 replay.sh 实跑过，全部命中 1 处、三道闸都过。
     出分行里的「共 N 条」要一起看：N 掉下来 = 测试没跑到底，那次的红不作数。

   | # | po.html 里的替换（原文 → 改成）                          | 打红了 |
   |---|--------------------------------------------------------|-------|
   | 1 | `        body.appendChild(sellerRow(r, lock));`
       | → `        if (r.selectable) body.appendChild(sellerRow(r, lock));`
       | 「确实有勾不动的行」「一行都没少」「这两行现在是灰的」×3 |
   | 2 | `qty(g.purchase_planned)` → `qty(g.demand_total)`
       | 「计划采购量的**数**真的印出来了(500)」×1
       | ★ 「期望销量合计(100)」那条**仍然绿** —— 说明两条各认各的数，不是连坐 |
   | 3 | `var k = it.sku + '|' + it.period;`
       | → `var k = it.sku + '|' + it.period + '|' + it.seller_id;`
       | 「合并成 1 行」「数量 = 两店之和」「合了几个店铺」「点开展开」「两个店铺名」×5 |
   | 4 | `return '<li>' + esc(s) + '</li>';` → `return '';`
       | 「9999 出现在页面上」「原文没被摘要」×2 |
   | 5 | `function readOnly() { return po.state !== '草稿'; }`
       | → `function readOnly() { return false; }`（恒**不**只读）
       | 「所有 data-mut 控件全部禁用」「说清了为什么只读」×2 |
   | 6 | 同上 → `function readOnly() { return true; }`（恒只读）
       | **反向那条**「草稿态下没有被禁用」×1（外加 21 条连锁 / 共 57 条，页面什么都做不了了）
       | ⚠ 这一条最初只报「转红 10 条 / 共 27 条」—— **测试崩在半路**，
       |   另外 29 条一条都没跑。详见下面「测试自己不许崩」。|
   | 7'| `                  ? chip(r.receipt_no, 'dim')`
       | → `                  ? chip('', 'dim')`（单号画成空白）
       | 「每一格单号都真的显示出来了」「是真的那几个单号」×2 |
   | 8'| 同上 → `                  ? chip('RK-FAKE-' + i, 'dim')`（单号**编一个**）
       | **只**打红「是真的那几个单号」×1 —— 非空那条仍然绿
       | ★ 7'/8' 也是一对：「有没有显示」和「显示的是不是真的」是两件事，
       |   各有各的证伪路径。只验 7' 的话，编出来的假单号照样全绿 |
   | 9 | `          + p.receipts.map(function (r, i) {`
       | → `          + p.receipts.slice(1).map(function (r, i) {`（少画一行）
       | 「表格真的长出行了」「行数 ≡ 回执条数」「取到了单号列」「真的那几个单号」×4 |

   ------------------------------------------------------------
   ★ 2026-09-18 改版（说明文字收进 fold）之后补的两条 —— 变异实跑，各命中 1 处

   | # | po.html 里的替换（原文 → 改成）                            | 打红了 |
   |---|----------------------------------------------------------|-------|
   |10 | `<div id="banner"></div>` 之后塞回
   |   | `<div class="note"><p>故意留下的说明方块</p></div>`          | 「没有 .note / .gate」×1 |
   |11 | `details class="fold"` → `details class="foldx"`（5 处）    | 「收进了 fold」×1 |

   ★ #10 / #11 打红的是**不相交**的两条 —— 这正是它们各自存在的理由：
     只数 .note 的话，把说明整段删掉（信息扔了）也能全绿；
     只数 fold 的话，fold 之外再铺一屏说明也能全绿。两条必须一起看。
   ★ 改版没有作废任何一条旧断言：第 2 / 6 / 7 / 8 节守的文案
     （「计划采购量」「期望销量合计」「供应链要买」「运营想卖」「勾不动」
     「没有过滤掉」「尚未到货」「不是「没有数据」」「货可能在做」）
     **一个字没改**，只是换了载体（.note → .fold / .flash / .empty）。

   ★ 第 5 / 6 行是**一对**，而且是**互斥**的：
     #5 只打红正向那条、完全不碰反向那条；#6 打红反向那条。
     这才说明红的原因是「只读跟着 po.state 走」，不是连坐。
     只验一头的话，「恒为只读」也能让第 7 节全绿 —— 这就是第 6 行存在的全部理由。
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
const PAGE = process.env.PO_PAGE || path.join(DIR, 'po.html');   // ★ 变异回放入口
const fails = [];
const errors = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}
function eq(got, want, name) {
  ok(got === want, name, '实际 ' + JSON.stringify(got) + '，应为 ' + JSON.stringify(want));
}

/* ── 0. 先在纯 node 里造一份**有数据**的状态 ────────────────────
   空采购单 + 空计划渲染出 0 行是**对的**，拿它当测试等于什么都没测。
   照抄 test_buyer.js 的做法：加货品 → 填期望销量和采购量 → 提交计划 → 建采购单。 */
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
  // ★ 必须挑一个**跨店铺**的货号，否则「同一货号展开成多个店铺行」根本测不到
  const sku = Object.keys(group)
    .filter(k => new Set(group[k].map(r => r.seller_id)).size >= 2)[0];
  if (!sku) throw new Error('剧本里没有跨店铺的货号 —— 这个测试的前提不成立');

  S.addClaims(plan.plan_id, group[sku].map(r =>
    ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid })));
  const p2 = S.plan(plan.plan_id);
  const period = p2.periods[0];
  // ★ 每个店铺填**不同**的期望销量：填一样的话，「数量 = 两者之和」
  //   和「数量 = 两者之一 × 2」会长得一模一样
  const perSeller = {};
  p2.claims.filter(c => c.sku === sku).forEach((c, i) => {
    const v = 40 + i * 20;
    S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, period, v);
    perSeller[c.seller_id] = (perSeller[c.seller_id] || 0) + v;
  });
  // ★ 计划采购量**故意**和期望销量合计不同 —— 这正是界面必须把两个数都给出来的理由
  const purchasePlanned = 500;
  S.setPurchase(p2.plan_id, sku, period, purchasePlanned);
  const sub = S.submitPlan(p2.plan_id);
  if (!sub.ok) throw new Error('计划提交失败：' + JSON.stringify(sub));

  const line = S.lines().filter(l => l.sku === sku && l.period === period)[0];
  if (!line) throw new Error('没铸出计划记录');

  const wid = S.get().warehouses[0].wid;
  const made = S.createPo({ supplier: '东莞宠具', wid: wid, opt_uid: '8801', purchaser_id: '2001' });
  if (!made.ok) throw new Error('建单失败：' + JSON.stringify(made));

  const sellers = Object.keys(line.demand_by_seller || {});
  // ★ 这一份快照要在造第二张单**之前**拿：第二张单会吃掉 sellers[0] 的一部分未组单量，
  //   混进来的话，「数量 = 两店之和」那条断言算的就不是我以为的那个数了。
  //   两个场景各用各的状态，互不污染。
  const jsonDraft = mem['scm_proto'];

  // ★ 第二张单：已下单 + 已到货。块三（本地仓到货进度）的表格路径只有
  //   **真的有入库单**时才走得到 —— 只用那张草稿单测，那段代码一行都没被跑过。
  //   到货是事实驱动的：不推进时间就没有回执。
  const madeB = S.createPo({ supplier: '东莞宠具', wid: wid, opt_uid: '8801', purchaser_id: '2001' });
  if (!madeB.ok) throw new Error('建第二张单失败：' + JSON.stringify(madeB));
  const addB = S.addPoItems(madeB.po_id, [
    { line_id: line.line_id, seller_id: sellers[0], units: 10, price: 12.02 }
  ]);
  if (!addB.ok || addB.added !== 1) throw new Error('第二张单加货失败：' + JSON.stringify(addB));
  const placedB = S.placePo(madeB.po_id);
  if (!placedB.ok) throw new Error('第二张单下单失败：' + JSON.stringify(placedB));
  let ticks = 0;
  while (!(S.po(madeB.po_id).receipts || []).length && ticks < 10) { S.tick(); ticks++; }
  const gotB = (S.po(madeB.po_id).receipts || []).length;
  if (!gotB) throw new Error('推进了 ' + ticks + ' 天还是没有入库回执 —— 块三测不到');

  return {
    json: jsonDraft, poId: made.po_id, planId: p2.plan_id,
    sku, period, lineId: line.line_id,
    sellers, perSeller, purchasePlanned,
    demandTotal: sellers.reduce((t, s) => t + Number(line.demand_by_seller[s] || 0), 0),
    arrivedJson: mem['scm_proto'], arrivedPoId: madeB.po_id,
    arrivedReceipts: gotB, arrivedTicks: ticks,
    arrivedUnits: S.poProgress(madeB.po_id).arrived,
    arrivedTotal: S.poProgress(madeB.po_id).total,
    arrivedDocNos: (S.po(madeB.po_id).receipts || []).map(r => r.doc_no)
  };
}

const seed = seedState();
console.log('\n── 0. 种子 ──');
ok(seed.sellers.length >= 2, '★ 造出一个跨店铺的货号（否则「分店看」测不到）',
   seed.sku + ' 只有 ' + seed.sellers.length + ' 个店铺');
ok(seed.purchasePlanned !== seed.demandTotal,
   '★ 计划采购量 ≠ 期望销量合计（否则「两个数」那条断言是空转）',
   seed.purchasePlanned + ' vs ' + seed.demandTotal);
console.log('     采购单 ' + seed.poId + ' · 记录 ' + seed.lineId
  + ' · 计划采购量 ' + seed.purchasePlanned + ' · 期望销量合计 ' + seed.demandTotal
  + '（' + seed.sellers.map(s => s + ':' + seed.perSeller[s]).join('、') + '）');

/* ── 1. 加载页面 ───────────────────────────────────────────── */
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.stack || e.message)));
// ★ 「回退必须有声」是本项目的规矩，所以页面在拒绝超量添加时**应该**打日志。
//   这类日志不算失败 —— 但也不能只是过滤掉，要**断言它确实出现过**，
//   否则哪天页面改成静默吞掉，测试照样绿。
const expected = [];
vc.on('error', (...a) => {
  const s = a.map(String).join(' ');
  if (/cells-backfilled/.test(s)) { expected.push(s); return; }             // store 装载期回填，良性
  if (/po\.items-partially-rejected/.test(s)) { expected.push(s); return; }  // 页面：部分被拒有声
  if (/po\.items-rejected/.test(s)) { expected.push(s); return; }            // store：同一件事
  errors.push('console.error: ' + s);
});

const dom = new JSDOM(fs.readFileSync(PAGE, 'utf8'), {
  runScripts: 'outside-only',      // ★ 我们手工按顺序 eval，顺带能抓到抛错的是哪个脚本
  url: 'http://localhost/po.html?po=' + encodeURIComponent(seed.poId),
  virtualConsole: vc, pretendToBeVisual: true
});
const w = dom.window, doc = w.document;
w.scrollTo = function () {};
w.localStorage.setItem('scm_proto', seed.json);

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

function fire(el, type) {
  const ev = doc.createEvent('HTMLEvents');
  ev.initEvent(type, true, true);
  el.dispatchEvent(ev);
}
const flat = n => (n ? n.textContent.replace(/\s+/g, ' ') : '');
const all = sel => [...doc.querySelectorAll(sel)];

/* ── 2. 筛选器真的长出行来 ───────────────────────────────────── */
console.log('\n── 1. 候选表：真的长出行了吗 ──');
const candBody = doc.getElementById('candBody');
ok(!!candBody, '页面里有 #candBody');

const grpRows = all('#candBody tr[data-lvl="grp"]');
const sellRows = all('#candBody tr[data-lvl="sell"]');
ok(grpRows.length > 0, '★ 候选表长出了货号合计行（不是「没报错」）',
   '实际 0 行；表内文字：' + flat(candBody).slice(0, 120));
ok(sellRows.length > 0, '★ 候选表长出了店铺行', '实际 0 行');

const mine = all(`#candBody tr[data-lvl="sell"][data-line="${seed.lineId}"]`);
ok(mine.length >= 2,
   '★★ 同一个货号展开成了多个店铺行（看的时候分店）',
   '记录 ' + seed.lineId + ' 只有 ' + mine.length + ' 行');
ok(mine.every(tr => /\S/.test(flat(tr.children[4]))),
   '每个店铺行都写了店铺名（不是空的）',
   mine.map(tr => JSON.stringify(flat(tr.children[4]))).join(' / '));

/* ── 3. 两个数，粒度不同，都要显示 ───────────────────────────── */
console.log('\n── 2. ★ 计划采购量 与 期望销量合计 是两个数 ──');
const myGrp = all(`#candBody tr[data-lvl="grp"][data-line="${seed.lineId}"]`)[0];
ok(!!myGrp, '找得到这条记录的货号合计行');
const grpTxt = flat(myGrp);
ok(/计划采购量/.test(grpTxt), '合计行上写了「计划采购量」', grpTxt.slice(0, 160));
ok(/期望销量合计/.test(grpTxt), '合计行上写了「期望销量合计」', grpTxt.slice(0, 160));
ok(grpTxt.includes(String(seed.purchasePlanned)),
   '★ 计划采购量的**数**真的印出来了（' + seed.purchasePlanned + '）', grpTxt.slice(0, 200));
ok(grpTxt.includes(String(seed.demandTotal)),
   '★ 期望销量合计的**数**真的印出来了（' + seed.demandTotal + '）', grpTxt.slice(0, 200));
ok(/供应链要买/.test(grpTxt) && /运营想卖/.test(grpTxt),
   '★ 并且标清了各自是谁的量 —— 光给两个数还是会被当成一类东西', grpTxt.slice(0, 200));

/* ── 4. 反向断言：未提交时**不是**只读 ─────────────────────────
   ★ 没有这一条，「恒为只读」也能让第 7 节全绿。 */
console.log('\n── 3. ★ 反向：未提交时不是只读 ──');
const mutBefore = all('[data-mut]');
ok(mutBefore.length > 0, '★ 页面上确实有可变更控件（非空断言：0 个也能让下一条空转）',
   '一个 data-mut 都没有');
const disabledBefore = mutBefore.filter(n => n.disabled || n.getAttribute('aria-disabled') === 'true');
// 「移除（0 件）」和「添加勾选的 0 行」本来就该是禁用的 —— 那是「没东西可做」，不是只读
const shouldBeLive = mutBefore.filter(n =>
  n.getAttribute('data-pick') || n.getAttribute('data-units') ||
  n.getAttribute('data-price') || n.getAttribute('data-act') === 'add' ||
  n.id === 'btnPlace' || n.id === 'btnSaveDraft');
ok(shouldBeLive.length > 0, '★ 其中有一批本该是活的（非空断言）', '' + shouldBeLive.length);
ok(shouldBeLive.every(n => !n.disabled),
   '★★ 草稿态下这些控件**没有**被禁用（反向断言）',
   shouldBeLive.filter(n => n.disabled).map(n =>
     n.tagName + '[' + (n.id || n.getAttribute('data-act') || n.getAttribute('data-pick')) + ']'
   ).join('、') + '；总禁用数 ' + disabledBefore.length);

/* ── 5. 超量添加：拒绝原文必须原样出现在页面上 ───────────────── */
console.log('\n── 4. 超量添加：原文上屏 ──');
const k0 = seed.lineId + '|' + seed.sellers[0];
const u0 = doc.querySelector(`input[data-units="${CSS_ESC(k0)}"]`);
const add0 = doc.querySelector(`button[data-act="add"][data-k="${CSS_ESC(k0)}"]`);
function CSS_ESC(s) { return s.replace(/["\\]/g, '\\$&'); }
ok(!!u0 && !!add0, '找得到这一行的数量框和「添加」按钮',
   'units=' + !!u0 + ' add=' + !!add0);

const openBefore = Number(seed.perSeller[seed.sellers[0]]);
if (u0 && add0) {
  u0.value = '9999';
  fire(u0, 'input');
  add0.click();
}
const rejectTxt = flat(doc.getElementById('rejectBox'));
ok(/9999/.test(rejectTxt),
   '★ 拒绝原文里的「本次 9999 件」出现在页面上', rejectTxt.slice(0, 220) || '(rejectBox 是空的)');
ok(/未组单/.test(rejectTxt) && new RegExp('需求 ' + openBefore).test(rejectTxt),
   '★★ 原文**没有被摘要**：需求 / 已组单 / 已下单 都还点名在上面',
   rejectTxt.slice(0, 260));
eq(w.Store.po(seed.poId).items.length, 0, '被拒的那项没混进采购单');
ok(expected.some(s => /po\.items-partially-rejected/.test(s)),
   '★ 而且这件事**有声**：控制台留了 po.items-partially-rejected',
   '收到的预期日志：' + expected.length + ' 条');

/* ── 6. 勾两个店铺行 → items 合并成一行 ───────────────────────── */
console.log('\n── 5. ★ 勾选两个店铺行 → items 合并成一行 ──');
const k1 = seed.lineId + '|' + seed.sellers[1];
[k0, k1].forEach(k => {
  const u = doc.querySelector(`input[data-units="${CSS_ESC(k)}"]`);
  if (u) { u.value = String(seed.perSeller[k.split('|')[1]]); fire(u, 'input'); }
  const box = doc.querySelector(`input[data-pick="${CSS_ESC(k)}"]`);
  if (box) { box.checked = true; fire(box, 'change'); }
});
const btnAdd = doc.getElementById('btnAddPicked');
ok(!!btnAdd && !btnAdd.disabled, '勾了两行之后「添加勾选」可点',
   btnAdd ? ('disabled=' + btnAdd.disabled + ' / ' + flat(btnAdd)) : '按钮不存在');
if (btnAdd && !btnAdd.disabled) btnAdd.click();

const poNow = w.Store.po(seed.poId);
eq(poNow.items.length, 2, '★ 底下存的是**未合并**的：两个店铺 = 两行');

const itemGrp = all('#itemBody tr[data-lvl="item"]');
ok(itemGrp.length > 0, '★ items 区真的长出行了（非空断言）',
   'itemBody：' + flat(doc.getElementById('itemBody')).slice(0, 140));
eq(itemGrp.length, 1, '★★ 显示层按「货号 + 月份」合并成了 1 行');
const wantUnits = Number(seed.perSeller[seed.sellers[0]]) + Number(seed.perSeller[seed.sellers[1]]);
const grpNumCell = itemGrp[0] ? flat(itemGrp[0].children[2]) : '';
ok(grpNumCell.includes(String(wantUnits)),
   '★★ 合并行的数量 = 两个店铺之和（' + seed.perSeller[seed.sellers[0]] + ' + '
     + seed.perSeller[seed.sellers[1]] + ' = ' + wantUnits + '）',
   '这一格实际是 ' + JSON.stringify(grpNumCell));
ok(/2 个店铺合并/.test(flat(itemGrp[0])),
   '★ 合并行自己说清了它合了几个店铺（否则合并是不可见的）', flat(itemGrp[0]).slice(0, 160));

// 点开 → 各店明细，且明细行数 = 未合并的行数
// ★ 必须判空再点：itemGrp 为空时 `itemGrp[0].click()` 会**抛异常并中断整个测试**，
//   后面 29 条断言一条都不会跑 —— 而中断长得和「变异把它们打红了」一模一样。
//   2026-09-18 实测：#6（恒只读）那次就是这样，56 条只跑了 27 条。
//   ★ 判据：测试自己**不许崩**。崩了就分不清「断言抓到了」和「断言没跑到」。
if (itemGrp.length) itemGrp[0].click();
else ok(false, '★ 没有合并行可点开 —— 后面「展开成各店明细」那两条是空转（已点名，不是静默跳过）');
const itemLines = all('#itemBody tr[data-lvl="line"]');
eq(itemLines.length, 2, '★ 点开后展开成各店明细（两层的第二层）');
const lineTxt = itemLines.map(flat).join(' | ');
ok(seed.sellers.every(s => lineTxt.includes(w.Store.sellerName(s))),
   '★ 两个店铺名都在明细里（合并没把店铺归属吃掉）', lineTxt.slice(0, 220));

/* ── 7. selectable=false 的行仍在表里 ─────────────────────────── */
console.log('\n── 6. ★ 勾不动的行留在表里标灰，不是被过滤掉 ──');
const blocked = all('#candBody tr[data-sel="no"]');
ok(blocked.length > 0,
   '★ 确实有勾不动的行（非空断言：0 行的话下一条就是空转）',
   '一行都没有 —— 刚才两个店铺的未组单量应该都归零了');
const mineNow = all(`#candBody tr[data-lvl="sell"][data-line="${seed.lineId}"]`);
eq(mineNow.length, mine.length,
   '★★ 组完单之后，这条记录的店铺行**一行都没少**（没被过滤掉）');
const blockedMine = mineNow.filter(tr => tr.getAttribute('data-sel') === 'no');
eq(blockedMine.length, 2, '★ 这两行现在是灰的（而不是消失）');
ok(blockedMine.every(tr => /已经全部组单|全部组单|下单了/.test(flat(tr))),
   '★★ 并且每一行都写了 why_not —— 人得知道「为什么勾不动」',
   blockedMine.map(tr => flat(tr).slice(-90)).join(' || '));
ok(/勾不动/.test(flat(doc.getElementById('candSum'))) &&
   /没有过滤掉/.test(flat(doc.getElementById('candSum'))),
   '★ 汇总行也把「被挡下的那一侧」统计出来了',
   flat(doc.getElementById('candSum')).slice(0, 200));

/* ── 8. 提交采购 → 整页只读 ─────────────────────────────────── */
console.log('\n── 7. 提交采购之后整页只读 ──');
const btnPlace = doc.getElementById('btnPlace');
ok(!!btnPlace && !btnPlace.disabled, '「提交采购」此刻可点');
if (btnPlace) btnPlace.click();

const modal = doc.querySelector('.modal-backdrop .modal');
ok(!!modal, '★ 先弹二次确认（不可逆的动作不许一点就走）');
ok(modal && /不可撤销|只读|CreatePurchaseOrder/.test(flat(modal)),
   '★ 确认框里写清了不可逆', modal ? flat(modal).slice(0, 220) : '-');
const confirmBtn = doc.querySelector('.modal__foot .btn--danger');
ok(!!confirmBtn, '确认框里有「我确认」按钮');
if (confirmBtn) confirmBtn.click();

const poPlaced = w.Store.po(seed.poId);
eq(poPlaced.state, '已下单', '★ 真的下单了（不是只改了界面）');

const mutAfter = all('[data-mut]');
ok(mutAfter.length > 0,
   '★ 只读之后页面上仍然有可变更控件（非空断言：控件都不渲染的话，'
   + '「全部禁用」是空转）', '一个 data-mut 都没有');
const stillLive = mutAfter.filter(n => !n.disabled);
ok(stillLive.length === 0,
   '★★ 所有 data-mut 控件全部禁用（' + mutAfter.length + ' 个）',
   '还活着的：' + stillLive.map(n =>
     n.tagName + '[' + (n.id || n.getAttribute('data-act') ||
       n.getAttribute('data-pick') || n.getAttribute('data-edit') || '?') + ']').join('、'));
const banner = flat(doc.getElementById('banner'));
ok(/只读/.test(banner) && /货可能在做|钱可能已付/.test(banner),
   '★ 而且说清了**为什么**只读 —— 一片灰不算解释', banner.slice(0, 220));

/* ── 9. 到货进度：为空时要说清是「还没到货」不是「没有数据」 ──── */
console.log('\n── 8. 到货进度 ──');
const prog = flat(doc.getElementById('progBox'));
ok(/已到 \/ 总量|已到/.test(prog), '进度块渲染了「已到 / 总量」', prog.slice(0, 160));
ok(/尚未到货/.test(prog) && /不是「没有数据」/.test(prog),
   '★★ 刚下单没到货时，明写了「尚未到货（不是没有数据）」', prog.slice(0, 240));
ok(/领星|status_shipped/.test(prog), '带上了领星侧状态（镜像，我们不写）', prog.slice(0, 200));

/* ── 10. 另一张单：真的到了货 ─────────────────────────────────
   ★ 上面那张单从头到尾没有入库回执，块三的**表格**路径一行都没被跑过。
     「没到货时的说法」测过了不等于「到货后的表格」画得出来 ——
     这正是「遍历了 0 行也算绿」的另一种形态。 */
console.log('\n── 9. 到货之后：入库单表格真的画出来了吗 ──');
ok(seed.arrivedReceipts > 0,
   '★ 种子里那张单真的收到了回执（非空断言：0 条的话下面全是空转）',
   '推进了 ' + seed.arrivedTicks + ' 天，回执 ' + seed.arrivedReceipts + ' 条');

const vc2 = new VirtualConsole();
const errors2 = [], expected2 = [];
vc2.on('jsdomError', e => errors2.push('jsdomError: ' + (e.stack || e.message)));
vc2.on('error', (...a) => {
  const s = a.map(String).join(' ');
  if (/cells-backfilled/.test(s)) { expected2.push(s); return; }   // store 装载期回填，良性
  // ★ 这里**不再**过滤 receipt 相关的日志。store.js 修好之后，
  //   `po.receipt-missing-doc-no` 只会在写入方字段名又变了时出现 ——
  //   那种时候要的是把这一页打红，不是悄悄放行。
  errors2.push('console.error: ' + s);
});
const dom2 = new JSDOM(fs.readFileSync(PAGE, 'utf8'), {
  runScripts: 'outside-only',
  url: 'http://localhost/po.html?po=' + encodeURIComponent(seed.arrivedPoId),
  virtualConsole: vc2, pretendToBeVisual: true
});
const w2 = dom2.window, doc2 = w2.document;
w2.scrollTo = function () {};
w2.localStorage.setItem('scm_proto', seed.arrivedJson);
[...doc2.querySelectorAll('script[src]')].forEach(sc => {
  try { w2.eval(fs.readFileSync(path.join(DIR, sc.getAttribute('src')), 'utf8')); }
  catch (e) { errors2.push('★ ' + sc.getAttribute('src') + ' 抛出: ' + (e.stack || e)); }
});
[...doc2.querySelectorAll('script:not([src])')].forEach((sc, i) => {
  try { w2.eval(sc.textContent); }
  catch (e) { errors2.push('★ 内联脚本 #' + i + ' 抛出: ' + (e.stack || e)); }
});

const rcptRows = [...doc2.querySelectorAll('#progBox table tbody tr')];
ok(rcptRows.length > 0, '★ 入库单表格真的长出行了（不是「没报错」）',
   'progBox：' + (doc2.getElementById('progBox') || {}).textContent);
eq(rcptRows.length, seed.arrivedReceipts, '★ 一条回执都没少（行数 ≡ 回执条数）');
const progTxt2 = (doc2.getElementById('progBox') || {}).textContent.replace(/\s+/g, ' ');
ok(progTxt2.includes(String(seed.arrivedUnits) + ' / ' + seed.arrivedTotal) ||
   (progTxt2.includes(String(seed.arrivedUnits)) && progTxt2.includes(String(seed.arrivedTotal))),
   '★ 「已到 / 总量」是真数（' + seed.arrivedUnits + ' / ' + seed.arrivedTotal + '）',
   progTxt2.slice(0, 200));

/* ★ 入库单号：守的是**用户看见了什么** —— 单号真的印在页面上。
   2026-09-18 这里原本有两条断言守「页面回退取 doc_no」「回退有声」：
   那时 Store.poProgress 认的字段名和 tick 写进去的对不上，页面自己捞了一把。
   ★ 根因已经在 store.js 里修掉了（poProgress 改认写入方的 doc_no），
     那条回退分支**已经不存在**，两条断言一并删掉 ——
     守一个不会再发生的分支，只会让人以为那条路还活着。
   留下的是下面这两条：它们问的不是「兜底成没成」，而是「单号显示出来没有」。 */
const noCells = rcptRows.map(tr => tr.children[0].textContent.replace(/\s+/g, ' ').trim());
ok(noCells.length > 0, '★ 取到了入库单号那一列（非空断言）', '' + noCells.length);
ok(noCells.every(t => t && t !== '无单号' && !/undefined|null/.test(t)),
   '★★ 每一格入库单号都真的显示出来了 —— 不是空白、不是 undefined',
   JSON.stringify(noCells));
ok(seed.arrivedDocNos.every(no => noCells.some(t => t.includes(no))),
   '★★ 而且是**真的那几个单号**（' + seed.arrivedDocNos.join('、') + '），不是编的',
   JSON.stringify(noCells));
// ★ po.receipt-missing-doc-no 故意**不**放进 expected：它要是出现了，
//   说明写入方的字段名又变了，那就该把这一页打红，而不是过滤掉。
ok(errors2.length === 0, '★ 这一页加载全程没有预期外的异常',
   errors2.slice(0, 5).join('\n      '));

/* ── 11. 版面：说明文字收进 fold，不铺在屏幕上 ─────────────────
   ★ 判据是**屏幕上没有段落式说明方块**，而不是「文字变少了」——
     后者没法证伪（少一个字也算少）。.note / .gate 是上一版满屏说明的载体，
     所以数它们：一个都不许有。
   ★ 与之配对的是 fold 那条：只删不收等于把信息扔了，两条必须一起看。
   ★ 这里数的是**只读之后**的页面 —— 只读横幅、被拒清单、到货空态
     这三处原来都是 .note，正是最容易漏掉的那几处。 */
console.log('\n── 10. 版面：说明进 fold，不进屏幕 ──');
const noteNodes = [...doc.querySelectorAll('.note, .gate')];
ok(noteNodes.length === 0,
   '★ 页面上没有 .note / .gate 说明方块（说明属于文档，不属于屏幕）',
   '还剩 ' + noteNodes.map(n => flat(n).slice(0, 40)).join(' ｜ '));
ok(doc.querySelectorAll('details.fold').length >= 1,
   '★ 而说明一个字都没删 —— 收进了 <details class="fold">',
   'fold 数 ' + doc.querySelectorAll('details.fold').length);

/* ── 12. 收尾 ────────────────────────────────────────────────── */
console.log('\n── 11. 运行期异常 ──');
ok(errors.length === 0, '★ 加载与交互全程没有预期外的异常',
   errors.slice(0, 6).join('\n      '));
console.log('     （预期内的「有声回退」日志 ' + expected.length + ' 条，已断言过）');

console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败：\n  - ' + fails.join('\n  - ')
                                 : '★ 全绿'));
process.exit(fails.length ? 1 : 0);

/* ============================================================
   ★ 已结：replay.sh 的「测试根本没跑」缺口已补成闸 3（2026-09-18）

   下面这段留着不是备忘，是**判据本身**：闸 3 存在的理由。
   ★ 已实测改口：不设 NODE_PATH 跑一条必定命中的真变异，
     现在报「闸 3 拒绝出分：基线跑下来一条断言都没执行」（退出码 4），
     而不再是「★ 这才是真的『这条路没有断言守着』」。
   ★ 验的是**它对原案改口了没有**，不是它自检时自称活着 ——
     自检只能证明闸在，证不了闸拦的是当初那件事。

   ------------------------------------------------------------
   ★★ 仍未闭合的一种：**测试自己崩在半路**（2026-09-18 实测，已报 ui-ops）

   闸 3 问的是「跑了几条」，它挡住了「一条都没跑」。
   但还有一档它放过去了：**跑了一半，然后抛异常中断**。

   本文件第 5 节原来有一句无保护的 `itemGrp[0].click()`。
   变异 #6（恒只读）让 items 一行都长不出来 → `itemGrp` 为空 →
   `undefined.click()` 抛异常 → 整个测试**在第 27 条上死掉**，
   后面 29 条（含第 7 节「提交后整页只读」全部）一条都没跑。

   而 replay.sh 照样给它出了分：`转红 10 条`。
   ★ 那 10 条红是真的，但**中断和「被打红」长得一模一样** ——
     要是崩的位置在目标断言**之前**，就会得到一份「红得挺多、目标一条没中」的证据，
     和假证据 ③ 同形，只是病灶从页面挪到了测试自己身上。
   ★ 闸 2（对页面 node --check）看不见它：页面语法好得很，崩的是测试。

   → 本文件的处置：**测试自己不许崩**。凡是要 `[0]` 取元素再调方法的地方，
     先判空；空了就 `ok(false, ...)` 点名说「这几条是空转」，而不是让它抛。
     修完 #6 从「转红 10 条 / 共 27 条」变成「转红 22 条 / 共 57 条」。
   → replay.sh 已按此补上闸 3 的第三档（ui-ops 落地）：
     测试若**没有打印它自己的收尾行**（`★ 全绿` / `✗ N 项失败`），
     就是中断，拒绝出分。这个判据不会误伤早退型测试：早退也会走到收尾行
     （test_shift.js 的 `return finish()` 实测验过）——
     ★ 而比对断言数分不开这两者：27 和 5 都是「掉下来了」。**选对信号比加严阈值有用。**
     ★ 已重跑原案验活（不是只看它自检）：把上面那处守卫拆回去做成一份崩溃版测试，
       #6 之下报「没有打印收尾行 —— 它是半路崩的，跑到第 27 条（基线 56 条）」并拒绝出分；
       同一份测试配一条无害变异则正常出分，说明闸不是恒拒。

   ------------------------------------------------------------
   ★ 附：本页原始缺口的完整描述（闸 3 就是照它实现的）

   replay.sh 挡住了三种假证据里的两种：
     ① 变异没生效（闸 1：from 命中数 ≠ 1）
     ③ 变异把页面打炸了（闸 2：node --check）

   还有第四种，两道闸都看不见 —— **测试根本没执行**：

     本文件没有 jsdom 时会明说跳过、`exit 0`、**一条 ✗ 都不打**。
     这时两道闸全过（变异确实落进文件了、语法也没问题），
     replay.sh 于是打印：

         · <标签>  全绿 —— 变异确实生效了（两道闸都过），却一条都没打红。
             ★ 这才是真的「这条路没有断言守着」。

     ★ 它斩钉截铁地说了一句**反的话**。
       「没有断言守着」的下一步是**去补断言、或删掉那段代码**；
       而真相是断言活得好好的，只是没跑。
       比起沉默，一个自信的错误结论更贵 —— 它会让人动手去删还活着的防线。

   ★ 判据：闸看的是**文件**（改没改、语法对不对），
     而这一种的病灶在**运行**（跑没跑、跑了几条）。两者不同维，所以挡不住。

   修法（给 replay.sh）：加第三道闸 —— 变异前先跑一次基线，
   记下断言总条数 N；变异后那次若断言数 ≠ N（尤其是 0），就拒绝出分并说
   「测试没跑到底」，而不是报「没有断言守着」。
   ★ 基线那次顺带还能证明「改之前是绿的」—— 否则本来就红的断言会被算进战果。

   ── 实际落地时 ui-ops 收窄了一处，收窄得对 ──
   判据没有用「断言数 ≠ N」，而是「= 0」：严格相等会**误伤早退型测试**
   （test_shift.js 的 A 条变异让测试在 `if (!shifts.length) return finish()`
   提前返回，17 → 5 条，那是正常形态不是没跑）。
   取而代之的是把「共几条」打进出分行，让掉得异常时人一眼看得见。
   ★ 这个取舍立刻兑现了：本页 #6 的「共 27 条」就是这样被看出来的 ——
     闸没拦，但数字把它顶到了脸上，于是查出了上面那个「测试崩在半路」。
   ============================================================ */
