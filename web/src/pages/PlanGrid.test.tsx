import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError } from '../api/client';
import type {
  DemandCell, ForecastState, GridResponse, PlanSummary, PurchaseCell, ReleaseResult, RemoveSkuResult, Seller,
  SkuForecast, StoreForecast,
} from '../api/types';

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
    system_basis: null,
  });
  const st = (sid: string, p: string, opening: number | null, closing: number | null,
              state: ForecastState = closing === null ? 'unknown' : 'ok'): StoreForecast => ({
    sku: 'SKU-1', sid, period: p, state,
    reason: state === 'ok' ? null : state === 'not_applicable' ? 'not_applicable' : 'unknown_demand',
    demand: opening !== null && closing !== null ? opening - closing : null,
    system: null, system_not_applicable: false, opening, inbound_planned: 0, closing,
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
    params: { as_of: '2026-09-21', route: 'oversea_sea', make_days: 30, ship_days: 60,
              anchor_day: 15, embargo_days: 7, in_transit_as_of: '2026-09-21' },
    forecast: [],
    forecast_msku: [],
    forecast_store: [st('11072', P[0]!, 420, 200), st('11072', P[1]!, 200, -30),
                     st('11072', P[2]!, -30, null),
                     ...P.map((p) => st('90001', p, null, null, 'not_applicable'))],
    // ★ 只给 10 月的货号行（在途 80）：11/12 月缺行 ⇒ 在途未知，用来钉「未知不是 0」
    forecast_sku: [transitRow('SKU-1', P[0]!, 80)],
    beyond_horizon: [],
    unallocated: [],
  };
}

/** 货号级一行：在途全记在 ordered_open 上，组成与总数一致 */
function transitRow(sku: string, period: string, units: number): SkuForecast {
  return {
    sku, period, state: 'ok', reason: null, reason_from: null, demand: null, system: null, system_not_applicable: false,
    opening: null, stock_demand: null, inbound_planned: 0, closing: null, excluded_not_applicable: 0,
    pipeline: { making: 0, shipping: 0, ordered_open: units, ordered_open_overdue: 0, ordered_open_sources: [],
                fba_inbound: null, fba_inbound_reason: 'not_fetched' },
    in_transit: units, in_transit_excludes: ['fba_inbound'], unfilled_purchase: [],
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
  removeSku: (...args: unknown[]) => Promise<RemoveSkuResult>;
  submit: (...args: unknown[]) => Promise<unknown>;
  // ★ Task 20：PlanGrid 现在挂 LeadTimeBar，挂载即调 getLeadTime —— 这个测试替身
  //   不覆盖它就会在每个用例里抛 TypeError（不是本测试关心的路径，给固定值即可）
  getLeadTime: () => Promise<{ route: 'oversea_sea'; make_days: number; ship_days: number;
    anchor_day: number; valid_from: { making: string; shipping: string } }>;
  putLeadTime: (...args: unknown[]) => Promise<{ route: 'oversea_sea'; make_days: number; ship_days: number;
    anchor_day: number; valid_from: { making: string; shipping: string };
    changed: { segment: 'making' | 'shipping'; from: number; to: number }[] }>;
}

function buildMockApi(grid: GridResponse) {
  const state = { grid };
  const dropSku = (sku: string) => {
    const g = state.grid;
    const gone = g.purchase.filter((x) => x.sku === sku);
    g.purchase = g.purchase.filter((x) => x.sku !== sku);
    g.forecast_store = g.forecast_store.filter((x) => x.sku !== sku);
    g.forecast_sku = g.forecast_sku.filter((x) => x.sku !== sku);
    return gone.map((x) => ({ ...x }));
  };
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
      // 测试替身自己按「期初 − Σ 该店该货号生效值」重算这一格，只为验证界面会当场刷新
      const sf = state.grid.forecast_store.find((x) => x.sku === cell.sku && x.sid === sid && x.period === period)!;
      const mine = state.grid.demand.filter((x) => x.sku === cell.sku && x.sid === sid && x.period === period);
      const sum = mine.some((x) => x.effective_units === null)
        ? null : mine.reduce((a, x) => a + (x.effective_units as number), 0);
      sf.demand = sum;
      sf.closing = sf.opening === null || sum === null ? null : sf.opening - sum;
      sf.state = sf.closing === null ? 'unknown' : 'ok';
      sf.reason = sf.closing === null ? 'unknown_demand' : null;
      return cell;
    }),
    putPurchase: vi.fn(async (...args: unknown[]) => {
      const [, sku, period, units] = args as [number, string, string, number | null];
      const c = state.grid.purchase.find((x) => x.sku === sku && x.period === period)!;
      c.planned_units = units;
      return c;
    }),
    // ★ Task 22：替身照后端的口径删 —— 店里没 msku 了店铺行走，货号没 msku 了采购格与货号行一起走
    releaseClaim: vi.fn(async (...args: unknown[]) => {
      const [, sellerSku, sid] = args as [number, string, string];
      const g = state.grid;
      const before = g.demand.filter((x) => x.seller_sku === sellerSku && x.sid === sid);
      const sku = before[0]?.sku;
      g.demand = g.demand.filter((x) => !(x.seller_sku === sellerSku && x.sid === sid));
      // ★ 与后端同口径：空格照报（null ≠ 0），不在替身里先滤掉
      const dropped_cells = before.map((x) => ({ period: x.period, expected_units: x.expected_units }));
      if (!g.demand.some((x) => x.sku === sku && x.sid === sid)) {
        g.forecast_store = g.forecast_store.filter((x) => !(x.sku === sku && x.sid === sid));
      }
      return { released: { seller_sku: sellerSku, sid }, dropped_cells,
               dropped_purchase_cells: sku !== undefined && !g.demand.some((x) => x.sku === sku) ? dropSku(sku) : [] };
    }),
    removeSku: vi.fn(async (...args: unknown[]) => {
      const [, sku] = args as [number, string];
      const g = state.grid;
      const mine = g.demand.filter((x) => x.sku === sku);
      const released = [...new Map(mine.map((x) => [`${x.seller_sku}/${x.sid}`, { seller_sku: x.seller_sku, sid: x.sid }])).values()];
      g.demand = g.demand.filter((x) => x.sku !== sku);
      return { sku, released,
               dropped_cells: mine.map((x) => ({ seller_sku: x.seller_sku, sid: x.sid, period: x.period, expected_units: x.expected_units })),
               dropped_purchase_cells: dropSku(sku) };
    }),
    submit: vi.fn(async () => ({ rev: 1, lines: 1, in_flight: true, content_digest: 'x', skipped: [] })),
    getLeadTime: vi.fn(async () => ({ route: 'oversea_sea' as const, make_days: 30, ship_days: 60, anchor_day: 15,
      valid_from: { making: '2026-01-01', shipping: '2026-01-01' } })),
    putLeadTime: vi.fn(async (...args: unknown[]) => {
      const [input] = args as [{ make_days?: number; ship_days?: number }];
      return { route: 'oversea_sea' as const, make_days: input.make_days ?? 30, ship_days: input.ship_days ?? 60,
        anchor_day: 15, valid_from: { making: '2026-01-01', shipping: '2026-01-01' }, changed: [] };
    }),
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

