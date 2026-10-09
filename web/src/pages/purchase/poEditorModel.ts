import type { CandidateLine, PoBody, PoLineInput, PreflightGate, PurchaseOrder } from '../../api/types';

export type QcType = 0 | 1 | 2;
export interface DraftSource { line_id: number; seller_id: string; units: number }
export interface DraftLine {
  sku: string;
  /** '' = 还没选仓 */
  wid: string;
  /** null = 跟随来源合计；人改过才有数 */
  units: number | null;
  price: string;
  expect_arrive_date: string;
  unplanned_reason: string;
  sources: DraftSource[];
}
export interface DraftHeader { supplier_id: string; purchaser_id: string; opt_uid: string; qc_type: QcType | null }
export interface Draft { header: DraftHeader; lines: DraftLine[] }

export const QC_LABEL: Record<QcType, string> = { 0: '仓库质检', 1: '预检', 2: '免检' };

export function emptyDraft(): Draft {
  return { header: { supplier_id: '', purchaser_id: '', opt_uid: '', qc_type: null }, lines: [] };
}

export function setHeader(d: Draft, patch: Partial<DraftHeader>): Draft {
  return { ...d, header: { ...d.header, ...patch } };
}

export const sourcesUnits = (l: DraftLine): number => l.sources.reduce((a, s) => a + s.units, 0);
export const lineUnits = (l: DraftLine): number => l.units ?? sourcesUnits(l);
export const unplannedUnits = (l: DraftLine): number => lineUnits(l) - sourcesUnits(l);

const key = (lineId: number, sid: string) => `${lineId}|${sid}`;

/** 记录级剩余，同 available 的口径：后端 remaining 已扣本单已保存的量，加回后再扣本稿 */
export function recordRemaining(c: Pick<CandidateLine, 'line_id' | 'remaining'>, d: Draft, base: Baseline): number {
  let saved = 0;
  for (const [k, v] of base) if (k.startsWith(`${c.line_id}|`)) saved += v;
  let now = 0;
  for (const ln of d.lines) for (const s of ln.sources) if (s.line_id === c.line_id) now += s.units;
  return c.remaining + saved - now;
}

/** 已保存草稿自己占着的量。★ 后端 sellers[].used 已含本单，不加回来就会把自己算成别人 */
export type Baseline = ReadonlyMap<string, number>;
export function baselineOf(po: Pick<PurchaseOrder, 'lines'> | null): Baseline {
  const m = new Map<string, number>();
  for (const ln of po?.lines ?? []) for (const s of ln.sources) {
    m.set(key(s.line_id, s.seller_id), (m.get(key(s.line_id, s.seller_id)) ?? 0) + s.units);
  }
  return m;
}

export function inDraft(d: Draft, lineId: number, sid: string): number {
  let n = 0;
  for (const ln of d.lines) for (const s of ln.sources) if (s.line_id === lineId && s.seller_id === sid) n += s.units;
  return n;
}

/** 能往里加来源的东西：候选记录，或候选里已不列出的「本单原有来源」 */
export type SourceTarget = Pick<CandidateLine, 'line_id' | 'sku' | 'blocked' | 'sellers'>;

/** null = 剩余未知（无分店依据），不是 0 */
export function available(c: Pick<SourceTarget, 'line_id' | 'sellers'>, sid: string, d: Draft, base: Baseline): number | null {
  const r = c.sellers[sid]?.remaining;
  if (r === null || r === undefined) return null;
  return r + (base.get(key(c.line_id, sid)) ?? 0) - inDraft(d, c.line_id, sid);
}

function mergeSources(a: DraftSource[], b: DraftSource[]): DraftSource[] {
  const out = a.map((s) => ({ ...s }));
  for (const s of b) {
    const hit = out.find((x) => x.line_id === s.line_id && x.seller_id === s.seller_id);
    if (hit) hit.units += s.units; else out.push({ ...s });
  }
  return out;
}

function mergeLines(a: DraftLine, b: DraftLine): DraftLine {
  return {
    ...a,
    units: a.units === null && b.units === null ? null : lineUnits(a) + lineUnits(b),
    price: a.price || b.price,
    expect_arrive_date: a.expect_arrive_date || b.expect_arrive_date,
    unplanned_reason: a.unplanned_reason || b.unplanned_reason,
    sources: mergeSources(a.sources, b.sources),
  };
}

export type AddRefusal = 'blocked' | 'no_seller' | 'bad_units' | 'over_remaining';
export type AddResult = { ok: true; draft: Draft } | { ok: false; reason: AddRefusal; available?: number };

