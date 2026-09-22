import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ClosingReason, GridResponse, PlanSummary, Seller } from '../api/types';

const P = ['2026-10', '2026-11', '2026-12'];
const PLAN: PlanSummary = {
  plan_id: 1, title: '2026 Q4 销售计划', period_start: '2026-10-01', months: 3,
  owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
};
const SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

function makeGrid(): GridResponse {
  const d = (ss: string, sid: string, p: string, eff: number | null, extrap = false) => ({
    seller_sku: ss, sid, sku: 'SKU-1', period: p, system_units: 100, system_extrapolated: extrap,
    expected_units: eff, effective_units: eff,
    basis: (eff === null ? 'unknown' : 'human') as 'unknown' | 'human',
  });
  const i = (sid: string, p: string, onhand: number | null, closing: number | null,
             closing_reason: ClosingReason = null, transit: number | null = 80) => ({
    sku: 'SKU-1', sid, period: p, onhand, inbound: null as null, closing,
    basis: { source: 'ch' as const, as_of: '2026-09-21', includes_plan_purchase: false as const,
             demand: onhand !== null && closing !== null ? onhand - closing : null,
             reason: 'no_seller_attribution' as const, closing_reason,
             sku_level_in_transit: transit, sources: [] },
  });
  return {
    plan_id: 1, periods: [...P],
    demand: [d('MSKU-A', '11072', P[0]!, 180), d('MSKU-A', '11072', P[1]!, 200),
             d('MSKU-A', '11072', P[2]!, null, true),
             // ★ MSKU-B 10 月无系统预估（system_units:null）—— 清空人工输入时没有系统底料可回退，
             //   整格真的落回「未知」；11 月给了具体期望值（30），使这一格与库存行 basis.demand
             //   （由 onhand−closing=200−(−30)=230 反推）互为一致的两个证人，断货场景本身不产生「对不上」
             { ...d('MSKU-B', '11072', P[0]!, 40), system_units: null },
             d('MSKU-B', '11072', P[1]!, 30),
             d('MSKU-B', '11072', P[2]!, null),
             d('MSKU-W', '90001', P[0]!, 60), d('MSKU-W', '90001', P[1]!, 65), d('MSKU-W', '90001', P[2]!, 70)],
    purchase: P.map((p, n) => ({ sku: 'SKU-1', period: p, planned_units: n === 0 ? 500 : null })),
    // ★ sku_pipeline 只在 10 月有在途（80），11/12 月为 null —— 11072 每格的
    //   basis.sku_level_in_transit 必须跟它一致，否则 in_transit_disagrees 会（正确地）报出来
    inventory: [i('11072', P[0]!, 420, 200), i('11072', P[1]!, 200, -30, null, null),
                i('11072', P[2]!, -30, null, 'unknown_demand', null),
                i('90001', P[0]!, null, null, 'not_applicable', null),
                i('90001', P[1]!, null, null, 'not_applicable', null),
                i('90001', P[2]!, null, null, 'not_applicable', null)],
    sku_pipeline: P.map((p, n) => ({
      sku: 'SKU-1', period: p, units: n === 0 ? 80 : null,
      sources: [], no_seller_attribution: true as const,
    })),
  };
}

beforeEach(() => { vi.resetModules(); });

