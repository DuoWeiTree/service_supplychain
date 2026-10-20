import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppShell } from '../../shell/AppShell';
import { useInFlight } from '../../shell/useInFlight';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty } from '../../components/Qty';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { AttentionItem, LineTrace, PoId, PoTimeline, PurchaseOptions, PurchaseOrder, ReadBackResult, Seller } from '../../api/types';
import { AttemptTable, AttentionPanel } from './AttentionPanel';
import { exactTotal, fmtAt } from './fmt';
import { KIND_LABEL, inFlightCreate, isEditable, usePoll } from './poStatus';
import { QC_LABEL, chainText } from './poEditorModel';
import { homeHref } from './purchaseHomeModel';
import { PoCrumbs, StatusPair, type LocalState } from './parts';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';

type TraceState = { kind: 'loading' } | { kind: 'ok'; t: LineTrace } | { kind: 'fail'; err: ApiError };
type ReadBack = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ok'; r: ReadBackResult } | { kind: 'fail'; err: ApiError };


/** 各读各自落地、各记各的错；返回的 promise 在这一读落地后才 resolve，写动作之后可以等它 */
function read<T>(name: string, f: () => Promise<T>, ok: (v: T) => void, bad: (e: ApiError | null) => void): Promise<void> {
  const t0 = performance.now();
  return Promise.resolve().then(f).then((v) => { ok(v); bad(null); }, (e: unknown) => {
    const err = asApiError(e);
    console.warn(`[po-detail] ${name} failed`, JSON.stringify({
      status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
    }));
    bad(err);
  });
}

