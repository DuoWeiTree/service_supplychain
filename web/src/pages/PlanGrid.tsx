import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty, type QtyValue } from '../components/Qty';
import { api, ApiError } from '../api';
import type { GridResponse, PlanSummary, Seller, SubmitResult } from '../api/types';
import { buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, sumUnits } from './planGridModel';
import { usePlanGridSaves } from './usePlanGridSaves';
import { SubmitPanel, type InFlightBlock } from './SubmitPanel';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<ApiError | null>(null);
  const [report, setReport] = useState<SubmitResult | null>(null);
  const [inFlight, setInFlight] = useState<InFlightBlock | null>(null);
  // ★ Ruling C：提交按钮从一开始就带在飞护栏——disabled + 入口早退 + finally 复位，
  //   与 F3（保存格子）、F4（删除货号）同一形状，防双击发出重复提交
  const [submitting, setSubmitting] = useState(false);

  const load = () => Promise.all([api.getPlan(planId), api.getGrid(planId), api.listSellers()])
    .then(([p, g, s]) => { setPlan(p); setGrid(g); setSellers(s); setErr(null); })
    .catch((e: ApiError) => setErr(e));

  useEffect(() => { void load(); }, [planId]);

  const { pending, inputErr, saveDemand, savePurchase, removeSku, removeMsku } = usePlanGridSaves(planId, load, setErr);

  const model = useMemo(() => (grid ? buildGridModel(grid, sellers) : null), [grid, sellers]);

  if (err && grid === null) return <AppShell crumb="计划编辑"><ErrorDetail err={err} /></AppShell>;
  if (!grid || !model || !plan) return <AppShell crumb="计划编辑"><div className="empty" /></AppShell>;

  async function submit() {
    if (submitting) return;
    setSubmitting(true);
    setReport(null);
    setInFlight(null);
    setErr(null);
    try {
      const r = await api.submit(planId);
      setReport(r);
      // ★ 不自动跳走：skipped[] 只在这一次响应里存在，成功后留在原地由人点「去版本」
      pushToast({
        kind: r.skipped.length === 0 ? 'ok' : 'warn',
        text: `已提交 rev ${r.rev}，铸出 ${r.lines} 条，跳过 ${r.skipped.length} 条`,
      });
      await load();
    } catch (e) {
      const ae = e as ApiError;
      // ★ 409 是「你没写错，但现在不行」—— 点名旧版号，下一步是去看那一版
      if (ae.status === 409 && ae.error === 'rev_in_flight') {
        setInFlight({ rev: Number(ae.fields['in_flight_rev']), err: ae });
        return;
      }
      setErr(ae);
    } finally {
      setSubmitting(false);
    }
  }

  const toggle = (key: string) => setExpanded((s) => {
    const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n;
  });

  return (
    <AppShell crumb="计划编辑">
      <div className="head">
        <div className="head__main">
          <h1>{plan.title}</h1>
          {/* ★ F16 裁定：三段各自成句，不用 `·` 拼成一行元串 */}
          <div className="head__meta" data-testid="head-meta">
            <span>起始月 <b>{plan.period_start.slice(0, 7)}</b></span>
            <span>跨 <b>{plan.months}</b> 月</span>
            <span>负责人 <b>{plan.owner_actor}</b></span>
          </div>
        </div>
        <div className="head__act">
          {/* ★ F17 裁定：本页内部导航改用 Router 的 Link，避免整页刷新 */}
          <Link className="btn" to={`/plans/${planId}/add`}>添加货品</Link>
          <button type="button" className="btn btn--ghost" onClick={() => void load()}>重置</button>
          {/* ★「提交」排在「重置」之后，与原理图一致；disabled 是双击护栏（Ruling C） */}
          <button type="button" className="btn btn--primary" disabled={submitting} onClick={() => void submit()}>
            提交
          </button>
          <Link className="btn" to={`/plans/${planId}/revs`}>版本</Link>
        </div>
      </div>

      <SubmitPanel planId={planId} report={report} inFlight={inFlight} />

      {model.orphans.length > 0 && (
        <div className="sec" data-testid="orphans">
          {model.orphans.map((o) => (
            <div className="dropline" key={`${o.kind}-${o.key}`}>
              <span className="k">{o.kind}</span><span>{o.key}</span>
            </div>
          ))}
        </div>
      )}

      {model.blocks.map((block) => {
        const key = `${block.sid}-${block.sku}`;
        const open = expanded.has(key);
        return (
          <div className="sheet" key={key} data-testid={`block-${key}`} data-open={open ? 'true' : 'false'}>
            <div className="sheet__head">
              {/* ★ F11 裁定：真 button 要有 aria-expanded，且可及名称点名是哪一块 */}
              <button
                type="button" className="gr__toggle" aria-expanded={open}
                aria-label={`${open ? '折叠' : '展开'} ${block.seller_name} 的 ${block.sku}`}
                onClick={() => toggle(key)}
              >
                {open ? '折叠' : '展开'}
              </button>
              <span className="sheet__sku">{block.sku}</span>
              <span className="sheet__name">{block.seller_name}</span>
              <span className="sheet__stat">
                {/* ★ 断货计数挂在块头：折起来也看得见 */}
                {outageCount(block) > 0 && (
                  <span className="chip chip--bad" data-testid={`outage-${block.sid}-${block.sku}`}>
                    断货 {outageCount(block)} 个月
                  </span>
                )}
                <button type="button" className="btn btn--sm btn--danger" onClick={() => void removeSku(block)}>
                  删除货号
                </button>
              </span>
            </div>
            <div className="sheet__scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th className="gh--row">店铺·货号</th>
                    {model.periods.map((p) => <th key={p}>{p}</th>)}
                    <th className="gh--sum">合计</th>
                  </tr>
                </thead>
                <tbody>
                  {/* ★ 店铺·货号行：库存预估的身份就是这一行（02 §3.1a），折叠态也在 */}
                  <tr>
                    <td className="gr">
                      <div className="gr__who">{block.seller_name}</div>
                      <div className="gr__code">{block.sku}</div>
                      <div className="gr__sub">{block.mskus.length} 个 msku</div>
                    </td>
                    {model.periods.map((p) => {
                      const closing = inventoryAt(block, p);
                      const cell = block.inventory.find((i) => i.period === p);
                      return (
                        <td className="cell" key={p} data-state={stateOf(closing)} data-testid={`sku-cell-${p}`}>
                          <div className="cell__stack">
                            <div className="cell__lead i-pencil">
                              预估 <Qty v={sumUnits(block.mskus.map((r) =>
                                r.cells.find((c) => c.period === p)?.system_units ?? null))} />
                            </div>
                            {/* ★ F10 裁定：这个 testid 恒为 closing-sku（不再随 open 切换）——
                                折叠行本身不随展开/折叠消失，状态改用块上的 data-open 表达 */}
                            <div data-testid={closing.kind === 'na' ? 'closing-na' : 'closing-sku'}>
                              <Qty v={closing} big />
                            </div>
                            {cell && cell.basis.closing_reason !== 'not_applicable' && (
                              <div className="basis">
                                <span>期初 {cell.onhand ?? '—'}</span>
                                <span>{cell.basis.as_of}</span>
                                <span className="chip chip--dim">未计本计划采购</span>
                              </div>
                            )}
                            <div className="cell__row"><span className="k">Σ 期望</span>
                              <span className="v i-ink" data-testid={`sum-expected-${p}`}>
                                <Qty v={demandAt(block, p)} /></span></div>
                          </div>
                        </td>
                      );
                    })}
                    <td className="gsum">
                      <div data-testid="sum-demand">
                        <Qty v={sumUnits(block.mskus.flatMap((r) => r.cells.map((c) => c.effective_units)))} />
                      </div>
                      {/* ★ 存量不求和，给期末：onhand 是一条链，三个月加起来等于把同一批货数三遍。
                          无 FBA 的店铺整块「不适用」——每格已经写了一遍，合计列不再重复这三个字 */}
                      {block.has_fba && (
                        <div className="cell__row"><span className="k">期末</span>
                          <span className="v" data-testid="sum-inventory">
                            <Qty v={closingOfLast(block, model.periods)} /></span></div>
                      )}
                    </td>
                  </tr>

                  {open && block.mskus.map((row) => (
                    <tr key={row.seller_sku}>
                      <td className="gr">
                        <div className="gr__code">{row.seller_sku}</div>
                        <div className="gr__sub">
                          sid {row.sid}{' '}
                          <button type="button" className="btn btn--sm btn--ghost"
                                  onClick={() => void removeMsku(row.seller_sku, row.sid)}>删除 msku</button>
                        </div>
                      </td>
                      {model.periods.map((p) => {
                        const c = row.cells.find((x) => x.period === p);
                        const dKey = `d:${row.seller_sku}:${row.sid}:${p}`;
                        return (
                          <td className="cell" key={p} data-testid={`cell-${row.seller_sku}-${row.sid}-${p}`}>
                            <div className="cell__stack">
                              <div className="cell__lead i-pencil" data-testid="system">
                                预估 {c?.system_units ?? '—'}
                                {/* ★ F15 裁定：可见的「外」字保留，可及名称改用 aria-label */}
                                {c?.system_extrapolated && (
                                  <sup className="ext" aria-label="系统外推" title="外推">外</sup>
                                )}
                              </div>
                              <input
                                className="g" type="text" inputMode="numeric" placeholder=""
                                aria-label={`期望销量 ${row.seller_sku} ${p}`}
                                defaultValue={c?.expected_units == null ? '' : String(c.expected_units)}
                                data-touched={c?.basis === 'human' ? '1' : undefined}
                                disabled={pending.has(dKey)}
                                onBlur={(e) => void saveDemand(row.seller_sku, row.sid, p, e.target.value)}
                              />
                              {inputErr[dKey] && (
                                <div className="i-red" role="alert" data-testid={`err-${row.seller_sku}-${row.sid}-${p}`}>
                                  {inputErr[dKey]}
                                </div>
                              )}
                            </div>
                          </td>
                        );
                      })}
                      <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.effective_units))} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      <div className="sheet" data-testid="purchase-block">
        <div className="sheet__head"><span className="sheet__sku">计划采购量</span></div>
        <div className="sheet__scroll">
          <table className="grid">
            <thead>
              <tr>
                <th className="gh--row">货号</th>
                {model.periods.map((p) => <th key={p}>{p}</th>)}
                <th className="gh--sum">合计</th>
              </tr>
            </thead>
            <tbody>
              {model.purchase.map((row) => (
                <tr key={row.sku}>
                  <td className="gr"><div className="gr__code">{row.sku}</div></td>
                  {row.cells.map((c) => {
                    const pKey = `p:${row.sku}:${c.period}`;
                    return (
                      <td className="cell" key={c.period}>
                        <input
                          className="g" type="text" inputMode="numeric" placeholder=""
                          aria-label={`计划采购量 ${row.sku} ${c.period}`}
                          defaultValue={c.planned_units === null ? '' : String(c.planned_units)}
                          data-touched={c.planned_units === null ? undefined : '1'}
                          disabled={pending.has(pKey)}
                          onBlur={(e) => void savePurchase(row.sku, c.period, e.target.value)}
                        />
                        {inputErr[pKey] && (
                          <div className="i-red" role="alert" data-testid={`err-purchase-${row.sku}-${c.period}`}>
                            {inputErr[pKey]}
                          </div>
                        )}
                      </td>
                    );
                  })}
                  <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.planned_units))} /></td>
                </tr>
              ))}
              {/* ★ F6/F8 裁定：货号级在途改按 model.transit（blocks ∪ purchase ∪ pipeline 的并集）铺，
                  权威读数来自 inventory[].basis.sku_level_in_transit，只读、一件都不分摊到店铺 */}
              {model.transit.map((row) => (
                <tr key={`transit-${row.sku}`} data-testid={`transit-row-${row.sku}`}>
                  <td className="gr">
                    <div className="gr__code">{row.sku}</div>
                    <div className="gr__sub i-pencil">货号级在途</div>
                    <div className="gr__sub"><span className="chip chip--dim">未分摊到店铺</span></div>
                  </td>
                  {row.cells.map((c) => (
                    <td className="cell" key={c.period}>
                      <span className="i-pencil">
                        <Qty v={c.units === null ? { kind: 'unknown' } : { kind: 'num', value: c.units }} />
                      </span>
                    </td>
                  ))}
                  <td className="gsum">
                    <Qty v={sumUnits(row.cells.map((c) => c.units))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {err && <ErrorDetail err={err} />}
    </AppShell>
  );
}

/** ★ 状态用左边条不用徽章（10 §3.2 ③）。未知与不适用都不给 data-state —— 它们不是「没货」 */
function stateOf(v: QtyValue): 'out' | 'low' | undefined {
  if (v.kind !== 'num') return undefined;
  if (v.value <= 0) return 'out';
  if (v.value < 50) return 'low';
  return undefined;
}
