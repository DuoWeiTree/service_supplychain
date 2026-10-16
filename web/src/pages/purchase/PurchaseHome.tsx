import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty, type QtyValue } from '../../components/Qty';
import { api, ApiError } from '../../api';
import type { AttentionKind, AttentionQueue, CandidateLine, CandidateLines, PoList, PurchaseChecks, Seller } from '../../api/types';
import { asApiError } from '../../api/client';
import { KIND_LABEL } from './poStatus';
import { planName, sellerShares } from './poEditorModel';

// PB-D29：不显示「状态不可组单」—— 数的是旧版被替换 / 已完结的历史记录，采购员无事可做，只会越积越多
const DROPPED_LABEL: [keyof CandidateLines['excluded'], string][] = [
  ['fully_confirmed', '已组满'],
];

/** R3：采购从「销售计划已提交、还没组满的记录」开始 —— 勾几条带进编辑器（设计 PendingLines） */
function TodoList({ cands, err, shop, sellersErr }: {
  cands: CandidateLines | null; err: ApiError | null; shop: (sid: string) => string; sellersErr: ApiError | null;
}) {
  const navigate = useNavigate();
  const [picked, setPicked] = useState<number[]>([]);
  const [q, setQ] = useState('');
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const lines = cands?.lines ?? [];
  const needle = q.trim().toLowerCase();
  const shown = needle === '' ? lines : lines.filter((l) => `${l.sku} ${planName(l)}`.toLowerCase().includes(needle));
  const chosen = lines.filter((l) => picked.includes(l.line_id));
  const allOn = shown.length > 0 && shown.every((l) => picked.includes(l.line_id));
  const toggleAll = () => setPicked((p) => (allOn ? p.filter((id) => !shown.some((l) => l.line_id === id))
    : [...new Set([...p, ...shown.map((l) => l.line_id)])]));
  const go = () => {
    // 按清单顺序带过去，不按点选顺序：编辑器左栏的顺序与这里一致
    const ids = chosen.map((l) => l.line_id);
    console.info('[purchase-home] build from todo', JSON.stringify({ lines: ids }));
    navigate(`/purchase/new?lines=${ids.join(',')}`);
  };
  const shopCell = (l: CandidateLine) => {
    const shares = sellerShares(l);
    return (
      <div className="pt-stores">
        {Object.keys(l.sellers).sort().map((sid) => {
          const v = l.sellers[sid]!;
          return (
            <span key={sid} className="pt-store">
              <span className="pt-store__n">{shop(sid)}</span>{' '}
              {v.expected === null ? <span className="pt-unknown">期望未知</span>
                : <span>期望 {v.expected}{shares ? `（占比 ${shares[sid]}%）` : ''}</span>}{' '}
              <span className="pt-used">已组 {v.used}</span>
            </span>
          );
        })}
      </div>
    );
  };
  const dropped = cands ? DROPPED_LABEL.filter(([k]) => (cands.excluded[k] ?? 0) > 0) : [];
  const droppedN = cands ? dropped.reduce((a, [k]) => a + cands.excluded[k], 0) : 0;

  return (
    <section className="pl sec" data-testid="todo">
      <h2>待采购</h2>
      {err ? <ErrorDetail err={err} /> : cands === null ? <div className="empty">正在取待采购记录…</div> : (
        <>
          {sellersErr && (
            <div>
              <span className="muted">店名取不到，按店编号显示</span>
              <Fold summary="原因"><ErrorDetail err={sellersErr} /></Fold>
            </div>
          )}
          {lines.length === 0 ? (
            <div className="empty">没有待采购的记录 <Link to="/">去销售计划</Link></div>
          ) : (
            <>
              <div className="pm-bar">
                <div className="pm-search-wrap">
                  <input className="pm-search" type="search" placeholder="搜索货号、计划" aria-label="搜索待采购"
                    value={q} onChange={(e) => setQ(e.target.value)} />
                </div>
                <span className="pm-bar__sp" />
                <span className="pl-sum" data-testid="todo-sum">
                  {chosen.length > 0
                    ? <>已选 <b>{chosen.length}</b> 条，还可组 <b>{chosen.reduce((a, l) => a + l.remaining, 0)}</b> 件</>
                    : '勾选要组到同一张采购单的记录'}
                </span>
                <button type="button" className="btn btn--primary" disabled={chosen.length === 0} onClick={go}>
                  {chosen.length > 0 ? `用选中的 ${chosen.length} 条建采购单` : '用选中的记录建采购单'}
                </button>
              </div>
              <div className="pt-wrap">
                <table className="pt" data-testid="todo-list">
                  <thead><tr>
                    <th className="pt-chk"><input type="checkbox" aria-label="全选" checked={allOn} onChange={toggleAll} /></th>
                    <th>计划</th><th>货号</th><th>月份</th>
                    <th className="r">总量</th><th className="r">还可组</th><th>各店期望与已组</th>
                  </tr></thead>
                  <tbody>
                    {shown.map((l) => {
                      const on = picked.includes(l.line_id);
                      return (
                        <tr key={l.line_id} data-testid={`todo-${l.line_id}`} className={on ? 'is-on' : undefined}
                          onClick={(e) => { const t = (e.target as HTMLElement).tagName; if (t !== 'A' && t !== 'INPUT') toggle(l.line_id); }}>
                          <td className="pt-chk">
                            <input type="checkbox" aria-label={`选记录 ${l.line_id}`} checked={on} onChange={() => toggle(l.line_id)} />
                          </td>
                          <td><Link to={`/plans/${l.plan_id}`}>{planName(l)}</Link><span className="pt-rev">rev{l.rev}</span></td>
                          <td className="pt-sku">{l.sku}</td>
                          <td>{l.period}</td>
                          <td className="r num">{l.total_units}</td>
                          <td className="r num" data-testid="todo-remaining"><b className={l.remaining === 0 ? 'pt-zero' : undefined}>{l.remaining}</b></td>
                          <td data-testid="todo-shops">{shopCell(l)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {shown.length === 0 && <div className="muted">没有匹配「{q}」的记录</div>}
            </>
          )}
          {/* 已组满的记录不列，给计数（折叠） */}
          {droppedN > 0 && (
            <Fold summary={`未列入 ${droppedN} 条`} testId="todo-dropped">
              {dropped.map(([k, t]) => <div className="dropline" key={k}>{t} {cands.excluded[k]} 条</div>)}
            </Fold>
          )}
        </>
      )}
    </section>
  );
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
  const [cands, setCands] = useState<CandidateLines | null>(null);
  const [candsErr, setCandsErr] = useState<ApiError | null>(null);
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [sellersErr, setSellersErr] = useState<ApiError | null>(null);

  useEffect(() => {
    // 各读各自落地：慢的或超时的那个不许拖住另外两块
    const load = <T,>(name: string, f: () => Promise<T>, ok: (v: T) => void, bad: (e: ApiError) => void) => {
      const t0 = performance.now();
      // api 同步抛（桩缺方法）也要落进各自的错，不能让整页白屏
      Promise.resolve().then(f).then(ok, (e: unknown) => {
        const err = asApiError(e);
        console.warn(`[purchase-home] ${name} failed`, JSON.stringify({
          status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
        }));
        bad(err);
      });
    };
    load('listAttention', () => api.listAttention(), setAttn, setAttnErr);
    load('listPurchaseOrders', () => api.listPurchaseOrders(), setList, setListErr);
    load('getPurchaseChecks', () => api.getPurchaseChecks(), setChecks, setChecksErr);
    load('listCandidateLines', () => api.listCandidateLines({}), setCands, setCandsErr);
    load('listSellers', () => api.listSellers(), setSellers, setSellersErr);
  }, []);
  const sellerName = useMemo(() => new Map((sellers ?? []).map((x) => [x.seller_id, x.name])), [sellers]);
  const shop = (sid: string) => sellerName.get(sid) ?? sid;

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

      <TodoList cands={cands} err={candsErr} shop={shop} sellersErr={sellersErr} />

      {attnErr && <ErrorDetail err={attnErr} />}
      {attn !== null && attn.items.length > 0 && (
        <div className="panel sec" data-testid="attention-list">
          <div className="panel__head">需要人工处理</div>
          <div className="panel__body">
            {/* spec §6.6 ①：按类型分组 */}
            {(Object.keys(KIND_LABEL) as AttentionKind[]).map((k) => {
              const its = attn.items.filter((it) => it.kind === k);
              return its.length === 0 ? null : (
                <div key={k} className="sec" data-testid={`attention-group-${k}`}>
                  <div className="sec__title"><span className="chip chip--bad">{KIND_LABEL[k]}</span> {its.length} 项</div>
                  {its.map((it) => (
                    <div key={`${it.po_id}-${it.kind}`} className="bar">
                      <span className="num">{it.custom_order_sn}</span>
                      <Link to={`/purchase/${it.po_id}`}>打开</Link>
                    </div>
                  ))}
                </div>
              );
            })}
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

      <h2 className="sec">采购单</h2>
      {listErr ? <ErrorDetail err={listErr} /> : list === null ? (
        <div className="empty">正在取采购单…</div>
      ) : rows.length === 0 ? (
        <div className="empty">还没有采购单</div>
      ) : (
        <div className="pt-wrap">
          <table className="pt" data-testid="po-list">
            <thead><tr>
              <th>单号</th><th>供应商</th><th>本地状态</th><th>领星单号</th>
              <th className="r">行</th><th className="r">件数</th><th>待处理</th><th>建单人</th>
            </tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.po_id}>
                  <td><Link to={`/purchase/${p.po_id}`}>{p.custom_order_sn}</Link></td>
                  <td>{p.supplier_name}</td>
                  <td>
                    <span className={`chip${p.state === '已下单' ? ' chip--ok' : ''}`}>{p.state}</span>
                    {p.is_void && <> <span className="chip chip--dim">作废</span></>}
                  </td>
                  <td className="num">{p.order_sn ?? (p.state === '草稿' ? <span className="pt-unknown">未下单</span> : <Qty v={{ kind: 'unknown' }} />)}</td>
                  <td className="r num">{p.lines}</td>
                  <td className="r num">{p.units}</td>
                  {/* 列表接口只给计数，不给类型 */}
                  <td>{p.open_attention > 0 ? <span className="chip chip--bad">待处理 {p.open_attention}</span> : null}</td>
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
