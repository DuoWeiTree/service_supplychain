import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty, type QtyValue } from '../components/Qty';
import { api, ApiError } from '../api';
import type { GridResponse, PlanSummary, Seller } from '../api/types';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, sumUnits, type SkuBlock,
} from './planGridModel';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<ApiError | null>(null);

  const load = () => Promise.all([api.getPlan(planId), api.getGrid(planId), api.listSellers()])
    .then(([p, g, s]) => { setPlan(p); setGrid(g); setSellers(s); setErr(null); })
    .catch((e: ApiError) => setErr(e));

  useEffect(() => { void load(); }, [planId]);

  const model = useMemo(() => (grid ? buildGridModel(grid, sellers) : null), [grid, sellers]);

  if (err && grid === null) return <AppShell crumb="计划编辑"><ErrorDetail err={err} /></AppShell>;
  if (!grid || !model || !plan) return <AppShell crumb="计划编辑"><div className="empty" /></AppShell>;

  async function saveDemand(sellerSku: string, sid: string, period: string, raw: string) {
    // ★ 空串 = 未知 ⇒ null；不是 0
    const units = raw.trim() === '' ? null : Number(raw);
    if (units !== null && !Number.isInteger(units)) {
      pushToast({ kind: 'fail', text: `${sellerSku} ${period}：${raw} 不是整数` });
      return;
    }
    try { await api.putDemand(planId, sellerSku, sid, period, units); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
  }

  async function savePurchase(sku: string, period: string, raw: string) {
    const units = raw.trim() === '' ? null : Number(raw);
    try { await api.putPurchase(planId, sku, period, units); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
  }

  async function removeSku(block: SkuBlock) {
    let dropped = 0;
    for (const row of block.mskus) {
      dropped += (await api.releaseClaim(planId, row.seller_sku, row.sid)).dropped_cells.length;
    }
    await load();
    pushToast({ kind: 'ok', text: `${block.sku} 移出 ${block.mskus.length} 个 msku · 丢弃 ${dropped} 格` });
  }

  async function removeMsku(sellerSku: string, sid: string) {
    const { dropped_cells } = await api.releaseClaim(planId, sellerSku, sid);
    await load();
    pushToast({ kind: 'ok', text: `${sellerSku} 已移出 · 丢弃 ${dropped_cells.length} 格` });
  }

  const toggle = (key: string) => setExpanded((s) => {
    const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n;
  });

  return (
    <AppShell crumb="计划编辑">
      <div className="head">
        <div className="head__main">
          <h1>{plan.title}</h1>
          <div className="head__meta">
            起始月 <b>{plan.period_start.slice(0, 7)}</b> · 跨 <b>{plan.months}</b> 月 ·
            负责人 <b>{plan.owner_actor}</b>
          </div>
        </div>
        <div className="head__act">
          <a className="btn" href={`/plans/${planId}/add`}>添加货品</a>
          <button type="button" className="btn btn--ghost" onClick={() => void load()}>重置</button>
          <a className="btn" href={`/plans/${planId}/revs`}>版本</a>
        </div>
      </div>

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
          <div className="sheet" key={key} data-testid={`block-${key}`}>
            <div className="sheet__head">
              <button type="button" className="gr__toggle" onClick={() => toggle(key)}>
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
                            {/* ★ 折叠态用 `closing`：这是本格唯一权威来源，就在这一行。
                                展开后 msku 明细已经可见，这个求和数字降级为 `closing-sku`
                                标记 —— 避免与「库存只画一遍」的判据在扫描 DOM 时撞名 */}
                            <div data-testid={closing.kind === 'na' ? 'closing-na' : (open ? 'closing-sku' : 'closing')}>
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
                        return (
                          <td className="cell" key={p} data-testid={`cell-${row.seller_sku}-${p}`}>
                            <div className="cell__stack">
                              <div className="cell__lead i-pencil" data-testid="system">
                                预估 {c?.system_units ?? '—'}
                                {c?.system_extrapolated && <sup className="ext" title="外推">外</sup>}
                              </div>
                              <input
                                className="g" type="text" inputMode="numeric" placeholder=""
                                aria-label={`期望销量 ${row.seller_sku} ${p}`}
                                defaultValue={c?.expected_units == null ? '' : String(c.expected_units)}
                                data-touched={c?.basis === 'human' ? '1' : undefined}
                                onBlur={(e) => void saveDemand(row.seller_sku, row.sid, p, e.target.value)}
                              />
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
                  {row.cells.map((c) => (
                    <td className="cell" key={c.period}>
                      <input
                        className="g" type="text" inputMode="numeric" placeholder=""
                        aria-label={`计划采购量 ${row.sku} ${c.period}`}
                        defaultValue={c.planned_units === null ? '' : String(c.planned_units)}
                        data-touched={c.planned_units === null ? undefined : '1'}
                        onBlur={(e) => void savePurchase(row.sku, c.period, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.planned_units))} /></td>
                </tr>
              ))}
              {/* ★ 货号级在途：只读一行，一件都不分摊到店铺 */}
              {model.purchase.map((row) => (
                <tr key={`transit-${row.sku}`} data-testid="transit-row">
                  <td className="gr">
                    <div className="gr__code i-pencil">货号级在途</div>
                    <div className="gr__sub"><span className="chip chip--dim">未分摊到店铺</span></div>
                  </td>
                  {model.periods.map((p) => {
                    const t = model.pipeline.find((x) => x.sku === row.sku && x.period === p)?.units ?? null;
                    return (
                      <td className="cell" key={p}>
                        <span className="i-pencil">
                          <Qty v={t === null ? { kind: 'unknown' } : { kind: 'num', value: t }} />
                        </span>
                      </td>
                    );
                  })}
                  <td className="gsum">
                    <Qty v={sumUnits(model.periods.map((p) =>
                      model.pipeline.find((x) => x.sku === row.sku && x.period === p)?.units ?? null))} />
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
