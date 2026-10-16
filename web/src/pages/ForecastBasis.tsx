import type { ReactNode } from 'react';
import { Fold } from '../components/Fold';
import type {
  FbaShipmentRef, ForecastArrival, ForecastBridge, MonthInTransit, MonthInTransitItem, ForecastReason, ForecastState, MskuForecast, NoSalesSourceCause, Period, SegmentDetail,
  OnhandParts, StoreForecast, SystemBasis,
} from '../api/types';
import type { QtyValue } from '../components/Qty';
import { demandAt, sumUnits, totalsAt, type GridModel, type MskuCell, type MskuRow, type SkuBlock } from './planGridModel';

/** 点的是哪个指标（FM-D4）：缺省 = 月末库存；`sales` 销量预测；`transit` 当月在途（只有货号行） */
export type BasisMetric = 'stock' | 'sales' | 'transit';
export type BasisTarget =
  | { kind: 'msku'; block: SkuBlock; row: MskuRow; period: Period; metric?: BasisMetric }
  | { kind: 'store'; block: SkuBlock; period: Period; metric?: BasisMetric }
  | { kind: 'sku'; sku: string; period: Period; metric?: BasisMetric };

/** ★ 库存为什么没有数。格子上的 tooltip 与依据面板共用这一份，两处说法不许分叉 */
export const FORECAST_WHY: Record<Exclude<ForecastReason, null>, string> = {
  as_of_inside_month: 'as_of 在本月中，从次日起扣',
  unknown_onhand: '无在仓数',
  unknown_demand: '计划销量待填',
  bridge_rate_unknown: '没有预测速率、首月计划销量也未填，as_of 到首月之间扣不了',
  inbound_unknown: '到货数未知（有店缺口未知，分不出去）',
  not_applicable: '无 FBA',
  elapsed: '月份已过去',
  no_msku: '无 msku',
  fba_inbound_unavailable: 'FBA 货件当天未采到，在途未知',
};

const CAUSE: Record<NoSalesSourceCause, string> = {
  non_amazon_platform: '非亚马逊店铺，没有销量取数源',
  store_absent_from_order_report: '订单报表里没有这个店，没有销量取数源',
};

const md = (iso: string) => iso.slice(5);                       // "2026-09-22" → "09-22"
const n = (v: number | null) => (v === null ? '—' : String(v));
// 后端 units_for_month 先 round 到 9 位再取整（OP-10）：差在 1e-9 以内的就是整数
const isInt = (x: number) => Math.abs(x - Math.round(x)) < 1e-9;
/** 取整前的乘积。★ 位数不够时加位：123.001 印成「123.00 → 向上取整 124」就是屏上自相矛盾 */
function fmtRaw(x: number): string {
  for (let d = 2; d < 9; d++) {
    const s = x.toFixed(d);
    if (!isInt(Number(s))) return s.replace(/(\.\d*?)0+$/, '$1');
  }
  return x.toFixed(9);
}
/** 「= 乘积 → 向上取整 N」；乘积本来就是整数时不说取整 */
function rawTail(raw: number, units: number | null): string {
  return isInt(raw) ? `= ${Math.round(raw)}` : `= ${fmtRaw(raw)} → 向上取整 ${n(units)}`;
}
/** 一段扣减怎么来的（`forecast/calendar.py::segment_detail`）：月总量 × 半月权重 → 向上取整 */
function segLine(s: SegmentDetail): string {
  const terms = [
    s.first_half_days > 0 ? `前半月 ${s.first_half_days} 天 ÷ ${s.first_half_divisor}` : null,
    s.second_half_days > 0 ? `后半月 ${s.second_half_days} 天 ÷ ${s.second_half_divisor}` : null,
  ].filter(Boolean).join(' + ');
  return `${md(s.from)}–${md(s.to)} 扣 ${s.units} = 月总量 ${s.month_total} × (${terms}) ${rawTail(s.raw, s.units)}`;
}
const BRIDGE_BY: Record<ForecastBridge['by'], string> = {
  forecast: '按预测扣', first_month_expected: '按首月计划销量折算（无预测速率）扣', unknown: '扣',
};

function prevMonth(p: Period): Period {
  const [y, m] = p.split('-').map(Number) as [number, number];
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
}

