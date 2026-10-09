import { useCallback, useEffect, useState } from 'react';
import { AppShell } from '../../shell/AppShell';
import { useInFlight } from '../../shell/useInFlight';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty } from '../../components/Qty';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { AttentionItem, LineTrace, PoId, PoTimeline, PurchaseOrder } from '../../api/types';
import { AttemptTable, AttentionPanel } from './AttentionPanel';
import { fmtAt } from './fmt';
import { KIND_LABEL, inFlightCreate, isEditable, usePoll } from './poStatus';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';

type TraceState = { kind: 'loading' } | { kind: 'ok'; t: LineTrace } | { kind: 'fail'; err: ApiError };

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
  const { pending, run } = useInFlight();

  const load = useCallback(async () => {
    await Promise.all([
      read('getPurchaseOrder', () => api.getPurchaseOrder(poId), setPo, setPoErr),
      read('getTimeline', () => api.getTimeline(poId), setTl, setTlErr),
      read('listAttention', () => api.listAttention(), (q) => setQueue(q.items), setQueueErr),
    ]);
  }, [poId]);

  useEffect(() => { void load(); }, [load]);

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
      pushToast({ kind: 'fail', text: `领星拒绝了这张单（code ${done.lx_code ?? '无'}）：${done.lx_msg ?? '没有说明'}` });
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
      <AppShell crumb="采购单">
        <div className="head"><div className="head__main"><h1>采购单 {poId}</h1></div></div>
        {poErr ? <><ErrorDetail err={poErr} />{retry}</> : <div className="empty">正在取采购单…</div>}
      </AppShell>
    );
  }

  const hasAttention = po.attention.length > 0 || stuck.length > 0 || placing !== null;
  const localState = po.discarded_at ? `${po.state}（已作废）` : po.state;

  return (
    <AppShell crumb={po.custom_order_sn}>
      <div className="head">
        <div className="head__main"><h1>采购单 {po.custom_order_sn}</h1></div>
      </div>

      {poErr && <ErrorDetail err={poErr} />}
      {(poErr || tlErr || queueErr) && retry}
      {queueErr && (
        <div>
          <span className="muted">待处理队列取不到，卡住的调用可能没显示</span>
          <Fold summary="原因"><ErrorDetail err={queueErr} /></Fold>
        </div>
      )}

      {hasAttention && (
        <AttentionPanel po={po} timeline={tl} timelineErr={tlErr} stuck={stuck} placing={placing} onChanged={load} />
      )}

      <div className="sec bar" data-testid="po-detail-head">
        <span className="field" data-testid="po-axes">
          <span className="lbl">本地状态</span><span>{localState}</span>
          <span className="sep" />
          <span className="lbl">领星状态</span><span>{po.is_void ? '已作废' : '未读回'}</span>
        </span>
        <span className="field">
          <span className="lbl">领星单号</span>
          <span className="num">{po.order_sn ?? (po.state === '草稿' ? '未下单' : <Qty v={{ kind: 'unknown' }} />)}</span>
        </span>
        <span className="field"><span className="lbl">幂等键</span><span className="num">{po.custom_order_sn}</span></span>
        {po.placed_at && <span className="field"><span className="lbl">下单时间</span><span className="num">{fmtAt(po.placed_at)}</span></span>}
        <span className="field"><span className="lbl">供应商</span><span>{po.supplier_id}</span></span>
      </div>

      <div className="table-scroll sec">
        <table className="table table--dense">
          <thead><tr>
            <th className="r">行</th><th>货号</th><th>仓</th><th className="r">数量</th><th className="r">单价</th>
            <th className="r">无计划来源</th><th>领星行号</th><th>来源</th>
          </tr></thead>
          <tbody>
            {po.lines.map((l) => (
              <tr key={l.line_no} data-testid="detail-line">
                <td className="r">{l.line_no}</td>
                <td className="num">{l.sku}</td>
                <td>{l.wid}</td>
                <td className="r num">{l.units}</td>
                <td className="r num">{l.price}</td>
                <td className="r"><Qty v={{ kind: 'num', value: l.unplanned_units }} />{l.unplanned_reason && <div className="muted">{l.unplanned_reason}</div>}</td>
                <td className="num">{l.lx_item_id ?? <span className="muted">未回填</span>}</td>
                <td>
                  {l.sources.length === 0 && <span className="muted">无</span>}
                  {l.sources.map((s) => {
                    const tr = traces[s.line_id];
                    return (
                      <div key={`${s.line_id}|${s.seller_id}`}>
                        店 {s.seller_id} 量 <span className="num">{s.units}</span>{' '}
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
