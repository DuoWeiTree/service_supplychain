import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { Qty, type QtyValue } from '../components/Qty';
import type { ForecastReason, Period, Sid } from '../api/types';
import {
  closingOfLast, demandAt, mskuStockAt, stockAt, sumUnits, systemAt, totalsAt,
  type GridModel, type MskuCell, type MskuRow, type SkuBlock,
} from './planGridModel';
import { buildCards, cardMatches, monthLabel, riskOrder, stockState, summarize, type CardSummary, type SkuCard } from './planSheetModel';
import { demandKey, mskuKey, purchaseKey, skuKey } from './usePlanGridSaves';

/** ★ 设计取值 · 未实测：期末低于这个件数就铺「偏低」底色。
 *  没有任何一条口径指到这个 50 —— 它只改底色和「偏低」筛选，不参与任何一个数。 */
export const LOW_STOCK_UNITS = 50;

/** ★ 设计取值 · 未实测：行高固定是虚拟滚动的前提（只渲染可视区上下各 OVERSCAN 行） */
const ROW_H = { comfortable: 48, compact: 34 } as const;
const HEAD_H = 40;
const OVERSCAN = 6;
/** 量不到可视区高度时（jsdom、首帧）按这个算 */
const FALLBACK_VIEW_H = 640;

export interface PlanSheetProps {
  model: GridModel;
  /** usePlanGridSaves 的在飞集合：在飞的格子不接受第二次编辑（F3） */
  pending: ReadonlySet<string>;
  /** 客户端校验不过的行内提示，按 demandKey / purchaseKey 记 */
  inputErr: Record<string, string>;
  onDemand: (sellerSku: string, sid: Sid, period: Period, raw: string) => void;
  onPurchase: (sku: string, period: Period, raw: string) => void;
  onRemoveStore: (block: SkuBlock) => void;
  onRemoveMsku: (sellerSku: string, sid: Sid) => void;
  lowStock?: number;
}

/** 可以落光标的一行：货号行（计划采购）/ 单 msku 的店铺行 / msku 子行 */
type NavRow =
  | { kind: 'purchase'; card: SkuCard }
  | { kind: 'demand'; block: SkuBlock; row: MskuRow };

/** 表里的一行（含不能落光标的：多 msku 店铺的合计行、没有采购行的货号行） */
type SheetRow =
  | { type: 'sku'; card: SkuCard; nav: number | undefined }
  | { type: 'store'; card: SkuCard; block: SkuBlock; nav: number | undefined }
  | { type: 'msku'; card: SkuCard; block: SkuBlock; row: MskuRow; nav: number };

type Filter = 'all' | 'out' | 'todo' | 'low';
type Sort = 'risk' | 'sales' | 'sku';
type Density = keyof typeof ROW_H;

function cellOf(row: MskuRow, p: Period): MskuCell | undefined { return row.cells.find((c) => c.period === p); }
const fmt = (n: number | null) => (n === null ? '—' : n.toLocaleString('en-US'));
const blockKey = (b: SkuBlock) => `${b.sid}-${b.sku}`;
const SHORT_LABEL = { 预测: '预', 库存: '存', 在途: '途' } as const;

/** 库存为什么是 — 。★ 光秃秃一根 — 分不出「没在仓数」「销量待填」「mock 没重算」 */
const UNKNOWN_WHY: Partial<Record<Exclude<ForecastReason, null>, string>> = {
  unknown_onhand: '无在仓数',
  unknown_demand: '销量待填',
  bridge_rate_unknown: '计划首月前的销速未知',
  inbound_unknown: '到货数未知',
  not_recomputed_in_mock: '改后未重算（mock）',
  elapsed: '月份已过',
  no_msku: '无 msku',
};
function unknownWhy(reason: ForecastReason | undefined): string {
  if (reason == null) return '无数据';
  return UNKNOWN_WHY[reason] ?? `未知（${reason}）`;
}

