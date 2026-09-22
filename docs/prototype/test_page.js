/* ============================================================
   plan.html —— 真加载一遍页面的自检

   ★ 为什么要有这个文件：
     2026-09-18 负责人打开页面，网格**一个块都没渲染出来**，页面看起来
     「加载完了、只是没有内容」。而 `node --check` 是绿的，读代码也看不出来 ——
     运行期异常发生在 `host.innerHTML=''` 之后、appendChild 之前，
     于是「渲染失败」和「本来就没数据」长得一模一样。

     ★ 判据因此是：**网格必须真的长出块来**，不是「没有报错」。

   跑法:  node test_page.js
   依赖:  jsdom（没装会告诉你怎么装，并以「跳过」退出，不冒充通过）

   ------------------------------------------------------------
   ★ 变异证据（2026-09-18）

   留在这里的是**结论**，不是脚本。脚本的价值在当时（证明那一版断言不是空转），
   留进仓库会变成没人跑也没人删的死代码，而且断言一改它就全过期。
   ★ 以后谁要改下面这些断言，先看一眼「它当初是被什么打红的」——
     如果你的改动让这一列里的某条变异不再打红它，那条断言就已经空转了。

   ------------------------------------------------------------
   ★ 回放记录（2026-09-18，ui-ops 版面返工之后）

   版面返工动的是 DOM 的样子，而下面这些断言**全都挂在 DOM 上** ——
   所以「返工后测试仍然全绿」什么都证明不了：绿只说明现在没坏，
   不说明断言**还守得住**。断言可能还在，但已经空转。
   → 用 `./replay.sh` 把下面三张表**整张重放**了一遍：**13 条全部仍然出分**，
     闸 1 拒 0、闸 2 拒 0，转红的断言与返工前一一对上。
   ★ 判据用的是「**改前红 ⊆ 改后红**」：没有一条丢失，多出一条（见 §4 第一行）。
     这比读 diff 硬 —— 它证的是行为没变弱，不是文本没变。

   ★ 两条教训，写给下一个回放的人：
     · **锚点会随版面漂走，而表里只写「描述」是回放不了的。** 所以每行后面补了
       `锚:` —— 那是 replay.sh 的 from 串，照抄就能跑。
       实测有 2 行的锚点在返工中挪了位（见下），只靠描述当时就卡住了。
     · **「两段措辞写成一样」那行曾经根本没回放成**：上一轮用 perl 替换，
       替换串里的引号被 shell 吃掉 → 报「没打中」。换成 replay.sh 的带引号
       heredoc 一次就过。★ 这正是它把 from/to 放进 stdin 而不是命令行参数的理由：
       **接口形状是事前排除，闸是事后发现。**

   回放命令（照抄，锚点换成下面各行的 `锚:`）：
     ./replay.sh plan.html test_page.js '标签' <<'MUT'
     <锚点原文>
     --
     <改成什么>
     MUT
   ------------------------------------------------------------

   第 4 节 · 计划周期
   | 把 plan.html 故意改成            | 转红的断言                    |
   |---------------------------------|------------------------------|
   | renderAll() → refreshAllBlocks() | 列数变了 ×2（拉长 5→8、缩短→4）+ §5「照建议填 N 警告消失」×1 |
   | renderDropped 变空操作            | 被切掉的清单 ×4               |
   | 被拒时不恢复输入框                 | 「没留下假数」×1              |
   | ext 恒真                         | 「3 个月时一个都没有」×1        |
   | ext 恒假                         | 「有标记」×2                  |
   锚: `    renderAll();` +下一行（`renderAll();` 全文件 7 处，单独一行过不了闸 1）
   锚: `  function renderDropped(dropped, from, to) {`
   锚: `      syncPlanMonthsInput('rejected-restore');`（to 留空 = 删掉）
   锚: `        var ext = !!mm.system_extrapolated;` → `var ext = true;` / `= false;`
   ★ 第一行返工后**多打红一条**（§5 那条）：refreshAllBlocks 不会重画 leadSay，
     于是那句建议停在旧文案上。这是**真的连坐**、不是误伤 —— 记下来，
     免得下一个人以为多出来的红是噪声而去放宽断言。
   ★ 原表里还有一行「去掉 <sup data-ext> 角标」，**已删**：它和「ext 恒假」
     打红的是同两条断言，是同一件事的两种拆法；而且它的锚点在返工中变了
     （`<sup data-ext=` → `<sup class="ext" data-ext=`）。
     ★ 注意：变的只是**外观类名**，`data-ext` 这个钩子还在 —— 断言因此没受影响。
       这正是「断言钉在钩子上、别钉在长相上」的好处。
   ★ 最后两行是**一对**：两个方向各自只打红对侧、不打红同侧 ——
     这才说明红的原因是那个字段，不是连坐。只验一头的话，「标记恒真」也能全绿。

   第 6 节 · 提交后置灰
   | 把 plan.html 故意改成              | 转红的断言                  |
   |-----------------------------------|----------------------------|
   | 只灰 mkDays、漏掉 spDays            | 「三个框都置灰」×1           |
   | 只灰提前期、漏掉 planMonths          | 同上 ×1                    |
   | 预设按钮漏掉                        | 「预设按钮也一起灰」×1       |
   | 恒为 disabled（未提交就灰着）        | **反向那两条** ×2           |
   | 页面里加硬锁（函数开头 / 包 setParams）| 「只是置灰、没有硬锁」×1     |
   | 两段措辞写成一样                    | 「说明了不是锁死」×1         |
   锚: `    el.ship.disabled = lock;` → `= false;`
   锚: `    el.disabled = lock;` → `= false;`（syncPlanMonthsInput 里那一处）
   锚: `    Array.prototype.forEach.call(btns, function (b) { b.disabled = lock; b.title = tip; });`
   锚: `    var pa = Store.params(), el = leadInputs(), lock = locked();` → `lock = true;`
   锚: `  function applyLead(makeRaw, shipRaw, how) {` +下一行，中间插 `if (locked()) return false;`
   锚: leadSay 里 `? '　<strong>这一版已提交，提前期先不动。</strong>'` 起那 4 行，
       整段换成计划周期那句「已提交就改不了」的措辞
   ★ 「只是置灰、没有硬锁」那条**最初是错的**：它当时直接调 Store.setParams，
     测的是 store 不是页面 —— 在 applyLead 里加 `if (locked()) return` 照样全绿。
     现在改成「把框重新启用 → 走页面自己的 change」。
     ★★ 教训：**验页面行为只准走页面自己的入口。**
        隔着一层去问，问到的永远是下一层的能力，而缺陷长在这一层的拦截上。

   第 1b 节 · 人动过的格子留印子（2026-09-18 改版后加）
   | 把 plan.html 故意改成                        | 转红的断言                  |
   |---------------------------------------------|----------------------------|
   | 写入成功后不再 setAttribute('data-touched')   | 「填过的那一格有」×1         |
   | 模板里给**每个**期望销量 input 都写死 data-touched | 「还没人动过时没有」「没填过的那格没有」×2 |
   ★ 这两行是**一对**，各自只打红对侧。少了下面那条，「恒为 1」也能全绿 ——
     而那正好把「人写的」和「系统给的」重新混成一种东西，
     等于把整套墨迹语言在这一格上作废。
   ★ 这两条变异各被同一形态骗过一次，**都表现为「跑出来不对，但看起来像结论」**：
     · 「恒为 1」第一次正则没匹配上 → 全绿，绿的原因是变异没生效；
     · 「不留印子」第一次替换串的转义没生效，注进去把内联脚本打成语法错 →
       整页没渲染，红的是「网格长出 3 个块」这种无关断言，目标断言反而没红。
     ★ 改这张表的人：先确认 from 串**命中且只命中一次**，再确认变异之后页面
       **还能解析**。三种情况（没生效 / 页面炸了 / 断言够强）在红绿上分不出来。

   第 7 节 · 版面（2026-09-18 改版后加）
   | 把 plan.html 故意改成                  | 转红的断言          |
   |---------------------------------------|--------------------|
   | 在 #banner 下留一个 <div class="note">  | 「一个都没有」×1        |
   | 把 id="whyGrid" 改名                    | 「#whyGrid 还在」×1     |
   | 留着 #whyGrid 外壳，把 .fold__body 掏空 | 「它里面有货」×1        |
   ★ 后两行是**一对**，各自只打红对侧：「元素在」和「里面有货」是两件事，
     只验前者的话，把说明掏空也能全绿 —— 而「收起来」和「掏空」长得一模一样。
   ★ 这一节原来写的是「`.fold` 至少一个」，那是个**假地板**：plan.html 有 4 个
     fold，只删掉页面级那一个照样 ≥ 1，实测全绿。点名才守得住。
   ★ 这两行是**一对**：各自只打红对侧。少了下面那条，把说明整段**删掉**也能让
     上面那条变绿 —— 而「收起来」和「删掉」是两回事，后者是另一种错。

   第 5 节 · 页面给用户的建议
   ★ 它守的不是一个数，是页面对用户说的一句**可证伪的承诺**：
     「把计划周期填成 N 个月」。页面敢给建议，就得一直有人盯着这条建议还成不成立。
   | 把 N 算成                | 转红的断言              |
   |-------------------------|------------------------|
   | monthsBetween + 2（报大）| 「填 N−1 仍然警告」×1   |
   | monthsBetween + 0（报小）| 「填 N 警告消失」×1     |
   锚: `    var want = monthsBetween(first, arr) + 1;` → `+ 2;` / `+ 0;`
   ★ 这个锚点**在返工中挪过位**：原来这个数是在 leadSay 的 innerHTML 串里当场算的，
     现在提到了 `want` 变量上。原表只写了「把 N 算成 +2」这样的描述，
     回放时两条都被闸 1 拒（from 没命中）—— 描述不是证据，**能跑的串才是**。
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
const errors = [];
function ok(cond, name, detail) {
  if (cond) console.log('  ✓ ' + name);
  else { console.log('  ✗ ' + name + (detail ? '\n      ' + detail : '')); fails.push(name); }
}

// ── 1. 先在纯 node 里造一份**有数据**的状态 ─────────────────────
//    空计划渲染出 0 个块是**对的**，拿它当测试等于什么都没测。
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
  const skus = Object.keys(group)
    .filter(k => new Set(group[k].map(r => r.seller_id)).size >= 2).slice(0, 3);
  skus.forEach(k => S.addClaims(plan.plan_id, group[k].map(r =>
    ({ seller_id: r.seller_id, sku: r.sku, seller_sku: r.seller_sku, sid: r.sid }))));
  const p2 = S.plan(plan.plan_id);
  (p2.claims || []).forEach((c, i) => p2.periods.forEach((m, j) =>
    S.setDemand(p2.plan_id, c.seller_id, c.seller_sku, m, 30 + i * 5 + j * 4)));
  S.setPurchase(p2.plan_id, skus[0], p2.periods[0], 500);
  return { json: mem['scm_proto'], planId: p2.plan_id, skus };
}

const seeded = seedState();
console.log('\n── 0. 种子 ──');
ok(seeded.skus.length === 3, '造了 3 个跨店铺货号的计划', seeded.skus.join('、'));

// ── 2. 加载页面 ───────────────────────────────────────────────
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.stack || e.message)));
// ★ 「回退必须有声」是本项目的规矩，所以页面在拒绝越界输入时**应该**打日志。
//   这类日志不算失败 —— 但也不能只是过滤掉，要**断言它确实出现过**，
//   否则哪天页面改成静默吞掉，测试照样绿。
const expected = [];
vc.on('error', (...a) => {
  const s = a.map(String).join(' ');
  if (/cells-backfilled/.test(s)) return;         // 装载期回填提示，良性
  if (/setParams\.failed/.test(s)) { expected.push(s); return; }   // 拒绝有声
  if (/setPlanMonths\.failed/.test(s)) { expected.push(s); return; }        // 同上，周期越界
  if (/plan\.months-dropped-cells/.test(s)) { expected.push(s); return; }   // ★ 丢格子必须有声
  errors.push('console.error: ' + s);
});

const dom = new JSDOM(fs.readFileSync(path.join(DIR, 'plan.html'), 'utf8'), {
  runScripts: 'outside-only',      // ★ 我们手工按顺序 eval，顺带能抓到抛错的是哪个脚本
  url: 'http://localhost/plan.html?plan=' + seeded.planId,
  virtualConsole: vc, pretendToBeVisual: true
});
const w = dom.window, doc = w.document;
/* ★ jsdom 没实现 window.scrollTo。页面提交成功后滚回顶部是**对的**行为，
   所以这里补一个桩，而不是把这条错误加进过滤名单 ——
   过滤会连真的 jsdomError 一起盖住，而那正是这个测试存在的理由。 */
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

