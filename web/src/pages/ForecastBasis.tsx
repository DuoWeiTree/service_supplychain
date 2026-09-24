import type { ReactNode } from 'react';
import { Fold } from '../components/Fold';
import type {
  ForecastArrival, ForecastReason, ForecastState, MskuForecast, NoSalesSourceCause, Period, StoreForecast, SystemBasis,
} from '../api/types';
import { totalsAt, type GridModel, type MskuCell, type MskuRow, type SkuBlock } from './planGridModel';

export type BasisTarget =
  | { kind: 'msku'; block: SkuBlock; row: MskuRow; period: Period }
  | { kind: 'store'; block: SkuBlock; period: Period }
  | { kind: 'sku'; sku: string; period: Period };

/** ★ 库存为什么没有数。格子上的 tooltip 与依据面板共用这一份，两处说法不许分叉 */
export const FORECAST_WHY: Record<Exclude<ForecastReason, null>, string> = {
  as_of_inside_month: 'as_of 在本月中，从次日起扣',
  unknown_onhand: '无在仓数',
  unknown_demand: '期望销量待填',
  bridge_rate_unknown: '没有预测速率，as_of 到首月之间扣不了',
  inbound_unknown: '到货数未知（有店缺口未知，分不出去）',
  not_applicable: '无 FBA',
  elapsed: '月份已过去',
  no_msku: '无 msku',
  not_recomputed_in_mock: '改后未重算（mock）',
};

const CAUSE: Record<NoSalesSourceCause, string> = {
  non_amazon_platform: '非亚马逊店铺，没有销量取数源',
  store_absent_from_order_report: '订单报表里没有这个店，没有销量取数源',
};

const md = (iso: string) => iso.slice(5);                       // "2026-09-22" → "09-22"
const n = (v: number | null) => (v === null ? '—' : String(v));
function prevMonth(p: Period): Period {
  const [y, m] = p.split('-').map(Number) as [number, number];
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
}

export function basisTitle(t: BasisTarget): string {
  if (t.kind === 'msku') return `库存 ${t.block.seller_name} / ${t.row.seller_sku} / ${t.period}`;
  if (t.kind === 'store') return `库存 ${t.block.seller_name} / ${t.block.sku} / ${t.period}`;
  return `货号 ${t.sku} / ${t.period}`;
}

/** 没有期末时说为什么。★ 只说原因，不给任何数（mock 未重算的行尤其：旧数就是错数） */
function noClosing(state: ForecastState, reason: ForecastReason, since: Period | null = null): string {
  if (state === 'not_applicable') return '无 FBA，不推演库存';
  if (reason === 'not_recomputed_in_mock' || state !== 'unknown') return reason ? FORECAST_WHY[reason] : '无数据';
  return `未知：${since ? `${since} 起` : ''}${reason ? FORECAST_WHY[reason] : '原因未给'}`;
}

function arrivalLine(a: ForecastArrival): string {
  if (a.reason === 'not_recomputed_in_mock') return `${md(a.date)} 到货 —（${a.order_period} 下单，改后未重算）`;
  if (a.units === null) return `${md(a.date)} 到货 —（${a.order_period} 下单，有店缺口未知，分不出去）`;
  return `${md(a.date)} 到货 ${a.units}（${a.order_period} 下单，${a.rule ?? '分法未给'}，这批 ${n(a.pool)}）`;
}

/** partial 月（as_of 在本月里）的起点。★ 不叫「期初」：本月 as_of 及之前的天数不在账上 */
function asOfStartLine(asOf: string, onhand: number | null, month: Period, sigma = ''): string {
  return `as_of ${asOf} 可售 ${n(onhand)}（${sigma}本月 ${md(month + '-01')}–${md(asOf)} 已过，不计）`;
}

