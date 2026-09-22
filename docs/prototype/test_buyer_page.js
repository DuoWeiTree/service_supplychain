/* ============================================================
   buyer.html —— 真加载一遍页面的自检

   ★ 为什么是这个形态：
     `node --check` 绿、读代码也看不出来的那种缺陷，长在**运行期**：
     `body.innerHTML=''` 之后 appendChild 之前抛一下，页面就变成
     「加载完了、只是没有内容」—— 而「渲染失败」和「本来就没数据」
     在界面上长得一模一样。所以本文件的判据一律是
     **表格真的长出行来 / 文案真的出现在页面上**，不是「没有报错」。

   跑法:  node test_buyer_page.js
   依赖:  jsdom（没装会明说跳过，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据
     · 2026-09-18 首跑 16 条（自写的一次性回放器，基线全绿）
     · 同日页面改版（说明收进 fold、换配色 token）后，**整表 18 条用
       `./replay.sh` 原地重跑**：17 条出分且转红清单与首跑逐条一致，
       M18 被闸 1 拒 —— 那次拒绝暴露的是**断言太松**（见 M18a~d），已收紧。
     · 收紧之后**整表 21 条再跑一遍，21 条全部出分**。
     · 最后用 `./replay.sh --table evidence-buyer.tbl` 跑全表：
       **表里 21 条 / 实际跑 21 条 / 出分 21 条，三道闸各拒 0 条。**
       （「表里几条 / 跑了几条」要核对 —— 少跑的那几条是**静默**消失的，
         汇总照样打印、数字照样自洽，只有对着表数一遍才看得出来。）
     · 这一跑对应的 buyer.html：`md5 a2b102bd32bbdabf5c7f1e8f44998889`

   留在这里的是**结论**，不是脚本 —— 脚本的价值在当时（证明这一版断言不是
   空转），留进仓库会变成没人跑也没人删的死代码，而且断言一改它就全过期。
   ★ 以后谁要改下面这些断言，先看一眼「它当初是被什么打红的」——
     如果你的改动让这一列里的某条变异不再打红它，那条断言就已经空转了。

   ★★ 下面这张表记的是**改动要点**；可以照抄回放的**原文串在
       `evidence-buyer.tbl`**，跑法 `./replay.sh --table evidence-buyer.tbl`。
       ★ 两份**分工不同，别合并**：要点跟着行为走、长期有效；
         串跟着字面走、改版即失效（只对上面那个 md5 的文件成立）。

     这段是 2026-09-18 改写的。原来那句担保是「变异串是完整的原文替换，
     可以照抄回放（原文在对应函数里唯一出现一次）」。

     ★ 那句担保**从写下那天起就是假的** —— 它担保了一件这张表里**根本没有**
       的东西：表里一直只有要点，没有串可抄。
     ★ 它能活下来，是因为**没人兑现的承诺不会失败**。
       直到同日一次**零行为变更**的改版（`'<div data-empty="'` →
       `'<div class="empty" data-empty="'`、`<ul style="margin:6px…">` → `<ul>`、
       说明搬进 `<details class="fold">`），有人真去贴串，才发现 18 行里
       7 行对不上号 —— 而**断言一条都没失效，烂的是串不是判据**。

     ★ 所以这类东西不能等它出错来暴露，只能**主动去兑现一次**。
       `evidence-buyer.tbl` 就是把担保落到实处：串真的在那儿，
       `--table` 一跑就知道它还成不成立。
     ★ 为什么先改这句话、再补那 7 行：**数据过期了人会怀疑，担保还在人就不会。**
       一句不再成立的担保会把下一个人直接送进闸 1 的一片拒绝里，
       而他那时分不清是断言烂了还是串漂了。

   | # | 把 buyer.html 故意改成                                | 转红的断言（条数） |
   |---|------------------------------------------------------|------------------|
   | M1| renderPos: `r.kept.forEach(… appendChild(poRow(po)))` → 空操作 | 7：长出 2 行 / 两行各自在 / 领星单号两条 / ②开关后出现 / ③清空后回来 |
   | M2| poRow 的 order_sn 那一列 `<td>…</td>` → `<td></td>`    | 2：已下单带 CGD、草稿写「还没下单」 |
   | M3| renderPoEmpty 三个分支都渲染成「暂无数据」              | 12：三种文案两两不同 ×3 + 各自的出路与成因 |
   | M4| never 分支判据 `!all.length` → `!open.length`          | 5：②判成 all-done / ②文案与出路 / ①≠② |
   | M5| filtered 分支去掉 `<ul>` 成因清单                       | 2：③说清哪个条件筛掉多少 / 空单单独说破 |
   | M6| showCreateFail 的 `E(detail)` → 固定串「创建失败」       | 3：detail 原样显示 / missing_wid / missing_opt_uid |
   | M7| 弹窗去掉「建完之后不可编辑」那句注                        | 1：线框那句注在界面上 |
   | M8| renderPlans 的 `planQuery({state…})` → 不传 state       | 4：滤真的滤掉了 / 每行草稿 / 每行已提交 / 两半相加=全部 |
   | M9| 计划 MSKU 条件 `if (f.msku && …)` → `if (false)`        | 2：按 MSKU 查得到 / 不存在的滤成 0 行 |
   |M10| `dropped.no_items++` → `dropped.period++`（空单混进 period）| 1：空单被时间过滤时单独说破 |
   |M11| renderDoneBar 的 N=0 分支 → `bar.innerHTML = ''`        | 1：出路那一行 0 张时也说话 |
   |M12| 「显示已完成」按钮不再置 showDone                        | 2：点了开关真的出现 / 空状态消失 |
   |M13| 建单成功后不写 `#poResult`（只靠跳转）                   | 2：留下链接 / 链接指向新单 |
   |M14| `stat(drafts,'未处理')` → `stat(all.length,…)`          | 1：未处理 = 草稿 1 张 |
   |M15| 页面上「这里没有 MSKU 过滤，换成了店铺」那句话删掉         | 1：取舍写在页面上 |
   |M16| setDisabled 恒不禁用                                    | 2：第一页上一页禁用 / 只有 1 页下一页禁用 |

   ------------------------------------------------------------
   ★ 2026-09-18 改版（说明文字收进 fold）之后补的两条 —— 变异实跑，各命中 1 处

   |  # | 把 buyer.html 故意改成                                    | 转红的断言 |
   |----|---------------------------------------------------------|-----------|
   | M17| `<div id="poResult"></div>` 之后塞回
   |    | `<div class="note"><p>故意留下的说明方块</p></div>`         | 2：p1 / p5 两处「没有 .note / .gate」 |
   | M18| ~~`details class="fold"` → `foldx`（5 处）~~ **已作废，见下** | ~~1：「收进了 fold」~~ |

   ★ M17 / M18 打红的是**不相交**的两条 —— 这正是它们各自存在的理由：
     只数 .note 的话，把说明整段删掉（信息扔了）也能全绿；
     只数 fold 的话，fold 之外再铺一屏说明也能全绿。两条必须一起看。

   ------------------------------------------------------------
   ★ M18 作废的经过 —— ★★ 这一段是「闸 1 拒绝 ≠ 变异写错」的判例，别删

   M18 用 `./replay.sh` 回放时被**闸 1 拒**（命中 5 次，闸要 1 次）。
   ★ 当时最省事的反应是去改 from 串。那条路是死的 ——
     **不是变异写错，也不是页面改版，是 M18 与它守的断言天生配不上**：

     旧断言 `querySelectorAll('details.fold').length >= 1`，而页面上有 3~5 个 fold。
     要让它转红必须**同时**改掉全部，而「一次只改一处」正是闸 1 存在的理由。
     ★ 反过来说：**这条断言拆掉 4 个 fold 也照样绿** ——
       它声称守「说明都收着」，实际只守「至少还剩一个 fold」。

   ★ 所以真正该动的是**断言**。判据换成 ui-ops 在四个运营页上定的那套
     （按 id 点名 + `.fold__body` 字数下限 + 默认收起），M18 随之拆成四条
     **各打红一条、互不相交**的变异 —— 每一维都有自己的证伪路径：

   |  #  | 把文件故意改成                                  | 转红的断言 |
   |-----|-----------------------------------------------|-----------|
   | M18a| `id="whyPlanFilter"` → `id="whyPlanFilterX"`   | 1：#whyPlanFilter 还在（点名） |
   | M18b| 留 `<details>` 外壳，把 `.fold__body` 掏到 67 字 | 1：里面有货（≥ 200） |
   | M18c| `<details class="fold" id="whyPlanFilter">` 加 `open` | 1：默认是收起的 |
   | M18d| `WHY_IDS = ['whyPoFilter','whyPlanFilter']` → `[]` | 1：点名清单 ≥ 2（非空断言） |

   ★ M18d 守的是这一节自己的空转：WHY_IDS 一空，下面那个 forEach 遍历 0 项，
     ①②③ 三条**一条都不会跑**，而测试全绿。本项目三次「测试看起来过了」
     都是这个形态 —— 所以凡遍历型断言必配一条非空断言。
   ★ M18a 只打红 1 条（而不是 3 条）是**故意的**：`if (!why) return;`
     之后另两条是**跳过**不是**通过**。第一条已经红了，失败看得见；
     但要知道这时断言总数会从 80 掉到 78 —— 总数变了就是有断言没跑到。

   ★★ 一般化（已转给 ui-ops 做进汇总层）：**闸 1 拒绝有三种成因，处置互不相同**
       · 多数被拒            → 页面改版了，整表重跑刷新
       · 零星被拒 · 命中 0   → from 串写错，改这一条的串
       · 零星被拒 · 命中 >1  → ★ **断言太松**，改 from 串永远修不好。
                               「命中 >1」= 这东西在页面上有好几份，
                               而断言只要求有一份。
   ------------------------------------------------------------
   ★ M18a~c **都没有**打红「取舍写在页面上」那条（M15 守的那条）—— 这也是对的：
     改 id / 加 open / 掏掉**另一个** fold 都不影响 M15 守的那段文字，
     M15 守的是**文字在不在**，fold 这几条守的是**它铺在屏幕上还是收着**。
     两件事，两条路，各红各的。

   ★ 改版没有作废任何一条旧断言：M7（弹窗那句「建完之后不可编辑」）和
     M15（「这里没有 MSKU 过滤，换成了店铺」）守的文字**一个字没删**，
     只是搬进了 <details class="fold"> —— textContent 照旧包含它们。

   ★ M8 **没有**打红「滤成草稿后仍有 ≥1 行」—— 这是对的，而且正是重点：
     「每行都是草稿」单独立不住（遍历 0 行也全过），必须和那条**非空断言**
     配成一对才算数。本项目三次「测试看起来过了、其实没测到目标」都是这个形态。
   ★ M4 与 M11 是**一对方向相反**的变异：一个让 all-done 被 never 吞掉，
     一个让「0 张也要说话」那条失声。只验一头的话，两种写死都能全绿。
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
const HTML = fs.readFileSync(path.join(DIR, 'buyer.html'), 'utf8');
const STORE_SRC = fs.readFileSync(path.join(DIR, 'assets/store.js'), 'utf8');

const fails = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

/* ════════════════════════════════════════════════════════════
   一、在纯 node 里造状态（不经页面）
   ★ 空剧本渲染出 0 行是**对的**，拿它当测试等于什么都没测。
   ════════════════════════════════════════════════════════════ */