function fire(el, type) {
  const ev = doc.createEvent('HTMLEvents');
  ev.initEvent(type, true, true);
  el.dispatchEvent(ev);
}

setTimeout(() => {
  console.log('\n── 1. 首次渲染 ──');
  const blocks = doc.getElementById('blocks');
  const empty = doc.getElementById('emptyState');
  ok(!!blocks, '页面里有 #blocks');
  ok(blocks && blocks.children.length === 3,
     '★ 网格真的长出 3 个货号块（不是「没报错」）',
     '实际 ' + (blocks ? blocks.children.length : '-') + ' 个；' +
     'emptyState=' + (empty ? empty.textContent.trim().slice(0, 40) : '-'));
  const txt = blocks ? blocks.textContent.replace(/\s+/g, ' ') : '';
  ok(/计划采购量/.test(txt), '块里有「计划采购量」行');
  ok(/生产/.test(txt) && /货运/.test(txt), '块里能看到生产 / 货运的天数口径');

  /* ── 1b. ★ 人动过的格子要留下印子 ──
     `data-touched="1"` 不只是个样式钩子，它**承载语义**：这个数是人写的。
     ★ 两头都要验 —— 只验「填过的有」的话，「恒为 1」也能全绿，
       而那正好把「人写的」和「系统给的」重新混成一种东西，
       等于把整套墨迹语言在这一格上作废。 */
  console.log('\n── 1b. ★ 人动过的格子留印子 ──');
  const ins = blocks ? [...blocks.querySelectorAll('input[data-din]')] : [];
  ok(ins.length >= 2, '★ 前置：至少两个期望销量格子（否则「另一格没有」验不成）',
     '只有 ' + ins.length + ' 个 —— 下面是空转');
  if (ins.length >= 2) {
    ok(ins.every(i => !i.getAttribute('data-touched')),
       '★ 还没人动过时，一个印子都没有（否则「恒为 1」也能绿）',
       '已经有 ' + ins.filter(i => i.getAttribute('data-touched')).length + ' 个带印子');
    ins[0].value = String(Number(ins[0].value || 0) + 7);
    fire(ins[0], 'input');
    ok(ins[0].getAttribute('data-touched') === '1',
       '★ 填过的那一格有 data-touched="1"',
       '实际 ' + JSON.stringify(ins[0].getAttribute('data-touched')));
    ok(!ins[1].getAttribute('data-touched'),
       '★★ 没填过的那一格**没有** —— 印子跟着人走，不是给所有格子发的',
       '它也被标上了：那这个属性就不再区分「人写的」和「系统给的」了');
  }

  console.log('\n── 2. ★ 改提前期：不许把网格改没了 ──');
  const mk = doc.getElementById('mkDays');
  const sp = doc.getElementById('spDays');
  ok(!!mk && !!sp, '找得到提前期的两个输入框');
  if (mk && sp) {
    const before = blocks.children.length;
    mk.value = '45'; fire(mk, 'change');
    setTimeout(() => {
      ok(blocks.children.length === before,
         '★ 改生产周期后网格还在（' + before + ' → ' + blocks.children.length + '）');
      ok(w.Store.params().make_days === 45, '★ 改动真的写进去了',
         JSON.stringify(w.Store.params()));

      // ★ 越界必须被拒，且框要恢复成真实值 —— 不许界面上留一个没生效的数
      mk.value = '999'; fire(mk, 'change');
      setTimeout(() => {
        ok(w.Store.params().make_days === 45, '★ 越界 999 被拒，参数没被改坏',
           JSON.stringify(w.Store.params()));
        ok(String(mk.value) === '45', '★ 被拒后输入框恢复成真实值，没留下假数', 'mk=' + mk.value);
        ok(blocks.children.length === before, '★ 被拒也没把网格弄没', '' + blocks.children.length);
        planMonthsSection(blocks);
      }, 60);
    }, 60);
  } else finish();
}, 300);