export function basisTitle(t: BasisTarget): string {
  const head = t.metric === 'sales' ? '销量预测 ' : t.metric === 'transit' ? '当月在途 ' : '';
  if (t.kind === 'msku') return `${head || '库存 '}${t.block.seller_name} / ${t.row.seller_sku} / ${t.period}`;
  if (t.kind === 'store') return `${head || '库存 '}${t.block.seller_name} / ${t.block.sku} / ${t.period}`;
  return `${head || '货号 '}${t.sku} / ${t.period}`;
}

// ── 悬停一行组成（FM-D4）。★ 格子 title 与弹窗首行共用这几条，两处说法不许分叉 ────────

const sys = (v: number | null | undefined, na?: boolean) => (na ? '不适用（不计）' : n(v ?? null));

/** 货号销量预测 = Σ 店铺 */
export function salesCompositionSku(model: GridModel, sku: string, period: Period): string {
  return model.blocks.filter((b) => b.sku === sku).map((b) => {
    const st = b.stock.find((x) => x.period === period);
    return `${b.seller_name} ${sys(st?.system, st?.system_not_applicable)}`;
  }).join(' + ');
}

/** 店铺销量预测 = Σ msku */
export function salesCompositionStore(block: SkuBlock, period: Period): string {
  return block.mskus.map((r) => {
    const c = r.cells.find((x) => x.period === period);
    return `${r.seller_sku} ${sys(c?.system_units, c?.system_basis?.reason === 'not_applicable_no_sales_source')}`;
  }).join(' + ');
}

/** msku 销量预测 = 速率 × 天数 */
export function salesCompositionMsku(c: MskuCell | undefined): string {
  const sb = c?.system_basis;
  if (!sb) return '没有预测依据';
  if (sb.reason === 'not_applicable_no_sales_source') return `不适用（${CAUSE[sb.cause]}）`;
  if (sb.base_rate === null) return sb.reason === 'no_sales_in_window' ? '窗口内没有销量' : '没有预测速率';
  return `${sb.base_rate.toFixed(4)} 件/天 × ${sb.days_in_month} 天`;
}

const nextDay = (iso: string) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
};

/** 月末库存 = 期初 − 计划销量 + 到货。as_of 落在本月时起点是「现有（as_of 那天）」，计划销量只扣 as_of 次日起那一段 */
export function stockComposition(opening: number | null, start: number | null, demand: number | null,
                                 inbound: number | null, closing: number | null, asOf?: string): string | null {
  if (closing === null) return null;
  if (opening !== null) return `期初 ${opening} − 计划销量 ${n(demand)} + 到货 ${n(inbound)} = ${closing}`;
  if (start === null) return null;
  return asOf
    ? `现有 ${start}（${md(asOf)}）− 计划销量 ${n(demand)}（${md(nextDay(asOf))} 起）+ 到货 ${n(inbound)} = ${closing}`
    : `现有 ${start} − 计划销量 ${n(demand)} + 到货 ${n(inbound)} = ${closing}`;
}

/** 当月在途 = FBA 货件 + 海外仓 */
export function transitComposition(mit: MonthInTransit | null): string {
  if (!mit) return '这次没取当月在途';
  if (mit.before_as_of) return 'as_of 之前的月份没有在途记录';
  const part = (v: number | null) => (v === null ? '读不到' : String(v));
  return `FBA 货件 ${part(mit.fba)} + 海外仓 ${part(mit.oversea)}`;
}

/** 没有期末时说为什么。★ 只说原因，不给任何数 */
function noClosing(state: ForecastState, reason: ForecastReason, since: Period | null = null): string {
  if (state === 'not_applicable') return '无 FBA，不推演库存';
  if (state !== 'unknown') return reason ? FORECAST_WHY[reason] : '无数据';
  return `未知：${since ? `${since} 起` : ''}${reason ? FORECAST_WHY[reason] : '原因未给'}`;
}

function arrivalLine(a: ForecastArrival): string {
  if (a.kind === 'fba_shipment') return `${md(a.date)} 到货 ${a.units}（${fbaRef(a)}，按结束日）`;
  if (a.units === null) return `${md(a.date)} 到货 —（${a.order_period} 下单，有店缺口未知，分不出去）`;
  return `${md(a.date)} 到货 ${a.units}（${a.order_period} 下单，${a.rule ?? '分法未给'}，这批 ${n(a.pool)}）`;
}