function newStore() {
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
  vm.runInContext(STORE_SRC, sb);
  return { S: sb.window.Store, dump: () => mem['scm_proto'] };
}

/** 提交一张计划，铸出计划记录 —— 没有它一条候选都没有，也就建不出带货品的采购单 */
function submitOnePlan(S) {
  const plan = (S.get().plans || []).filter(p => !(p.claims || []).length)[0];
  const hit = S.catalog(plan.plan_id, { keyword: '', only: 'all', limit: 300 });
  const group = {};
  (hit.rows || []).filter(r => r.selectable)
    .forEach(r => (group[r.sku] = group[r.sku] || []).push(r));
  const sku = Object.keys(group)
    .filter(k => new Set(group[k].map(r => r.seller_id)).size >= 2)[0];
  if (!sku) throw new Error('剧本里找不到跨店铺的货号 —— 种子变了，这个测试要跟着改');
  S.addClaims(plan.plan_id, group[sku].map(r =>
    ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid })));
  const p2 = S.plan(plan.plan_id);
  const period = p2.periods[0];
  (p2.claims || []).filter(c => c.sku === sku)
    .forEach((c, i) => S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, period, 60 + i * 20));
  S.setPurchase(p2.plan_id, sku, period, 200);
  const sub = S.submitPlan(p2.plan_id);
  if (!sub.ok) throw new Error('提交计划失败：' + JSON.stringify(sub));
  const line = S.lines().filter(l => l.sku === sku && l.period === period)[0];
  return { plan_id: p2.plan_id, sku, period, line };
}