async function renderGrid(grid: GridResponse = makeGrid()) {
  const { ApiError } = await import('../api/client');
  const state = { grid };
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      getPlan: async () => PLAN,
      listSellers: async () => SELLERS,
      getGrid: async () => JSON.parse(JSON.stringify(state.grid)) as GridResponse,
      putDemand: async (_p: number, ss: string, sid: string, period: string, units: number | null) => {
        const cell = state.grid.demand.find((x) => x.seller_sku === ss && x.period === period)!;
        cell.expected_units = units;
        cell.effective_units = units ?? cell.system_units;
        cell.basis = units === null ? 'system' : 'human';
        const iv = state.grid.inventory.find((x) => x.sku === cell.sku && x.sid === sid && x.period === period)!;
        const mine = state.grid.demand.filter((x) => x.sku === cell.sku && x.sid === sid && x.period === period);
        const sum = mine.some((x) => x.effective_units === null)
          ? null : mine.reduce((a, x) => a + (x.effective_units as number), 0);
        iv.basis.demand = sum;
        iv.closing = iv.onhand === null || sum === null ? null : iv.onhand - sum;
        iv.basis.closing_reason = iv.closing === null ? 'unknown_demand' : null;
        return cell;
      },
      putPurchase: async (_p: number, sku: string, period: string, units: number | null) => {
        const c = state.grid.purchase.find((x) => x.sku === sku && x.period === period)!;
        c.planned_units = units;
        return c;
      },
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], stranded_purchase_cells: [] }),
      submit: async () => ({ rev: 1, lines: 1, in_flight: true, content_digest: 'x', skipped: [] }),
    },
  }));
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

const expand = async (testId: string) => {
  const block = await screen.findByTestId(testId);
  await userEvent.click(within(block).getByRole('button', { name: '展开' }));
  return block;
};