/** 「FBA 货件 X，送达 a–b」—— 到货、周期外、未计入三处同一说法 */
function fbaRef(a: FbaShipmentRef): string {
  const win = a.deliver_from && a.deliver_to ? `，送达 ${md(a.deliver_from)}–${md(a.deliver_to)}`
    : a.deliver_to ? `，送达至 ${md(a.deliver_to)}` : '';
  return `FBA 货件 ${a.shipment_id}${win}`;
}

/** 期初两部分（OP-19 c）。调仓中为 0 时只写可售 —— 不让每格都多一串 */
function onhandText(onhand: number | null, parts?: OnhandParts | null): string {
  return parts && parts.fc_transfers > 0
    ? `现有 ${n(onhand)}（可售 ${parts.fulfillable} + 调仓中 ${parts.fc_transfers}）` : `可售 ${n(onhand)}`;
}

/** partial 月（as_of 在本月里）的起点。★ 不叫「期初」：本月 as_of 及之前的天数不在账上 */
function asOfStartLine(asOf: string, onhand: number | null, month: Period, sigma = '', parts?: OnhandParts | null): string {
  return `as_of ${asOf} ${onhandText(onhand, parts)}（${sigma}本月 ${md(month + '-01')}–${md(asOf)} 已过，不计）`;
}

/** Σ 一组 partial 月的 msku 行。任一缺数 ⇒ null（Σ 不当 0） */
function partialSum(rows: (MskuForecast | undefined)[]) {
  const pick = (g: (f: MskuForecast) => number | null | undefined) => {
    const v = rows.map((f) => (f ? g(f) ?? null : null));
    return v.length === 0 || v.some((x) => x === null) ? null : v.reduce<number>((a, b) => a + (b as number), 0);
  };
  const ful = pick((f) => f.basis.as_of_start?.onhand_parts?.fulfillable);
  const fc = pick((f) => f.basis.as_of_start?.onhand_parts?.fc_transfers);
  return { onhand: pick((f) => f.basis.as_of_start?.onhand_as_of), demand: pick((f) => f.demand),
           inbound: pick((f) => f.inbound_planned),
           parts: ful === null || fc === null ? null : { fulfillable: ful, fc_transfers: fc } };
}

function openingLine(f: MskuForecast, periods: Period[], asOf: string): string {
  if (f.basis.as_of_start) {
    return asOfStartLine(asOf, f.basis.as_of_start.onhand_as_of, f.period, '', f.basis.as_of_start.onhand_parts);
  }
  const i = periods.indexOf(f.period);
  const b = f.basis.bridge;
  if (b) {
    const arr = b.arrival_units ? ` + 到货 ${b.arrival_units}` : '';
    return `期初 ${n(f.opening)}（= ${prevMonth(f.period)} 末；as_of ${asOf} ${onhandText(b.onhand_as_of, b.onhand_parts)}`
      + ` − ${md(b.from)}–${md(b.to)} ${BRIDGE_BY[b.by]} ${n(b.units)}${arr}）`;
  }
  if (i > 0) return `期初 ${n(f.opening)}（= ${periods[i - 1]} 期末）`;
  return `期初 ${n(f.opening)}（= as_of ${asOf} 可售）`;
}

/** 桥接扣减的每一步：速率从哪来 → 每个桥接月的月总量 → 该月那一段按半月权重切、取整（终审 I-4） */
function BridgeDetail({ b }: { b: ForecastBridge }): ReactNode {
  if (b.rate === null) return null;
  const rate = b.rate;
  const fme = b.first_month_expected;
  return (
    <>
      {b.by === 'first_month_expected' && fme
        && <div className="fb-sub">日速率 {rate.toFixed(4)} 件/天 = {fme.period} 计划销量 {fme.units} ÷ {fme.days} 天</div>}
      {b.segments.map((s) => (
        <div key={s.month}>
          <div className="fb-sub">
            {s.month} 月总量 {s.month_total} = {rate.toFixed(4)} 件/天 × {s.month_days} 天 {rawTail(s.month_total_raw, s.month_total)}
          </div>
          <div className="fb-sub">{segLine(s)}</div>
        </div>
      ))}
    </>
  );
}

