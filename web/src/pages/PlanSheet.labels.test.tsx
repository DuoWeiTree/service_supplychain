import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GridResponse } from '../api/types';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_GRID_PARTIAL, LABEL_SELLERS } from './__fixtures__/labelGrid';

function renderSheet(grid: GridResponse = LABEL_GRID) {
  const model = buildGridModel(grid, LABEL_SELLERS);
  expect(model.orphans).toEqual([]);            // ★ 构造的网格自身必须 Σ 一致
  render(<PlanSheet model={model} pending={new Set()} inputErr={{}} onDemand={vi.fn()}
                    onPurchase={vi.fn()} onRemoveSku={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

describe('spec §5：三种行各显示什么', () => {
  it('货号行：主数采购，副数 预测 · 月末 · 在途（带文字标签）', () => {
    renderSheet();
    const row = screen.getByTestId('purchase-row-SKU-1');
    // ★ 沃尔玛店预测不适用、不进 Σ：货号预测 = 11072 的 145，不是 —
    expect(within(row).getByTestId('sku-system-SKU-1-2026-10')).toHaveTextContent('145');
    expect(within(row).getByTestId('sku-stock-SKU-1-2026-10')).toHaveTextContent('166');
    // FM-D2/D3：当月在途 = FBA 货件 + 海外仓（冻结样例：一张海外仓备货单 40 件，11-05 预计到达 → 10、11 月都在途）
    expect(within(row).getByTestId('transit-SKU-1-2026-10')).toHaveTextContent('40');
    expect(within(row).getByTestId('transit-SKU-1-2026-11')).toHaveTextContent('40');
    const cell = within(row).getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')!;
    expect(cell).toHaveTextContent(/预测.*月末.*在途/);
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
    expect(cell).toHaveTextContent(/预测.*月末/);
  });

  it('msku 行：期望 + 预测 · 库存', () => {
    renderSheet();
    expect(screen.getByTestId('msku-system-MSKU-A-11072-2026-10')).toHaveTextContent('135');
    expect(screen.getByTestId('msku-stock-MSKU-A-11072-2026-10')).toHaveTextContent('161');
    expect(screen.getByTestId('msku-stock-MSKU-B-11072-2026-11')).toHaveTextContent('0');
    const cell = screen.getByTestId('cell-MSKU-A-11072-2026-10');
    expect(cell).toHaveTextContent(/预测.*月末/);
  });

  it('★ 无 FBA 店铺：库存写「无 FBA」，不是 0 也不是 —；预测标签照样在', () => {
    renderSheet();
    const block = screen.getByTestId('block-90001-SKU-1');
    expect(within(block).getAllByTestId('closing-na')[0]).toHaveTextContent('无 FBA');
    expect(within(block).getByTestId('sku-cell-2026-10')).toHaveTextContent(/预测.*月末/);
  });

  it('在途的说明挂在 title 上、不常驻屏上：FBA 货件已计入月末（spec 2026-10-14 推翻 OP-14）', () => {
    renderSheet();
    const transit = screen.getByTestId('transit-SKU-1-2026-10');
    expect(transit.closest('[title]')!.getAttribute('title')).toBe('FBA 货件 0 + 海外仓 40');
    expect(screen.getByTestId('purchase-row-SKU-1')).not.toHaveTextContent('FBA 货件');
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
    expect(cell).toHaveTextContent(/月末库存\s*10\s*偏低/);
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

  it('★ 紧凑：货号行副数用单字标签 预 / 末 / 途，全称在 title 上', async () => {
    renderSheet();
    const cell = () => screen.getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')!;
    expect(cell()).toHaveTextContent(/预测.*月末.*在途/);
    await userEvent.click(screen.getByRole('button', { name: '紧凑' }));
    expect(cell()).toHaveTextContent(/销\s*145.*存\s*166.*途\s*40/);
    expect(cell()).not.toHaveTextContent(/销量预测|月末库存|当月在途/);
    for (const [short, full] of [['销', '销量预测'], ['存', '月末库存'], ['途', '当月在途']] as const) {
      expect(within(cell() as HTMLElement).getByText(short)).toHaveAttribute('title', full);
    }
  });
});

describe('★ OP-19（10-14 负责人投诉「库存 37」对不上领星）：月末预计 ≠ 现在的库存', () => {
  it('标签不写光秃秃的「库存」（FM-D1 起写「月末库存」），title 给组成', () => {
    renderSheet();
    const cell = screen.getByTestId('block-11072-SKU-1');
    expect(within(cell).getByTestId('sku-cell-2026-10')).not.toHaveTextContent(/(?<!月末)库存/);
    expect(within(cell).getAllByTitle(/期初 \d+ − 计划销量 \d+ \+ 到货 \d+ = \d+/).length).toBeGreaterThan(0);
  });

  it('as_of 所在月：店铺行与 msku 行写「现有」= as_of 当天 FBA 可售 + 调仓中，title 拆开并带采集日', () => {
    renderSheet(LABEL_GRID_PARTIAL);
    const store = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10');
    expect(within(store).getByTestId('onhand-11072-SKU-1-2026-10')).toHaveTextContent('313');
    const label = within(store).getByTestId('onhand-11072-SKU-1-2026-10').closest('[title]')!;
    expect(label).toHaveTextContent(/^现有/);
    expect(label).toHaveAttribute('title', expect.stringMatching(/FBA 可售 313 \+ 调仓中 0.*采集于 2026-10-10/));
    expect(screen.getByTestId('msku-onhand-MSKU-A-11072-2026-10')).toHaveTextContent('300');
    expect(screen.queryByTestId('onhand-11072-SKU-1-2026-11')).toBeNull();
  });

  it('as_of 在计划首月之前：首月回显 as_of 可售（桥接段的起点），其余月不写', () => {
    renderSheet();
    expect(screen.getByTestId('onhand-11072-SKU-1-2026-10')).toHaveTextContent('313');
    expect(screen.queryByTestId('onhand-11072-SKU-1-2026-11')).toBeNull();
  });
});


// ── spec 2026-10-15：三指标 销量预测 · 月末库存 · 当月在途（FM-D1/D4/D5）────────────
describe('★ FM：三个指标写全称，悬停给组成', () => {
  it('货号行：销量预测 · 月末库存 · 当月在途；店铺行 / msku 行只有销量预测 · 月末库存', () => {
    renderSheet();
    expect(screen.getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c')).toHaveTextContent(/销量预测.*月末库存.*当月在途/);
    const store = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10');
    expect(store).toHaveTextContent(/销量预测.*月末库存/);
    expect(store).not.toHaveTextContent('当月在途');
    expect(screen.getByTestId('cell-MSKU-A-11072-2026-10')).toHaveTextContent(/销量预测.*月末库存/);
  });

  it('当月在途 = 海外仓 40（FBA 0）；悬停写组成', () => {
    renderSheet();
    const v = screen.getByTestId('transit-SKU-1-2026-10');
    expect(v).toHaveTextContent('40');
    expect(v.closest('[title]')).toHaveAttribute('title', 'FBA 货件 0 + 海外仓 40');
  });

  it('销量预测悬停：货号 = Σ 店铺；店铺 = Σ msku；msku = 速率 × 天数', () => {
    renderSheet();
    expect(screen.getByTestId('sku-system-SKU-1-2026-10').closest('[title]'))
      .toHaveAttribute('title', 'A4Pet-US 145 + A4Pet-WM 不适用（不计）');
    expect(screen.getByTestId('store-system-11072-SKU-1-2026-10').closest('[title]'))
      .toHaveAttribute('title', 'MSKU-A 135 + MSKU-B 10');
    expect(screen.getByTestId('msku-system-MSKU-A-11072-2026-10').closest('[title]'))
      .toHaveAttribute('title', expect.stringMatching(/^4\.3251 件\/天 × 31 天/));
  });

  it('月末库存悬停：期初 − 计划销量 + 到货 = 月末', () => {
    renderSheet();
    const store = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10');
    expect(within(store).getByTestId('closing-sku').closest('[title]'))
      .toHaveAttribute('title', '期初 271 − 计划销量 105 + 到货 0 = 166');
  });

  it('紧凑：货号行单字 销 / 存 / 途，全称在 title 上', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '紧凑' }));
    const cell = screen.getByTestId('sku-stock-SKU-1-2026-10').closest('.ps-c') as HTMLElement;
    for (const [short, full] of [['销', '销量预测'], ['存', '月末库存'], ['途', '当月在途']] as const) {
      expect(within(cell).getByText(short)).toHaveAttribute('title', full);
    }
  });

  it('海外仓读不到：当月在途写「—」，悬停说为什么，不当 0', () => {
    const g = structuredClone(LABEL_GRID);
    for (const s of g.forecast_sku) s.month_in_transit = { ...s.month_in_transit!, total: null, oversea: null };
    renderSheet(g);
    const v = screen.getByTestId('transit-SKU-1-2026-10');
    expect(v).toHaveTextContent('—');
    expect(v.closest('[title]')).toHaveAttribute('title', 'FBA 货件 0 + 海外仓 读不到');
  });
});

describe('★ FM 终审修正（I-4 · M-2）', () => {
  it('as_of 之前的月份：当月在途写「—」，悬停说没有记录', () => {
    const g = structuredClone(LABEL_GRID);
    g.forecast_sku[0]!.month_in_transit = { total: null, fba: null, oversea: null, items: [], before_as_of: true };
    renderSheet(g);
    expect(screen.getByTestId('transit-SKU-1-2026-10')).toHaveTextContent('—');
    expect(screen.getByTestId('transit-SKU-1-2026-10').closest('[title]'))
      .toHaveAttribute('title', 'as_of 之前的月份没有在途记录');
  });

  it('as_of 所在月：月末库存悬停写清起点日与计划销量从哪天起扣', () => {
    renderSheet(LABEL_GRID_PARTIAL);
    const store = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10');
    expect(within(store).getByTestId('closing-sku').closest('[title]'))
      .toHaveAttribute('title', '现有 313（10-10）− 计划销量 71（10-11 起）+ 到货 0 = 242');
  });
});

// ── 10-15 负责人：「SKU 行是要求填计划采购数量，而 msku 行是填计划销售数量 …… 运营是不会懂的」──────
describe('★ 填什么一眼分得开：货号行填计划采购，店铺 / msku 行填计划销量', () => {
  const firstCell = (row: HTMLElement) => within(row).getAllByRole('gridcell')[0]!;

  it('可填的数前面写全称：货号行「计划采购」，单 msku 店铺行与 msku 行「计划销量」', () => {
    renderSheet();
    expect(firstCell(screen.getByTestId('purchase-row-SKU-1'))).toHaveTextContent(/^计划采购\s*200/);
    expect(within(screen.getByTestId('block-90001-SKU-1')).getByTestId('sku-cell-2026-10')).toHaveTextContent(/^计划销量\s*60/);
    expect(screen.getByTestId('cell-MSKU-A-11072-2026-10')).toHaveTextContent(/^计划销量\s*100/);
  });

  it('多 msku 的店铺行：计划销量是自动合计，不长成输入框', () => {
    renderSheet();
    const ro = within(screen.getByTestId('block-11072-SKU-1')).getByTestId('sku-cell-2026-10');
    expect(ro).toHaveTextContent(/^计划销量\s*105\s*自动合计/);
    expect(ro.className).not.toContain('is-input');
    expect(screen.getByTestId('cell-MSKU-A-11072-2026-10').className).toContain('is-input');
    expect(firstCell(screen.getByTestId('purchase-row-SKU-1')).className).toContain('is-input');
  });

  it('可填但没填：灰字提示「填采购量」，不是「—」', () => {
    const g = structuredClone(LABEL_GRID);
    g.purchase = g.purchase.map((p) => (p.period === '2026-11' ? { ...p, planned_units: null } : p));
    renderSheet(g);
    const nov = within(screen.getByTestId('purchase-row-SKU-1')).getAllByRole('gridcell')[1]!;
    expect(nov).toHaveTextContent(/^计划采购\s*填采购量/);
    expect(nov).not.toHaveTextContent(/^计划采购\s*—/);
  });

  it('行头标签：货号行「填计划采购」，店铺 / msku 行「填计划销量」', () => {
    renderSheet();
    expect(within(screen.getByTestId('purchase-row-SKU-1')).getByRole('rowheader')).toHaveTextContent('填计划采购');
    expect(within(screen.getByTestId('block-90001-SKU-1')).getAllByRole('rowheader')[0]).toHaveTextContent('填计划销量');
    const msku = screen.getByTestId('cell-MSKU-A-11072-2026-10').closest('[role="row"]') as HTMLElement;
    expect(within(msku).getByRole('rowheader')).toHaveTextContent('填计划销量');
  });

  it('合计列写清哪个是计划采购、哪个是计划销量；屏上不再出现「期望」', () => {
    renderSheet();
    const sum = screen.getByTestId('purchase-row-SKU-1').querySelector('.ps-sum') as HTMLElement;
    expect(sum).toHaveTextContent(/计划采购\s*280/);
    expect(sum).toHaveTextContent(/计划销量\s*\d+/);
    expect(document.body).not.toHaveTextContent('期望');
  });

  it('紧凑：可填数的名字缩成单字「采」/「销」，全称在 title 上', async () => {
    renderSheet();
    await userEvent.click(screen.getByRole('button', { name: '紧凑' }));
    expect(within(firstCell(screen.getByTestId('purchase-row-SKU-1'))).getByText('采')).toHaveAttribute('title', '计划采购');
    expect(within(screen.getByTestId('cell-MSKU-A-11072-2026-10')).getByText('销')).toHaveAttribute('title', '计划销量');
  });
});
