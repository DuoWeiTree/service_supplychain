import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Qty } from '../../components/Qty';
import { api, ApiError } from '../../api';
import type { AttentionKind, AttentionQueue, PoList, PurchaseChecks, PurchasePlan, PurchasePlanDraft, PurchasePlans } from '../../api/types';
import { asApiError } from '../../api/client';
import { KIND_LABEL } from './poStatus';
import {
  cellKind, cellTitle, filterPlans, pickState, pivotPlan, planPick, skuHit, toggleIds, togglePlan, type Pick, type PickPivot,
} from './purchaseHomeModel';

/** indeterminate 只能经 DOM 设 */
function TriCheck({ state, label, disabled, onChange }: { state: Pick; label: string; disabled?: boolean; onChange: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = state === 'some'; }, [state]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={state === 'all'} disabled={disabled} onChange={onChange} />;
}

function DraftChip({ d }: { d: PurchasePlanDraft }) {
  return (
    <Link to={`/purchase/${d.po_id}`} className={d.open_attention > 0 ? 'chip chip--bad' : 'chip chip--ours'}
      data-testid={`draft-chip-${d.po_id}`}>
      {d.custom_order_sn} {d.open_attention > 0 ? `待处理 ${d.open_attention} 项` : `草稿 · ${d.units} 件 · 接着编辑`}
    </Link>
  );
}

