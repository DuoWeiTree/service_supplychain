import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GridResponse } from '../api/types';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_SELLERS } from './__fixtures__/labelGrid';

function renderSheet(grid: GridResponse = LABEL_GRID) {
  const model = buildGridModel(grid, LABEL_SELLERS);
  expect(model.orphans).toEqual([]);            // ★ 构造的网格自身必须 Σ 一致
  render(<PlanSheet model={model} pending={new Set()} inputErr={{}} onDemand={vi.fn()}
                    onPurchase={vi.fn()} onRemoveSku={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

describe('spec §5：三种行各显示什么', () => {
  it('货号行：主数采购，副数 预测 · 库存 · 在途（带文字标签）', () => {
    renderSheet();
    const row = screen.getByTestId('purchase-row-SKU-1');
    // ★ 沃尔玛店预测不适用、不进 Σ：货号预测 = 11072 的 145，不是 —
    expect(within(row).getByTestId('sku-system-SKU-1-2026-10')).toHaveTextContent('145');
    expect(within(row).getByTestId('sku-stock-SKU-1-2026-10')).toHaveTextContent('166');
    // 在途 = 生产中 + 货运中 + 已下单：10 月 200 + 0 + 90；11 月 80（11 月单在产）+ 200（10 月单在船）+ 90
    //（已下单 90 = 已逾期的 40 + 10 月预计到的 50，逾期的照计，终审 I-2）
    expect(within(row).getByTestId('transit-SKU-1-2026-10')).toHaveTextContent('290');
    expect(within(row).getByTestId('transit-SKU-1-2026-11')).toHaveTextContent('370');
    const cell = within(row).getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')!;
    expect(cell).toHaveTextContent(/预测.*库存.*在途/);
  });

  it('★ 负责人 09-29：多 msku 的店铺行只读 —— 它是 Σ，不是一个可以填的数', () => {
    renderSheet();
    // 11072 挂着两个 msku（MSKU-A / MSKU-B）⇒ 这一行原则上不该有人去填
    const multi = screen.getByTestId('block-11072-SKU-1');
    const roCell = within(multi).getByTestId('sku-cell-2026-10');
    expect(roCell.className).toContain('is-ro');
    expect(roCell.getAttribute('role')).not.toBe('gridcell');   // ★ 可编辑格才是 gridcell
    // 90001 只有一个 msku（MSKU-W）⇒ 按 spec §5「单 msku 直接填」，这一行必须可填
    const single = screen.getByTestId('block-90001-SKU-1');
    const rwCell = within(single).getByTestId('sku-cell-2026-10');
    expect(rwCell.getAttribute('role')).toBe('gridcell');
    expect(rwCell.className).not.toContain('is-ro');
    // ★ 「Σ 遇到任一 msku 未知就显示待填、绝不凑成 0」这条**不在这里钉**：三份冻结网格
    //   里没有未知的需求格，要在 DOM 层验它得新造一份 fixture，而那三份是 Python 侧
    //   test_label_grid_fixture.py 逐字守着的。规则本身钉在模型层：
    //   planGridModel.test.ts 的 sumUnits([180, null, 210]) → unknown 与 demandAt 未知那两条。
  });

  it('店铺行：期望 Σ + 预测 · 库存', () => {
    renderSheet();
    const block = screen.getByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('105');
    expect(within(cell).getByTestId('store-system-11072-SKU-1-2026-10')).toHaveTextContent('145');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('166');
    expect(cell).toHaveTextContent(/预测.*库存/);
  });

  it('msku 行：期望 + 预测 · 库存', () => {
    renderSheet();
    expect(screen.getByTestId('msku-system-MSKU-A-11072-2026-10')).toHaveTextContent('135');
    expect(screen.getByTestId('msku-stock-MSKU-A-11072-2026-10')).toHaveTextContent('161');
    expect(screen.getByTestId('msku-stock-MSKU-B-11072-2026-11')).toHaveTextContent('0');
    const cell = screen.getByTestId('cell-MSKU-A-11072-2026-10');
    expect(cell).toHaveTextContent(/预测.*库存/);
  });

  it('★ 无 FBA 店铺：库存写「无 FBA」，不是 0 也不是 —；预测标签照样在', () => {
    renderSheet();
    const block = screen.getByTestId('block-90001-SKU-1');
    expect(within(block).getAllByTestId('closing-na')[0]).toHaveTextContent('无 FBA');
    expect(within(block).getByTestId('sku-cell-2026-10')).toHaveTextContent(/预测.*库存/);
  });

  it('OP-14：在途的说明「不含 FBA 货件（未取）」挂在在途上，不常驻屏上', () => {
    renderSheet();
    const transit = screen.getByTestId('transit-SKU-1-2026-10');
    expect(transit.closest('[title]')!.getAttribute('title')).toBe('不含 FBA 货件（未取）');
    expect(screen.getByTestId('purchase-row-SKU-1')).not.toHaveTextContent('不含 FBA 货件');
  });

  it('★ 断货 / 偏低是「库存」后面的状态词，标签「库存」不被它顶掉', () => {
    renderSheet();
    const block = screen.getByTestId('block-11072-SKU-1');
    // 期末 71 ≥ 50：不带状态词
    expect(within(block).getByTestId('sku-cell-2026-11')).not.toHaveTextContent(/断货|偏低/);
    const low = structuredClone(LABEL_GRID);
    // 11 月 MSKU-B 期末 0 → 店铺期末 = A 的期末。A 期望 90 → 151 把期末压到 161 − 151 = 10，Σ 同步改
    low.forecast.find((f) => f.seller_sku === 'MSKU-A' && f.period === '2026-11')!.closing = 10;
    low.forecast.find((f) => f.seller_sku === 'MSKU-A' && f.period === '2026-11')!.demand = 151;
    low.demand.find((d) => d.seller_sku === 'MSKU-A' && d.period === '2026-11')!.expected_units = 151;
    low.demand.find((d) => d.seller_sku === 'MSKU-A' && d.period === '2026-11')!.effective_units = 151;
    const st = low.forecast_store.find((s) => s.sid === '11072' && s.period === '2026-11')!;
    st.closing = 10; st.demand = 156;
    cleanup();
    renderSheet(low);
    const cell = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-11');
    expect(cell).toHaveTextContent(/库存\s*10\s*偏低/);
  });

  it('★ 库存未知不是一根光秃秃的 —：说出为什么', () => {
    const g = structuredClone(LABEL_GRID);
    for (const s of g.forecast_store) if (s.sid === '11072') {
      s.state = 'unknown'; s.reason = 'unknown_onhand'; s.closing = null; s.opening = null;
    }
    for (const f of g.forecast) if (f.sid === '11072') {
      f.state = 'unknown'; f.reason = 'unknown_onhand'; f.closing = null; f.opening = null;
    }
    for (const k of g.forecast_sku) {
      k.state = 'unknown'; k.reason = 'unknown_onhand'; k.closing = null;
      k.reason_from = { seller_sku: 'MSKU-A', sid: '11072' };
    }
    renderSheet(g);
    const store = within(within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10')).getByTestId('closing-sku');
    expect(store).toHaveTextContent('—');
    expect(store.closest('[title]')!.getAttribute('title')).toBe('无在仓数');
    const msku = screen.getByTestId('msku-stock-MSKU-A-11072-2026-10');
    expect(msku.closest('[title]')!.getAttribute('title')).toBe('无在仓数');
    const sku = screen.getByTestId('sku-stock-SKU-1-2026-10');
    expect(sku.closest('[title]')!.getAttribute('title')).toBe('MSKU-A：无在仓数');
  });

  it('★ 图例：库存含计划采购到货，并回显提前期（不再说「未计本计划采购」）', () => {
    renderSheet();
    expect(screen.getByText('库存含计划采购到货（生产 30 天 + 货运 60 天）')).toBeInTheDocument();
    expect(screen.queryByText('未计本计划采购')).toBeNull();
  });

  it('★ 没有销量取数源的店：预测写「不适用」；旧认领没种依据的仍是 —（OP-8）', () => {
    renderSheet();
    expect(screen.getByTestId('store-system-90001-SKU-1-2026-10')).toHaveTextContent('不适用');

    const old = structuredClone(LABEL_GRID);
    for (const d of old.demand) if (d.sid === '90001') d.system_basis = null;
    for (const s of old.forecast_store) if (s.sid === '90001') s.system_not_applicable = false;
    for (const k of old.forecast_sku) k.system = null;
    cleanup();
    renderSheet(old);
    expect(screen.getByTestId('store-system-90001-SKU-1-2026-10')).toHaveTextContent('—');
    expect(screen.getByTestId('store-system-90001-SKU-1-2026-10')).not.toHaveTextContent('不适用');
    expect(screen.getByTestId('sku-system-SKU-1-2026-10')).toHaveTextContent('—');
  });

  it('★ msku 行：认领时记下无销量源 → 预测「不适用」', () => {
    const g = structuredClone(LABEL_GRID);
    for (const d of g.demand) if (d.seller_sku === 'MSKU-B') {
      d.system_units = null;
      d.system_basis = { reason: 'not_applicable_no_sales_source', cause: 'store_absent_from_order_report',
                         days_in_month: 31, units_raw: null };
    }
    renderSheet(g);
    expect(screen.getByTestId('msku-system-MSKU-B-11072-2026-10')).toHaveTextContent('不适用');
    expect(screen.getByTestId('msku-system-MSKU-A-11072-2026-10')).toHaveTextContent('135');
  });

  it('★ 紧凑：货号行副数用单字标签 预 / 存 / 途，全称在 title 上', async () => {
    renderSheet();
    const cell = () => screen.getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')!;
    expect(cell()).toHaveTextContent(/预测.*库存.*在途/);
    await userEvent.click(screen.getByRole('button', { name: '紧凑' }));
    expect(cell()).toHaveTextContent(/预\s*145.*存\s*166.*途\s*290/);
    expect(cell()).not.toHaveTextContent(/预测|库存|在途/);
    for (const [short, full] of [['预', '预测'], ['存', '库存'], ['途', '在途']] as const) {
      expect(within(cell() as HTMLElement).getByText(short)).toHaveAttribute('title', full);
    }
  });
});