/* ── 4. ★ 计划周期（月）：列数真的要变，切掉的数真的要出现在页面上 ──
   ★ 这一节的判据刻意不是「没报错」：
     · 改周期 → 数一数**表头列数**，而不是「参数写进去了」——
       参数写进去了但网格没重建，正是最容易发生的那种半吊子
     · 缩短 → 断言被切掉的数**原文出现在页面上**，而不是「toast 弹过」——
       一闪而过的提示等于没说，而「少了几个数」在界面上是看不出来的 */
function planMonthsSection(blocks) {
  console.log('\n── 4. ★ 计划周期（月） ──');
  const pm = doc.getElementById('planMonths');
  ok(!!pm, '找得到计划周期输入框');
  if (!pm) return finish();

  const ths = () => blocks.children[0].querySelectorAll('thead th').length;
  // 表头 = 「货号层 / msku 明细」+ N 个月 + 「整期」
  const before = w.Store.plan(seeded.planId).periods.length;
  ok(String(pm.value) === String(before), '框里是这张计划真实的月数', 'value=' + pm.value);
  ok(ths() === before + 2, '表头列数 = 月数 + 2', '实际 ' + ths() + '，月数 ' + before);

  // ★ 外推标记要**两头都验**：只验「拉长后有标记」的话，标记恒真也能绿。
  //   剧本预估正好覆盖 3 个月，所以 3 个月时**一个标记都不该有**——
  //   这一半才是真正能证伪「恒真」的那一半
  const extCount = () => (blocks.innerHTML.match(/data-ext/g) || []).length;
  ok(extCount() === 0, '★ 3 个月时一个外推标记都没有（预估正好覆盖得住）',
     '实际 ' + extCount() + ' 个 —— 标记是不是恒真？');

  pm.value = '6'; fire(pm, 'change');
  setTimeout(() => {
    ok(w.Store.plan(seeded.planId).periods.length === 6, '★ 周期真的改成 6 个月',
       JSON.stringify(w.Store.plan(seeded.planId).periods));
    ok(ths() === 8, '★★ 网格**列数**跟着变了（3 个月 5 列 → 6 个月 8 列）',
       '实际 ' + ths() + ' 列 —— 参数改了但网格没重建？');
    ok(blocks.children.length === 3, '★ 3 个货号块都还在', '' + blocks.children.length);

    // ★ 外推：剧本预估只有 3 个月，拉到 6 个月后第 4~6 个月必然是外推的
    const html = blocks.innerHTML;
    ok(/data-ext/.test(html), '★ 外推的系统预估被标出来了（不标就会被当成真预估）');
    ok(/外推/.test(blocks.textContent), '★ 外推标记是**看得见的字**，不是只有 title');

    // ── 缩短：把填过的数切掉，清单必须留在页面上 ──
    pm.value = '2'; fire(pm, 'change');
    setTimeout(() => {
      const box = doc.getElementById('leadDropped');
      const t = box ? box.textContent.replace(/\s+/g, ' ') : '';
      ok(w.Store.plan(seeded.planId).periods.length === 2, '★ 周期缩到 2 个月');
      ok(ths() === 4, '★ 列数跟着缩回去（2 个月 4 列）', '实际 ' + ths());
      ok(t.length > 0, '★★ 被切掉的数列在页面上了（不是只弹个 toast）', 'leadDropped 是空的');
      ok(/期望销量/.test(t), '★ 清单里逐条写明了是什么数', t.slice(0, 160));
      ok(/我看过了/.test(t), '★ 由**用户**收起，不自动消失', '没有收起按钮 —— 它会自己消失吗？');

      // ★ 越界必须被拒，且框恢复成真实值
      pm.value = '99'; fire(pm, 'change');
      setTimeout(() => {
        ok(w.Store.plan(seeded.planId).periods.length === 2, '★ 越界 99 被拒，周期没被改坏');
        ok(String(pm.value) === '2', '★ 被拒后框恢复成真实值，没留下假数', 'value=' + pm.value);
        ok(doc.getElementById('leadErr').textContent.indexOf('1~24') >= 0,
           '★ 页面上给出了 store 的 detail 原文（不是「改失败」三个字）',
           doc.getElementById('leadErr').textContent.trim().slice(0, 120));
        ok(box.textContent.trim().length > 0,
           '★ 被切掉的清单**没有**因为后续操作被冲掉', '清单不见了');
        adviceSection();
      }, 60);
    }, 80);
  }, 80);
}