/** 建一张带货品的采购单；placed=true 就顺手下单 */
function makePo(S, ctx, { units = 20, placed = false } = {}) {
  const wid = S.get().warehouses[0].wid;
  const r = S.createPo({ supplier: 'SUP-01 · 东莞宠悦制品厂', wid, opt_uid: '8801', purchaser_id: '2001' });
  if (!r.ok) throw new Error('建单失败：' + JSON.stringify(r));
  const cand = S.poCandidates({ sku: ctx.sku }).rows
    .filter(x => x.line_id === ctx.line.line_id && x.selectable)[0];
  if (!cand) throw new Error('没有可挑的候选 —— 种子变了');
  const add = S.addPoItems(r.po_id, [{ line_id: cand.line_id, seller_id: cand.seller_id, units, price: 12.5 }]);
  if (!add.ok || add.added !== 1) throw new Error('加货品失败：' + JSON.stringify(add));
  if (placed) {
    const p = S.placePo(r.po_id);
    if (!p.ok) throw new Error('下单失败：' + JSON.stringify(p));
  }
  return r.po_id;
}

/* ════════════════════════════════════════════════════════════
   二、加载页面
   ════════════════════════════════════════════════════════════ */

function loadPage(json) {
  const errors = [], expected = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => {
    const s = e.stack || e.message || String(e);
    // ★ 建单成功后页面会 location.href 跳 po.html —— jsdom 不实现导航。
    //   这不是失败，但也不能只是过滤掉：收进 expected，由断言证明它确实发生过。
    if (/Not implemented: navigation/.test(s)) { expected.push(s); return; }
    errors.push('jsdomError: ' + s);
  });
  vc.on('error', (...a) => {
    const s = a.map(String).join(' ');
    // store.js 装载期的回填提示，良性（与 test_page.js 同一条过滤）。
    // ★ 它来自 store 不是本页，所以只过滤、不断言 —— 本页管不着它在不在。
    if (/cells-backfilled/.test(s)) return;
    // ★ 「拒绝必须有声」是本项目的规矩，所以页面拒绝建单时**应该**打 console.error。
    //   同样收进 expected 并由断言证明，否则哪天页面改成静默吞掉，测试照样绿。
    if (/po\.create-rejected/.test(s)) { expected.push(s); return; }
    errors.push('console.error: ' + s);
  });

  const dom = new JSDOM(HTML, {
    runScripts: 'outside-only',
    url: 'http://localhost/buyer.html',
    virtualConsole: vc, pretendToBeVisual: true
  });
  const w = dom.window, doc = w.document;
  w.scrollTo = function () { };
  w.localStorage.setItem('scm_proto', json);

  function run(code, label) {
    try { w.eval(code); }
    catch (e) { errors.push('★ ' + label + ' 抛出: ' + (e.stack || e)); }
  }
  // ★ 顺序：先所有 src（store → shell），再所有内联（mount → 主脚本）——
  //   这保持了内联脚本之间的相对次序，正是页面依赖的次序
  [...doc.querySelectorAll('script[src]')].forEach(sc => {
    const p = path.join(DIR, sc.getAttribute('src'));
    if (!fs.existsSync(p)) { errors.push('缺少脚本 ' + sc.getAttribute('src')); return; }
    run(fs.readFileSync(p, 'utf8'), sc.getAttribute('src'));
  });
  [...doc.querySelectorAll('script:not([src])')].forEach((sc, i) =>
    run(sc.textContent, '内联脚本 #' + i));

  return { w, doc, errors, expected };
}

