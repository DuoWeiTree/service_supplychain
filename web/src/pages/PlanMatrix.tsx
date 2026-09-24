import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Qty, type QtyValue } from '../components/Qty';
import type { Period, Sid } from '../api/types';
import {
  closingOfLast, demandAt, inventoryAt, sumUnits,
  type GridModel, type MskuCell, type MskuRow, type SkuBlock,
} from './planGridModel';
import { buildCards, cardMatches, monthLabel, stockState, summarize, type SkuCard } from './planMatrixModel';
import { demandKey, mskuKey, purchaseKey, skuKey } from './usePlanGridSaves';

/** ★ 设计取值 · 未实测：期末低于这个件数就铺「偏低」底色。
 *  没有任何一条口径指到这个 50 —— 它只改底色，不参与任何一个数。 */
export const LOW_STOCK_UNITS = 50;

export interface PlanMatrixProps {
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

/** 可以落光标的一行：单 msku 的店铺行 / msku 子行 / 采购行 */
type NavRow =
  | { kind: 'demand'; block: SkuBlock; row: MskuRow }
  | { kind: 'purchase'; card: SkuCard };

type Filter = 'all' | 'out' | 'todo';
type Density = 'comfortable' | 'compact';

function cellOf(row: MskuRow, p: Period): MskuCell | undefined { return row.cells.find((c) => c.period === p); }

export function PlanMatrix(props: PlanMatrixProps) {
  const { model, pending, inputErr } = props;
  const low = props.lowStock ?? LOW_STOCK_UNITS;
  const periods = model.periods;

  const cards = useMemo(() => buildCards(model), [model]);
  const sums = useMemo(() => new Map(cards.map((c) => [c.sku, summarize(c, periods, low)])), [cards, periods, low]);

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [density, setDensity] = useState<Density>('comfortable');
  // ★ 多 msku 的店铺默认展开：要填的数就在 msku 那一层，收着等于让人多点一下
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [cur, setCur] = useState({ r: 0, c: 0 });
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [editing, setEditingState] = useState<{ draft: string; mode: 'replace' | 'edit' } | null>(null);
  // 在飞期间显示人刚填的数，落地后以服务端重载的为准
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const editRef = useRef<typeof editing>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const setEditing = (v: typeof editing) => { editRef.current = v; setEditingState(v); };

  useEffect(() => {
    const up = () => { dragging.current = false; };
    document.addEventListener('mouseup', up);
    return () => document.removeEventListener('mouseup', up);
  }, []);

  const counts = {
    all: cards.length,
    out: cards.filter((c) => (sums.get(c.sku)?.outs.length ?? 0) > 0).length,
    todo: cards.filter((c) => (sums.get(c.sku)?.todo ?? 0) > 0).length,
  };
  const shown = cards.filter((c) => {
    const s = sums.get(c.sku)!;
    if (filter === 'out' && s.outs.length === 0) return false;
    if (filter === 'todo' && s.todo === 0) return false;
    return cardMatches(c, query);
  });

  const blockKey = (b: SkuBlock) => `${b.sid}-${b.sku}`;
  const isOpen = (b: SkuBlock) => b.mskus.length > 1 && !collapsed.has(blockKey(b));

  // ── 导航网格 ──
  const nav: NavRow[] = [];
  const navIndex = new Map<string, number>();
  for (const card of shown) {
    for (const b of card.stores) {
      const rows = b.mskus.length === 1 ? b.mskus : isOpen(b) ? b.mskus : [];
      for (const r of rows) { navIndex.set(`d:${r.seller_sku}:${r.sid}`, nav.length); nav.push({ kind: 'demand', block: b, row: r }); }
    }
    if (card.purchase) { navIndex.set(`p:${card.sku}`, nav.length); nav.push({ kind: 'purchase', card }); }
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

  function moveTo(r: number, c: number, extend = false) {
    const t = { r: Math.max(0, Math.min(nav.length - 1, r)), c: Math.max(0, Math.min(periods.length - 1, c)) };
    setCur(t); if (!extend) setAnchor(t);
  }
  const focusGrid = () => wrapRef.current?.focus({ preventScroll: true });

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
      if (nr < 0 || nr >= nav.length) return;           // 让焦点离开矩阵
      e.preventDefault(); moveTo(nr, nc);
    }
    else if (k === 'Enter' || k === 'F2') { e.preventDefault(); startEdit('edit'); }
    else if (k === 'Delete' || k === 'Backspace') {
      e.preventDefault();
      eachSel((n, p) => { if (humanOf(n, p) !== null) save(n, p, ''); });
    }
    else if (mod && (k === 'd' || k === 'D')) {
      e.preventDefault();
      for (let c = sel.c0; c <= sel.c1; c++) {
        const p = periods[c]; const top = nav[sel.r0]; if (!p || !top) continue;
        const v = shownOf(top, p);
        for (let r = sel.r0 + 1; r <= sel.r1; r++) { const n = nav[r]; if (n && shownOf(n, p) !== v) save(n, p, v === null ? '' : String(v)); }
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
    // 任何可打印字符都进入编辑：非法输入（5oo、-5）也要走到校验那一步并被点名，不是被键盘吞掉
    else if (!mod && !e.altKey && k.length === 1 && k !== ' ') { e.preventDefault(); startEdit('replace', k); }
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
    const rows = text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((l) => l.split('\t'));
    const single = rows.length === 1 && rows[0]!.length === 1;
    const r1 = single ? sel.r1 : Math.min(nav.length - 1, sel.r0 + rows.length - 1);
    const c1 = single ? sel.c1 : Math.min(periods.length - 1, sel.c0 + rows[0]!.length - 1);
    for (let r = sel.r0; r <= r1; r++) for (let c = sel.c0; c <= c1; c++) {
      const raw = single ? rows[0]![0] : rows[r - sel.r0]?.[c - sel.c0];
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
    key: string; n: NavRow; p: Period; ci: number; label: string; testId?: string;
    stock?: string | undefined; human: boolean; title?: string | undefined; children: ReactNode;
  }) {
    const ni = navIndex.get(args.n.kind === 'purchase' ? `p:${args.n.card.sku}` : `d:${args.n.row.seller_sku}:${args.n.row.sid}`);
    const k = keyOf(args.n, args.p);
    const isCur = ni === cr && args.ci === cur.c;
    const isEdit = isCur && editing !== null;
    const busy = pending.has(k);
    const err = inputErr[k];
    const selected = ni !== undefined && inSel(ni, args.ci);
    return (
      <td
        key={args.key}
        role="gridcell"
        aria-label={`${args.label} ${args.p}`}
        aria-selected={selected}
        aria-busy={busy || undefined}
        data-testid={args.testId}
        data-stock={args.stock}
        data-state={args.stock}
        title={args.title}
        className={['pm-c', selected && 'is-sel', isCur && 'is-cur', isEdit && 'is-edit',
          args.human && 'is-human', busy && 'is-pending', err && 'is-err'].filter(Boolean).join(' ')}
        onMouseDown={(e) => {
          if (isEdit || ni === undefined) return;
          if (editRef.current) commit();
          e.preventDefault(); dragging.current = true; moveTo(ni, args.ci, e.shiftKey); focusGrid();
        }}
        onMouseEnter={() => { if (dragging.current && ni !== undefined) setCur({ r: ni, c: args.ci }); }}
        onDoubleClick={() => startEdit('edit')}
      >
        {isEdit ? (
          <div className="pm-v">
            <input
              className="pm-in" autoFocus inputMode="numeric" value={editing.draft}
              aria-label={`${args.label} ${args.p}`}
              onChange={(e) => setEditing({ draft: e.target.value, mode: editing.mode })}
              onKeyDown={onInputKey}
              onBlur={() => commit()}
            />
          </div>
        ) : args.children}
        {err && <span className="pm-err" role="alert" data-testid={errTestId(args.n, args.p)}>{err}</span>}
      </td>
    );
  }

  function errTestId(n: NavRow, p: Period) {
    return n.kind === 'purchase' ? `err-purchase-${n.card.sku}-${p}` : `err-${n.row.seller_sku}-${n.row.sid}-${p}`;
  }

  function demandValue(c: MskuCell | undefined, draft: string | undefined, testId?: string): ReactNode {
    if (draft !== undefined) {
      return <div className="pm-v pm-v--human"><span className="pm-v__n" data-testid={testId}>{draft === '' ? '—' : draft}</span></div>;
    }
    if (!c || c.effective_units === null) {
      return <div className="pm-v pm-v--todo"><span className="pm-v__n" data-testid={testId}>待填</span></div>;
    }
    return (
      <div className={`pm-v pm-v--${c.basis === 'human' ? 'human' : 'est'}`}>
        {c.system_extrapolated && c.basis !== 'human' && <span className="pm-v__tag" aria-label="系统外推">外推</span>}
        <span className="pm-v__n" data-testid={testId}>{c.effective_units.toLocaleString('en-US')}</span>
      </div>
    );
  }

  function stockLine(v: QtyValue, block: SkuBlock, p: Period): ReactNode {
    if (v.kind === 'na') return <div className="pm-d" data-testid="closing-na">无 FBA</div>;
    const s = stockState(v, low);
    const word = s === 'out' ? '断货' : s === 'low' ? '偏低' : '库存';
    const inv = block.inventory.find((i) => i.period === p);
    const why = inv?.basis.closing_reason === 'unknown_onhand' ? '没有在仓数'
      : inv?.basis.closing_reason === 'unknown_demand' ? '当月销量待填' : undefined;
    return (
      <div className="pm-d" title={why}>
        {word} <b data-testid="closing-sku">{v.kind === 'num' ? v.value.toLocaleString('en-US') : '—'}</b>
      </div>
    );
  }

  function stockTitle(block: SkuBlock, p: Period): string | undefined {
    const inv = block.inventory.find((i) => i.period === p);
    if (!inv || inv.basis.closing_reason === 'not_applicable') return undefined;
    return `期初 ${inv.onhand ?? '—'} · 数据截至 ${inv.basis.as_of} · 不含本计划采购`;
  }

  function storeRows(block: SkuBlock): ReactNode {
    const multi = block.mskus.length > 1;
    const open = isOpen(block);
    const single = multi ? null : block.mskus[0]!;
    const bKey = blockKey(block);
    const head = (
      <th className="pm-rh" scope="row">
        <div className="pm-who">
          {multi ? (
            <button
              type="button" className="pm-tw" aria-expanded={open}
              aria-label={`${open ? '折叠' : '展开'} ${block.seller_name} 的 ${block.sku}`}
              onClick={() => setCollapsed((s) => { const n = new Set(s); if (n.has(bKey)) n.delete(bKey); else n.add(bKey); return n; })}
            >›</button>
          ) : <span className="pm-tw pm-tw--none" />}
          <span className="pm-who__name">{block.seller_name}</span>
          <button
            type="button" className="pm-rm" aria-label="移出货号" title={`把 ${block.seller_name} 的 ${block.sku} 移出这张计划`}
            disabled={pending.has(skuKey(block))} onClick={() => props.onRemoveStore(block)}
          >移出</button>
        </div>
        <div className={`pm-who__sub${multi ? ' pm-who__sub--plain' : ''}`}>
          {multi ? `${block.mskus.length} 个 msku 合计` : single!.seller_sku}
        </div>
      </th>
    );
    return (
      <>
        <tr className="pm-row--store">
          {head}
          {periods.map((p, ci) => {
            const inv = inventoryAt(block, p);
            const stock = stockState(inv, low);
            if (single) {
              const n: NavRow = { kind: 'demand', block, row: single };
              const c = cellOf(single, p);
              const k = demandKey(single.seller_sku, single.sid, p);
              return (
                editableCell({ key: p, n: n, p: p, ci: ci, label: `期望销量 ${single.seller_sku}`, testId: `sku-cell-${p}`, stock: stock, human: c?.basis === 'human', title: stockTitle(block, p), children: (<>
                  <div data-testid={`cell-${single.seller_sku}-${single.sid}-${p}`}>
                    {demandValue(c, pending.has(k) ? drafts[k] : undefined, `sum-expected-${p}`)}
                  </div>
                  {stockLine(inv, block, p)}
                </>) })
              );
            }
            const d = demandAt(block, p);
            const anyHuman = block.mskus.some((r) => cellOf(r, p)?.basis === 'human');
            return (
              <td key={p} className="pm-c is-ro" data-testid={`sku-cell-${p}`} data-stock={stock} data-state={stock}
                title={stockTitle(block, p)}>
                <div className={`pm-v pm-v--${d.kind !== 'num' ? 'todo' : anyHuman ? 'human' : 'est'}`}>
                  <span className="pm-v__n" data-testid={`sum-expected-${p}`}>
                    {d.kind === 'num' ? d.value.toLocaleString('en-US') : '待填'}
                  </span>
                </div>
                {stockLine(inv, block, p)}
              </td>
            );
          })}
          <td className="pm-sumc">
            <div className="pm-v" data-testid="sum-demand">
              <Qty v={sumUnits(block.mskus.flatMap((r) => r.cells.map((c) => c.effective_units)))} />
            </div>
            {/* ★ 存量不求和，给最后一个月的期末：onhand 是一条链，相加等于把同一批货数三遍 */}
            {block.has_fba && (
              <div className="pm-d">期末 <b data-testid="sum-inventory"><Qty v={closingOfLast(block, periods)} /></b></div>
            )}
          </td>
        </tr>
        {open && block.mskus.map((r) => {
          const n: NavRow = { kind: 'demand', block, row: r };
          return (
            <tr key={r.seller_sku} className="pm-row--msku">
              <th className="pm-rh" scope="row">
                <div className="pm-who">
                  <span className="pm-who__name">{r.seller_sku}</span>
                  <button type="button" className="pm-rm" aria-label="移出 msku" title={`把 ${r.seller_sku} 移出这张计划`}
                    disabled={pending.has(mskuKey(r.seller_sku, r.sid))} onClick={() => props.onRemoveMsku(r.seller_sku, r.sid)}>移出</button>
                </div>
              </th>
              {periods.map((p, ci) => {
                const c = cellOf(r, p);
                const k = demandKey(r.seller_sku, r.sid, p);
                return (
                  editableCell({ key: p, n: n, p: p, ci: ci, label: `期望销量 ${r.seller_sku}`, testId: `cell-${r.seller_sku}-${r.sid}-${p}`, human: c?.basis === 'human', title: c?.system_units != null ? `系统预估 ${c.system_units}` : '没有预估，需要人填', children: (<>
                    {demandValue(c, pending.has(k) ? drafts[k] : undefined)}
                  </>) })
                );
              })}
              <td className="pm-sumc"><div className="pm-v"><Qty v={sumUnits(r.cells.map((c) => c.effective_units))} /></div></td>
            </tr>
          );
        })}
      </>
    );
  }

  function purchaseRow(card: SkuCard): ReactNode {
    const transitAt = (p: Period) => card.transit?.cells.find((c) => c.period === p)?.units ?? null;
    const transitLine = (p: Period) => {
      const t = transitAt(p);
      return <div className="pm-d">在途 <b data-testid={`transit-${card.sku}-${p}`}>{t === null ? '—' : t.toLocaleString('en-US')}</b></div>;
    };
    const head = (
      <th className="pm-rh" scope="row">
        <div className="pm-who"><span className="pm-ico" aria-hidden>采</span><span className="pm-who__name">计划采购</span></div>
        <div className="pm-who__sub pm-who__sub--plain">按货号，不分店铺</div>
      </th>
    );
    const buy = card.purchase;
    return (
      <tr className="pm-row--buy" data-testid={`purchase-row-${card.sku}`}>
        {head}
        {periods.map((p, ci) => {
          if (!buy) return <td key={p} className="pm-c is-ro"><div className="pm-v pm-v--none"><span className="pm-v__n">—</span></div>{transitLine(p)}</td>;
          const n: NavRow = { kind: 'purchase', card };
          const k = purchaseKey(card.sku, p);
          const v = buy.cells.find((c) => c.period === p)?.planned_units ?? null;
          const draft = pending.has(k) ? drafts[k] : undefined;
          const txt = draft !== undefined ? (draft === '' ? '—' : draft) : v === null ? '—' : v.toLocaleString('en-US');
          return (
            editableCell({ key: p, n: n, p: p, ci: ci, label: `计划采购量 ${card.sku}`, human: v !== null, children: (<>
              <div className={`pm-v ${v === null && draft === undefined ? 'pm-v--none' : 'pm-v--human'}`}><span className="pm-v__n">{txt}</span></div>
              {transitLine(p)}
            </>) })
          );
        })}
        <td className="pm-sumc">
          <div className="pm-v"><Qty v={known(buy?.cells.map((c) => c.planned_units) ?? [])} /></div>
          <div className="pm-d">在途 <b><Qty v={known(card.transit?.cells.map((c) => c.units) ?? [])} /></b></div>
        </td>
      </tr>
    );
  }

  function totalRow(card: SkuCard): ReactNode {
    const per = periods.map((p) => sumUnits(card.stores.map((b) => { const d = demandAt(b, p); return d.kind === 'num' ? d.value : null; })));
    const all = sumUnits(per.map((v) => (v.kind === 'num' ? v.value : null)));
    return (
      <tr className="pm-row--total">
        <th className="pm-rh" scope="row"><div className="pm-who"><span className="pm-tw pm-tw--none" /><span className="pm-who__name">各店合计</span></div></th>
        {per.map((v, i) => <td key={periods[i]} className="pm-c is-ro"><div className="pm-v"><Qty v={v} /></div></td>)}
        <td className="pm-sumc"><div className="pm-v"><Qty v={all} /></div></td>
      </tr>
    );
  }

  const kpi = (label: string, v: number | null) => (
    <div className="pm-kpi">
      <span className="pm-kpi__l">{label}</span>
      <span className={`pm-kpi__v${v === null ? ' pm-kpi__v--muted' : ''}`}>{v === null ? '—' : v.toLocaleString('en-US')}</span>
    </div>
  );

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

  const multiBlocks = model.blocks.filter((b) => b.mskus.length > 1);
  const allOpen = multiBlocks.length > 0 && multiBlocks.every((b) => !collapsed.has(blockKey(b)));

  return (
    <div className={`pm${density === 'compact' ? ' pm--compact' : ''}`} data-testid="plan-matrix">
      <div className="pm-bar">
        <div className="pm-search-wrap">
          <input className="pm-search" type="search" placeholder="搜索货号、店铺、msku" aria-label="搜索货号、店铺、msku"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {seg(filter, setFilter, [
          { v: 'all', l: '全部', n: counts.all },
          { v: 'out', l: '有断货', n: counts.out, tone: 'danger' },
          { v: 'todo', l: '有待填', n: counts.todo, tone: 'warn' },
        ], '筛选')}
        {multiBlocks.length > 0 && (
          <button type="button" className="btn btn--ghost"
            onClick={() => setCollapsed(allOpen ? new Set(multiBlocks.map(blockKey)) : new Set())}>
            {allOpen ? '全部收起 msku' : '全部展开 msku'}
          </button>
        )}
        <span className="pm-bar__sp" />
        {seg(density, setDensity, [{ v: 'comfortable', l: '舒适' }, { v: 'compact', l: '紧凑' }], '密度')}
      </div>

      <div className="pm-legend" aria-hidden>
        <span><i className="pm-sw" style={{ background: 'var(--primary)', clipPath: 'polygon(0 0,100% 0,0 100%)' }} />人填</span>
        <span><i className="pm-sw" style={{ background: 'var(--text-3)' }} />系统预估</span>
        <span><i className="pm-sw" style={{ background: 'var(--warn-soft)', boxShadow: 'inset 0 0 0 1px var(--warn)' }} />待填 / 库存偏低（&lt; {low}）</span>
        <span><i className="pm-sw" style={{ background: 'var(--danger-soft)', boxShadow: 'inset 0 0 0 1px var(--danger)' }} />断货</span>
        <span className="chip chip--dim">未计本计划采购</span>
        <span className="chip chip--dim">在途未分摊到店铺</span>
      </div>

      <div
        ref={wrapRef} className="pm-blocks" tabIndex={0} role="grid" aria-label="计划矩阵"
        onKeyDown={onKeyDown} onCopy={onCopy} onPaste={onPaste} style={{ outline: 'none' }}
      >
        {shown.length === 0 && (
          <div className="pm-empty">{cards.length === 0 ? '这张计划还没有货号，先去「添加货品」' : '没有符合筛选的货号'}</div>
        )}
        {shown.map((card) => {
          const s = sums.get(card.sku)!;
          return (
            <section key={card.sku} className="pm-block" aria-label={card.sku} data-testid={`card-${card.sku}`}>
              <div className="pm-block__head">
                <div className="pm-block__id"><span className="pm-block__sku">{card.sku}</span></div>
                <div className="pm-kpis">{kpi('销量', s.demandKnown)}{kpi('计划采购', s.purchase)}{kpi('在途', s.transit)}</div>
                <div className="pm-block__flags">
                  {s.outs.map((o) => (
                    <span key={`o${o.block.sid}`} className="chip chip--bad" data-testid={`outage-${o.block.sid}-${o.block.sku}`}>
                      {o.block.seller_name} 断货 {o.months} 个月
                    </span>
                  ))}
                  {s.lows.map((o) => <span key={`l${o.block.sid}`} className="chip chip--warn">{o.block.seller_name} 库存偏低</span>)}
                  {s.todo > 0 && <span className="chip chip--warn">{s.todo} 格销量待填</span>}
                </div>
              </div>
              <div className="pm-scroll">
                <table className="pm-t">
                  <thead>
                    <tr>
                      <th className="pm-rh">店铺 / MSKU</th>
                      {periods.map((p) => {
                        const m = monthLabel(p);
                        return <th key={p} aria-label={p}><span className="pm-mh"><span className="pm-mh__m">{m.m}</span><span className="pm-mh__y">{m.y}</span></span></th>;
                      })}
                      <th className="pm-sumc">合计</th>
                    </tr>
                  </thead>
                  {card.stores.map((b) => (
                    <tbody key={b.sid} data-testid={`block-${b.sid}-${b.sku}`} data-open={isOpen(b) ? 'true' : 'false'}>
                      {storeRows(b)}
                    </tbody>
                  ))}
                  <tbody data-testid={`purchase-${card.sku}`}>
                    {purchaseRow(card)}
                    {card.stores.length > 1 && totalRow(card)}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })}
      </div>

      <div className="pm-status" aria-live="polite">
        {selInfo && (
          <span className="pm-status__sel">
            已选 <b>{selInfo.n}</b> 格，合计 <b>{selInfo.unknown ? '—' : selInfo.sum.toLocaleString('en-US')}</b>
            {selInfo.unknown > 0 && `（${selInfo.unknown} 格待填）`}
          </span>
        )}
        <span className="pm-status__keys">
          <span><kbd className="pm-kbd">0-9</kbd>直接改</span>
          <span><kbd className="pm-kbd">↵</kbd>编辑并下移</span>
          <span><kbd className="pm-kbd">Del</kbd>清掉人填</span>
          <span><kbd className="pm-kbd">⌘V</kbd>粘贴 Excel</span>
          <span><kbd className="pm-kbd">⌘D</kbd><kbd className="pm-kbd">⌘R</kbd>填充</span>
        </span>
      </div>
    </div>
  );
}

/** 采购 / 在途合计：只加已填的月；一个都没有才是未知 */
function known(values: (number | null)[]): QtyValue {
  let s = 0; let any = false;
  for (const v of values) if (v !== null) { any = true; s += v; }
  return any ? { kind: 'num', value: s } : { kind: 'unknown' };
}

