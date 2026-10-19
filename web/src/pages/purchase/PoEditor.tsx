import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { useInFlight } from '../../shell/useInFlight';
import { useActionError } from '../../shell/useActionError';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Modal } from '../../components/Modal';
import { Qty } from '../../components/Qty';
import { PlaceDialog } from './PlaceDialog';
import { PoDetail } from './PoDetail';
import { isEditable } from './poStatus';
import { api, ApiError } from '../../api';
import type { CandidateLines, LineTrace, PoId, PreflightGate, PurchaseOptions, PurchaseOrder, Seller } from '../../api/types';
import {
  baselineOf, draftWids, inDraft, setAllocation, setDraftWid, chainText, emptyDraft, fromPo, gateText, groupCandidates, lineUnits, ownOnlyRecords,
  parseLineIds, planName, preselect, QC_LABEL, sourceSummary,
  recordRemaining, removeSource, setHeader, setLineWid, sourcesUnits, toBody, unplannedUnits, updateLine, validate,
  type AddRefusal, type Baseline, type ChainRef, type Draft, type DraftHeader, type QcType, type SourceTarget,
} from './poEditorModel';
import { asApiError } from '../../api/client';
import { exactTotal, handlerThrew } from './fmt';
import { CommitButton, GateList, PoCrumbs } from './parts';
import { LeaveGuard } from './LeaveGuard';
import { homeHref, skuHit } from './purchaseHomeModel';

const logFail = (name: string, err: ApiError, t0: number) => {
  console.warn(`[po-editor] ${name} failed`, JSON.stringify({
    status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
  }));
};

// PB-D29：不列「状态不可组单」（旧版被替换 / 已完结的历史，采购员无事可做）
const EXCLUDED_LABEL: Partial<Record<keyof CandidateLines['excluded'], string>> = {
  sku: '货号不符', category: '品类不符', period: '月份不符', fully_confirmed: '已组满',
};
const fieldLabel = (k: string) => {
  const m = /^lines\[(\d+)\]\.wid$/.exec(k);
  return m ? `第 ${m[1]} 行仓` : MISSING_LABEL[k] ?? k;
};
const MISSING_LABEL: Record<string, string> = {
  supplier_id: '供应商', purchaser_id: '采购方', opt_uid: '采购员', qc_type: '质检方式', lines: '明细',
};
const REFUSAL_TEXT: Record<Exclude<AddRefusal, 'over_remaining'>, string> = {
  no_seller: '这家店不在该记录里', bad_units: '件数要是非负整数',
};

/** 带来的记录列不出时反查到的样子：组满了 / 状态走了 / 不存在 / 查不到 */
const TRACE_CAP = 20;
type Missing = { kind: 'pending' } | { kind: 'skipped' } | { kind: 'trace'; t: LineTrace } | { kind: 'gone' } | { kind: 'err'; err: ApiError };
const ORDERABLE_STATES: readonly string[] = ['已提交', '已确认'];
function missingText(id: number, m: Missing): string {
  if (m.kind === 'pending') return `记录 ${id} 正在查状态…`;
  if (m.kind === 'skipped') return `记录 ${id} 不可组单，超过 ${TRACE_CAP} 条未反查状态`;
  if (m.kind === 'gone') return `记录 ${id} 不存在`;
  if (m.kind === 'err') return `记录 ${id} 状态取不到`;
  // 状态还可组单却没列出 = 被 fully_confirmed 滤掉了：说「组满」，不拿一个可组单的状态说「不可组单」
  return ORDERABLE_STATES.includes(m.t.state) && m.t.confirmed_units >= m.t.total_units
    ? `记录 ${id} 已组满（共 ${m.t.total_units} 件）`
    : `记录 ${id} 已不可组单（状态 ${m.t.state}）`;
}

const MANUAL = '__manual__';
const STATE_MOVED = new Set(['po_placing', 'needs_verify', 'mismatch_open', 'po_frozen', 'po_discarded']);

interface ActorOption { value: string; text: string; history: boolean }