/** 期初 → − 计划销量 → + 到货 → = 期末 → 下月期初。★ 链是恒等式③④（14 §1.1），每一环都要看得见 */
function MskuChain({ f, periods, asOf, firstStockout, monthExpected }: {
  f: MskuForecast | undefined; periods: Period[]; asOf: string; firstStockout: string | null;
  monthExpected: number | null;
}): ReactNode {
  if (!f) return <p>没有这一月的推演行</p>;
  const b = f.basis.bridge;
  const nxt = periods[periods.indexOf(f.period) + 1];
  return (
    <div className="fb-chain" data-testid="fb-chain">
      {f.closing === null ? (
        <>
          {/* 期末未知时起点照给：as_of 可售是事实，与后面哪一环未知无关 */}
          {(f.opening !== null || f.basis.as_of_start) && <div>{openingLine(f, periods, asOf)}</div>}
          {f.opening !== null && b && <BridgeDetail b={b} />}
          <div data-testid="fb-no-closing">{noClosing(f.state, f.reason, f.unknown_since)}</div>
        </>
      ) : (
        <>
          <div>{openingLine(f, periods, asOf)}</div>
          {b && <BridgeDetail b={b} />}
          {b?.arrivals.map((a) => <div key={`b${a.date}${a.order_period}`} className="fb-sub">{arrivalLine(a)}</div>)}
          {b?.stockout_date && <div className="fb-sub">as_of 到首月之间断货 {b.stockout_date}</div>}
          {f.state === 'partial'
            // ★ 这里的数只是 as_of 次日到月末那一段，不是格子上的期望 —— 标出来，否则链对不上格子
            ? <>
                <div>− 本月 as_of 后计划销量 {n(f.demand)}（{md(f.basis.as_of_start?.from ?? asOf)} 起，全月计划销量 {n(monthExpected)} 的剩余部分）</div>
                {f.basis.as_of_start?.segment && <div className="fb-sub">{segLine(f.basis.as_of_start.segment)}</div>}
              </>
            : <div>− 计划销量 {n(f.demand)}</div>}
          <div>+ 到货 {n(f.inbound_planned)}</div>
          {f.basis.arrivals.map((a) => <div key={`a${a.date}${a.order_period}`} className="fb-sub">{arrivalLine(a)}</div>)}
          <div>= 期末 {f.closing}{nxt ? `　→ ${nxt} 期初` : ''}</div>
        </>
      )}
      {f.stockout_date && <div>本月断货 {f.stockout_date}</div>}
      {firstStockout && <div>首次断货 {firstStockout}</div>}
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
  const product = sb.units_raw === null ? '' : ` ${rawTail(sb.units_raw, c.system_units)}`;
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
        速率 {f4(sb.base_rate)} = (n × 收缩前 + {k.shrink_prior} × 目标窗日均) ÷ (n + {k.shrink_prior})
        = ({sb.n_eff.toFixed(2)} × {f4(sb.raw_rate)} + {k.shrink_prior} × {f4(sb.target_rate)}) ÷ {(sb.n_eff + k.shrink_prior).toFixed(2)}
      </p>
      <p>
        收缩前 {f4(sb.raw_rate)}：估计窗 {sb.window.from} ~ {sb.window.until} 的 {k.window_days} 天日销，剔断货日，
        {sb.winsor_cap !== null ? `截到非零日 p${Math.round(k.winsor_q * 100)}（${sb.winsor_cap}）`
          : k.winsor_min_nonzero !== undefined ? `非零日不足 ${k.winsor_min_nonzero} 天不削峰` : '非零日太少不削峰'}，
        半衰期 {k.halflife_days} 天指数加权；n = 权重和 {sb.n_eff.toFixed(2)}
      </p>
      <p>
        {/* ★ 按实际算法命名：天数从首次有销量起算、最多 {k.target_window_days}，不是「近 90 天均值」（OP-3） */}
        目标窗日均 {f4(sb.target_rate)} = 目标窗 {sb.target_window.from} ~ {sb.target_window.until} 的销量
        {sb.target_units != null && sb.target_days != null ? ` ${sb.target_units} ÷ ${sb.target_days} 天` : ' ÷ 天数'}
        （天数自首次有销量{sb.first_seen ? ` ${sb.first_seen} ` : ''}起算，最多 {k.target_window_days}）
      </p>
      <p>估计锚点 {sb.estimation_anchor} = as_of {sb.as_of} − 禁言 {sb.embargo_days} 天</p>
    </>
  );
}