/** 已下单 / 待处理 / 已作废 的采购单。`onEditable`：处理完（如确认领星无此单）草稿解锁时交回路由 */
export function PoDetail({ poId, onEditable }: { poId: PoId; onEditable?: () => void }) {
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [poErr, setPoErr] = useState<ApiError | null>(null);
  const [tl, setTl] = useState<PoTimeline | null>(null);
  const [tlErr, setTlErr] = useState<ApiError | null>(null);
  const [queue, setQueue] = useState<AttentionItem[] | null>(null);
  const [queueErr, setQueueErr] = useState<ApiError | null>(null);
  const [traces, setTraces] = useState<Record<number, TraceState>>({});
  const [rb, setRb] = useState<ReadBack>({ kind: 'idle' });
  // 名字与编辑器同一套来源：取不到退回编号并点名，不拖住单本身
  const [options, setOptions] = useState<PurchaseOptions | null>(null);
  const [optionsErr, setOptionsErr] = useState<ApiError | null>(null);
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [sellersErr, setSellersErr] = useState<ApiError | null>(null);
  const { pending, run } = useInFlight();

  const load = useCallback(async () => {
    await Promise.all([
      read('getPurchaseOrder', () => api.getPurchaseOrder(poId), setPo, setPoErr),
      read('getTimeline', () => api.getTimeline(poId), setTl, setTlErr),
      read('listAttention', () => api.listAttention(), (q) => setQueue(q.items), setQueueErr),
    ]);
  }, [poId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void read('getPurchaseOptions', () => api.getPurchaseOptions(), setOptions, setOptionsErr);
    void read('listSellers', () => api.listSellers(), setSellers, setSellersErr);
  }, []);
  const names = useMemo(() => ({
    supplier: new Map((options?.suppliers ?? []).map((x) => [x.supplier_id, x.name])),
    wid: new Map((options?.warehouses ?? []).map((x) => [x.wid, x.name])),
    opt: new Map((options?.opt_uids ?? []).flatMap((x) => (x.name ? [[x.opt_uid, x.name] as const] : []))),
    shop: new Map((sellers ?? []).map((x) => [x.seller_id, x.name])),
  }), [options, sellers]);

  // 已下单的单：打开即现查领星（只读），库里那条观察可能是很久以前的
  const readBack = useCallback(async () => {
    const t0 = performance.now();
    setRb({ kind: 'loading' });
    try {
      const r = await api.readBack(poId);
      console.info('[po-detail] read-back', JSON.stringify({ po_id: poId, checked: r.checked, read_outcome: r.read_outcome,
        call: r.read_back_call, status: r.lx?.status ?? null, problems: r.problems.length, ms: Math.round(performance.now() - t0) }));
      setRb({ kind: 'ok', r });
      setPo((p) => (p ? { ...p, lx: r.lx, is_void: r.lx?.status === -1 ? true : p.is_void } : p));
      // 核对出不一致 / 看到作废：待处理项与额度变了，整页重取
      if (r.problems.length > 0 || r.lx?.status === -1) await load();
    } catch (e) {
      const err = asApiError(e);
      console.warn('[po-detail] read-back failed', JSON.stringify({ po_id: poId, status: err.status, error: err.error,
        fields: err.fields, ms: Math.round(performance.now() - t0) }));
      setRb({ kind: 'fail', err });
    }
  }, [poId, load]);
  const placed = po?.state === '已下单' && !!po.order_sn;
  useEffect(() => { if (placed) void readBack(); }, [placed, readBack]);

  const stuck = (queue ?? []).filter((i) => i.po_id === poId && i.kind === 'stuck_call');

  useEffect(() => {
    // 队列没取到时不判可编辑：卡住的调用只在队列里，判了就可能把人送回一张正在下单的草稿
    if (po && queue !== null && tl !== null && onEditable && isEditable(po, stuck.length, tl)) onEditable();
  }, [po, queue, tl]);   // eslint-disable-line react-hooks/exhaustive-deps

  const placing = tl ? inFlightCreate(tl) : null;
  // 下单在途：等它落地再按结果重取。被拒 / 没发出的结果编辑器上看不到，这里用 toast 说出来
  usePoll(placing !== null, async () => {
    const t = await api.getTimeline(poId);
    setTl(t);
    if (inFlightCreate(t)) return false;
    const done = t.attempts.find((a) => a.call_id === placing?.call_id);
    console.info('[po-detail] in-flight create settled', JSON.stringify({ po_id: poId, call_id: done?.call_id,
      outcome: done?.outcome, lx_code: done?.lx_code, cause: done?.cause }));
    if (done?.outcome === 'rejected') {
      pushToast({ kind: 'fail', text: done.rejected_kind === 'auth'
        ? `领星接口无权限或授权失效（code ${done.lx_code ?? '无'}，接口 ${done.path ?? '?'}），改草稿没用，需领星管理员开通：${done.lx_msg ?? '没有说明'}`
        : `领星拒绝了这张单（code ${done.lx_code ?? '无'}）：${done.lx_msg ?? '没有说明'}` });
    } else if (done?.outcome === 'not_sent') {
      pushToast({ kind: 'warn', text: '没有发到领星，可以再次下单' });
    }
    refreshAttention();
    await load();
    return true;
  }, 'po-detail');

  const lineIds = po ? [...new Set(po.lines.flatMap((l) => l.sources.map((s) => s.line_id)))] : [];
  const lineKey = lineIds.join(',');
  useEffect(() => {
    for (const id of lineIds) {
      setTraces((m) => ({ ...m, [id]: { kind: 'loading' } }));
      void read(`getLineTrace#${id}`, () => api.getLineTrace(id),
        (t) => setTraces((m) => ({ ...m, [id]: { kind: 'ok', t } })),
        (err) => { if (err) setTraces((m) => ({ ...m, [id]: { kind: 'fail', err } })); });
    }
  }, [lineKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 读失败后不用整页刷新：同一个 load 再跑一遍，各读各自落地
  const retry = (
    <button type="button" className="btn btn--sm" disabled={pending.has('reload')}
      onClick={() => void run('reload', load)}>重新载入</button>
  );

  if (po === null) {
    return (
      <AppShell crumb="采购单" wide>
        <div className="head"><div className="head__main"><h1>采购单 {poId}</h1></div></div>
        {poErr ? <><ErrorDetail err={poErr} />{retry}</> : <div className="empty">正在取采购单…</div>}
      </AppShell>
    );
  }

  const hasAttention = po.attention.length > 0 || stuck.length > 0 || placing !== null;
  const whName = (wid: number | null) => (wid === null ? '—' : names.wid.get(wid) ?? String(wid));
  const units = po.lines.reduce((a, l) => a + l.units, 0);
  const amount = exactTotal(po.lines.map((l) => ({ units: l.units, price: l.price ?? '' })));
  const home = homeHref(po.lines.flatMap((l) => l.sources));
  const localState: LocalState = po.discarded_at ? '草稿（已作废）' : po.state === '已下单' ? '已下单' : '草稿';

  return (
    <AppShell crumb={po.custom_order_sn} wide>
      <div className="head">
        <div className="head__main">
          <PoCrumbs home={home} here={po.custom_order_sn} />
          <h1>采购单 {po.custom_order_sn}</h1>
        </div>
      </div>

      {poErr && <ErrorDetail err={poErr} />}
      {(poErr || tlErr || queueErr) && retry}
      {queueErr && (
        <div>
          <span className="muted">待处理队列取不到，卡住的调用可能没显示</span>
          <Fold summary="原因"><ErrorDetail err={queueErr} /></Fold>
        </div>
      )}

      <header className="pdt-head sec" data-testid="po-detail-head">
        <div className="pdt-meta">
          <span>供应商 <b>{names.supplier.get(po.supplier_id) ?? po.supplier_id}</b></span>
          <span>领星单号 <b className="num">{po.order_sn ?? (po.state === '草稿' ? '未下单' : <Qty v={{ kind: 'unknown' }} />)}</b></span>
          {po.placed_at && <span>下单于 <b className="num">{fmtAt(po.placed_at)}</b></span>}
          <span>采购方 <b className="num">{po.purchaser_id}</b></span>
          <span>采购员 <b>{names.opt.get(po.opt_uid) ?? po.opt_uid}</b></span>
          <span>质检 <b>{QC_LABEL[po.qc_type] ?? po.qc_type}</b></span>
          <span>仓 <b>{whName(po.wid)}</b></span>
          {(optionsErr || sellersErr) && (
            <span data-testid="names-failed">
              <span className="muted">名称取不到，按编号显示</span>
              <Fold summary="原因">{optionsErr && <ErrorDetail err={optionsErr} />}{sellersErr && <ErrorDetail err={sellersErr} />}</Fold>
            </span>
          )}
        </div>
        <StatusPair local={localState} lx={po.lx} reading={rb.kind === 'loading'}
          readError={rb.kind === 'ok' && !rb.r.checked ? (rb.r.read_outcome ?? '无结果') : rb.kind === 'fail' ? `读回请求失败：${rb.err.hint}` : null}
          onReread={placed ? () => void readBack() : undefined}>
          {rb.kind === 'ok' && !rb.r.checked && rb.r.read_cause && (
            <div className="sp-err"><Fold summary="读回失败原因" testId="lx-fail-cause">{rb.r.read_cause}（调用 #{rb.r.read_back_call ?? '?'}）</Fold></div>
          )}
          {rb.kind === 'fail' && <div className="sp-err"><Fold summary="读回失败原因" testId="lx-fail-cause"><ErrorDetail err={rb.err} /></Fold></div>}
        </StatusPair>
      </header>

      {hasAttention && (
        <AttentionPanel po={po} timeline={tl} timelineErr={tlErr} stuck={stuck} placing={placing} onChanged={load} />
      )}

      <div className="sec pe-pane__head">
        <span className="pe-pane__title">明细</span>
        <span className="pe-pane__sub" data-testid="detail-sum">
          {po.lines.length} 行，{units} 件，金额 {amount ?? <Qty v={{ kind: 'unknown' }} />}
        </span>
      </div>
      <div className="pt-wrap">
        <table className="pt">
          <thead><tr>
            <th className="r">行</th><th>货号</th><th>仓</th><th className="r">数量</th><th className="r">单价</th>
            <th className="r">无计划来源</th><th>领星行号</th><th>来源</th>
          </tr></thead>
          <tbody>
            {po.lines.map((l) => (
              <tr key={l.line_no} data-testid="detail-line">
                <td className="r">{l.line_no}</td>
                <td className="pt-sku num">{l.sku}</td>
                <td>{whName(l.wid)}</td>
                <td className="r num">{l.units}</td>
                <td className="r num">{l.price}</td>
                <td className="r"><Qty v={{ kind: 'num', value: l.unplanned_units }} />{l.unplanned_reason && <div className="muted">{l.unplanned_reason}</div>}</td>
                <td className="num">{l.lx_item_id ?? <span className="pt-unknown">未回填</span>}</td>
                <td>
                  {l.sources.length === 0 && <span className="muted">无</span>}
                  {l.sources.map((s) => {
                    const tr = traces[s.line_id];
                    return (
                      <div key={`${s.line_id}|${s.seller_id}`}>
                        {/* R3：来源链由单自己带，不等反查 */}
                        {chainText(s, names.shop.get(s.seller_id) ?? s.seller_id, s.units)}{' '}
                        {(!tr || tr.kind === 'loading') && <span className="muted">记录 #{s.line_id} 正在反查…</span>}
                        {tr?.kind === 'ok' && (
                          <span className="muted" data-testid={`trace-${s.line_id}`}>
                            记录 #{tr.t.line_id} {tr.t.period} {tr.t.state} 已确认 {tr.t.confirmed_units} 已下单 {tr.t.ordered_units}
                          </span>
                        )}
                        {tr?.kind === 'fail' && (
                          <span>
                            <span className="muted">记录 #{s.line_id} </span><span className="i-red">反查取不到</span>
                            <Fold summary="原因"><ErrorDetail err={tr.err} /></Fold>
                          </span>
                        )}
                      </div>
                    );
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="sec">
        {!hasAttention && tlErr && <ErrorDetail err={tlErr} />}
        {tl && !hasAttention && (
          <Fold summary={`调用记录 ${tl.attempts.length} 次`}>
            <AttemptTable attempts={tl.attempts} />
          </Fold>
        )}
        {tl && (
          <Fold summary={`状态记录 ${tl.events.length + tl.attention.filter((a) => a.resolved_at).length} 条`}>
            <table className="table table--dense" data-testid="events">
              <thead><tr><th>时间</th><th>人</th><th>事</th><th>理由</th></tr></thead>
              <tbody>
                {tl.events.map((e, i) => (
                  <tr key={`e${i}`}><td className="num">{fmtAt(e.at)}</td><td>{e.actor}</td>
                    <td>{e.from} → {e.to}{e.src ? <span className="muted"> {e.src}</span> : null}</td><td>{e.reason ?? '—'}</td></tr>
                ))}
                {tl.attention.filter((a) => a.resolved_at).map((a) => (
                  <tr key={`a${a.attention_id}`}><td className="num">{fmtAt(a.resolved_at)}</td><td>{a.resolved_by ?? '—'}</td>
                    <td>{KIND_LABEL[a.kind]}：{a.resolution ?? '—'}</td><td>{a.reason ?? '—'}</td></tr>
                ))}
              </tbody>
            </table>
          </Fold>
        )}
      </div>
    </AppShell>
  );
}
