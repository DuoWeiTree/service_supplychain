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
/** ★ 采购方 / 采购员 / 仓在稿里是**文本**（下拉值与手填框都是字符串），后端是整数：只在 fromPo / toBody 转 */
export interface DraftHeader { supplier_id: string; purchaser_id: string; opt_uid: string; qc_type: QcType | null }
/** `wid`：整张单的仓（PB-D25 一单一仓，单头选、写到每一行）；'' = 还没选。没有明细时也要记得住 */
export interface Draft { header: DraftHeader; lines: DraftLine[]; wid?: string }

export const QC_LABEL: Record<QcType, string> = { 0: '仓库质检', 1: '预检', 2: '免检' };

export function emptyDraft(): Draft {
  return { header: { supplier_id: '', purchaser_id: '', opt_uid: '', qc_type: null }, lines: [], wid: '' };
}

export function setHeader(d: Draft, patch: Partial<DraftHeader>): Draft {
  return { ...d, header: { ...d.header, ...patch } };
}

export const sourcesUnits = (l: DraftLine): number => l.sources.reduce((a, s) => a + s.units, 0);
export const lineUnits = (l: DraftLine): number => l.units ?? sourcesUnits(l);
export const unplannedUnits = (l: DraftLine): number => lineUnits(l) - sourcesUnits(l);

const key = (lineId: number, sid: string) => `${lineId}|${sid}`;

/** 记录级剩余（PB-D22：只卡总量）：后端 remaining 已扣本单已保存的量，加回后再扣本稿 */
export function recordRemaining(c: Pick<CandidateLine, 'line_id' | 'remaining'>, d: Draft, base: Baseline): number {
  let saved = 0;
  for (const [k, v] of base) if (k.startsWith(`${c.line_id}|`)) saved += v;
  let now = 0;
  for (const ln of d.lines) for (const s of ln.sources) if (s.line_id === c.line_id) now += s.units;
  return c.remaining + saved - now;
}

/** 已保存草稿自己占着的量。★ 后端 remaining / sellers[].used 已含本单，不加回来就会把自己算成别人 */
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
export type SourceTarget = Pick<CandidateLine, 'line_id' | 'sku' | 'remaining' | 'sellers'>;

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

export type AddRefusal = 'no_seller' | 'bad_units' | 'over_remaining';
export type AddResult = { ok: true; draft: Draft } | { ok: false; reason: AddRefusal; available?: number };