/* ── 5. ★ 页面给用户的那句建议，照做真的有效吗 ──
   页面在「本计划期内下的单一件都到不了」时会说：把计划周期填成 N 个月。
   ★ 那是一句**可证伪的承诺**，不是一句安慰话 —— 让人按一个没试过的建议去操作，
     比不给建议更糟：不给建议，人至少知道要自己想。
   ★ 两头都要验：
     · 填 N−1 仍该警告 —— 否则 N 报大了，那就不是「够用的最小值」
     · 填 N   警告须消失，**且周期确实变成了 N** —— 只看警告没了，
       它也可能是因为别的原因没了
   ★ 必须跑在提交之前：提交后 setPlanMonths 会被 guardEditable 拒掉。 */
function adviceSection() {
  console.log('\n── 5. ★ 那句「填成 N 个月」的建议 ──');
  const WARN = '一件都到不了';
  const say = () => doc.getElementById('leadSay').textContent.replace(/\s+/g, ' ');
  const pm = doc.getElementById('planMonths');

  const t0 = say();
  ok(t0.indexOf(WARN) >= 0, '当前这组参数确实触发了「到不了」的警告',
     '没触发 —— 这一节的靶子没打中，下面全是空转：' + t0.slice(0, 120));
  const m = t0.match(/计划周期填成 (\d+) 个月/);
  ok(!!m, '★ 警告里**算出了**具体填几个月（不是让人自己去数）', t0.slice(0, 160));
  if (!m) return lockedSection();
  const N = Number(m[1]);

  pm.value = String(N - 1); fire(pm, 'change');
  setTimeout(() => {
    ok(say().indexOf(WARN) >= 0, '★ 填 N−1=' + (N - 1) + ' 仍然警告（说明 N 不是报大了）',
       '少一个月就不警告了 —— 那 N 就不是够用的最小值');

    pm.value = String(N); fire(pm, 'change');
    setTimeout(() => {
      ok(say().indexOf(WARN) < 0, '★★ 照建议填 N=' + N + '：警告消失了 —— 这句建议是真的',
         '照着填了还警告 —— 页面在教人做无效操作：' + say().slice(0, 160));
      // ★ 必须读**页面那个** Store：拿另一个实例作证，等于让没在现场的人作证
      const per = w.Store.plan(seeded.planId).periods;
      ok(per.length === N, '★ 警告消失的原因确实是「周期变成了 ' + N + ' 个月」，不是别的',
         '实际 ' + per.length + ' 个月：' + per.join(','));
      lockedSection();
    }, 80);
  }, 80);
}

