import { useCallback, useEffect, useMemo, useState } from 'react';
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
  addSource, available, baselineOf, chainText, emptyDraft, fromPo, gateText, groupCandidates, lineUnits, ownOnlyRecords,
  parseLineIds, planName, preselect, QC_LABEL, sourceSummary,
  recordRemaining, removeSource, setHeader, setLineWid, sourcesUnits, toBody, unplannedUnits, updateLine, validate,
  type AddRefusal, type Baseline, type Draft, type DraftHeader, type QcType, type SourceTarget,
} from './poEditorModel';
import { asApiError } from '../../api/client';
import { handlerThrew } from './fmt';

const logFail = (name: string, err: ApiError, t0: number) => {
  console.warn(`[po-editor] ${name} failed`, JSON.stringify({
    status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
  }));
};

const EXCLUDED_LABEL: Record<keyof CandidateLines['excluded'], string> = {
  state: '状态不可组单', sku: '货号不符', category: '品类不符', period: '月份不符', fully_confirmed: '已组满',
};
const fieldLabel = (k: string) => {
  const m = /^lines\[(\d+)\]\.wid$/.exec(k);
  return m ? `第 ${m[1]} 行仓` : MISSING_LABEL[k] ?? k;
};
const MISSING_LABEL: Record<string, string> = {
  supplier_id: '供应商', purchaser_id: '采购方', opt_uid: '采购员', qc_type: '质检方式', lines: '明细',
};
const REFUSAL_TEXT: Record<Exclude<AddRefusal, 'over_remaining'>, string> = {
  blocked: '无分店依据', no_seller: '这家店不在该记录里', bad_units: '加入数量要是正整数',
};