/** Σ 一组 partial 月的 msku 行。任一缺数 ⇒ null（Σ 不当 0） */
function partialSum(rows: (MskuForecast | undefined)[]) {
  const pick = (g: (f: MskuForecast) => number | null | undefined) => {
    const v = rows.map((f) => (f ? g(f) ?? null : null));
    return v.length === 0 || v.some((x) => x === null) ? null : v.reduce<number>((a, b) => a + (b as number), 0);
  };
  return { onhand: pick((f) => f.basis.as_of_start?.onhand_as_of), demand: pick((f) => f.demand),
           inbound: pick((f) => f.inbound_planned) };
}

function openingLine(f: MskuForecast, periods: Period[], asOf: string): string {
  if (f.state === 'partial') return asOfStartLine(asOf, f.basis.as_of_start?.onhand_as_of ?? null, f.period);
  const i = periods.indexOf(f.period);
  const b = f.basis.bridge;
  if (b) {
    const arr = b.arrival_units ? ` + 到货 ${b.arrival_units}` : '';
    return `期初 ${n(f.opening)}（= ${prevMonth(f.period)} 末；as_of ${asOf} 可售 ${b.onhand_as_of}`
      + ` − ${md(b.from)}–${md(b.to)} 按预测扣 ${n(b.units)}${arr}）`;
  }
  if (i > 0) return `期初 ${n(f.opening)}（= ${periods[i - 1]} 期末）`;
  return `期初 ${n(f.opening)}（= as_of ${asOf} 可售）`;
}

/** 期初 → − 期望 → + 到货 → = 期末 → 下月期初。★ 链是恒等式③④（14 §1.1），每一环都要看得见 */
function MskuChain({ f, periods, asOf, firstStockout, monthExpected }: {
  f: MskuForecast | undefined; periods: Period[]; asOf: string; firstStockout: string | null;
  monthExpected: number | null;
}): ReactNode {
  if (!f) return <p>没有这一月的推演行</p>;
  const b = f.basis.bridge;
  const stale = f.reason === 'not_recomputed_in_mock';
  const nxt = periods[periods.indexOf(f.period) + 1];
  return (
    <div className="fb-chain" data-testid="fb-chain">
      {f.closing === null ? (
        <>
          {!stale && f.opening !== null && <div>{openingLine(f, periods, asOf)}</div>}
          <div data-testid="fb-no-closing">{noClosing(f.state, f.reason, f.unknown_since)}</div>
        </>
      ) : (
        <>
          <div>{openingLine(f, periods, asOf)}</div>
          {b?.arrivals.map((a) => <div key={`b${a.date}${a.order_period}`} className="fb-sub">{arrivalLine(a)}</div>)}
          {b?.stockout_date && <div className="fb-sub">as_of 到首月之间断货 {b.stockout_date}</div>}
          {f.state === 'partial'
            // ★ 这里的数只是 as_of 次日到月末那一段，不是格子上的期望 —— 标出来，否则链对不上格子
            ? <div>− 本月 as_of 后期望 {n(f.demand)}（{md(f.basis.as_of_start?.from ?? asOf)} 起，全月期望 {n(monthExpected)} 的剩余部分）</div>
            : <div>− 期望 {n(f.demand)}</div>}
          <div>+ 到货 {n(f.inbound_planned)}</div>
          {f.basis.arrivals.map((a) => <div key={`a${a.date}${a.order_period}`} className="fb-sub">{arrivalLine(a)}</div>)}
          <div>= 期末 {f.closing}{nxt ? `　→ ${nxt} 期初` : ''}</div>
        </>
      )}
      {!stale && f.stockout_date && <div>本月断货 {f.stockout_date}</div>}
      {!stale && firstStockout && <div>首次断货 {firstStockout}</div>}
    </div>
  );
}

