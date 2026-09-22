import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty, type QtyValue } from '../components/Qty';
import { api, ApiError } from '../api';
import type { GridResponse, PlanSummary, Seller } from '../api/types';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, parseUnits, sumUnits, type SkuBlock,
} from './planGridModel';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<ApiError | null>(null);
  // ★ F3 裁定：PUT 在飞时置灰、忽略第二次 blur，避免先回来的 load() 覆盖后回来的那次
  const [pending, setPending] = useState<Set<string>>(new Set());
  // ★ F1 裁定：校验不过时就地给一条具名的行内提示，不是 toast —— toast 一闪就没了，
  //   这种「你刚填的这一格」的反馈需要留在原地
  const [inputErr, setInputErr] = useState<Record<string, string>>({});

  const load = () => Promise.all([api.getPlan(planId), api.getGrid(planId), api.listSellers()])
    .then(([p, g, s]) => { setPlan(p); setGrid(g); setSellers(s); setErr(null); })
    .catch((e: ApiError) => setErr(e));

  useEffect(() => { void load(); }, [planId]);

  const model = useMemo(() => (grid ? buildGridModel(grid, sellers) : null), [grid, sellers]);

  if (err && grid === null) return <AppShell crumb="计划编辑"><ErrorDetail err={err} /></AppShell>;
  if (!grid || !model || !plan) return <AppShell crumb="计划编辑"><div className="empty" /></AppShell>;

  function clearErr(key: string) {
    setInputErr((m) => { if (!(key in m)) return m; const n = { ...m }; delete n[key]; return n; });
  }

  // ★ F1/F2 裁定：期望销量与计划采购量共用同一套解析（`parseUnits`）——
  //   空 = 未知 ⇒ null；非负整数才收；`5oo`/负数/小数一律拒收，且**不发请求**。
  async function saveDemand(sellerSku: string, sid: string, period: string, raw: string) {
    const key = `d:${sellerSku}:${sid}:${period}`;
    if (pending.has(key)) return;
    const parsed = parseUnits(raw);
    if (!parsed.ok) { setInputErr((m) => ({ ...m, [key]: parsed.why })); return; }
    clearErr(key);
    setPending((s) => new Set(s).add(key));
    try { await api.putDemand(planId, sellerSku, sid, period, parsed.value); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
    finally { setPending((s) => { const n = new Set(s); n.delete(key); return n; }); }
  }

  async function savePurchase(sku: string, period: string, raw: string) {
    const key = `p:${sku}:${period}`;
    if (pending.has(key)) return;
    const parsed = parseUnits(raw);
    if (!parsed.ok) { setInputErr((m) => ({ ...m, [key]: parsed.why })); return; }
    clearErr(key);
    setPending((s) => new Set(s).add(key));
    try { await api.putPurchase(planId, sku, period, parsed.value); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
    finally { setPending((s) => { const n = new Set(s); n.delete(key); return n; }); }
  }

  // ★ F4/F5 裁定：try/catch → ErrorDetail + reload；逐个释放、遇错即停，
  //   toast 点名已释放的与失败的那一个；搁浅的货号级采购格单独报一句，不许吞掉
  async function removeSku(block: SkuBlock) {
    const released: string[] = [];
    let dropped = 0;
    let stranded = 0;
    for (const row of block.mskus) {
      try {
        const r = await api.releaseClaim(planId, row.seller_sku, row.sid);
        released.push(row.seller_sku);
        dropped += r.dropped_cells.length;
        stranded += r.stranded_purchase_cells.length;
      } catch (e) {
        // ★ load() 成功时会把 err 清空（`setErr(null)`）——先 load 再 setErr，
        //   否则这一条错误会被自己触发的重载悄悄冲掉
        await load();
        setErr(e as ApiError);
        const releasedText = released.length > 0 ? released.join('、') : '无';
        pushToast({ kind: 'fail', text: `${block.sku} 移出中断：已释放 ${releasedText}，${row.seller_sku} 释放失败` });
        return;
      }
    }
    await load();
    let text = `已移出 ${released.length} 个 msku，丢弃 ${dropped} 格`;
    if (stranded > 0) text += `。货号级采购格 ${stranded} 个未删、待处理`;
    pushToast({ kind: 'ok', text });
  }

  async function removeMsku(sellerSku: string, sid: string) {
    try {
      const { dropped_cells, stranded_purchase_cells } = await api.releaseClaim(planId, sellerSku, sid);
      await load();
      let text = `${sellerSku} 已移出，丢弃 ${dropped_cells.length} 格`;
      if (stranded_purchase_cells.length > 0) text += `。货号级采购格 ${stranded_purchase_cells.length} 个未删、待处理`;
      pushToast({ kind: 'ok', text });
    } catch (e) {
      await load();
      setErr(e as ApiError);
      pushToast({ kind: 'fail', text: `${sellerSku} 移出失败` });
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
          <Link className="btn" to={`/plans/${planId}/revs`}>版本</Link>
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
                          <td className="cell" key={p} data-testid={`cell-${row.seller_sku}-${p}`}>
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
                                <div className="i-red" role="alert" data-testid={`err-${row.seller_sku}-${p}`}>
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
