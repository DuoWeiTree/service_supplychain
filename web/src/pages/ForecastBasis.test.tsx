import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GridResponse } from '../api/types';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_GRID_LONG, LABEL_GRID_PARTIAL, LABEL_SELLERS } from './__fixtures__/labelGrid';

function renderSheet(grid: GridResponse = LABEL_GRID, orphansOk = false) {
  const model = buildGridModel(grid, LABEL_SELLERS);
  if (!orphansOk) expect(model.orphans).toEqual([]);
  render(<PlanSheet model={model} pending={new Set()} inputErr={{}} onDemand={vi.fn()}
                    onPurchase={vi.fn()} onRemoveSku={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

async function open(name: string) {
  await userEvent.click(screen.getByRole('button', { name }));
  return screen.getByRole('dialog');
}

/** 数据区（不含「怎么算的」折叠区 —— 那里的公式本来就写着「期初」「期末」） */
const data = (d: HTMLElement) => within(d).getByTestId('fb-data');

// 数字全部来自 labelGrid*.json（tests/test_label_grid_fixture.py 用 forecast_payload 跑出来的）
describe('spec §5：点开一格看依据', () => {
  it('msku 首月：期初来自 as_of 可售 − 桥接段按预测扣，再 − 期望 + 到货 = 期末 → 下月期初', async () => {
    renderSheet();
    const d = await open('依据 MSKU-A 2026-10');
    expect(d).toHaveTextContent('库存 A4Pet-US / MSKU-A / 2026-10');
    expect(d).toHaveTextContent('期初 261（= 2026-09 末；as_of 2026-09-21 可售 300 − 09-22–09-30 按预测扣 39）');
    // ★ 终审 I-4：桥接扣减在面板上复算得出 —— 月总量从哪来、半月天数与除数、取整各一步
    expect(d).toHaveTextContent('2026-09 月总量 130 = 4.3251 件/天 × 30 天 = 129.75 → 向上取整 130');
    // 130 × 9 ÷ 30 正好 39：不说「向上取整」
    expect(d).toHaveTextContent('09-22–09-30 扣 39 = 月总量 130 × (后半月 9 天 ÷ 30) = 39');
    expect(d).not.toHaveTextContent(/= 39\.0+|39 → 向上取整/);
    expect(d).toHaveTextContent('− 期望 100');
    expect(d).toHaveTextContent('+ 到货 0');
    expect(d).toHaveTextContent('= 期末 161');
    expect(d).toHaveTextContent('→ 2026-11 期初');
    expect(d).toHaveTextContent('预测 135 = 4.3251 件/天 × 31 天 = 134.08 → 向上取整 135（窗口 07-21 ~ 09-14，禁言 7 天，剔断货 0 天）');
  });

  it('次月期初 ≡ 上月期末：链逐月相接', async () => {
    renderSheet();
    const d = await open('依据 MSKU-A 2026-11');
    expect(d).toHaveTextContent('期初 161（= 2026-10 期末）');
    expect(d).toHaveTextContent('= 期末 71');
    expect(d).not.toHaveTextContent(/→ \d{4}-\d{2} 期初/);   // 最后一个月没有下月
  });

  it('到货逐笔说清怎么分的（14 §3.2 依据必须说实话）；断货日照报', async () => {
    renderSheet(LABEL_GRID_LONG);
    const d = await open('依据 MSKU-A 2027-01');
    expect(d).toHaveTextContent('期初 -39（= 2026-12 期末）');
    expect(d).toHaveTextContent('+ 到货 189');
    expect(d).toHaveTextContent('01-13 到货 189（2026-10 下单，缺口 159 补齐，余量按期望销量再摊 30，这批 200）');
    expect(d).toHaveTextContent('= 期末 30');
    expect(d).toHaveTextContent('本月断货 2027-01-01');
    expect(d).toHaveTextContent('首次断货 2026-12-20');
  });

  it('货号行：库存链 + 在途拆成 生产中 / 货运中 / 已下单未到，FBA 货件写「未取」；周期外逐笔列出', async () => {
    renderSheet();
    const d = await open('依据 SKU-1 2026-10');
    expect(d).toHaveTextContent('货号 SKU-1 / 2026-10');
    expect(d).toHaveTextContent('期初 271 − 期望 105 + 到货 0 = 期末 166');
    expect(d).toHaveTextContent('不含 1 家无 FBA 店铺');
    expect(d).toHaveTextContent('在途 290 = 生产中 200 + 货运中 0 + 已下单未到 90（其中已逾期 40）');
    expect(d).toHaveTextContent('FBA 货件 未取');
    // ★ 终审 I-2：period 是预计到货月（ETA），不是下单月；逾期的单独标出（OQ-6）
    expect(d).toHaveTextContent('PO-2026-0701 40（预计 2026-07 到货，已逾期）');
    expect(d).toHaveTextContent('PO-2026-0918 50（预计 2026-10 到货）');
    expect(d).not.toHaveTextContent(/PO-2026-09\d+ \d+（[^）]*下单/);
    expect(d).toHaveTextContent('2026-10 下单 200 件 → 2027-01-13 到货（周期外）');
    expect(d).toHaveTextContent('2026-11 下单 80 件 → 2027-02-13 到货（周期外）');
  });

  it('货号行：采购未填的月份点名', async () => {
    renderSheet(LABEL_GRID_LONG);
    const d = await open('依据 SKU-1 2027-01');
    expect(d).toHaveTextContent('采购未填：2026-12、2027-01');
  });

  it('店铺行：Σ msku 的链，逐个 msku 列期末；单 msku 的店铺直接给该 msku 的链', async () => {
    renderSheet();
    const d = await open('依据 A4Pet-US SKU-1 2026-10');
    expect(d).toHaveTextContent('库存 A4Pet-US / SKU-1 / 2026-10');
    expect(d).toHaveTextContent('期初 271 − 期望 105 + 到货 0 = 期末 166');
    expect(d).toHaveTextContent('MSKU-A：期末 161');
    expect(d).toHaveTextContent('MSKU-B：期末 5');
  });

  it('无 FBA 店铺：说「无 FBA」，不给数；预测不适用给成因', async () => {
    renderSheet();
    const d = await open('依据 A4Pet-WM SKU-1 2026-10');
    expect(d).toHaveTextContent('无 FBA，不推演库存');
    expect(d).toHaveTextContent('预测 不适用（非亚马逊店铺，没有销量取数源）');
    expect(data(d)).not.toHaveTextContent('期末');
  });

  it('★ 说明进折叠区，默认收起', async () => {
    renderSheet();
    const d = await open('依据 MSKU-A 2026-10');
    const details = within(d).getByText('怎么算的').closest('details')!;
    expect(details).not.toHaveAttribute('open');
  });

  it('未知给原因，不给数', async () => {
    const g = structuredClone(LABEL_GRID);
    const r = g.forecast.find((f) => f.seller_sku === 'MSKU-B' && f.period === '2026-11')!;
    Object.assign(r, { state: 'unknown', reason: 'unknown_demand', unknown_since: '2026-11', closing: null });
    renderSheet(g, true);
    const d = await open('依据 MSKU-B 2026-11');
    expect(d).toHaveTextContent('未知：2026-11 起期望销量待填');
    expect(data(d)).not.toHaveTextContent('= 期末');   // 期初照给（它有数），期末不编
  });

  it('预测：旧认领没有依据 / 窗口内没有销量 —— 说原因，不编数', async () => {
    const g = structuredClone(LABEL_GRID);
    g.demand.find((c) => c.seller_sku === 'MSKU-A' && c.period === '2026-10')!.system_basis = null;
    const b = g.demand.find((c) => c.seller_sku === 'MSKU-B' && c.period === '2026-10')!;
    b.system_units = null;
    Object.assign(b.system_basis!, { base_rate: null, reason: 'no_sales_in_window', units_raw: null });
    renderSheet(g, true);
    expect(await open('依据 MSKU-A 2026-10')).toHaveTextContent('预测 —（旧认领无依据，重新添加后可算）');
    await userEvent.keyboard('{Escape}');
    expect(await open('依据 MSKU-B 2026-10')).toHaveTextContent('预测 —（窗口内没有销量）');
  });

  it('★ 点依据不移动格子光标（按钮不触发选中）', async () => {
    renderSheet();
    const before = screen.getAllByRole('gridcell').find((c) => c.getAttribute('aria-selected') === 'true');
    await open('依据 MSKU-A 2026-11');
    const after = screen.getAllByRole('gridcell').find((c) => c.getAttribute('aria-selected') === 'true');
    expect(after).toBe(before);
  });

  it('★ 面板开着时整表重载：显示新 model 的数，不是打开那一刻的旧对象', async () => {
    const props = { pending: new Set<string>(), inputErr: {}, onDemand: vi.fn(), onPurchase: vi.fn(),
                    onRemoveSku: vi.fn(), onRemoveStore: vi.fn(), onRemoveMsku: vi.fn() };
    const { rerender } = render(<PlanSheet model={buildGridModel(LABEL_GRID, LABEL_SELLERS)} {...props} />);
    await open('依据 MSKU-A 2026-11');
    const g = structuredClone(LABEL_GRID);
    const r = g.forecast.find((f) => f.seller_sku === 'MSKU-A' && f.period === '2026-11')!;
    Object.assign(r, { state: 'unknown', reason: 'unknown_demand', unknown_since: '2026-11', closing: null });
    rerender(<PlanSheet model={buildGridModel(g, LABEL_SELLERS)} {...props} />);
    expect(screen.getByRole('dialog')).toHaveTextContent('未知：2026-11 起期望销量待填');
  });

  it('★ as_of 落在本月里：起点是 as_of 可售，期望只扣 as_of 后那一段并标明，链对得上', async () => {
    renderSheet(LABEL_GRID_PARTIAL);
    const d = await open('依据 MSKU-A 2026-10');
    expect(d).toHaveTextContent('as_of 2026-10-10 可售 300（本月 10-01–10-10 已过，不计）');
    expect(d).toHaveTextContent('− 本月 as_of 后期望 67（10-11 起，全月期望 100 的剩余部分）');
    expect(d).toHaveTextContent('10-11–10-31 扣 67 = 月总量 100 × (前半月 5 天 ÷ 30 + 后半月 16 天 ÷ 32) = 66.67 → 向上取整 67');
    expect(d).toHaveTextContent('+ 到货 0');
    expect(d).toHaveTextContent('= 期末 233 → 2026-11 期初');
    expect(data(d)).not.toHaveTextContent(/期初 —|− 期望 /);
    await userEvent.keyboard('{Escape}');
    expect(await open('依据 MSKU-A 2026-11')).toHaveTextContent('期初 233（= 2026-10 期末）');
  });

  it('★ partial 月的店铺 / 货号：起点与期望取 Σ msku 那一段（行上的期望是全月，不能拿来扣）', async () => {
    renderSheet(LABEL_GRID_PARTIAL);
    const line = 'as_of 2026-10-10 可售 313（Σ msku，本月 10-01–10-10 已过，不计） − 本月 as_of 后期望 71（全月期望 105 的剩余部分）'
      + ' + 到货 0 = 期末 242 → 2026-11 期初';
    expect(await open('依据 A4Pet-US SKU-1 2026-10')).toHaveTextContent(line);
    await userEvent.keyboard('{Escape}');
    expect(await open('依据 SKU-1 2026-10')).toHaveTextContent(line);
  });

  it('★ 已过去的月：说「已过去」，库存一个数都不给', async () => {
    renderSheet(LABEL_GRID_PARTIAL);
    for (const name of ['依据 MSKU-A 2026-09', '依据 A4Pet-US SKU-1 2026-09', '依据 SKU-1 2026-09']) {
      const d = await open(name);
      const chain = within(d).getByTestId('fb-chain');
      expect(chain).toHaveTextContent('月份已过去');
      expect(chain).not.toHaveTextContent(/\d/);
      await userEvent.keyboard('{Escape}');
    }
  });

  it('怎么算的：收缩的中间量照 claim 记下的给（n/(n+3) 加权）', async () => {
    renderSheet();
    const d = await open('依据 MSKU-A 2026-10');
    const fold = within(d).getByText('怎么算的').closest('details')!;
    expect(fold).toHaveTextContent('速率 4.3251 = (n × 收缩前 + 3 × 目标窗日均) ÷ (n + 3) = (19.41 × 4.0000 + 3 × 6.4286) ÷ 22.41');
    // ★ 按实际算法命名：360 ÷ 56，不是「90 天均值」
    expect(fold).toHaveTextContent('目标窗日均 6.4286 = 目标窗 2026-06-17 ~ 2026-09-14 的销量 360 ÷ 56 天（天数自首次有销量 2026-07-21 起算，最多 90）');
    expect(fold).not.toHaveTextContent('天均值');
    expect(fold).toHaveTextContent('估计锚点 2026-09-14 = as_of 2026-09-21 − 禁言 7 天');
    expect(fold).toHaveTextContent('截到非零日 p95（4）');
  });

  it('怎么算的：削峰阈值取自依据（不写死）；旧认领缺总量 / 天数时只给公式不编数', async () => {
    const g = structuredClone(LABEL_GRID);
    const sb = g.demand.find((c) => c.seller_sku === 'MSKU-A' && c.period === '2026-10')!.system_basis!;
    if (sb.reason === 'not_applicable_no_sales_source') throw new Error('fixture 变了');
    sb.winsor_cap = null; sb.constants.winsor_min_nonzero = 7;
    delete sb.target_units; delete sb.target_days;
    renderSheet(g, true);
    const fold = within(await open('依据 MSKU-A 2026-10')).getByText('怎么算的').closest('details')!;
    expect(fold).toHaveTextContent('非零日不足 7 天不削峰');
    expect(fold).toHaveTextContent('的销量 ÷ 天数（天数自首次有销量');
  });

  it('partial 月期末未知：as_of 起点照给，再说为什么未知', async () => {
    const g = structuredClone(LABEL_GRID_PARTIAL);
    const r = g.forecast.find((f) => f.seller_sku === 'MSKU-A' && f.period === '2026-10')!;
    Object.assign(r, { state: 'unknown', reason: 'inbound_unknown', unknown_since: '2026-10', closing: null });
    renderSheet(g, true);
    const chain = within(await open('依据 MSKU-A 2026-10')).getByTestId('fb-chain');
    expect(chain).toHaveTextContent('as_of 2026-10-10 可售 300（本月 10-01–10-10 已过，不计）');
    expect(chain).toHaveTextContent('未知：2026-10 起到货数未知');
    expect(chain).not.toHaveTextContent('= 期末');
  });

  it('★ 依据按钮不进 Tab 序；光标格上按 I 打开它的依据', async () => {
    renderSheet();
    for (const b of screen.getAllByRole('button', { name: /^依据 / })) expect(b).toHaveAttribute('tabIndex', '-1');
    // 光标在第一格（货号行 SKU-1 2026-10）
    screen.getByRole('grid').focus();
    await userEvent.keyboard('i');
    expect(screen.getByRole('dialog')).toHaveTextContent('货号 SKU-1 / 2026-10');
    expect(screen.getByText('看依据')).toBeInTheDocument();
  });

  it('★ 预测乘积：整数不说「向上取整」；非整数不许印成 123.00 → 124', async () => {
    const g = structuredClone(LABEL_GRID);
    const cell = g.demand.find((c) => c.seller_sku === 'MSKU-A' && c.period === '2026-10')!;
    const sb = cell.system_basis!;
    if (sb.reason === 'not_applicable_no_sales_source') throw new Error('fixture 变了');
    Object.assign(sb, { units_raw: 123.001 }); cell.system_units = 124;
    renderSheet(g, true);
    let d = await open('依据 MSKU-A 2026-10');
    expect(d).toHaveTextContent('= 123.001 → 向上取整 124');
    expect(d).not.toHaveTextContent('123.00 →');
    cleanup();
    Object.assign(sb, { units_raw: 124 });
    renderSheet(g, true);
    d = await open('依据 MSKU-A 2026-10');
    expect(d).toHaveTextContent('× 31 天 = 124（');
    expect(d).not.toHaveTextContent('向上取整 124');
  });

  it('★ 没有预测速率：桥接段按首月期望折算，面板说清折算用的数（终审 I-5）', async () => {
    const g = structuredClone(LABEL_GRID);
    const r = g.forecast.find((f) => f.seller_sku === 'MSKU-A' && f.period === '2026-10')!;
    // 形状照 forecast/plan_forecast.py 的 first_month_expected 分支：100 ÷ 31 × 30 = 96.77 → 97；× 9/30 = 29.1 → 30
    const seg = r.basis.bridge!.segments[0]!;
    Object.assign(seg, { month_total: 97, month_total_raw: 96.774193548, raw: 29.1, units: 30 });
    Object.assign(r.basis.bridge!, {
      by: 'first_month_expected', rate: 100 / 31, units: 30, month_totals: { '2026-09': 97 },
      first_month_expected: { period: '2026-10', units: 100, days: 31 },
    });
    renderSheet(g, true);
    const d = data(await open('依据 MSKU-A 2026-10'));
    expect(d).toHaveTextContent('09-22–09-30 按首月期望折算（无预测速率）扣 30');
    expect(d).toHaveTextContent('日速率 3.2258 件/天 = 2026-10 期望 100 ÷ 31 天');
    expect(d).toHaveTextContent('2026-09 月总量 97 = 3.2258 件/天 × 30 天 = 96.77 → 向上取整 97');
    expect(d).toHaveTextContent('09-22–09-30 扣 30 = 月总量 97 × (后半月 9 天 ÷ 30) = 29.1 → 向上取整 30');
    expect(d).not.toHaveTextContent('按预测扣');
  });

  it('★ 面板上没有 ` · ` 元串（文案纪律）', async () => {
    renderSheet(LABEL_GRID_LONG);
    for (const name of ['依据 MSKU-A 2027-01', '依据 SKU-1 2027-01', '依据 A4Pet-US SKU-1 2027-01']) {
      const d = await open(name);
      expect(d.textContent).not.toContain(' · ');
      await userEvent.keyboard('{Escape}');
    }
  });
});