function fire(doc, el, type) {
  const ev = doc.createEvent('HTMLEvents');
  ev.initEvent(type, true, true);
  el.dispatchEvent(ev);
}
const flat = s => String(s || '').replace(/\s+/g, ' ').trim();
/* ★ 取节点文本时不许让测试自己崩：节点不在正是某些变异的后果，
   崩掉会把「失败清单」一起弄没，于是看不出还有哪些断言本来也该红。 */
const textOf = (doc, sel) => { const n = doc.querySelector(sel); return n ? flat(n.textContent) : ''; };

/* ════════════════════════════════════════════════════════════
   第 1 节 · 有采购单时，表格真的长出行来
   ════════════════════════════════════════════════════════════ */

console.log('\n── 1. 有采购单：表格真的长出行来 ──');

const h1 = newStore();
const ctx1 = submitOnePlan(h1.S);
const draftPo = makePo(h1.S, ctx1, { units: 20 });
const placedPo = makePo(h1.S, ctx1, { units: 15, placed: true });
const p1 = loadPage(h1.dump());

ok(p1.errors.length === 0, '装载期没有异常', p1.errors.join('\n      '));

const body1 = p1.doc.getElementById('poBody');
ok(!!body1, '页面里有 #poBody');
ok(body1 && body1.children.length === 2,
   '★ 采购单表真的长出 2 行（不是「没报错」）',
   '实际 ' + (body1 ? body1.children.length : '-') + ' 行；空状态='
   + flat(p1.doc.getElementById('poEmpty').textContent).slice(0, 60));
ok(!!body1.querySelector('tr[data-po="' + draftPo + '"]'), '草稿单那行在', draftPo);
ok(!!body1.querySelector('tr[data-po="' + placedPo + '"]'), '已下单那行在', placedPo);

const placedRow = body1.querySelector('tr[data-po="' + placedPo + '"]');
ok(placedRow && /CGD/.test(placedRow.textContent),
   '★ 已下单那行带领星单号（CGD…）', flat(placedRow && placedRow.textContent).slice(0, 120));
const draftRow = body1.querySelector('tr[data-po="' + draftPo + '"]');
ok(draftRow && /还没下单/.test(draftRow.textContent),
   '★ 草稿那行的领星单号列写「还没下单」，不是空白',
   flat(draftRow && draftRow.textContent).slice(0, 120));
ok(flat(p1.doc.getElementById('poEmpty').textContent) === '',
   '有行的时候不显示任何空状态', flat(p1.doc.getElementById('poEmpty').textContent));

// 统计数字：两个数都要是真的
const stats1 = flat(p1.doc.getElementById('poStats').textContent);
ok(/1\s*未处理/.test(stats1), '★ 「未处理」= 草稿 1 张（不是把所有单都算进去）', stats1.slice(0, 120));
ok(/2\s*未完成/.test(stats1), '★ 「未完成」= 2 张', stats1.slice(0, 120));

// 「已完成 N 张已隐藏」那一行：★ 一张已完成都没有的时候也必须在
const doneBar1 = flat(p1.doc.getElementById('doneBar').textContent);
ok(doneBar1.length > 0 && /没有已完成/.test(doneBar1),
   '★ 「已完成」出路那一行常驻 —— 0 张时也说话', doneBar1.slice(0, 120));