/** spec §3.3：「预测 124 = 4.13 件/天 × 30 天」。★ 没有依据就说为什么没有，不编数 */
function forecastLine(c: MskuCell | undefined): string {
  if (!c) return '预测 —（没有这一格）';
  const sb = c.system_basis;
  if (sb === null) return '预测 —（旧认领无依据，重新添加后可算）';
  if (sb.reason === 'not_applicable_no_sales_source') return `预测 不适用（${CAUSE[sb.cause]}）`;
  if (sb.base_rate === null) return `预测 —（${sb.reason === 'no_sales_in_window' ? '窗口内没有销量' : '没有预测速率'}）`;
  const extra = [
    sb.stock_days_uncaptured > 0 ? `库存未采到 ${sb.stock_days_uncaptured} 天` : null,
    c.system_extrapolated ? '外推' : null,
  ].filter(Boolean).map((s) => `，${s}`).join('');
  // ★ 写出乘积与取整：只写 4.00 × 30 时 121 看起来是算错了
  const product = sb.units_raw === null ? '' : ` = ${sb.units_raw.toFixed(2)} → 向上取整 ${n(c.system_units)}`;
  return `预测 ${n(c.system_units)} = ${sb.base_rate.toFixed(4)} 件/天 × ${sb.days_in_month} 天${product}`
    + `（窗口 ${md(sb.window.from)} ~ ${md(sb.window.until)}，禁言 ${sb.embargo_days} 天，剔断货 ${sb.censored_days} 天${extra}）`;
}

function MskuBody({ model, row, period }: { model: GridModel; row: MskuRow; period: Period }) {
  const first = model.firstStockout.get(`${row.seller_sku}/${row.sid}`) ?? null;
  const cell = row.cells.find((c) => c.period === period);
  return (
    <>
      <MskuChain f={row.forecast.find((f) => f.period === period)} periods={model.periods}
                 asOf={model.params.as_of} firstStockout={first} monthExpected={cell?.effective_units ?? null} />
      <div className="fb-fc">{forecastLine(cell)}</div>
    </>
  );
}

/** 收缩的中间量（forecast/baseline.py）。★ 进折叠区：屏上给结论，这里给它从哪来 */
function RateDetail({ sb }: { sb: SystemBasis }) {
  if (sb.base_rate === null || sb.raw_rate === null || sb.target_rate === null || sb.n_eff === null) return null;
  const k = sb.constants;
  const f4 = (x: number) => x.toFixed(4);
  return (
    <>
      <p>
        速率 {f4(sb.base_rate)} = (n × 收缩前 + {k.shrink_prior} × 自身 {k.target_window_days} 天均值) ÷ (n + {k.shrink_prior})
        = ({sb.n_eff.toFixed(2)} × {f4(sb.raw_rate)} + {k.shrink_prior} × {f4(sb.target_rate)}) ÷ {(sb.n_eff + k.shrink_prior).toFixed(2)}
      </p>
      <p>
        收缩前 {f4(sb.raw_rate)}：估计窗 {sb.window.from} ~ {sb.window.until} 的 {k.window_days} 天日销，剔断货日，
        {sb.winsor_cap === null ? '非零日不足 5 天不削峰' : `截到非零日 p${Math.round(k.winsor_q * 100)}（${sb.winsor_cap}）`}，
        半衰期 {k.halflife_days} 天指数加权；n = 权重和 {sb.n_eff.toFixed(2)}
      </p>
      <p>自身 {k.target_window_days} 天均值 {f4(sb.target_rate)}：目标窗 {sb.target_window.from} ~ {sb.target_window.until}{sb.first_seen ? `，首次有销量 ${sb.first_seen}` : ''}</p>
      <p>估计锚点 {sb.estimation_anchor} = as_of {sb.as_of} − 禁言 {sb.embargo_days} 天</p>
    </>
  );
}

/** partial 月的 Σ 链：店铺 / 货号的 opening 是 null、期望是全月 —— 起点与期望都改取 Σ msku 的那一段 */
function partialChain(rows: (MskuForecast | undefined)[], closing: number, asOf: string, month: Period, nxt: Period | undefined): string {
  const p = partialSum(rows);
  return `${asOfStartLine(asOf, p.onhand, month, 'Σ msku，')} − 本月 as_of 后期望 ${n(p.demand)} + 到货 ${n(p.inbound)} = 期末 ${closing}`
    + (nxt ? `　→ ${nxt} 期初` : '');
}

