/* ============================================================
   service_supplychain 可点击原型 —— 状态模型 · 领域操作 · 时间推进
   纯原生 JS，无依赖、无 CDN。状态存 localStorage['scm_proto']。

   ★ 这个系统的状态是「事实驱动」的：到货、领星回执会自动推进状态，
     没有任何一步是「人记得去点」。所以原型必须能推进时间（Store.tick），
     否则一整圈走不完。判据出处：docs/04-状态流转.md §1.2。

   日志约定（和后端同一条规矩，见全局规范）：
   · 出错必须带上下文（哪个 id / 哪一步 / 为什么），不许只打一句话。
   · 静默兜底是最坏的一种 —— 回退分支必须有声。
   · 每个领域操作都往 state.logs 里留一条痕，界面上看得见。
   ============================================================ */
(function (window) {
  'use strict';

  var TAG = '[scm-store]';
  var KEY = 'scm_proto';

  /* ★ 剧本版本号。改了种子或 state 形状就 +1。
     不加这个，浏览器里存着旧剧本，页面看起来「正常」—— 只是内容是旧的，
     而「旧」和「对」在界面上长得一模一样。这正是我们一路在防的那类静默。 */
  var SCHEMA = 9;

  /* ---------- 诊断日志（控制台，带上下文） ---------- */

  function dbg(event, detail) {
    try { console.debug(TAG, event, detail || {}); } catch (e) { /* 控制台不可用 */ }
  }
  function err(event, detail) {
    try { console.error(TAG, event, detail || {}); } catch (e) { /* 控制台不可用 */ }
  }

  /* ---------- 常量 ---------- */

  var START_DAY = '2026-09-16';

  // 计划单记录状态（docs/04-状态流转.md §1.1）。只前进，不回退，
  // 唯一的例外是「已确认 → 已提交」（组单被撤掉，木桶不再满）。
  var S = {
    SUBMITTED: '已提交',
    CONFIRMED: '已确认',
    ORDERED: '已下单',
    READY: '准备排货',
    SCHEDULED: '已排货',
    CLOSED: '已完结',
    CANCELLED: '已撤销'
  };

  // 领星侧排货单进度（我们只读，不写）
  var LX_STAGES = ['待配货', '待发货', '待收货', '已完成'];

  /* ---------- 日期（纯字符串，不引 Date 库） ---------- */

  function parseDay(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || ''));
    if (!m) {
      err('day.unparsable', { raw: s, hint: '期望 YYYY-MM-DD；已回退到 START_DAY，这条回退是有声的' });
      return new Date(Date.UTC(2026, 8, 16));
    }
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  }
  function fmtDay(d) {
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function addDays(dayStr, n) {
    var d = parseDay(dayStr);
    d.setUTCDate(d.getUTCDate() + n);
    return fmtDay(d);
  }
  function daysBetween(a, b) {
    return Math.round((parseDay(b) - parseDay(a)) / 86400000);
  }
  function compact(dayStr) { return String(dayStr).replace(/-/g, '').slice(2); } // 2026-09-16 → 260916

  /* ---------- 小工具 ---------- */

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function sum(arr, f) {
    var t = 0;
    for (var i = 0; i < arr.length; i++) t += (f(arr[i]) || 0);
    return t;
  }
  function stockKey(sku, wid) { return sku + '|' + wid; }
  function cellKey(sellerId, sku, period) { return sellerId + '|' + sku + '|' + period; }

  function digestOf(text) {
    // djb2：只是让「同样的内容得到同样的摘要」看得见，不是密码学用途
    var h = 5381;
    for (var i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
    return ('00000000' + h.toString(16)).slice(-8);
  }

  /* ============================================================
     初始剧本
     ============================================================ */

  function seed() {
    var day = START_DAY;
    var periods = ['2026-10', '2026-11', '2026-12'];

    // ★ market 决定共享哪个海外仓池；has_fba=false 的平台（Walmart 等）没有 FBA，
    //   界面要显式显示「该平台无 FBA」，不是显示 0 —— 0 和「不适用」是两回事。
    var sellers = [
      { seller_id: '11072', name: 'A4Pet-US',     market: 'US', has_fba: true },
      { seller_id: '11201', name: 'Walmart-US',   market: 'US', has_fba: false },
      { seller_id: '11081', name: 'A4Pet-UK',     market: 'UK', has_fba: true },
      { seller_id: '11094', name: 'A4Pet-DE',     market: 'DE', has_fba: true }
    ];
    var warehouses = [
      { wid: 12, name: '12-上海愉辉' },
      { wid: 18, name: '18-深圳坂田' }
    ];

    // ★ 计划默认是**空的** —— 货号与 msku 由人「添加」进来。
    //   种子里预置的不是认领，而是**可选目录**（catalog）。
    var claims = [];
    var catalogSrc = [
      { seller_sku: 'A4P-CAT-001', sid: 11072, sku: 'DCC1800264', seller_id: '11072' },
      { seller_sku: 'A4P-CAT-001-V2', sid: 11072, sku: 'DCC1800264', seller_id: '11072' },
      { seller_sku: 'A4P-DOG-220', sid: 11072, sku: 'DCC1800265', seller_id: '11072' },
      { seller_sku: 'A4P-CAT-001-UK', sid: 11081, sku: 'DCC1800264', seller_id: '11081' },
      { seller_sku: 'A4P-BED-330', sid: 11081, sku: 'DCC1900110', seller_id: '11081' }
    ];

    // ★ 空计划：cells 也是空的。下面是「添加」时用来算系统建议的**底表**。
    //   ★ 刻意做到**真实量级**（3 店铺 × 120 货号 ≈ 400+ 个 msku）——
    //     18 条时「全列出来让人挑」看起来能用，1000 条时它从第一天就是错的。
    //     原型必须把这个量级摆出来，否则交互问题根本暴露不出来。
    var CATS = [
      ['猫用品', ['猫砂盆', '猫抓板', '猫爬架', '猫砂', '猫窝']],
      ['狗用品', ['狗窝', '狗笼', '磨牙棒', '狗厕所']],
      ['通用', ['饮水机', '喂食器', '宠物床', '尿垫']],
      ['出行', ['牵引绳', '航空箱', '推车', '背包']],
      ['美容', ['梳毛器', '指甲剪', '浴液', '吹风机']]
    ];
    var SIZES = ['S', 'M', 'L', 'XL'];
    var combos = [];
    (function buildCatalog() {
      var n = 0;
      for (var ci = 0; ci < CATS.length; ci++) {
        var top = CATS[ci][0], subs = CATS[ci][1];
        for (var si = 0; si < subs.length; si++) {
          for (var k = 0; k < 6; k++) {                 // 每个二级品类 6 个货号
            n++;
            var sku = 'DCC' + (1800000 + n * 7);
            var size = SIZES[(n + k) % SIZES.length];
            var name = subs[si] + ' · ' + size;
            var cat = top + ' / ' + subs[si];
            // 每个货号在 1~3 个店铺有 listing
            var stores = ['11072', '11201', '11081', '11094'].filter(function (x, i) { return (n + i) % 4 !== 1; });
            if (!stores.length) stores = ['11072'];
            stores.forEach(function (sid, i) {
              var b0 = 20 + ((n * 13 + i * 29) % 260);
              var base = [b0, Math.round(b0 * 1.1), Math.round(b0 * 0.95)];
              if (n % 17 === 0) base[2] = null;          // 少数货号缺 12 月建议 → 空格子
              var ms = ['A4P-' + sku.slice(-4) + '-' + sid.slice(-2)];
              if (n % 5 === 0) ms.push(ms[0] + '-V2');   // 约 1/5 有第二个 listing（跟卖迭代款）
              combos.push({ seller_id: sid, sku: sku, name: name, cat: cat, base: base, msku: ms });
            });
          }
        }
      }
    })();

    // ★ 另一张**别人的**计划，占住德国店的全部 msku ——
    //   没有它，「被占用」那条规则在界面上永远看不到，等于没写。
    var rivalClaims = [];
    combos.filter(function (c) { return c.seller_id === '11094'; })
      .slice(0, 20)                                     // 只占 20 个，不把整店占死
      .forEach(function (c) {
        c.msku.forEach(function (ms) {
          rivalClaims.push({ seller_sku: ms, sid: 11094, sku: c.sku, seller_id: '11094' });
        });
      });

    var cells = {};                      // ★ 空计划
    var catalog = [];                    // ★ 可选目录：店铺 × 货号 × msku
    combos.forEach(function (c) {
      c.msku.forEach(function (ms) {
        catalog.push({
          seller_id: c.seller_id, sku: c.sku, seller_sku: ms,
          sid: Number(c.seller_id),
          name: c.name, cat: c.cat,
          base: c.base.slice(),
          share: c.msku.length === 1 ? 1 : (c.msku.indexOf(ms) === 0 ? 0.75 : 0.25)
        });
      });
    });
    var _unusedCells = {};
    [].forEach(function (c) {
      periods.forEach(function (p, i) {
        var sys = c.base[i];
        var key = cellKey(c.seller_id, c.sku, p);
        var mskuRows = c.msku.map(function (ms, j) {
          // 两个 msku 的系统建议按 3:1 拆（形状真实即可，数字是假的）
          if (sys === null) return { seller_sku: ms, system: null };
          var part = c.msku.length === 1 ? sys : (j === 0 ? Math.round(sys * 0.75) : sys - Math.round(sys * 0.75));
          return { seller_sku: ms, system: part };
        });
        cells[key] = { system: sys, demand: sys, purchase: null, touched_demand: false, touched_purchase: false, msku: mskuRows };
      });
    });

    /* ★ 三种库存，用途不同，不许混（见 docs/02 §3.1a）
         stock_fba    FBA 在仓     msku 级        ┐ 合起来 = 可售库存
         stock_os     海外仓在仓   货号 × 市场    ┘ ★ 海外仓是共享池
         stock        本地仓       货号 × 仓位      ★ 不进可售，只用于排货确认闸 G3 */
    var stock_fba = {}, stock_os = {};
    combos.forEach(function (c) {
      var sel = sellers.filter(function (x) { return x.seller_id === c.seller_id; })[0];
      if (!sel) return;
      if (sel.has_fba) {
        c.msku.forEach(function (ms, j) {
          var h = 0, t = ms + '|' + c.seller_id;
          for (var i = 0; i < t.length; i++) h = (h * 29 + t.charCodeAt(i)) % 83;
          stock_fba[ms + '|' + c.seller_id] = 10 + h;      // 10 ~ 92
        });
      }
      var k = c.sku + '|' + sel.market;
      if (!(k in stock_os)) {
        var h2 = 0, t2 = k;
        for (var i2 = 0; i2 < t2.length; i2++) h2 = (h2 * 23 + t2.charCodeAt(i2)) % 61;
        stock_os[k] = h2;                                   // 0 ~ 60，★ 该市场共享
      }
    });

    // ★ 本地仓初始 valid 全 0 —— 必须先下单、先到货才能排货，这样才走得到完整链路
    var stock = {};
    var skus = ['DCC1800264', 'DCC1800265', 'DCC1900110'];
    skus.forEach(function (sku) {
      warehouses.forEach(function (w) {
        stock[stockKey(sku, w.wid)] = { valid: 0, locked: 0 };
      });
    });

    return {
      schema: SCHEMA,
      day: day,
      tick_no: 0,
      role: 'ops',
      seq: { po: 0, sheet: 0, cid: 0, line: 0, doc: 0 },
      plans: [{
        plan_id: 'PLAN-2026Q4',
        title: '2026 Q4 补货计划',
        period_start: '2026-10',
        months: 3,
        periods: periods,
        owner: '张运营',
        claims: claims,
        cells: {},
        cells_demand: {},
        cells_purchase: {},
        revs: []
      }, {
        // ★ 别人的计划：只为演示「被占用」这条规则，本原型不编辑它
        plan_id: 'PLAN-2026Q3',
        title: '2026 Q3 欧洲补货（李运营）',
        period_start: '2026-07', months: 3,
        periods: ['2026-07', '2026-08', '2026-09'],
        owner: '李运营',
        claims: rivalClaims,
        cells: {}, cells_demand: {}, cells_purchase: {}, revs: []
      }],
      lines: [],
      pos: [],
      sheets: [],
      stock: stock,
      stock_fba: stock_fba,
      stock_os: stock_os,
      catalog: catalog,
      warehouses: warehouses,
      sellers: sellers,
      logs: [{ day: day, role: 'system', text: '剧本已初始化：1 张**空**计划（★ 货号与 msku 要自己「添加」）；可选目录 ' + catalog.length + ' 条；本地仓可发量全为 0。' }]
    };
  }

  /* ============================================================
     持久化
     ============================================================ */

  var state = null;

  function load() {
    var raw = null;
    try { raw = window.localStorage.getItem(KEY); }
    catch (e) {
      // 回退必须有声：隐私模式 / 禁用存储时，内存里照样跑，但要说出来
      err('storage.read-failed', { key: KEY, message: e && e.message, fallback: '改用内存态，刷新后丢失' });
    }
    if (!raw) { state = seed(); persist(); return state; }
    try {
      state = JSON.parse(raw);
    } catch (e) {
      err('storage.parse-failed', { key: KEY, bytes: raw.length, message: e && e.message, fallback: '已重置为初始剧本' });
      state = seed(); persist();
    }
    if (!state || !state.plans) {
      err('storage.shape-invalid', { key: KEY, got: state ? Object.keys(state) : null, fallback: '已重置为初始剧本' });
      state = seed(); persist();
    }
    /* ★ 版本不符 = 浏览器里存的是旧剧本。
       ★ 但**不许直接清空** —— 那等于把人的草稿干掉，而草稿正是「下次接着干」的全部意义。
         做法：按新剧本重播，然后**把能迁的迁过来**，迁不动的**逐项点名**，不静默丢。 */
    if (Number(state.schema || 0) !== SCHEMA) {
      var was = Number(state.schema || 0);
      var old = state;
      state = seed();
      var kept = { claims: 0, demand: 0, purchase: 0 }, lost = [];

      try {
        var oldPlan = (old.plans || [])[0] || {};
        var np = state.plans[0];
        var cat = state.catalog || [];

        // ① 迁认领：目录里还认得出来的才迁
        (oldPlan.claims || []).forEach(function (c) {
          var known = cat.some(function (x) {
            return x.seller_sku === c.seller_sku && String(x.sid) === String(c.sid);
          });
          if (!known) { lost.push('认领 ' + c.seller_sku + '（新目录里已不存在）'); return; }
          if (np.claims.some(function (y) { return y.seller_sku === c.seller_sku && String(y.sid) === String(c.sid); })) return;
          np.claims.push({ seller_sku: c.seller_sku, sid: c.sid, sku: c.sku, seller_id: c.seller_id });
          kept.claims++;
        });
        var touched = {};
        np.claims.forEach(function (c) { touched[c.seller_id + '|' + c.sku] = c; });
        Object.keys(touched).forEach(function (k) { rebuildCells(np, touched[k].seller_id, touched[k].sku); });

        // ② 迁人改过的期望销量
        Object.keys(oldPlan.cells_demand || {}).forEach(function (k) {
          var d = oldPlan.cells_demand[k];
          if (!d || !d.touched) return;
          if (np.cells_demand[k]) { np.cells_demand[k].demand = d.demand; np.cells_demand[k].touched = true; kept.demand++; }
          else lost.push('期望销量 ' + k + '（格子已不存在）');
        });
        // ③ 迁人改过的计划采购量
        Object.keys(oldPlan.cells_purchase || {}).forEach(function (k) {
          var q = oldPlan.cells_purchase[k];
          if (!q || !q.touched) return;
          if (np.cells_purchase[k]) { np.cells_purchase[k].purchase = q.purchase; np.cells_purchase[k].touched = true; kept.purchase++; }
          else lost.push('计划采购量 ' + k + '（格子已不存在）');
        });
        // ④ 已提交的版本迁不了 —— 结构变了，硬迁会造出假记录
        if ((oldPlan.revs || []).length) {
          lost.push('已提交的 ' + oldPlan.revs.length + ' 个版本（记录结构已变，硬迁会造出假记录）');
        }
      } catch (e) {
        err('storage.migrate-failed', { from: was, to: SCHEMA, message: e && e.message,
          fallback: '只保留新剧本，旧草稿未能迁移' });
      }

      err('storage.schema-stale', {
        key: KEY, found: was, expected: SCHEMA,
        kept: kept, lost: lost, action: '已重播并迁移草稿'
      });
      state.logs.unshift({
        day: state.day, role: 'system',
        text: '★ 原型改版（v' + was + ' → v' + SCHEMA + '）：已重播剧本并迁回草稿 —— '
            + '认领 ' + kept.claims + ' 个 msku · 期望销量 ' + kept.demand + ' 格 · 计划采购量 ' + kept.purchase + ' 格'
            + (lost.length ? '。★ 以下迁不动，逐项列出：' + lost.join('；') : '。没有丢失。')
      });
      persist();
      try { window.__SCM_SCHEMA_RESET__ = { from: was, to: SCHEMA, kept: kept, lost: lost }; } catch (e2) {}
    }
    backfillCells();
    return state;
  }

  /* ★ 有认领却没格子的计划，把格子补上。
     踩到的：种子里「别人的计划」直接写了 claims、没建 cells_purchase，
     于是它在界面上**看着可编辑，采购列却静默失灵** —— setPurchase 一律
     返回 cell_not_found，而用户只会觉得「点了没反应」。
     ★ 「看着能用但其实不能用」比「明确不能用」坏得多，所以这里补而不是屏蔽。 */
  function backfillCells() {
    var fixed = [];
    (state.plans || []).forEach(function (p) {
      p.cells_demand = p.cells_demand || {};
      p.cells_purchase = p.cells_purchase || {};
      var pairs = {};
      (p.claims || []).forEach(function (c) { pairs[c.seller_id + '|' + c.sku] = c; });
      Object.keys(pairs).forEach(function (k) {
        var c = pairs[k];
        if (p.cells_purchase[pKey(c.sku, p.periods[0])]) return;   // 已有，跳过
        rebuildCells(p, c.seller_id, c.sku);
        fixed.push(p.plan_id + '/' + c.sku);
      });
    });
    if (fixed.length) {
      err('plan.cells-backfilled', { count: fixed.length, sample: fixed.slice(0, 5),
        why: '这些计划有认领但没有格子，采购量会填不进去' });
      persist();
    }
  }

  function persist() {
    try { window.localStorage.setItem(KEY, JSON.stringify(state)); }
    catch (e) {
      err('storage.write-failed', { key: KEY, message: e && e.message, hint: '可能超配额；本次改动只存在于内存' });
    }
  }

  function ensure() { return state || load(); }

  /* ============================================================
     查找
     ============================================================ */

  function findPlan(id) {
    var s = ensure();
    var p = id ? s.plans.filter(function (x) { return x.plan_id === id; })[0] : s.plans[0];
    if (!p) err('plan.not-found', { plan_id: id, known: s.plans.map(function (x) { return x.plan_id; }) });
    return p || null;
  }
  function findLine(id) {
    var l = ensure().lines.filter(function (x) { return x.line_id === id; })[0];
    if (!l) err('line.not-found', { line_id: id, total_lines: ensure().lines.length });
    return l || null;
  }
  function findPo(id) {
    var p = ensure().pos.filter(function (x) { return x.po_id === id; })[0];
    if (!p) err('po.not-found', { po_id: id, known: ensure().pos.map(function (x) { return x.po_id; }) });
    return p || null;
  }
  function findSheet(id) {
    var sh = ensure().sheets.filter(function (x) { return x.sheet_id === id; })[0];
    if (!sh) err('sheet.not-found', { sheet_id: id, known: ensure().sheets.map(function (x) { return x.sheet_id; }) });
    return sh || null;
  }
  function findConsignment(cid) {
    var s = ensure(), hit = null;
    s.sheets.forEach(function (sh) {
      sh.consignments.forEach(function (c) { if (String(c.cid) === String(cid)) hit = { sheet: sh, cons: c }; });
    });
    if (!hit) err('consignment.not-found', { cid: cid });
    return hit;
  }

  /* ============================================================
     量的定义（docs/04-状态流转.md §1.3）
     ★ 每个量都是一个小而独立的函数 —— 判据能被单独指认，才谈得上可证伪。
     ============================================================ */

  // 已确认量 = 所有采购单（含草稿）里指向该记录的 units 之和
  function qtyConfirmed(lineId) {
    return sum(ensure().pos, function (po) {
      return sum(po.items, function (it) { return it.line_id === lineId ? it.units : 0; });
    });
  }

  // 已下单量 = 同上，但只算 state='已下单' 的采购单
  function qtyOrdered(lineId) {
    return sum(ensure().pos, function (po) {
      if (po.state !== '已下单') return 0;
      return sum(po.items, function (it) { return it.line_id === lineId ? it.units : 0; });
    });
  }

  // 已排未发量 = 排货单里 our_stage ∈ {草稿, 已确认} 的行
  // ★ 排货表里「已捡出、还没编进排货单」的池子也算在内 ——
  //   不算就会把同一批需求排两遍（正是 G4 要拦的那类错误）。
  function qtyStaged(lineId) {
    var s = ensure(), t = 0;
    s.sheets.forEach(function (sh) {
      if (sh.state === '已取消') return;
      t += sum(sh.pool || [], function (p) { return p.line_id === lineId ? p.units : 0; });
      sh.consignments.forEach(function (c) {
        if (c.our_stage !== '草稿' && c.our_stage !== '已确认') return;
        t += sum(c.lines, function (l) { return l.line_id === lineId ? l.units : 0; });
      });
    });
    return t;
  }

  // 只在排货表池子里的量（staged 的子集，界面上要能分辨「捡了但没编排」）
  function qtyPooled(lineId) {
    var s = ensure(), t = 0;
    s.sheets.forEach(function (sh) {
      if (sh.state === '已取消') return;
      t += sum(sh.pool || [], function (p) { return p.line_id === lineId ? p.units : 0; });
    });
    return t;
  }

  // 已投送量 = 排货单里 our_stage ∈ {已投送, 已归档} 的行 ★ 不认草稿
  function qtyDispatched(lineId) {
    var s = ensure(), t = 0;
    s.sheets.forEach(function (sh) {
      sh.consignments.forEach(function (c) {
        if (c.our_stage !== '已投送' && c.our_stage !== '已归档') return;
        t += sum(c.lines, function (l) { return l.line_id === lineId ? l.units : 0; });
      });
    });
    return t;
  }

  function lineQty(lineId) {
    var l = findLine(lineId);
    var total = l ? l.total : 0;
    var staged = qtyStaged(lineId);
    var dispatched = qtyDispatched(lineId);
    return {
      total: total,
      confirmed: qtyConfirmed(lineId),
      ordered: qtyOrdered(lineId),
      staged: staged,
      pooled: qtyPooled(lineId),
      dispatched: dispatched,
      pending: Math.max(0, total - staged - dispatched)
    };
  }

  /* ============================================================
     状态推进判据（docs/04-状态流转.md §1.2）
     ★ 木桶用在我们自己的动作上，下限用在外部物理事实上 —— 两类不能混用。
     ============================================================ */

  // 木桶：已确认量 = 总量 才算「已确认」。部分组单 = 这条需求还没被完全承接。
  function gateConfirmed(line) { return qtyConfirmed(line.line_id) >= line.total; }

  // 木桶：已下单量 = 总量
  function gateOrdered(line) { return qtyOrdered(line.line_id) >= line.total; }

  // ★ 下限 + 布尔：关联采购单中「至少一张已到货」（status_shipped ∈ {2,3}）。
  //   货是一批批来的，等全到齐再排，货就白躺在仓里。不要求全到，也不摊分到货量。
  function gateReady(line) {
    return ensure().pos.some(function (po) {
      var hit = po.items.some(function (it) { return it.line_id === line.line_id; });
      if (!hit) return false;
      return po.receipts.length > 0 && po.snapshot.status_shipped >= 2;
    });
  }

  // 木桶：已投送量 = 总量。★ 只认已投送/已归档，不认草稿。
  function gateScheduled(line) { return qtyDispatched(line.line_id) >= line.total; }

  // 木桶：关联排货单全部 lingxing_stage = '已完成'
  function gateClosed(line) {
    var cs = consignmentsOf(line.line_id);
    if (!cs.length) return false;
    return cs.every(function (c) { return c.lingxing_stage === '已完成'; });
  }

  function consignmentsOf(lineId) {
    var out = [];
    ensure().sheets.forEach(function (sh) {
      sh.consignments.forEach(function (c) {
        if (c.lines.some(function (l) { return l.line_id === lineId; })) out.push(c);
      });
    });
    return out;
  }

  // 一条记录下一步应该到哪 —— 一次只走一格，由 reevaluate 循环到不动点
  function nextStateOf(line) {
    var q = lineQty(line.line_id);
    switch (line.state) {
      case S.SUBMITTED:
        return gateConfirmed(line) ? { to: S.CONFIRMED, fact: '已确认量 ' + q.confirmed + ' = 总量 ' + q.total + '（木桶）' } : null;
      case S.CONFIRMED:
        // 组单被撤掉 → 木桶不再满 → 退回已提交（04 §1.2 里唯一的自动回退）
        if (!gateConfirmed(line)) return { to: S.SUBMITTED, fact: '组单被撤，已确认量 ' + q.confirmed + ' < 总量 ' + q.total };
        return gateOrdered(line) ? { to: S.ORDERED, fact: '已下单量 ' + q.ordered + ' = 总量 ' + q.total + '（木桶）' } : null;
      case S.ORDERED:
        return gateReady(line) ? { to: S.READY, fact: '关联采购单已有到货（status_shipped ∈ {2,3}，布尔判据，不要求全到）' } : null;
      case S.READY:
        return gateScheduled(line) ? { to: S.SCHEDULED, fact: '已投送量 ' + q.dispatched + ' = 总量 ' + q.total + '（只认已投送，不认草稿）' } : null;
      case S.SCHEDULED:
        return gateClosed(line) ? { to: S.CLOSED, fact: '关联排货单领星进度全部 = 已完成' } : null;
      default:
        return null;
    }
  }

  // ★ 每次操作后、每次 tick 后都跑一遍，循环到不动点
  function reevaluate(by) {
    var s = ensure(), moved = 0, round = 0, changed = true;
    while (changed) {
      changed = false;
      round++;
      if (round > 20) {
        // 不动点没收敛 = 判据互相打架。这种事必须炸出来，不能默默跑满循环
        err('reevaluate.no-fixpoint', { rounds: round, by: by, lines: s.lines.length, hint: '判据之间可能存在环，检查 nextStateOf' });
        break;
      }
      s.lines.forEach(function (l) {
        if (l.state === S.CANCELLED) return;
        var nx = nextStateOf(l);
        if (!nx || nx.to === l.state) return;
        l.events.push({ day: s.day, from: l.state, to: nx.to, by: by || '系统', fact: nx.fact });
        dbg('line.transit', { line_id: l.line_id, from: l.state, to: nx.to, fact: nx.fact, by: by || '系统' });
        l.state = nx.to;
        moved++;
        changed = true;
      });
    }
    return moved;
  }

  /* ============================================================
     日志
     ============================================================ */

  function log(text) {
    var s = ensure();
    s.logs.push({ day: s.day, role: s.role, text: String(text) });
    dbg('log', { day: s.day, role: s.role, text: text });
    persist();
  }

  /* ============================================================
     运营：填数 · 提交
     ============================================================ */

  function guardEditable(p) {
    if ((p.revs || []).length) {
      return { ok: false, error: 'plan_submitted',
        detail: '计划已提交 rev ' + p.revs[p.revs.length - 1].rev + '，要改请建新版本' };
    }
    return null;
  }

  /** ★ 期望销量 —— msku × 月。减**当月**该 listing 的可售库存 */
  function setDemand(planId, sellerId, sellerSku, period, value) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;
    var k = dKey(sellerId, sellerSku, period);
    var cell = (p.cells_demand || {})[k];
    if (!cell) {
      return { ok: false, error: 'cell_not_found',
        detail: '格子不存在：' + sellerName(sellerId) + ' · ' + sellerSku + ' · ' + period
          + '（该 msku 还没添加进这张计划）' };
    }
    var v = num(value);
    if (v !== null && v < 0) return { ok: false, error: 'negative', detail: '期望销量不能为负：' + value };
    var before = cell.demand;
    cell.demand = v; cell.touched = true;
    persist();
    log('填期望销量：' + sellerName(sellerId) + ' / ' + sellerSku + ' / ' + period + '　'
      + (before === null ? '空' : before) + ' → ' + (v === null ? '空' : v));
    return { ok: true, value: v };
  }

  /** ★ 计划采购量 —— 货号 × 月，**不带店铺**。加 `当月 + 提前期` 的本地仓池子 */
  function setPurchase(planId, sku, period, value) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;
    var k = pKey(sku, period);
    var cell = (p.cells_purchase || {})[k];
    if (!cell) {
      return { ok: false, error: 'cell_not_found',
        detail: '采购格子不存在：' + sku + ' · ' + period + '（该货号还没添加进这张计划）' };
    }
    var v = num(value);
    if (v !== null && v < 0) return { ok: false, error: 'negative', detail: '计划采购量不能为负：' + value };
    var before = cell.purchase;
    cell.purchase = v; cell.touched = true;
    persist();
    log('填计划采购量：' + sku + ' / ' + period + '　'
      + (before === null ? '空' : before) + ' → ' + (v === null ? '空' : v)
      + '（★ 生产 ' + params().make_days + ' 天，' + monthAfterDays(period, params().make_days)
      + ' 到本地仓；再运 ' + params().ship_days + ' 天，'
      + monthAfterDays(period, params().total_days) + ' 才可售）');
    return { ok: true, value: v,
      made_at: monthAfterDays(period, params().make_days),
      arrives_at: monthAfterDays(period, params().total_days) };
  }

  function submitPlan(planId) {
    var s = ensure();
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };

    var rev = p.revs.length + 1;
    var skipped = [];
    var lineIds = [];
    var combos = {};

    /* ★ 计划单记录按「货号 × 月」铸出 —— 冻结的是**计划采购量**，
       因为供应链要下单的就是它（采购不关心店铺，见 docs/02 §3.1b）。
       同时把当时各店铺的期望销量一并冻结，供回溯「当时是按卖多少算的」。 */
    Object.keys(p.cells_purchase || {}).sort().forEach(function (key) {
      var c = p.cells_purchase[key];
      var planned = num(c.purchase);

      // 当时该货号各 msku 的期望销量（按店铺聚合）
      var demandBySeller = {}, demandSum = 0;
      Object.keys(p.cells_demand || {}).forEach(function (dk) {
        var d = p.cells_demand[dk];
        if (d.sku !== c.sku || d.period !== c.period) return;
        var v = num(d.demand);
        if (v === null) return;
        demandBySeller[d.seller_id] = (demandBySeller[d.seller_id] || 0) + v;
        demandSum += v;
      });

      // ★ 被丢掉的东西必须看得见：每一条 skip 都点名，并写清是哪种空
      if (planned === null) {
        skipped.push({
          sku: c.sku, period: c.period,
          reason: demandSum > 0
            ? '计划采购量未填（该月各店铺期望销量合计 ' + demandSum + '，但没说要买多少）'
            : '计划采购量与期望销量都没填'
        });
        return;
      }
      if (planned <= 0) {
        skipped.push({ sku: c.sku, period: c.period, reason: '计划采购量为 ' + planned + '，不生成记录' });
        return;
      }
      s.seq.line++;
      var lineId = 'L' + rev + '-' + pad(s.seq.line);
      s.lines.push({
        line_id: lineId,
        plan_id: p.plan_id,
        rev: rev,
        seller_id: null,                 // ★ 采购不带店铺
        sku: c.sku,
        period: c.period,
        total: planned,
        demand_at_submit: demandSum,
        demand_by_seller: demandBySeller,
        state: S.SUBMITTED,
        events: [{ day: s.day, from: '', to: S.SUBMITTED, by: p.owner, fact: '计划 ' + p.plan_id + ' rev' + rev + ' 提交，计划采购量 ' + planned + '（当时各店铺期望销量合计 ' + demandSum + '）' }]
      });
      lineIds.push(lineId);
    });

    // 旧 rev 里「一点进展都没有」的记录随新版作废；有进展的保留（历史是留痕，不是可改的）
    var dropped = 0;
    s.lines.forEach(function (l) {
      if (l.rev >= rev || l.state === S.CANCELLED) return;
      var q = lineQty(l.line_id);
      if (q.confirmed === 0 && q.staged === 0 && q.dispatched === 0) {
        l.events.push({ day: s.day, from: l.state, to: S.CANCELLED, by: p.owner, fact: '被 rev' + rev + '取代，且尚无任何组单/排货' });
        l.state = S.CANCELLED;
        dropped++;
      }
    });

    var digestSrc = Object.keys(p.cells_purchase || {}).sort().map(function (k) {
      return k + '=' + ((p.cells_purchase[k].purchase === null || p.cells_purchase[k].purchase === undefined) ? '' : p.cells_purchase[k].purchase);
    }).join(';');
    p.revs.forEach(function (r) { r.is_current = false; });
    p.revs.push({ rev: rev, digest: digestOf(digestSrc), is_current: true, day: s.day, line_ids: lineIds });

    reevaluate('提交计划');
    persist();
    log('提交计划 ' + p.title + ' rev' + rev + '：生成 ' + lineIds.length + ' 条记录，跳过 ' + skipped.length + ' 个空格子'
      + (dropped ? '，作废旧版 ' + dropped + ' 条无进展记录' : ''));
    dbg('plan.submit', { plan_id: p.plan_id, rev: rev, lines: lineIds.length, skipped: skipped.length, dropped: dropped });

    return { ok: true, rev: rev, skipped: skipped };
  }

  /* ============================================================
     采购：组单 · 下单
     ============================================================ */

  function buildPo(payload) {
    var s = ensure();
    payload = payload || {};
    var items = payload.items || [];

    // 🔒 领星必填项：缺一个就在组单时炸，而不是等下单调接口时吃 500
    if (!payload.supplier) return fail('missing_supplier', '供应商必填');
    if (payload.wid === undefined || payload.wid === null || payload.wid === '') return fail('missing_wid', '收货仓必填');
    if (!payload.opt_uid) return fail('missing_opt_uid', '采购员（opt_uid）必填 —— 领星硬性要求');
    if (!payload.purchaser_id) return fail('missing_purchaser_id', '采购方（purchaser_id）必填 —— 领星硬性要求');
    if (!items.length) return fail('empty_items', '至少要有一条明细');

    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var line = ensure().lines.filter(function (x) { return x.line_id === it.line_id; })[0];
      if (!line) return fail('line_not_found', '第 ' + (i + 1) + ' 行：找不到记录 ' + it.line_id);
      if (line.state === S.CANCELLED) return fail('line_cancelled', '第 ' + (i + 1) + ' 行：记录 ' + it.line_id + ' 已撤销');
      if (it.sku && it.sku !== line.sku) {
        return fail('sku_mismatch', '第 ' + (i + 1) + ' 行：货号 ' + it.sku + ' 与记录 ' + it.line_id + ' 的 ' + line.sku + ' 不一致');
      }
      var units = num(it.units);
      if (units === null || units <= 0) return fail('bad_units', '第 ' + (i + 1) + ' 行（' + it.line_id + '）：数量必须大于 0');
      // ★ 单价必填 —— 领星 CreatePurchaseOrder 的 price 是硬性必填。
      //   判据放在这里（= 真实系统里的 rules/ 层），不放页面：
      //   放页面就只对「记得调它的那条路径」有效，换个入口就绕过去了。
      var price = num(it.price);
      if (price === null || price < 0) {
        return fail('missing_price',
          '第 ' + (i + 1) + ' 行（' + it.line_id + ' · ' + line.sku + '）：单价必填 —— '
          + '领星 CreatePurchaseOrder 的 price 是硬性必填，缺了会在下单那一刻吃 500，'
          + '而那时人已经以为下完单了');
      }
      var q = lineQty(it.line_id);
      if (q.confirmed + units > q.total) {
        return fail('over_confirmed',
          '第 ' + (i + 1) + ' 行（' + it.line_id + ' · ' + line.sku + '）：本单 ' + units
          + ' 件 + 已组单 ' + q.confirmed + ' 件 > 总量 ' + q.total + ' 件');
      }
    }

    s.seq.po++;
    var poId = 'PO-' + pad(s.seq.po);
    var po = {
      po_id: poId,
      supplier: payload.supplier,
      wid: Number(payload.wid),
      opt_uid: payload.opt_uid,
      purchaser_id: payload.purchaser_id,
      state: '草稿',
      // ★ 我们生成的幂等键：超时后靠它查重，而不是靠「再下一次试试」
      custom_order_sn: 'SCM' + compact(s.day) + pad(s.seq.po),
      order_sn: '',
      placed_day: '',
      items: items.map(function (it) {
        var line = ensure().lines.filter(function (x) { return x.line_id === it.line_id; })[0];
        return { line_id: it.line_id, sku: it.sku || line.sku, units: Number(it.units), price: num(it.price) };
      }),
      snapshot: { status: '3待提交', status_shipped: 1 },
      receipts: []
    };
    s.pos.push(po);

    reevaluate('组采购单');
    persist();
    log('组采购单 ' + poId + '（' + po.supplier + ' → ' + warehouseName(po.wid) + '）：'
      + po.items.length + ' 行 / ' + sum(po.items, function (x) { return x.units; }) + ' 件，幂等键 ' + po.custom_order_sn);
    dbg('po.build', { po_id: poId, items: po.items.length, units: sum(po.items, function (x) { return x.units; }), custom_order_sn: po.custom_order_sn });
    return { ok: true, po_id: poId };

    function fail(code, detail) {
      err('po.build-rejected', { code: code, detail: detail, payload_keys: Object.keys(payload) });
      return { ok: false, error: code, detail: detail };
    }
  }

  /* ============================================================
     ③ 采购域 —— 采购员工作台（负责人 2026-09-18 线框）

     ★ 裁定：**「看的时候分店，下单的时候合并」**
       候选按 `计划记录 × 店铺` 展开给人看，勾选后按**货号**合并成采购单一行。

     ★ 为什么这条能成立：**店铺归属是人勾出来的，不是系统摊的。**
       采购员勾「US 那份 15 件」时，他做的是一个决定，系统只是记下来 ——
       所以 `已下单量[店铺]` 是**事实**，不是分摊假设。
       这也是它和库存预测里那个「未定向量」的根本区别。

     ★ 于是 P2「plan_line 不带 seller_id」保持不变：
       店铺归属落在**承重墙①的行上**（哪次组单消耗了哪个店的那份），
       而不是落在计划记录上。恒等式仍然成立：
           Σ_店铺 未组单量[店铺] ≡ 货号级未组单量
     ============================================================ */

  /** 新建空采购单（线框里那个弹窗）。★ 供应商 / 收货仓先定，建完不可改 */
  function createPo(payload) {
    var s = ensure();
    payload = payload || {};
    // 🔒 领星必填项：缺一个就在建单时炸，而不是等下单调接口时吃 500
    if (!payload.supplier) return poFail('missing_supplier', '供应商必填');
    if (payload.wid === undefined || payload.wid === null || payload.wid === '') {
      return poFail('missing_wid', '收货仓必填');
    }
    if (!payload.opt_uid) return poFail('missing_opt_uid', '采购员（opt_uid）必填 —— 领星硬性要求');
    if (!payload.purchaser_id) return poFail('missing_purchaser_id', '采购方（purchaser_id）必填 —— 领星硬性要求');

    s.seq.po++;
    var poId = 'PO-' + pad(s.seq.po);
    s.pos.push({
      po_id: poId,
      supplier: payload.supplier,
      wid: Number(payload.wid),
      opt_uid: payload.opt_uid,
      purchaser_id: payload.purchaser_id,
      state: '草稿',
      created_day: s.day,
      custom_order_sn: 'SCM' + compact(s.day) + pad(s.seq.po),
      order_sn: '', placed_day: '',
      items: [],
      snapshot: { status: '3待提交', status_shipped: 1 },
      receipts: []
    });
    persist();
    log('＋ 新建采购单 ' + poId + '（' + payload.supplier + ' → ' + warehouseName(payload.wid)
      + '）—— 空单，接下来从未处理的销售计划里挑货品。幂等键 ' + 'SCM' + compact(s.day) + pad(s.seq.po));
    return { ok: true, po_id: poId };

    function poFail(code, detail) {
      err('po.create-rejected', { code: code, detail: detail });
      return { ok: false, error: code, detail: detail };
    }
  }

  /** 某条计划记录、某个店铺那份，被各采购单消耗了多少 */
  function poUsage(lineId, sellerId) {
    var draft = 0, placed = 0;
    ensure().pos.forEach(function (po) {
      (po.items || []).forEach(function (it) {
        if (it.line_id !== lineId) return;
        if (sellerId !== undefined && sellerId !== null
            && String(it.seller_id) !== String(sellerId)) return;
        if (po.state === '已下单') placed += it.units; else draft += it.units;
      });
    });
    return { draft: draft, placed: placed };
  }

  /* ★ 未处理的销售计划产品 —— 按 `计划记录 × 店铺` 展开
       filter: { plan_id, sku, seller_id, from, to, only_open }
     ★ 返回两层：rows（店铺行，可勾）+ groups（货号合计行）。
       合计**不独立算**，是 rows 求和 —— 否则就是第二个真相。 */
  function poCandidates(filter) {
    var s = ensure(), f = filter || {};
    var rows = [], groups = {};

    s.lines.forEach(function (l) {
      if (l.state === S.CANCELLED) return;
      if (f.plan_id && l.plan_id !== f.plan_id) return;
      if (f.sku && l.sku.indexOf(f.sku) < 0) return;
      if (f.from && l.period < f.from) return;
      if (f.to && l.period > f.to) return;

      var byS = l.demand_by_seller || {};
      var sellers = Object.keys(byS);
      // ★ 一条记录一个店铺都没有 = 提交时期望销量全空。不许静默跳过：
      //   「没有候选」和「候选是 0」在界面上长得一模一样。
      if (!sellers.length) {
        rows.push({
          line_id: l.line_id, plan_id: l.plan_id, rev: l.rev, sku: l.sku, period: l.period,
          seller_id: null, seller_name: '（无店铺归属）', market: null,
          demand: null, drafted: 0, placed: 0, open: 0,
          state: l.state, selectable: false,
          why_not: '提交这一版时，这个货号在所有店铺的期望销量都是空的 —— '
                 + '所以不知道它该给谁。要组单请先回销售计划补期望销量。'
        });
        return;
      }
      sellers.forEach(function (sid) {
        if (f.seller_id && String(sid) !== String(f.seller_id)) return;
        var demand = Number(byS[sid]) || 0;
        var u = poUsage(l.line_id, sid);
        var open = Math.max(0, demand - u.draft - u.placed);
        if (f.only_open && open <= 0) return;
        rows.push({
          line_id: l.line_id, plan_id: l.plan_id, rev: l.rev, sku: l.sku, period: l.period,
          seller_id: sid, seller_name: sellerName(sid), market: marketOf(sid),
          demand: demand, drafted: u.draft, placed: u.placed, open: open,
          state: l.state, selectable: open > 0,
          why_not: open > 0 ? null : '这个店铺那份已经全部组单/下单了'
        });
      });

      // ★ 合计行只登记「不来自店铺行」的那两个数；其余全部由店铺行求和（见下）
      groups[l.line_id] = groups[l.line_id] || {
        line_id: l.line_id, plan_id: l.plan_id, rev: l.rev, sku: l.sku, period: l.period,
        // ★ 两个数，粒度不同，都要显示（docs/02 §3.1b）
        purchase_planned: l.total,          // 计划采购量 —— 供应链要下的量
        demand_total: 0,                    // 期望销量合计 —— 运营想卖的量
        drafted: 0, placed: 0, open: 0, sellers: 0
      };
    });

    rows.forEach(function (r) {
      var g = groups[r.line_id];
      if (!g || r.seller_id === null) return;
      g.demand_total += r.demand; g.drafted += r.drafted;
      g.placed += r.placed; g.open += r.open; g.sellers += 1;
    });

    return {
      rows: rows,
      groups: Object.keys(groups).map(function (k) { return groups[k]; }),
      matched: rows.length
    };
  }

  /* 把勾选的店铺行加进采购单。★ 按货号合并成一行，但每一份的店铺归属都留痕 */
  function addPoItems(poId, picks) {
    var po = findPo(poId);
    if (!po) return { ok: false, error: 'po_not_found', detail: '找不到采购单 ' + poId };
    if (po.state !== '草稿') {
      return { ok: false, error: 'po_locked',
        detail: '采购单 ' + poId + ' 已是「' + po.state + '」，不能再加货品' };
    }
    var list = picks || [], added = [], rejected = [];
    list.forEach(function (p, i) {
      var line = findLine(p.line_id);
      if (!line) { rejected.push('第 ' + (i + 1) + ' 项：找不到计划记录 ' + p.line_id); return; }
      var units = num(p.units);
      if (units === null || units <= 0) {
        rejected.push('第 ' + (i + 1) + ' 项（' + line.sku + '）：数量必须大于 0，收到「' + p.units + '」');
        return;
      }
      var demand = Number((line.demand_by_seller || {})[p.seller_id]) || 0;
      var u = poUsage(p.line_id, p.seller_id);
      var open = Math.max(0, demand - u.draft - u.placed);
      if (units > open) {
        rejected.push('第 ' + (i + 1) + ' 项（' + line.sku + ' · ' + sellerName(p.seller_id)
          + '）：本次 ' + units + ' 件 > 未组单 ' + open + ' 件'
          + '（需求 ' + demand + ' − 已组单 ' + u.draft + ' − 已下单 ' + u.placed + '）');
        return;
      }
      po.items.push({
        line_id: p.line_id, sku: line.sku, period: line.period,
        seller_id: p.seller_id,          // ★ 人勾出来的归属，不是系统摊的
        units: units, price: num(p.price)
      });
      added.push(p);
    });
    reevaluate('组采购单');
    persist();
    log('采购单 ' + poId + ' 添加 ' + added.length + ' 项'
      + (rejected.length ? '，拒绝 ' + rejected.length + ' 项（已点名）' : '')
      + '；当前 ' + po.items.length + ' 行 / ' + sum(po.items, function (x) { return x.units; }) + ' 件。');
    if (rejected.length) err('po.items-rejected', { po_id: poId, rejected: rejected });
    return { ok: true, added: added.length, rejected: rejected, items: po.items.length };
  }

  /** 改某一行的数量 / 单价 */
  function setPoItem(poId, idx, patch) {
    var po = findPo(poId);
    if (!po) return { ok: false, error: 'po_not_found', detail: '找不到采购单 ' + poId };
    if (po.state !== '草稿') {
      return { ok: false, error: 'po_locked', detail: '采购单已是「' + po.state + '」，不能改' };
    }
    var it = po.items[idx];
    if (!it) return { ok: false, error: 'item_not_found', detail: '第 ' + idx + ' 行不存在' };
    var p = patch || {};
    if (p.units !== undefined) {
      var units = num(p.units);
      if (units === null || units <= 0) {
        return { ok: false, error: 'bad_units', detail: '数量必须大于 0，收到「' + p.units + '」' };
      }
      var line = findLine(it.line_id);
      var demand = Number((line.demand_by_seller || {})[it.seller_id]) || 0;
      var u = poUsage(it.line_id, it.seller_id);
      var openExcl = Math.max(0, demand - (u.draft - it.units) - u.placed);
      if (units > openExcl) {
        return { ok: false, error: 'over_open',
          detail: '改成 ' + units + ' 件超了：这个店铺那份还剩 ' + openExcl + ' 件未组单' };
      }
      it.units = units;
    }
    if (p.price !== undefined) it.price = num(p.price);
    reevaluate('改采购单行');
    persist();
    log('采购单 ' + poId + ' 第 ' + (idx + 1) + ' 行：' + it.sku + ' → ' + it.units + ' 件 @ '
      + (it.price === null ? '（单价未填）' : it.price));
    return { ok: true, item: it };
  }

  /** ★ 移除一行 —— 「状态机能画出的退回边，必须真的退得回去」（P9） */
  function removePoItem(poId, idx) {
    var po = findPo(poId);
    if (!po) return { ok: false, error: 'po_not_found', detail: '找不到采购单 ' + poId };
    if (po.state !== '草稿') {
      return { ok: false, error: 'po_locked',
        detail: '采购单已是「' + po.state + '」—— 已下单的行不能移除（货可能在做、钱可能已付）' };
    }
    var it = po.items[idx];
    if (!it) return { ok: false, error: 'item_not_found', detail: '第 ' + idx + ' 行不存在' };
    po.items.splice(idx, 1);
    reevaluate('移除采购单行');
    persist();
    log('－ 采购单 ' + poId + ' 移除 ' + it.sku + ' · ' + sellerName(it.seller_id)
      + ' ' + it.units + ' 件，已退回「未组单」。');
    return { ok: true, left: po.items.length };
  }

  /** 本地仓到货进度（★ 到货是领星回执的镜像，我们不写） */
  function poProgress(poId) {
    var po = findPo(poId);
    if (!po) return null;
    var total = sum(po.items, function (x) { return x.units; });
    var arrived = sum(po.receipts || [], function (r) { return r.units; });
    return {
      po_id: poId, total: total, arrived: arrived,
      pending: Math.max(0, total - arrived),
      pct: total ? Math.round(arrived * 100 / total) : 0,
      lx_status: po.snapshot ? po.snapshot.status : null,
      shipped_flag: po.snapshot ? po.snapshot.status_shipped : null,
      /* ★ 字段名以**写入方**为准：tick() 落进 receipts 的是 `doc_no` / `day`。
         我第一版写成 `r.receipt_no || r.no`，取不到就是 undefined —— 而且**不吭声**。
         界面上「单号空着」和「这批货没单号」长得一模一样，正是静默兜底最坏的形态。
         现在：认写入方的字段名，另两个只作兼容；★ 三个都没有就吼一声。 */
      receipts: (po.receipts || []).map(function (r, i) {
        var no = r.doc_no || r.receipt_no || r.no;
        if (!no) {
          err('po.receipt-missing-doc-no', {
            po_id: poId, index: i, keys: Object.keys(r),
            why: '入库记录没有单号 —— 写入方的字段名可能又变了，界面会显示空白'
          });
        }
        return {
          receipt_no: no || null, sku: r.sku, units: r.units,
          wid: r.wid, warehouse: warehouseName(r.wid == null ? po.wid : r.wid),
          at: r.at || r.day || null
        };
      })
    };
  }

  /** 销售计划查询（线框左下角那块） */
  function planQuery(filter) {
    var s = ensure(), f = filter || {};
    return (s.plans || []).map(function (p) {
      var revs = p.revs || [];
      var cur = revs.filter(function (r) { return r.is_current; })[0] || revs[revs.length - 1] || null;
      return {
        plan_id: p.plan_id, title: p.title, owner: p.owner,
        period_start: p.period_start, months: (p.periods || []).length,
        period_end: (p.periods || [])[(p.periods || []).length - 1] || null,
        rev: cur ? cur.rev : null,
        state: revs.length ? '已提交' : '草稿',
        lines: s.lines.filter(function (l) { return l.plan_id === p.plan_id; }).length
      };
    }).filter(function (r) {
      if (f.state && r.state !== f.state) return false;
      if (f.plan_id && r.plan_id.indexOf(f.plan_id) < 0) return false;
      if (f.from && r.period_end && r.period_end < f.from) return false;
      if (f.to && r.period_start > f.to) return false;
      return true;
    });
  }

  function placePo(poId) {
    var s = ensure();
    var po = findPo(poId);
    if (!po) return { ok: false, error: 'po_not_found', detail: '找不到采购单 ' + poId };
    if (po.state === '已下单') {
      return { ok: false, error: 'already_placed', detail: '采购单 ' + poId + ' 已于 ' + po.placed_day + ' 下单，单号 ' + po.order_sn + '（幂等键 ' + po.custom_order_sn + ' 已被占用）' };
    }
    s.seq.doc++;
    po.order_sn = 'CGD' + compact(s.day) + pad(s.seq.doc);
    po.state = '已下单';
    po.placed_day = s.day;
    po.snapshot.status = '2待到货';
    po.snapshot.status_shipped = 1;

    reevaluate('下采购单');
    persist();
    log('⛔ 下采购单 ' + poId + ' → 领星单号 ' + po.order_sn + '（不可撤销；到货由每日采集回执推进）');
    dbg('po.place', { po_id: poId, order_sn: po.order_sn, day: s.day });
    return { ok: true, order_sn: po.order_sn };
  }

  /* ============================================================
     排货：候选 · 排货表 · 排货单 · 确认闸 · 投送
     ============================================================ */

  // ★ 候选判据（04 §1.2.2）：记录状态 ∈ {已下单, 准备排货} 且 待排量 > 0。
  //   物理够不够由人看库存判断，最终由确认闸 G3 兜底 —— 两个数不相减、不合成一个。
  function candidates() {
    var s = ensure();
    return s.lines.filter(function (l) {
      return (l.state === S.ORDERED || l.state === S.READY) && lineQty(l.line_id).pending > 0;
    }).map(function (l) {
      var byWarehouse = {};
      var available = 0;
      s.warehouses.forEach(function (w) {
        var st = s.stock[stockKey(l.sku, w.wid)] || { valid: 0, locked: 0 };
        byWarehouse[w.wid] = st.valid;
        available += st.valid;
      });
      return {
        line_id: l.line_id, sku: l.sku, seller_id: l.seller_id, period: l.period, state: l.state,
        pending: lineQty(l.line_id).pending,
        available: available,
        byWarehouse: byWarehouse
      };
    });
  }

  /* ============================================================
     ⑤ 排货域 —— 排货员工作台（负责人 2026-09-18 线框）

     ★ 线框上那句红字是整个设计的依据：
         **「基于采购计划操作，这样会让人更容易理解」**
       所以排货详情用的是**和计划网格同构的形态** —— 店铺·货号 × 月，
       每格摆着销售预估 / 库存预估 / 计划采购，外加一个「安排发货」。
       三个角色（运营填计划 → 采购组单 → 排货发货）共用同一个心智模型，
       不是三套各学一遍的界面。

     ★ 店铺是在**这一步**才定的（裁定 C4）：计划记录不带店铺，
       采购按货号下单，到了排货才决定「这批货发给谁」。
     ============================================================ */

  /** 销售计划完成度（排货员首页）—— 计划的货生产出来了多少、进本地仓了多少 */
  function planProgress() {
    var s = ensure();
    var rows = (s.plans || []).map(function (p) {
      var lines = s.lines.filter(function (l) {
        return l.plan_id === p.plan_id && l.state !== S.CANCELLED;
      });
      var demand = sum(lines, function (l) { return l.total; });
      // 「生产已入库」= 这些计划记录对应的采购单，已到本地仓的量
      var arrived = 0;
      s.pos.forEach(function (po) {
        (po.receipts || []).forEach(function (r) {
          var belongs = (po.items || []).some(function (it) {
            return it.sku === r.sku && lines.some(function (l) { return l.line_id === it.line_id; });
          });
          if (belongs) arrived += r.units;
        });
      });
      var skus = {};
      lines.forEach(function (l) { skus[l.sku] = 1; });
      return {
        plan_id: p.plan_id, title: p.title, owner: p.owner,
        state: (p.revs || []).length ? '已提交' : '草稿',
        skus: Object.keys(skus).length,
        demand: demand,
        arrived: arrived,
        pct: demand ? Math.round(arrived * 1000 / demand) / 10 : 0
      };
    });
    var d = sum(rows, function (r) { return r.demand; });
    var a = sum(rows, function (r) { return r.arrived; });
    return {
      rows: rows, demand_total: d, arrived_total: a,
      pct: d ? Math.round(a * 1000 / d) / 10 : 0
    };
  }

  /* ★ 排货网格 —— 和计划网格同构：店铺·货号 × 月
       每格给排货员四个数：
         销售预估（机器估的）· 期望销量（人填的）· 库存预估（推演出来的）· 计划采购（人填的）
       外加「这一格已经安排发了多少」。 */
  /* 对一组东西各求一个数再加起来 —— 比嵌套两层 sum 读起来清楚 */
  function sum2d(list, fn) {
    return (list || []).reduce(function (t, x) { return t + (fn(x) || 0); }, 0);
  }

  function dispatchGrid(sheetId) {
    var s = ensure();
    var sh = sheetId ? findSheet(sheetId) : null;
    var out = [];

    (s.plans || []).forEach(function (p) {
      if (!(p.revs || []).length) return;           // 只看已提交的计划
      var skus = {};
      (p.claims || []).forEach(function (c) { skus[c.sku] = 1; });
      Object.keys(skus).forEach(function (sku) {
        var f = forecastSku(p.plan_id, sku);
        if (!f) return;
        f.mskus.forEach(function (m) {
          var cells = p.periods.map(function (period) {
            var mm = m.months.filter(function (x) { return x.period === period; })[0] || {};
            var pc = (p.cells_purchase || {})[pKey(sku, period)] || {};
            var line = s.lines.filter(function (l) {
              return l.plan_id === p.plan_id && l.sku === sku && l.period === period
                  && l.state !== S.CANCELLED;
            })[0];
            return {
              period: period,
              system: mm.system === undefined ? null : mm.system,
              system_extrapolated: !!mm.system_extrapolated,
              demand: mm.demand === undefined ? null : mm.demand,
              closing: mm.closing === undefined ? null : mm.closing,
              unknown: !!mm.unknown,
              purchase: pc.purchase === undefined ? null : pc.purchase,
              line_id: line ? line.line_id : null,
              /* ★★ 这一格安排了多少 —— **拆成两个状态**，不是一个数。
                 原来只数待编排池，于是一编入排货单格子就归零、按钮变回
                 「安排发货」：**「还没排过」和「排了且已编进单」长得一模一样。**
                 ★ 两者的处置完全相反：前者要去安排，后者要去排货单里动。 */
              pooled: sh ? sum(sh.pool || [], function (x) {
                return (x.line_id === (line && line.line_id)
                     && String(x.seller_id) === String(m.seller_id)) ? x.units : 0;
              }) : 0,
              consigned: sh ? sum2d(sh.consignments || [], function (c) {
                if (c.our_stage === '已投送' || c.our_stage === '已归档') return 0;
                return sum(c.lines || [], function (l) {
                  return (l.line_id === (line && line.line_id)
                       && String(l.seller_id) === String(m.seller_id)) ? l.units : 0;
                });
              }) : 0,
              /* ★ 已投送的那部分要单独留着。
                 不留的话，投送之后这一格又回到 arranged=0、又显示「安排发货」——
                 **「已经发出去了」和「从没排过」再一次长得一模一样**。
                 货确实已经出手了，所以它是只读的；但它必须**看得见**。 */
              dispatched: sh ? sum2d(sh.consignments || [], function (c) {
                if (c.our_stage !== '已投送' && c.our_stage !== '已归档') return 0;
                return sum(c.lines || [], function (l) {
                  return (l.line_id === (line && line.line_id)
                       && String(l.seller_id) === String(m.seller_id)) ? l.units : 0;
                });
              }) : 0,
              // 合计：给「这一格到底动没动过」用；★ 可改的只有 pooled 那部分
              arranged: sh ? (sum(sh.pool || [], function (x) {
                return (x.line_id === (line && line.line_id)
                     && String(x.seller_id) === String(m.seller_id)) ? x.units : 0;
              }) + sum2d(sh.consignments || [], function (c) {
                if (c.our_stage === '已投送' || c.our_stage === '已归档') return 0;
                return sum(c.lines || [], function (l) {
                  return (l.line_id === (line && line.line_id)
                       && String(l.seller_id) === String(m.seller_id)) ? l.units : 0;
                });
              })) : 0
            };
          });
          out.push({
            plan_id: p.plan_id, sku: sku,
            seller_id: m.seller_id, seller_name: sellerName(m.seller_id),
            seller_sku: m.seller_sku, market: m.market, has_fba: m.has_fba,
            periods: p.periods, cells: cells,
            total_demand: m.demand_total, total_gap: m.gap_total,
            stockout_month: m.stockout_month
          });
        });
      });
    });
    return out;
  }

  /* ★★ 某仓某货号「已承诺但还没发出」的量。

     ★ 这是一个**共享池**：同一批物理库存，10 月排一次、11 月又排一次，
       两次都对着同一个 `valid` 比大小 —— 于是 14 件能被承诺 28 件。
       负责人实测：库存 14，10 月安排 10 之后，11 月仍然允许再安排 14。

     ★ 根因：`valid` 只在**投送**那一刻才扣（shipStock）。
       从「安排发货」到「投送」之间，这批货在物理上还在仓里、
       在账上却已经有主了 —— 这段窗口没有任何地方把它减掉。

     所以可安排量要现算：
         可安排 = valid − 已承诺未发出
     其中「已承诺未发出」= 待编排池 + 尚未投送的排货单行。
     ★ 不减 `locked`：确认时 lockStock 记的是同一批量的另一面，
       两个都减就成了双重计入。 */
  function committedUnits(sku, wid, exceptSheetId, exceptCell) {
    var s2 = ensure(), t = 0;
    /* ★ exceptCell = {sheet_id, line_id, seller_id}：算「改成 N 件」够不够时，
       这一格**自己现在占的量**不能算进「别人占的」—— 否则改小也会被自己挡住。 */
    var ec = exceptCell || null;
    (s2.sheets || []).forEach(function (sh) {
      if (sh.state === '已取消') return;
      if (exceptSheetId && sh.sheet_id === exceptSheetId) return;
      (sh.pool || []).forEach(function (p) {
        if (p.sku !== sku || String(p.from_wid) !== String(wid)) return;
        if (ec && sh.sheet_id === ec.sheet_id && p.line_id === ec.line_id
            && String(p.seller_id) === String(ec.seller_id)) return;
        t += p.units;
      });
      (sh.consignments || []).forEach(function (c) {
        // ★ 已投送 / 已归档的，valid 已经扣过了，不能再算一次
        if (c.our_stage === '已投送' || c.our_stage === '已归档') return;
        (c.lines || []).forEach(function (l) {
          if (l.sku === sku && String(l.from_wid) === String(wid)) t += l.units;
        });
      });
    });
    return t;
  }

  /** 某仓某货号现在还能安排多少 */
  function freeUnits(sku, wid, exceptSheetId) {
    var st = ensure().stock[stockKey(sku, wid)] || { valid: 0 };
    return Math.max(0, st.valid - committedUnits(sku, wid, exceptSheetId));
  }

  /** 本地仓货源（「安排发货」弹出的筛选器）—— ★ 按货号自动过滤 */
  function localSources(sku) {
    var s = ensure();
    return (s.warehouses || []).map(function (w) {
      var st = s.stock[stockKey(sku, w.wid)] || { valid: 0, locked: 0 };
      var used = committedUnits(sku, w.wid, null);
      return {
        wid: w.wid, warehouse: w.name || warehouseName(w.wid), sku: sku,
        valid: st.valid,            // 仓里还有多少（物理）
        locked: st.locked,
        committed: used,            // ★ 其中已经被别的月份/别的排货表承诺掉的
        free: Math.max(0, st.valid - used)   // ★ 现在还能安排多少
      };
    }).filter(function (r) { return r.valid > 0 || r.locked > 0; });
  }

  /* 安排发货：把某一格的量从本地仓分拨进排货表的待编排池。
     picks: [{wid, units}] —— ★ 分几个仓出是排货员的决定，不是系统摊的 */
  function arrangeShipment(sheetId, cell) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    if (sh.state !== '草稿') {
      return { ok: false, error: 'sheet_not_draft',
        detail: '排货表 ' + sheetId + ' 已是「' + sh.state + '」，不能再安排发货' };
    }
    cell = cell || {};
    if (!cell.line_id) {
      return { ok: false, error: 'no_line',
        detail: '这一格没有计划记录 —— 该月这个货号还没提交过计划采购量，没有可排的量。'
              + '要排货请先回销售计划补上并提交。' };
    }
    if (!cell.seller_id) {
      return { ok: false, error: 'no_seller',
        detail: '没说发给哪个店铺 —— 排货是「给货指定店铺」的那一步，店铺不能空' };
    }
    var picks = (cell.picks || []).filter(function (x) { return num(x.units) > 0; });
    if (!picks.length) {
      return { ok: false, error: 'empty_picks', detail: '至少要从一个本地仓分拨出大于 0 的数量' };
    }

    var line = findLine(cell.line_id);
    var q = lineQty(cell.line_id);
    var want = sum(picks, function (x) { return Number(x.units); });

    /* ★ 两条限制：待排量、各仓可发量。**一次报全，不要只报第一个撞到的** ——
       只报一个会让人玩打地鼠：改完这个又撞那个，每次都以为快好了。 */
    var rejected = [];
    if (want > q.pending) {
      rejected.push('待排量不够：本次 ' + want + ' 件 > 这条记录还能排 ' + q.pending + ' 件'
        + '（总量 ' + q.total + ' − 已排 ' + q.staged + ' − 已投送 ' + q.dispatched + '）');
    }
    /* 逐仓校验 —— ★ 比的是**可安排量**，不是仓里的物理量。
       物理量是共享池：10 月安排过的 10 件，11 月不能再安排一次。
       ★ 同一张排货表内部的已安排量也要算进去（exceptSheetId 传 null），
         否则在同一张表里连点两次「安排发货」照样能超订。 */
    var wantByWid = {};
    picks.forEach(function (x) {
      wantByWid[x.wid] = (wantByWid[x.wid] || 0) + Number(x.units);
    });
    Object.keys(wantByWid).forEach(function (wid) {
      var st = ensure().stock[stockKey(line.sku, wid)] || { valid: 0 };
      var used = committedUnits(line.sku, wid, null);
      var free = Math.max(0, st.valid - used);
      if (wantByWid[wid] > free) {
        rejected.push(warehouseName(wid) + '：要 ' + wantByWid[wid] + ' 件，'
          + '现在只能安排 ' + free + ' 件'
          + '（仓里 ' + st.valid + ' 件，其中 ' + used + ' 件已被别处安排、还没发出）');
      }
    });
    if (rejected.length) {
      err('sheet.arrange-rejected', { sheet_id: sheetId, line_id: cell.line_id, rejected: rejected });
      return {
        ok: false,
        error: want > q.pending ? 'over_pending' : 'over_stock',
        detail: rejected.join('；'),
        rejected: rejected              // ★ 全部原因，界面要逐条列出
      };
    }

    sh.pool = sh.pool || [];
    picks.forEach(function (x) {
      sh.pool.push({
        line_id: cell.line_id, sku: line.sku, period: line.period,
        seller_id: cell.seller_id,        // ★ 店铺在这一步才定（裁定 C4）
        from_wid: Number(x.wid), units: Number(x.units)
      });
    });
    reevaluate('安排发货');
    persist();
    log('排货表 ' + sheetId + ' 安排发货：' + line.sku + ' · ' + sellerName(cell.seller_id)
      + ' · ' + line.period + ' 共 ' + want + ' 件（'
      + picks.map(function (x) { return warehouseName(x.wid) + ' ' + x.units; }).join('，') + '）');
    return { ok: true, units: want, pool: sh.pool.length };
  }

  /* ★★ 一个格子的安排状态，由**一次操作整体决定**。

     ★ 为什么不做成「加一笔 / 减一笔」：那会长出三条路（安排 / 改 / 撤回），
       三条路各有各的校验，迟早对不上 —— 而「改」恰恰是最容易被漏掉的那条
       （负责人 2026-09-18 实测：只能撤回再重排，改个数量要两步）。
     ★ 收成一个原语之后：
         安排 = 从空替换成 N
         改   = 从 N 替换成 M
         撤回 = 替换成空
       三件事同一条校验、同一条日志、同一条回退边。 */
  function setArrangement(sheetId, cell) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    if (sh.state !== '草稿') {
      return { ok: false, error: 'sheet_not_draft',
        detail: '排货表 ' + sheetId + ' 已是「' + sh.state + '」，不能再改安排' };
    }
    cell = cell || {};
    if (!cell.line_id) {
      return { ok: false, error: 'no_line',
        detail: '这一格没有计划记录 —— 该月这个货号还没提交过计划采购量，没有可排的量。'
              + '要排货请先回销售计划补上并提交。' };
    }
    if (!cell.seller_id) {
      return { ok: false, error: 'no_seller',
        detail: '没说发给哪个店铺 —— 排货是「给货指定店铺」的那一步，店铺不能空' };
    }

    var line = findLine(cell.line_id);
    if (!line) return { ok: false, error: 'line_not_found', detail: '找不到计划记录 ' + cell.line_id };

    var picks = (cell.picks || []).filter(function (x) { return num(x.units) > 0; });
    var want = sum(picks, function (x) { return Number(x.units); });

    // 这一格现在占了多少（要从校验里刨掉，否则改小也会被自己挡住）
    var mine = sum(sh.pool || [], function (p) {
      return (p.line_id === cell.line_id && String(p.seller_id) === String(cell.seller_id))
        ? p.units : 0;
    });

    /* ★ 两条限制一次报全，不让人玩打地鼠 */
    var rejected = [];
    var q = lineQty(cell.line_id);
    if (want > q.pending + mine) {
      rejected.push('待排量不够：要 ' + want + ' 件 > 这条记录还能排 ' + (q.pending + mine) + ' 件'
        + '（总量 ' + q.total + ' − 已排 ' + (q.staged - mine) + ' − 已投送 ' + q.dispatched + '）');
    }
    var ec = { sheet_id: sheetId, line_id: cell.line_id, seller_id: cell.seller_id };
    var wantByWid = {};
    picks.forEach(function (x) { wantByWid[x.wid] = (wantByWid[x.wid] || 0) + Number(x.units); });
    Object.keys(wantByWid).forEach(function (wid) {
      var st = ensure().stock[stockKey(line.sku, wid)] || { valid: 0 };
      var used = committedUnits(line.sku, wid, null, ec);
      var free = Math.max(0, st.valid - used);
      if (wantByWid[wid] > free) {
        rejected.push(warehouseName(wid) + '：要 ' + wantByWid[wid] + ' 件，'
          + '现在只能安排 ' + free + ' 件'
          + '（仓里 ' + st.valid + ' 件，其中 ' + used + ' 件已被别处安排、还没发出）');
      }
    });
    if (rejected.length) {
      err('sheet.arrange-rejected', { sheet_id: sheetId, line_id: cell.line_id, rejected: rejected });
      return { ok: false, error: 'over_limit', detail: rejected.join('；'), rejected: rejected };
    }

    // ★ 整体替换：先把这一格原来的清掉，再按新的写进去
    sh.pool = (sh.pool || []).filter(function (p) {
      return !(p.line_id === cell.line_id && String(p.seller_id) === String(cell.seller_id));
    });
    picks.forEach(function (x) {
      sh.pool.push({
        line_id: cell.line_id, sku: line.sku, period: line.period,
        seller_id: cell.seller_id,        // ★ 店铺在这一步才定（裁定 C4）
        from_wid: Number(x.wid), units: Number(x.units)
      });
    });
    reevaluate('安排发货');
    persist();
    log('排货表 ' + sheetId + '　' + line.sku + ' · ' + sellerName(cell.seller_id) + ' · ' + line.period
      + '：' + mine + ' → ' + want + ' 件'
      + (picks.length ? '（' + picks.map(function (x) {
          return warehouseName(x.wid) + ' ' + x.units; }).join('，') + '）' : '（已清空）'));
    return { ok: true, units: want, was: mine, pool: sh.pool.length };
  }

  /** 撤回某一格已安排的发货（★ 画得出的退回边必须真的退得回去） */
  function unarrangeShipment(sheetId, lineId, sellerId) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    if (sh.state !== '草稿') {
      return { ok: false, error: 'sheet_not_draft',
        detail: '排货表已是「' + sh.state + '」，不能撤回' };
    }
    var before = (sh.pool || []).length;
    var gone = 0;
    sh.pool = (sh.pool || []).filter(function (x) {
      var hit = x.line_id === lineId && String(x.seller_id) === String(sellerId);
      if (hit) gone += x.units;
      return !hit;
    });
    if (before === sh.pool.length) {
      return { ok: false, error: 'nothing_to_undo', detail: '这一格本来就没有安排过发货' };
    }
    reevaluate('撤回发货安排');
    persist();
    log('排货表 ' + sheetId + ' 撤回 ' + gone + ' 件（' + sellerName(sellerId) + '），已退回待排量。');
    return { ok: true, released: gone };
  }

  /** 排货单列表（线框「排货单」那一块） */
  function sheetList(filter) {
    var s = ensure(), f = filter || {};
    var out = [];
    (s.sheets || []).forEach(function (sh) {
      (sh.consignments || []).forEach(function (c) {
        var skus = {};
        (c.lines || []).forEach(function (l) { skus[l.sku] = 1; });
        var row = {
          sheet_id: sh.sheet_id, cid: c.cid, state: sh.state,
          channel: c.channel, channel_label: c.channel === 'fba' ? 'FBA' : '海外仓',
          /* ★ 目的地的形状**按渠道不同**：FBA 是 {sid, fc}（店铺 + 亚马逊 FC），
             海外仓是 {wid}。硬当成 wid 读会得到 undefined，
             再喂给 warehouseName 就是一句 warehouse.unknown —— 界面上只看到「—」。 */
          dest: c.dest || {},
          dest_label: c.channel === 'fba'
            ? ('店铺 ' + ((c.dest || {}).sid || '（缺）') + ' · FC ' + ((c.dest || {}).fc || '（缺）'))
            : ((c.dest || {}).wid ? warehouseName(c.dest.wid) : '（目的仓未定）'),
          our_stage: c.our_stage, lingxing_stage: c.lingxing_stage || null,
          skus: Object.keys(skus).length,
          units: sum(c.lines || [], function (l) { return l.units; })
        };
        if (f.channel && row.channel !== f.channel) return;
        if (f.sku && !Object.keys(skus).some(function (k) { return k.indexOf(f.sku) >= 0; })) return;
        out.push(row);
      });
      // ★ 空排货表也要出现在列表里 —— 「建了但还没编排」和「没建过」是两回事
      if (!(sh.consignments || []).length) {
        out.push({
          sheet_id: sh.sheet_id, cid: null, state: sh.state,
          channel: null, channel_label: '（未编排）', dest: {}, dest_label: '—',
          our_stage: '草稿', lingxing_stage: null,
          skus: 0, units: sum(sh.pool || [], function (p) { return p.units; }),
          pooled_only: true
        });
      }
    });
    return out;
  }

  function createSheet() {
    var s = ensure();
    s.seq.sheet++;
    var id = 'SHEET-' + pad(s.seq.sheet);
    s.sheets.push({ sheet_id: id, day: s.day, state: '草稿', pool: [], consignments: [] });
    persist();
    log('新建排货表 ' + id);
    dbg('sheet.create', { sheet_id: id });
    return { ok: true, sheet_id: id };
  }

  // 把候选捡进排货表（进「待编排池」，还没分到哪张排货单）
  function addSheetLines(sheetId, picks) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    if (sh.state !== '草稿') return { ok: false, error: 'sheet_not_draft', detail: '排货表 ' + sheetId + ' 已是「' + sh.state + '」，不能再捡货' };
    picks = picks || [];
    var added = 0, rejected = [];
    picks.forEach(function (p, i) {
      var line = ensure().lines.filter(function (x) { return x.line_id === p.line_id; })[0];
      if (!line) { rejected.push('第 ' + (i + 1) + ' 项：找不到记录 ' + p.line_id); return; }
      var units = num(p.units);
      if (units === null || units <= 0) { rejected.push('第 ' + (i + 1) + ' 项（' + p.line_id + '）：数量必须大于 0'); return; }
      var q = lineQty(p.line_id);
      if (units > q.pending) {
        rejected.push('第 ' + (i + 1) + ' 项（' + p.line_id + ' · ' + line.sku + '）：捡 ' + units + ' 件 > 待排 ' + q.pending + ' 件');
        return;
      }
      sh.pool.push({ line_id: p.line_id, sku: p.sku || line.sku, from_wid: Number(p.from_wid), units: units });
      added++;
    });
    // ★ 被丢弃的那一侧必须统计并报出来，不许静默少一批
    if (rejected.length) err('sheet.picks-rejected', { sheet_id: sheetId, rejected: rejected, accepted: added });
    reevaluate('捡货');
    persist();
    log('排货表 ' + sheetId + ' 捡入 ' + added + ' 行' + (rejected.length ? '，拒绝 ' + rejected.length + ' 行：' + rejected.join('；') : ''));
    return { ok: rejected.length === 0, added: added, rejected: rejected };
  }

  function createConsignment(sheetId, opt) {
    var s = ensure();
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    if (sh.state !== '草稿') return { ok: false, error: 'sheet_not_draft', detail: '排货表 ' + sheetId + ' 已是「' + sh.state + '」，不能再加排货单' };
    opt = opt || {};
    if (opt.channel !== 'fba' && opt.channel !== 'oversea') {
      return { ok: false, error: 'bad_channel', detail: '渠道只能是 fba 或 oversea，收到 ' + JSON.stringify(opt.channel) };
    }
    /* ★ 目的地的形状**按渠道不同**：FBA 是 {sid, fc}，海外仓是 {wid}。
       原来这里写的是 `opt.dest || {}` —— 调用方传错字段名（比如 dest_wid）
       就**静默变成空对象**，一路到界面才显示成「（缺）」，而那时已经没人知道
       是没填还是传丢了。★ 一个排货单没有目的地是不成立的，所以当场拒。 */
    var dest = opt.dest || {};
    if (opt.channel === 'fba' && !(dest.sid && dest.fc)) {
      return { ok: false, error: 'bad_dest',
        detail: 'FBA 排货单的目的地要给 {sid, fc}（店铺 + 亚马逊 FC），收到 '
              + JSON.stringify(opt.dest === undefined ? '（没传 dest）' : opt.dest)
              + '。★ 注意字段名是 dest，不是 dest_wid。' };
    }
    if (opt.channel === 'oversea' && !dest.wid) {
      return { ok: false, error: 'bad_dest',
        detail: '海外仓排货单的目的地要给 {wid}（收货仓），收到 '
              + JSON.stringify(opt.dest === undefined ? '（没传 dest）' : opt.dest)
              + '。★ 注意字段名是 dest，不是 dest_wid。' };
    }

    s.seq.cid++;
    var cid = s.seq.cid;
    sh.consignments.push({
      cid: cid,
      channel: opt.channel,
      dest: dest,
      our_stage: '草稿',
      lines: [],
      ext_refs: [],
      lingxing_stage: ''
    });
    persist();
    log('排货表 ' + sheetId + ' 新建排货单 #' + cid + '（' + (opt.channel === 'fba' ? '美国 FBA' : '海外仓') + '）');
    return { ok: true, cid: cid };
  }

  // 从待编排池挪进某张排货单（可部分挪）
  function moveToConsignment(sheetId, cid, pick) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    var cons = sh.consignments.filter(function (c) { return String(c.cid) === String(cid); })[0];
    if (!cons) {
      err('consignment.not-in-sheet', { sheet_id: sheetId, cid: cid, known: sh.consignments.map(function (c) { return c.cid; }) });
      return { ok: false, error: 'consignment_not_found', detail: '排货表 ' + sheetId + ' 里没有排货单 #' + cid };
    }
    if (cons.our_stage !== '草稿') return { ok: false, error: 'consignment_locked', detail: '排货单 #' + cid + ' 已是「' + cons.our_stage + '」，不能再改行' };
    pick = pick || {};
    var units = num(pick.units);
    if (units === null || units <= 0) return { ok: false, error: 'bad_units', detail: '数量必须大于 0' };

    /* ★★ 池里的一行是 (计划记录 × 店铺 × 来源仓)。
       原来这里**不看 seller_id** —— 同一条记录下两个店铺从同一个仓各安排一笔，
       就会撞成两条同键的行，而代码 `break` 在第一条。
       ★ 排货**就是**「给货指定店铺」的那一步（裁定 C4），
         在编入排货单时把店铺丢掉，等于把这一步的结论扔了。 */
    var hits = [];
    for (var i = 0; i < sh.pool.length; i++) {
      var p = sh.pool[i];
      if (p.line_id !== pick.line_id) continue;
      if (String(p.from_wid) !== String(pick.from_wid)) continue;
      if (pick.sku && p.sku !== pick.sku) continue;
      if (pick.seller_id !== undefined && pick.seller_id !== null
          && String(p.seller_id) !== String(pick.seller_id)) continue;
      hits.push(i);
    }
    if (!hits.length) {
      err('pool.entry-missing', { sheet_id: sheetId, want: pick, pool: sh.pool });
      return { ok: false, error: 'pool_entry_missing',
        detail: '待编排池里没有「' + pick.line_id + ' · 仓 ' + pick.from_wid
              + (pick.seller_id ? ' · ' + sellerName(pick.seller_id) : '') + '」这一项' };
    }
    /* ★ 歧义时**不许取第一条** —— 取第一条会把货编给错的店，
       而界面上两行长得一模一样，没有任何东西会告诉人挑错了。 */
    if (hits.length > 1) {
      var who = hits.map(function (k) {
        return sellerName(sh.pool[k].seller_id) + ' ' + sh.pool[k].units + ' 件';
      });
      err('pool.entry-ambiguous', { sheet_id: sheetId, want: pick, candidates: who });
      return { ok: false, error: 'pool_entry_ambiguous',
        detail: '待编排池里有 ' + hits.length + ' 行都对得上（' + who.join('、')
              + '）—— 请说清是哪个店铺那一笔。'
              + '★ 排货是「给货指定店铺」的那一步，这里不能替你挑。' };
    }
    var idx = hits[0];
    var entry = sh.pool[idx];
    if (units > entry.units) {
      return { ok: false, error: 'over_pool', detail: '要挪 ' + units + ' 件，池里只有 ' + entry.units + ' 件（' + entry.line_id + ' · ' + warehouseName(entry.from_wid) + '）' };
    }
    entry.units -= units;
    if (entry.units === 0) sh.pool.splice(idx, 1);
    cons.lines.push({
      line_id: entry.line_id, sku: entry.sku, from_wid: entry.from_wid,
      seller_id: entry.seller_id,        // ★ 店铺归属要跟着货走，不能停在池子里
      units: units, shipment_no: '', box: null
    });
    persist();
    log('排货表 ' + sheetId + '：' + entry.line_id + ' · ' + sellerName(entry.seller_id)
      + ' × ' + units + ' 件（' + warehouseName(entry.from_wid) + '）→ 排货单 #' + cid);
    return { ok: true, idx: cons.lines.length - 1 };
  }

  /* ★ 保留这个函数是给**回执/订正**用的，不是给界面当输入框用。
     货件号由投送时的「取得货件号」自动回填 —— 界面上不该有让人手填它的地方，
     因为在人能看到这个界面的那一刻，这个号还不存在。 */
  function setLineShipment(sheetId, cid, idx, shipmentNo) {
    var r = lineAt(sheetId, cid, idx);
    if (!r.ok) return r;
    r.line.shipment_no = shipmentNo || '';
    persist();
    log('排货单 #' + cid + ' 第 ' + (idx + 1) + ' 行归属货件 ' + (shipmentNo || '（清空）'));
    return { ok: true };
  }

  function setLineBox(sheetId, cid, idx, box) {
    var r = lineAt(sheetId, cid, idx);
    if (!r.ok) return r;
    r.line.box = box ? {
      count: num(box.count), l: num(box.l), w: num(box.w), h: num(box.h),
      weight: num(box.weight), per: num(box.per)
    } : null;
    persist();
    log('排货单 #' + cid + ' 第 ' + (idx + 1) + ' 行填装箱数据');
    return { ok: true };
  }

  function lineAt(sheetId, cid, idx) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId };
    var cons = sh.consignments.filter(function (c) { return String(c.cid) === String(cid); })[0];
    if (!cons) return { ok: false, error: 'consignment_not_found', detail: '排货表 ' + sheetId + ' 里没有排货单 #' + cid };
    var line = cons.lines[idx];
    if (!line) {
      err('consignment.line-index-out-of-range', { sheet_id: sheetId, cid: cid, idx: idx, have: cons.lines.length });
      return { ok: false, error: 'line_index_out_of_range', detail: '排货单 #' + cid + ' 只有 ' + cons.lines.length + ' 行，没有第 ' + (idx + 1) + ' 行' };
    }
    return { ok: true, sheet: sh, cons: cons, line: line };
  }

  /* ---------- 确认闸 6 项（docs/06-核心业务流程.md §3.2） ----------
     ★ 它是一组可证伪检查，不是一个状态位。
     ★ 必须在确认时硬失败，而不是发货时才炸 —— 那时已经跨过不可逆边界了。
     ★ 过的也返回：只列失败项，人就不知道另外五项到底跑没跑。
     ------------------------------------------------------------- */

  function consLabel(sh, cons) {
    var i = sh.consignments.indexOf(cons);
    return '排货单#' + (i + 1) + '（#' + cons.cid + ' · ' + (cons.channel === 'fba' ? 'FBA' : '海外仓') + '）';
  }
  function rowLabel(sh, cons, idx) {
    var l = cons.lines[idx];
    return consLabel(sh, cons) + ' 第 ' + (idx + 1) + ' 行（' + l.sku + ' · ' + warehouseName(l.from_wid) + ' · ' + l.units + ' 件）';
  }

  // G1 装箱数据齐全（FBA 行）
  function gateG1(sh) {
    var fields = [['count', '箱数'], ['l', '长'], ['w', '宽'], ['h', '高'], ['weight', '重量'], ['per', '每箱数量']];
    var bad = [], fbaRows = 0;
    sh.consignments.forEach(function (c) {
      if (c.channel !== 'fba') return;
      c.lines.forEach(function (l, i) {
        fbaRows++;
        if (!l.box) { bad.push(rowLabel(sh, c, i) + '：装箱数据整块为空'); return; }
        var miss = fields.filter(function (f) { return l.box[f[0]] === null || l.box[f[0]] === undefined || l.box[f[0]] === '' || Number(l.box[f[0]]) <= 0; })
          .map(function (f) { return f[1]; });
        if (miss.length) bad.push(rowLabel(sh, c, i) + '：缺 ' + miss.join('、'));
      });
    });
    return mk('G1', 'packing_incomplete', '装箱数据齐全', bad,
      fbaRows ? fbaRows + ' 个 FBA 行装箱数据齐全' : '本表没有 FBA 行，不适用');
  }

  /* G2 FBA 排货单具备建货件的前置条件

     ★★ 这一条原来检查的是「每行已归属货件（shipment_no 非空）」—— **时点错了**。
       货件号是亚马逊在 `confirmPlacementOption` 之后才返回的
       （docs/02 §488、docs/07 §337：不轮询 taskId 就不知道货件号），
       而确认闸跑在**投送之前** —— 那一刻这个号**根本不存在**。
       界面上于是长出一个让人手填 `FBA16XXXXX` 的输入框，而没有人填得出来。

     ★ 文档里 A-6 说的是「排货行**发货前**必须已归属货件」——
       「发货」是投送链的最后一步，在「STA 建货件」**之后**。
       所以这条判据的正确位置在**投送链内部**（见 dispatch），不在确认闸。

     ★ 那确认闸这一格还能查什么？—— 查**建货件需要的东西齐不齐**：
       FBA 的目的地（店铺 sid + 亚马逊 FC）。这个是人定的、此刻就该有。 */
  function gateG2(sh) {
    var bad = [], fbaCons = 0;
    sh.consignments.forEach(function (c) {
      if (c.channel !== 'fba') return;
      fbaCons++;
      var d = c.dest || {};
      if (!d.sid || !d.fc) {
        bad.push(consLabel(sh, c) + '：目的地不全（店铺 ' + (d.sid || '（缺）')
          + ' · FC ' + (d.fc || '（缺）') + '）—— 建货件需要它');
      }
    });
    return mk('G2', 'dest_incomplete', 'FBA 排货单目的地齐全（建货件的前置）', bad,
      fbaCons ? fbaCons + ' 张 FBA 排货单的目的地都齐了；★ 货件号在投送时由亚马逊返回，不用人填'
              : '本表没有 FBA 排货单，不适用');
  }

  // G3 可发量够 —— ★ 只读 valid（product_valid_num），禁止用总量相减
  function gateG3(sh) {
    var s = ensure(), need = {}, where = {};
    sh.consignments.forEach(function (c) {
      c.lines.forEach(function (l, i) {
        var k = stockKey(l.sku, l.from_wid);
        need[k] = (need[k] || 0) + l.units;
        (where[k] = where[k] || []).push(rowLabel(sh, c, i));
      });
    });
    var bad = [], okPairs = 0;
    Object.keys(need).forEach(function (k) {
      var st = s.stock[k] || { valid: 0, locked: 0 };
      var parts = k.split('|');
      if (need[k] > st.valid) {
        bad.push(parts[0] + ' @ ' + warehouseName(parts[1]) + '：需 ' + need[k] + '，可发 ' + st.valid
          + '（锁定 ' + st.locked + '）→ ' + where[k].join('；'));
      } else { okPairs++; }
    });
    return mk('G3', 'insufficient_available', '可发量够', bad,
      okPairs ? okPairs + ' 个（货号 × 仓）组合可发量都够' : '本表没有行');
  }

  // G4 没撞别的排货表的「已排未发」—— 原型里恒过（真实实现是账本水位比对）
  function gateG4(sh) {
    return mk('G4', 'ledger_moved', '未撞别的排货表的已排未发', [],
      '账本水位无冲突（原型恒过；真实实现要比对其它排货表的已排未发量）');
  }

  // G5 「疑似未关联的发货」已清零 —— 原型里恒过
  function gateG5(sh) {
    return mk('G5', 'unlinked_shipments_pending', '未关联发货已清零', [],
      '疑似未关联的发货 = 0（原型恒过；真实实现读领星侧未关联发货单）');
  }

  // G6 目的地合法（oversea 有 r_wid；fba 有 sid + FC）
  function gateG6(sh) {
    var bad = [];
    sh.consignments.forEach(function (c) {
      var d = c.dest || {};
      if (c.channel === 'oversea') {
        if (!d.wid) bad.push(consLabel(sh, c) + '：海外仓渠道缺目的仓 wid');
      } else {
        if (!d.sid) bad.push(consLabel(sh, c) + '：FBA 渠道缺店铺 sid');
        if (!d.fc) bad.push(consLabel(sh, c) + '：FBA 渠道缺 FC 代码');
      }
    });
    return mk('G6', 'invalid_destination', '目的地合法', bad,
      sh.consignments.length + ' 张排货单目的地都合法');
  }

  function mk(code, api, name, bad, okDetail) {
    return {
      code: code,
      api_code: api,
      name: name,
      pass: bad.length === 0,
      // ★ 失败要点名到行，不许只说「校验失败」
      detail: bad.length ? bad.join('\n') : okDetail
    };
  }

  function confirmSheet(sheetId) {
    var sh = findSheet(sheetId);
    if (!sh) return { ok: false, error: 'sheet_not_found', detail: '找不到排货表 ' + sheetId, gates: [] };
    if (sh.state !== '草稿') {
      return { ok: false, error: 'sheet_not_draft', detail: '排货表 ' + sheetId + ' 已是「' + sh.state + '」', gates: [] };
    }
    var t0 = Date.now();
    var gates = [gateG1(sh), gateG2(sh), gateG3(sh), gateG4(sh), gateG5(sh), gateG6(sh)];
    var failed = gates.filter(function (g) { return !g.pass; });

    if (failed.length) {
      // 不过闸时不改状态。日志带上是哪几项、点名到行 —— 下次不用重新复现
      err('sheet.gate-failed', {
        sheet_id: sheetId, ms: Date.now() - t0,
        failed: failed.map(function (g) { return g.code + ' ' + g.api_code; }),
        detail: failed.map(function (g) { return g.code + ': ' + g.detail; })
      });
      log('确认排货表 ' + sheetId + ' 未过闸：' + failed.map(function (g) { return g.code + ' ' + g.name; }).join('、')
        + '（状态未改变，仍是草稿）');
      return { ok: false, error: failed[0].api_code, gates: gates };
    }

    if (sh.pool.length) {
      // 池子里还有没编排的行：这不是闸，但也不能静默带过
      log('注意：排货表 ' + sheetId + ' 待编排池里还剩 ' + sh.pool.length + ' 行未编入任何排货单，确认后它们仍占着「已排未发」。');
    }
    sh.state = '已确认';
    sh.consignments.forEach(function (c) { if (c.our_stage === '草稿') c.our_stage = '已确认'; });
    reevaluate('确认排货表');
    persist();
    log('🔒 确认排货表 ' + sheetId + '：6 项闸全过，' + sh.consignments.length + ' 张排货单转「已确认」，可以投送');
    dbg('sheet.confirm', { sheet_id: sheetId, ms: Date.now() - t0, consignments: sh.consignments.length });
    return { ok: true, gates: gates };
  }

  /* ---------- ★ 添加 / 删除货号与 msku（UC-O2 认领即占用）----------
     ★ 计划默认是空的。人从目录里挑 msku 加进来，格子才长出来。
     ★ 占用规则：一个 msku 同时只被**一个尚未下单的计划**占用。
     ★ 格子的系统建议 = 该 (店铺,货号) 下**已认领** msku 的销量之和
       —— 没认领的不算，所以删一个 msku，格子的数要跟着变小。
     ------------------------------------------------------------------ */

  function claimedElsewhere(planId, sellerSku, sid) {
    var hit = null;
    ensure().plans.forEach(function (p) {
      if (p.plan_id === planId) return;
      (p.claims || []).forEach(function (c) {
        if (c.seller_sku === sellerSku && String(c.sid) === String(sid)) hit = p;
      });
    });
    return hit;
  }

  /** ★ 可选目录 —— **搜索式**，不是「全列出来让人挑」。
   *  真实库里成百上千个货号，全量渲染从第一天就是错的。
   *  filter: {seller_id, category, keyword, limit, only}
   *    only: 'selectable' | 'claimed_here' | 'claimed_by_other' | undefined
   *  返回 {rows, matched, total, truncated, needQuery}
   *    ★ needQuery = true 表示「没给任何条件，故意不返回」——
   *      不是「没有数据」。两者在界面上必须长得不一样。
   */
  function catalog(planId, filter) {
    var s2 = ensure(), f = filter || {};
    var limit = f.limit || 50;
    var p = findPlan(planId);
    var mine = {};
    if (p) (p.claims || []).forEach(function (c) { mine[c.seller_sku + '|' + c.sid] = true; });

    var all = s2.catalog || [];
    var hasQuery = !!(f.seller_id || f.category || (f.keyword && String(f.keyword).trim()) || f.only);
    if (!hasQuery) {
      return { rows: [], matched: 0, total: all.length, truncated: false, needQuery: true };
    }

    var kw = f.keyword ? String(f.keyword).trim().toLowerCase() : '';
    var hit = all.filter(function (row) {
      if (f.seller_id && row.seller_id !== f.seller_id) return false;
      if (f.category && String(row.cat || '').indexOf(f.category) !== 0) return false;
      if (kw) {
        var hay = (row.sku + ' ' + row.seller_sku + ' ' + (row.name || '') + ' ' + (row.cat || '')).toLowerCase();
        if (hay.indexOf(kw) < 0) return false;
      }
      return true;
    }).map(function (row) {
      var other = claimedElsewhere(planId, row.seller_sku, row.sid);
      return {
        seller_id: row.seller_id, sku: row.sku, seller_sku: row.seller_sku, sid: row.sid,
        name: row.name, cat: row.cat, base: row.base, share: row.share,
        claimed_here: !!mine[row.seller_sku + '|' + row.sid],
        claimed_by: other ? other.plan_id : null,
        claimed_by_title: other ? other.title : null,
        selectable: !mine[row.seller_sku + '|' + row.sid] && !other
      };
    });

    if (f.only === 'selectable') hit = hit.filter(function (r) { return r.selectable; });
    if (f.only === 'claimed_here') hit = hit.filter(function (r) { return r.claimed_here; });
    if (f.only === 'claimed_by_other') hit = hit.filter(function (r) { return !!r.claimed_by; });

    return {
      rows: hit.slice(0, limit),
      matched: hit.length,
      total: all.length,
      truncated: hit.length > limit,        // ★ 命中太多 → 让人细化条件，而不是硬塞
      needQuery: false
    };
  }

  /** 品类选项（二级），给筛选下拉用 */
  function categories() {
    var seen = {}, out = [];
    (ensure().catalog || []).forEach(function (r) {
      if (r.cat && !seen[r.cat]) { seen[r.cat] = 1; out.push(r.cat); }
    });
    return out.sort();
  }

  /** 按该 (店铺,货号) 下**已认领**的 msku 重算格子；一个都不剩就删掉整行 */
  /* ★ 认领后重建两张格子表：
       cells_demand   msku × 月     期望销量（系统给建议）
       cells_purchase 货号 × 月     计划采购量（★ 不带店铺）
     ------------------------------------------------------------------ */
  function dKey(sellerId, sellerSku, period) { return sellerId + '|' + sellerSku + '|' + period; }
  function pKey(sku, period) { return sku + '|' + period; }

  /* ★★ 计划周期也是变量（负责人 2026-09-18）

       「默认未来 3 个月，但实际上也是变量，允许用户编辑，比如 6 个月、9 个月」

     ★ 它和提前期不同：提前期是**全局配置**，计划周期是**这张计划自己的属性**
       （每张计划的起点和长度本来就可以不一样）。
       所以全局那个 `plan_months` 只是**新建计划的默认值**，
       改某张计划要走 setPlanMonths，并且要重建它的格子。

     ★ 缩短周期会让已经填过的格子消失。
       按本项目的规矩：**迁不动的逐项点名，绝不静默清空**。 */
  function periodsFrom(start, months) {
    var out = [];
    for (var i = 0; i < months; i++) out.push(shiftMonth(start, i));
    return out;
  }

  function setPlanMonths(planId, months) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;

    var n = Number(months);
    if (!isFinite(n) || n < 1 || n > 24 || Math.round(n) !== n) {
      return { ok: false, error: 'bad_months',
        detail: '计划周期要是 1~24 之间的整数月，收到的是「' + months + '」' };
    }
    var before = p.periods.slice();
    var next = periodsFrom(p.period_start, n);
    var keep = {};
    next.forEach(function (m) { keep[m] = 1; });

    // ★ 先点名：哪些**人填过的**格子会随着缩短而消失
    var dropped = [];
    Object.keys(p.cells_demand || {}).forEach(function (k) {
      var c = p.cells_demand[k];
      if (keep[c.period]) return;
      if (c.touched && num(c.demand) !== null) {
        dropped.push('期望销量 ' + c.period + ' · ' + sellerName(c.seller_id) + ' / '
          + c.seller_sku + ' = ' + c.demand);
      }
    });
    Object.keys(p.cells_purchase || {}).forEach(function (k) {
      var c = p.cells_purchase[k];
      if (keep[c.period]) return;
      if (c.touched && num(c.purchase) !== null) {
        dropped.push('计划采购量 ' + c.period + ' · ' + c.sku + ' = ' + c.purchase);
      }
    });

    // 真正切掉周期外的格子
    Object.keys(p.cells_demand || {}).forEach(function (k) {
      if (!keep[p.cells_demand[k].period]) delete p.cells_demand[k];
    });
    Object.keys(p.cells_purchase || {}).forEach(function (k) {
      if (!keep[p.cells_purchase[k].period]) delete p.cells_purchase[k];
    });

    p.months = n;
    p.periods = next;

    // 新增的月份要长出格子
    var pairs = {};
    (p.claims || []).forEach(function (c) { pairs[c.seller_id + '|' + c.sku] = c; });
    Object.keys(pairs).forEach(function (k) {
      rebuildCells(p, pairs[k].seller_id, pairs[k].sku);
    });
    persist();

    log('⚙ 计划周期改了：' + before.length + ' 个月 → ' + n + ' 个月（'
      + next[0] + ' ~ ' + next[next.length - 1] + '）'
      + (dropped.length ? '；★ 有 ' + dropped.length + ' 个填过的格子落在周期外，已逐项列出'
                        : '；没有填过的数被丢掉。'));
    if (dropped.length) {
      // ★ 丢东西必须有声 —— 「少了几个数」在界面上是看不出来的
      err('plan.months-dropped-cells', { plan_id: planId,
        from: before.length, to: n, dropped: dropped });
    }
    return { ok: true, months: n, periods: next, dropped: dropped };
  }

  function rebuildCells(p, sellerId, sku) {
    p.cells_demand = p.cells_demand || {};
    p.cells_purchase = p.cells_purchase || {};
    var cat = ensure().catalog || [];
    var claimed = (p.claims || []).filter(function (c) {
      return c.seller_id === sellerId && c.sku === sku;
    });

    // ① msku × 月 的期望销量
    p.periods.forEach(function (period, i) {
      claimed.forEach(function (c) {
        var src = cat.filter(function (x) {
          return x.seller_sku === c.seller_sku && String(x.sid) === String(c.sid);
        })[0];
        /* ★ 系统销量预估只覆盖前几个月（剧本里 base 是 3 个数）。
           计划周期可以被拉到 6 / 9 个月，后面那些月没有预估。
           ★ 当 null 处理会让推演从第 4 个月起一律「未知」，整条曲线作废；
             直接沿用最后一个值又会把**外推**冒充成**预估**。
           所以：沿用最后一个值，但**标明它是外推的**，让界面能说出来。 */
        var arr = (src && src.base) || [];
        var b = null, extrapolated = false;
        if (arr.length) {
          if (i < arr.length) { b = arr[i]; }
          else { b = arr[arr.length - 1]; extrapolated = true; }
        }
        var sys = (b === null || b === undefined) ? null : Math.round(b * (src.share || 1));
        var k = dKey(sellerId, c.seller_sku, period);
        var old = p.cells_demand[k];
        p.cells_demand[k] = {
          seller_id: sellerId, sku: sku, seller_sku: c.seller_sku, sid: c.sid, period: period,
          system: sys,
          system_extrapolated: extrapolated,   // ★ 这个预估是外推出来的，不是真预估
          demand: (old && old.touched) ? old.demand : sys,
          touched: old ? !!old.touched : false
        };
      });
      // 已释放的 msku：删掉它的格子
      Object.keys(p.cells_demand).forEach(function (k) {
        var d = p.cells_demand[k];
        if (d.seller_id !== sellerId || d.sku !== sku || d.period !== period) return;
        if (!claimed.some(function (c) { return c.seller_sku === d.seller_sku; })) delete p.cells_demand[k];
      });
    });

    // ② 货号 × 月 的采购量：只要该货号在本计划里还有任一店铺被认领，就保留
    var skuStillClaimed = (p.claims || []).some(function (c) { return c.sku === sku; });
    p.periods.forEach(function (period) {
      var k = pKey(sku, period);
      if (!skuStillClaimed) { delete p.cells_purchase[k]; return; }
      if (!p.cells_purchase[k]) p.cells_purchase[k] = { sku: sku, period: period, purchase: null, touched: false };
    });
  }

  /** 批量添加。picks: [{seller_id, sku, seller_sku, sid}] */
  function addClaims(planId, picks) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;
    p.claims = p.claims || [];
    var list = picks || [], added = [], rejected = [];
    list.forEach(function (x, i) {
      var dup = p.claims.some(function (c) {
        return c.seller_sku === x.seller_sku && String(c.sid) === String(x.sid);
      });
      if (dup) { rejected.push('第 ' + (i + 1) + ' 项（' + x.seller_sku + '）：本计划已添加，跳过'); return; }
      var other = claimedElsewhere(planId, x.seller_sku, x.sid);
      if (other) {
        rejected.push('第 ' + (i + 1) + ' 项（' + x.seller_sku + ' · 店铺 ' + x.sid + '）：已被计划 '
          + other.plan_id + '（' + other.title + '）占用 —— 一个 msku 同时只能属于一个尚未下单的计划');
        return;
      }
      p.claims.push({ seller_sku: x.seller_sku, sid: x.sid, sku: x.sku, seller_id: x.seller_id });
      added.push(x);
    });
    var touched = {};
    added.forEach(function (x) { touched[x.seller_id + '|' + x.sku] = x; });
    Object.keys(touched).forEach(function (k) { rebuildCells(p, touched[k].seller_id, touched[k].sku); });
    persist();
    log('＋ 计划 ' + planId + ' 添加 ' + added.length + ' 个 msku'
      + (rejected.length ? '，拒绝 ' + rejected.length + ' 个（已点名）' : '')
      + '；当前期望销量格子 ' + Object.keys(p.cells_demand || {}).length
      + ' 个 / 采购格子 ' + Object.keys(p.cells_purchase || {}).length + ' 个。');
    if (rejected.length) err('claims.rejected', { planId: planId, rejected: rejected });
    return { ok: true, added: added.length, rejected: rejected };
  }

  /** 删掉整个货号在某店铺下的全部 msku */
  function removeSku(planId, sellerId, sku) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;
    var before = p.claims.length;
    p.claims = p.claims.filter(function (c) { return !(c.seller_id === sellerId && c.sku === sku); });
    rebuildCells(p, sellerId, sku);
    persist();
    log('－ 计划 ' + planId + ' 删除 ' + sellerName(sellerId) + ' 的货号 ' + sku
      + '：释放 ' + (before - p.claims.length) + ' 个 msku 认领。');
    return { ok: true, released: before - p.claims.length };
  }

  /** 删掉指定 msku。★ 删完该店铺该货号的期望销量格子会变少 */
  function removeMsku(planId, sellerId, sku, sellerSku) {
    var p = findPlan(planId);
    if (!p) return { ok: false, error: 'plan_not_found', detail: '找不到计划 ' + planId };
    var g = guardEditable(p); if (g) return g;
    var hit = p.claims.filter(function (c) {
      return c.seller_id === sellerId && c.sku === sku && c.seller_sku === sellerSku;
    })[0];
    if (!hit) {
      return { ok: false, error: 'claim_not_found',
        detail: '计划里没有这个认领：' + sellerSku + ' @ ' + sellerName(sellerId) + ' · ' + sku };
    }
    p.claims = p.claims.filter(function (c) { return c !== hit; });
    var left = p.claims.filter(function (c) { return c.seller_id === sellerId && c.sku === sku; }).length;
    var skuLeft = p.claims.filter(function (c) { return c.sku === sku; }).length;
    rebuildCells(p, sellerId, sku);
    persist();
    log('－ 计划 ' + planId + ' 删除 msku ' + sellerSku + '：该店铺该货号还剩 ' + left + ' 个'
      + (skuLeft ? '' : '；★ 该货号在本计划已无任何店铺，采购量格子一并移除'));
    return { ok: true, left: left, row_removed: left === 0, sku_removed: skuLeft === 0 };
  }

  function lockedByRev(p) {
    return (p.revs || []).length > 0;
  }

  /* ---------- ★ 逐月库存推演（格子里的第二行「库存预估」）----------
     期末 = 期初 + 计划采购量 − 销量预估，逐月滚动。
     ★ 它必须**当场显示在格子里** —— 只说「将触发重算」而不给结果，等于没算。

     ★ 聚合语义（docs/02 §2）：
       销量预估、计划采购量是**流量**，横向可求和；
       库存预估是**存量**，求和无意义 —— 合计列给的是**期末库存**，不是三个月相加。
     ------------------------------------------------------------------ */

  /* ★★ 提前期 —— ★ 不是常量，是**运营自己配的变量**（负责人 2026-09-18）

       「本身这就是一个预估过程」——写死一个数，等于把一个会变的事实
       固化成常量：换供应商、换航线、旺季、切国内环境，全都不适用。

     ★ 单位是**天**，不是月。月为单位表达不了「国内调拨 7 天」——
       而那恰恰是国内环境下最常见的那一档。
     ------------------------------------------------------------------ */

  var DEFAULT_PARAMS = {
    make_days: 30,      // 生产：下单 → 完工入本地仓
    ship_days: 60,      // 货运：本地仓发出 → 目的仓可售（海外海运）
    plan_months: 3      // ★ 新建计划的默认周期。改某张计划走 Store.setPlanMonths
  };

  /* ★ 预设只是**起点**，不是选项清单 —— 用户照样可以填任意天数 */
  var LEAD_PRESETS = [
    { key: 'sea',      label: '海外 · 海运', make_days: 30, ship_days: 60 },
    { key: 'air',      label: '海外 · 空运', make_days: 30, ship_days: 10 },
    { key: 'domestic', label: '国内调拨',    make_days: 30, ship_days: 3  }
  ];

  function params() {
    var st = ensure();
    if (!st.params) { st.params = { make_days: DEFAULT_PARAMS.make_days,
                                    ship_days: DEFAULT_PARAMS.ship_days }; }
    var ok1 = function (v, d) { var n = Number(v); return isFinite(n) && n >= 0 ? Math.round(n) : d; };
    var mk = ok1(st.params.make_days, DEFAULT_PARAMS.make_days);
    var sp = ok1(st.params.ship_days, DEFAULT_PARAMS.ship_days);
    var pm = ok1(st.params.plan_months, DEFAULT_PARAMS.plan_months);
    return {
      make_days: mk, ship_days: sp, total_days: mk + sp,
      plan_months: pm < 1 ? DEFAULT_PARAMS.plan_months : pm
    };
  }

  /** 改提前期。★ 立即生效、立即重算 —— 这就是「快速改变系统配置」的意思 */
  function setParams(next) {
    var st = ensure(), before = params();
    var mk = Number((next || {}).make_days), sp = Number((next || {}).ship_days);
    if (!isFinite(mk) || mk < 0 || mk > 720) {
      return { ok: false, error: 'bad_make_days',
        detail: '生产周期要是 0~720 之间的天数，收到的是「' + (next || {}).make_days + '」' };
    }
    if (!isFinite(sp) || sp < 0 || sp > 720) {
      return { ok: false, error: 'bad_ship_days',
        detail: '货运周期要是 0~720 之间的天数，收到的是「' + (next || {}).ship_days + '」' };
    }
    /* ★ plan_months 可不传 —— 不传就保持现值，不许被 Number(undefined)=NaN 打成默认。
       「没说要改」和「改成默认」是两回事。 */
    var pm = params().plan_months;
    if ((next || {}).plan_months !== undefined) {
      var v = Number(next.plan_months);
      if (!isFinite(v) || v < 1 || v > 24 || Math.round(v) !== v) {
        return { ok: false, error: 'bad_plan_months',
          detail: '默认计划周期要是 1~24 之间的整数月，收到的是「' + next.plan_months + '」' };
      }
      pm = Math.round(v);
    }
    st.params = { make_days: Math.round(mk), ship_days: Math.round(sp), plan_months: pm };
    persist();
    var after = params();
    log('⚙ 提前期改了：生产 ' + before.make_days + '→' + after.make_days + ' 天，'
      + '货运 ' + before.ship_days + '→' + after.ship_days + ' 天'
      + '（全程 ' + before.total_days + '→' + after.total_days + ' 天）'
      + '；★ 所有计划的库存预估已重算。');
    return { ok: true, params: after };
  }

  function shiftMonth(period, n) {
    var y = Number(period.slice(0, 4)), m = Number(period.slice(5, 7)) + n;
    y += Math.floor((m - 1) / 12); m = ((m - 1) % 12 + 12) % 12 + 1;
    return y + '-' + (m < 10 ? '0' + m : m);
  }

  /* ★ 从某月起算 n 天，落在哪个月。天算、月显示。

     ★★ 锚在**月中**（15 号），不是月初 —— 这不是凑数，是模型口径：
       计划只说「10 月下单」，没说 10 月几号。真实下单日在当月内大致均匀分布，
       **期望值就是月中**，而这整套本来就是期望值模型。

     ★ 锚在月初会得出「10 月下的单、生产 30 天、10 月就完工」—— 与直觉相反，
       而且系统性**乐观**：把每一笔单都当成 1 号下的。
     ★ 锚在月末则系统性悲观，还会把「国内调拨 3 天」一律推到下个月，
       而那恰恰是国内环境下最该看见的「当月就能到」。 */
  var MONTH_ANCHOR_DAY = 15;

  function monthAfterDays(period, days) {
    var d = new Date(Date.UTC(Number(period.slice(0, 4)), Number(period.slice(5, 7)) - 1,
                              MONTH_ANCHOR_DAY));
    d.setUTCDate(d.getUTCDate() + days);
    var m = d.getUTCMonth() + 1;
    return d.getUTCFullYear() + '-' + (m < 10 ? '0' + m : m);
  }

  function openingOf(sellerId, sku) {
    // 原型：期初 = 目的仓当前可售（真实系统取该 店铺×货号 的 FBA + 海外仓可售）
    var h = 0, t = sellerId + '|' + sku;
    for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) % 97;
    return 40 + h;   // 40 ~ 136
  }

  function inTransitOf(sellerId, sku) {
    // 原型：已在途、将于第 1 个月到达（真实系统取 A 台账）
    var h = 0, t = sku + '|' + sellerId;
    for (var i = 0; i < t.length; i++) h = (h * 17 + t.charCodeAt(i)) % 61;
    return h;        // 0 ~ 60
  }

  /* ★★ 逐月库存推演（docs/14 · 已由 forecast_demo.py 用负责人 Excel 的数字验证）

       ★ 主公式          期末 = 期初 − 期望销量 + 当期入库量
         加项是「当期入库量」，**不是「计划采购量」** ——
         把采购量直接加进库存，会得出「当月买当月就有」这种与现实相反的结论。

       ★ 管道      计划采购 →【生产中】→【本地仓】→【排货中】→【货运中】→ 入库 → 可售
                    人填       make_days    完工即发    —— ship_days ——
                    货号        货号         货号        ★ 店铺      ★ 店铺
                    └──── 无店铺归属 ────┘  └──── 已定向 ────┘
         ★ 那条分界线不是模型画的，是「排货」这个动作画的：
           排货就是给货指定店铺的那一刻，线以下的量物理上还不属于任何店。

       ★ 入库分两种，不许混成一个数：
           已确认  已发运、有预计到货日          = 事实
           按计划推算  由计划采购量经管道推出来   = 假设
         混成一行，运营就分不清「货真在路上」和「我假设它会到」。

       ★ 提前期按**天**配（make_days / ship_days），不是常量 ——
         换供应商、换航线、旺季、切国内环境全都不一样；
         而且月为单位表达不了「国内调拨 3 天」，那恰恰是国内最常见的一档。
     ------------------------------------------------------------------ */

  function marketOf(sellerId) {
    var x = (ensure().sellers || []).filter(function (t) { return t.seller_id === sellerId; })[0];
    return x ? x.market : null;
  }
  function hasFba(sellerId) {
    var x = (ensure().sellers || []).filter(function (t) { return t.seller_id === sellerId; })[0];
    return x ? !!x.has_fba : false;
  }

  /* ★★ 库存推演（docs/02 §3.1a / §3.1b）

       可售库存 = FBA 在仓（msku 级） + 海外仓在仓（★ 该市场共享池，单列不并入）
       ★ 本地仓不进可售 —— 生产后的临时仓，35 天到不了目的仓

       msku 层    期末[m] = 期末[m-1] − 期望销量[m]
       货号层     本地仓池子[m] += 计划采购量[m − 提前期]

     ★ 采购量**不进** msku 层的期末公式 —— 它到的是本地仓，还没定向到任何店铺，
       要经过**排货**才变成某店铺的可售库存（C4：不自动分摊）。
     ------------------------------------------------------------------ */

  function forecastSku(planId, sku) {
    var p = findPlan(planId);
    if (!p) return null;
    var st = ensure(), PA = params(), periods = p.periods;
    var inWin = function (m) { return periods.indexOf(m) >= 0; };

    // 本计划里这个货号涉及哪些 (店铺, msku)
    var rows = (p.claims || []).filter(function (c) { return c.sku === sku; });
    var sellers = [];
    rows.forEach(function (c) { if (sellers.indexOf(c.seller_id) < 0) sellers.push(c.seller_id); });

    var demandOf = function (c, period) {
      var d = (p.cells_demand || {})[dKey(c.seller_id, c.seller_sku, period)] || {};
      return d.demand !== undefined ? d.demand : null;
    };

    /* ───── 第一遍 · 基线：★ 只算已确认的量，完全不动未定向池 ─────
       基线回答的是「什么都不补，会欠多少」—— 这个数本身就是运营要看的。 */
    var base = rows.map(function (c) {
      var fba = hasFba(c.seller_id) ? (st.stock_fba[c.seller_sku + '|' + c.seller_id] || 0) : null;
      var cur = fba === null ? 0 : fba;
      var transit = inTransitOf(c.seller_id, sku);   // ★ 已在途：事实，第 1 个月到
      var unknown = false, gaps = {}, conf = {};
      conf[periods[0]] = transit;
      periods.forEach(function (period) {
        var demand = demandOf(c, period);
        if (demand === null) unknown = true;
        if (unknown) { gaps[period] = null; return; }
        cur = cur - demand + (conf[period] || 0);
        gaps[period] = cur < 0 ? -cur : 0;           // ★ 累计缺口
      });
      return { c: c, fba: fba, transit: transit, conf: conf, gaps: gaps };
    });

    /* ───── 管道 · 批次化 ─────
       ★ 一笔采购 = 一个批次，走 下单 → 生产 make_days → 发出 → 货运 ship_days → 可售。
       ★ 全程按**天**算，只在显示时落到月 —— 月为单位表达不了「国内调拨 3 天」。 */
    var batches = [], purchaseTotal = 0, beyond = [];

    // 本地仓现有库存：★ 已经生产完了，只差运，所以只走 ship_days
    var localNow = 0;
    (st.warehouses || []).forEach(function (w) {
      var k = sku + '|' + w.wid;
      localNow += (st.stock[k] ? st.stock[k].valid : 0) || 0;
    });
    if (localNow > 0) {
      batches.push({ order_month: null, units: localNow, made_month: periods[0],
                     arrive_month: monthAfterDays(periods[0], PA.ship_days), from_stock: true });
    }

    periods.forEach(function (om) {
      var c = (p.cells_purchase || {})[pKey(sku, om)] || {};
      var u = num(c.purchase);
      if (u === null || u === 0) return;
      purchaseTotal += u;
      var made = monthAfterDays(om, PA.make_days);
      var arr = monthAfterDays(om, PA.total_days);
      batches.push({ order_month: om, units: u, made_month: made, arrive_month: arr });
      // ★ 到货落在计划周期之外的，必须显式列出，不许悄悄丢掉
      if (!inWin(arr)) beyond.push({ order_month: om, arrive_month: arr, units: u,
                                     made_month: made });
    });

    /* ───── 第二遍 · 分配：未定向池 → 各 msku ─────
       ★ 两个独立的决定，不许混：
         「发多少」—— 完工即发（推式）。货不会因为暂时不缺就蒸发。
         「给哪个」—— 按缺口比例；缺口吃不下时，余量按期望销量摊。
       ★ 这是**模拟**不是排货：不写任何单据，假设可见可改（docs/14 §4）。 */
    var allocated = {}, arrivePlanned = {}, traces = [];
    rows.forEach(function (c) { allocated[c.seller_sku + '|' + c.seller_id] = 0; });

    batches.slice().sort(function (x, y) {
      return x.arrive_month < y.arrive_month ? -1 : x.arrive_month > y.arrive_month ? 1 : 0;
    }).forEach(function (bt) {
      if (!inWin(bt.arrive_month)) return;          // 周期外，已记进 beyond
      var am = bt.arrive_month;
      var need = {}, total = 0;
      base.forEach(function (r) {
        var k = r.c.seller_sku + '|' + r.c.seller_id;
        var g = r.gaps[am];
        need[k] = g === null ? 0 : Math.max(0, g - allocated[k]);
        total += need[k];
      });
      var wt = {}, tw = 0;                          // 按期望销量的权重（兜底与分余量用）
      base.forEach(function (r) {
        var k = r.c.seller_sku + '|' + r.c.seller_id;
        wt[k] = Math.max(0, demandOf(r.c, am) || 0); tw += wt[k];
      });
      if (tw === 0) { base.forEach(function (r) {
        wt[r.c.seller_sku + '|' + r.c.seller_id] = 1; }); tw = base.length; }

      var give = {}, basis = {};
      if (total === 0) {
        // ★ 谁都不缺，但货照发 —— 全部按期望销量摊
        spread(bt.units, wt, tw, give);
        base.forEach(function (r) {
          var k = r.c.seller_sku + '|' + r.c.seller_id;
          basis[k] = { rule: '无人缺货，按期望销量比例 ' + wt[k] + '/' + tw,
                       gap_part: 0, weight_part: give[k], pool: bt.units };
        });
      } else if (total <= bt.units) {
        // ★ 先按缺口补齐，余量再按期望销量摊 —— 合计必须仍等于这批货
        var rest = bt.units - total, extra = {};
        if (rest > 0) spread(rest, wt, tw, extra);
        base.forEach(function (r) {
          var k = r.c.seller_sku + '|' + r.c.seller_id;
          give[k] = need[k] + (extra[k] || 0);
          basis[k] = { rule: rest > 0
              ? '缺口 ' + need[k] + ' 补齐，余量按期望销量再摊 ' + (extra[k] || 0)
              : '按缺口补齐 ' + need[k],
            gap_part: need[k], weight_part: extra[k] || 0, pool: bt.units };
        });
      } else {
        // ★ 不够分：按缺口比例，取整，余数给缺口最大的那个
        var sum = 0, keys = [];
        base.forEach(function (r) {
          var k = r.c.seller_sku + '|' + r.c.seller_id;
          give[k] = Math.floor(bt.units * need[k] / total); sum += give[k]; keys.push(k);
        });
        keys.sort(function (x, y) { return need[y] - need[x]; })
            .slice(0, bt.units - sum).forEach(function (k) { give[k] += 1; });
        base.forEach(function (r) {
          var k = r.c.seller_sku + '|' + r.c.seller_id;
          basis[k] = { rule: '不够分，按缺口比例 ' + need[k] + '/' + total + ' × ' + bt.units,
                       gap_part: give[k], weight_part: 0, pool: bt.units };
        });
      }

      var check = 0;
      Object.keys(give).forEach(function (k) { check += give[k]; });
      if (check !== bt.units) {
        // ★ 分配合计必须等于这批货 —— 对不上就是管道漏货，宁可报错不许静默
        err('forecast.alloc_mismatch', { sku: sku, arrive: am, units: bt.units, given: check });
      }
      base.forEach(function (r) {
        var k = r.c.seller_sku + '|' + r.c.seller_id;
        if (!give[k]) return;
        allocated[k] += give[k];
        arrivePlanned[k + '|' + am] = (arrivePlanned[k + '|' + am] || 0) + give[k];
        traces.push({ arrive_month: am, seller_id: r.c.seller_id, seller_sku: r.c.seller_sku,
                      units: give[k], order_month: bt.order_month,
                      from_stock: !!bt.from_stock, basis: basis[k] });
      });
    });

    function spread(amount, w, tw2, out) {
      var sum = 0, keys = Object.keys(w);
      keys.forEach(function (k) { out[k] = Math.floor(amount * w[k] / tw2); sum += out[k]; });
      keys.sort(function (x, y) { return w[y] - w[x]; })
          .slice(0, amount - sum).forEach(function (k) { out[k] += 1; });
    }

    /* ───── 可售侧：★ 期末 = 期初 − 期望销量 + 当期入库量 ─────
       ★ 加项是「当期入库量」，不是「计划采购量」——
         把采购量直接加进库存，会得出「当月买当月就有」这种与现实相反的结论。 */
    var mskus = base.map(function (r) {
      var c = r.c, k = c.seller_sku + '|' + c.seller_id;
      var cur = r.fba === null ? 0 : r.fba, unknown = false;
      var months = periods.map(function (period) {
        var demand = demandOf(c, period);
        if (demand === null) unknown = true;
        var ic = r.conf[period] || 0;                     // ★ 事实
        var ip = arrivePlanned[k + '|' + period] || 0;    // ★ 假设
        var open = cur;
        var close = unknown ? null : open - demand + ic + ip;
        cur = close;
        var d = (p.cells_demand || {})[dKey(c.seller_id, c.seller_sku, period)] || {};
        return {
          period: period, system: d.system === undefined ? null : d.system,
          /* ★ 出处必须跟着数走：`system` 是从格子派生的，它是不是外推的
             也得一并带出来。让页面回头去读原格子，等于同一个数有两个来源。 */
          system_extrapolated: !!d.system_extrapolated,
          demand: demand, opening: open,
          inbound_confirmed: ic, inbound_planned: ip, inbound: ic + ip,
          closing: close,
          shortage: close !== null && close < 0, gap: (close !== null && close < 0) ? -close : 0,
          baseline_gap: r.gaps[period],                   // ★ 不补货会欠多少
          unknown: unknown
        };
      });
      return {
        seller_id: c.seller_id, seller_sku: c.seller_sku, sid: c.sid,
        market: marketOf(c.seller_id), has_fba: hasFba(c.seller_id),
        fba_onhand: r.fba,                    // ★ null = 该平台无 FBA，不是 0
        in_transit: r.transit, months: months,
        demand_total: months.reduce(function (t, m) { return t + (m.demand || 0); }, 0),
        inbound_total: months.reduce(function (t, m) { return t + m.inbound; }, 0),
        gap_total: months.reduce(function (t, m) { return t + m.gap; }, 0),
        stockout_month: (months.filter(function (m) { return m.shortage; })[0] || {}).period || null
      };
    });

    // 各市场的海外仓共享池（★ 单列，不并入任何单店）
    var markets = [];
    sellers.forEach(function (sid) {
      var mk = marketOf(sid); if (mk && markets.indexOf(mk) < 0) markets.push(mk);
    });
    var shared = markets.map(function (mk) {
      return { market: mk, oversea_onhand: st.stock_os[sku + '|' + mk] || 0,
               served_sellers: sellers.filter(function (sid) { return marketOf(sid) === mk; }) };
    });

    /* ───── 管道四段的逐月余额 ─────
       ★ 左两段（生产中 / 本地仓）**无店铺归属**，右两段有 ——
         分界线是「排货」这个动作，不是模型画的。 */
    var pipeline = periods.map(function (period) {
      var making = 0, local = 0, shipping = 0, arrive = 0, made = 0;
      batches.forEach(function (bt) {
        if (bt.order_month && bt.order_month <= period && period < bt.made_month) making += bt.units;
        if (bt.made_month === period) made += bt.units;
        if (bt.made_month <= period && period < bt.arrive_month) shipping += bt.units;
        if (bt.arrive_month === period) arrive += bt.units;
      });
      // ★ 完工即发：本地仓当月进当月出，结余 0（M-6 的乐观假设，已标明）
      var cell = (p.cells_purchase || {})[pKey(sku, period)] || {};
      return {
        period: period,
        purchase: cell.purchase === undefined ? null : cell.purchase,
        made_at: cell.purchase ? monthAfterDays(period, PA.make_days) : null,
        arrives_at: cell.purchase ? monthAfterDays(period, PA.total_days) : null,
        making: making, local_in: made, local_out: made, local: local,
        shipping: shipping, arrive: arrive,
        in_pipeline: making + local + shipping
      };
    });

    var confTotal = 0, planTotal = 0;
    mskus.forEach(function (m) {
      m.months.forEach(function (x) { confTotal += x.inbound_confirmed; planTotal += x.inbound_planned; });
    });

    return {
      sku: sku, periods: periods,
      params: PA,                                  // ★ 结论必须回显它的依据
      mskus: mskus, shared: shared, pipeline: pipeline,
      pool_months: pipeline,                       // 兼容旧字段名
      traces: traces, beyond: beyond,
      local_now: localNow, purchase_total: purchaseTotal,
      inbound_confirmed_total: confTotal, inbound_planned_total: planTotal,
      demand_total: mskus.reduce(function (t, m) { return t + m.demand_total; }, 0),
      gap_total: mskus.reduce(function (t, m) { return t + m.gap_total; }, 0),
      any_shortage: mskus.some(function (m) { return !!m.stockout_month; })
    };
  }

  /** 某个 msku 某月的推演行 */
  function cellForecast(planId, sellerId, sku, period, sellerSku) {
    var r = forecastSku(planId, sku);
    if (!r) return null;
    var m = r.mskus.filter(function (x) {
      return x.seller_id === sellerId && (!sellerSku || x.seller_sku === sellerSku);
    })[0];
    if (!m) return null;
    return m.months.filter(function (x) { return x.period === period; })[0] || null;
  }

  /* ---------- 投送：两条链并排（docs/06 §3.3） ----------
     ★ 海外仓没有「STA 建货件」这一步 —— 这是两条链的核心差异，
       不许为了「统一步数」硬加一个空步骤。
     ------------------------------------------------------- */

  function dispatch(cid) {
    var s = ensure();
    var hit = findConsignment(cid);
    if (!hit) return { ok: false, error: 'consignment_not_found', detail: '找不到排货单 #' + cid, steps: [] };
    var sh = hit.sheet, cons = hit.cons;

    // 🔒 绕过确认闸直接投送 = 把「搞错了不会直接发出」这道墙拆了
    if (sh.state !== '已确认') {
      return { ok: false, error: 'sheet_not_confirmed', detail: '所属排货表 ' + sh.sheet_id + ' 还是「' + sh.state + '」，必须先过确认闸', steps: [], our_stage: cons.our_stage };
    }
    if (cons.our_stage === '已投送' || cons.our_stage === '已归档') {
      return { ok: false, error: 'already_dispatched', detail: '排货单 #' + cid + ' 已是「' + cons.our_stage + '」，不要重投（已产生的单据：' + cons.ext_refs.map(function (r) { return r.doc_no; }).join('、') + '）', steps: [], our_stage: cons.our_stage };
    }
    if (!cons.lines.length) {
      return { ok: false, error: 'empty_consignment', detail: '排货单 #' + cid + ' 一行都没有', steps: [], our_stage: cons.our_stage };
    }

    var t0 = Date.now();
    var units = sum(cons.lines, function (l) { return l.units; });
    var steps = [];
    s.seq.doc++;
    var seq = pad(s.seq.doc);

    // ★ 故意留一个「部分成功」：cid 为偶数的 FBA 单，**首次**投送时最后一步超时。
    //   只报「失败」，人会以为什么都没发生，然后重投一次 —— 那才是真正的损失。
    //   ★ 它必须只发生一次：永久失败会让人无限重投，而每次重投都在制造重复单据。
    cons.attempts = (cons.attempts || 0) + 1;
    var partial = (cons.channel === 'fba' && Number(cid) % 2 === 0 && cons.attempts === 1);

    var plan = cons.channel === 'oversea'
      ? [
        { stage: '建备货单', kind: 'inbound_order', doc: 'OWS' + compact(s.day) + seq },
        { stage: '锁库存', kind: '', doc: '' },
        { stage: '发货（扣减）', kind: '', doc: '' }
      ]
      : [
        { stage: '建发货计划', kind: 'shipment_plan', doc: 'RP' + compact(s.day) + seq },
        { stage: '锁库存', kind: '', doc: '' },
        { stage: 'STA建货件', kind: 'shipment', doc: 'FBA16X' + digestOf(String(cid) + s.day).slice(0, 5).toUpperCase() },
        { stage: '建发货单', kind: 'ship_order', doc: 'SP' + compact(s.day) + seq },
        { stage: '发货（扣减）', kind: '', doc: '' }
      ];

    /* ★★ 断点续跑：已经拿到单号的步骤**不许重跑**。
       「重投会重复建单」不是领星的毛病，是从头重跑造成的 ——
       真实系统里这一条是硬要求：投送不是一个事务，
       每一步成功就落 ext_ref，重投必须从断点接上。 */
    var doneKinds = {};
    (cons.ext_refs || []).forEach(function (r) { doneKinds[r.kind] = r.doc_no; });

    for (var i = 0; i < plan.length; i++) {
      var st = plan[i];
      var isLast = (i === plan.length - 1);

      if (st.kind && doneKinds[st.kind]) {
        steps.push({ stage: st.stage, status: 'ok', doc_no: doneKinds[st.kind], reason: '★ 上次已完成，本次跳过（不重复建单）' });
        continue;
      }

      if (isLast && partial) {
        steps.push({ stage: st.stage, status: 'manual', doc_no: '', reason: '超时，需人工核实' });
        // ★ 前几步是真的发生了：ext_refs 照样落下，our_stage 停在「已确认」
        err('dispatch.partial', {
          cid: cid, channel: cons.channel, stage: st.stage, ms: Date.now() - t0,
          done_refs: cons.ext_refs.map(function (r) { return r.kind + ':' + r.doc_no; }),
          hint: '前几步产生的单据真实存在，重投会重复建单 —— 必须先按单号去领星核实'
        });
        break;
      }

      if (st.stage === '锁库存') lockStock(cons, '+');
      if (isLast) shipStock(cons);
      if (st.kind) cons.ext_refs.push({ kind: st.kind, doc_no: st.doc, day: s.day });

      /* ★★ 「取得货件号」这一步的产出要**回填到行上** —— 不回填的话，
         号只存在于 ext_refs 里，而「这一行发的是哪个货件」永远没人知道。
         ★ 这就是 A-6「发货前必须已归属货件」的落点：归属发生在这里，
           不在确认闸（那时号还不存在），也不靠人手填。 */
      if (st.kind === 'shipment' && st.doc) {
        cons.lines.forEach(function (l) { l.shipment_no = st.doc; });
        log('排货单 #' + cid + '：亚马逊返回货件 ' + st.doc
          + '，已回填到 ' + cons.lines.length + ' 行（★ 归属由系统做，不用人填）');
      }

      /* ★ A-6 的真正检查点：发货之前，每一行都必须已经有货件号。
         ★ 放在这里才检查得到 —— 前面那一步刚把号取回来。 */
      if (st.stage === '发货（扣减）' && cons.channel === 'fba') {
        var missing = cons.lines.filter(function (l) { return !l.shipment_no; });
        if (missing.length) {
          err('dispatch.unassigned-before-send', {
            cid: cid, missing: missing.length,
            why: '取得货件号那一步没把号回填到行上 —— 发货会把货发到不知道哪个货件里'
          });
          steps.push({ stage: st.stage, status: 'fail', doc_no: '',
            reason: '★ 还有 ' + missing.length + ' 行没有货件号，不能发货（A-6）' });
          persist();
          return { ok: false, error: 'unassigned_before_send', steps: steps,
            detail: '有 ' + missing.length + ' 行还没归属货件 —— 不发。'
                  + '★ 货件号本该在上一步由亚马逊返回并回填，请查「取得货件号」那一步。' };
        }
      }
      steps.push({ stage: st.stage, status: 'ok', doc_no: st.doc || '', reason: '' });
    }

    if (partial) {
      cons.our_stage = '已确认';
      persist();
      log('⚠ 投送排货单 #' + cid + ' 部分成功：' + steps.filter(function (x) { return x.status === 'ok'; }).length + ' / ' + plan.length
        + ' 步完成，最后一步超时需人工核实。已产生单据：' + cons.ext_refs.map(function (r) { return r.doc_no; }).join('、')
        + '。我方进度停在「已确认」，库存已锁未扣。');
    } else {
      cons.our_stage = '已投送';
      cons.lingxing_stage = LX_STAGES[0];
      reevaluate('投送');
      persist();
      log('⛔ 投送排货单 #' + cid + '（' + (cons.channel === 'fba' ? 'FBA' : '海外仓') + ' · ' + units + ' 件）成功：'
        + steps.map(function (x) { return x.stage + (x.doc_no ? ' ' + x.doc_no : ''); }).join(' → ')
        + '。领星进度 = 待配货。');
      dbg('dispatch.ok', { cid: cid, channel: cons.channel, units: units, ms: Date.now() - t0, refs: cons.ext_refs.length });
    }

    return { ok: !partial, steps: steps, our_stage: cons.our_stage };
  }

  /* ---------- ★ 人工核实：超时之后唯一的出路 ----------
     「超时转人工」只说了一半 —— 人去领星查完，得能把结论告诉系统。
     没有这个动作，超时就是死胡同：过不去，又只能重投。
     verdict: 'sent'（领星那边其实发出去了）| 'not_sent'（确实没发）
     ------------------------------------------------------- */
  function resolveManual(cid, verdict, actor) {
    var s = ensure();
    var hit = findConsignment(cid);
    if (!hit) return { ok: false, error: 'consignment_not_found', detail: '找不到排货单 #' + cid };
    var cons = hit.cons;
    if (cons.our_stage !== '已确认' || !(cons.ext_refs || []).length) {
      return { ok: false, error: 'nothing_to_resolve',
        detail: '排货单 #' + cid + ' 当前「' + cons.our_stage + '」、已产生单据 '
          + (cons.ext_refs || []).length + ' 张 —— 没有待核实的超时' };
    }
    if (verdict !== 'sent' && verdict !== 'not_sent') {
      return { ok: false, error: 'bad_verdict', detail: 'verdict 只能是 sent 或 not_sent，收到：' + verdict };
    }
    var nos = (cons.ext_refs || []).map(function (r) { return r.doc_no; }).join('、');
    if (verdict === 'sent') {
      shipStock(cons);
      cons.our_stage = '已投送';
      cons.lingxing_stage = LX_STAGES[0];
      cons.manual_resolved = { verdict: 'sent', day: s.day, actor: actor || '人工' };
      reevaluate('人工核实：已发');
      persist();
      log('✅ 人工核实排货单 #' + cid + '：领星侧确认**已发出**（' + nos + '）。我方进度推进到「已投送」，库存已扣。');
      return { ok: true, our_stage: cons.our_stage, verdict: 'sent' };
    }
    cons.manual_resolved = { verdict: 'not_sent', day: s.day, actor: actor || '人工' };
    persist();
    log('↩ 人工核实排货单 #' + cid + '：领星侧确认**未发出**（已建单据 ' + nos
      + ' 仍在）。可以重投 —— ★ 已拿到单号的步骤会跳过，只补发货那一步。');
    return { ok: true, our_stage: cons.our_stage, verdict: 'not_sent',
      hint: '重投时前几步会跳过，不会重复建单' };
  }

  function lockStock(cons, sign) {
    var s = ensure();
    cons.lines.forEach(function (l) {
      var k = stockKey(l.sku, l.from_wid);
      var st = s.stock[k] || (s.stock[k] = { valid: 0, locked: 0 });
      st.locked += (sign === '+' ? l.units : -l.units);
    });
  }

  function shipStock(cons) {
    var s = ensure();
    cons.lines.forEach(function (l) {
      var k = stockKey(l.sku, l.from_wid);
      var st = s.stock[k] || (s.stock[k] = { valid: 0, locked: 0 });
      st.valid -= l.units;
      st.locked -= l.units;
      if (st.valid < 0 || st.locked < 0) {
        // 扣成负数说明闸放行了不该放的量 —— 这种事要看得见，不许静默钳成 0
        err('stock.negative', { key: k, valid: st.valid, locked: st.locked, cid: cons.cid, hint: 'G3 可能被绕过了' });
      }
    });
  }

  /* ============================================================
     时间推进 —— ★ 到货与领星回执都是「事实」，由时间带来，不由人点
     ============================================================ */

  function tick() {
    var s = ensure();
    s.day = addDays(s.day, 1);
    s.tick_no++;

    var arrivedUnits = 0, arrivedDocs = 0, advanced = [];

    // 2) 采购单到货：下单后第 2 天到约 60%，第 5 天补齐
    s.pos.forEach(function (po) {
      if (po.state !== '已下单' || !po.placed_day) return;
      var age = daysBetween(po.placed_day, s.day);
      if (age === 2 && po.receipts.length === 0) {
        s.seq.doc++;
        var no = 'RK' + compact(s.day) + pad(s.seq.doc);
        po.items.forEach(function (it) {
          var part = Math.ceil(it.units * 0.6);
          po.receipts.push({ doc_no: no, sku: it.sku, units: part, wid: po.wid, day: s.day });
          bumpStock(it.sku, po.wid, part);
          arrivedUnits += part;
        });
        po.snapshot.status_shipped = 2; // 部分到货
        arrivedDocs++;
        log('📥 入库单 ' + no + '：采购单 ' + po.po_id + ' 部分到货 → ' + warehouseName(po.wid) + '（status_shipped=2）');
      } else if (age === 5 && po.snapshot.status_shipped === 2) {
        s.seq.doc++;
        var no2 = 'RK' + compact(s.day) + pad(s.seq.doc);
        po.items.forEach(function (it) {
          var got = sum(po.receipts, function (r) { return r.sku === it.sku ? r.units : 0; });
          var rest = it.units - got;
          if (rest <= 0) return;
          po.receipts.push({ doc_no: no2, sku: it.sku, units: rest, wid: po.wid, day: s.day });
          bumpStock(it.sku, po.wid, rest);
          arrivedUnits += rest;
        });
        po.snapshot.status_shipped = 3; // 全部到货
        po.snapshot.status = '9完成';
        arrivedDocs++;
        log('📥 入库单 ' + no2 + '：采购单 ' + po.po_id + ' 到货补齐 → ' + warehouseName(po.wid) + '（status_shipped=3）');
      }
    });

    // 3) 已投送的排货单：领星进度沿 待配货→待发货→待收货→已完成 前进一档
    s.sheets.forEach(function (sh) {
      sh.consignments.forEach(function (c) {
        if (c.our_stage !== '已投送') return;
        var i = LX_STAGES.indexOf(c.lingxing_stage);
        if (i < 0) {
          err('lingxing.stage-unknown', { cid: c.cid, stage: c.lingxing_stage, known: LX_STAGES, fallback: '按待配货处理' });
          i = 0;
        }
        if (i >= LX_STAGES.length - 1) return;
        c.lingxing_stage = LX_STAGES[i + 1];
        advanced.push('#' + c.cid + '→' + c.lingxing_stage);
        if (c.lingxing_stage === '已完成') c.our_stage = '已归档';
      });
    });

    // 4) 跑一遍推进判据，循环到不动点
    var moved = reevaluate('每日回执');

    // 5) 留痕
    var text = '⏩ 第 ' + s.tick_no + ' 天（' + s.day + '）：到货 ' + arrivedUnits + ' 件 / ' + arrivedDocs + ' 张入库单；'
      + advanced.length + ' 张排货单进度推进' + (advanced.length ? '（' + advanced.join('、') + '）' : '')
      + '；' + moved + ' 条记录状态变化。';
    log(text);
    dbg('tick', { day: s.day, tick_no: s.tick_no, arrivedUnits: arrivedUnits, arrivedDocs: arrivedDocs, advanced: advanced, moved: moved });
    return { ok: true, day: s.day, arrivedUnits: arrivedUnits, arrivedDocs: arrivedDocs, advanced: advanced, moved: moved, text: text };
  }

  function bumpStock(sku, wid, units) {
    var s = ensure();
    var k = stockKey(sku, wid);
    var st = s.stock[k] || (s.stock[k] = { valid: 0, locked: 0 });
    st.valid += units;
  }

  /* ============================================================
     追溯
     ============================================================ */

  function trace(lineId) {
    var l = findLine(lineId);
    if (!l) return { sources: [], dispatches: [], events: [] };
    var s = ensure();
    var sources = [];
    s.pos.forEach(function (po) {
      po.items.forEach(function (it) {
        if (it.line_id !== lineId) return;
        sources.push({
          po_id: po.po_id, supplier: po.supplier, wid: po.wid, state: po.state,
          order_sn: po.order_sn, custom_order_sn: po.custom_order_sn,
          units: it.units, price: it.price,
          status: po.snapshot.status, status_shipped: po.snapshot.status_shipped,
          receipts: po.receipts.filter(function (r) { return r.sku === it.sku; })
        });
      });
    });
    var dispatches = [];
    s.sheets.forEach(function (sh) {
      sh.consignments.forEach(function (c) {
        c.lines.forEach(function (cl, i) {
          if (cl.line_id !== lineId) return;
          dispatches.push({
            sheet_id: sh.sheet_id, sheet_state: sh.state, cid: c.cid, row: i + 1,
            channel: c.channel, dest: c.dest, our_stage: c.our_stage, lingxing_stage: c.lingxing_stage,
            from_wid: cl.from_wid, units: cl.units, shipment_no: cl.shipment_no, box: cl.box,
            ext_refs: c.ext_refs
          });
        });
      });
      (sh.pool || []).forEach(function (p) {
        if (p.line_id !== lineId) return;
        dispatches.push({
          sheet_id: sh.sheet_id, sheet_state: sh.state, cid: null, row: null,
          channel: '', dest: {}, our_stage: '待编排', lingxing_stage: '',
          from_wid: p.from_wid, units: p.units, shipment_no: '', box: null, ext_refs: []
        });
      });
    });
    return { line: l, qty: lineQty(lineId), sources: sources, dispatches: dispatches, events: l.events.slice() };
  }

  /* ============================================================
     待办
     ============================================================ */

  function todos(role) {
    var s = ensure();
    role = role || s.role;
    var out = [];

    if (role === 'ops') {
      s.plans.forEach(function (p) {
        var cur = p.revs.filter(function (r) { return r.is_current; })[0];
        var empty = Object.keys(p.cells_purchase || {}).filter(function (k) { return num(p.cells_purchase[k].purchase) === null; }).length;
        if (!cur) {
          out.push({
            title: '填写并提交「' + p.title + '」',
            hint: Object.keys(p.cells_purchase || {}).length + ' 个采购格子，其中 ' + empty + ' 个还空着（提交时会被跳过并列出来）',
            href: 'plan.html?plan=' + p.plan_id, urgent: true
          });
        } else {
          out.push({
            title: '跟进「' + p.title + '」rev' + cur.rev + ' 的进度',
            hint: cur.line_ids.length + ' 条记录在跑' + (empty ? '；仍有 ' + empty + ' 个空格子未纳入' : ''),
            href: 'ops.html?plan=' + p.plan_id, urgent: false
          });
        }
      });
      var stuck = s.lines.filter(function (l) { return l.state === S.SUBMITTED; }).length;
      if (stuck) out.push({ title: stuck + ' 条记录还停在「已提交」', hint: '采购尚未组单承接，或只组了一部分（木桶未满）', href: 'ops.html', urgent: false });
    }

    if (role === 'buyer') {
      var need = s.lines.filter(function (l) {
        return (l.state === S.SUBMITTED || l.state === S.CONFIRMED) && lineQty(l.line_id).confirmed < l.total;
      });
      if (need.length) out.push({ title: need.length + ' 条需求待组采购单', hint: '合计还差 ' + sum(need, function (l) { return l.total - lineQty(l.line_id).confirmed; }) + ' 件未承接', href: 'buyer.html', urgent: true });
      var drafts = s.pos.filter(function (p) { return p.state === '草稿'; });
      if (drafts.length) out.push({ title: drafts.length + ' 张采购单待下单', hint: '下单不可撤销：' + drafts.map(function (p) { return p.po_id; }).join('、'), href: 'po.html?po=' + drafts[0].po_id, urgent: true });
      var waiting = s.pos.filter(function (p) { return p.state === '已下单' && p.snapshot.status_shipped < 3; });
      if (waiting.length) out.push({ title: waiting.length + ' 张采购单在等到货', hint: '到货由每日采集回执推进 —— 点「⏩ 推进一天」', href: 'buyer.html', urgent: false });
    }

    if (role === 'dispatcher') {
      var cand = candidates();
      if (cand.length) {
        var withStock = cand.filter(function (c) { return c.available > 0; });
        out.push({
          title: cand.length + ' 条记录可排货',
          hint: withStock.length + ' 条本地仓已有货（合计可发 ' + sum(withStock, function (c) { return c.available; }) + ' 件）；其余还在等到货',
          href: 'dispatcher.html', urgent: withStock.length > 0
        });
      }
      s.sheets.forEach(function (sh) {
        if (sh.state === '草稿') {
          out.push({ title: '排货表 ' + sh.sheet_id + ' 待确认', hint: sh.consignments.length + ' 张排货单 / 待编排池 ' + sh.pool.length + ' 行；确认要过 6 项闸', href: 'sheet.html?id=' + sh.sheet_id, urgent: false });
        }
        sh.consignments.forEach(function (c) {
          if (sh.state !== '已确认') return;
          if (c.our_stage === '已确认' && c.ext_refs.length) {
            out.push({ title: '⚠ 排货单 #' + c.cid + ' 需人工核实', hint: '上次投送最后一步超时；已产生 ' + c.ext_refs.map(function (r) { return r.doc_no; }).join('、') + '，重投会重复建单', href: 'sheet.html?id=' + sh.sheet_id + '&cid=' + c.cid, urgent: true });
          } else if (c.our_stage === '已确认') {
            out.push({ title: '排货单 #' + c.cid + ' 待投送', hint: (c.channel === 'fba' ? 'FBA 5 步' : '海外仓 3 步') + '，投送不可撤销', href: 'sheet.html?id=' + sh.sheet_id + '&cid=' + c.cid, urgent: false });
          }
        });
      });
    }

    return out;
  }

  /* ============================================================
     名称
     ============================================================ */

  function warehouseName(wid) {
    var w = ensure().warehouses.filter(function (x) { return String(x.wid) === String(wid); })[0];
    if (!w) {
      // ★ 认不出的仓名必须硬失败/可见，不许落进 else '' 再被下游过滤掉
      err('warehouse.unknown', { wid: wid, known: ensure().warehouses.map(function (x) { return x.wid; }) });
      return '未知仓(' + wid + ')';
    }
    return w.name;
  }
  function sellerName(sellerId) {
    /* ★ null / 空 是**有含义的合法值**，不是查找失败：
       计划记录按货号铸出，刻意不带店铺（P2）—— 供应链下单不关心店铺，
       「这批货给哪个店」要到排货才定。
       ★ 原来这里会对它报 seller.unknown，于是每渲染一条计划记录就吼一声，
         真正的「店铺号写错了」反而被淹没在噪音里。 */
    if (sellerId === null || sellerId === undefined || sellerId === '') {
      return '不分店铺';
    }
    var x = ensure().sellers.filter(function (y) { return y.seller_id === String(sellerId); })[0];
    if (!x) {
      err('seller.unknown', { seller_id: sellerId, known: ensure().sellers.map(function (y) { return y.seller_id; }) });
      return '未知店铺(' + sellerId + ')';
    }
    return x.name;
  }

  /* ============================================================
     导出
     ============================================================ */

  var Store = {
    STATES: S,
    LX_STAGES: LX_STAGES,

    get: function () { return ensure(); },
    save: function () { persist(); return { ok: true }; },
    reset: function () {
      state = seed();
      persist();
      dbg('reset', { day: state.day });
      return { ok: true };
    },

    day: function () { return ensure().day; },
    tickNo: function () { return ensure().tick_no; },
    role: function () { return ensure().role; },
    setRole: function (r) {
      var s = ensure();
      if (['ops', 'buyer', 'dispatcher'].indexOf(r) < 0) {
        err('role.unknown', { role: r, known: ['ops', 'buyer', 'dispatcher'] });
        return { ok: false, error: 'bad_role' };
      }
      s.role = r;
      persist();
      return { ok: true };
    },

    tick: tick,
    log: log,
    todos: todos,

    submitPlan: submitPlan,

    buildPo: buildPo,
    createPo: createPo,
    poCandidates: poCandidates,
    addPoItems: addPoItems,
    setPoItem: setPoItem,
    removePoItem: removePoItem,
    poUsage: poUsage,
    poProgress: poProgress,
    planQuery: planQuery,
    pos: function () { return ensure().pos; },
    placePo: placePo,

    createSheet: createSheet,
    planProgress: planProgress,
    dispatchGrid: dispatchGrid,
    localSources: localSources,
    committedUnits: committedUnits,
    freeUnits: freeUnits,
    arrangeShipment: arrangeShipment,
    setArrangement: setArrangement,
    unarrangeShipment: unarrangeShipment,
    sheetList: sheetList,
    addSheetLines: addSheetLines,
    createConsignment: createConsignment,
    moveToConsignment: moveToConsignment,
    setLineShipment: setLineShipment,
    setLineBox: setLineBox,
    confirmSheet: confirmSheet,
    dispatch: dispatch,
    resolveManual: resolveManual,
    catalog: catalog,
    categories: categories,
    setDemand: setDemand,
    setPurchase: setPurchase,
    forecastSku: forecastSku,
    params: params,
    setParams: setParams,
    setPlanMonths: setPlanMonths,
    periodsFrom: periodsFrom,
    LEAD_PRESETS: LEAD_PRESETS,
    monthAfterDays: monthAfterDays,
    marketOf: marketOf,
    hasFba: hasFba,
    addClaims: addClaims,
    removeSku: removeSku,
    removeMsku: removeMsku,
    shiftMonth: shiftMonth,
    cellForecast: cellForecast,

    lineQty: lineQty,
    candidates: candidates,
    trace: trace,

    // 便利查找（页面用）
    plan: findPlan,
    line: findLine,
    po: findPo,
    sheet: findSheet,
    consignment: findConsignment,
    lines: function () { return ensure().lines.filter(function (l) { return l.state !== S.CANCELLED; }); },
    warehouseName: warehouseName,
    sellerName: sellerName,
    stockOf: function (sku, wid) { return ensure().stock[stockKey(sku, wid)] || { valid: 0, locked: 0 }; }
  };

  load();
  window.Store = Store;
  dbg('ready', { day: state.day, role: state.role, lines: state.lines.length, pos: state.pos.length, sheets: state.sheets.length });
})(window);