/* ════════════════════════════════════════════════════════════
   第 2 节 · 三种空状态，文案必须互不相同
   ════════════════════════════════════════════════════════════ */

console.log('\n── 2. 三种空状态 ──');

// ① 一张采购单都没建过
const h2 = newStore();
submitOnePlan(h2.S);
const p2 = loadPage(h2.dump());
ok(p2.errors.length === 0, '（空剧本）装载期没有异常', p2.errors.join('\n      '));
const e2 = p2.doc.querySelector('#poEmpty [data-empty]');
ok(!!e2, '① 空状态节点存在');
ok(e2 && e2.getAttribute('data-empty') === 'never',
   '① 判成「从没建过」', e2 && e2.getAttribute('data-empty'));
const t2 = flat(e2 && e2.textContent);
ok(/一张都没建过/.test(t2), '① 文案点明「一张都没建过」', t2.slice(0, 120));
ok(/新建采购单/.test(t2), '① 给了出路：去建单', t2.slice(0, 120));

// ② 建过，但全部已完成 → 被「仅显示未完成」隐藏
const h3 = newStore();
const ctx3 = submitOnePlan(h3.S);
makePo(h3.S, ctx3, { units: 10, placed: true });
for (let i = 0; i < 5; i++) h3.S.tick();      // 下单后第 2 天部分到货、第 5 天补齐
const donePo = h3.S.pos()[0];
ok(donePo.snapshot.status_shipped === 3,
   '（前置）那张单真的到货补齐了 status_shipped=3', JSON.stringify(donePo.snapshot));
const p3 = loadPage(h3.dump());
ok(p3.errors.length === 0, '（全完成剧本）装载期没有异常', p3.errors.join('\n      '));
ok(p3.doc.getElementById('poBody').children.length === 0, '② 表里一行都没有（已完成被隐藏）');
const e3 = p3.doc.querySelector('#poEmpty [data-empty]');
ok(e3 && e3.getAttribute('data-empty') === 'all-done',
   '② 判成「全部已完成」', e3 && e3.getAttribute('data-empty'));
const t3 = flat(e3 && e3.textContent);
ok(/1 张采购单全部已完成/.test(t3) || /全部已完成/.test(t3),
   '② 文案说清「有 N 张已完成被隐藏了」', t3.slice(0, 140));
ok(/显示已完成（1 张）/.test(t3) || /显示已完成\(1 张\)/.test(t3) || /显示已完成/.test(t3),
   '② 给了出路：一个能把它们显示出来的开关', t3.slice(0, 140));

// ★ 开关真的管用 —— 只有文案不算数
const sw3 = p3.doc.querySelector('#poEmpty button[data-act="show-done"]');
ok(!!sw3, '② 开关按钮在');
if (sw3) {
  sw3.click();
  ok(p3.doc.getElementById('poBody').children.length === 1,
     '★★ ② 点了开关，那张已完成的单**真的出现在表里**',
     '实际 ' + p3.doc.getElementById('poBody').children.length + ' 行');
  ok(flat(p3.doc.getElementById('poEmpty').textContent) === '', '② 显示出来后空状态消失');
}

// ③ 过滤条件太窄
const p4 = loadPage(h1.dump());     // 复用「有 2 张单」的剧本
const fSku = p4.doc.getElementById('fSku');
fSku.value = 'ZZZZ-不存在的货号';
fire(p4.doc, fSku, 'change');
ok(p4.doc.getElementById('poBody').children.length === 0, '③ 过滤后一行不剩');
const e4 = p4.doc.querySelector('#poEmpty [data-empty]');
ok(e4 && e4.getAttribute('data-empty') === 'filtered',
   '③ 判成「被过滤掉了」', e4 && e4.getAttribute('data-empty'));
const t4 = flat(e4 && e4.textContent);
ok(/货号/.test(t4) && /筛掉 2 张/.test(t4),
   '★ ③ 说清是**哪个条件**筛掉了**多少张**（不是一句「无结果」）', t4.slice(0, 160));
ok(/清空过滤/.test(t4), '③ 给了出路：清空过滤', t4.slice(0, 160));

const clr = p4.doc.querySelector('#poEmpty button[data-act="clear"]');
ok(!!clr, '③ 清空过滤按钮在');
if (clr) {
  clr.click();
  ok(p4.doc.getElementById('poBody').children.length === 2,
     '★★ ③ 点了「清空过滤」，2 行真的回来了',
     '实际 ' + p4.doc.getElementById('poBody').children.length + ' 行');
}

// ★ 三种文案两两不同 —— 都写成「暂无数据」的话，三件完全不同的事就合并成一件
ok(t2 && t3 && t4, '三种空状态都真的取到了文案（非空）',
   JSON.stringify([t2.length, t3.length, t4.length]));
