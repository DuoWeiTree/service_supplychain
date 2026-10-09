import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty, type QtyValue } from '../../components/Qty';
import { api, ApiError } from '../../api';
import type { AttentionKind, AttentionQueue, PoId, PoList, PurchaseChecks } from '../../api/types';

export const KIND_LABEL: Record<AttentionKind, string> = {
  needs_verify: '下单结果待核实',
  landed_mismatch: '领星明细不一致',
  stuck_call: '下单请求卡住',
};

/** 列表接口不回 custom_order_sn；它由 po_id 定死（spec §10.2 V7，设计取值） */
const orderSn = (poId: PoId) => `SCM-PO-${poId}`;

// 非 ApiError（建连失败等）也要落成可显示明细：cause.code 是分辨「超时」与「连不上」的唯一线索
function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const err = e as { name?: string; message?: string; cause?: { code?: string } };
  return new ApiError(0, err.name ?? 'Error', err.message ?? String(e),
    err.cause?.code ? { cause_code: err.cause.code } : {});
}

export function PurchaseHome() {
  const navigate = useNavigate();
  const [attn, setAttn] = useState<AttentionQueue | null>(null);
  const [list, setList] = useState<PoList | null>(null);
  const [checks, setChecks] = useState<PurchaseChecks | null>(null);
  // 三个读各记各的错：一个挂了另外两个照样出数
  const [attnErr, setAttnErr] = useState<ApiError | null>(null);
  const [listErr, setListErr] = useState<ApiError | null>(null);
  const [checksErr, setChecksErr] = useState<ApiError | null>(null);

  useEffect(() => {
    const t0 = performance.now();
    const settle = <T,>(name: string, r: PromiseSettledResult<T>, ok: (v: T) => void, bad: (e: ApiError) => void) => {
      if (r.status === 'fulfilled') { ok(r.value); return; }
      const err = asApiError(r.reason);
      console.warn(`[purchase-home] ${name} failed`, JSON.stringify({
        status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
      }));
      bad(err);
    };
    // api 同步抛（桩缺方法）也要落进各自的错，不能让整页白屏
    const call = <T,>(f: () => Promise<T>) => Promise.resolve().then(f);
    void Promise.allSettled([
      call(() => api.listAttention()), call(() => api.listPurchaseOrders()), call(() => api.getPurchaseChecks()),
    ]).then(([a, l, c]) => {
      settle('listAttention', a, setAttn, setAttnErr);
      settle('listPurchaseOrders', l, setList, setListErr);
      settle('getPurchaseChecks', c, setChecks, setChecksErr);
    });
  }, []);

  const rows = list?.purchase_orders ?? [];
  const countOf = (state: '草稿' | '已下单'): QtyValue =>
    list === null ? { kind: 'unknown' } : { kind: 'num', value: rows.filter((p) => p.state === state).length };
  const draftN = useMemo(() => countOf('草稿'), [list]);        // eslint-disable-line react-hooks/exhaustive-deps
  const placedN = useMemo(() => countOf('已下单'), [list]);     // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AppShell crumb="采购">
      <div className="head">
        <div className="head__main"><h1>采购</h1></div>
        <div className="head__act">
          <button type="button" className="btn btn--primary" onClick={() => navigate('/purchase/new')}>新建采购单</button>
        </div>
      </div>

      {attnErr && <ErrorDetail err={attnErr} />}
      {attn !== null && attn.items.length > 0 && (
        <div className="panel sec" data-testid="attention-list">
          <div className="panel__head">需要人工处理</div>
          <div className="panel__body">
            {attn.items.map((it) => (
              <div key={`${it.po_id}-${it.kind}`} className="bar">
                <span className="chip chip--bad">{KIND_LABEL[it.kind]}</span>
                <span className="num">{it.custom_order_sn}</span>
                <Link to={`/purchase/${it.po_id}`}>打开</Link>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="sec bar" data-testid="counts">
        <div className="kpi"><span className="kpi__n"><Qty v={draftN} /></span><span className="kpi__d">草稿</span></div>
        <div className="kpi"><span className="kpi__n"><Qty v={placedN} /></span><span className="kpi__d">已下单</span></div>
      </div>

      {checksErr && <ErrorDetail err={checksErr} />}
      {checks !== null && (checks.ok
        ? <div className="muted">核对全部通过</div>
        : (
          <>
            <div><span className="chip chip--bad">核对有 {checks.violations.length} 项不一致</span></div>
            <Fold summary="不一致明细">
              <ul>
                {checks.violations.map((v, i) => <li key={i}>{v.check_name} {v.ref}</li>)}
              </ul>
            </Fold>
          </>
        ))}

      {listErr ? <ErrorDetail err={listErr} /> : list === null ? (
        <div className="empty">正在取采购单…</div>
      ) : rows.length === 0 ? (
        <div className="empty">还没有采购单</div>
      ) : (
        <div className="table-scroll">
          <table className="table table--dense" data-testid="po-list">
            <thead><tr>
              <th>单号</th><th>供应商</th><th>状态</th><th>领星单号</th>
              <th className="r">行</th><th className="r">件数</th><th className="r">待处理</th><th>建单人</th>
            </tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.po_id}>
                  <td><Link to={`/purchase/${p.po_id}`}>{orderSn(p.po_id)}</Link></td>
                  <td>{p.supplier_name}</td>
                  <td>
                    {p.state}{p.is_void && <> <span className="chip chip--dim">作废</span></>}
                  </td>
                  <td>{p.order_sn ?? <Qty v={{ kind: 'unknown' }} />}</td>
                  <td className="r">{p.lines}</td>
                  <td className="r">{p.units}</td>
                  <td className="r">{p.open_attention > 0
                    ? <span className="chip chip--bad">{p.open_attention}</span> : p.open_attention}</td>
                  <td>{p.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
