import { useEffect, useState } from 'react';
import { Modal } from '../../components/Modal';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty } from '../../components/Qty';
import { useInFlight } from '../../shell/useInFlight';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { PlaceResult, PoTimeline, PurchaseOptions, PurchaseOrder } from '../../api/types';
import { QC_LABEL } from './poEditorModel';
import { exactTotal, fmtAt, handlerThrew, logWrite, logWriteFail } from './fmt';
import { isCreate, isUnknownOutcome, lastCreateId, usePoll } from './poStatus';

type Res =
  | { kind: 'rejected'; code: string | number | null; msg: string | null }
  | { kind: 'not_sent'; cause: string | null }
  | { kind: 'stale' }
  | { kind: 'unknown'; err: ApiError | null; note: string }
  | { kind: 'error'; err: ApiError };

/** ⛔ 下单确认。★ 打开时把单冻结成快照：屏上看到的就是发出去的，token 也是这一份的 ——
 *  打开期间草稿被别人改了，后端按 token 拒（stale_confirm_token），不会悄悄发出新内容。 */
export function PlaceDialog({ po, options, onClose, onSettled, onStale }: {
  po: PurchaseOrder;
  options: PurchaseOptions | null;
  onClose: () => void;
  /** 单的状态已变（已下单 / 待核实 / 不知道）：交给路由重新按状态分发 */
  onSettled: () => void;
  onStale: () => void;
}) {
  const [snap] = useState(po);
  const { pending, run } = useInFlight();
  const [res, setRes] = useState<Res | null>(null);
  // 打开时记下已有的最后一次下单调用：结果未知后只认它之后的那次，不把上一轮的结果当成这一轮的
  const [baseline, setBaseline] = useState<number | null>(null);
  const [baselineErr, setBaselineErr] = useState<ApiError | null>(null);
  const busy = pending.has('place');
  const unknown = res?.kind === 'unknown';

  const supplier = options?.suppliers.find((x) => x.supplier_id === snap.supplier_id);
  const optUid = options?.opt_uids.find((o) => o.opt_uid === snap.opt_uid);
  const wh = (wid: number) => {
    const w = options?.warehouses.find((x) => x.wid === wid);
    return w ? `${w.name}（${wid}）` : wid;
  };
  const units = snap.lines.reduce((a, l) => a + l.units, 0);
  const amount = exactTotal(snap.lines);

  useEffect(() => {
    const t0 = performance.now();
    Promise.resolve().then(() => api.getTimeline(snap.po_id)).then(
      (t) => setBaseline(lastCreateId(t)),
      (e: unknown) => { const err = asApiError(e); logWriteFail('po-place', `GET /purchase-orders/${snap.po_id}/timeline`, t0, err); setBaselineErr(err); },
    );
  }, [snap.po_id]);

  const settleFrom = async (t: PoTimeline): Promise<boolean> => {
    const mine = t.attempts.filter((a) => isCreate(a) && a.call_id > (baseline ?? 0)).at(-1);
    if (!mine) { setRes((r) => (r?.kind === 'unknown' ? { ...r, note: '后端还没有这次下单的记录' } : r)); return false; }
    if (mine.outcome === 'in_flight') {
      setRes((r) => (r?.kind === 'unknown' ? { ...r, note: `下单进行中，开始于 ${fmtAt(mine.at)}` } : r));
      return false;
    }
    console.info('[po-place] settled by timeline', JSON.stringify({ po_id: snap.po_id, call_id: mine.call_id,
      outcome: mine.outcome, http: mine.http_status, lx_code: mine.lx_code, cause: mine.cause }));
    refreshAttention();
    if (mine.outcome === 'rejected') { setRes({ kind: 'rejected', code: mine.lx_code, msg: mine.lx_msg }); return true; }
    if (mine.outcome === 'not_sent') { setRes({ kind: 'not_sent', cause: mine.cause }); return true; }
    const p = await api.getPurchaseOrder(snap.po_id);
    if (p.state === '已下单') pushToast({ kind: 'ok', text: `已下单，领星单号 ${p.order_sn ?? '未知'}` });
    else pushToast({ kind: 'warn', text: '下单结果未知，待核实' });
    onSettled();
    return true;
  };
  usePoll(unknown && baseline !== null, async () => settleFrom(await api.getTimeline(snap.po_id)), 'po-place');

  const confirm = () => run('place', async () => {
    try {
      await doPlace();
    } catch (e) {
      setRes({ kind: 'error', err: handlerThrew('po-place', 'place', e) });
    }
  });

  const doPlace = async () => {
    const call = `POST /purchase-orders/${snap.po_id}/place`;
    const t0 = performance.now();
    let r: PlaceResult;
    try {
      r = await api.placePurchaseOrder(snap.po_id, snap.confirm_token);
    } catch (e) {
      const err = e instanceof ApiError ? e : handlerThrew('po-place', 'place', e);
      logWriteFail('po-place', call, t0, err);
      refreshAttention();
      if (err.error === 'stale_confirm_token') { setRes({ kind: 'stale' }); onStale(); return; }
      // 状态在别处变了：回到按状态分发
      // po_placing：本次请求被在途锁挡下、什么也没建；在途的那次（可能是别的页签）在详情页「下单进行中」里等
      if (err.error === 'needs_verify' || err.error === 'po_frozen' || err.error === 'mismatch_open'
          || err.error === 'po_placing') {
        pushToast({ kind: 'warn', text: err.hint });
        onSettled();
        return;
      }
      // 没拿到明确答复 / 已有下单在途：请求可能已在建单 —— 关掉确认，按调用记录等真实结果
      if (isUnknownOutcome(err)) {
        setRes({ kind: 'unknown', err, note: '正在查调用记录' });
        return;
      }
      setRes({ kind: 'error', err });
      return;
    }
    logWrite('po-place', call, t0, { po_id: snap.po_id, outcome: r.outcome, erp_call_id: r.erp_call_id ?? null,
      ...(r.outcome === 'rejected' ? { lx_code: r.lx_code, lx_msg: r.lx_msg } : {}),
      ...(r.outcome === 'not_sent' ? { cause: r.cause } : {}),
      ...(r.outcome === 'needs_verify' ? { lookup_outcome: r.lookup_outcome ?? null, truncated: r.truncated ?? null } : {}) });
    refreshAttention();
    if (r.outcome === 'placed') {
      pushToast({ kind: 'ok', text: `已下单，领星单号 ${r.order_sn ?? '未知'}` });
      onSettled();
    } else if (r.outcome === 'needs_verify') {
      pushToast({ kind: 'warn', text: '下单结果未知，待核实' });
      onSettled();
    } else if (r.outcome === 'rejected') {
      setRes({ kind: 'rejected', code: r.lx_code, msg: r.lx_msg });
    } else if (r.outcome === 'not_sent') {
      setRes({ kind: 'not_sent', cause: r.cause });
    } else {
      // ★ 不可逆路径上认不出的结果只能是「不知道」，绝不默认成「没发出」
      setRes({ kind: 'unknown', err: null, note: `后端回了认不出的结果：${String((r as { outcome?: unknown }).outcome)}` });
    }
  };

  return (
    <Modal danger warn="这一步会在领星建出真实采购单，不可撤销" title="⛔ 下单到领星"
      onClose={busy || unknown ? () => {} : onClose}>
      <table className="table table--dense" data-testid="place-header">
        <tbody>
          <tr><th>供应商</th><td>{supplier ? `${supplier.name}（${snap.supplier_id}）` : snap.supplier_id}</td></tr>
          <tr><th>采购方</th><td>{snap.purchaser_id}</td></tr>
          <tr><th>采购员</th><td>{snap.opt_uid}{optUid?.name ? ` ${optUid.name}` : ''}</td></tr>
          <tr><th>质检方式</th><td>{snap.qc_type} {QC_LABEL[snap.qc_type]}</td></tr>
          <tr><th>幂等键</th><td className="num">{snap.custom_order_sn}</td></tr>
        </tbody>
      </table>
      <div className="table-scroll mt">
        <table className="table table--dense">
          <thead><tr><th>货号</th><th>仓</th><th className="r">数量</th><th className="r">单价</th><th className="r">其中无计划来源</th></tr></thead>
          <tbody>
            {snap.lines.map((l) => (
              <tr key={l.line_no} data-testid="place-line">
                <td className="num">{l.sku}</td>
                <td>{wh(l.wid)}</td>
                <td className="r num">{l.units}</td>
                <td className="r num">{l.price}</td>
                <td className="r" data-testid="unplanned"><Qty v={{ kind: 'num', value: l.unplanned_units }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt" data-testid="place-total">
        合计 {units} 件，金额 {amount ?? <Qty v={{ kind: 'unknown' }} />}
      </div>

      {res && (
        <div className="mt" data-testid="place-result">
          {res.kind === 'rejected' && (
            <div className="flash flash--bad" role="alert">
              <div>领星拒绝了这张单，仍是草稿；改草稿后再下单</div>
              <div className="gate__code">code {res.code ?? '无'}</div>
              <div>{res.msg ?? '领星没有给出说明'}</div>
            </div>
          )}
          {res.kind === 'not_sent' && (
            <div className="flash flash--bad" role="alert">
              <div>没有发到领星，可以再次下单</div>
              <Fold summary="原因">{res.cause ?? '后端未给原因'}</Fold>
            </div>
          )}
          {res.kind === 'stale' && (
            <div className="flash flash--bad" role="alert">草稿已变，请重新打开下单</div>
          )}
          {res.kind === 'unknown' && (
            <>
              <div className="flash flash--bad" role="alert">
                <div>结果未知：不要再点，正在每 3 秒查一次调用记录</div>
                <div data-testid="place-poll">{res.note}</div>
              </div>
              {res.err && <ErrorDetail err={res.err} />}
            </>
          )}
          {res.kind === 'error' && <ErrorDetail err={res.err} />}
        </div>
      )}

      {baselineErr && (
        <div className="mt">
          <div className="flash flash--bad" role="alert">调用记录取不到，下单后核对不了结果，先不下单</div>
          <ErrorDetail err={baselineErr} />
        </div>
      )}

      <div className="btn-row mt">
        <button type="button" className="btn btn--danger"
          disabled={busy || baseline === null || res?.kind === 'stale' || unknown}
          onClick={() => void confirm()}>确认下单</button>
        {unknown
          ? <button type="button" className="btn" onClick={onSettled}>重新载入</button>
          : <button type="button" className="btn btn--ghost" disabled={busy} onClick={onClose}>取消</button>}
      </div>
    </Modal>
  );
}