ok(t2 !== t3, '★ ①「从没建过」≠ ②「全完成了」');
ok(t3 !== t4, '★ ②「全完成了」≠ ③「被过滤掉了」');
ok(t2 !== t4, '★ ①「从没建过」≠ ③「被过滤掉了」');

// ★ 空单 + 时间过滤：那几张单不是「不匹配」，是「无从比较」，必须单独说破
const p4b = loadPage(h2.dump());          // 一张单都没有的剧本，先在页面上建一张空单
p4b.doc.getElementById('btnNewPo').click();
p4b.doc.getElementById('mSupplier').value = 'SUP-01 · 东莞宠悦制品厂';
p4b.doc.getElementById('mWid').value = String(h2.S.get().warehouses[0].wid);
p4b.doc.getElementById('mCreate').click();
ok(p4b.w.Store.pos().length === 1, '（前置）页面上建出了一张空单', JSON.stringify(p4b.w.Store.pos().length));
const fFrom = p4b.doc.getElementById('fFrom');
fFrom.value = '2026-10';
fire(p4b.doc, fFrom, 'change');
const t4b = textOf(p4b.doc, '#poEmpty [data-empty]');
ok(/空单/.test(t4b) && /还没挑货品/.test(t4b),
   '★ 空单被时间过滤排除时单独说破（「无从比较」≠「不匹配」）', t4b.slice(0, 180));

/* ════════════════════════════════════════════════════════════
   第 3 节 · 弹窗：打开 · 必填被拒 · 填全成功
   ════════════════════════════════════════════════════════════ */

console.log('\n── 3. 新建采购单弹窗 ──');

const p5 = loadPage(h2.dump());     // 一张采购单都没有的剧本
ok(!p5.doc.getElementById('newPoBackdrop'), '（前置）一开始没有弹窗');
p5.doc.getElementById('btnNewPo').click();
const back5 = p5.doc.getElementById('newPoBackdrop');
ok(!!back5, '★ 点〔＋ 新建采购单〕弹窗真的开了');
ok(!!p5.doc.getElementById('mSupplier') && !!p5.doc.getElementById('mWid')
   && !!p5.doc.getElementById('mOpt') && !!p5.doc.getElementById('mPurchaser'),
   '弹窗里四个字段都在（供应商 / 收货仓 / 采购员 / 采购方）');
ok(p5.doc.getElementById('mOpt').value === '8801'
   && p5.doc.getElementById('mPurchaser').value === '2001',
   '★ 采购员 / 采购方 给了默认值', p5.doc.getElementById('mOpt').value + ' / '
   + p5.doc.getElementById('mPurchaser').value);
ok(/不可编辑/.test(flat(back5.textContent)),
   '★ 线框那句注「建完之后供应商、收货仓不可编辑」出现在界面上',
   flat(back5.textContent).slice(0, 140));

// ① 什么都不填就点创建 → detail 必须原样出现在页面上
p5.doc.getElementById('mCreate').click();
const err5 = flat(p5.doc.getElementById('mErr').textContent);
ok(/missing_supplier/.test(err5), '★ 错误码 missing_supplier 显示出来了', err5.slice(0, 160));
ok(/供应商必填/.test(err5),
   '★★ Store 给的 detail **原样**出现在页面上（不是一句「创建失败」）', err5.slice(0, 160));
ok(p5.w.Store.pos().length === 0,
   '★ 被拒时一张单都没建出来', '实际 ' + p5.w.Store.pos().length + ' 张');
ok(p5.expected.some(s => /po\.create-rejected/.test(s)),
   '★ 拒绝有声：控制台留了 po.create-rejected', JSON.stringify(p5.expected).slice(0, 200));

// ② 只补供应商，收货仓仍空 → 换一个错误码，detail 也要跟着换
p5.doc.getElementById('mSupplier').value = 'SUP-02 · 佛山萌宠家居';
p5.doc.getElementById('mCreate').click();
const err5b = flat(p5.doc.getElementById('mErr').textContent);
ok(/missing_wid/.test(err5b) && /收货仓必填/.test(err5b),
   '★ 补了供应商之后换成 missing_wid —— 错误是逐个字段点名的', err5b.slice(0, 160));
ok(err5b !== err5, '★ 两次拒绝的文案不同（不是一句通用的「失败」）');

// ③ 采购员留空 → 领星硬性必填也要被拦在建单时
p5.doc.getElementById('mWid').value = String(h2.S.get().warehouses[0].wid);
p5.doc.getElementById('mOpt').value = '';
p5.doc.getElementById('mCreate').click();
const err5c = flat(p5.doc.getElementById('mErr').textContent);
ok(/missing_opt_uid/.test(err5c) && /领星硬性要求/.test(err5c),
   '★ 采购员空 → missing_opt_uid，且说清是领星硬性要求', err5c.slice(0, 160));

