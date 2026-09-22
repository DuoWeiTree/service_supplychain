import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError } from '../api/client';
import type { ClosingReason, DemandCell, GridResponse, PlanSummary, PurchaseCell, ReleaseResult, Seller } from '../api/types';

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

interface MockApi {
  getPlan: () => Promise<PlanSummary>;
  listSellers: () => Promise<Seller[]>;
  getGrid: () => Promise<GridResponse>;
  putDemand: (...args: unknown[]) => Promise<DemandCell>;
  putPurchase: (...args: unknown[]) => Promise<PurchaseCell>;
  releaseClaim: (...args: unknown[]) => Promise<ReleaseResult>;
  submit: (...args: unknown[]) => Promise<unknown>;
}

function buildMockApi(grid: GridResponse) {
  const state = { grid };
  const api: MockApi = {
    getPlan: async () => PLAN,
    listSellers: async () => SELLERS,
    getGrid: async () => JSON.parse(JSON.stringify(state.grid)) as GridResponse,
    putDemand: vi.fn(async (...args: unknown[]) => {
      const [, ss, sid, period, units] = args as [number, string, string, string, number | null];
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
    }),
    putPurchase: vi.fn(async (...args: unknown[]) => {
      const [, sku, period, units] = args as [number, string, string, number | null];
      const c = state.grid.purchase.find((x) => x.sku === sku && x.period === period)!;
      c.planned_units = units;
      return c;
    }),
    releaseClaim: vi.fn(async (...args: unknown[]) => {
      const [, sellerSku, sid] = args as [number, string, string];
      const before = state.grid.demand.filter((x) => x.seller_sku === sellerSku && x.sid === sid);
      state.grid.demand = state.grid.demand.filter((x) => !(x.seller_sku === sellerSku && x.sid === sid));
      const dropped_cells = before
        .filter((x) => x.expected_units !== null)
        .map((x) => ({ period: x.period, expected_units: x.expected_units }));
      return { released: { seller_sku: sellerSku, sid }, dropped_cells, stranded_purchase_cells: [] };
    }),
    submit: vi.fn(async () => ({ rev: 1, lines: 1, in_flight: true, content_digest: 'x', skipped: [] })),
  };
  return { state, api };
}

async function renderGrid(grid: GridResponse = makeGrid(), patch?: (m: { state: { grid: GridResponse }; api: MockApi }) => void) {
  const mocked = buildMockApi(grid);
  patch?.(mocked);
  vi.doMock('../api', () => ({ ApiError, api: mocked.api }));
  const { PlanGrid } = await import('./PlanGrid');
  const utils = render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
  return { ...utils, mock: mocked };
}

// ★ F11 裁定：折叠按钮的可及名称点名是哪一块（`展开 {seller_name} 的 {sku}`），
//   不再是裸的「展开」二字 —— 用前缀正则匹配，兼容不同块的不同名称
const expand = async (testId: string) => {
  const block = await screen.findByTestId(testId);
  await userEvent.click(within(block).getByRole('button', { name: /^展开 / }));
  return block;
};