export function PlanSheet(props: PlanSheetProps) {
  const { model, pending, inputErr } = props;
  const low = props.lowStock ?? LOW_STOCK_UNITS;
  const periods = model.periods;

  const cards = useMemo(() => buildCards(model), [model]);
  const sums = useMemo(() => new Map(cards.map((c) => [c.sku, summarize(c, periods, low)])), [cards, periods, low]);

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [sort, setSort] = useState<Sort>('risk');
  const [density, setDensity] = useState<Density>('comfortable');
  const [foldedSkus, setFoldedSkus] = useState<Set<string>>(new Set());
  // ★ 多 msku 的店铺默认展开：要填的数就在 msku 那一层，收着等于让人多点一下
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [cur, setCur] = useState({ r: 0, c: 0 });
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [editing, setEditingState] = useState<{ draft: string; mode: 'replace' | 'edit' } | null>(null);
  // 在飞期间显示人刚填的数，落地后以服务端重载的为准
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(FALLBACK_VIEW_H);
  const editRef = useRef<typeof editing>(null);
  const scRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const ROW = ROW_H[density];

  const setEditing = (v: typeof editing) => { editRef.current = v; setEditingState(v); };

  useEffect(() => {
    const up = () => { dragging.current = false; };
    document.addEventListener('mouseup', up);
    return () => document.removeEventListener('mouseup', up);
  }, []);

  // 可视区高度跟着窗口走（CSS 定高度，这里只量出来给虚拟滚动用）
  useLayoutEffect(() => {
    const el = scRef.current;
    if (!el) return;
    const measure = () => { if (el.clientHeight > 0) setViewH(el.clientHeight - HEAD_H); };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const tally = {
    all: cards.length,
    out: cards.filter((c) => sums.get(c.sku)!.outs.length > 0).length,
    todo: cards.filter((c) => sums.get(c.sku)!.todo > 0).length,
    low: cards.filter((c) => sums.get(c.sku)!.lows.length > 0).length,
  };
  const passes = (s: CardSummary) =>
    filter === 'all' || (filter === 'out' ? s.outs.length > 0 : filter === 'todo' ? s.todo > 0 : s.lows.length > 0);
  let shown = cards.filter((c) => passes(sums.get(c.sku)!) && cardMatches(c, query));
  if (sort === 'risk') shown = [...shown].sort((a, b) => riskOrder(sums.get(a.sku)!, sums.get(b.sku)!) || a.sku.localeCompare(b.sku));
  else if (sort === 'sales') shown = [...shown].sort((a, b) => (sums.get(b.sku)!.demandKnown ?? 0) - (sums.get(a.sku)!.demandKnown ?? 0));
  else shown = [...shown].sort((a, b) => a.sku.localeCompare(b.sku));

  const isOpen = (b: SkuBlock) => b.mskus.length > 1 && !collapsed.has(blockKey(b));

  // ── 扁平行 + 导航行 ──
  const rows: SheetRow[] = [];
  const nav: NavRow[] = [];
  const navRowIdx: number[] = [];   // nav 下标 → rows 下标（光标出可视区时滚过去）
  const addNav = (n: NavRow) => { navRowIdx.push(rows.length); nav.push(n); return nav.length - 1; };
  for (const card of shown) {
    rows.push({ type: 'sku', card, nav: card.purchase ? addNav({ kind: 'purchase', card }) : undefined });
    if (foldedSkus.has(card.sku)) continue;
    for (const b of card.stores) {
      const single = b.mskus.length === 1 ? b.mskus[0]! : null;
      rows.push({ type: 'store', card, block: b, nav: single ? addNav({ kind: 'demand', block: b, row: single }) : undefined });
      if (isOpen(b)) for (const r of b.mskus) rows.push({ type: 'msku', card, block: b, row: r, nav: addNav({ kind: 'demand', block: b, row: r }) });
    }
  }
  const cr = Math.min(cur.r, Math.max(0, nav.length - 1));
  const ar = Math.min(anchor.r, Math.max(0, nav.length - 1));
  const sel = { r0: Math.min(cr, ar), r1: Math.max(cr, ar), c0: Math.min(cur.c, anchor.c), c1: Math.max(cur.c, anchor.c) };
  const inSel = (r: number, c: number) => r >= sel.r0 && r <= sel.r1 && c >= sel.c0 && c <= sel.c1;

  function keyOf(n: NavRow, p: Period): string {
    return n.kind === 'purchase' ? purchaseKey(n.card.sku, p) : demandKey(n.row.seller_sku, n.row.sid, p);
  }
  /** 人填的那一层；没有人填 ⇒ null */
  function humanOf(n: NavRow, p: Period): number | null {
    if (n.kind === 'purchase') return n.card.purchase?.cells.find((c) => c.period === p)?.planned_units ?? null;
    return cellOf(n.row, p)?.expected_units ?? null;
  }
  /** 屏上显示的那个数（生效值） */
  function shownOf(n: NavRow, p: Period): number | null {
    if (n.kind === 'purchase') return humanOf(n, p);
    return cellOf(n.row, p)?.effective_units ?? null;
  }

  function save(n: NavRow, p: Period, raw: string) {
    const k = keyOf(n, p);
    if (pending.has(k)) return;
    setDrafts((d) => ({ ...d, [k]: raw }));
    if (n.kind === 'purchase') props.onPurchase(n.card.sku, p, raw);
    else props.onDemand(n.row.seller_sku, n.row.sid, p, raw);
  }
  // 落地后丢掉草稿
  useEffect(() => {
    setDrafts((d) => {
      const keep = Object.fromEntries(Object.entries(d).filter(([k]) => pending.has(k)));
      return Object.keys(keep).length === Object.keys(d).length ? d : keep;
    });
  }, [pending]);

  /** 光标走出可视区就滚过去 —— 虚拟滚动下，看不见的行根本不在 DOM 里 */
  function reveal(ni: number) {
    const el = scRef.current; const ri = navRowIdx[ni];
    if (!el || ri === undefined) return;
    const top = ri * ROW; const bottom = top + ROW;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (bottom > el.scrollTop + viewH) el.scrollTop = bottom - viewH;
  }
  function moveTo(r: number, c: number, extend = false) {
    const t = { r: Math.max(0, Math.min(nav.length - 1, r)), c: Math.max(0, Math.min(periods.length - 1, c)) };
    setCur(t); if (!extend) setAnchor(t);
    reveal(t.r);
  }
  const focusGrid = () => scRef.current?.focus({ preventScroll: true });

  function commit(dr = 0, dc = 0) {
    const ed = editRef.current;
    if (!ed) return;
    editRef.current = null;
    const n = nav[cr]; const p = periods[cur.c];
    if (n && p) {
      const raw = ed.draft.trim();
      const before = humanOf(n, p);
      const onScreen = shownOf(n, p);
      // 没改（包括原样确认一个预估值）就不发请求：确认预估 ≠ 人填
      const unchanged = raw === (before === null ? '' : String(before))
        || (before === null && onScreen !== null && raw === String(onScreen));
      if (!unchanged) save(n, p, raw);
    }
    setEditingState(null);
    if (dr || dc) moveTo(cr + dr, cur.c + dc);
    setTimeout(focusGrid, 0);
  }

  function startEdit(mode: 'replace' | 'edit', first = '') {
    const n = nav[cr]; const p = periods[cur.c];
    if (!n || !p || pending.has(keyOf(n, p))) return;
    const v = shownOf(n, p);
    setEditing({ draft: mode === 'replace' ? first : v === null ? '' : String(v), mode });
  }

  function eachSel(fn: (n: NavRow, p: Period) => void) {
    for (let r = sel.r0; r <= sel.r1; r++) for (let c = sel.c0; c <= sel.c1; c++) {
      const n = nav[r]; const p = periods[c];
      if (n && p) fn(n, p);
    }
  }

  /** 从光标往后找第一格「待填」—— 只找销量格：采购空着是「不买」，不是没填 */
  function nextTodo() {
    for (let r = cr; r < nav.length; r++) {
      const n = nav[r]!;
      if (n.kind !== 'demand') continue;
      for (let c = r === cr ? cur.c + 1 : 0; c < periods.length; c++) {
        if (shownOf(n, periods[c]!) === null) { moveTo(r, c); return; }
      }
    }
  }
  const toggleSku = (sku: string) => setFoldedSkus((s) => { const n = new Set(s); if (n.has(sku)) n.delete(sku); else n.add(sku); return n; });

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (editRef.current || nav.length === 0 || e.target !== e.currentTarget) return;
    const k = e.key; const mod = e.ctrlKey || e.metaKey;
    if (k === 'ArrowUp') { e.preventDefault(); moveTo(cr - 1, cur.c, e.shiftKey); }
    else if (k === 'ArrowDown') { e.preventDefault(); moveTo(cr + 1, cur.c, e.shiftKey); }
    else if (k === 'ArrowLeft') { e.preventDefault(); moveTo(cr, cur.c - 1, e.shiftKey); }
    else if (k === 'ArrowRight') { e.preventDefault(); moveTo(cr, cur.c + 1, e.shiftKey); }
    else if (k === 'Tab') {
      let nc = cur.c + (e.shiftKey ? -1 : 1); let nr = cr;
      if (nc >= periods.length) { nc = 0; nr++; } else if (nc < 0) { nc = periods.length - 1; nr--; }
      if (nr < 0 || nr >= nav.length) return;           // 让焦点离开整表
      e.preventDefault(); moveTo(nr, nc);
    }
    else if (k === 'Enter' || k === 'F2') { e.preventDefault(); startEdit('edit'); }
    else if (k === 'Delete' || k === 'Backspace') {
      e.preventDefault();
      eachSel((n, p) => { if (humanOf(n, p) !== null) save(n, p, ''); });
    }
    // ★ ⌘D 只在同一类行之间填：采购只填到采购，销量只填到销量 —— 两种量单位相同，填串了不会报错
    else if (mod && (k === 'd' || k === 'D')) {
      e.preventDefault();
      const top = nav[sel.r0];
      for (let c = sel.c0; c <= sel.c1; c++) {
        const p = periods[c]; if (!p || !top) continue;
        const v = shownOf(top, p);
        for (let r = sel.r0 + 1; r <= sel.r1; r++) {
          const n = nav[r];
          if (n && n.kind === top.kind && shownOf(n, p) !== v) save(n, p, v === null ? '' : String(v));
        }
      }
    }
    else if (mod && (k === 'r' || k === 'R')) {
      e.preventDefault();
      for (let r = sel.r0; r <= sel.r1; r++) {
        const n = nav[r]; const p0 = periods[sel.c0]; if (!n || !p0) continue;
        const v = shownOf(n, p0);
        for (let c = sel.c0 + 1; c <= sel.c1; c++) { const p = periods[c]; if (p && shownOf(n, p) !== v) save(n, p, v === null ? '' : String(v)); }
      }
    }
    else if (mod && (k === 'a' || k === 'A')) { e.preventDefault(); setAnchor({ r: 0, c: 0 }); setCur({ r: nav.length - 1, c: periods.length - 1 }); }
    else if (!mod && (k === 'n' || k === 'N')) { e.preventDefault(); nextTodo(); }
    else if (!mod && k === ' ') {
      e.preventDefault();
      const n = nav[cr];
      if (n?.kind === 'purchase') toggleSku(n.card.sku);
    }
    // 其余可打印字符都进入编辑：非法输入（5oo、-5）也要走到校验那一步并被点名，不是被键盘吞掉
    else if (!mod && !e.altKey && k.length === 1) { e.preventDefault(); startEdit('replace', k); }
  }

  function onCopy(e: ClipboardEvent<HTMLDivElement>) {
    if (editRef.current || nav.length === 0) return;
    const lines: string[] = [];
    for (let r = sel.r0; r <= sel.r1; r++) {
      const cells: string[] = [];
      for (let c = sel.c0; c <= sel.c1; c++) {
        const n = nav[r]; const p = periods[c];
        const v = n && p ? shownOf(n, p) : null;
        cells.push(v === null ? '' : String(v));
      }
      lines.push(cells.join('\t'));
    }
    e.clipboardData.setData('text/plain', lines.join('\n'));
    e.preventDefault();
  }

  /** 从 Excel 粘一块：从选区左上角铺开。每格各自走 parseUnits —— 坏值逐格点名，其余照写 */
  function onPaste(e: ClipboardEvent<HTMLDivElement>) {
    if (editRef.current || nav.length === 0) return;
    const text = e.clipboardData.getData('text/plain');
    if (!text) return;
    e.preventDefault();
    const lines = text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((l) => l.split('\t'));
    const single = lines.length === 1 && lines[0]!.length === 1;
    const r1 = single ? sel.r1 : Math.min(nav.length - 1, sel.r0 + lines.length - 1);
    const c1 = single ? sel.c1 : Math.min(periods.length - 1, sel.c0 + lines[0]!.length - 1);
    for (let r = sel.r0; r <= r1; r++) for (let c = sel.c0; c <= c1; c++) {
      const raw = single ? lines[0]![0] : lines[r - sel.r0]?.[c - sel.c0];
      const n = nav[r]; const p = periods[c];
      if (raw === undefined || !n || !p) continue;
      const before = humanOf(n, p);
      if (raw.trim() !== (before === null ? '' : String(before))) save(n, p, raw);
    }
    setAnchor({ r: sel.r0, c: sel.c0 }); setCur({ r: r1, c: c1 });
  }

  function onInputKey(e: KeyboardEvent<HTMLInputElement>) {
    const k = e.key;
    if (k === 'Enter') { e.preventDefault(); commit(e.shiftKey ? -1 : 1, 0); }
    else if (k === 'Tab') { e.preventDefault(); commit(0, e.shiftKey ? -1 : 1); }
    else if (k === 'Escape') { e.preventDefault(); editRef.current = null; setEditingState(null); setTimeout(focusGrid, 0); }
    else if (k === 'ArrowUp' || k === 'ArrowDown') { e.preventDefault(); commit(k === 'ArrowUp' ? -1 : 1, 0); }
    else if (editing?.mode === 'replace' && (k === 'ArrowLeft' || k === 'ArrowRight')) { e.preventDefault(); commit(0, k === 'ArrowLeft' ? -1 : 1); }
  }

  let selInfo: { n: number; sum: number; unknown: number } | null = null;
  if (nav.length > 0) {
    let n = 0; let s = 0; let u = 0;
    eachSel((row, p) => { n++; const v = shownOf(row, p); if (v === null) u++; else s += v; });
    selInfo = { n, sum: s, unknown: u };
  }

  // ── 格子 ──
  // ★ 普通函数，不是组件：写成 <EditableCell> 会在每次渲染时换一个组件类型，编辑框随之被卸载、丢焦点
  function editableCell(args: {
    key: string; n: NavRow; ni: number; p: Period; ci: number; label: string; testId?: string;
    stock?: string | undefined; human: boolean; title?: string | undefined; value: ReactNode; sub: ReactNode;
  }) {
    const k = keyOf(args.n, args.p);
    const isCur = args.ni === cr && args.ci === cur.c;
    const isEdit = isCur && editing !== null;
    const busy = pending.has(k);
    const err = inputErr[k];
    const selected = inSel(args.ni, args.ci);
    return (
      <div
        key={args.key}
        role="gridcell"
        aria-label={`${args.label} ${args.p}`}
        aria-selected={selected}
        aria-busy={busy || undefined}
        data-testid={args.testId}
        data-stock={args.stock}
        data-state={args.stock}
        title={err ?? args.title}
        className={['ps-c', selected && 'is-sel', isCur && 'is-cur', isEdit && 'is-edit',
          args.human && 'is-human', busy && 'is-pending', err && 'is-err'].filter(Boolean).join(' ')}
        onMouseDown={(e) => {
          if (isEdit) return;
          if (editRef.current) commit();
          e.preventDefault(); dragging.current = true; moveTo(args.ni, args.ci, e.shiftKey); focusGrid();
        }}
        onMouseEnter={() => { if (dragging.current) setCur({ r: args.ni, c: args.ci }); }}
        onDoubleClick={() => startEdit('edit')}
      >
        {isEdit ? (
          <input
            className="ps-in" autoFocus inputMode="numeric" value={editing.draft}
            aria-label={`${args.label} ${args.p}`}
            onChange={(e) => setEditing({ draft: e.target.value, mode: editing.mode })}
            onKeyDown={onInputKey}
            onBlur={() => commit()}
          />
        ) : args.value}
        {/* 行高固定：错误顶掉副数那一行，不撑高 */}
        {err ? <span className="ps-err" role="alert" data-testid={errTestId(args.n, args.p)}>{err}</span> : args.sub}
      </div>
    );
  }

  function errTestId(n: NavRow, p: Period) {
    return n.kind === 'purchase' ? `err-purchase-${n.card.sku}-${p}` : `err-${n.row.seller_sku}-${n.row.sid}-${p}`;
  }

  function demandValue(c: MskuCell | undefined, draft: string | undefined, testId?: string): ReactNode {
    if (draft !== undefined) return <span className="ps-v ps-v--human" data-testid={testId}>{draft === '' ? '—' : draft}</span>;
    if (!c || c.effective_units === null) return <span className="ps-v ps-v--todo" data-testid={testId}>待填</span>;
    return (
      <span className={`ps-v ps-v--${c.basis === 'human' ? 'human' : 'est'}`}>
        {c.system_extrapolated && c.basis !== 'human' && <span className="pm-v__tag" aria-label="系统外推">外推</span>}
        <span data-testid={testId}>{fmt(c.effective_units)}</span>
      </span>
    );
  }

  /** 店铺行副数：预测 + 库存。★ 断货 / 偏低是跟在库存后面的状态词，不顶掉「库存」这个标签 */
  function stockLine(v: QtyValue, block: SkuBlock, p: Period): ReactNode {
    const s = stockState(v, low);
    const inv = block.stock.find((r) => r.period === p);
    return (
      <span className="ps-d">
        <span>预测 <b data-testid={`store-system-${block.sid}-${block.sku}-${p}`}><Qty v={systemAt(block, p)} /></b></span>
        {v.kind === 'na'
          ? <span>库存 <b data-testid="closing-na">无 FBA</b></span>
          : (
            <span title={v.kind === 'num' ? undefined : unknownWhy(inv?.reason)}>
              库存 <b data-testid="closing-sku">{v.kind === 'num' ? fmt(v.value) : '—'}</b>
              {(s === 'out' || s === 'low') && (
                <em className={`ps-flag ps-flag--${s === 'out' ? 'danger' : 'warn'}`}>{s === 'out' ? '断货' : '偏低'}</em>
              )}
            </span>
          )}
      </span>
    );
  }

  function stockTitle(block: SkuBlock, p: Period): string | undefined {
    const inv = block.stock.find((s) => s.period === p);
    if (!inv || inv.state === 'not_applicable') return undefined;
    return `期初 ${inv.opening ?? '—'} · 数据截至 ${model.params.as_of}`;
  }

  const cols = `var(--row-head-w) repeat(${periods.length}, minmax(var(--col-w), 1fr)) 112px`;
  const minW = `calc(var(--row-head-w) + ${periods.length} * var(--col-w) + 112px)`;
  const rowStyle = (i: number): CSSProperties => ({ gridTemplateColumns: cols, height: ROW, top: i * ROW, minWidth: minW });

  /** 紧凑行高放不下货号行三个全称标签：换单字，全称挂 title（仍是文字，不只靠颜色） */
  const skuLabel = (full: keyof typeof SHORT_LABEL): ReactNode =>
    density === 'compact' ? <span title={full}>{SHORT_LABEL[full]}</span> : full;

  function skuRow(row: Extract<SheetRow, { type: 'sku' }>, i: number): ReactNode {
    const { card } = row;
    const s = sums.get(card.sku)!;
    const folded = foldedSkus.has(card.sku);
    const buy = card.purchase;
    const transitAt = (p: Period) => card.transit?.cells.find((c) => c.period === p)?.units ?? null;
    const sub = (p: Period) => {
      const t = totalsAt(model, card.sku, p);
      const stockTxt = !t ? '—' : t.state === 'not_applicable' ? '无 FBA' : fmt(t.closing);
      const stockWhy = !t ? '无货号行'
        : t.closing !== null || t.state === 'not_applicable' ? undefined
        : t.reason_from ? `${t.reason_from.seller_sku}：${unknownWhy(t.reason)}` : unknownWhy(t.reason);
      return (
        <span className="ps-d">
          <span>{skuLabel('预测')} <b data-testid={`sku-system-${card.sku}-${p}`}>{t?.system_not_applicable ? '不适用' : fmt(t?.system ?? null)}</b></span>
          <span title={stockWhy}>{skuLabel('库存')} <b data-testid={`sku-stock-${card.sku}-${p}`}>{stockTxt}</b></span>
          <span title="不含 FBA 货件（未取）">{skuLabel('在途')} <b data-testid={`transit-${card.sku}-${p}`}>{fmt(transitAt(p))}</b></span>
        </span>
      );
    };
    return (
      <div key={`s|${card.sku}`} className="ps-row ps-row--sku" role="row" style={rowStyle(i)} data-testid={`purchase-row-${card.sku}`}>
        <div className="ps-rh" role="rowheader">
          <button type="button" className="pm-tw" aria-expanded={!folded}
            aria-label={`${folded ? '展开' : '折叠'} ${card.sku}`} onClick={() => toggleSku(card.sku)}>›</button>
          <div className="ps-rh__txt">
            <div className="ps-sku">
              {card.sku}
              {s.outs.length > 0 && (
                <span className="ps-flag ps-flag--danger" data-testid={`outage-${card.sku}`}
                  title={s.outs.map((o) => `${o.block.seller_name} 断货 ${o.months} 个月`).join('\n')}>断 {s.outs.length}</span>
              )}
              {s.todo > 0 && <span className="ps-flag ps-flag--warn" title={`${s.todo} 格销量待填`}>待 {s.todo}</span>}
            </div>
            <div className="ps-sub">采购／预测·库存·在途</div>
          </div>
        </div>
        {periods.map((p, ci) => {
          if (!buy || row.nav === undefined) {
            return <div key={p} className="ps-c is-ro"><span className="ps-v ps-v--none">—</span>{sub(p)}</div>;
          }
          const n: NavRow = { kind: 'purchase', card };
          const k = purchaseKey(card.sku, p);
          const v = buy.cells.find((c) => c.period === p)?.planned_units ?? null;
          const draft = pending.has(k) ? drafts[k] : undefined;
          const txt = draft !== undefined ? (draft === '' ? '—' : draft) : fmt(v);
          return editableCell({
            key: p, n, ni: row.nav, p, ci, label: `计划采购量 ${card.sku}`, human: v !== null,
            value: <span className={`ps-v ${v === null && draft === undefined ? 'ps-v--none' : 'ps-v--human'}`}>{txt}</span>,
            sub: sub(p),
          });
        })}
        <div className="ps-sum">
          <span className="ps-v"><Qty v={known(buy?.cells.map((c) => c.planned_units) ?? [])} /></span>
          <span className="ps-d">期望 <b>{fmt(s.demandKnown)}</b></span>
        </div>
      </div>
    );
  }

  function storeRow(row: Extract<SheetRow, { type: 'store' }>, i: number): ReactNode {
    const { block } = row;
    const multi = block.mskus.length > 1;
    const open = isOpen(block);
    const single = multi ? null : block.mskus[0]!;
    const bKey = blockKey(block);
    return (
      <div key={`b|${bKey}`} className="ps-row ps-row--store" role="row" style={rowStyle(i)}>
        <div className="ps-rh ps-rh--store" role="rowheader">
          {multi ? (
            <button
              type="button" className="pm-tw" aria-expanded={open}
              aria-label={`${open ? '折叠' : '展开'} ${block.seller_name} 的 ${block.sku}`}
              onClick={() => setCollapsed((s) => { const n = new Set(s); if (n.has(bKey)) n.delete(bKey); else n.add(bKey); return n; })}
            >›</button>
          ) : <span className="pm-tw pm-tw--none" />}
          <div className="ps-rh__txt">
            <div className="ps-store">
              {block.seller_name}
              {block.market && <span className="pm-mkt">{block.market}</span>}
            </div>
            <div className={`ps-sub${multi ? '' : ' ps-sub--mono'}`}>{multi ? `${block.mskus.length} 个 msku 合计` : single!.seller_sku}</div>
          </div>
          <button
            type="button" className="pm-rm" aria-label="移出货号" title={`把 ${block.seller_name} 的 ${block.sku} 移出这张计划`}
            disabled={pending.has(skuKey(block))} onClick={() => props.onRemoveStore(block)}
          >移出</button>
        </div>
        {periods.map((p, ci) => {
          const inv = stockAt(block, p);
          const stock = stockState(inv, low);
          if (single && row.nav !== undefined) {
            const c = cellOf(single, p);
            const k = demandKey(single.seller_sku, single.sid, p);
            return editableCell({
              key: p, n: { kind: 'demand', block, row: single }, ni: row.nav, p, ci,
              label: `期望销量 ${single.seller_sku}`, testId: `sku-cell-${p}`, stock, human: c?.basis === 'human',
              title: stockTitle(block, p),
              value: <span data-testid={`cell-${single.seller_sku}-${single.sid}-${p}`}>{demandValue(c, pending.has(k) ? drafts[k] : undefined, `sum-expected-${p}`)}</span>,
              sub: stockLine(inv, block, p),
            });
          }
          const d = demandAt(block, p);
          const anyHuman = block.mskus.some((r) => cellOf(r, p)?.basis === 'human');
          return (
            <div key={p} className="ps-c is-ro" data-testid={`sku-cell-${p}`} data-stock={stock} data-state={stock}
              title={stockTitle(block, p)}>
              <span className={`ps-v ps-v--${d.kind !== 'num' ? 'todo' : anyHuman ? 'human' : 'est'}`} data-testid={`sum-expected-${p}`}>
                {d.kind === 'num' ? fmt(d.value) : '待填'}
              </span>
              {stockLine(inv, block, p)}
            </div>
          );
        })}
        <div className="ps-sum">
          <span className="ps-v" data-testid="sum-demand">
            <Qty v={sumUnits(block.mskus.flatMap((r) => r.cells.map((c) => c.effective_units)))} />
          </span>
          {/* ★ 存量不求和，给最后一个月的期末：onhand 是一条链，相加等于把同一批货数三遍 */}
          {block.has_fba
            ? <span className="ps-d">期末 <b data-testid="sum-inventory"><Qty v={closingOfLast(block, periods)} /></b></span>
            : <span className="ps-d">无 FBA</span>}
        </div>
      </div>
    );
  }

  function mskuLine(r: MskuRow, c: MskuCell | undefined, p: Period): ReactNode {
    const v = mskuStockAt(r, p);
    const f = r.forecast.find((x) => x.period === p);
    return (
      <span className="ps-d">
        <span>预测 <b data-testid={`msku-system-${r.seller_sku}-${r.sid}-${p}`}>{c?.system_na ? '不适用' : fmt(c?.system_units ?? null)}</b></span>
        <span title={v.kind === 'unknown' ? unknownWhy(f?.reason) : undefined}>
          库存 <b data-testid={`msku-stock-${r.seller_sku}-${r.sid}-${p}`}>{v.kind === 'na' ? '无 FBA' : <Qty v={v} />}</b>
        </span>
      </span>
    );
  }

  function mskuRow(row: Extract<SheetRow, { type: 'msku' }>, i: number): ReactNode {
    const r = row.row;
    return (
      <div key={`m|${r.seller_sku}|${r.sid}|${row.block.sku}`} className="ps-row ps-row--msku" role="row" style={rowStyle(i)}>
        <div className="ps-rh ps-rh--msku" role="rowheader">
          <span className="ps-sub--mono">{r.seller_sku}</span>
          <button type="button" className="pm-rm" aria-label="移出 msku" title={`把 ${r.seller_sku} 移出这张计划`}
            disabled={pending.has(mskuKey(r.seller_sku, r.sid))} onClick={() => props.onRemoveMsku(r.seller_sku, r.sid)}>移出</button>
        </div>
        {periods.map((p, ci) => {
          const c = cellOf(r, p);
          const k = demandKey(r.seller_sku, r.sid, p);
          return editableCell({
            key: p, n: { kind: 'demand', block: row.block, row: r }, ni: row.nav, p, ci,
            label: `期望销量 ${r.seller_sku}`, testId: `cell-${r.seller_sku}-${r.sid}-${p}`, human: c?.basis === 'human',
            title: c?.system_na ? '无销量取数源，预测不适用' : c?.system_units != null ? `系统预估 ${c.system_units}` : '没有预估，需要人填',
            value: demandValue(c, pending.has(k) ? drafts[k] : undefined),
            sub: mskuLine(r, c, p),
          });
        })}
        <div className="ps-sum"><span className="ps-v"><Qty v={sumUnits(r.cells.map((c) => c.effective_units))} /></span></div>
      </div>
    );
  }

  // ── 只画看得见的行；按货号 / 店铺套上 display:contents 的分组，行仍然相对整表定位 ──
  const first = Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((scrollTop + viewH) / ROW) + OVERSCAN);
  const body: ReactNode[] = [];
  for (let i = first; i < last;) {
    const card = rows[i]!.card;
    const inCard: ReactNode[] = [];
    while (i < last && rows[i]!.card === card) {
      const row = rows[i]!;
      if (row.type === 'sku') { inCard.push(skuRow(row, i)); i++; continue; }
      const b = row.block;
      const inBlock: ReactNode[] = [];
      while (i < last && rows[i]!.type !== 'sku' && (rows[i] as { block: SkuBlock }).block === b) {
        const rr = rows[i]!;
        inBlock.push(rr.type === 'store' ? storeRow(rr, i) : mskuRow(rr as Extract<SheetRow, { type: 'msku' }>, i));
        i++;
      }
      inCard.push(
        <div key={`g|${blockKey(b)}`} className="ps-group" data-testid={`block-${b.sid}-${b.sku}`} data-open={isOpen(b) ? 'true' : 'false'}>
          {inBlock}
        </div>,
      );
    }
    body.push(<div key={`c|${card.sku}`} className="ps-group" aria-label={card.sku} data-testid={`card-${card.sku}`}>{inCard}</div>);
  }

  const seg = <T extends string>(value: T, set: (v: T) => void, opts: { v: T; l: string; n?: number; tone?: 'danger' | 'warn' }[], label: string) => (
    <div className="pm-seg" role="group" aria-label={label}>
      {opts.map((o) => (
        <button key={o.v} type="button" aria-pressed={value === o.v} onClick={() => set(o.v)}>
          {o.l}
          {o.n !== undefined && <span className={`pm-seg__n${o.tone && o.n > 0 ? ` pm-seg__n--${o.tone}` : ''}`}>{o.n}</span>}
        </button>
      ))}
    </div>
  );
  const anyFolded = shown.some((c) => foldedSkus.has(c.sku));

  return (
    <div className={`ps${density === 'compact' ? ' ps--compact' : ''}`} data-testid="plan-sheet">
      <div className="pm-bar">
        <div className="pm-search-wrap">
          <input className="pm-search" type="search" placeholder="搜索货号、店铺、msku" aria-label="搜索货号、店铺、msku"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {seg(filter, setFilter, [
          { v: 'all', l: '全部', n: tally.all },
          { v: 'out', l: '有断货', n: tally.out, tone: 'danger' },
          { v: 'todo', l: '有待填', n: tally.todo, tone: 'warn' },
          { v: 'low', l: '偏低', n: tally.low, tone: 'warn' },
        ], '筛选')}
        <label className="ps-sort">排序
          <select className="ps-select" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="risk">风险优先</option>
            <option value="sales">销量高→低</option>
            <option value="sku">货号</option>
          </select>
        </label>
        <span className="pm-bar__sp" />
        <button type="button" className="btn btn--ghost"
          onClick={() => setFoldedSkus(anyFolded ? new Set() : new Set(shown.map((c) => c.sku)))}>
          {anyFolded ? '全部展开' : '全部折叠成一行一个货号'}
        </button>
        <button type="button" className="btn" onClick={() => { focusGrid(); nextTodo(); }}>
          下一个待填<kbd className="pm-kbd">N</kbd>
        </button>
        {seg(density, setDensity, [{ v: 'comfortable', l: '舒适' }, { v: 'compact', l: '紧凑' }], '密度')}
      </div>

      <div className="pm-legend" aria-hidden>
        <span><i className="pm-sw" style={{ background: 'var(--primary)', clipPath: 'polygon(0 0,100% 0,0 100%)' }} />人填</span>
        <span><i className="pm-sw" style={{ background: 'var(--text-3)' }} />系统预估</span>
        <span><i className="pm-sw" style={{ background: 'var(--warn-soft)', boxShadow: 'inset 0 0 0 1px var(--warn)' }} />待填 / 库存偏低（&lt; {low}）</span>
        <span><i className="pm-sw" style={{ background: 'var(--danger-soft)', boxShadow: 'inset 0 0 0 1px var(--danger)' }} />断货</span>
        <span className="chip chip--dim">{`库存含计划采购到货（生产 ${model.params.make_days} 天 + 货运 ${model.params.ship_days} 天）`}</span>
        <span className="chip chip--dim">在途未分摊到店铺</span>
      </div>

      <div
        ref={scRef} className="ps-scroll" tabIndex={0} role="grid" aria-label="计划整表" aria-rowcount={rows.length + 1}
        onKeyDown={onKeyDown} onCopy={onCopy} onPaste={onPaste}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        <div className="ps-row ps-head" role="row" style={{ gridTemplateColumns: cols, height: HEAD_H, minWidth: minW }}>
          <div className="ps-rh" role="columnheader">货号 / 店铺 / MSKU</div>
          {periods.map((p) => {
            const m = monthLabel(p);
            return <div key={p} className="ps-hc" role="columnheader" aria-label={p}><span className="pm-mh__m">{m.m}</span><span className="pm-mh__y">{m.y}</span></div>;
          })}
          <div className="ps-hc ps-hc--sum" role="columnheader">合计</div>
        </div>
        <div className="ps-body" style={{ height: rows.length * ROW, minWidth: minW }}>{body}</div>
        {rows.length === 0 && (
          <div className="pm-empty">{cards.length === 0 ? '这张计划还没有货号，先去「添加货品」' : '没有符合筛选的货号'}</div>
        )}
      </div>

      <div className="pm-status" aria-live="polite">
        <span className="pm-status__sel">
          <b>{shown.length}</b> 个货号，<b>{rows.length}</b> 行
          {selInfo && (
            <>
              {'　'}已选 <b>{selInfo.n}</b> 格，合计 <b>{selInfo.unknown ? '—' : selInfo.sum.toLocaleString('en-US')}</b>
              {selInfo.unknown > 0 && `（${selInfo.unknown} 格待填）`}
            </>
          )}
        </span>
        <span className="pm-status__keys">
          <span><kbd className="pm-kbd">0-9</kbd>直接改</span>
          <span><kbd className="pm-kbd">N</kbd>下一个待填</span>
          <span><kbd className="pm-kbd">空格</kbd>折叠货号</span>
          <span><kbd className="pm-kbd">Del</kbd>清掉人填</span>
          <span><kbd className="pm-kbd">⌘V</kbd>粘贴 Excel</span>
          <span><kbd className="pm-kbd">⌘D</kbd><kbd className="pm-kbd">⌘R</kbd>填充</span>
        </span>
      </div>
    </div>
  );
}

/** 采购合计：只加已填的月；一个都没有才是未知 */
function known(values: (number | null)[]): QtyValue {
  let s = 0; let any = false;
  for (const v of values) if (v !== null) { any = true; s += v; }
  return any ? { kind: 'num', value: s } : { kind: 'unknown' };
}