/** partial 月的 Σ 链：店铺 / 货号的 opening 是 null、期望是全月 —— 起点与期望都改取 Σ msku 的那一段 */
function partialChain(rows: (MskuForecast | undefined)[], fullMonth: QtyValue, closing: number, asOf: string,
                      month: Period, nxt: Period | undefined): string {
  const p = partialSum(rows);
  const full = fullMonth.kind === 'num' ? fullMonth.value : null;
  return `${asOfStartLine(asOf, p.onhand, month, 'Σ msku，', p.parts)} − 本月 as_of 后计划销量 ${n(p.demand)}（全月计划销量 ${n(full)} 的剩余部分）`
    + ` + 到货 ${n(p.inbound)} = 期末 ${closing}`
    + (nxt ? `　→ ${nxt} 期初` : '');
}

function storeChain(s: StoreForecast | undefined, block: SkuBlock, periods: Period[], asOf: string): string {
  if (!s) return '没有这一月的店铺行';
  if (s.closing === null) return noClosing(s.state, s.reason);
  const nxt = periods[periods.indexOf(s.period) + 1];
  if (s.state === 'partial') {
    return partialChain(block.mskus.map((r) => r.forecast.find((f) => f.period === s.period)),
      demandAt(block, s.period), s.closing, asOf, s.period, nxt);
  }
  return `期初 ${n(s.opening)} − 计划销量 ${n(s.demand)} + 到货 ${n(s.inbound_planned)} = 期末 ${s.closing}`
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
  unknown_onhand: '无在仓数', fba_overdue: '送达时段已过未签收', fba_no_eta: '没有送达时段',
};

function SkuBody({ model, sku, period }: { model: GridModel; sku: string; period: Period }) {
  const t = totalsAt(model, sku, period);
  if (!t) return <p>没有这一月的货号行</p>;
  const pl = t.pipeline;
  const nxt = model.periods[model.periods.indexOf(period) + 1];
  const fba = model.blocks.filter((b) => b.sku === sku && b.has_fba);   // 只有它们进库存求和
  const chain = t.closing === null
    ? (t.reason_from
      ? `${t.reason_from.seller_sku}：${noClosing(t.state, t.reason)}` : noClosing(t.state, t.reason))
    : t.state === 'partial'
      ? partialChain(fba.flatMap((b) => b.mskus.map((r) => r.forecast.find((f) => f.period === period))),
        sumUnits(fba.map((b) => demandAt(b, period)).map((q) => (q.kind === 'num' ? q.value : null))),
        t.closing, model.params.as_of, period, nxt)
      : `期初 ${n(t.opening)} − 计划销量 ${n(t.stock_demand)} + 到货 ${n(t.inbound_planned)} = 期末 ${t.closing}`
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
        <div>
          采购管道 {n(t.in_transit)}（不计入当月在途）= 生产中 {n(pl.making)} + 货运中 {n(pl.shipping)} + 已下单未到 {pl.ordered_open}
          {pl.ordered_open_overdue > 0 && `（其中已逾期 ${pl.ordered_open_overdue}）`}
        </div>
        {/* ★ period 是采购单的预计到货月（ETA），不是下单月；逾期的不改日期，照标（OQ-6） */}
        {pl.ordered_open_sources.map((o) => (
          <div key={`${o.ref}/${o.period}`} className="fb-sub">
            {o.ref} {o.units}（预计 {o.period} 到货{o.overdue ? '，已逾期' : ''}）
          </div>
        ))}
        <div className="fb-sub">{pl.fba_inbound === null ? 'FBA 货件当天未采到，月末未知'
          : `推演期内另有 FBA 货件在途 ${pl.fba_inbound} 件，按到货日计入月末`}</div>
      </div>
      {t.unfilled_purchase.length > 0 && <div>采购未填：{t.unfilled_purchase.join('、')}</div>}
      {(beyond.length > 0 || unalloc.length > 0) && (
        <ul className="fb-list">
          {beyond.map((b) => (b.kind === 'fba_shipment'
            ? <li key={`bhf${b.shipment_id}${b.seller_sku}`}>FBA 货件 {b.shipment_id} {b.units} 件 → {b.arrive} 到货（周期外）</li>
            : (
              <li key={`bh${b.order_period}`}>
                {b.order_period} 下单 {b.units} 件 → {b.arrive} 到货（{b.reason === 'arrives_after_horizon' ? '周期外' : '早于 as_of'}）
              </li>
            )))}
          {unalloc.map((u) => (u.kind === 'fba_shipment'
            ? <li key={`uaf${u.shipment_id}${u.seller_sku}`}>未计入：FBA 货件 {u.shipment_id} {u.units} 件（{u.seller_sku}，{UNALLOCATED_WHY[u.reason] ?? u.reason}）</li>
            : (
            <li key={`ua${u.order_period}${u.scope === 'msku' ? u.seller_sku + u.sid : ''}`}>
              {u.arrive} 到货 {n(u.units)} 件{u.scope === 'msku' ? `分给 ${u.seller_sku} 但推演不了` : '没分出去'}（{UNALLOCATED_WHY[u.reason] ?? u.reason}）
            </li>
            )))}
        </ul>
      )}
    </>
  );
}