describe('计划编辑网格', () => {
  it('标题 + 月份列 + 合计列', async () => {
    await renderGrid();
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    const head = within(await screen.findByTestId('block-11072-SKU-1')).getAllByRole('columnheader');
    expect(head.map((h) => h.textContent)).toEqual(['店铺·货号', '2026-10', '2026-11', '2026-12', '合计']);
  });

  it('★ 库存预估画在店铺·货号行上，折叠态就看得见', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('200');
    expect(within(cell).getByText('期初 420')).toBeInTheDocument();
    expect(within(cell).getByText('未计本计划采购')).toBeInTheDocument();
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('220');
    // ★ 折叠态没有输入框：填数是 msku 级的事
    expect(within(block).queryByRole('textbox')).toBeNull();
  });

  it('★ 展开后的 msku 行只有预估与输入 —— 库存不在这一层，不许重复画', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const cell = within(block).getByTestId('cell-MSKU-A-2026-10');
    expect(within(cell).getByTestId('system')).toHaveClass('i-pencil');
    expect(within(cell).getByRole('textbox')).toHaveValue('180');
    expect(within(cell).queryByTestId('closing')).toBeNull();
    // 店铺·货号行还在，库存只画那一遍
    // ★ getAllByTestId 在 0 命中时会抛错而不是回空数组 —— 断言"零命中"必须用 queryAllByTestId
    expect(within(block).queryAllByTestId(/^closing$/)).toHaveLength(0);
    expect(within(block).getAllByTestId('closing-sku')).toHaveLength(3);
  });

  it('★ 外推的预估带朱批角标 —— 外推 ≠ 预估', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    expect(within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByTitle('外推')).toHaveClass('ext');
    expect(within(within(block).getByTestId('cell-MSKU-A-2026-10')).queryByTitle('外推')).toBeNull();
  });

  it('★ 空 = 未知：输入框空着，不显示 0，placeholder 也不是 "0"', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByRole('textbox');
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('placeholder', '');
    expect(within(block).getByTestId('sku-cell-2026-12')).toHaveTextContent('—');
  });

  it('★ 断货格：朱批左边条 + data-state=out，画在店铺·货号行上', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-11');
    expect(cell).toHaveAttribute('data-state', 'out');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('-30');
  });

  it('★ 无 FBA 的店铺：写「不适用」，整块里不出现 0 库存', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    expect(within(block).getAllByText('不适用')).toHaveLength(3);
    expect(within(block).getByTestId('sku-cell-2026-10')).not.toHaveAttribute('data-state');
  });

  it('★ 合计列：期望跨月求和；存量给「期末」而不是三个月相加', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(within(block).getByText('期末')).toBeInTheDocument();
    // 12 月 closing 未知 ⇒ 期末也是未知；★ 而 200 + (−30) 这种和一个字都不许出现
    expect(within(block).getByTestId('sum-inventory')).toHaveTextContent('—');
    expect(within(block).queryByText('170')).toBeNull();
    expect(within(block).getByTestId('sum-demand')).toHaveTextContent('—');  // 12 月未知 ⇒ 传染
  });

  it('★ 断货计数挂在块头，折起来也看得见', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(within(block).getByTestId('outage-11072-SKU-1')).toHaveTextContent('断货 1 个月');
    // ★ 整块不适用的不算断货
    expect(within(screen.getByTestId('block-90001-SKU-1'))
      .queryByTestId('outage-90001-SKU-1')).toBeNull();
  });

  it('填期望销量 → 保存并当场刷新店铺·货号行的库存预估', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '300');
    await userEvent.tab();
    // 420 − (300 + 40)
    expect(await within(block).findByTestId('sku-cell-2026-10')).toHaveTextContent('80');
  });

  it('★ 清空一个 msku 的输入 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-B-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.tab();
    // ★ 断言必须钉在 closing 本身（block 已展开 ⇒ testid 是 closing-sku），
    //   不能只查整个 td 的文本 —— 同一格里的「预估」行也会因为 MSKU-B 无系统预估而显示 —，
    //   宽断言会被旁边的同形文字托住，测不出「未知落成 0」这类真回归
    const cell = await within(block).findByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('—');
  });

  it('计划采购量是货号级，行头不带店铺；在途单独一行只读', async () => {
    await renderGrid();
    const block = await screen.findByTestId('purchase-block');
    expect(within(block).getByText('SKU-1')).toBeInTheDocument();
    expect(within(block).queryByText('A4Pet-US')).toBeNull();
    expect(within(block).getAllByRole('textbox')).toHaveLength(3);

    const transit = within(block).getByTestId('transit-row');
    expect(transit).toHaveTextContent('80');
    expect(transit).toHaveTextContent('货号级在途');
    expect(within(transit).getByText('未分摊到店铺')).toBeInTheDocument();
    expect(within(transit).queryByRole('textbox')).toBeNull();   // ★ 只读
    expect(transit).toHaveTextContent('—');                       // 11/12 月无在途 ⇒ 未知不是 0
  });

  it('★ 在途一件都没并进库存', async () => {
    await renderGrid();
    await screen.findByTestId('block-11072-SKU-1');
    expect(screen.queryByText('280')).toBeNull();   // 200 + 80
  });

  it('★ 对不上的行要点名；数据干净时丢弃区不渲染（不是渲染一个空框）', async () => {
    await renderGrid();
    await screen.findByTestId('block-11072-SKU-1');
    expect(screen.queryByTestId('orphans')).toBeNull();

    const dirty = makeGrid();
    dirty.sku_pipeline[0] = { sku: 'SKU-1', period: P[0]!, units: 999,
                              sources: [], no_seller_attribution: true };
    vi.resetModules();
    await renderGrid(dirty);
    expect(await screen.findByTestId('orphans')).toHaveTextContent('in_transit_disagrees');
  });

  it('★ 后端那份真 fixture 也画得出三种形态（不写死数字，只认形态）', async () => {
    vi.resetModules();
    const { createMockApi } = await import('../api/mock');
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
    const { PlanGrid } = await import('./PlanGrid');
    render(
      <MemoryRouter initialEntries={['/plans/1']}>
        <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
      </MemoryRouter>,
    );
    const blocks = await screen.findAllByTestId(/^block-/);
    expect(blocks.length).toBeGreaterThan(0);
    expect(screen.getAllByText('不适用').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未计本计划采购').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未分摊到店铺').length).toBeGreaterThan(0);
    // ★ 「不可求和」在阶段 A 没有落点：出现了就说明有人把它当默认值用了
    expect(screen.queryByText('不可求和')).toBeNull();
  });
});