/** `wid` 不给：并进该货号的第一行，没有就新开一行（仓待选） */
export function addSource(d: Draft, c: SourceTarget, sid: string, units: number, base: Baseline, wid?: string): AddResult {
  if (!(sid in c.sellers)) return { ok: false, reason: 'no_seller' };
  if (!Number.isInteger(units) || units <= 0) return { ok: false, reason: 'bad_units' };
  const avail = recordRemaining(c, d, base);
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

/** 单头仓（PB-D25）：写到每一行。明细分属多个仓的旧草稿，选定后全单统一 */
export function setDraftWid(d: Draft, wid: string): Draft {
  return { ...d, wid, lines: d.lines.map((l) => ({ ...l, wid })) };
}

/** 明细里出现过的仓（去重排序）。多于一个 = 旧的多仓草稿，要人选一个统一 */
export function draftWids(d: Draft): string[] {
  return [...new Set(d.lines.map((l) => l.wid).filter((w) => w !== ''))].sort();
}

/** 把某条记录给某家店的件数**设为** units（绝对量，0 = 去掉这条来源）。
 *  可设的上限 = 记录剩余 + 本稿已给这家店的（改量不是叠加） */
export function setAllocation(d: Draft, c: SourceTarget, sid: string, units: number, base: Baseline): AddResult {
  if (!(sid in c.sellers)) return { ok: false, reason: 'no_seller' };
  if (!Number.isInteger(units) || units < 0) return { ok: false, reason: 'bad_units' };
  const cur = inDraft(d, c.line_id, sid);
  const room = recordRemaining(c, d, base) + cur;
  if (units > room) return { ok: false, reason: 'over_remaining', available: room };
  const hit = (s: DraftSource) => s.line_id === c.line_id && s.seller_id === sid;
  const at = d.lines.findIndex((l) => l.sources.some(hit));
  if (at >= 0) {
    const lines: DraftLine[] = [];
    d.lines.forEach((l, i) => {
      if (i !== at) { lines.push(l); return; }
      const sources = units > 0 ? l.sources.map((s) => (hit(s) ? { ...s, units } : s)) : l.sources.filter((s) => !hit(s));
      // 同 removeSource：人改过数量的行留着（那部分是无计划来源）
      if (sources.length === 0 && l.units === null) return;
      lines.push({ ...l, sources });
    });
    return { ok: true, draft: { ...d, lines } };
  }
  if (units === 0) return { ok: true, draft: d };
  const src: DraftSource = { line_id: c.line_id, seller_id: sid, units };
  const idx = d.lines.findIndex((l) => l.sku === c.sku);
  if (idx < 0) {
    const line: DraftLine = { sku: c.sku, wid: d.wid ?? '', units: null, price: '', expect_arrive_date: '',
      unplanned_reason: '', sources: [src] };
    return { ok: true, draft: { ...d, lines: [...d.lines, line] } };
  }
  return { ok: true, draft: { ...d, lines: d.lines.map((l, i) => (i === idx ? { ...l, sources: [...l.sources, src] } : l)) } };
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
  const wids = [...new Set(po.lines.map((l) => String(l.wid)))];
  return {
    // 明细全同仓才算有单头仓；分属多个仓的旧草稿留空，要人选一个统一（single_wid 闸点名）
    wid: wids.length === 1 ? wids[0]! : '',
    header: { supplier_id: String(po.supplier_id), purchaser_id: String(po.purchaser_id), opt_uid: String(po.opt_uid),
      qc_type: po.qc_type },
    lines: po.lines.map((ln) => {
      const srcSum = ln.sources.reduce((a, s) => a + s.units, 0);
      return {
        sku: ln.sku, wid: String(ln.wid),
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
    // 采购方 / 采购员是整数列：手填了字母也算没过（后端 preflight 只判有无，入库才炸）
    gate('supplier', !!h.supplier_id), gate('purchaser', intOf(h.purchaser_id) !== null), gate('opt_uid', intOf(h.opt_uid) !== null),
    gate('qc_type', h.qc_type !== null, { got: h.qc_type }), gate('lines', d.lines.length > 0),
  ];
  d.lines.forEach((l, i) => {
    const n = i + 1;
    const units = lineUnits(l);
    const src = sourcesUnits(l);
    out.push(gate('line_wid', intOf(l.wid) !== null, { line_no: n }));
    out.push(gate('line_price', nonnegNumber(l.price), { line_no: n }));
    out.push(gate('line_units', units >= src, { line_no: n, units, sources: src }));
    out.push(gate('line_unplanned_reason', units - src <= 0 || !!l.unplanned_reason.trim(),
      { line_no: n, unplanned: Math.max(units - src, 0) }));
  });
  // PB-D25：领星一张采购单只有一个仓；要发两个仓就建两张草稿
  const wids = [...new Set(d.lines.map((l) => intOf(l.wid)).filter((w): w is number => w !== null))].sort((a, b) => a - b);
  out.push(gate('single_wid', wids.length <= 1, { wids }));
  return out;
}

const GATE_LABEL: Record<string, string> = {
  supplier: '供应商', purchaser: '采购方', opt_uid: '采购员', qc_type: '质检方式', lines: '至少一行明细',
  line_wid: '仓', line_price: '单价', line_units: '数量不少于来源合计', line_unplanned_reason: '无计划来源的理由',
  sources_orderable: '来源记录仍可组单', single_wid: '全单同一个仓（领星一张单只能一个仓，分仓请建两张草稿）',
};

export function gateText(g: PreflightGate): string {
  const label = GATE_LABEL[g.gate];
  if (label === undefined) return g.gate;
  const n = g.detail['line_no'];
  return typeof n === 'number' ? `第 ${n} 行${label}` : label;
}

export type BodyResult = { ok: true; body: PoBody } | { ok: false; missing: string[]; invalid: string[] };

/** 文本 → 整数；不是纯数字（手填了字母、小数、负号）就是 null，不猜 */
export function intOf(s: string): number | null {
  const t = s.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return Number.isSafeInteger(n) ? n : null;
}

/** 缺单头 / 没有明细 / 有行没选仓时不出 body（没有合法整数可发，不编一个 0）；
 *  采购方 / 采购员 / 仓填了却不是整数 → invalid 点名，不发（后端是整数列）。其余照发，由后端点名 */
export function toBody(d: Draft): BodyResult {
  const h = d.header;
  const missing = (['supplier_id', 'purchaser_id', 'opt_uid'] as const).filter((k) => !h[k].trim()) as string[];
  if (h.qc_type === null) missing.push('qc_type');
  if (d.lines.length === 0) missing.push('lines');
  const invalid: string[] = [];
  const purchaser = intOf(h.purchaser_id);
  const optUid = intOf(h.opt_uid);
  if (h.purchaser_id.trim() && purchaser === null) invalid.push('purchaser_id');
  if (h.opt_uid.trim() && optUid === null) invalid.push('opt_uid');
  const wids = d.lines.map((l) => (l.wid === '' ? null : intOf(l.wid)));
  d.lines.forEach((l, i) => {
    if (l.wid === '') missing.push(`lines[${i + 1}].wid`);
    else if (wids[i] === null) invalid.push(`lines[${i + 1}].wid`);
  });
  if (missing.length > 0 || invalid.length > 0 || h.qc_type === null || purchaser === null || optUid === null) {
    return { ok: false, missing, invalid };
  }
  return {
    ok: true,
    body: {
      supplier_id: h.supplier_id, purchaser_id: purchaser, opt_uid: optUid, qc_type: h.qc_type,
      lines: d.lines.map((l, i) => {
        const out: PoLineInput = {
          sku: l.sku, wid: wids[i]!, units: lineUnits(l), price: l.price.trim(),
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
 *  记录的完整余量要后端排除本单才算得出） */
export function ownOnlyRecords(po: Pick<PurchaseOrder, 'lines'> | null, listed: readonly { line_id: number }[]): OwnRecord[] {
  const shown = new Set(listed.map((l) => l.line_id));
  const m = new Map<number, OwnRecord>();
  for (const ln of po?.lines ?? []) for (const s of ln.sources) {
    if (shown.has(s.line_id)) continue;
    const sid = String(s.seller_id);
    const r = m.get(s.line_id) ?? { target: { line_id: s.line_id, sku: ln.sku, remaining: 0, sellers: {} }, saved: {} };
    r.saved[sid] = (r.saved[sid] ?? 0) + s.units;
    r.target.sellers[sid] = { expected: null, used: 0 };
    m.set(s.line_id, r);
  }
  return [...m.values()].sort((a, b) => a.target.sku.localeCompare(b.target.sku) || a.target.line_id - b.target.line_id);
}

// ─── 来源：从哪张销售计划来（R3） ───

type PlanRef = Pick<CandidateLine, 'plan_id' | 'plan_title'>;
export const planName = (c: PlanRef): string => c.plan_title.trim() || `计划 ${c.plan_id}`;

export type ChainRef = Pick<CandidateLine, 'plan_id' | 'plan_title' | 'rev' | 'sku' | 'period'>;
/** 「10 月计划 rev5 DCC1045014AI 2026-10 → A4Pet-US 100 件」 */
export function chainText(c: ChainRef, shop: string, units: number): string {
  return `${planName(c)} rev${c.rev} ${c.sku} ${c.period} → ${shop} ${units} 件`;
}

/** null = 没有可说的来源（空选择），不是「来自 0 条」 */
export function sourceSummary(lines: readonly Pick<CandidateLine, 'plan_id' | 'plan_title' | 'rev'>[]): string | null {
  if (lines.length === 0) return null;
  const m = new Map<string, { c: PlanRef & { rev: number }; n: number }>();
  for (const l of lines) {
    const k = `${l.plan_id}#${l.rev}`;
    const hit = m.get(k);
    if (hit) hit.n += 1; else m.set(k, { c: l, n: 1 });
  }
  return `来自 ${[...m.values()].map(({ c, n }) => `销售计划「${planName(c)}」rev${c.rev} 的 ${n} 条记录`).join('，')}`;
}

/** `?lines=` → 记录号。null = 没带这个参数（或带了个空值：没带来任何东西，不收窄）；
 *  不是正整数的片段原样交回去点名，不丢 */
export function parseLineIds(raw: string | null): { ids: number[]; bad: string[] } | null {
  if (raw === null) return null;
  const ids: number[] = [];
  const bad: string[] = [];
  for (const t of raw.split(',').map((x) => x.trim()).filter((x) => x !== '')) {
    const n = intOf(t);
    if (n === null || n <= 0) { if (!bad.includes(t)) bad.push(t); continue; }
    if (!ids.includes(n)) ids.push(n);
  }
  return ids.length === 0 && bad.length === 0 ? null : { ids, bad };
}

/** 候选里列得出的给出来；列不出的逐条交回去，由页面反查状态后点名 */
export function preselect<T extends { line_id: number }>(ids: readonly number[], all: readonly T[]): { shown: T[]; missing: number[] } {
  const want = new Set(ids);
  const have = new Set(all.map((c) => c.line_id));
  return { shown: all.filter((c) => want.has(c.line_id)), missing: ids.filter((id) => !have.has(id)) };
}

/** 各店期望占比（%，取整），只作分店时的参考（PB-D22 不再据此限量）。
 *  有店期望为空或合计为 0 时不给 —— 不是 0% */
export function sellerShares(c: Pick<CandidateLine, 'sellers'>): Record<string, number> | null {
  const ex = Object.entries(c.sellers).map(([sid, v]) => [sid, v.expected] as const);
  if (ex.some(([, e]) => e === null)) return null;
  const sum = ex.reduce((a, [, e]) => a + (e ?? 0), 0);
  if (sum <= 0) return null;
  // 最大余数法凑满 100：各自四舍五入会出现 33+33+33
  const out = Object.fromEntries(ex.map(([sid, e]) => [sid, Math.floor(((e ?? 0) * 100) / sum)]));
  const extra = 100 - Object.values(out).reduce((a, b) => a + b, 0);
  const order = ex.map(([sid, e]) => [sid, ((e ?? 0) * 100) % sum] as const)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [sid] of order.slice(0, extra)) out[sid]! += 1;
  return out;
}

/** PB-D30：一条记录的剩余按各店期望比例拆（最大余数法，余数相同按店号序，同 `rules/autofill.py::split`）。
 *  有店有期望时，期望空 / 0 的店不分（空 ≠ 0）；全没有期望 → 整条给店号序第一家（负责人：「随便给一家」） */
export function prefillSplit(c: Pick<CandidateLine, 'sellers'>, room: number): Record<string, number> {
  const sids = Object.keys(c.sellers).sort();
  if (room <= 0 || sids.length === 0) return {};
  const w = sids.map((sid) => [sid, c.sellers[sid]!.expected ?? 0] as const).filter(([, e]) => e > 0);
  if (w.length === 0) return { [sids[0]!]: room };
  const sum = w.reduce((a, [, e]) => a + e, 0);
  const out = Object.fromEntries(w.map(([sid, e]) => [sid, Math.floor((room * e) / sum)]));
  const left = room - Object.values(out).reduce((a, b) => a + b, 0);
  const order = [...w].sort((a, b) => ((room * b[1]) % sum) - ((room * a[1]) % sum) || a[0].localeCompare(b[0]));
  for (const [sid] of order.slice(0, left)) out[sid]! += 1;
  return Object.fromEntries(Object.entries(out).filter(([, n]) => n > 0));
}

export interface PrefillResult {
  draft: Draft;
  filled: { line_id: number; units: number }[];
  skipped: { line_id: number; reason: 'no_remaining' | AddRefusal }[];
}

/** PB-D30：把带进来的记录逐条按 prefillSplit 填进稿；填不进的点名，不静默丢 */
export function prefill(d: Draft, recs: readonly SourceTarget[], base: Baseline): PrefillResult {
  let draft = d;
  const filled: PrefillResult['filled'] = [];
  const skipped: PrefillResult['skipped'] = [];
  for (const c of recs) {
    const parts = prefillSplit(c, recordRemaining(c, draft, base));
    if (Object.keys(parts).length === 0) { skipped.push({ line_id: c.line_id, reason: 'no_remaining' }); continue; }
    let units = 0;
    for (const [sid, n] of Object.entries(parts)) {
      const r = setAllocation(draft, c, sid, n, base);
      if (!r.ok) { skipped.push({ line_id: c.line_id, reason: r.reason }); continue; }
      draft = r.draft;
      units += n;
    }
    if (units > 0) filled.push({ line_id: c.line_id, units });
  }
  return { draft, filled, skipped };
}