/** `wid` 不给：并进该货号的第一行，没有就新开一行（仓待选） */
export function addSource(d: Draft, c: SourceTarget, sid: string, units: number, base: Baseline, wid?: string): AddResult {
  if (c.blocked) return { ok: false, reason: 'blocked' };
  if (!(sid in c.sellers)) return { ok: false, reason: 'no_seller' };
  if (!Number.isInteger(units) || units <= 0) return { ok: false, reason: 'bad_units' };
  const avail = available(c, sid, d, base);
  if (avail === null) return { ok: false, reason: 'blocked' };
  if (units > avail) return { ok: false, reason: 'over_remaining', available: avail };
  const src: DraftSource = { line_id: c.line_id, seller_id: sid, units };
  const idx = d.lines.findIndex((l) => l.sku === c.sku && (wid === undefined || l.wid === wid));
  if (idx < 0) {
    const line: DraftLine = { sku: c.sku, wid: wid ?? '', units: null, price: '', expect_arrive_date: '',
      unplanned_reason: '', sources: [src] };
    return { ok: true, draft: { ...d, lines: [...d.lines, line] } };
  }
  const lines = d.lines.map((l, i) => (i === idx ? { ...l, sources: mergeSources(l.sources, [src]) } : l));
  return { ok: true, draft: { ...d, lines } };
}

export function removeSource(d: Draft, lineIdx: number, srcIdx: number): Draft {
  const lines: DraftLine[] = [];
  d.lines.forEach((l, i) => {
    if (i !== lineIdx) { lines.push(l); return; }
    const sources = l.sources.filter((_, j) => j !== srcIdx);
    // 人改过数量的行留着：那部分是无计划来源，不能随来源一起悄悄消失
    if (sources.length === 0 && l.units === null) return;
    lines.push({ ...l, sources });
  });
  return { ...d, lines };
}

export function setLineWid(d: Draft, idx: number, wid: string): Draft {
  const cur = d.lines[idx];
  if (!cur) return d;
  const moved = { ...cur, wid };
  const twin = d.lines.findIndex((l, i) => i !== idx && l.sku === cur.sku && l.wid === wid);
  if (twin < 0) return { ...d, lines: d.lines.map((l, i) => (i === idx ? moved : l)) };
  // 同 (货号, 仓) 一单只能一行（后端 duplicate_line）：并进先出现的那一行
  const [keep, drop] = twin < idx ? [twin, idx] : [idx, twin];
  const merged = keep === idx ? mergeLines(moved, d.lines[twin]!) : mergeLines(d.lines[twin]!, moved);
  return { ...d, lines: d.lines.flatMap((l, i) => (i === keep ? [merged] : i === drop ? [] : [l])) };
}

export function updateLine(
  d: Draft, idx: number,
  patch: Partial<Pick<DraftLine, 'units' | 'price' | 'expect_arrive_date' | 'unplanned_reason'>>,
): Draft {
  return { ...d, lines: d.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)) };
}

export function fromPo(po: Pick<PurchaseOrder, 'supplier_id' | 'purchaser_id' | 'opt_uid' | 'qc_type' | 'lines'>): Draft {
  return {
    header: { supplier_id: po.supplier_id, purchaser_id: po.purchaser_id, opt_uid: po.opt_uid, qc_type: po.qc_type },
    lines: po.lines.map((ln) => {
      const srcSum = ln.sources.reduce((a, s) => a + s.units, 0);
      return {
        sku: ln.sku, wid: ln.wid,
        units: ln.units === srcSum && ln.sources.length > 0 ? null : ln.units,
        price: ln.price ?? '', expect_arrive_date: ln.expect_arrive_date ?? '',
        unplanned_reason: ln.unplanned_reason ?? '',
        sources: ln.sources.map((s) => ({ line_id: s.line_id, seller_id: String(s.seller_id), units: s.units })),
      };
    }),
  };
}

const nonnegNumber = (s: string): boolean => s.trim() !== '' && Number.isFinite(Number(s)) && Number(s) >= 0;
const gate = (g: string, ok: boolean, detail: Record<string, unknown> = {}): PreflightGate => ({ gate: g, ok, detail });

