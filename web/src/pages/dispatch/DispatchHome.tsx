import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { DispatchPlan, DispatchPlanSheet, DispatchPlans, DispatchRoute, SheetList } from '../../api/types';
import { ROUTE_SHORT } from './dispatchGridModel';
import { TONE_CLASS, cellTitle, filterPlans, pivotRecords, sheetNextStep, skuHit, type Pivot } from './dispatchHomeModel';
import { fmtAt } from '../purchase/fmt';

const ROUTES = ['fba', 'oversea'] as const;

/** 排货员首页（CA-D11 · DL-D1）：计划抬头为主线，默认收起；展开是货号 × 月小表 */
export function DispatchHome() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState<DispatchPlans | null>(null);
  const [plansErr, setPlansErr] = useState<ApiError | null>(null);
  const [list, setList] = useState<SheetList | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [createErr, setCreateErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [q, setQ] = useState('');
  /** 人点过的展开 / 收起；没点过的跟着搜索走（货号命中自动展开） */
  const [override, setOverride] = useState<Map<number, boolean>>(() => new Map());

  useEffect(() => {
    api.listDispatchPlans().then(setPlans, (e: unknown) => setPlansErr(asApiError(e)));
    api.listSheets().then(setList, (e: unknown) => setErr(asApiError(e)));
  }, []);

  const { shown, autoOpen } = useMemo(() => filterPlans(plans?.plans ?? [], q), [plans, q]);
  const auto = useMemo(() => new Set(autoOpen), [autoOpen]);
  const isOpen = (id: number) => override.get(id) ?? auto.has(id);
  const toggle = (id: number) => setOverride((prev) => new Map(prev).set(id, !isOpen(id)));
  const search = (v: string) => {
    setQ(v);
    // 新命中的计划让搜索说了算，不被之前的手动收起挡住
    const hit = filterPlans(plans?.plans ?? [], v).autoOpen;
    if (hit.length) setOverride((prev) => { const n = new Map(prev); hit.forEach((id) => n.delete(id)); return n; });
  };

  /** ★ CA-D13：按去向开表；这个去向已有草稿就接着排那张 */
  const start = async (p: DispatchPlan, route: DispatchRoute) => {
    const draft = p.sheets.find((s) => s.state === '草稿' && s.route === route);
    if (draft) { navigate(`/dispatch/sheets/${draft.sheet_id}`); return; }
    setBusy(`${p.plan_id}/${route}`);
    try {
      const { sheet_id } = await api.createSheet(p.plan_id, route);
      navigate(`/dispatch/sheets/${sheet_id}`);
    } catch (e) {
      setCreateErr(asApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <AppShell crumb="排货">
      <div className="head">
        <div className="head__main"><h1>排货</h1></div>
        <div className="head__act">
          <span className="pm-search-wrap">
            <input className="pm-search" type="search" aria-label="搜计划名或货号" placeholder="搜 计划 / 货号"
              value={q} onChange={(e) => search(e.target.value)} />
          </span>
        </div>
      </div>
      {createErr && <ErrorDetail err={createErr} onDismiss={() => setCreateErr(null)} />}

      <section className="sec">
        <h2 className="sec__title">未完成的销售计划{plans && plans.plans.length > 0 && <span className="muted">· {plans.plans.length} 个</span>}</h2>
        {plansErr ? <ErrorDetail err={plansErr} /> : plans === null ? <div className="empty">正在取销售计划…</div>
          : plans.plans.length === 0 ? <div className="empty">没有未完成的销售计划</div>
          : shown.length === 0 ? <div className="empty">没有匹配的计划或货号</div> : (
            <div className="dh-plans" data-testid="dispatch-plans">
              {shown.map((p) => (
                <PlanBlock key={p.plan_id} p={p} open={isOpen(p.plan_id)} q={q} busy={busy}
                  onToggle={() => toggle(p.plan_id)} onStart={(route) => void start(p, route)} />
              ))}
            </div>
          )}
      </section>

      <AllSheets list={list} err={err} />
    </AppShell>
  );
}

function PlanBlock({ p, open, q, busy, onToggle, onStart }: {
  p: DispatchPlan; open: boolean; q: string; busy: string | null; onToggle: () => void; onStart: (r: DispatchRoute) => void;
}) {
  const pivot = useMemo(() => pivotRecords(p.records), [p.records]);
  const bodyId = `plan-body-${p.plan_id}`;
  const onHead = (e: MouseEvent<HTMLDivElement>) => {
    // 抬头里的链接 / 按钮各做各的事；标题按钮自己会切换
    if ((e.target as HTMLElement).closest('a, button, input, select')) return;
    onToggle();
  };
  return (
    <article className="dh-plan" data-testid={`plan-${p.plan_id}`}>
      <div className="dh-plan__head" onClick={onHead}>
        <div className="dh-plan__id">
          <button type="button" className="dh-plan__title" aria-expanded={open} aria-controls={open ? bodyId : undefined}
            onClick={onToggle}>
            {p.title} <span className="pt-rev">rev {p.rev}</span>
          </button>
          <span className="dh-plan__count">{pivot.rows.length} 个货号 · {p.lines} 条记录</span>
        </div>
        <div className="dh-plan__kpis">
          {([['需求', p.total], ['已组单', p.confirmed], ['已下单', p.ordered], ['已排未发', p.staged], ['已投送', p.dispatched]] as const).map(([l, v]) => (
            <div key={l} className="pm-kpi"><span className="pm-kpi__l">{l}</span><span className="pm-kpi__v">{v}</span></div>
          ))}
          <div className="pm-kpi ds-kpi--left"><span className="pm-kpi__l">还差</span>
            <span className={`pm-kpi__v${p.pending === 0 ? ' ds-done' : ''}`} data-testid={`plan-left-${p.plan_id}`}>
              {p.pending === 0 ? '已满足' : p.pending}
            </span>
          </div>
        </div>
        <div className="dh-plan__row2">
          {p.sheets.map((sh) => <SheetChip key={sh.sheet_id} sh={sh} />)}
          {p.pending > 0 && (
            <div className="dh-plan__acts">
              {ROUTES.map((route) => {
                const draft = p.sheets.find((s) => s.state === '草稿' && s.route === route);
                return (
                  <button key={route} type="button" className={`btn btn--sm${draft ? ' btn--primary' : ''}`}
                    disabled={busy === `${p.plan_id}/${route}`} onClick={() => onStart(route)}>
                    {draft ? `接着${ROUTE_SHORT[route]} #${draft.sheet_id}` : route === 'fba' ? '平台仓备货 · FBA' : '海外仓备货'}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
      {open && <div className="dh-plan__body" id={bodyId}><PivotTable planId={p.plan_id} pivot={pivot} q={q} /></div>}
    </article>
  );
}

function SheetChip({ sh }: { sh: DispatchPlanSheet }) {
  const step = sheetNextStep(sh);
  useEffect(() => {
    if (step.unmatched) console.warn('[dispatch] next step unmatched', JSON.stringify(sh));
  }, [step.unmatched, sh]);
  return (
    <Link to={`/dispatch/sheets/${sh.sheet_id}`} className={TONE_CLASS[step.tone]} data-testid={`sheet-chip-${sh.sheet_id}`}>
      #{sh.sheet_id}{sh.route ? ` ${ROUTE_SHORT[sh.route]}` : ''} {step.text}
      {step.more > 0 && <span className="dh-more">+{step.more}</span>}
    </Link>
  );
}

function PivotTable({ planId, pivot, q }: { planId: number; pivot: Pivot; q: string }) {
  return (
    <div className="pt-wrap">
      <table className="pt" data-testid={`pivot-${planId}`}>
        <thead>
          <tr><th>货号</th><th>品名</th>{pivot.months.map((m) => <th key={m} className="r">{m}</th>)}<th className="r">还差</th></tr>
        </thead>
        <tbody>
          {pivot.rows.map((r) => (
            <tr key={r.sku} className={skuHit(r.sku, q) ? 'is-on' : undefined} data-testid={`pivot-row-${planId}-${r.sku}`}>
              <td className="pt-sku">{r.sku}</td>
              <td className="dh-name" title={r.sku_name ?? undefined}>{r.sku_name ?? ''}</td>
              {pivot.months.map((m) => {
                const c = r.cells.get(m);
                return (
                  <td key={m} className="r dh-cell" data-testid={`cell-${planId}-${r.sku}-${m}`}
                    title={c ? cellTitle(r.sku, m, c) : undefined}>
                    {!c ? <span className="pt-zero">—</span> : (
                      <>
                        <span aria-hidden="true">
                          {c.pending === 0 ? <span className="ds-done">已满足</span> : <><b>{c.pending}</b><span className="pt-rev">/{c.total}</span></>}
                        </span>
                        <span className="dh-sr">{cellTitle(r.sku, m, c)}</span>
                      </>
                    )}
                  </td>
                );
              })}
              <td className="r dh-cell"><b className={r.pending_sum === 0 ? 'ds-done' : 'ds-pending'}>{r.pending_sum === 0 ? '已满足' : r.pending_sum}</b></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AllSheets({ list, err }: { list: SheetList | null; err: ApiError | null }) {
  if (err) return <section className="sec"><h2 className="sec__title">全部排货表</h2><ErrorDetail err={err} /></section>;
  const n = list?.sheets.length ?? 0;
  const cancelled = list?.sheets.filter((s) => s.state === '已取消').length ?? 0;
  return (
    <section className="sec">
      <Fold summary={list === null ? '全部排货表（正在取…）' : `全部排货表（${n} 张，含已取消 ${cancelled}）`} testId="all-sheets">
        {list === null ? <div className="empty">正在取排货表…</div> : n === 0 ? <div className="empty">还没有排货表</div> : (
          <div className="table-scroll">
            <table className="table table--dense" data-testid="sheet-list">
              <thead><tr><th>排货表</th><th>销售计划</th><th>去向</th><th>状态</th><th className="r">排货单</th><th className="r">件数</th><th>建表人</th><th>建于</th></tr></thead>
              <tbody>
                {list.sheets.map((s) => (
                  <tr key={s.sheet_id}>
                    <td><Link to={`/dispatch/sheets/${s.sheet_id}`}>#{s.sheet_id}</Link></td>
                    <td>{s.plan_title ?? <span className="muted">未关联计划</span>}</td>
                    <td>{s.route ? ROUTE_SHORT[s.route] : <span className="muted">旧版混合</span>}</td>
                    <td><span className={`chip ${s.state === '草稿' ? 'chip--ours' : s.state === '已确认' ? 'chip--ok' : 'chip--dim'}`}>{s.state}</span></td>
                    <td className="r num">{s.consignments}</td>
                    <td className="r num">{s.units}</td>
                    <td>{s.created_by}</td>
                    <td>{fmtAt(s.created_at)}</td>
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