// ④ 填全 → 真的建出来
p5.doc.getElementById('mOpt').value = '8801';
p5.doc.getElementById('mCreate').click();
ok(p5.w.Store.pos().length === 1, '★ 填全后真的建出一张单', '实际 ' + p5.w.Store.pos().length + ' 张');
const made = p5.w.Store.pos()[0];
ok(made && made.supplier === 'SUP-02 · 佛山萌宠家居' && made.items.length === 0,
   '★ 建出来的是空单，供应商就是弹窗里选的那个', JSON.stringify(made && {
     supplier: made.supplier, wid: made.wid, items: made.items.length }));
ok(!p5.doc.getElementById('newPoBackdrop'), '成功后弹窗关掉了');
const link5 = p5.doc.querySelector('#poResult a[href^="po.html?po="]');
ok(!!link5, '★ 页面上留下了去新单的链接（跳转失败时唯一的出路）');
ok(link5 && link5.getAttribute('href') === 'po.html?po=' + encodeURIComponent(made.po_id),
   '★ 链接指向刚建出来的那张单', link5 && link5.getAttribute('href'));
ok(p5.expected.some(s => /Not implemented: navigation/.test(s)),
   '★ 确实尝试过跳转 po.html（jsdom 不实现导航，所以页面上那条链接才是兜底）',
   JSON.stringify(p5.expected).slice(-200));

/* ════════════════════════════════════════════════════════════
   第 4 节 · 销售计划查询
   ════════════════════════════════════════════════════════════ */

console.log('\n── 4. 销售计划查询 ──');

const p6 = loadPage(h1.dump());
const planBody = p6.doc.getElementById('planBody');
const allRows = planBody.children.length;
ok(allRows >= 2, '★ 不过滤时真的长出行来（剧本里 2 张计划）', '实际 ' + allRows + ' 行');

const gState = p6.doc.getElementById('gState');
gState.value = '草稿';
fire(p6.doc, gState, 'change');
const draftRows = [...planBody.children];
// ★ 这两条是**一对**：只有「每行都是草稿」的话，遍历 0 行也全过 ——
//   本项目三次「测试看起来过了、其实没测到目标」都是这个形态
ok(draftRows.length >= 1, '★ 滤成「草稿」后仍有 ≥1 行（非空断言，防止空转）',
   '实际 ' + draftRows.length + ' 行');
ok(draftRows.length < allRows, '★ 过滤真的滤掉了东西',
   '全部 ' + allRows + ' 行，草稿 ' + draftRows.length + ' 行');
ok(draftRows.every(tr => tr.getAttribute('data-state') === '草稿'),
   '★ 留下的每一行状态都是草稿',
   JSON.stringify(draftRows.map(tr => tr.getAttribute('data-state'))));

gState.value = '已提交';
fire(p6.doc, gState, 'change');
const subRows = [...planBody.children];
ok(subRows.length >= 1, '★ 反过来滤「已提交」也有行（两头都验，否则「恒取一半」也能绿）',
   '实际 ' + subRows.length + ' 行');
ok(subRows.every(tr => tr.getAttribute('data-state') === '已提交'),
   '★ 留下的每一行状态都是已提交',
   JSON.stringify(subRows.map(tr => tr.getAttribute('data-state'))));
ok(subRows.length + draftRows.length === allRows,
   '★ 两半相加 = 全部（没有行被两边同时丢掉）',
   subRows.length + ' + ' + draftRows.length + ' vs ' + allRows);

// MSKU 过滤：★ 这一块**有**真的 msku（计划是按 msku 逐格填的），所以它必须真的管用
gState.value = '';
fire(p6.doc, gState, 'change');
const claimMsku = p6.w.Store.plan(ctx1.plan_id).claims[0].seller_sku;
const gMsku = p6.doc.getElementById('gMsku');
gMsku.value = claimMsku;
fire(p6.doc, gMsku, 'change');
const mskuRows = [...planBody.children];
ok(mskuRows.length === 1 && mskuRows[0].getAttribute('data-plan') === ctx1.plan_id,
   '★ 按 MSKU 查得到那张认领了它的计划',
   claimMsku + ' → ' + JSON.stringify(mskuRows.map(tr => tr.getAttribute('data-plan'))));
gMsku.value = 'ZZZZ-不存在的-msku';
fire(p6.doc, gMsku, 'change');
ok(planBody.children.length === 0, '★ 不存在的 MSKU 真的滤成 0 行（不是恒真）');
const t6 = textOf(p6.doc, '#planEmpty [data-empty]');
ok(/MSKU 含/.test(t6) && /清空过滤/.test(t6),
   '★ 计划查询滤空时也说清条件并给出路', t6.slice(0, 160));

// 采购单那块**没有** MSKU 过滤，且页面上写明了为什么
const headTxt = flat(p1.doc.querySelector('.wrap').textContent);
ok(!p1.doc.getElementById('fMsku'),
   '★ 采购单过滤条里没有 MSKU 输入框（采购行本来就没有 msku）');
ok(/这里没有「MSKU」过滤，换成了「店铺」/.test(headTxt),
   '★★ 这个取舍写在**页面上**，不是只写在代码注释里', headTxt.slice(0, 200));
