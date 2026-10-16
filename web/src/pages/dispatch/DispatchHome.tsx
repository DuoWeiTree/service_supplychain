import { useEffect, useState, Fragment } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import type { DispatchPlan, DispatchPlans, DispatchRoute, SheetList } from '../../api/types';
import { ROUTE_SHORT } from './dispatchGridModel';
import { fmtAt } from '../purchase/fmt';

/** 排货员首页（CA-D11）：先选未完成的销售计划，再按它排；下面是全部排货表 */
export function DispatchHome() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState<DispatchPlans | null>(null);
  const [plansErr, setPlansErr] = useState<ApiError | null>(null);
  const [list, setList] = useState<SheetList | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [createErr, setCreateErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.listDispatchPlans().then(setPlans, (e: unknown) => setPlansErr(asApiError(e)));
    api.listSheets().then(setList, (e: unknown) => setErr(asApiError(e)));
  }, []);

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
      <div className="head"><div className="head__main"><h1>排货</h1></div></div>
      {createErr && <ErrorDetail err={createErr} onDismiss={() => setCreateErr(null)} />}

      <section className="sec">
        <h2 className="sec__title">未完成的销售计划</h2>
        {plansErr ? <ErrorDetail err={plansErr} /> : plans === null ? <div className="empty">正在取销售计划…</div>
          : plans.plans.length === 0 ? <div className="empty">没有未完成的销售计划</div> : (
            <div className="table-scroll">
              <table className="table--dense" data-testid="dispatch-plans">
                <thead>
                  <tr>
                    <th>销售计划</th><th className="num">记录</th><th className="num">需求</th>
                    <th className="num">已组单</th><th className="num">已下单</th>
                    <th className="num">已排未发</th><th className="num">已投送</th><th className="num">还差</th><th>排货表</th><th />
                  </tr>
                </thead>
                <tbody>
                  {plans.plans.map((p) => {
                    return (
                      <Fragment key={p.plan_id}>
                      <tr data-testid={`plan-${p.plan_id}`}>
                        <td><b>{p.title}</b> <span className="pt-rev">rev {p.rev}</span></td>
                        <td className="num">{p.lines}</td>
                        <td className="num">{p.total}</td>
                        <td className="num">{p.confirmed}</td>
                        <td className="num">{p.ordered}</td>
                        <td className="num">{p.staged}</td>
                        <td className="num">{p.dispatched}</td>
                        <td className="num"><b className={p.pending === 0 ? 'ds-done' : 'ds-pending'}>{p.pending === 0 ? '已满足' : p.pending}</b></td>
                        <td>
                          {p.sheets.length === 0 ? <span className="pt-unknown">无</span> : p.sheets.map((sh) => (
                            <Link key={sh.sheet_id} to={`/dispatch/sheets/${sh.sheet_id}`} style={{ marginRight: 4 }}
                              className={`chip ${sh.state === '草稿' ? 'chip--ours' : sh.state === '已确认' ? 'chip--ok' : 'chip--dim'}`}>
                              #{sh.sheet_id} {sh.route ? ROUTE_SHORT[sh.route] : '旧版混合'} {sh.state}
                            </Link>
                          ))}
                        </td>
                        <td>
                          {p.pending > 0 && (['fba', 'oversea'] as const).map((route) => {
                            const draft = p.sheets.find((s) => s.state === '草稿' && s.route === route);
                            return (
                              <button key={route} type="button" className={`btn btn--sm${draft ? '' : ' btn--primary'}`}
                                style={{ marginRight: 4 }} disabled={busy === `${p.plan_id}/${route}`} onClick={() => void start(p, route)}>
                                {draft ? `接着${ROUTE_SHORT[route]} #${draft.sheet_id}` : route === 'fba' ? '平台仓备货 · FBA' : '海外仓备货'}
                              </button>
                            );
                          })}
                        </td>
                      </tr>
                      {p.records.map((r) => (
                        <tr key={r.line_id} className="ds-plan-rec" data-testid={`plan-rec-${r.line_id}`}>
                          <td className="ds-plan-rec__sku"><code>{r.sku}</code> {r.sku_name ?? ''} <span className="pt-rev">{r.period.slice(0, 7)}</span></td>
                          <td className="num" />
                          <td className="num">{r.total}</td>
                          <td className="num" />
                          <td className="num" />
                          <td className="num">{r.staged}</td>
                          <td className="num">{r.dispatched}</td>
                          <td className="num">
                            <span className={r.pending === 0 ? 'ds-done' : 'ds-pending'}>{r.pending === 0 ? '已满足' : `还差 ${r.pending}`}</span>
                          </td>
                          <td colSpan={2} />
                        </tr>
                      ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
      </section>

      <section className="sec">
        <h2 className="sec__title">全部排货表</h2>
        {err ? <ErrorDetail err={err} /> : list === null ? <div className="empty">正在取排货表…</div>
          : list.sheets.length === 0 ? <div className="empty">还没有排货表</div> : (
            <div className="table-scroll">
              <table className="table--dense" data-testid="sheet-list">
                <thead><tr><th>排货表</th><th>销售计划</th><th>去向</th><th>状态</th><th className="num">排货单</th><th className="num">件数</th><th>建表人</th><th>建于</th></tr></thead>
                <tbody>
                  {list.sheets.map((s) => (
                    <tr key={s.sheet_id}>
                      <td><Link to={`/dispatch/sheets/${s.sheet_id}`}>#{s.sheet_id}</Link></td>
                      <td>{s.plan_title ?? <span className="muted">未关联计划</span>}</td>
                      <td>{s.route ? ROUTE_SHORT[s.route] : <span className="muted">旧版混合</span>}</td>
                      <td><span className={`chip ${s.state === '草稿' ? 'chip--ours' : s.state === '已确认' ? 'chip--ok' : 'chip--dim'}`}>{s.state}</span></td>
                      <td className="num">{s.consignments}</td>
                      <td className="num">{s.units}</td>
                      <td>{s.created_by}</td>
                      <td>{fmtAt(s.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>
    </AppShell>
  );
}