/** 销量预测的成分（FM-D4）：货号 = Σ 店铺 → 店铺 = Σ msku → msku = 速率 × 天数 */
function SalesBody({ model, target }: { model: GridModel; target: BasisTarget }) {
  const p = target.period;
  if (target.kind === 'msku') {
    return <div className="fb-fc">{forecastLine(target.row.cells.find((c) => c.period === p))}</div>;
  }
  const blocks = target.kind === 'store' ? [target.block] : model.blocks.filter((b) => b.sku === target.sku);
  const t = target.kind === 'sku' ? totalsAt(model, target.sku, p) : undefined;
  const head = target.kind === 'sku'
    ? `销量预测 ${t?.system_not_applicable ? '不适用' : n(t?.system ?? null)} = ${salesCompositionSku(model, target.sku, p)}`
    : `销量预测 ${sys(target.block.stock.find((x) => x.period === p)?.system)} = ${salesCompositionStore(target.block, p)}`;
  return (
    <>
      <div className="fb-chain" data-testid="fb-chain"><div>{head}</div></div>
      <ul className="fb-list">
        {blocks.map((b) => {
          const st = b.stock.find((x) => x.period === p);
          return (
            <li key={b.sid}>
              {target.kind === 'sku' && <div>{b.seller_name} {sys(st?.system, st?.system_not_applicable)} = {salesCompositionStore(b, p)}</div>}
              {b.mskus.map((r) => (
                <div key={r.seller_sku} className="fb-sub">{r.seller_sku}：{forecastLine(r.cells.find((c) => c.period === p))}</div>
              ))}
            </li>
          );
        })}
      </ul>
    </>
  );
}

const fmtSeen = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

function transitItemLine(x: MonthInTransitItem, model: GridModel): string {
  if (x.source === 'fba') {
    const shop = model.blocks.find((b) => b.sid === x.sid)?.seller_name ?? x.sid ?? '';
    const win = x.deliver_from && x.eta ? `送达 ${md(x.deliver_from)}–${md(x.eta)}` : x.eta ? `送达至 ${md(x.eta)}` : '没有送达时段';
    const only = x.current_only ? (x.eta ? '，已过送达时段未签收，只计本月' : '，只计本月') : '';
    return `FBA 货件 ${x.ref} ${x.units} 件（${shop} ${x.msku ?? ''}，${win}${only}）`;
  }
  const sent = x.shipped_at ? `${md(x.shipped_at)} 发出，` : '';
  const eta = x.eta ? `预计 ${md(x.eta)} 到达` : '没有预计到达日';
  const only = x.current_only ? (x.eta ? '，已过预计到达未收完，只计本月' : '，只计本月') : '';
  return `海外仓备货单 ${x.ref} ${x.units} 件（${sent}${eta}${only}）`;
}