/** R3 / UX-D1：采购从计划开始 —— 计划抬头为主线、默认收起，展开是货号 × 月小表，格子可勾 */
export function PurchaseHome() {
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const focus = Number(search.get('plan')) || null;
  const [attn, setAttn] = useState<AttentionQueue | null>(null);
  const [list, setList] = useState<PoList | null>(null);
  const [checks, setChecks] = useState<PurchaseChecks | null>(null);
  const [plans, setPlans] = useState<PurchasePlans | null>(null);
  // 各读各记各的错：一个挂了另外几个照样出数
  const [attnErr, setAttnErr] = useState<ApiError | null>(null);
  const [listErr, setListErr] = useState<ApiError | null>(null);
  const [checksErr, setChecksErr] = useState<ApiError | null>(null);
  const [plansErr, setPlansErr] = useState<ApiError | null>(null);
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<number[]>([]);
  /** 人点过的展开 / 收起；没点过的跟着搜索与 ?plan= 走 */
  const [override, setOverride] = useState<Map<number, boolean>>(() => new Map());

  useEffect(() => {
    // 慢的或超时的那个不许拖住另外几块
    const load = <T,>(name: string, f: () => Promise<T>, ok: (v: T) => void, bad: (e: ApiError) => void) => {
      const t0 = performance.now();
      Promise.resolve().then(f).then((v) => {
        console.info(`[purchase-home] ${name} ok`, JSON.stringify({ ms: Math.round(performance.now() - t0) }));
        ok(v);
      }, (e: unknown) => {
        const err = asApiError(e);
        console.warn(`[purchase-home] ${name} failed`, JSON.stringify({
          status: err.status, error: err.error, fields: err.fields, ms: Math.round(performance.now() - t0),
        }));
        bad(err);
      });
    };
    load('listPurchasePlans', () => api.listPurchasePlans(), setPlans, setPlansErr);
    load('listAttention', () => api.listAttention(), setAttn, setAttnErr);
    load('listPurchaseOrders', () => api.listPurchaseOrders(), setList, setListErr);
    load('getPurchaseChecks', () => api.getPurchaseChecks(), setChecks, setChecksErr);
  }, []);

  const all = plans?.plans ?? [];
  const { shown, autoOpen } = useMemo(() => filterPlans(all, q), [all, q]);   // eslint-disable-line react-hooks/exhaustive-deps
  const auto = useMemo(() => new Set(focus === null ? autoOpen : [...autoOpen, focus]), [autoOpen, focus]);
  const isOpen = (id: number) => override.get(id) ?? auto.has(id);
  const toggleOpen = (id: number) => setOverride((m) => new Map(m).set(id, !(m.get(id) ?? auto.has(id))));
  const onSearch = (v: string) => {
    setQ(v);
    // 新命中的计划让搜索说了算，不被之前的手动收起挡住
    const hit = filterPlans(all, v).autoOpen;
    if (hit.length) setOverride((m) => { const n = new Map(m); hit.forEach((id) => n.delete(id)); return n; });
  };

  // UX-D6：从采购单回来，滚到那一组
  const focusMissing = plans !== null && focus !== null && !all.some((p) => p.plan_id === focus);
  useEffect(() => {
    if (plans === null || focus === null) return;
    const el = document.getElementById(`plan-${focus}`);
    if (el) el.scrollIntoView?.({ block: 'start' });
    else console.info('[purchase-home] focus plan not listed', JSON.stringify({ plan_id: focus }));
  }, [plans, focus]);

  // 按清单顺序带过去，不按点选顺序：编辑器左栏的顺序与这里一致
  const order = all.flatMap((p) => p.records.filter((r) => r.orderable));
  const chosen = order.filter((r) => picked.includes(r.line_id));
  const go = () => {
    const ids = chosen.map((r) => r.line_id);
    console.info('[purchase-home] build from todo', JSON.stringify({ lines: ids }));
    navigate(`/purchase/new?lines=${ids.join(',')}`);
  };
  const quiet = plans?.excluded.nothing_to_order ?? [];

  return (
    <AppShell crumb="采购">
      <div className="head">
        <div className="head__main"><h1>采购</h1></div>
        <div className="head__act">
          <span className="pm-search-wrap">
            <input className="pm-search" type="search" aria-label="搜计划名或货号" placeholder="搜 计划 / 货号"
              value={q} onChange={(e) => onSearch(e.target.value)} />
          </span>
          <button type="button" className="btn" onClick={() => navigate('/purchase/new')}>新建空白采购单</button>
        </div>
      </div>

      <section className="sec" data-testid="todo">
        <div className="pm-bar">
          <h2 className="sec__title">待采购{plans && all.length > 0 && <span className="muted"> · {all.length} 个计划</span>}</h2>
          <span className="pm-bar__sp" />
          {all.length > 0 && (
            <>
              <span className="pl-sum" data-testid="todo-sum">
                {chosen.length > 0
                  ? <>已选 <b>{chosen.length}</b> 条，还可组 <b>{chosen.reduce((a, r) => a + r.remaining, 0)}</b> 件</>
                  : '勾选要组到同一张采购单的记录'}
              </span>
              <button type="button" className="btn btn--primary" disabled={chosen.length === 0} onClick={go}>
                {chosen.length > 0 ? `用选中的 ${chosen.length} 条建采购单` : '用选中的记录建采购单'}
              </button>
            </>
          )}
        </div>
        {plansErr ? <ErrorDetail err={plansErr} /> : plans === null ? <div className="empty">正在取待采购记录…</div>
          : all.length === 0 ? <div className="empty">没有待采购的记录 <Link to="/">去销售计划</Link></div>
          : shown.length === 0 ? <div className="empty">没有匹配「{q}」的计划或货号</div> : (
            <div className="dh-plans" data-testid="purchase-plans">
              {shown.map((p) => (
                <PlanBlock key={p.plan_id} p={p} open={isOpen(p.plan_id)} q={q} picked={picked} focused={p.plan_id === focus}
                  onToggle={() => toggleOpen(p.plan_id)} onPick={setPicked} />
              ))}
            </div>
          )}
        {focusMissing && (
          <div className="muted" data-testid="focus-missing">
            {(() => {
              const hit = quiet.find((x) => x.plan_id === focus);
              return hit ? `「${hit.title}」没有可组的记录、也没有草稿，不在列表里` : `计划 #${focus} 不在待采购里`;
            })()}
          </div>
        )}
        {plans && plans.unplanned_drafts.length > 0 && (
          <div className="dh-plan__row2 sec" data-testid="unplanned-drafts">
            <span className="muted">不来自计划的草稿</span>
            {plans.unplanned_drafts.map((d) => <DraftChip key={d.po_id} d={d} />)}
          </div>
        )}
        {quiet.length > 0 && (
          <Fold summary={`另有 ${quiet.length} 个计划没有可组的记录、也没有草稿`} testId="todo-dropped">
            {quiet.map((x) => (
              <div className="dropline" key={x.plan_id}>
                <Link to={`/plans/${x.plan_id}`}>{x.title}</Link>{x.rev !== null && <span className="pt-rev">rev {x.rev}</span>}
              </div>
            ))}
          </Fold>
        )}
      </section>

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

      {checksErr && <ErrorDetail err={checksErr} />}
      {checks !== null && !checks.ok && (
        <div className="sec">
          <span className="chip chip--bad">核对有 {checks.violations.length} 项不一致</span>
          <Fold summary="不一致明细">
            <ul>{checks.violations.map((v, i) => <li key={i}>{v.check_name} {v.ref}</li>)}</ul>
          </Fold>
        </div>
      )}

      <AllPos list={list} err={listErr} checksOk={checks?.ok === true} />
    </AppShell>
  );
}