/** 闸名与顺序照 rules/purchase.py::preflight；sources_orderable 要查记录状态，只有后端能判 */
export function validate(d: Draft): PreflightGate[] {
  const h = d.header;
  const out = [
    gate('supplier', !!h.supplier_id), gate('purchaser', !!h.purchaser_id), gate('opt_uid', !!h.opt_uid),
    gate('qc_type', h.qc_type !== null, { got: h.qc_type }), gate('lines', d.lines.length > 0),
  ];
  d.lines.forEach((l, i) => {
    const n = i + 1;
    const units = lineUnits(l);
    const src = sourcesUnits(l);
    out.push(gate('line_wid', !!l.wid, { line_no: n }));
    out.push(gate('line_price', nonnegNumber(l.price), { line_no: n }));
    out.push(gate('line_units', units >= src, { line_no: n, units, sources: src }));
    out.push(gate('line_unplanned_reason', units - src <= 0 || !!l.unplanned_reason.trim(),
      { line_no: n, unplanned: Math.max(units - src, 0) }));
  });
  return out;
}

const GATE_LABEL: Record<string, string> = {
  supplier: '供应商', purchaser: '采购方', opt_uid: '采购员', qc_type: '质检方式', lines: '至少一行明细',
  line_wid: '仓', line_price: '单价', line_units: '数量不少于来源合计', line_unplanned_reason: '无计划来源的理由',
  sources_orderable: '来源记录仍可组单',
};

export function gateText(g: PreflightGate): string {
  const label = GATE_LABEL[g.gate];
  if (label === undefined) return g.gate;
  const n = g.detail['line_no'];
  return typeof n === 'number' ? `第 ${n} 行${label}` : label;
}

export type BodyResult = { ok: true; body: PoBody } | { ok: false; missing: string[] };

/** 缺单头 / 没有明细时不出 body（qc_type 没选就没有合法值可发）；其余照发，由后端点名 */
export function toBody(d: Draft): BodyResult {
  const h = d.header;
  const missing = (['supplier_id', 'purchaser_id', 'opt_uid'] as const).filter((k) => !h[k].trim()) as string[];
  if (h.qc_type === null) missing.push('qc_type');
  if (d.lines.length === 0) missing.push('lines');
  if (missing.length > 0 || h.qc_type === null) return { ok: false, missing };
  return {
    ok: true,
    body: {
      supplier_id: h.supplier_id, purchaser_id: h.purchaser_id.trim(), opt_uid: h.opt_uid.trim(), qc_type: h.qc_type,
      lines: d.lines.map((l) => {
        const out: PoLineInput = {
          sku: l.sku, wid: l.wid, units: lineUnits(l), price: l.price.trim(),
          sources: l.sources.map((s) => ({ ...s })),
        };
        const reason = l.unplanned_reason.trim();
        if (unplannedUnits(l) > 0 && reason) out.unplanned_reason = reason;
        if (l.expect_arrive_date) out.expect_arrive_date = l.expect_arrive_date;
        return out;
      }),
    },
  };
}

export interface CandidateGroup { sku: string; records: CandidateLine[] }
export function groupCandidates(lines: CandidateLine[]): CandidateGroup[] {
  const m = new Map<string, CandidateLine[]>();
  for (const l of lines) m.set(l.sku, [...(m.get(l.sku) ?? []), l]);
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
    .map(([sku, records]) => ({
      sku, records: [...records].sort((a, b) => a.period.localeCompare(b.period) || a.line_id - b.line_id),
    }));
}

export interface OwnRecord { target: SourceTarget; saved: Record<string, number> }

/** 本单已保存的来源里、候选没列出的记录（本单把它组满了 ⇒ 后端按 fully_confirmed 滤掉）。
 *  ★ 不列出来，移除后到保存前就加不回去。remaining 记 0：可加的只有本单原有的量（下限，
 *  记录的完整分店余量要后端排除本单才算得出） */
export function ownOnlyRecords(po: Pick<PurchaseOrder, 'lines'> | null, listed: readonly { line_id: number }[]): OwnRecord[] {
  const shown = new Set(listed.map((l) => l.line_id));
  const m = new Map<number, OwnRecord>();
  for (const ln of po?.lines ?? []) for (const s of ln.sources) {
    if (shown.has(s.line_id)) continue;
    const sid = String(s.seller_id);
    const r = m.get(s.line_id) ?? { target: { line_id: s.line_id, sku: ln.sku, blocked: null, sellers: {} }, saved: {} };
    r.saved[sid] = (r.saved[sid] ?? 0) + s.units;
    r.target.sellers[sid] = { expected: null, cap: null, used: 0, remaining: 0 };
    m.set(s.line_id, r);
  }
  return [...m.values()].sort((a, b) => a.target.sku.localeCompare(b.target.sku) || a.target.line_id - b.target.line_id);
}