function storeChain(s: StoreForecast | undefined, block: SkuBlock, periods: Period[], asOf: string): string {
  if (!s) return '没有这一月的店铺行';
  if (s.closing === null) return noClosing(s.state, s.reason);
  const nxt = periods[periods.indexOf(s.period) + 1];
  if (s.state === 'partial') {
    return partialChain(block.mskus.map((r) => r.forecast.find((f) => f.period === s.period)), s.closing, asOf, s.period, nxt);
  }
  return `期初 ${n(s.opening)} − 期望 ${n(s.demand)} + 到货 ${n(s.inbound_planned)} = 期末 ${s.closing}`
    + (nxt ? `　→ ${nxt} 期初` : '');
}

function StoreBody({ model, block, period }: { model: GridModel; block: SkuBlock; period: Period }) {
  // 单 msku 的店铺：店铺的链就是这个 msku 的链，直接给它（含桥接段与到货怎么分）
  if (block.mskus.length === 1) return <MskuBody model={model} row={block.mskus[0]!} period={period} />;
  const s = block.stock.find((x) => x.period === period);
  return (
    <>
      <div className="fb-chain" data-testid="fb-chain"><div>{storeChain(s, block, model.periods, model.params.as_of)}</div></div>
      <ul className="fb-list">
        {block.mskus.map((r) => {
          const f = r.forecast.find((x) => x.period === period);
          return (
            <li key={r.seller_sku}>
              {r.seller_sku}：{f && f.closing !== null ? `期末 ${f.closing}` : f ? noClosing(f.state, f.reason, f.unknown_since) : '没有推演行'}
            </li>
          );
        })}
      </ul>
      <div className="fb-fc">
        预测 {s?.system_not_applicable ? '不适用' : n(s?.system ?? null)}（Σ msku）
      </div>
    </>
  );
}

const UNALLOCATED_WHY: Record<string, string> = {
  gap_unknown: '有店缺口未知', no_fba_msku: '没有 FBA 店', not_applicable: '无 FBA',
  unknown_onhand: '无在仓数', not_recomputed_in_mock: '改后未重算',
};