ok(!!p1.doc.getElementById('fSeller') && p1.doc.getElementById('fSeller').options.length >= 2,
   '★ 换上的「店铺」过滤器有真的选项（不是一个点了没反应的空下拉）',
   '选项数 ' + p1.doc.getElementById('fSeller').options.length);

/* ════════════════════════════════════════════════════════════
   第 5 节 · 版面：说明文字收进 fold，不铺在屏幕上
   ★ 判据是**屏幕上没有段落式说明方块**，而不是「文字变少了」——
     后者没法证伪（少一个字也算少）。.note / .gate 是上一版满屏说明的载体，
     所以数它们：一个都不许有。
   ★ 与之配对的是 fold 那条：只删不收等于把信息扔了，两条必须一起看。

   ★ fold 那条**按 id 点名，不数个数**（2026-09-18 收紧，形状照 ui-ops 在
     四个运营页上定的那套）。原来写的是 `details.fold` 个数 `>= 1`，那是个**假地板**：
     这页有 3 个 fold，删掉任意一个甚至两个，「≥ 1」照样成立 —— 也就是说
     它根本没在守「这段说明还在」。
   ★ 点名之后还要验**三件事**，缺一件就有一种坏法能溜过去：
       ① #id 在、且确实是 .fold   —— 防「说明被整段删掉 / id 被改掉」
       ② .fold__body 字数 ≥ 200   —— ★ 防「留外壳、把里面掏空」：
                                     「收起来」和「掏空」在屏幕上长得一模一样
       ③ 默认没有 open 属性       —— 防「收进 fold 但一直展开着」，那等于没收
   ════════════════════════════════════════════════════════════ */

const WHY_MIN = 200;   // 字数下限：当前实际远大于它，是**地板**不是目标
const WHY_IDS = ['whyPoFilter', 'whyPlanFilter'];

console.log('\n── 5. 版面：说明进 fold，不进屏幕 ──');

const noteNodes = [...p1.doc.querySelectorAll('.note, .gate')];
ok(noteNodes.length === 0,
   '★ 页面上没有 .note / .gate 说明方块（说明属于文档，不属于屏幕）',
   '还剩 ' + noteNodes.map(n => flat(n.textContent).slice(0, 40)).join(' ｜ '));

// ★ 非空断言：没有它，WHY_IDS 被清空后下面整个 forEach 遍历 0 项也全绿
ok(WHY_IDS.length >= 2, '★ 点名的说明 fold 至少有 2 个（防止清单被清空后空转）',
   'WHY_IDS = ' + JSON.stringify(WHY_IDS));

WHY_IDS.forEach(id => {
  const why = p1.doc.getElementById(id);
  ok(!!why && why.className.indexOf('fold') >= 0,
     '★ 页面级说明 #' + id + ' 还在（点名，不数个数）',
     why ? '找到了但它不是 .fold：class=' + why.className
         : '找不到 #' + id + ' —— 说明被删了，还是 id 被改了？');
  if (!why) return;

  const body = why.querySelector('.fold__body');
  const text = body ? body.textContent.replace(/\s+/g, '') : '';
  ok(text.length >= WHY_MIN,
     '★★ #' + id + ' 里面**有货**（' + text.length + ' 字 ≥ ' + WHY_MIN + '）'
     + ' —— 光验元素在，掏空也能绿',
     body ? '只剩 ' + text.length + ' 字：是收起来了，还是被掏空了？'
          : '连 .fold__body 都没有');

  ok(!why.hasAttribute('open'),
     '★ #' + id + ' 默认是收起的（展开着就等于没收）',
     'open=' + why.getAttribute('open'));
});
// ★ 弹窗那条路径单独数一遍：建单被拒的错误框曾经也是 .gate
const noteNodes5 = [...p5.doc.querySelectorAll('.note, .gate')];
ok(noteNodes5.length === 0,
   '★ 开过弹窗、被拒过三次之后也没有 .note / .gate',
   '还剩 ' + noteNodes5.map(n => flat(n.textContent).slice(0, 40)).join(' ｜ '));

// 分页控件
console.log('\n── 6. 分页 ──');
const p7 = loadPage(h1.dump());
ok(p7.doc.getElementById('btnPrev').hasAttribute('disabled'),
   '★ 第一页时「上一页」是禁用的（不是点了没反应）');
ok(p7.doc.getElementById('btnNext').hasAttribute('disabled'),
   '★ 只有 1 页时「下一页」也是禁用的');
ok(/第 1 \/ 1 页/.test(flat(p7.doc.getElementById('pagerSay').textContent)),
   '★ 页码是写出来的', flat(p7.doc.getElementById('pagerSay').textContent));

/* ════════════════════════════════════════════════════════════ */

console.log('\n' + (fails.length
  ? '✗ ' + fails.length + ' 项失败：\n  - ' + fails.join('\n  - ')
  : '★ 全绿'));
process.exit(fails.length ? 1 : 0);
