/* ============================================================
   service_supplychain 可点击原型 —— 顶部工具条 · 待办 · 通用组件
   依赖：assets/store.js（必须先引）。零外部资源。

   页面这样用：
     <link rel="stylesheet" href="assets/shell.css">
     <script src="assets/store.js"></script>
     <script src="assets/shell.js"></script>
     <script>Shell.mount({title:'排货台', crumb:[{text:'首页',href:'index.html'},{text:'排货台'}]});</script>

   ★ 「⏩ 推进一天」是这个原型的心脏：到货、领星回执都是事实驱动的，
     不推进时间就走不到「准备排货」和「已完结」。
   ============================================================ */
(function (window, document) {
  'use strict';

  var TAG = '[scm-shell]';
  var TOAST_KEY = 'scm_proto_toast';   // 跨刷新传递提示（tick 后页面会重载）

  function dbg(e, d) { try { console.debug(TAG, e, d || {}); } catch (x) { } }
  function fail(e, d) { try { console.error(TAG, e, d || {}); } catch (x) { } }

  var ROLES = [
    { key: 'ops', name: '运营', home: 'ops.html' },
    { key: 'buyer', name: '采购员', home: 'buyer.html' },
    { key: 'dispatcher', name: '排货员', home: 'dispatcher.html' }
  ];

  function roleOf(key) {
    var r = ROLES.filter(function (x) { return x.key === key; })[0];
    if (!r) {
      // 认不出的角色必须可见，不许悄悄当成运营
      fail('role.unknown', { key: key, known: ROLES.map(function (x) { return x.key; }) });
      return { key: key, name: '未知角色(' + key + ')', home: 'index.html' };
    }
    return r;
  }

  /* ---------- DOM 小工具 ---------- */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function needStore(where) {
    if (!window.Store) {
      fail('store.missing', { where: where, hint: '页面必须先 <script src="assets/store.js">' });
      return false;
    }
    return true;
  }

  /* ---------- 数字渲染（返回 HTML 字符串，方便拼模板） ---------- */

  function fmt(n) {
    var v = Number(n);
    if (!isFinite(v)) return String(n === null || n === undefined ? '—' : n);
    return v.toLocaleString('en-US');
  }
  // Shell.qty(1200) → <span class="qty">1,200</span>
  function qty(n) {
    if (n === null || n === undefined || n === '') return '<span class="qty qty--zero">—</span>';
    var zero = Number(n) === 0 ? ' qty--zero' : '';
    return '<span class="qty' + zero + '">' + esc(fmt(n)) + '</span>';
  }
  // Shell.qtyOf(200, 300) → 「200 / 300」，分母退后一层；满了标绿
  function qtyOf(a, b) {
    var full = Number(a) >= Number(b) && Number(b) > 0 ? ' qty-of--full' : '';
    return '<span class="qty-of' + full + '">'
      + '<span class="qty-of__now">' + esc(fmt(a)) + '</span>'
      + '<span class="qty-of__sep"> / </span>'
      + '<span class="qty-of__all">' + esc(fmt(b)) + '</span></span>';
  }
  function chip(text, kind) {
    return '<span class="chip' + (kind ? ' chip--' + kind : '') + '">' + esc(text) + '</span>';
  }

  /* ---------- toast ---------- */

  function toastLayer() {
    var n = document.querySelector('.toasts');
    if (!n) { n = el('div', 'toasts'); document.body.appendChild(n); }
    return n;
  }
  function toast(msg, kind, ms) {
    var n = el('div', 'toast' + (kind ? ' toast--' + kind : ''), msg);
    toastLayer().appendChild(n);
    dbg('toast', { kind: kind || 'info', msg: String(msg).slice(0, 120) });
    window.setTimeout(function () { if (n.parentNode) n.parentNode.removeChild(n); }, ms || 6000);
    return n;
  }
  // 跨刷新的提示：tick / 切角色会重载页面，提示得活过那一下
  function toastAfterReload(msg, kind) {
    try { window.sessionStorage.setItem(TOAST_KEY, JSON.stringify({ msg: String(msg), kind: kind || '' })); }
    catch (e) { fail('toast.stash-failed', { message: e && e.message, fallback: '本次提示会随刷新丢失' }); }
  }
  function drainToast() {
    var raw = null;
    try { raw = window.sessionStorage.getItem(TOAST_KEY); window.sessionStorage.removeItem(TOAST_KEY); }
    catch (e) { return; }
    if (!raw) return;
    try { var t = JSON.parse(raw); toast(t.msg, t.kind, 9000); }
    catch (e) { fail('toast.parse-failed', { raw: raw, message: e && e.message }); }
  }

  /* ---------- 二次确认（不可逆动作） ---------- */

  function confirmDanger(opts) {
    opts = opts || {};
    var steps = opts.steps || [];
    var t0 = Date.now();

    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', opts.title || '确认');

    modal.appendChild(el('div', 'modal__head', opts.title || '确认这个动作'));

    var body = el('div', 'modal__body');
    var warn = el('div', 'modal__warn');
    warn.appendChild(el('span', 'irreversible', opts.warn || '此动作不可撤销'));
    body.appendChild(warn);

    if (steps.length) {
      body.appendChild(el('div', 'modal__steps-title', '将依次执行'));
      var ol = el('ol', 'modal__steps');
      steps.forEach(function (s) { ol.appendChild(el('li', null, s)); });
      body.appendChild(ol);
      body.appendChild(el('p', 'muted', '★ 中途失败时，已经执行过的步骤是真的发生了 —— 结果会逐步列出。'));
    }
    modal.appendChild(body);

    var foot = el('div', 'modal__foot');
    var cancel = el('button', 'btn', opts.cancelText || '取消'); cancel.type = 'button';
    var ok = el('button', 'btn btn--danger', opts.confirmText || '我确认'); ok.type = 'button';
    foot.appendChild(cancel); foot.appendChild(ok);
    modal.appendChild(foot);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    var lastFocus = document.activeElement;
    ok.focus();
    dbg('confirm.open', { title: opts.title || null, steps: steps.length });

    function close(how) {
      document.removeEventListener('keydown', onKey, true);
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      if (lastFocus && lastFocus.focus) lastFocus.focus();
      dbg('confirm.close', { how: how, ms: Date.now() - t0 });
    }
    function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); close('esc'); run(opts.onCancel, 'onCancel'); } }
    function run(fn, name) {
      if (typeof fn !== 'function') return;
      try { fn(); }
      catch (e) {
        // 吞掉这里的异常，会让「点了没反应」和「动作真的失败了」长得一模一样
        fail('confirm.hook-threw', { hook: name, message: e && e.message, stack: e && e.stack });
        toast('动作出错了：' + (e && e.message ? e.message : '未知错误') + '（详情见控制台）', 'fail');
      }
    }
    cancel.addEventListener('click', function () { close('cancel'); run(opts.onCancel, 'onCancel'); });
    ok.addEventListener('click', function () { close('confirm'); run(opts.onConfirm, 'onConfirm'); });
    backdrop.addEventListener('click', function (e) { if (e.target === backdrop) { close('backdrop'); run(opts.onCancel, 'onCancel'); } });
    document.addEventListener('keydown', onKey, true);
    return { close: close };
  }

  /* ---------- 顶部工具条 ---------- */

  function mount(opts) {
    if (!needStore('Shell.mount')) return null;
    opts = (typeof opts === 'string') ? { title: opts } : (opts || {});

    var s = window.Store.get();
    var shell = el('div', 'shell');

    var bar = el('div', 'shell__bar');

    var brand = el('div', 'shell__brand');
    var a = el('a', null, 'SUPPLYCHAIN 原型');
    a.href = 'index.html';
    brand.appendChild(a);
    bar.appendChild(brand);

    // 1) 角色切换 —— 切换后跳该角色首页
    var roles = el('div', 'shell__roles');
    roles.setAttribute('role', 'group');
    roles.setAttribute('aria-label', '角色');
    ROLES.forEach(function (r) {
      var b = el('button', 'shell__role', r.name);
      b.type = 'button';
      var n = window.Store.todos(r.key).length;
      if (n) b.textContent = r.name + ' ' + n;
      if (s.role === r.key) b.setAttribute('aria-current', 'true');
      b.addEventListener('click', function () { switchRole(r.key); });
      roles.appendChild(b);
    });
    bar.appendChild(roles);

    bar.appendChild(el('div', 'shell__spacer'));

    // 2) 当前模拟日期 + 推进一天
    var day = el('div', 'shell__day');
    day.innerHTML = '模拟日期 <b>' + esc(s.day) + '</b>'
      + '<span class="shell__daytag">第 ' + esc(s.tick_no) + ' 天</span>';
    bar.appendChild(day);

    var tickBtn = el('button', 'btn btn--sm', '⏩ 推进一天');
    tickBtn.type = 'button';
    tickBtn.title = '到货与领星回执是事实驱动的：不推进时间，记录走不到「准备排货」和「已完结」';
    tickBtn.addEventListener('click', doTick);
    bar.appendChild(tickBtn);

    // 3) 重置剧本
    var resetBtn = el('button', 'btn btn--sm btn--ghost', '↺ 重置剧本');
    resetBtn.type = 'button';
    resetBtn.addEventListener('click', doReset);
    bar.appendChild(resetBtn);

    shell.appendChild(bar);

    // 4) 面包屑（页面自己传）
    var crumbs = normalizeCrumb(opts);
    if (crumbs.length) {
      var c = el('div', 'shell__crumb');
      crumbs.forEach(function (it, i) {
        if (i) { var sep = el('span', 'sep', '/'); c.appendChild(sep); }
        if (it.href) { var link = el('a', null, it.text); link.href = it.href; c.appendChild(link); }
        else { c.appendChild(el('span', null, it.text)); }
      });
      shell.appendChild(c);
    }

    document.body.insertBefore(shell, document.body.firstChild);
    drainToast();
    dbg('mount', { role: s.role, day: s.day, title: opts.title || null });
    return shell;
  }

  function normalizeCrumb(opts) {
    var out = [];
    if (opts.crumb) {
      var raw = Array.isArray(opts.crumb) ? opts.crumb : [opts.crumb];
      raw.forEach(function (x) { out.push(typeof x === 'string' ? { text: x } : x); });
    } else if (opts.title) {
      out = [{ text: '首页', href: 'index.html' }, { text: opts.title }];
    }
    return out;
  }

  function switchRole(key) {
    if (!needStore('switchRole')) return;
    var r = window.Store.setRole(key);
    if (!r.ok) { toast('切换角色失败：' + (r.error || '未知'), 'fail'); return; }
    var home = roleOf(key).home;
    var n = window.Store.todos(key).length;
    toastAfterReload('已切到「' + roleOf(key).name + '」视角，当前待办 ' + n + ' 条。');
    window.location.href = home;
  }

  function doTick() {
    if (!needStore('doTick')) return;
    var t0 = Date.now();
    var r;
    try { r = window.Store.tick(); }
    catch (e) {
      fail('tick.threw', { message: e && e.message, cause: e && e.cause && e.cause.code, stack: e && e.stack });
      toast('推进一天失败：' + (e && e.message) + '（详情见控制台）', 'fail');
      return;
    }
    dbg('tick.done', { ms: Date.now() - t0, day: r.day, arrived: r.arrivedUnits, moved: r.moved });
    var kind = (r.arrivedUnits || r.advanced.length || r.moved) ? 'ok' : '';
    toastAfterReload(r.text + (kind ? '' : '\n（这一天什么都没发生 —— 还没有已下单的采购单或已投送的排货单）'), kind);
    window.location.reload();
  }

  function doReset() {
    if (!needStore('doReset')) return;
    confirmDanger({
      title: '重置剧本',
      warn: '会清空当前所有计划、采购单、排货表与日志，回到 2026-09-16 的初始剧本',
      steps: ['清空 localStorage[scm_proto]', '按初始剧本重新播种', '回到首页'],
      confirmText: '我确认，重置',
      onConfirm: function () {
        window.Store.reset();
        toastAfterReload('剧本已重置：回到 2026-09-16，本地仓可发量全为 0。');
        window.location.href = 'index.html';
      }
    });
  }

  /* ---------- 待办渲染 ---------- */

  function todos(target, role) {
    if (!needStore('Shell.todos')) return null;
    var node = resolveTarget(target, 'Shell.todos');
    var r = role || window.Store.role();
    var list = window.Store.todos(r);

    var box;
    if (!list.length) {
      box = el('div', 'todos__empty', '当前没有待办 —— 试试「⏩ 推进一天」，或切到别的角色看看。');
    } else {
      box = el('div', 'todos');
      list.forEach(function (t) {
        var card = el('div', 'todo' + (t.urgent ? ' todo--urgent' : ''));
        card.appendChild(el('div', 'todo__title', t.title));
        if (t.hint) card.appendChild(el('div', 'todo__hint', t.hint));
        if (t.href) {
          var go = el('div', 'todo__go');
          var a = el('a', null, '去处理 →');
          a.href = t.href;
          go.appendChild(a);
          card.appendChild(go);
        }
        box.appendChild(card);
      });
    }
    if (node) { node.innerHTML = ''; node.appendChild(box); }
    dbg('todos.render', { role: r, count: list.length, mounted: !!node });
    return box;
  }

  function resolveTarget(target, where) {
    if (!target) {
      var auto = document.getElementById('todos') || document.querySelector('[data-todos]');
      if (!auto) fail('target.missing', { where: where, hint: '传一个元素/id，或在页面上放 <div id="todos">；本次只返回节点，没挂上去' });
      return auto;
    }
    if (typeof target === 'string') {
      var n = document.getElementById(target);
      if (!n) fail('target.not-found', { where: where, id: target });
      return n;
    }
    return target;
  }

  /* ---------- 给业务页用的两个小渲染器 ---------- */

  // 确认闸结果：★ 6 项全列，过的也列
  function gates(list, target) {
    var node = resolveTarget(target || null, 'Shell.gates');
    var passed = (list || []).filter(function (g) { return g.pass; }).length;
    var box = el('div', 'gate');
    box.appendChild(el('div', 'gate__sum', '确认闸 ' + passed + ' / ' + (list || []).length + ' 项通过'
      + (passed === (list || []).length ? '　—— 全过，排货表已转「已确认」' : '　—— 有未过项，状态未改变')));
    (list || []).forEach(function (g) {
      var item = el('div', 'gate__item gate__item--' + (g.pass ? 'pass' : 'fail'));
      var head = el('span');
      head.innerHTML = '<span class="gate__code">' + esc(g.code) + '</span>' + esc(g.name);
      item.appendChild(head);
      if (g.detail) item.appendChild(el('span', 'gate__detail', g.detail));
      box.appendChild(item);
    });
    if (node) { node.innerHTML = ''; node.appendChild(box); }
    return box;
  }

  // 投送结果：逐步列出，manual 的那步标 ⚠ 并说明「前几步真的发生了」
  function steps(list, target) {
    var node = resolveTarget(target || null, 'Shell.steps');
    var box = el('div');
    var ol = el('ol', 'steps');
    (list || []).forEach(function (s) {
      var li = el('li');
      li.setAttribute('data-state', s.status === 'ok' ? 'ok' : (s.status === 'manual' ? 'manual' : 'pending'));
      li.appendChild(el('span', 'steps__mark'));
      li.appendChild(el('span', 'steps__name', s.stage));
      li.appendChild(el('span', 'steps__ref', s.doc_no || ''));
      if (s.reason) li.appendChild(el('span', 'stale', s.reason));
      ol.appendChild(li);
    });
    box.appendChild(ol);
    var manual = (list || []).filter(function (s) { return s.status === 'manual'; });
    if (manual.length) {
      var note = el('div', 'note');
      note.appendChild(el('p', null,
        '★ 前面几步是真的发生了 —— 单据此刻真实存在于领星里。只报「失败」，人会以为什么都没发生，然后重投一次。'));
      note.appendChild(el('p', null, '我方进度停在「已确认」，库存已锁未扣；请先按单号去领星核实，再决定是否续投。'));
      box.appendChild(note);
    }
    if (node) { node.innerHTML = ''; node.appendChild(box); }
    return box;
  }

  /* ---------- URL 参数 ---------- */

  function param(name, dflt) {
    var m = new RegExp('[?&]' + name + '=([^&#]*)').exec(window.location.search);
    return m ? decodeURIComponent(m[1]) : (dflt === undefined ? null : dflt);
  }

  window.Shell = {
    ROLES: ROLES,
    mount: mount,
    todos: todos,
    toast: toast,
    toastAfterReload: toastAfterReload,
    confirmDanger: confirmDanger,
    qty: qty,
    qtyOf: qtyOf,
    chip: chip,
    esc: esc,
    el: el,
    gates: gates,
    steps: steps,
    param: param,
    roleName: function (k) { return roleOf(k).name; },
    roleHome: function (k) { return roleOf(k).home; },
    tick: doTick,
    reload: function () { window.location.reload(); }
  };
  dbg('ready', {});
})(window, document);