/** 下拉 + 手填：领星历史里的值不全（新采购员没下过单），只给下拉就会卡住人 */
function ActorSelect({ label, value, options, loading, onChange }: {
  label: string; value: string; options: ActorOption[] | null; loading: boolean; onChange: (v: string) => void;
}) {
  const known = options?.some((o) => o.value === value) ?? false;
  const [manual, setManual] = useState(false);
  const isManual = manual || (value !== '' && !known && options !== null);
  return (
    <label className="pe-field">
      <span className="pe-lbl">{label}</span>
      <select
        className="inp" aria-label={label} disabled={loading}
        value={isManual ? MANUAL : value}
        onChange={(e) => {
          if (e.target.value === MANUAL) { setManual(true); onChange(''); return; }
          setManual(false); onChange(e.target.value);
        }}
      >
        <option value="">{loading ? '正在取选项…' : options && options.length > 0 ? '请选择' : '无已知值，可手填'}</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value} className={o.history ? 'i-pencil' : undefined}>{o.text}</option>
        ))}
        <option value={MANUAL}>手填</option>
      </select>
      {isManual && (
        <input className="inp" aria-label={`${label}（手填）`} inputMode="numeric" value={value}
          onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}

/** 清空 = 回到「跟随来源合计」。★ 本地留一份文本：受控值一清空就弹回合计，人就没法改写成别的数 */
function UnitsInput({ value, onChange }: { value: number; onChange: (v: number | null) => void }) {
  const [text, setText] = useState(String(value));
  const [focused, setFocused] = useState(false);
  const [bad, setBad] = useState(false);
  useEffect(() => { if (!focused) { setText(String(value)); setBad(false); } }, [value, focused]);
  return (
    <>
      <input className={`pe-num${bad ? ' is-bad' : ''}`} type="number" min={0} step={1} aria-label="数量" value={text}
        aria-invalid={bad}
        onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
        onChange={(e) => {
          const v = e.target.value.trim();
          setText(e.target.value);
          if (v === '') { setBad(false); onChange(null); return; }
          const n = Number(v);
          // 小数 / 负数不截断：截了就是替人改了数，人看不出来
          if (!Number.isInteger(n) || n < 0) { setBad(true); return; }
          setBad(false);
          onChange(n);
        }} />
      {bad && <span className="pe-line__err"> 数量要是非负整数，未采用</span>}
    </>
  );
}

const countText = (source: string, count: number | null) => `（${source}${count === null ? '' : ` ${count} 单`}）`;

type RouteView = { kind: 'loading' } | { kind: 'fail'; err: ApiError } | { kind: 'editor' } | { kind: 'detail' };

/** ★ 按状态分发：只有「草稿 · 未作废 · 无未处理项 · 无卡住 / 在途的下单调用」进编辑器，其余进详情。
 *  待核实 / 在途时后端拒改 / 删 / 下单（409），编辑器在那时只会是一屏点了必错的按钮。
 *  ★ 失败即关闭：队列或调用记录取不到 → 进详情（那里会点名取不到什么），不赌它是可编辑的。 */
export function PoRoute() {
  const { poId } = useParams();
  const parsed = Number(poId);
  const id = Number.isInteger(parsed) && parsed > 0 ? parsed : -1;
  const [gen, setGen] = useState(0);
  const [view, setView] = useState<RouteView>({ kind: 'loading' });
  const reroute = useCallback(() => setGen((g) => g + 1), []);

  useEffect(() => {
    let alive = true;
    setView({ kind: 'loading' });
    const t0 = performance.now();
    const po = Promise.resolve().then(() => api.getPurchaseOrder(id));
    // 卡住的调用不在 po.attention 里，只在队列里；在途（未卡住）的下单调用只在调用记录里
    const stuck = Promise.resolve().then(() => api.listAttention()).then(
      (q) => q.items.filter((i) => i.po_id === id && i.kind === 'stuck_call').length,
      (e: unknown) => { logFail('listAttention (route)', asApiError(e), t0); return null; });
    const tl = Promise.resolve().then(() => api.getTimeline(id)).then(
      (t) => t,
      (e: unknown) => { logFail('getTimeline (route)', asApiError(e), t0); return null; });
    Promise.all([po, stuck, tl]).then(
      ([p, n, t]) => {
        const editor = n !== null && t !== null && isEditable(p, n, t);
        if (!editor && p.state === '草稿' && (n === null || t === null)) {
          console.warn('[po-route] fail closed -> detail', JSON.stringify({ po_id: id, queue: n !== null, timeline: t !== null }));
        }
        if (alive) setView({ kind: editor ? 'editor' : 'detail' });
      },
      (e: unknown) => { const err = asApiError(e); logFail('getPurchaseOrder (route)', err, t0); if (alive) setView({ kind: 'fail', err }); },
    );
    return () => { alive = false; };
  }, [id, gen]);

  if (view.kind === 'editor') return <PoEditor key={`${poId}-${gen}`} poId={id} onReroute={reroute} />;
  if (view.kind === 'detail') return <PoDetail key={`${poId}-${gen}`} poId={id} onEditable={reroute} />;
  return (
    <AppShell crumb="采购单">
      <div className="head"><div className="head__main"><h1>采购单 {poId}</h1></div></div>
      {view.kind === 'fail' ? <ErrorDetail err={view.err} /> : <div className="empty">正在取采购单…</div>}
    </AppShell>
  );
}

export function PoEditor({ poId, onReroute }: { poId: PoId | null; onReroute?: () => void }) {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const { pending, run } = useInFlight();
  const { err: actErr, fail, succeed, dismiss } = useActionError();

  const [options, setOptions] = useState<PurchaseOptions | null>(null);
  const [optionsErr, setOptionsErr] = useState<ApiError | null>(null);
  const [cands, setCands] = useState<CandidateLines | null>(null);
  const [candsErr, setCandsErr] = useState<ApiError | null>(null);
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [poErr, setPoErr] = useState<ApiError | null>(null);
  const [gates, setGates] = useState<PreflightGate[] | null>(null);
  const [gatesErr, setGatesErr] = useState<ApiError | null>(null);
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [sellersErr, setSellersErr] = useState<ApiError | null>(null);

  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [baseline, setBaseline] = useState<Baseline>(() => baselineOf(null));
  const [dirty, setDirty] = useState(false);
  const [addQty, setAddQty] = useState<Record<string, string>>({});
  const [refusal, setRefusal] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string[] | null>(null);
  const [invalid, setInvalid] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [placing, setPlacing] = useState(false);
  // 「保存并下单」：保存成功、后端对已保存内容的检查回来后才开确认框 —— 确认框只认已保存那一份的 token（H-1）。
  // 新建的单保存后换路由，靠 ?place=1 把这个意图带过去
  const [placeAfterSave, setPlaceAfterSave] = useState(() => poId !== null && search.get('place') === '1');
  const [showAll, setShowAll] = useState(false);
  const [missingInfo, setMissingInfo] = useState<Record<number, Missing>>({});
  const [skuQ, setSkuQ] = useState('');
  /** 人点过的货号展开 / 收起；没点过的按默认（见 skuOpenByDefault） */
  const [openSku, setOpenSku] = useState<Map<string, boolean>>(() => new Map());
  const leaving = useRef(false);

  // 稿一变，旧的拒绝理由（按旧可用量算的）就不再成立
  const edit = (next: Draft) => { setDraft(next); setDirty(true); setRefusal({}); };

  const loadCands = useCallback(() => {
    const t0 = performance.now();
    Promise.resolve().then(() => api.listCandidateLines({})).then(
      (c) => { setCands(c); setCandsErr(null); },
      (e: unknown) => { const err = asApiError(e); logFail('listCandidateLines', err, t0); setCandsErr(err); },
    );
  }, []);

  const loadPreflight = useCallback((id: PoId) => {
    const t0 = performance.now();
    setGates(null);
    Promise.resolve().then(() => api.getPreflight(id)).then(
      (r) => { setGates(r.gates); setGatesErr(null); },
      (e: unknown) => { const err = asApiError(e); logFail('getPreflight', err, t0); setGatesErr(err); },
    );
  }, []);

  /** 抛错交给调用方：首次载入失败整页给错，重新载入失败只记在动作上、不丢眼前的稿 */
  const loadPo = useCallback(async (id: PoId) => {
    const t0 = performance.now();
    try {
      const p = await api.getPurchaseOrder(id);
      setPo(p);
      setDraft(fromPo(p));
      setBaseline(baselineOf(p));
      setDirty(false);
      if (p.state === '草稿' && !p.discarded_at) loadPreflight(id);
    } catch (e) {
      const err = asApiError(e);
      logFail('getPurchaseOrder', err, t0);
      throw err;
    }
  }, [loadPreflight]);

  useEffect(() => {
    const t0 = performance.now();
    Promise.resolve().then(() => api.getPurchaseOptions()).then(
      (o) => {
        setOptions(o);
        if (!o.history.available) {
          console.warn('[po-editor] purchase history unavailable', JSON.stringify({ cause: o.history.cause }));
        }
      },
      (e: unknown) => { const err = asApiError(e); logFail('getPurchaseOptions', err, t0); setOptionsErr(err); },
    );
    const t1 = performance.now();
    Promise.resolve().then(() => api.listSellers()).then(setSellers,
      (e: unknown) => { const err = asApiError(e); logFail('listSellers', err, t1); setSellersErr(err); });
    loadCands();
    if (poId !== null) loadPo(poId).catch((e: unknown) => setPoErr(e as ApiError));
  }, [poId, loadCands, loadPo]);

  useEffect(() => {
    if (!search.has('place')) return;
    const next = new URLSearchParams(search);
    next.delete('place');
    setSearch(next, { replace: true });   // 刷新页面不该再自动开一次确认框
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!placeAfterSave || dirty || po === null) return;
    if (gatesErr) {
      console.warn('[po-editor] save-then-place stopped: preflight unavailable', JSON.stringify({ po_id: po.po_id, error: gatesErr.error }));
      setPlaceAfterSave(false);
      return;
    }
    if (gates === null) return;
    setPlaceAfterSave(false);
    const failed = gates.filter((g) => !g.ok).map((g) => g.gate);
    console.info('[po-editor] save-then-place', JSON.stringify({ po_id: po.po_id, failed }));
    // 没过的闸由按钮旁的 place-blocked 点名，不开确认框
    if (failed.length === 0) setPlacing(true);
  }, [placeAfterSave, dirty, po, gates, gatesErr]);

  // R3：从待采购带过来的记录（只对新建有意义；已保存的单以单里的来源为准）
  const wanted = useMemo(() => (poId === null ? parseLineIds(search.get('lines')) : null), [poId, search]);
  const sel = useMemo(() => (wanted && cands ? preselect(wanted.ids, cands.lines) : null), [wanted, cands]);
  const missingKey = sel?.missing.join(',') ?? '';
  useEffect(() => {
    if (!sel || sel.missing.length === 0) return;
    let alive = true;
    setMissingInfo(Object.fromEntries(sel.missing.map((id, i) =>
      [id, (i < TRACE_CAP ? { kind: 'pending' } : { kind: 'skipped' }) as Missing])));
    // 手拼的 URL 可以带任意多个号：只反查前 TRACE_CAP 条，其余点名「未反查」
    for (const id of sel.missing.slice(0, TRACE_CAP)) {
      const t0 = performance.now();
      Promise.resolve().then(() => api.getLineTrace(id)).then(
        (t) => { if (alive) setMissingInfo((m) => ({ ...m, [id]: { kind: 'trace', t } })); },
        (e: unknown) => {
          const err = asApiError(e);
          logFail(`getLineTrace(${id})`, err, t0);
          if (alive) setMissingInfo((m) => ({ ...m, [id]: err.status === 404 ? { kind: 'gone' } : { kind: 'err', err } }));
        },
      );
    }
    console.warn('[po-editor] preselected lines not orderable', JSON.stringify({ wanted: wanted?.ids, missing: sel.missing }));
    return () => { alive = false; };
  }, [missingKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // UX-D4：续编草稿只列本单来源计划的记录 —— 与新建带记录进来同一种收窄，同一个放开出口
  const ownPlans = useMemo(() => new Set((po?.lines ?? []).flatMap((l) => l.sources.map((s) => s.plan_id))), [po]);
  const scope = useMemo(() => {
    if (sel) return sel.shown;
    if (poId !== null && ownPlans.size > 0 && cands) return cands.lines.filter((l) => ownPlans.has(l.plan_id));
    return null;
  }, [sel, poId, ownPlans, cands]);
  const narrowed = scope !== null && !showAll;
  const allGroups = useMemo(() => groupCandidates(narrowed ? scope : cands?.lines ?? []), [cands, scope, narrowed]);
  const groups = useMemo(() => (skuQ.trim() === '' ? allGroups : allGroups.filter((g) => skuHit(g.sku, skuQ))),
    [allGroups, skuQ]);
  const summary = useMemo(() => {
    if (sel) return sourceSummary(sel.shown);
    const bySrc = new Map<number, ChainRef>();
    for (const ln of po?.lines ?? []) for (const x of ln.sources) bySrc.set(x.line_id, x);
    return sourceSummary([...bySrc.values()]);
  }, [sel, po]);
  // 来源链的依据：单自己带的（组满的记录不在候选里）优先，新加的来源从候选取
  const chainRef = useMemo(() => {
    const m = new Map<number, ChainRef>();
    for (const l of cands?.lines ?? []) m.set(l.line_id, l);
    for (const ln of po?.lines ?? []) for (const x of ln.sources) m.set(x.line_id, x);
    return m;
  }, [cands, po]);
  const localGates = useMemo(() => validate(draft), [draft]);
  // 本单原有的货号也算：移除最后一条来源时，正在改的那组不该在手底下收起来
  const touched = useMemo(() => new Set([...draft.lines, ...(po?.lines ?? [])].map((l) => l.sku)), [draft, po]);
  // UX-D5：带进来的记录（人刚在列表上挑过）、本单已填的货号、搜索命中的展开；其余收起，量大时不摊成几十屏
  const brought = useMemo(() => new Set((sel?.shown ?? []).map((l) => l.sku)), [sel]);
  const skuOpenByDefault = (sku: string) => brought.has(sku) || allGroups.length === 1 || touched.has(sku) || skuHit(sku, skuQ);
  const isSkuOpen = (sku: string) => openSku.get(sku) ?? skuOpenByDefault(sku);
  const allOpen = groups.length > 0 && groups.every((g) => isSkuOpen(g.sku));
  const ownOnly = useMemo(() => (cands !== null || candsErr !== null ? ownOnlyRecords(po, cands?.lines ?? []) : []),
    [po, cands, candsErr]);
  const sellerName = useMemo(() => new Map((sellers ?? []).map((x) => [x.seller_id, x.name])), [sellers]);
  const shop = (sid: string) => { const n = sellerName.get(sid); return n ? `${n}（${sid}）` : sid; };
  const shopName = (sid: string) => sellerName.get(sid) ?? sid;
  const useLocal = poId === null || dirty;

  /** 逐店件数：Enter / 离开输入框即采用（绝对量；空 = 0 = 去掉这条来源） */
  const commitAlloc = (c: SourceTarget, sid: string, raw: string) => {
    const k = `${c.line_id}|${sid}`;
    const t = raw.trim();
    const n = t === '' ? 0 : Number(t);
    if (n === inDraft(draft, c.line_id, sid)) { setAddQty(({ [k]: _same, ...rest }) => rest); return; }
    const r = setAllocation(draft, c, sid, n, baseline);
    if (!r.ok) {
      setRefusal({ ...refusal, [k]: r.reason === 'over_remaining'
        ? `超过该记录剩余，最多还能加 ${r.available ?? 0}` : REFUSAL_TEXT[r.reason] });
      return;
    }
    setAddQty(({ [k]: _drop, ...rest }) => rest);
    edit(r.draft);
  };

  /** 单的状态在别处变了（在途 / 待核实 / 不一致 / 已下单）：不当普通错误摆着，交回路由按状态重新分发 */
  const movedOn = (err: ApiError): boolean => {
    if (!onReroute || !STATE_MOVED.has(err.error)) return false;
    pushToast({ kind: 'warn', text: err.hint });
    refreshAttention();
    onReroute();
    return true;
  };

  const save = (thenPlace = false) => run('save', async () => {
    const t0 = performance.now();
    try {
      const r = toBody(draft);
      if (!r.ok) { setMissing(r.missing); setInvalid(r.invalid); return; }
      setMissing(null);
      setInvalid([]);
      if (poId === null) {
        const { po_id } = await api.createPurchaseOrder(r.body);
        console.info('[po-editor] createPurchaseOrder ok', JSON.stringify({ po_id, lines: r.body.lines.length,
          ms: Math.round(performance.now() - t0) }));
        succeed('save');
        pushToast({ kind: 'ok', text: '已保存草稿' });
        refreshAttention();
        leaving.current = true;
        // replace：浏览器返回不再落到一张空的新建编辑器
        navigate(`/purchase/${po_id}${thenPlace ? '?place=1' : ''}`, { replace: true });
        return;
      }
      if (po === null) return;
      const res = await api.updatePurchaseOrder(poId, { ...r.body, draft_version: po.draft_version });
      console.info('[po-editor] updatePurchaseOrder ok', JSON.stringify({ po_id: poId, draft_version: res.draft_version,
        lines: r.body.lines.length, ms: Math.round(performance.now() - t0) }));
      succeed('save');
      pushToast({ kind: 'ok', text: `已保存草稿 ${po.custom_order_sn}` });
      refreshAttention();
      loadCands();
      const reloaded = await loadPo(poId).then(() => true, (e: unknown) => { fail('reload', e as ApiError); return false; });
      if (thenPlace && reloaded) setPlaceAfterSave(true);
    } catch (e) {
      const err = e instanceof ApiError ? e : handlerThrew('po-editor', 'save', e);
      logFail(poId === null ? 'createPurchaseOrder' : 'updatePurchaseOrder', err, t0);
      if (movedOn(err)) return;
      fail('save', err);
    }
  });

  const reload = () => run('reload', async () => {
    if (poId === null) return;
    try {
      await loadPo(poId);
      loadCands();
      dismiss();
    } catch (e) {
      fail('reload', handlerThrew('po-editor', 'reload', e));
    }
  });

  const doDelete = () => run('delete', async () => {
    if (poId === null) return;
    const t0 = performance.now();
    try {
      await api.deletePurchaseOrder(poId);
      console.info('[po-editor] deletePurchaseOrder ok', JSON.stringify({ po_id: poId, ms: Math.round(performance.now() - t0) }));
      succeed('delete');
      pushToast({ kind: 'ok', text: `已删除草稿 ${po?.custom_order_sn ?? ''}`.trim() });
      refreshAttention();
      leaving.current = true;
      navigate('/purchase');
    } catch (e) {
      const err = e instanceof ApiError ? e : handlerThrew('po-editor', 'delete', e);
      logFail('deletePurchaseOrder', err, t0);
      setConfirmDelete(false);
      if (movedOn(err)) return;
      fail('delete', err);
    }
  });

  // ─── 非草稿 / 取不到单 ───
  if (poId !== null) {
    if (poErr) {
      return (
        <AppShell crumb="采购单">
          <div className="head"><div className="head__main"><h1>采购单 {poId}</h1></div></div>
          <ErrorDetail err={poErr} />
        </AppShell>
      );
    }
    if (po === null) {
      return (
        <AppShell crumb="采购单">
          <div className="head"><div className="head__main"><h1>采购单 {poId}</h1></div></div>
          <div className="empty">正在取采购单…</div>
        </AppShell>
      );
    }
    // 路由判过之后状态又变了（别处下了单 / 开了待核实）：不给编辑器
    if (po.state !== '草稿' || po.discarded_at || po.attention.length > 0) {
      return onReroute ? <PoDetail poId={poId} onEditable={onReroute} /> : <PoDetail poId={poId} />;
    }
  }

  const title = po ? `采购单 ${po.custom_order_sn}` : '新建采购单';
  const h = draft.header;
  const setH = (patch: Partial<DraftHeader>) => edit(setHeader(draft, patch));
  const purchaserOpts: ActorOption[] | null = options && options.purchasers.map((p) => ({
    value: String(p.purchaser_id), text: `${p.purchaser_id}${countText(p.source, p.count)}`, history: p.source === '领星历史' }));
  const optUidOpts: ActorOption[] | null = options && options.opt_uids.map((p) => ({
    value: String(p.opt_uid), text: `${p.opt_uid}${p.name ? ` ${p.name}` : ''}${countText(p.source, p.count)}`,
    history: p.source === '领星历史' }));
  const suppliers = options?.suppliers ?? [];
  const warehouses = options?.warehouses ?? [];
  const shownGates = useLocal ? localGates : gates;
  const failing = shownGates?.filter((g) => !g.ok).length ?? 0;
  // ⛔ 只认后端对已保存内容的检查：本地校验判不了 sources_orderable，也不是将要发出去的那一份
  const failingNames = (gates ?? []).filter((g) => !g.ok).map(gateText);
  // 未保存的改动不挡下单：按钮变成「保存并下单」（先存，再按存下的那一份确认）
  const unsaved = poId === null || dirty;
  // 未保存的改动不挡下单（PB-D23「保存并下单」）；但本地闸没过就不让点 —— 点名第一道和还有几道
  const blockOf = (gs: PreflightGate[]): string | null => {
    const bad = gs.filter((g) => !g.ok);
    return bad.length === 0 ? null : `${gateText(bad[0]!)} 未过${bad.length > 1 ? `，另有 ${bad.length - 1} 道` : ''}`;
  };
  const placeBlock: string | null = unsaved ? blockOf(localGates)
    : placeAfterSave ? '正在检查…'
    : po === null ? '正在取采购单…'
    : gatesErr ? '下单前检查取不到'
    : gates === null ? '正在检查…'
    : blockOf(gates);
  const afterPlace = () => {
    setPlacing(false);
    if (onReroute) onReroute();
    else if (poId !== null) loadPo(poId).catch((e: unknown) => fail('reload', e as ApiError));
  };

  // PB-D22：没有分店上限 —— 各店共用记录剩余，期望只作参考
  const sellerRows = (r: SourceTarget, saved?: Record<string, number>) => {
    const rem = recordRemaining(r, draft, baseline);
    return Object.keys(r.sellers).sort().map((sid) => {
      const k = `${r.line_id}|${sid}`;
      const cur = inDraft(draft, r.line_id, sid);
      const room = rem + cur;
      const exp = r.sellers[sid]!.expected;
      const typing = addQty[k];
      return (
        <div key={sid} className={`pe-st${refusal[k] ? ' is-err' : ''}`} data-testid={`seller-${sid}`}>
          <span className="pe-st__n">{shop(sid)}</span>
          <span className="pe-st__exp" data-testid="expected">
            {saved ? `本单原有 ${saved[sid] ?? 0}` : exp === null ? '期望未知' : `期望 ${exp}`}
          </span>
          <input className="pe-qty" inputMode="numeric" aria-label={`店 ${sid} 本单件数`}
            placeholder={room === 0 ? '已无剩余' : '0'} disabled={room === 0 && cur === 0}
            value={typing ?? (cur > 0 ? String(cur) : '')}
            onChange={(e) => { setAddQty({ ...addQty, [k]: e.target.value }); if (refusal[k]) setRefusal(({ [k]: _r, ...rest }) => rest); }}
            onBlur={(e) => { if (addQty[k] !== undefined) commitAlloc(r, sid, e.target.value); }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitAlloc(r, sid, e.currentTarget.value); } }} />
          <button type="button" className="pe-fill" disabled={room === 0 || cur === room}
            title={`把剩余 ${room} 件都给这家店`} onClick={() => commitAlloc(r, sid, String(room))}>剩余全给</button>
          {refusal[k] && <span className="pe-st__err" role="alert">{refusal[k]}</span>}
        </div>
      );
    });
  };
  const recHead = (label: string, r: SourceTarget, total: number | null, testId?: string) => {
    const rem = recordRemaining(r, draft, baseline);
    return (
      <div className="pe-rec__head">
        <span>{label}</span>
        <span className={`pe-rem${rem === 0 ? ' is-zero' : ''}`} data-testid={testId}>
          剩 <b>{rem}</b>{total !== null ? ` / ${total}` : ''}
        </span>
      </div>
    );
  };
  const wids = draftWids(draft);
  const totalUnits = draft.lines.reduce((a, l) => a + lineUnits(l), 0);
  const amount = exactTotal(draft.lines.map((l) => ({ units: lineUnits(l), price: l.price })));
  const crumbSources = draft.lines.flatMap((l) => l.sources.flatMap((s) => {
    const c = chainRef.get(s.line_id);
    return c ? [{ plan_id: c.plan_id, units: s.units }] : [];
  }));
  const firstBrought = sel?.shown[0];
  const home = crumbSources.length > 0 ? homeHref(crumbSources)
    : firstBrought ? `/purchase?plan=${firstBrought.plan_id}` : '/purchase';

  return (
    <AppShell crumb={po ? po.custom_order_sn : '新建采购单'}>
      <LeaveGuard when={unsaved && (dirty || draft.lines.length > 0)} bypass={leaving} />
      <div className="head">
        <div className="head__main">
          <PoCrumbs home={home} here={po ? po.custom_order_sn : '新建'} />
          <h1>{title}</h1>
        </div>
      </div>

      <div className="pe">
        {/* ─── 单头 ─── */}
        {optionsErr && <ErrorDetail err={optionsErr} />}
        <section className="pe-head" data-testid="po-header">
          <label className="pe-field">
            <span className="pe-lbl">供应商</span>
            <select className="inp" aria-label="供应商" value={h.supplier_id} disabled={options === null && !optionsErr}
              onChange={(e) => setH({ supplier_id: e.target.value })}>
              <option value="">{options === null ? (optionsErr ? '选项取不到' : '正在取选项…') : '请选择'}</option>
              {suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.name}（{s.supplier_id}）</option>)}
              {h.supplier_id && !suppliers.some((s) => s.supplier_id === h.supplier_id) && (
                <option value={h.supplier_id}>{h.supplier_id}</option>
              )}
            </select>
          </label>
          <ActorSelect label="采购方" value={h.purchaser_id} options={purchaserOpts} loading={options === null && !optionsErr}
            onChange={(v) => setH({ purchaser_id: v })} />
          <ActorSelect label="采购员" value={h.opt_uid} options={optUidOpts} loading={options === null && !optionsErr}
            onChange={(v) => setH({ opt_uid: v })} />
          <label className="pe-field">
            <span className="pe-lbl">质检方式</span>
            <select className="inp" aria-label="质检方式" value={h.qc_type === null ? '' : String(h.qc_type)}
              onChange={(e) => setH({ qc_type: e.target.value === '' ? null : (Number(e.target.value) as QcType) })}>
              <option value="">请选择</option>
              {([0, 1, 2] as const).map((q) => <option key={q} value={q}>{q} {QC_LABEL[q]}</option>)}
            </select>
          </label>
          <label className="pe-field">
            <span className="pe-lbl">仓库（整张单一个仓）</span>
            <select className="inp" aria-label="仓库" value={draft.wid ?? ''} onChange={(e) => edit(setDraftWid(draft, e.target.value))}>
              <option value="">{options === null ? (optionsErr ? '仓取不到' : '正在取仓…') : '请选择'}</option>
              {warehouses.map((w) => <option key={w.wid} value={String(w.wid)}>{w.name}（{w.wid}）</option>)}
              {draft.wid && !warehouses.some((w) => String(w.wid) === draft.wid) && <option value={draft.wid}>{draft.wid}</option>}
            </select>
            {!draft.wid && wids.length > 1 && (
              <span className="pe-warn" data-testid="mixed-wid">明细分属仓 {wids.join('、')}：选一个仓，全单统一</span>
            )}
          </label>
          {options && !options.history.available && (
            <span className="pe-field">
              <span className="pe-warn" data-testid="history-unavailable">领星历史暂不可用</span>
              <Fold summary="原因">{options.history.cause ?? '后端未给原因'}</Fold>
            </span>
          )}
          {options && options.history.available && options.history.dropped > 0 && (
            <span className="dropline">领星历史有 {options.history.dropped} 行无法识别，未列入</span>
          )}
        </section>

        <div className="pe-cols">
          {/* ─── 左：从计划记录分配 ─── */}
          <section className="pe-pane" aria-label="可组单记录" data-testid="candidates">
            <div className="pe-pane__head">
              <span className="pe-pane__title">从计划记录分配</span>
              {(sel !== null || (po !== null && cands !== null)) && summary && (
                <span className="pe-pane__sub" data-testid="source-summary">{summary}</span>
              )}
              <span className="pm-bar__sp" />
              {narrowed && (
                <button type="button" className="btn btn--sm btn--ghost" onClick={() => setShowAll(true)}>加入其他待采购记录</button>
              )}
            </div>
            {allGroups.length > 0 && (
              <div className="pe-find">
                <span className="pm-search-wrap">
                  <input className="pm-search" type="search" aria-label="搜货号" placeholder="搜货号" value={skuQ}
                    onChange={(e) => { setSkuQ(e.target.value); setOpenSku(new Map()); }} />
                </span>
                <span className="muted">{groups.length} 个货号</span>
                <span className="pm-bar__sp" />
                <button type="button" className="btn btn--sm btn--ghost" disabled={groups.length === 0}
                  onClick={() => setOpenSku(new Map(groups.map((g) => [g.sku, !allOpen])))}>{allOpen ? '全部收起' : '全部展开'}</button>
              </div>
            )}
            {wanted && (wanted.bad.length > 0 || (sel?.missing.length ?? 0) > 0) && (
              <ul className="pe-lost" data-testid="preselect-missing">
                {wanted.bad.map((t) => <li key={`bad-${t}`}>「{t}」不是记录号</li>)}
                {(sel?.missing ?? []).map((id) => {
                  const m = missingInfo[id] ?? { kind: 'pending' };
                  return (
                    <li key={id}>
                      <span>{missingText(id, m)}</span>
                      {m.kind === 'err' && <ErrorDetail err={m.err} />}
                    </li>
                  );
                })}
              </ul>
            )}
            {candsErr && <ErrorDetail err={candsErr} />}
            {sellersErr && (
              <div data-testid="seller-names-failed">
                <span className="muted">店名取不到，按店编号显示</span>
                <Fold summary="原因"><ErrorDetail err={sellersErr} /></Fold>
              </div>
            )}
            {/* 同采购首页：已组满等过滤掉的给计数（折叠），不是错误 */}
            {cands && (() => {
              const dropped = (Object.keys(EXCLUDED_LABEL) as (keyof CandidateLines['excluded'])[])
                .filter((k) => (cands.excluded[k] ?? 0) > 0);
              const n = dropped.reduce((a, k) => a + (EXCLUDED_LABEL[k] ? cands.excluded[k] : 0), 0);
              return n > 0 && (
                <Fold summary={`未列入 ${n} 条`} testId="cand-dropped">
                  {dropped.map((k) => <div className="dropline" key={k}>{EXCLUDED_LABEL[k]} {cands.excluded[k]} 条</div>)}
                </Fold>
              );
            })()}
            {cands === null ? (!candsErr && <div className="empty">正在取可组单记录…</div>)
              : allGroups.length === 0 ? (
                <div className="empty">{!narrowed ? '没有可组单的记录' : sel ? '带来的记录没有一条可组单' : '本单来源计划没有还可组的记录'}</div>
              ) : groups.length === 0 ? <div className="empty">没有匹配「{skuQ}」的货号</div>
              : groups.map((g) => {
                const open = isSkuOpen(g.sku);
                const ids = new Set(g.records.map((r) => r.line_id));
                const rem = g.records.reduce((a, r) => a + recordRemaining(r, draft, baseline), 0);
                const filled = draft.lines.reduce((a, l) => a + l.sources.filter((x) => ids.has(x.line_id)).reduce((b, x) => b + x.units, 0), 0);
                return (
                  <div className="pe-grp" key={g.sku} data-testid={`cand-sku-${g.sku}`}>
                    <button type="button" className="pe-grp__sku" aria-expanded={open}
                      onClick={() => setOpenSku((m) => new Map(m).set(g.sku, !open))}>
                      <span className="num">{g.sku}</span>
                      <span className="pe-grp__meta">{g.records.length} 条记录，剩 {rem}，本单已填 {filled}</span>
                    </button>
                    {open && g.records.map((r) => (
                      <div className="pe-rec" key={r.line_id} data-testid={`cand-${r.line_id}`}>
                        {recHead(`${planName(r)} rev${r.rev} ${r.period}`, r, r.total_units)}
                        {sellerRows(r)}
                      </div>
                    ))}
                  </div>
                );
              })}
            {ownOnly.map((o) => (
              <div className="pe-grp" key={`own-${o.target.line_id}`} data-testid={`own-${o.target.line_id}`}>
                <div className="pe-grp__sku"><span className="num">{o.target.sku}</span> 本单原有来源</div>
                <div className="pe-rec">
                  <div className="pe-rec__head">
                    <span>记录 #{o.target.line_id}</span>
                    <span className="pe-rem">可加回 <b className="num" data-testid="own-avail">{recordRemaining(o.target, draft, baseline)}</b></span>
                  </div>
                  <Fold summary="为什么单列">本单把这条记录组满了，候选不再列出它；这里只能加回本单原有的量。</Fold>
                  {sellerRows(o.target, o.saved)}
                </div>
              </div>
            ))}
          </section>

          {/* ─── 右：明细 ─── */}
          <section className="pe-pane" aria-label="明细" data-testid="po-lines">
            <div className="pe-pane__head">
              <span className="pe-pane__title">明细</span>
              <span className="pe-pane__sub" data-testid="lines-sum">
                {draft.lines.length} 行，{totalUnits} 件，金额 {amount ?? <Qty v={{ kind: 'unknown' }} />}
              </span>
            </div>
            {draft.lines.length === 0 ? <div className="pm-empty">在左边给店铺填件数，这里按货号生成明细</div> : (
              <div className="pt-wrap">
                <table className="pt pe-lt">
                  <thead><tr>
                    <th className="r">#</th><th>货号</th><th>数量</th><th>单价</th><th>预计到货</th><th>来源</th>
                  </tr></thead>
                  <tbody>
                    {draft.lines.map((l, i) => {
                      const unplanned = unplannedUnits(l);
                      return (
                        <tr key={`${l.sku}#${i}`} data-testid={`po-line-${i + 1}`}>
                          <td className="r pe-line__no">{i + 1}</td>
                          <td className="pt-sku">{l.sku}</td>
                          <td>
                            <UnitsInput value={lineUnits(l)} onChange={(v) => edit(updateLine(draft, i, { units: v }))} />
                            {unplanned < 0 && <div className="pe-line__err">少于来源合计 {sourcesUnits(l)} 件</div>}
                            {unplanned > 0 && (
                              <div className="pe-line__unpl">
                                <span className="chip chip--warn">无计划来源 {unplanned} 件</span>
                                <input className="pe-reason" aria-label="理由" placeholder="写理由（必填）" value={l.unplanned_reason}
                                  onChange={(e) => edit(updateLine(draft, i, { unplanned_reason: e.target.value }))} />
                              </div>
                            )}
                          </td>
                          <td>
                            <input className="pe-num" inputMode="decimal" aria-label="单价" placeholder="未填" value={l.price}
                              onChange={(e) => edit(updateLine(draft, i, { price: e.target.value }))} />
                          </td>
                          <td>
                            <input className="pe-date" type="date" aria-label="预计到货" value={l.expect_arrive_date}
                              onChange={(e) => edit(updateLine(draft, i, { expect_arrive_date: e.target.value }))} />
                          </td>
                          <td>
                            <details className="pe-srcfold">
                              <summary>{l.sources.length} 条来源，{sourcesUnits(l)} 件</summary>
                              <ul className="pe-src" data-testid="sources">
                                {l.sources.map((s, j) => {
                                  const c = chainRef.get(s.line_id);
                                  return (
                                    <li key={`${s.line_id}|${s.seller_id}`}>
                                      {/* R3：一眼看出这件货是哪张计划哪一版哪条记录、哪家店的量 */}
                                      <span>{c ? chainText(c, shopName(s.seller_id), s.units)
                                        : `记录 ${s.line_id} ${l.sku} → ${shopName(s.seller_id)} ${s.units} 件`}</span>
                                      <button type="button" className="pe-x" onClick={() => edit(removeSource(draft, i, j))}>移除</button>
                                    </li>
                                  );
                                })}
                              </ul>
                            </details>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {!useLocal && gatesErr ? <ErrorDetail err={gatesErr} />
              : shownGates === null ? <div className="muted">正在检查…</div>
              : <GateList gates={shownGates} source={useLocal ? '本地检查，保存后以服务端为准' : '服务端检查'} />}
          </section>
        </div>

        {missing && (missing.length > 0 || invalid.length > 0) && (
          <div className="flash flash--bad" role="alert" data-testid="save-missing">
            {missing.length > 0 && <div>保存草稿前要先填：{missing.map(fieldLabel).join('、')}</div>}
            {invalid.length > 0 && <div data-testid="save-invalid">要填整数：{invalid.map(fieldLabel).join('、')}</div>}
          </div>
        )}
        {actErr && (
          <div>
            <ErrorDetail err={actErr} onDismiss={dismiss} />
            {actErr.error === 'stale_draft' && (
              <button type="button" className="btn" disabled={pending.has('reload')} onClick={() => void reload()}>重新载入</button>
            )}
          </div>
        )}

        {/* ─── 底部动作条：普通动作在左，⛔ 单独在右 ─── */}
        <div className="pe-bar" data-testid="po-actions">
          <button type="button" className="btn" disabled={pending.has('save')} onClick={() => void save(false)}>保存草稿</button>
          {poId !== null && (
            <button type="button" className="btn btn--danger" disabled={pending.has('delete')} onClick={() => setConfirmDelete(true)}>删除草稿</button>
          )}
          <span className="pe-bar__state" data-testid="save-state">{unsaved ? (poId === null ? '新建，未保存' : '有未保存的改动') : '已保存'}</span>
          <span className="pm-bar__sp" />
          <CommitButton hint={placeBlock} disabled={placeBlock !== null || pending.has('save')}
            onClick={() => (unsaved ? void save(true) : setPlacing(true))}>{unsaved ? '保存并下单' : '下单'}</CommitButton>
        </div>
      </div>

      {placing && po && (
        <PlaceDialog po={po} options={options} onClose={() => setPlacing(false)} onSettled={afterPlace}
          onStale={() => { if (poId !== null) loadPo(poId).catch((e: unknown) => fail('reload', e as ApiError)); }} />
      )}

      {confirmDelete && po && (
        <Modal title={`删除草稿 ${po.custom_order_sn}`} onClose={() => setConfirmDelete(false)}>
          <p>{po.lines.length} 行，{po.lines.reduce((a, l) => a + l.units, 0)} 件</p>
          <div className="btn-row">
            <button type="button" className="btn btn--primary" disabled={pending.has('delete')}
              onClick={() => void doDelete()}>删除草稿</button>
            <button type="button" className="btn btn--ghost" onClick={() => setConfirmDelete(false)}>取消</button>
          </div>
        </Modal>
      )}
    </AppShell>
  );
}