describe('计划编辑网格', () => {
  it('标题 + 月份列 + 合计列', async () => {
    await renderGrid();
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    const head = within(await screen.findByTestId('block-11072-SKU-1')).getAllByRole('columnheader');
    expect(head.map((h) => h.textContent)).toEqual(['店铺·货号', '2026-10', '2026-11', '2026-12', '合计']);
  });

  it('★ 页头元数据不拼 A · B · C 的元串，三段各自成句（F16）', async () => {
    await renderGrid();
    await screen.findByRole('heading', { name: '2026 Q4 销售计划' });
    const meta = screen.getByTestId('head-meta');
    expect(meta.textContent).not.toContain('·');
    expect(meta).toHaveTextContent('起始月');
    expect(meta).toHaveTextContent('2026-10');
    expect(meta).toHaveTextContent('负责人');
    expect(meta).toHaveTextContent('ops.zhang');
  });

  it('★ 库存预估画在店铺·货号行上，折叠态就看得见', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    // ★ F10 裁定：折叠行的 testid 恒为 closing-sku，不再随展开/折叠切换
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('200');
    expect(within(cell).getByText('期初 420')).toBeInTheDocument();
    expect(within(cell).getByText('未计本计划采购')).toBeInTheDocument();
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('220');
    // ★ 折叠态没有输入框：填数是 msku 级的事
    expect(within(block).queryByRole('textbox')).toBeNull();
    // ★ F10：块上有 data-open 表达折叠/展开状态
    expect(block).toHaveAttribute('data-open', 'false');
  });

  it('★ 展开后的 msku 行只有预估与输入 —— 库存不在这一层，不许重复画', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    expect(block).toHaveAttribute('data-open', 'true');
    const cell = within(block).getByTestId('cell-MSKU-A-2026-10');
    expect(within(cell).getByTestId('system')).toHaveClass('i-pencil');
    expect(within(cell).getByRole('textbox')).toHaveValue('180');
    expect(within(cell).queryByTestId('closing-sku')).toBeNull();
    // 店铺·货号行还在，库存只画那一遍：整块只有 3 个 closing-sku（每月一个），全在折叠行上
    expect(within(block).getAllByTestId('closing-sku')).toHaveLength(3);
  });

  it('★ 外推的预估带朱批角标 —— 外推 ≠ 预估（F15：可及名称用 aria-label）', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const marker = within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByLabelText('系统外推');
    expect(marker).toHaveClass('ext');
    expect(marker).toHaveTextContent('外');
    expect(within(within(block).getByTestId('cell-MSKU-A-2026-10')).queryByLabelText('系统外推')).toBeNull();
  });

  it('★ 空 = 未知：输入框空着，不显示 0，placeholder 也不是 "0"', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByRole('textbox');
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('placeholder', '');
    expect(within(within(block).getByTestId('sku-cell-2026-12')).getByTestId('closing-sku')).toHaveTextContent('—');
  });

  it('★ 断货格：朱批左边条 + data-state=out，画在店铺·货号行上', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-11');
    expect(cell).toHaveAttribute('data-state', 'out');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('-30');
  });

  it('★ 无 FBA 的店铺：写「不适用」，整块里不出现 0 库存（F12：断言钉在取舍本身）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    expect(within(block).getAllByText('不适用')).toHaveLength(3);
    expect(within(block).getByTestId('sku-cell-2026-10')).not.toHaveAttribute('data-state');
    // ★ F12：合计列不渲染「期末」这个 testid（每月已各写一遍「不适用」，不重复）
    expect(within(block).queryByTestId('sum-inventory')).toBeNull();
    // ★ F12：整块任何库存展示都不许是字面 0
    expect(within(block).queryByText('0')).toBeNull();
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

  it('★ 折叠按钮有 aria-expanded 与点名是哪一块的可及名称（F11）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const btn = within(block).getByRole('button', { name: '展开 A4Pet-US 的 SKU-1' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(btn);
    expect(within(block).getByRole('button', { name: '折叠 A4Pet-US 的 SKU-1' }))
      .toHaveAttribute('aria-expanded', 'true');
  });

  it('填期望销量 → 保存并当场刷新店铺·货号行的库存预估', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '300');
    await userEvent.tab();
    // 420 − (300 + 40)，★ F13：断言钉在 closing-sku 本身，不查整个 td 的文本
    const cell = await within(block).findByTestId('sku-cell-2026-10');
    await waitFor(() => expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('80'));
  });

  it('★ 清空一个 msku 的输入 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-B-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.tab();
    // ★ 断言必须钉在 closing 本身，不能只查整个 td 的文本 —— 同一格里的「预估」行也会因为
    //   MSKU-B 无系统预估而显示 —，宽断言会被旁边的同形文字托住，测不出「未知落成 0」这类真回归
    const cell = await within(block).findByTestId('sku-cell-2026-10');
    await waitFor(() => expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('—'));
  });

  it('★ 非数字（5oo）在客户端就被拦下，不发请求（F1）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('purchase-block');
    const input = within(block).getByRole('textbox', { name: '计划采购量 SKU-1 2026-11' });
    await userEvent.type(input, '5oo');
    // ★ 用直接派发的 blur，不用 tab() —— tab() 会把焦点移到下一格再触发它的 blur，
    //   污染另一格的请求记录，测不准「这一格没发请求」
    fireEvent.blur(input);
    expect(mock.api.putPurchase).not.toHaveBeenCalled();
    expect(await within(block).findByTestId('err-purchase-SKU-1-2026-11')).toHaveTextContent('5oo');
  });

  it('★ 负数在客户端就被拦下，不发请求（F2）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('purchase-block');
    const input = within(block).getByRole('textbox', { name: '计划采购量 SKU-1 2026-11' });
    await userEvent.type(input, '-5');
    fireEvent.blur(input);
    expect(mock.api.putPurchase).not.toHaveBeenCalled();
    expect(await within(block).findByTestId('err-purchase-SKU-1-2026-11')).toHaveTextContent('-5');
  });

  it('★ 期望销量输入走同一套校验（F1/F2 裁定：两个输入共用 parseUnits）', async () => {
    const { mock } = await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-11')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '-3');
    fireEvent.blur(input);
    expect(mock.api.putDemand).not.toHaveBeenCalled();
    expect(await within(block).findByTestId('err-MSKU-A-2026-11')).toHaveTextContent('-3');
  });

  it('★ PUT 在飞时输入框置灰，且第二次 blur 不再重复发请求（F3）', async () => {
    let resolvePut!: (v: PurchaseCell) => void;
    const deferred = vi.fn(() => new Promise<PurchaseCell>((resolve) => { resolvePut = resolve; }));
    const { mock } = await renderGrid(makeGrid(), (m) => { m.api.putPurchase = deferred; });
    const block = await screen.findByTestId('purchase-block');
    const input = within(block).getByRole('textbox', { name: '计划采购量 SKU-1 2026-11' });

    await userEvent.type(input, '77');
    await userEvent.tab();
    await waitFor(() => expect(input).toBeDisabled());
    expect(mock.api.putPurchase).toHaveBeenCalledTimes(1);

    // ★ 飞行中再触发一次 blur（绕开浏览器对 disabled 元素的天然限制，直接派发事件）
    fireEvent.blur(input);
    expect(mock.api.putPurchase).toHaveBeenCalledTimes(1);

    resolvePut({ sku: 'SKU-1', period: '2026-11', planned_units: 77 });
    await waitFor(() => expect(input).not.toBeDisabled());
  });

  it('★ 删除货号遇错即停，toast 点名已释放的与失败的那个（F4）', async () => {
    const releaseClaim = vi.fn(async (...args: unknown[]) => {
      const [, sellerSku] = args as [number, string];
      if (sellerSku === 'MSKU-B') throw new ApiError(500, 'release_failed', '释放失败：服务器炸了');
      return { released: { seller_sku: sellerSku, sid: '11072' }, dropped_cells: [], stranded_purchase_cells: [] };
    });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '删除货号' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已释放 MSKU-A');
    expect(status).toHaveTextContent('MSKU-B 释放失败');
    expect(releaseClaim).toHaveBeenCalledTimes(2);
    // ★ F4：不是静默吞掉 —— ErrorDetail 也要看得见
    expect(await screen.findByRole('alert')).toHaveTextContent('释放失败：服务器炸了');
  });

  it('★ 删除货号在第一个 msku 就失败 —— 调用次数本身能区分「停了」还是「继续了」（F4）', async () => {
    const releaseClaim = vi.fn(async () => { throw new ApiError(500, 'release_failed', '第一个就炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '删除货号' }));
    await screen.findByRole('alert');
    // ★ 若代码把 catch 里的 return 换成继续循环，这里会变成 2 —— 块里一共 2 个 msku
    expect(releaseClaim).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toHaveTextContent('已释放 无');
    expect(screen.getByRole('status')).toHaveTextContent('MSKU-A 释放失败');
  });

  it('★ 单个 msku 释放失败走 ErrorDetail 并重载，不静默吞掉（F4）', async () => {
    const releaseClaim = vi.fn(async () => { throw new ApiError(500, 'release_failed', '释放失败：服务器炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await expand('block-11072-SKU-1');
    const buttons = within(block).getAllByRole('button', { name: '删除 msku' });
    await userEvent.click(buttons[0]!);
    expect(await screen.findByRole('alert')).toHaveTextContent('释放失败：服务器炸了');
  });

  it('★ 搁浅的货号级采购格要点名，不许吞掉（F5）', async () => {
    const releaseClaim = vi.fn(async () => ({
      released: { seller_sku: 'MSKU-A', sid: '11072' },
      dropped_cells: [{ period: '2026-10', expected_units: 180 }],
      stranded_purchase_cells: [{ sku: 'SKU-1', period: '2026-10' }],
    }));
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await expand('block-11072-SKU-1');
    const buttons = within(block).getAllByRole('button', { name: '删除 msku' });
    await userEvent.click(buttons[0]!);
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('MSKU-A 已移出，丢弃 1 格');
    expect(status).toHaveTextContent('货号级采购格 1 个未删、待处理');
  });

  it('计划采购量是货号级，行头不带店铺；在途单独一行只读', async () => {
    await renderGrid();
    const block = await screen.findByTestId('purchase-block');
    // ★ 「SKU-1」现在在采购行与在途行的货号列都会出现（两行各自的 gr__code），至少要有一处
    expect(within(block).getAllByText('SKU-1').length).toBeGreaterThanOrEqual(1);
    expect(within(block).queryByText('A4Pet-US')).toBeNull();
    expect(within(block).getAllByRole('textbox')).toHaveLength(3);

    // ★ F7 裁定：testid 按 sku 参数化
    const transit = within(block).getByTestId('transit-row-SKU-1');
    expect(transit).toHaveTextContent('80');
    expect(transit).toHaveTextContent('货号级在途');
    expect(within(transit).getByText('未分摊到店铺')).toBeInTheDocument();
    expect(within(transit).queryByRole('textbox')).toBeNull();   // ★ 只读
    expect(transit).toHaveTextContent('—');                       // 11/12 月无在途 ⇒ 未知不是 0
  });

  it('★ 在途行按 sku 参数化 testid，且覆盖不在 purchase 里的货号（F7/F8）', async () => {
    const grid = makeGrid();
    // 追加一个只出现在 sku_pipeline、既不在 purchase 也不在任何块里的货号
    grid.sku_pipeline.push({ sku: 'SKU-2', period: P[0]!, units: 15, sources: [], no_seller_attribution: true });
    await renderGrid(grid);
    await screen.findByTestId('purchase-block');
    expect(screen.getByTestId('transit-row-SKU-1')).toBeInTheDocument();
    expect(screen.getByTestId('transit-row-SKU-2')).toBeInTheDocument();
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
    // ★ 真 fixture 的 orphans 应当为空（F6/F8/F9 的验收基线，与 planGridModel.test.ts 的模型层测试呼应）
    expect(screen.queryByTestId('orphans')).toBeNull();
  });

  it('★ 真 fixture：外推角标在 MSKU-C 的 2026-12 上能找到（F15）', async () => {
    vi.resetModules();
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
    const { PlanGrid } = await import('./PlanGrid');
    render(
      <MemoryRouter initialEntries={['/plans/1']}>
        <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
      </MemoryRouter>,
    );
    const block = await expand('block-11094-DCC1800264G1');
    const marker = within(within(block).getByTestId('cell-MSKU-C-2026-12')).getByLabelText('系统外推');
    expect(marker).toHaveTextContent('外');
  });
});