function SkuBody({ model, sku, period }: { model: GridModel; sku: string; period: Period }) {
  const t = totalsAt(model, sku, period);
  if (!t) return <p>没有这一月的货号行</p>;
  const pl = t.pipeline;
  const nxt = model.periods[model.periods.indexOf(period) + 1];
  const chain = t.closing === null
    ? (t.reason_from && t.reason !== 'not_recomputed_in_mock'
      ? `${t.reason_from.seller_sku}：${noClosing(t.state, t.reason)}` : noClosing(t.state, t.reason))
    : t.state === 'partial'
      ? partialChain(model.blocks.filter((b) => b.sku === sku && b.has_fba)
          .flatMap((b) => b.mskus.map((r) => r.forecast.find((f) => f.period === period))),
        t.closing, model.params.as_of, period, nxt)
      : `期初 ${n(t.opening)} − 期望 ${n(t.stock_demand)} + 到货 ${n(t.inbound_planned)} = 期末 ${t.closing}`
        + (nxt ? `　→ ${nxt} 期初` : '');
  const beyond = model.beyond.filter((b) => b.sku === sku);
  const unalloc = model.unallocated.filter((u) => u.sku === sku);
  return (
    <>
      <div className="fb-chain" data-testid="fb-chain">
        <div>{chain}</div>
        {t.closing !== null && t.excluded_not_applicable > 0 && <div className="fb-sub">不含 {t.excluded_not_applicable} 家无 FBA 店铺</div>}
      </div>
      <div className="fb-chain">
        <div>在途 {n(t.in_transit)} = 生产中 {n(pl.making)} + 货运中 {n(pl.shipping)} + 已下单未到 {pl.ordered_open}</div>
        {pl.ordered_open_sources.map((o) => <div key={o.ref} className="fb-sub">{o.ref} {o.units}（{o.period} 下单）</div>)}
        <div className="fb-sub">FBA 货件 未取</div>
      </div>
      {t.unfilled_purchase === null
        ? <div>采购未填：改后未重算</div>
        : t.unfilled_purchase.length > 0 && <div>采购未填：{t.unfilled_purchase.join('、')}</div>}
      {(beyond.length > 0 || unalloc.length > 0) && (
        <ul className="fb-list">
          {beyond.map((b) => (
            <li key={`bh${b.order_period}`}>
              {b.order_period} 下单 {n(b.units)} 件 → {b.arrive} 到货（{
                b.reason === 'arrives_after_horizon' ? '周期外' : b.reason === 'arrives_before_as_of' ? '早于 as_of' : '改后未重算'}）
            </li>
          ))}
          {unalloc.map((u) => (
            <li key={`ua${u.order_period}${u.scope === 'msku' ? u.seller_sku + u.sid : ''}`}>
              {u.arrive} 到货 {n(u.units)} 件{u.scope === 'msku' ? `分给 ${u.seller_sku} 但推演不了` : '没分出去'}（{UNALLOCATED_WHY[u.reason] ?? u.reason}）
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function fresh(model: GridModel, t: BasisTarget): BasisTarget {
  if (t.kind === 'sku') return t;
  const block = model.blocks.find((b) => b.sid === t.block.sid && b.sku === t.block.sku) ?? t.block;
  if (t.kind === 'store') return { ...t, block };
  const row = block.mskus.find((r) => r.seller_sku === t.row.seller_sku) ?? t.row;
  return { ...t, block, row };
}

export function ForecastBasis({ model, target: t0 }: { model: GridModel; target: BasisTarget }) {
  const { make_days, ship_days, anchor_day } = model.params;
  // 面板开着时整表可能已重载（保存后）：按键回到当前 model 取块与行，不显示打开那一刻的旧对象
  const target = fresh(model, t0);
  // 只有落到一个 msku 时才有一份速率依据可展开
  const row = target.kind === 'msku' ? target.row
    : target.kind === 'store' && target.block.mskus.length === 1 ? target.block.mskus[0]! : null;
  const sb = row?.cells.find((c) => c.period === target.period)?.system_basis;
  return (
    <>
      <div className="fb" data-testid="fb-data">
        {target.kind === 'msku' && <MskuBody model={model} row={target.row} period={target.period} />}
        {target.kind === 'store' && <StoreBody model={model} block={target.block} period={target.period} />}
        {target.kind === 'sku' && <SkuBody model={model} sku={target.sku} period={target.period} />}
      </div>
      <Fold summary="怎么算的">
        {sb && sb.reason !== 'not_applicable_no_sales_source' && <RateDetail sb={sb} />}
        <p>首月期初 = as_of 当天 FBA 可售 − as_of 次日到首月前一天按预测扣的量 + 这段到货</p>
        <p>as_of 落在本月里：从 as_of 当天可售起算，只扣 as_of 次日到月末那一段期望，本月已过的天数不在账上</p>
        <p>期末 = 期初 − 期望 + 到货（负数照实显示）；下月期初 ≡ 本月期末</p>
        <p>到货日 = 下单月 {anchor_day} 号 + 生产 {make_days} 天 + 货运 {ship_days} 天；各 msku 先补缺口；缺口比这批多就按缺口比例分，比这批少则余量按期望销量比例摊</p>
        <p>预测 = 基线速率 × 当月天数，向上取整；店铺 = Σ msku，货号 = Σ 店铺（无 FBA 的不进库存求和）</p>
        <p>在途 = 生产中 + 货运中 + 已下单未到；FBA 货件未取，不计入</p>
      </Fold>
    </>
  );
}