function PlanBlock({ p, open, q, picked, focused, onToggle, onPick }: {
  p: PurchasePlan; open: boolean; q: string; picked: number[]; focused: boolean;
  onToggle: () => void; onPick: (f: (prev: number[]) => number[]) => void;
}) {
  const pivot = useMemo(() => pivotPlan(p.records), [p.records]);
  const bodyId = `plan-body-${p.plan_id}`;
  const onHead = (e: MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('a, button, input, select, label')) return;
    onToggle();
  };
  return (
    <article className={`dh-plan${focused ? ' is-focus' : ''}`} id={`plan-${p.plan_id}`} data-testid={`plan-${p.plan_id}`}>
      <div className="dh-plan__head" onClick={onHead}>
        <div className="dh-plan__id">
          <TriCheck state={planPick(p, picked)} label={`选「${p.title}」全部可组记录`} disabled={p.orderable_lines === 0}
            onChange={() => onPick((prev) => togglePlan(prev, p))} />
          <button type="button" className="dh-plan__title" aria-expanded={open} aria-controls={open ? bodyId : undefined}
            onClick={onToggle}>
            {p.title}{p.rev !== null && <span className="pt-rev">rev {p.rev}</span>}
          </button>
          <span className="dh-plan__count">{pivot.rows.length} 个货号 · {p.lines} 条记录</span>
        </div>
        <div className="dh-plan__kpis">
          {([['需求', p.total], ['已组单（含草稿）', p.confirmed], ['已下单', p.ordered]] as const).map(([l, v]) => (
            <div key={l} className="pm-kpi"><span className="pm-kpi__l">{l}</span><span className="pm-kpi__v">{v}</span></div>
          ))}
          <div className="pm-kpi ds-kpi--left"><span className="pm-kpi__l">还可组</span>
            <span className={`pm-kpi__v${p.orderable_units === 0 ? ' pt-zero' : ''}`} data-testid={`plan-left-${p.plan_id}`}>
              {p.orderable_units}
            </span>
          </div>
        </div>
        {p.drafts.length > 0 && (
          <div className="dh-plan__row2">{p.drafts.map((d) => <DraftChip key={d.po_id} d={d} />)}</div>
        )}
      </div>
      {open && <div className="dh-plan__body" id={bodyId}><PickTable planId={p.plan_id} pivot={pivot} q={q} picked={picked} onPick={onPick} /></div>}
    </article>
  );
}

function PickTable({ planId, pivot, q, picked, onPick }: {
  planId: number; pivot: PickPivot; q: string; picked: number[]; onPick: (f: (prev: number[]) => number[]) => void;
}) {
  return (
    <div className="pt-wrap">
      <table className="pt" data-testid={`pivot-${planId}`}>
        <thead>
          <tr><th>货号</th><th>品名</th>{pivot.months.map((m) => <th key={m} className="r">{m}</th>)}<th className="r">还可组</th></tr>
        </thead>
        <tbody>
          {pivot.rows.map((r) => (
            <tr key={r.sku} className={skuHit(r.sku, q) ? 'is-on' : undefined} data-testid={`pivot-row-${planId}-${r.sku}`}>
              <td className="pt-sku">{r.sku}</td>
              <td className="dh-name" title={r.sku_name ?? undefined}>{r.sku_name ?? ''}</td>
              {pivot.months.map((m) => {
                const c = r.cells.get(m);
                const testId = `cell-${planId}-${r.sku}-${m}`;
                if (!c) return <td key={m} className="r dh-cell" data-testid={testId}><span className="pt-zero">—</span></td>;
                const k = cellKind(c);
                return (
                  <td key={m} className="r dh-cell" data-testid={testId} title={cellTitle(r.sku, m, c)}>
                    {k.kind === 'full' ? <span className="ds-done">已组满</span>
                      : k.kind === 'state' ? <span className="muted">{k.state}</span> : (
                        <label className="ph-pick">
                          <TriCheck state={pickState(c.ids, picked)} label={`选 ${r.sku} ${m}`}
                            onChange={() => onPick((prev) => toggleIds(prev, c.ids))} />
                          <b>{c.remaining}</b><span className="pt-rev">/{c.total}</span>
                        </label>
                      )}
                  </td>
                );
              })}
              <td className="r dh-cell"><b className={r.orderable_sum === 0 ? 'pt-zero' : undefined}>{r.orderable_sum}</b></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AllPos({ list, err, checksOk }: { list: PoList | null; err: ApiError | null; checksOk: boolean }) {
  if (err) return <section className="sec"><h2 className="sec__title">全部采购单</h2><ErrorDetail err={err} /></section>;
  const rows = list?.purchase_orders ?? [];
  const voided = rows.filter((p) => p.is_void).length;
  return (
    <section className="sec">
      <Fold summary={list === null ? '全部采购单（正在取…）' : `全部采购单（${rows.length} 张，含作废 ${voided}）`} testId="all-pos">
        {checksOk && <div className="muted">核对全部通过</div>}
        {list === null ? <div className="empty">正在取采购单…</div> : rows.length === 0 ? <div className="empty">还没有采购单</div> : (
          <div className="pt-wrap">
            <table className="pt" data-testid="po-list">
              <thead><tr>
                <th>单号</th><th>供应商</th><th>本地状态</th><th>领星单号</th>
                <th className="r">行</th><th className="r">件数</th><th>建单人</th>
              </tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.po_id}>
                    <td><Link to={`/purchase/${p.po_id}`}>{p.custom_order_sn}</Link></td>
                    <td>{p.supplier_name}</td>
                    <td>
                      <span className={`chip${p.state === '已下单' ? ' chip--ok' : ''}`}>{p.state}</span>
                      {p.is_void && <> <span className="chip chip--dim">作废</span></>}
                      {/* 列表接口只给计数，不给类型 */}
                      {p.open_attention > 0 && <> <span className="chip chip--bad">待处理 {p.open_attention}</span></>}
                    </td>
                    <td className="num">{p.order_sn ?? (p.state === '草稿' ? <span className="pt-unknown">未下单</span> : <Qty v={{ kind: 'unknown' }} />)}</td>
                    <td className="r num">{p.lines}</td>
                    <td className="r num">{p.units}</td>
                    <td>{p.created_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Fold>
    </section>
  );
}