/** 当月在途的成分（FM-D3/D4）：按来源逐条 + 采集 / 实查时刻；采购管道另列、不计入（FM-Q1） */
function TransitBody({ model, sku, period }: { model: GridModel; sku: string; period: Period }) {
  const mit = model.transit.find((r) => r.sku === sku)?.cells.find((c) => c.period === period)?.mit ?? null;
  const t = totalsAt(model, sku, period);
  const notes = model.sourceNotes;
  const why = mit ? [mit.fba === null ? 'FBA 货件当天未采到' : null,
    mit.oversea === null ? `海外仓读不到：${notes?.oversea_in_transit_error ?? '原因未知'}` : null].filter(Boolean).join('；') : '';
  const head = !mit ? '当月在途 —（这次没取）'
    : mit.before_as_of ? '当月在途 —（as_of 之前的月份没有在途记录）'
      : mit.total === null ? `当月在途 —（${why}）` : `当月在途 ${mit.total} = ${transitComposition(mit)}`;
  const noSku = (notes?.dropped?.['fba_inbound'] as { items_without_sku?: { units: number } } | undefined)
    ?.items_without_sku?.units ?? 0;
  const fba = mit?.items.filter((x) => x.source === 'fba') ?? [];
  const ows = mit?.items.filter((x) => x.source === 'oversea') ?? [];
  return (
    <>
      <div className="fb-chain" data-testid="fb-chain"><div>{head}</div></div>
      {mit && (
        <ul className="fb-list">
          {mit.fba === null ? <li>FBA 货件：当天未采到</li> : fba.length === 0 ? <li>FBA 货件：没有在途</li>
            : fba.map((x, i) => <li key={`f${i}`}>{transitItemLine(x, model)}</li>)}
          {mit.oversea === null ? <li>海外仓：读不到</li> : ows.length === 0 ? <li>海外仓：没有在途</li>
            : ows.map((x, i) => <li key={`o${i}`}>{transitItemLine(x, model)}</li>)}
        </ul>
      )}
      <div className="fb-sub">
        FBA 货件：采集于 {notes?.fba_inbound_as_of ?? model.params.as_of}（每日采集）
        {notes?.oversea_in_transit_at && <>；海外仓：领星实查于 {fmtSeen(notes.oversea_in_transit_at)}</>}
      </div>
      {noSku > 0 && <div className="fb-sub">另有 {noSku} 件 FBA 货件明细没有货号，未计入任何货号的当月在途</div>}
      {t && (
        <div className="fb-chain">
          <div>
            采购管道 {n(t.in_transit)}（不计入当月在途）= 生产中 {n(t.pipeline.making)} + 货运中 {n(t.pipeline.shipping)} + 已下单未到 {t.pipeline.ordered_open}
          </div>
        </div>
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
        {target.metric === 'sales' && <SalesBody model={model} target={target} />}
        {target.metric === 'transit' && target.kind === 'sku' && <TransitBody model={model} sku={target.sku} period={target.period} />}
        {(!target.metric || target.metric === 'stock') && (
          <>
            {target.kind === 'msku' && <MskuBody model={model} row={target.row} period={target.period} />}
            {target.kind === 'store' && <StoreBody model={model} block={target.block} period={target.period} />}
            {target.kind === 'sku' && <SkuBody model={model} sku={target.sku} period={target.period} />}
          </>
        )}
      </div>
      <Fold summary="怎么算的">
        {sb && sb.reason !== 'not_applicable_no_sales_source' && <RateDetail sb={sb} />}
        <p>首月期初 = as_of 当天 FBA 可售 − as_of 次日到首月前一天按预测扣的量 + 这段到货；没有预测速率时按首月计划销量 ÷ 当月天数折成日速率</p>
        <p>一段的扣减 = 月总量 × (前半月天数 ÷ 30 + 后半月天数 ÷ 2×(月长 − 15))，向上取整：前后半月各占月总量一半</p>
        <p>as_of 落在本月里：从 as_of 当天可售起算，只扣 as_of 次日到月末那一段计划销量，本月已过的天数不在账上</p>
        <p>期末 = 期初 − 计划销量 + 到货（负数照实显示）；下月期初 ≡ 本月期末</p>
        <p>到货日 = 下单月 {anchor_day} 号 + 生产 {make_days} 天 + 货运 {ship_days} 天；各 msku 先补缺口；缺口比这批多就按缺口比例分，比这批少则余量按计划销量比例摊</p>
        <p>预测 = 基线速率 × 当月天数，向上取整；店铺 = Σ msku，货号 = Σ 店铺（无 FBA 的不进库存求和）</p>
        <p>当月在途 = 当月任一天在路上的货（到达那天所在月也算）：已发出未签收的 FBA 货件（按送达时段结束日）+ 已发未收的海外仓备货单（按预计到达，领星实查）；到达日未知或已过还没收完的只计 as_of 所在月</p>
        <p>采购管道 = 生产中 + 货运中 + 已下单未到（不计入当月在途）；已发出的 FBA 货件按亚马逊送达时段结束日计入月末（不在在途里重复计），送达时段已过未签收或没有送达时段的不计、单列；已下单未到的预计到货月早于 {model.params.in_transit_as_of.slice(0, 7)} 即已逾期</p>
      </Fold>
    </>
  );
}