/* ── 6. ★ 提交之后：整个配置区都该是「这一版已经定了」的样子 ──
   ★ 反向那一半才是关键：先断言**未提交**时三个框都能编辑。
     只验「提交后 disabled」的话，恒为 disabled 也能绿 —— 那正好是最坏的结果
     （运营永远改不了参数），却会显示成一片绿。 */
function lockedSection() {
  console.log('\n── 6. ★ 提交后前端置灰 ──');
  const ids = ['mkDays', 'spDays', 'planMonths'];
  const dis = () => ids.map(i => doc.getElementById(i).disabled);
  const presets = () => [...doc.querySelectorAll('#leadPresets button[data-preset]')];

  ok(dis().every(d => d === false), '★ 未提交时三个框都能编辑（否则「恒为 disabled」也能绿）',
     ids.map((i, n) => i + '=' + dis()[n]).join(' '));
  ok(presets().length > 0 && presets().every(b => !b.disabled),
     '★ 未提交时预设按钮也能点', presets().length + ' 个');

  // ★ 走页面**自己的**提交流程（按钮 → 二次确认 → onConfirm），
  //   不在背后调 Store.submitPlan：那样测的是 store，不是页面有没有跟着变
  const btn = doc.getElementById('btnSubmit');
  ok(!!btn, '找得到提交按钮');
  if (!btn) return finish();
  btn.click();
  const confirm = doc.querySelector('.modal__foot .btn--danger');
  ok(!!confirm, '二次确认弹出来了');
  if (!confirm) return finish();
  confirm.click();

  setTimeout(() => {
    ok((w.Store.plan(seeded.planId).revs || []).length === 1, '★ 真的铸出了一个 rev',
       JSON.stringify((w.Store.plan(seeded.planId).revs || []).length));
    ok(dis().every(d => d === true), '★★ 提交后三个框**都**置灰了',
       ids.map((i, n) => i + '=' + dis()[n]).join(' ') + ' —— 有哪个漏了？');
    ok(presets().every(b => b.disabled),
       '★ 预设按钮也一起灰了（框灰了按钮还能点，等于没灰）');

    /* ★ 置灰 ≠ 硬锁。这一条必须打在**页面**上，不能只问 store：
       直接调 w.Store.setParams 是绕过页面的，页面里加一道 `if (locked()) return`
       照样能让它绿 —— 而那正是裁定明令不许的做法。
       所以：把框**重新启用**（相当于运营改版后回来调），再走页面自己的 change 流程。 */
    const mk2 = doc.getElementById('mkDays');
    mk2.disabled = false; doc.getElementById('spDays').disabled = false;
    mk2.value = '31'; fire(mk2, 'change');
    ok(w.Store.params().make_days === 31,
       '★★ 页面**只是置灰、没有加硬锁**（框一旦可编辑，改动照样生效）',
       '实际 ' + JSON.stringify(w.Store.params()) + ' —— 页面里是不是拦了一道？');
    const r = w.Store.setParams({ make_days: 30, ship_days: 60 });
    ok(r.ok === true, '★ store 层也没有被顺手锁死', JSON.stringify(r));

    // ★ 两段文案必须不一样，否则人会以为提前期也被结构性冻结了
    const say = doc.getElementById('leadSay').textContent.replace(/\s+/g, ' ');
    const pmSay = doc.getElementById('planMonthsSay').textContent.replace(/\s+/g, ' ');
    ok(/改版后再调|不是锁死/.test(say), '★ 提前期那句说明了「不是锁死，可改版后再调」', say.slice(-120));
    ok(/已提交就改不了|自己的属性/.test(pmSay), '★ 计划周期那句仍是结构性冻结的措辞', pmSay.slice(-120));
    ok(say.replace(/\s/g, '') !== pmSay.replace(/\s/g, ''),
       '★ 两段措辞确实不一样（一样就分不出哪个是真冻结）');
    finish();
  }, 120);
}

