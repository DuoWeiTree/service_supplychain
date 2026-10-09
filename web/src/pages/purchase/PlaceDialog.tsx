import { useState } from 'react';
import { Modal } from '../../components/Modal';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty } from '../../components/Qty';
import { useInFlight } from '../../shell/useInFlight';
import { pushToast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { PlaceResult, PurchaseOptions, PurchaseOrder } from '../../api/types';
import { QC_LABEL } from './poEditorModel';
import { exactTotal, logWrite, logWriteFail } from './fmt';

type Res =
  | { kind: 'rejected'; code: string | number | null; msg: string | null }
  | { kind: 'not_sent'; cause: string | null }
  | { kind: 'stale' }
  | { kind: 'uncertain'; err: ApiError }
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
  const busy = pending.has('place');

  const supplier = options?.suppliers.find((s) => s.supplier_id === snap.supplier_id);
  const optUid = options?.opt_uids.find((o) => o.opt_uid === snap.opt_uid);
  const wh = (wid: string) => {
    const w = options?.warehouses.find((x) => x.wid === wid);
    return w ? `${w.name}（${wid}）` : wid;
  };
  const units = snap.lines.reduce((a, l) => a + l.units, 0);
  const amount = exactTotal(snap.lines);

  const confirm = () => run('place', async () => {
    const call = `POST /purchase-orders/${snap.po_id}/place`;
    const t0 = performance.now();
    let r: PlaceResult;
    try {
      r = await api.placePurchaseOrder(snap.po_id, snap.confirm_token);
    } catch (e) {
      const err = asApiError(e);
      logWriteFail('po-place', call, t0, err);
      refreshAttention();
      if (err.error === 'stale_confirm_token') { setRes({ kind: 'stale' }); onStale(); return; }
      // 已有待核实 / 已被下单：状态在别处变了，回到按状态分发
      if (err.error === 'needs_verify' || err.error === 'po_frozen') {
        pushToast({ kind: 'warn', text: err.hint });
        onSettled();
        return;
      }
      // 没收到回音：请求可能已到后端并在下单 —— 只能去看结果，不能当成失败再点
      if (err.status === 0) { setRes({ kind: 'uncertain', err }); return; }
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
    } else {
      setRes({ kind: 'not_sent', cause: r.cause });
    }
  });

  return (
    <Modal danger warn="这一步会在领星建出真实采购单，不可撤销" title="⛔ 下单到领星"
      onClose={busy ? () => {} : onClose}>
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
              <div>领星拒绝了这张单，仍是草稿</div>
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
          {res.kind === 'uncertain' && (
            <>
              <div className="flash flash--bad" role="alert">没等到结果，下单可能仍在进行；不要再点，重新载入看结果</div>
              <ErrorDetail err={res.err} />
            </>
          )}
          {res.kind === 'error' && <ErrorDetail err={res.err} />}
        </div>
      )}

      <div className="btn-row mt">
        <button type="button" className="btn btn--danger" disabled={busy || res?.kind === 'stale' || res?.kind === 'uncertain'}
          onClick={() => void confirm()}>确认下单</button>
        {res?.kind === 'uncertain'
          ? <button type="button" className="btn" onClick={onSettled}>重新载入</button>
          : <button type="button" className="btn btn--ghost" disabled={busy} onClick={onClose}>取消</button>}
      </div>
    </Modal>
  );
}
