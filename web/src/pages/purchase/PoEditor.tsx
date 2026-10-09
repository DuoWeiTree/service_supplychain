import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { useInFlight } from '../../shell/useInFlight';
import { useActionError } from '../../shell/useActionError';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Modal } from '../../components/Modal';
import { Qty } from '../../components/Qty';
import { PurchasePlaceholder } from '../PurchasePlaceholder';
import { api, ApiError } from '../../api';
import type { CandidateLine, CandidateLines, PoId, PreflightGate, PurchaseOptions, PurchaseOrder } from '../../api/types';
import {
  addSource, available, baselineOf, emptyDraft, fromPo, gateText, groupCandidates, lineUnits, QC_LABEL,
  removeSource, setHeader, setLineWid, sourcesUnits, toBody, unplannedUnits, updateLine, validate,
  type AddRefusal, type Baseline, type Draft, type DraftHeader, type QcType,
} from './poEditorModel';

// 非 ApiError（建连失败等）也要落成可显示明细：cause.code 是分辨「超时」与「连不上」的唯一线索
function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const err = e as { name?: string; message?: string; cause?: { code?: string } };
  return new ApiError(0, err.name ?? 'Error', err.message ?? String(e),
    err.cause?.code ? { cause_code: err.cause.code } : {});
}

const logFail = (name: string, err: ApiError, t0: number) => {
  console.warn(`[po-editor] ${name} failed`, JSON.stringify({
    status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
  }));
};

const EXCLUDED_LABEL: Record<keyof CandidateLines['excluded'], string> = {
  state: '状态不可组单', sku: '货号不符', category: '品类不符', period: '月份不符', fully_confirmed: '已组满',
};
const MISSING_LABEL: Record<string, string> = {
  supplier_id: '供应商', purchaser_id: '采购方', opt_uid: '采购员', qc_type: '质检方式', lines: '明细',
};
const REFUSAL_TEXT: Record<Exclude<AddRefusal, 'over_remaining'>, string> = {
  blocked: '无分店依据', no_seller: '这家店不在该记录里', bad_units: '加入数量要是正整数',
};

const MANUAL = '__manual__';

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
  useEffect(() => { if (!focused) setText(String(value)); }, [value, focused]);
  return (
    <input className="inp inp--tiny" type="number" min={0} step={1} aria-label="数量" value={text}
      onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
      onChange={(e) => {
        const v = e.target.value.trim();
        setText(e.target.value);
        if (v === '') onChange(null);
        else if (Number.isFinite(Number(v))) onChange(Math.trunc(Number(v)));
      }} />
  );
}

const countText = (source: string, count: number | null) => `（${source}${count === null ? '' : ` ${count} 单`}）`;

export function PoRoute() {
  const { poId } = useParams();
  const id = Number(poId);
  // key：/purchase/new 保存后跳到 /purchase/:id，必须换一个实例，不能带着新建态的本地状态
  return <PoEditor key={poId} poId={Number.isInteger(id) && id > 0 ? id : -1} />;
}