/** 带来的记录列不出时反查到的样子：组满了 / 状态走了 / 不存在 / 查不到 */
type Missing = { kind: 'pending' } | { kind: 'trace'; t: LineTrace } | { kind: 'gone' } | { kind: 'err'; err: ApiError };
const ORDERABLE_STATES: readonly string[] = ['已提交', '已确认'];
function missingText(id: number, m: Missing): string {
  if (m.kind === 'pending') return `记录 ${id} 正在查状态…`;
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
    <span className="field">
      <span className="lbl">{label}</span>
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
    </span>
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
      <input className="inp inp--tiny" type="number" min={0} step={1} aria-label="数量" value={text}
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
      {bad && <span className="i-red"> 数量要是非负整数，未采用</span>}
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
  const [search] = useSearchParams();
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
  const [showAll, setShowAll] = useState(false);
  const [missingInfo, setMissingInfo] = useState<Record<number, Missing>>({});

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

  // R3：从待采购带过来的记录（只对新建有意义；已保存的单以单里的来源为准）
  const wanted = useMemo(() => (poId === null ? parseLineIds(search.get('lines')) : null), [poId, search]);
  const sel = useMemo(() => (wanted && cands ? preselect(wanted.ids, cands.lines) : null), [wanted, cands]);
  const missingKey = sel?.missing.join(',') ?? '';
  useEffect(() => {
    if (!sel || sel.missing.length === 0) return;
    let alive = true;
    setMissingInfo(Object.fromEntries(sel.missing.map((id) => [id, { kind: 'pending' } as Missing])));
    for (const id of sel.missing) {
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
  const narrowed = sel !== null && !showAll;
  const groups = useMemo(() => groupCandidates(narrowed ? sel.shown : cands?.lines ?? []), [cands, sel, narrowed]);
  const candById = useMemo(() => new Map((cands?.lines ?? []).map((l) => [l.line_id, l])), [cands]);
  const localGates = useMemo(() => validate(draft), [draft]);
  const ownOnly = useMemo(() => (cands !== null || candsErr !== null ? ownOnlyRecords(po, cands?.lines ?? []) : []),
    [po, cands, candsErr]);
  const sellerName = useMemo(() => new Map((sellers ?? []).map((x) => [x.seller_id, x.name])), [sellers]);
  const shop = (sid: string) => { const n = sellerName.get(sid); return n ? `${n}（${sid}）` : sid; };
  const shopName = (sid: string) => sellerName.get(sid) ?? sid;
  const useLocal = poId === null || dirty;

  const doAdd = (c: SourceTarget, sid: string) => {
    const k = `${c.line_id}|${sid}`;
    const raw = (addQty[k] ?? '').trim();
    const r = addSource(draft, c, sid, raw === '' ? NaN : Number(raw), baseline);
    if (!r.ok) {
      setRefusal({ ...refusal, [k]: r.reason === 'over_remaining'
        ? `超过该店剩余，最多还能加 ${r.available ?? 0}` : REFUSAL_TEXT[r.reason] });
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

  const save = () => run('save', async () => {
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
        navigate(`/purchase/${po_id}`);
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
      await loadPo(poId).catch((e: unknown) => fail('reload', e as ApiError));
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
  const placeBlock: string | null = poId === null || dirty ? '先保存草稿'
    : po === null ? '正在取采购单…'
    : gatesErr ? '下单前检查取不到'
    : gates === null ? '正在检查…'
    : failingNames.length > 0 ? `${failingNames.join('、')} 未过` : null;
  const afterPlace = () => {
    setPlacing(false);
    if (onReroute) onReroute();
    else if (poId !== null) loadPo(poId).catch((e: unknown) => fail('reload', e as ApiError));
  };

  const sellerTable = (r: SourceTarget, saved?: Record<string, number>) => (
    <table className="table table--dense">
      <thead><tr><th>店</th>{saved && <th className="r">本单原有</th>}<th className="r">剩余</th><th>数量</th><th /></tr></thead>
      <tbody>
        {Object.keys(r.sellers).sort().map((sid) => {
          const k = `${r.line_id}|${sid}`;
          const avail = available(r, sid, draft, baseline);
          const off = avail === null || avail <= 0;
          return (
            <tr key={sid} data-testid={`seller-${sid}`}>
              <td>{shop(sid)}</td>
              {saved && <td className="r">{saved[sid] ?? 0}</td>}
              <td className="r" data-testid="avail">
                {avail === null ? <Qty v={{ kind: 'unknown' }} /> : avail}
              </td>
              <td>
                <input className="inp inp--tiny" type="number" min={1} step={1}
                  aria-label={`店 ${sid} 加入数量`} disabled={off}
                  value={addQty[k] ?? ''} onChange={(e) => setAddQty({ ...addQty, [k]: e.target.value })} />
              </td>
              <td>
                <button type="button" className="btn btn--sm" disabled={off} onClick={() => doAdd(r, sid)}>加入</button>
                {avail !== null && avail <= 0 && <span className="muted"> 已无剩余</span>}
                {avail === null && <span className="muted"> 剩余未知</span>}
                {refusal[k] && <span className="i-red"> {refusal[k]}</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  return (
    <AppShell crumb={po ? po.custom_order_sn : '新建采购单'}>
      <div className="head">
        <div className="head__main"><h1>{title}</h1></div>
        {dirty && <div className="head__meta">有未保存的改动</div>}
      </div>

      {/* ─── 单头 ─── */}
      {optionsErr && <ErrorDetail err={optionsErr} />}
      <div className="sec bar" data-testid="po-header">
        <span className="field">
          <span className="lbl">供应商</span>
          <select className="inp" aria-label="供应商" value={h.supplier_id} disabled={options === null && !optionsErr}
            onChange={(e) => setH({ supplier_id: e.target.value })}>
            <option value="">{options === null ? (optionsErr ? '选项取不到' : '正在取选项…') : '请选择'}</option>
            {suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.name}（{s.supplier_id}）</option>)}
            {h.supplier_id && !suppliers.some((s) => s.supplier_id === h.supplier_id) && (
              <option value={h.supplier_id}>{h.supplier_id}</option>
            )}
          </select>
        </span>
        <ActorSelect label="采购方" value={h.purchaser_id} options={purchaserOpts} loading={options === null && !optionsErr}
          onChange={(v) => setH({ purchaser_id: v })} />
        <ActorSelect label="采购员" value={h.opt_uid} options={optUidOpts} loading={options === null && !optionsErr}
          onChange={(v) => setH({ opt_uid: v })} />
        {options && !options.history.available && (
          <span className="field">
            <span className="muted" data-testid="history-unavailable">领星历史暂不可用</span>
            <Fold summary="原因">{options.history.cause ?? '后端未给原因'}</Fold>
          </span>
        )}
        {options && options.history.available && options.history.dropped > 0 && (
          <span className="dropline">领星历史有 {options.history.dropped} 行无法识别，未列入</span>
        )}
        <span className="field">
          <span className="lbl">质检方式</span>
          <select className="inp" aria-label="质检方式" value={h.qc_type === null ? '' : String(h.qc_type)}
            onChange={(e) => setH({ qc_type: e.target.value === '' ? null : (Number(e.target.value) as QcType) })}>
            <option value="">请选择</option>
            {([0, 1, 2] as const).map((q) => <option key={q} value={q}>{q} {QC_LABEL[q]}</option>)}
          </select>
        </span>
      </div>

      {wanted && (
        <div className="sec" data-testid="preselect">
          {cands !== null && sel !== null && (() => {
            const summary = sourceSummary(sel.shown);
            return summary && <div data-testid="source-summary"><strong>{summary}</strong></div>;
          })()}
          {(wanted.bad.length > 0 || (sel?.missing.length ?? 0) > 0) && (
            <ul className="i-red" data-testid="preselect-missing">
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
          {narrowed && (
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setShowAll(true)}>加入其他待采购记录</button>
          )}
        </div>
      )}

      <div className="sec twocol">
        {/* ─── 左：可组单记录 ─── */}
        <div className="panel" data-testid="candidates">
          <div className="panel__head">可组单记录</div>
          <div className="panel__body">
            {candsErr && <ErrorDetail err={candsErr} />}
            {sellersErr && (
              <div data-testid="seller-names-failed">
                <span className="muted">店名取不到，按店编号显示</span>
                <Fold summary="原因"><ErrorDetail err={sellersErr} /></Fold>
              </div>
            )}
            {cands && (Object.keys(EXCLUDED_LABEL) as (keyof CandidateLines['excluded'])[])
              .filter((k) => (cands.excluded[k] ?? 0) > 0)
              .map((k) => <div className="dropline" key={k}>{EXCLUDED_LABEL[k]} {cands.excluded[k]} 条</div>)}
            {cands === null ? (!candsErr && <div className="empty">正在取可组单记录…</div>)
              : groups.length === 0 ? <div className="empty">{narrowed ? '带来的记录没有一条可组单' : '没有可组单的记录'}</div>
              : groups.map((g) => (
                <div className="sec" key={g.sku} data-testid={`cand-sku-${g.sku}`}>
                  <div className="sec__title"><span className="num">{g.sku}</span></div>
                  {g.records.map((r) => (
                    <div key={r.line_id} data-testid={`cand-${r.line_id}`} className={r.blocked ? 'muted' : undefined}>
                      <div>{planName(r)} rev{r.rev} {r.period} 剩 <span className="num">{recordRemaining(r, draft, baseline)}/{r.total_units}</span></div>
                      {r.blocked ? (
                        <div>无分店依据：{r.blocked.sellers.map(shop).join('、')}</div>
                      ) : sellerTable(r)}
                    </div>
                  ))}
                </div>
              ))}
            {ownOnly.map((o) => (
              <div className="sec" key={`own-${o.target.line_id}`} data-testid={`own-${o.target.line_id}`}>
                <div className="sec__title"><span className="num">{o.target.sku}</span> 本单原有来源</div>
                <div className="muted">记录 #{o.target.line_id}</div>
                <Fold summary="为什么单列">本单把这条记录组满了，候选不再列出它；这里只能加回本单原有的量。</Fold>
                {sellerTable(o.target, o.saved)}
              </div>
            ))}
          </div>
        </div>

        {/* ─── 右：明细 ─── */}
        <div className="panel" data-testid="po-lines">
          <div className="panel__head">明细</div>
          <div className="panel__body">
            {draft.lines.length === 0 ? <div className="empty">还没有明细</div> : draft.lines.map((l, i) => {
              const unplanned = unplannedUnits(l);
              return (
                <div className="sec" key={`${l.sku}#${i}`} data-testid={`po-line-${i + 1}`}>
                  <div className="bar">
                    <span className="num">{l.sku}</span>
                    <span className="field"><span className="lbl">仓</span>
                      <select className="inp" aria-label="仓" value={l.wid} onChange={(e) => edit(setLineWid(draft, i, e.target.value))}>
                        <option value="">{options === null ? (optionsErr ? '仓取不到' : '正在取仓…') : '请选择'}</option>
                        {warehouses.map((w) => <option key={w.wid} value={String(w.wid)}>{w.name}（{w.wid}）</option>)}
                        {l.wid && !warehouses.some((w) => String(w.wid) === l.wid) && <option value={l.wid}>{l.wid}</option>}
                      </select>
                    </span>
                    <span className="field"><span className="lbl">数量</span>
                      <UnitsInput value={lineUnits(l)}
                        onChange={(v) => edit(updateLine(draft, i, { units: v }))} />
                    </span>
                    <span className="field"><span className="lbl">单价</span>
                      <input className="inp inp--tiny" inputMode="decimal" aria-label="单价" value={l.price}
                        onChange={(e) => edit(updateLine(draft, i, { price: e.target.value }))} />
                    </span>
                    <span className="field"><span className="lbl">预计到货</span>
                      <input className="inp" type="date" aria-label="预计到货" value={l.expect_arrive_date}
                        onChange={(e) => edit(updateLine(draft, i, { expect_arrive_date: e.target.value }))} />
                    </span>
                  </div>
                  <ul data-testid="sources">
                    {l.sources.map((s, j) => {
                      const c = candById.get(s.line_id);
                      return (
                        <li key={`${s.line_id}|${s.seller_id}`}>
                          {/* R3：一眼看出这件货是哪张计划哪一版哪条记录、哪家店的量 */}
                          {c ? chainText(c, shopName(s.seller_id), s.units)
                            : `记录 ${s.line_id} ${l.sku} → ${shopName(s.seller_id)} ${s.units} 件`}
                          {' '}<button type="button" className="btn btn--sm btn--ghost"
                            onClick={() => edit(removeSource(draft, i, j))}>移除</button>
                        </li>
                      );
                    })}
                  </ul>
                  {unplanned > 0 && (
                    <div className="bar">
                      <span className="i-red">无计划来源 {unplanned} 件</span>
                      <span className="field"><span className="lbl">理由</span>
                        <input className="inp inp--text" aria-label="理由" value={l.unplanned_reason}
                          onChange={(e) => edit(updateLine(draft, i, { unplanned_reason: e.target.value }))} />
                      </span>
                    </div>
                  )}
                  {unplanned < 0 && <div className="i-red">少于来源合计 {sourcesUnits(l)} 件</div>}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ─── 下单前检查 ─── */}
      <div className="panel sec" data-testid="preflight">
        <div className="panel__head">下单前检查</div>
        <div className="panel__body">
          {useLocal && poId !== null && <div className="muted">按未保存的内容检查</div>}
          {!useLocal && gatesErr && <ErrorDetail err={gatesErr} />}
          {shownGates === null ? (!gatesErr && <div className="muted">正在检查…</div>) : (
            <>
              <div className="gate__sum">{failing === 0 ? '全部通过' : `${failing} 项未过`}</div>
              <ul>
                {shownGates.map((g, i) => (
                  <li key={`${g.gate}-${i}`} className={`gate__item gate__item--${g.ok ? 'pass' : 'fail'}`}>
                    <span>{g.ok ? '通过' : '未过'}</span>
                    <span>{gateText(g)}</span>
                    <span className="gate__code">{g.gate}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
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

      <div className="sec btn-row">
        <button type="button" className="btn btn--primary" disabled={pending.has('save')} onClick={() => void save()}>保存草稿</button>
        {poId !== null && (
          <button type="button" className="btn" disabled={pending.has('delete')} onClick={() => setConfirmDelete(true)}>删除草稿</button>
        )}
        <div className="btn-row btn-row__end">
          {placeBlock && <span className="muted" data-testid="place-blocked">{placeBlock}</span>}
          <button type="button" className="btn btn--danger" disabled={placeBlock !== null}
            onClick={() => setPlacing(true)}>⛔ 下单</button>
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