/** 选中一格、打字、回车 —— 矩阵里填数的唯一方式（与真人操作同一条路径） */
const fill = async (cell: HTMLElement, text: string) => {
  await userEvent.click(cell);
  await userEvent.keyboard(`${text}{Enter}`);
};
const gridcell = (root: HTMLElement, name: string) => within(root).getByRole('gridcell', { name });

describe('计划整表', () => {
  it('标题 + 一张整表：月份列 + 合计列；一个货号一组', async () => {
    await renderGrid();
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    const card = await screen.findByTestId('card-SKU-1');
    const head = within(screen.getByRole('grid')).getAllByRole('columnheader');
    expect(head.map((h) => h.getAttribute('aria-label') ?? h.textContent))
      .toEqual(['货号 / 店铺 / MSKU', '2026-10', '2026-11', '2026-12', '合计']);
    // ★ 两家店与采购在同一组里 —— 不再是上面一块块销量、底下另一张采购表
    expect(within(card).getByTestId('block-11072-SKU-1')).toBeInTheDocument();
    expect(within(card).getByTestId('block-90001-SKU-1')).toBeInTheDocument();
    expect(within(card).getByTestId('purchase-row-SKU-1')).toBeInTheDocument();
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

  it('★ 库存画在店铺行上，依据（期初 / as_of）回显在格子上', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('200');
    expect(cell.getAttribute('title')).toContain('期初 420');
    expect(cell.getAttribute('title')).toContain('2026-09-21');
    // ★ 预测库存已计入本计划采购 —— 格子上不许再说「不含」
    expect(cell.getAttribute('title')).not.toContain('不含本计划采购');
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('220');
    // ★ 预测库存已含计划采购到货：图例说「含」并回显提前期，旧的「未计」必须消失
    expect(screen.getByText('库存含计划采购到货（生产 30 天 + 货运 60 天）')).toBeInTheDocument();
    expect(screen.queryByText('未计本计划采购')).toBeNull();
  });

  it('★ 多 msku 的店铺默认展开；店铺库存只画在店铺行，msku 行画自己的库存（spec §5）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(block).toHaveAttribute('data-open', 'true');
    const cell = within(block).getByTestId('cell-MSKU-A-11072-2026-10');
    expect(cell).toHaveTextContent('180');
    expect(cell).toHaveClass('is-human');                   // 人填：三角标记
    expect(within(cell).queryByTestId('closing-sku')).toBeNull();
    expect(within(block).getAllByTestId('closing-sku')).toHaveLength(3);
    expect(within(cell).getByTestId('msku-stock-MSKU-A-11072-2026-10')).toBeInTheDocument();
  });

  it('★ 只有一个 msku 的店铺直接在店铺行上填（不用展开）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    expect(within(block).queryByRole('button', { name: /^展开 / })).toBeNull();
    expect(gridcell(block, '期望销量 MSKU-W 2026-10')).toHaveTextContent('60');
  });

  it('★ 空 = 未知：写「待填」，不显示 0；库存跟着是 —', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('cell-MSKU-A-11072-2026-12');
    // ★ 主数是「待填」不是 0。只看主数：副数里的「预测 100」本来就带 0
    const main = cell.querySelector('.ps-v')!;
    expect(main).toHaveTextContent('待填');
    expect(main).not.toHaveTextContent('0');
    expect(within(within(block).getByTestId('sku-cell-2026-12')).getByTestId('closing-sku')).toHaveTextContent('—');
  });

  it('★ 断货格：data-state=out，画在店铺行上', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-11');
    expect(cell).toHaveAttribute('data-state', 'out');
    expect(cell).toHaveTextContent('断货');
    expect(within(cell).getByTestId('closing-sku')).toHaveTextContent('-30');
  });

  it('★ 无 FBA 的店铺：写「无 FBA」，整块里不出现 0 库存（F12）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    expect(within(block).getAllByTestId('closing-na')).toHaveLength(3);
    expect(within(block).getByTestId('sku-cell-2026-10')).not.toHaveAttribute('data-state');
    expect(within(block).queryByTestId('sum-inventory')).toBeNull();
    expect(within(block).queryByText('0')).toBeNull();
  });

  it('★ 合计列：期望跨月求和；存量给「期末」而不是三个月相加', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(within(block).getByTestId('sum-inventory')).toHaveTextContent('—');
    expect(within(block).queryByText('170')).toBeNull();
    expect(within(block).getByTestId('sum-demand')).toHaveTextContent('—');
  });

  it('★ 断货挂在货号行上，折叠也看得见；不适用的店不算断货', async () => {
    await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await userEvent.click(within(card).getByRole('button', { name: '折叠 SKU-1' }));
    expect(within(card).queryByTestId('block-11072-SKU-1')).toBeNull();
    const flag = within(card).getByTestId('outage-SKU-1');
    // 两家店里只有 A4Pet-US 断货；无 FBA 的 A4Pet-WM 是「不适用」，不计入
    expect(flag).toHaveTextContent('断 1');
    expect(flag.getAttribute('title')).toBe('A4Pet-US 断货 1 个月');
  });

  it('★ 下一个待填（N）：光标跳到下一格「待填」的销量，不停在空着的采购格', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await userEvent.click(gridcell(card, '计划采购量 SKU-1 2026-10'));
    await userEvent.keyboard('n');
    // SKU-1 下第一格待填是 MSKU-A 的 12 月；采购 11 / 12 月虽然空着，不算待填
    await userEvent.keyboard('9{Enter}');
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-A', '11072', '2026-12', 9);
    expect(mock.api.putPurchase).not.toHaveBeenCalled();
  });

  it('★ ⌘D 只在同一类行之间填：采购不会被填进销量', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await userEvent.click(gridcell(card, '计划采购量 SKU-1 2026-10'));
    await userEvent.keyboard('{Shift>}{ArrowDown}{/Shift}{Control>}d{/Control}');
    expect(mock.api.putDemand).not.toHaveBeenCalled();
  });

  it('★ 几十个货号时只渲染看得见的行；折叠全部后一行一个货号', async () => {
    const grid = makeGrid();
    for (let n = 2; n <= 80; n++) {
      grid.forecast_sku.push(transitRow(`SKU-${n}`, P[0]!, 1));
    }
    await renderGrid(grid);
    await screen.findByTestId('card-SKU-1');
    const status = screen.getByTestId('plan-sheet').querySelector('.pm-status__sel')!;
    expect(status).toHaveTextContent('80 个货号，84 行');
    // 表头 1 行 + 可视区一窗；84 行全画出来就等于没有虚拟滚动
    expect(screen.getAllByRole('row').length).toBeLessThan(40);
    await userEvent.click(screen.getByRole('button', { name: '全部折叠成一行一个货号' }));
    expect(status).toHaveTextContent('80 个货号，80 行');
    expect(screen.queryByTestId('block-11072-SKU-1')).toBeNull();
  });

  it('筛选「偏低」与排序「货号」都在工具栏上', async () => {
    await renderGrid();
    await screen.findByTestId('card-SKU-1');
    expect(screen.getByRole('button', { name: /^偏低/ })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '排序' })).toHaveValue('risk');
  });

  it('★ 折叠按钮有 aria-expanded 与点名是哪一块的可及名称（F11）', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const btn = within(block).getByRole('button', { name: '折叠 A4Pet-US 的 SKU-1' });
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    await userEvent.click(btn);
    expect(within(block).getByRole('button', { name: '展开 A4Pet-US 的 SKU-1' })).toHaveAttribute('aria-expanded', 'false');
    expect(block).toHaveAttribute('data-open', 'false');
    expect(within(block).queryByTestId('cell-MSKU-A-11072-2026-10')).toBeNull();
  });

  it('选中一格直接打数字 → 保存并当场刷新店铺行的库存', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    await fill(within(block).getByTestId('cell-MSKU-A-11072-2026-10'), '300');
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-A', '11072', '2026-10', 300);
    // 420 − (300 + 40)
    await waitFor(() => expect(within(within(block).getByTestId('sku-cell-2026-10')).getByTestId('closing-sku')).toHaveTextContent('80'));
  });

  it('★ Delete 清掉人填 → 没有预估可退的那一格整格未知（不是当 0）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByTestId('cell-MSKU-B-11072-2026-10'));
    await userEvent.keyboard('{Delete}');
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-B', '11072', '2026-10', null);
    await waitFor(() => expect(within(within(block).getByTestId('sku-cell-2026-10')).getByTestId('closing-sku')).toHaveTextContent('—'));
  });

  it('★ 原样确认一个值不发请求 —— 回车看一眼 ≠ 人填', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByTestId('cell-MSKU-A-11072-2026-10'));
    await userEvent.keyboard('{Enter}{Enter}');
    expect(mock.api.putDemand).not.toHaveBeenCalled();
  });

  it('★ 非数字（5oo）在客户端就被拦下，不发请求，并在格内点名（F1）', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await fill(gridcell(card, '计划采购量 SKU-1 2026-11'), '5oo');
    expect(mock.api.putPurchase).not.toHaveBeenCalled();
    expect(await within(card).findByTestId('err-purchase-SKU-1-2026-11')).toHaveTextContent('5oo');
  });

  it('★ 负数在客户端就被拦下，不发请求（F2）', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await fill(gridcell(card, '计划采购量 SKU-1 2026-11'), '-5');
    expect(mock.api.putPurchase).not.toHaveBeenCalled();
    expect(await within(card).findByTestId('err-purchase-SKU-1-2026-11')).toHaveTextContent('-5');
  });

  it('★ 期望销量走同一套校验（F1/F2：两种输入共用 parseUnits）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    await fill(within(block).getByTestId('cell-MSKU-A-11072-2026-11'), '-3');
    expect(mock.api.putDemand).not.toHaveBeenCalled();
    expect(await within(block).findByTestId('err-MSKU-A-11072-2026-11')).toHaveTextContent('-3');
  });

  it('★ 保存在飞时这一格标 busy，再改一次不会重复发请求（F3）', async () => {
    let resolvePut!: (v: PurchaseCell) => void;
    const deferred = vi.fn(() => new Promise<PurchaseCell>((resolve) => { resolvePut = resolve; }));
    const { mock } = await renderGrid(makeGrid(), (m) => { m.api.putPurchase = deferred; });
    const card = await screen.findByTestId('card-SKU-1');
    const cell = gridcell(card, '计划采购量 SKU-1 2026-11');

    await fill(cell, '77');
    await waitFor(() => expect(cell).toHaveAttribute('aria-busy', 'true'));
    expect(cell).toHaveTextContent('77');                  // 在飞时显示刚填的数
    expect(mock.api.putPurchase).toHaveBeenCalledTimes(1);

    await fill(cell, '88');
    expect(mock.api.putPurchase).toHaveBeenCalledTimes(1);

    await act(async () => { resolvePut({ sku: 'SKU-1', period: '2026-11', planned_units: 77 }); });
    await waitFor(() => expect(cell).not.toHaveAttribute('aria-busy'));
  });

  it('★ 期望销量在飞时同样挡住第二次（T4 R3）', async () => {
    let resolvePut!: (v: DemandCell) => void;
    const deferred = vi.fn(() => new Promise<DemandCell>((resolve) => { resolvePut = resolve; }));
    const { mock } = await renderGrid(makeGrid(), (m) => { m.api.putDemand = deferred; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('cell-MSKU-A-11072-2026-10');
    await fill(cell, '77');
    await waitFor(() => expect(cell).toHaveAttribute('aria-busy', 'true'));
    await fill(cell, '99');
    expect(mock.api.putDemand).toHaveBeenCalledTimes(1);
    await act(async () => { resolvePut(makeGrid().demand[0]!); });
    await waitFor(() => expect(cell).not.toHaveAttribute('aria-busy'));
  });

  it('键盘：方向键移动、Tab 右移，打数字落在光标所在格', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await userEvent.click(gridcell(card, '期望销量 MSKU-A 2026-10'));
    await userEvent.keyboard('{ArrowDown}{Tab}');            // → MSKU-B 11 月
    await userEvent.keyboard('12{Enter}');
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-B', '11072', '2026-11', 12);
  });

  it('★ 从 Excel 粘贴一块：从选区左上角铺开，坏值逐格点名、其余照写', async () => {
    const { mock } = await renderGrid();
    const card = await screen.findByTestId('card-SKU-1');
    await userEvent.click(gridcell(card, '期望销量 MSKU-A 2026-11'));
    const grid = screen.getByRole('grid');
    fireEvent.paste(grid, { clipboardData: { getData: () => '11\t12\nx\t22\n' } });
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-A', '11072', '2026-11', 11);
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-A', '11072', '2026-12', 12);
    expect(mock.api.putDemand).toHaveBeenCalledWith(1, 'MSKU-B', '11072', '2026-12', 22);
    expect(await within(card).findByTestId('err-MSKU-B-11072-2026-11')).toHaveTextContent('x');
  });

  it('筛选「有断货」只留有断货的货号；搜索按店铺名命中', async () => {
    const grid = makeGrid();
    grid.forecast_sku.push(transitRow('SKU-2', P[0]!, 15));
    await renderGrid(grid);
    await screen.findByTestId('card-SKU-1');
    expect(screen.getByTestId('card-SKU-2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^有断货/ }));
    expect(screen.getByTestId('card-SKU-1')).toBeInTheDocument();
    expect(screen.queryByTestId('card-SKU-2')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /^全部\s*\d/ }));
    await userEvent.type(screen.getByRole('searchbox', { name: '搜索货号、店铺、msku' }), 'A4Pet-WM');
    expect(screen.getByTestId('card-SKU-1')).toBeInTheDocument();
    expect(screen.queryByTestId('card-SKU-2')).toBeNull();
  });

  it('★ 移出该店遇错即停，toast 点名已释放的与失败的那个（F4）', async () => {
    const releaseClaim = vi.fn(async (...args: unknown[]) => {
      const [, sellerSku] = args as [number, string];
      if (sellerSku === 'MSKU-B') throw new ApiError(500, 'release_failed', '释放失败：服务器炸了');
      return { released: { seller_sku: sellerSku, sid: '11072' }, dropped_cells: [], dropped_purchase_cells: [] };
    });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已释放 MSKU-A');
    expect(status).toHaveTextContent('MSKU-B 释放失败');
    expect(releaseClaim).toHaveBeenCalledTimes(2);
    expect(await screen.findByText('释放失败：服务器炸了', { selector: '.flash--bad *' })).toBeInTheDocument();
  });

  it('★ 移出该店在第一个 msku 就失败 —— 调用次数本身能区分「停了」还是「继续了」（F4）', async () => {
    const releaseClaim = vi.fn(async () => { throw new ApiError(500, 'release_failed', '第一个就炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    await screen.findByText('500 release_failed');
    expect(releaseClaim).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toHaveTextContent('已释放 无');
    expect(screen.getByRole('status')).toHaveTextContent('MSKU-A 释放失败');
  });

  it('★ 单个 msku 释放失败走 ErrorDetail 并重载，不静默吞掉（F4）', async () => {
    const releaseClaim = vi.fn(async () => { throw new ApiError(500, 'release_failed', '释放失败：服务器炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getAllByRole('button', { name: '移出 msku' })[0]!);
    expect(await screen.findByText('500 release_failed')).toBeInTheDocument();
  });

  // ★ Task 22：店铺行的按钮原叫「移出货号」，其实只移这一家店的 msku —— 按钮名与动作对不上，
  //   改名「移出该店」；「移出货号」让给货号行（真的移出整个货号）。断言跟着改名，行为不变。
  it('★ 双击「移出该店」只跑一轮释放循环（I3）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const btn = within(block).getByRole('button', { name: '移出该店' });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await screen.findByRole('status');
    expect(mock.api.releaseClaim).toHaveBeenCalledTimes(2);
  });

  it('★ 双击「移出 msku」只发一次释放请求（I3）', async () => {
    const { mock } = await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const btn = within(block).getAllByRole('button', { name: '移出 msku' })[0]!;
    fireEvent.click(btn);
    fireEvent.click(btn);
    await screen.findByRole('status');
    expect(mock.api.releaseClaim).toHaveBeenCalledTimes(1);
  });

  // ★ Task 22：业主裁定 2026-09-27 推翻 F5 —— 原来这里钉「搁浅的采购格未删、待处理」。
  //   现在最后一个 msku 走了，采购格随之删掉；守的仍是同一件事：丢掉的东西必须点名，带值。
  it('★ 移出 msku 带走了货号：toast 点名丢弃的期望与计划采购（带值），不许吞掉', async () => {
    const releaseClaim = vi.fn(async () => ({
      released: { seller_sku: 'MSKU-A', sid: '11072' },
      dropped_cells: [{ period: '2026-10', expected_units: 180 }, { period: '2026-11', expected_units: null }],
      dropped_purchase_cells: [{ sku: 'SKU-1', period: '2026-10', planned_units: 24 },
                               { sku: 'SKU-1', period: '2026-11', planned_units: 22 },
                               { sku: 'SKU-1', period: '2026-12', planned_units: null }],
    }));
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getAllByRole('button', { name: '移出 msku' })[0]!);
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已移出 msku MSKU-A：丢弃期望 2 格（2026-10 180 件）');
    expect(status).toHaveTextContent('货号 SKU-1 已无 msku，一并移出');
    expect(status).toHaveTextContent('丢弃计划采购 3 格（2026-10 24 件、2026-11 22 件）');
    expect(status.textContent).not.toContain('待处理');
  });

  it('★ 移出 msku 没带走货号：toast 不提采购', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getAllByRole('button', { name: '移出 msku' })[0]!);
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已移出 msku MSKU-A：丢弃期望 3 格（2026-10 180 件、2026-11 200 件）');
    expect(status.textContent).not.toContain('计划采购');
    expect(screen.getByTestId('card-SKU-1')).toBeInTheDocument();
  });

  it('★ 移出该店：toast 用同一个动词，点名店与货号', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已移出该店 A4Pet-WM 的 SKU-1：msku 1 个，丢弃期望 3 格（MSKU-W 2026-10 60 件、');
    expect(status.textContent).not.toContain('计划采购');
    await waitFor(() => expect(screen.queryByTestId('block-90001-SKU-1')).toBeNull());
    expect(screen.getByTestId('card-SKU-1')).toBeInTheDocument();
  });

  it('★ 移出该店移走了货号的最后一家店：货号从表里消失，丢弃的计划采购点名', async () => {
    const grid = makeGrid();
    grid.demand = grid.demand.filter((d) => d.sid !== '90001');
    grid.forecast_store = grid.forecast_store.filter((s) => s.sid !== '90001');
    await renderGrid(grid);
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('已移出该店 A4Pet-US 的 SKU-1：msku 2 个');
    expect(status).toHaveTextContent('货号 SKU-1 已无 msku，一并移出：丢弃计划采购 3 格（2026-10 500 件）');
    await waitFor(() => expect(screen.queryByTestId('card-SKU-1')).toBeNull());
  });

  it('★ 货号行「移出货号」：一个调用移出全部店的 msku，双击只发一次（I3）；toast 点名丢弃的数，货号消失', async () => {
    const { mock } = await renderGrid();
    const row = await screen.findByTestId('purchase-row-SKU-1');
    const btn = within(row).getByRole('button', { name: '移出货号' });
    fireEvent.click(btn);
    fireEvent.click(btn);
    const status = await screen.findByRole('status');
    expect(mock.api.removeSku).toHaveBeenCalledTimes(1);
    expect(mock.api.removeSku).toHaveBeenCalledWith(1, 'SKU-1');
    expect(mock.api.releaseClaim).not.toHaveBeenCalled();
    expect(status).toHaveTextContent('已移出货号 SKU-1：msku 3 个，丢弃期望 9 格（MSKU-A 2026-10 180 件、');
    expect(status).toHaveTextContent('，丢弃计划采购 3 格（2026-10 500 件）');
    expect(status.textContent).not.toContain(' · ');
    await waitFor(() => expect(screen.queryByTestId('card-SKU-1')).toBeNull());
  });

  it('★ 三个移出按钮的字面就是动作名：货号行「移出货号」、店铺行「移出该店」，不是两个一样的「移出」', async () => {
    await renderGrid();
    const row = await screen.findByTestId('purchase-row-SKU-1');
    expect(within(row).getByRole('button', { name: '移出货号' })).toHaveTextContent(/^移出货号$/);
    const block = screen.getByTestId('block-11072-SKU-1');
    const store = within(block).getByRole('button', { name: '移出该店' });
    expect(store).toHaveTextContent(/^移出该店$/);
    expect(store.getAttribute('title')).toContain('该店');
  });

  it('★ 行头布局：移出按钮不与旗标同一行（悬停显形时不叠上去），名字可省略、全名在 title', async () => {
    await renderGrid();
    const row = await screen.findByTestId('purchase-row-SKU-1');
    const btn = within(row).getByRole('button', { name: '移出货号' });
    const name = row.querySelector('.ps-name')!;
    expect(name).toHaveAttribute('title', 'SKU-1');
    // 名字独占第一行；按钮与旗标同在第二行（ps-subline），不在名字那一行
    expect(btn.closest('.ps-subline')).not.toBeNull();
    expect(name.closest('.ps-sku')!.contains(btn)).toBe(false);
    const flags = row.querySelectorAll('.ps-flag');
    expect(flags.length).toBeGreaterThan(0);
    for (const f of flags) expect(f.closest('.ps-subline')).toBe(btn.closest('.ps-subline'));
    const block = screen.getByTestId('block-11072-SKU-1');
    const store = within(block).getByRole('button', { name: '移出该店' });
    expect(store.closest('.ps-subline')).not.toBeNull();
    expect(block.querySelector('.ps-store')!.contains(store)).toBe(false);
  });

  it('★ 同一货号的移出按钮互斥：「移出该店」在飞时，「移出货号」与「移出 msku」都置灰、点了也不发请求', async () => {
    let finish!: () => void;
    const gate = new Promise<void>((r) => { finish = r; });
    const { mock } = await renderGrid(makeGrid(), (m) => {
      const orig = m.api.releaseClaim;
      m.api.releaseClaim = vi.fn(async (...args: unknown[]) => { await gate; return orig(...args); });
    });
    const block = await screen.findByTestId('block-11072-SKU-1');
    const row = screen.getByTestId('purchase-row-SKU-1');
    fireEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    const skuBtn = within(row).getByRole('button', { name: '移出货号' });
    await waitFor(() => expect(skuBtn).toBeDisabled());
    for (const b of within(block).getAllByRole('button', { name: '移出 msku' })) expect(b).toBeDisabled();
    expect(within(screen.getByTestId('block-90001-SKU-1')).getByRole('button', { name: '移出该店' })).toBeDisabled();
    finish();
    await screen.findByRole('status');
    expect(mock.api.removeSku).not.toHaveBeenCalled();
  });

  it('★ 移出货号失败走 ErrorDetail 并重载，货号还在', async () => {
    const removeSku = vi.fn(async () => { throw new ApiError(500, 'remove_failed', '移出失败：服务器炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.removeSku = removeSku; });
    const row = await screen.findByTestId('purchase-row-SKU-1');
    await userEvent.click(within(row).getByRole('button', { name: '移出货号' }));
    expect(await screen.findByText('500 remove_failed')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('SKU-1 移出失败');
    expect(screen.getByTestId('card-SKU-1')).toBeInTheDocument();
  });

  it('计划采购是货号级：行里不带店铺；在途只读、未知不是 0', async () => {
    await renderGrid();
    const row = await screen.findByTestId('purchase-row-SKU-1');
    expect(within(row).queryByText('A4Pet-US')).toBeNull();
    expect(within(row).getAllByRole('gridcell')).toHaveLength(3);
    expect(within(row).getByTestId('transit-SKU-1-2026-10')).toHaveTextContent('80');
    expect(within(row).getByTestId('transit-SKU-1-2026-11')).toHaveTextContent('—');
    expect(screen.getByText('在途未分摊到店铺')).toBeInTheDocument();
  });

  it('★ 只有在途、不在 purchase 里的货号也有卡（F7/F8），采购格只读', async () => {
    const grid = makeGrid();
    grid.forecast_sku.push(transitRow('SKU-2', P[0]!, 15));
    await renderGrid(grid);
    const row = await screen.findByTestId('purchase-row-SKU-2');
    // ★ 在途只有 forecast_sku 一个来源（F6 的两源核对已随旧块退役）：上屏的就是它
    expect(within(row).getByTestId('transit-SKU-2-2026-10')).toHaveTextContent('15');
    expect(within(row).getByTestId('transit-SKU-2-2026-11')).toHaveTextContent('—');
    expect(within(row).queryAllByRole('gridcell')).toHaveLength(0);
  });

  it('★ 在途一件都没并进库存', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    // 10 月期末 200 + 在途 80 = 280 —— 任何一格库存都不许是它
    const stocks = within(block).getAllByTestId('closing-sku').map((e) => e.textContent);
    expect(stocks[0]).toBe('200');
    expect(stocks).not.toContain('280');
  });

  it('★ 删除失败留下的错误不被随后一次成功保存抹掉（T4 R2）', async () => {
    const releaseClaim = vi.fn(async () => { throw new ApiError(500, 'release_failed', '释放失败：服务器炸了'); });
    await renderGrid(makeGrid(), (m) => { m.api.releaseClaim = releaseClaim; });
    const block = await screen.findByTestId('block-11072-SKU-1');
    await userEvent.click(within(block).getAllByRole('button', { name: '移出 msku' })[0]!);
    expect(await screen.findByText('500 release_failed')).toBeInTheDocument();

    await fill(within(block).getByTestId('cell-MSKU-A-11072-2026-11'), '12');
    await waitFor(() => expect(within(block).getByTestId('cell-MSKU-A-11072-2026-11')).toHaveTextContent('12'));
    expect(screen.getByText('500 release_failed')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.queryByText('500 release_failed')).toBeNull();
  });

  it('★ 取店铺挂了，计划抬头照常出数（I6）', async () => {
    await renderGrid(makeGrid(), (m) => {
      m.api.listSellers = async () => { throw new ApiError(503, 'sellers_unavailable', '店铺镜像取不到'); };
    });
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    expect(await screen.findByText('503 sellers_unavailable')).toBeInTheDocument();
  });

  it('★ 取计划抬头挂了，矩阵照常出数（I6）', async () => {
    await renderGrid(makeGrid(), (m) => {
      m.api.getPlan = async () => { throw new ApiError(500, 'plan_head_failed', '抬头取不到'); };
    });
    expect(await screen.findByTestId('plan-sheet')).toBeInTheDocument();
    expect(screen.getByTestId('block-11072-SKU-1')).toBeInTheDocument();
    expect(screen.getByText('500 plan_head_failed')).toBeInTheDocument();
  });

  // ★ 复核 R1 第 2 点：提前期是第四个独立读（与 plan/grid/sellers 同一条 I6 纪律），
  //   挂了照 planErr/gridErr/sellersErr 的样子走 ErrorDetail，矩阵/抬头照常出数
  it('★ 取提前期挂了，矩阵与抬头照常出数（I6）', async () => {
    await renderGrid(makeGrid(), (m) => {
      m.api.getLeadTime = async () => { throw new ApiError(503, 'lead_time_unavailable', '提前期取不到'); };
    });
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    expect(await screen.findByTestId('block-11072-SKU-1')).toBeInTheDocument();
    expect(screen.getByText('503 lead_time_unavailable')).toBeInTheDocument();
    // ★ 挂了就不画那一条 bar —— 不许拿一份挂号数据硬凑出「改」按钮
    expect(screen.queryByRole('button', { name: '改提前期' })).toBeNull();
  });

  // ★ 复核 R1 第 1 点：真后端失败（不是客户端校验）要走 fail()/ErrorDetail，
  //   err.fields 一起上屏，且不许 push 成功 toast
  it('★ 提前期保存被后端拒绝：ErrorDetail 点名 code + fields，不 push 成功 toast', async () => {
    const putLeadTime = vi.fn(async () => {
      throw new ApiError(409, 'lead_time_conflict', '有一版提前期还没生效', { pending_from: '2026-09-25' });
    });
    await renderGrid(makeGrid(), (m) => { m.api.putLeadTime = putLeadTime; });
    await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
    await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));

    expect(await screen.findByText('409 lead_time_conflict')).toBeInTheDocument();
    // ★ hint 在 ErrorDetail 与失败 toast 里各出现一份，都在也是对的——只要不是「已保存」
    expect(screen.getAllByText('有一版提前期还没生效').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('2026-09-25')).toBeInTheDocument();   // ★ fields 没被吞掉
    expect(screen.queryByText(/已保存/)).toBeNull();
  });

  it('★ 对不上的行要点名；数据干净时丢弃区不渲染（不是渲染一个空框）', async () => {
    await renderGrid();
    await screen.findByTestId('block-11072-SKU-1');
    expect(screen.queryByTestId('orphans')).toBeNull();

    const dirty = makeGrid();
    dirty.forecast_sku[0] = { ...dirty.forecast_sku[0]!, in_transit: 999 };   // 总数 ≠ 组成
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
    expect(screen.getAllByTestId('closing-na').length).toBeGreaterThan(0);
    // ★ 预测库存已含计划采购到货：图例说「含」并回显提前期，旧的「未计」必须消失
    expect(screen.getByText('库存含计划采购到货（生产 30 天 + 货运 60 天）')).toBeInTheDocument();
    expect(screen.queryByText('未计本计划采购')).toBeNull();
    expect(screen.getByText('在途未分摊到店铺')).toBeInTheDocument();
    expect(screen.queryByText('不可求和')).toBeNull();
    expect(screen.queryByTestId('orphans')).toBeNull();
  });

  it('★ OP-17：真 mock 上改计划采购 → 该货号在途变 —，不留改之前的旧数', async () => {
    vi.resetModules();
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
    const { PlanGrid } = await import('./PlanGrid');
    render(
      <MemoryRouter initialEntries={['/plans/1']}>
        <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
      </MemoryRouter>,
    );
    const row = await screen.findByTestId('purchase-row-DCC1800264G1');
    expect(within(row).getByTestId('transit-DCC1800264G1-2026-10')).toHaveTextContent('700');
    await fill(gridcell(row, '计划采购量 DCC1800264G1 2026-12'), '321');
    await waitFor(() => expect(within(screen.getByTestId('purchase-row-DCC1800264G1'))
      .getByTestId('transit-DCC1800264G1-2026-10')).toHaveTextContent('—'));
  });

  it('★ 真 fixture：没有任何一格带外推标签（spec §3.3：速率模型不外推）', async () => {
    // ★ F16：monthly_estimate 退役，base_rate × 天数对任何月份都算得出，fixture 重固化后
    //   不会再产生外推那一格。标签本身的渲染逻辑不删——上面 makeGrid() 的合成数据仍在喂
    //   system_extrapolated:true 给 PlanSheet，这条测试只改成断言真 fixture 上找不到它。
    vi.resetModules();
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
    const { PlanGrid } = await import('./PlanGrid');
    render(
      <MemoryRouter initialEntries={['/plans/1']}>
        <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('block-11094-DCC1800264G1');
    expect(screen.queryAllByLabelText('系统外推')).toHaveLength(0);
  });
});