/* ── 7. ★ 版面：说明文字属于文档，不属于屏幕 ──
   这次返工要的是**版面干净，不是信息变少**。所以两条断言守的是**两件相反的事**：
     · `.note` / `.gate` 一个都不许留      —— 屏幕上不许有段落式说明
     · 页面级那个说明 fold 必须在、且**有货** —— 说明是收起来了，不是删掉了
   ★ 只守前者的话，我们会一路把负责人需要的东西删光，而测试全绿。

   ★ 第二条按 **id 点名**，不数 `.fold` 的个数：
     数个数是个假地板 —— plan.html 有 4 个 fold（页面级 2 个 + leadSay/planMonthsSay
     里嵌的 2 个），把页面级那个删掉，「≥ 1」照样成立。实测过，全绿。
   ★ 还要验 `.fold__body` 的**字数下限**：光验「元素在」，把里面掏空也能绿 ——
     「收起来」和「掏空」又是一对长得一模一样的东西。

   ★ 这一节必须跑在**最后**：提交之后 #banner / #result 才有内容，
     而那两处正是最容易顺手写回 .note 的地方。 */
var WHY_MIN = 200;     // 页面级说明的字数下限。当前实际远大于它，是地板不是目标

function proseSection() {
  console.log('\n── 7. ★ 版面：说明不许占屏，但也不许消失 ──');
  var prose = doc.querySelectorAll('.note, .gate');
  ok(prose.length === 0, '★ 页面上一个 .note / .gate 说明块都没有',
     '还剩 ' + prose.length + ' 个：' +
     Array.prototype.map.call(prose, function (n) {
       return '<' + n.tagName.toLowerCase() + ' class="' + n.className + '">' +
              n.textContent.replace(/\s+/g, ' ').trim().slice(0, 40);
     }).join(' ｜ '));

  var why = doc.getElementById('whyGrid');
  ok(!!why && why.className.indexOf('fold') >= 0,
     '★ 页面级说明 #whyGrid 还在（点名，不数个数）',
     why ? '找到了但它不是 .fold：class=' + why.className
         : '找不到 #whyGrid —— 说明被删了，还是 id 被改了？');
  if (!why) return;
  var body = why.querySelector('.fold__body');
  var text = body ? body.textContent.replace(/\s+/g, '') : '';
  ok(text.length >= WHY_MIN,
     '★★ 它里面**有货**（' + text.length + ' 字 ≥ ' + WHY_MIN + '）—— 光验元素在，掏空也能绿',
     body ? '只剩 ' + text.length + ' 字：说明是被收起来了，还是被掏空了？'
          : '连 .fold__body 都没有');
}

function finish() {
  proseSection();
  console.log('\n── 3. 控制台 ──');
  ok(expected.some(x => /setParams\.failed/.test(x)),
     '★ 拒绝越界输入时页面有出声（静默吞掉是最坏的一种）',
     '一条 setParams.failed 都没打 —— 是不是改成静默吞了？');
  if (!errors.length) console.log('  ✓ 没有未预期的错误');
  else errors.forEach(e => {
    console.log('  ✗ ' + e.split('\n').slice(0, 6).join('\n    '));
    fails.push('控制台有错');
  });
  console.log('\n' + (fails.length ? '✗ ' + fails.length + ' 项失败' : '★ 全绿'));
  process.exit(fails.length ? 1 : 0);
}