export function PoEditor({ poId }: { poId: PoId | null }) {
  const navigate = useNavigate();
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

  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [baseline, setBaseline] = useState<Baseline>(() => baselineOf(null));
  const [dirty, setDirty] = useState(false);
  const [addQty, setAddQty] = useState<Record<string, string>>({});
  const [refusal, setRefusal] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string[] | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const edit = (next: Draft) => { setDraft(next); setDirty(true); };

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
    loadCands();
    if (poId !== null) loadPo(poId).catch((e: unknown) => setPoErr(e as ApiError));
  }, [poId, loadCands, loadPo]);

  const groups = useMemo(() => groupCandidates(cands?.lines ?? []), [cands]);
  const candById = useMemo(() => new Map((cands?.lines ?? []).map((l) => [l.line_id, l])), [cands]);
  const localGates = useMemo(() => validate(draft), [draft]);
  const useLocal = poId === null || dirty;

  const doAdd = (c: CandidateLine, sid: string) => {
    const k = `${c.line_id}|${sid}`;
    const raw = (addQty[k] ?? '').trim();
    const r = addSource(draft, c, sid, raw === '' ? NaN : Number(raw), baseline);
    if (!r.ok) {
      setRefusal({ ...refusal, [k]: r.reason === 'over_remaining'
        ? `超过该店剩余，最多还能加 ${r.available ?? 0}` : REFUSAL_TEXT[r.reason] });
      return;
    }
    setRefusal(({ [k]: _drop, ...rest }) => rest);
    setAddQty(({ [k]: _drop, ...rest }) => rest);
    edit(r.draft);
  };

  const save = () => run('save', async () => {
    const r = toBody(draft);
    if (!r.ok) { setMissing(r.missing); return; }
    setMissing(null);
    const t0 = performance.now();
    try {
      if (poId === null) {
        const { po_id } = await api.createPurchaseOrder(r.body);
        console.info('[po-editor] createPurchaseOrder ok', JSON.stringify({ po_id, lines: r.body.lines.length,
          ms: Math.round(performance.now() - t0) }));
        succeed('save');
        pushToast({ kind: 'ok', text: `已保存草稿 SCM-PO-${po_id}` });
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
      const err = asApiError(e);
      logFail(poId === null ? 'createPurchaseOrder' : 'updatePurchaseOrder', err, t0);
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
      fail('reload', e as ApiError);
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
      const err = asApiError(e);
      logFail('deletePurchaseOrder', err, t0);
      setConfirmDelete(false);
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
    if (po.state !== '草稿' || po.discarded_at) return <PurchasePlaceholder crumb="采购单" />;
  }

  const title = po ? `采购单 ${po.custom_order_sn}` : '新建采购单';
  const h = draft.header;
  const setH = (patch: Partial<DraftHeader>) => edit(setHeader(draft, patch));
  const purchaserOpts: ActorOption[] | null = options && options.purchasers.map((p) => ({
    value: p.purchaser_id, text: `${p.purchaser_id}${countText(p.source, p.count)}`, history: p.source === '领星历史' }));
  const optUidOpts: ActorOption[] | null = options && options.opt_uids.map((p) => ({
    value: p.opt_uid, text: `${p.opt_uid}${p.name ? ` ${p.name}` : ''}${countText(p.source, p.count)}`,
    history: p.source === '领星历史' }));
  const suppliers = options?.suppliers ?? [];
  const warehouses = options?.warehouses ?? [];
  const shownGates = useLocal ? localGates : gates;
  const failing = shownGates?.filter((g) => !g.ok).length ?? 0;

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

      <div className="sec twocol">
        {/* ─── 左：可组单记录 ─── */}
        <div className="panel" data-testid="candidates">
          <div className="panel__head">可组单记录</div>
          <div className="panel__body">
            {candsErr && <ErrorDetail err={candsErr} />}
            {cands && (Object.keys(EXCLUDED_LABEL) as (keyof CandidateLines['excluded'])[])
              .filter((k) => (cands.excluded[k] ?? 0) > 0)
              .map((k) => <div className="dropline" key={k}>{EXCLUDED_LABEL[k]} {cands.excluded[k]} 条</div>)}
            {cands === null ? (!candsErr && <div className="empty">正在取可组单记录…</div>)
              : groups.length === 0 ? <div className="empty">没有可组单的记录</div>
              : groups.map((g) => (
                <div className="sec" key={g.sku} data-testid={`cand-sku-${g.sku}`}>
                  <div className="sec__title"><span className="num">{g.sku}</span></div>
                  {g.records.map((r) => (
                    <div key={r.line_id} data-testid={`cand-${r.line_id}`} className={r.blocked ? 'muted' : undefined}>
                      <div>计划 #{r.plan_id} {r.period} 剩 <span className="num">{r.remaining}/{r.total_units}</span></div>
                      {r.blocked ? (
                        <div>无分店依据：{r.blocked.sellers.join('、')}</div>
                      ) : (
                        <table className="table table--dense">
                          <thead><tr><th>店</th><th className="r">剩余</th><th>数量</th><th /></tr></thead>
                          <tbody>
                            {Object.keys(r.sellers).sort().map((sid) => {
                              const k = `${r.line_id}|${sid}`;
                              const avail = available(r, sid, draft, baseline);
                              return (
                                <tr key={sid} data-testid={`seller-${sid}`}>
                                  <td>{sid}</td>
                                  <td className="r" data-testid="avail">
                                    {avail === null ? <Qty v={{ kind: 'unknown' }} /> : avail}
                                  </td>
                                  <td>
                                    <input className="inp inp--tiny" type="number" min={1} step={1}
                                      aria-label={`店 ${sid} 加入数量`} disabled={!avail}
                                      value={addQty[k] ?? ''} onChange={(e) => setAddQty({ ...addQty, [k]: e.target.value })} />
                                  </td>
                                  <td>
                                    <button type="button" className="btn btn--sm" disabled={!avail}
                                      onClick={() => doAdd(r, sid)}>加入</button>
                                    {avail === 0 && <span className="muted"> 已无剩余</span>}
                                    {avail === null && <span className="muted"> 剩余未知</span>}
                                    {refusal[k] && <span className="i-red"> {refusal[k]}</span>}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      )}
                    </div>
                  ))}
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
                        {warehouses.map((w) => <option key={w.wid} value={w.wid}>{w.name}（{w.wid}）</option>)}
                        {l.wid && !warehouses.some((w) => w.wid === l.wid) && <option value={l.wid}>{l.wid}</option>}
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
                          店 {s.seller_id} 量 <span className="num">{s.units}</span>
                          {' '}<span className="muted">{c ? `计划 #${c.plan_id} ${c.period}` : `记录 #${s.line_id}`}</span>
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

      {missing && (
        <div className="flash flash--bad" role="alert" data-testid="save-missing">
          <div>保存草稿前要先填：{missing.map((k) => MISSING_LABEL[k] ?? k).join('、')}</div>
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
      </div>

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
