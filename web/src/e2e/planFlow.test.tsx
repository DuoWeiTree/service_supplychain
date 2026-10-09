import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../testing/fixtures/grid-1.json';
import type { GridResponse } from '../api/types';

const G = gridFixture as GridResponse;
/** ★ 不写死后端 seed 出来的名字与数字，只从 fixture 里认形态 */
const FIRST_INV = G.forecast_store.find((s) => s.state === 'ok' && s.closing !== null)!;
const EDITED = G.demand.find((d) =>
  d.sku === FIRST_INV.sku && d.sid === FIRST_INV.sid && d.period === FIRST_INV.period)!;
const FIRST_SKU = G.purchase[0]!.sku;
const LAST_PERIOD = G.periods[G.periods.length - 1]!;

beforeEach(() => { vi.resetModules(); });

async function app(entry: string) {
  const { ApiError } = await import('../api/client');
  const { createMemoryBackend } = await import('../testing/memoryBackend');
  vi.doMock('../api', () => ({ api: createMemoryBackend(), ApiError }));
  const [{ OpsHome }, { PlanGrid }, { PlanAdd }, { PlanRevs }] = await Promise.all([
    import('../pages/OpsHome'), import('../pages/PlanGrid'),
    import('../pages/PlanAdd'), import('../pages/PlanRevs'),
  ]);
  const utils = render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<OpsHome />} />
        <Route path="/plans/:planId" element={<PlanGrid />} />
        <Route path="/plans/:planId/add" element={<PlanAdd />} />
        <Route path="/plans/:planId/revs" element={<PlanRevs />} />
      </Routes>
    </MemoryRouter>,
  );
  // ★ app() 是 async 函数：调用方 `await` 它的那一下就是一次微任务跳变，
  //   只发一个请求的页面（PlanRevs 的 listRevs）会正好在 act() 之外落地 → act() 警告。
  //   空的 async act 把那批待落地的更新收进来，行为不变。
  await act(async () => {});
  return utils;
}

describe('判据① · 建 → 加货品 → 填两种量 → 提交 → 铸出 rev', () => {
  it('① 建：新建后走到网格', async () => {
    await app('/');
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '新建' }));
    // ★ brief 一稿断言 `nav-to` testid：那是 OpsHome.test.tsx 里"裸 MemoryRouter、无 <Routes>"
    //   场景下才会停留可见的产物——这里接了真的 <Routes>，create() 里 `navigate()` 一响，
    //   OpsHome 本身就整个卸载，`nav-to` 从未来得及停留在 DOM 里（查证：跑出来是超时，
    //   而不是内容不对）。判据①要的是"新建后走到网格"这件事本身，不是内部实现细节，
    //   所以改成认网格页确实渲染出了刚填的标题——不依赖 mock 派发的自增 plan_id 数字。
    expect(await screen.findByRole('heading', { name: '2027 Q1 销售计划' })).toBeInTheDocument();
    expect(screen.getByTestId('plan-sheet')).toBeInTheDocument();
  });

  it('② 加货品：认领 msku 后可返回网格', async () => {
    await app('/plans/1/add');
    await userEvent.type(screen.getByLabelText('搜货号'), 'MSKU');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    const boxes = await screen.findAllByRole('checkbox');
    const free = boxes.find((b) => !(b as HTMLInputElement).disabled)!;
    await userEvent.click(free);
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    // ★ 实现里的动词是「已添加」（PlanAdd.tsx：按钮「添加」→ toast/报告都叫「已添加」），
    //   不是 brief 一稿写的「成功」——查实现，不查旧文档（team-lead 裁定）
    expect(await screen.findByTestId('claim-report')).toHaveTextContent(/已添加 \d+/);
    expect(screen.getByRole('link', { name: '返回网格' })).toHaveAttribute('href', '/plans/1');
  });

  it('③④ 填两种量 → 提交 → 铸出 rev，且两种量各自生效', async () => {
    await app('/plans/1');
    await screen.findByTestId('plan-sheet');
    const block = await screen.findByTestId(`block-${FIRST_INV.sid}-${FIRST_INV.sku}`);
    // 多 msku 的店铺默认展开，单 msku 的店铺直接在店铺行上填 —— 都不用先点展开

    // 期望销量（msku 级输入）。★ testid 带 sid（`cell-${seller_sku}-${sid}-${period}`），
    //   brief 一稿漏了 sid —— 同一 seller_sku 在不同 sid 下是不同 listing（CLAUDE.md 铁律）
    // ★ M5（终审）：断言钉在 closing-sku 这个点上，不打在整个 sku-cell 上 ——
    //   同一格里还有期初、as_of、预估、Σ 期望，旁边那串字会把错的期望值托住
    const skuCell = within(block).getByTestId(`sku-cell-${FIRST_INV.period}`);
    expect(within(skuCell).getByTestId('closing-sku')).toHaveTextContent(String(FIRST_INV.closing));

    const cell = within(block).getByTestId(`cell-${EDITED.seller_sku}-${EDITED.sid}-${FIRST_INV.period}`);
    await userEvent.click(cell);
    await userEvent.keyboard('10{Enter}');

    await waitFor(() => expect(within(screen.getByTestId(`block-${FIRST_INV.sid}-${FIRST_INV.sku}`))
      .getByTestId(`cell-${EDITED.seller_sku}-${EDITED.sid}-${FIRST_INV.period}`)).toHaveTextContent('10'));

    // 计划采购量（货号 × 月，不带店铺）
    const purchase = within(screen.getByTestId(`purchase-row-${FIRST_SKU}`))
      .getByRole('gridcell', { name: `计划采购量 ${FIRST_SKU} ${LAST_PERIOD}` });
    await userEvent.click(purchase);
    await userEvent.keyboard('300{Enter}');

    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 恒等式，不是写死的数：铸出 + 跳过 = 货号×月 的格子数
    const lines = Number(/铸出 (\d+) 条/.exec(report.textContent!)![1]);
    const skipped = Number(/跳过 (\d+) 条/.exec(report.textContent!)![1]);
    expect(lines + skipped).toBe(G.purchase.length);
    expect(lines).toBeGreaterThanOrEqual(1);   // ★ 刚填的 300 至少让一个月铸得出来

    // ★ T8（parked）：「铸出了一版」此前只有提交响应这一个证人 —— 那是同一次调用自己说的。
    //   去版本页回查一遍：真的有 rev 1，且它就是在流转的那一版。
    await userEvent.click(within(report).getByRole('link', { name: '去版本' }));
    const row = await screen.findByTestId('rev-1');
    expect(within(row).getByText('流转中')).toBeInTheDocument();
    expect(row).toHaveTextContent(String(lines));
  });

  it('⑤ 版本页看得到流转中与当前使用', async () => {
    await app('/plans/2/revs');
    const row = await screen.findByTestId('rev-2');
    expect(within(row).getByText('当前使用')).toBeInTheDocument();
    expect(within(row).getByText('流转中')).toBeInTheDocument();
  });
});

describe('★ Task 22（业主裁定 2026-09-27）：内存替身下移走货号的最后一个 msku，货号也消失', () => {
  it('单店单 msku 的货号「移出该店」→ 卡片消失，丢弃的计划采购点名', async () => {
    const lone = G.demand.map((d) => d.sku)
      .find((sku) => new Set(G.demand.filter((d) => d.sku === sku).map((d) => `${d.seller_sku}/${d.sid}`)).size === 1)!;
    const sid = G.demand.find((d) => d.sku === lone)!.sid;
    await app('/plans/1');
    const block = await screen.findByTestId(`block-${sid}-${lone}`);
    await userEvent.click(within(block).getByRole('button', { name: '移出该店' }));
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent(`货号 ${lone} 已无 msku，一并移出：丢弃计划采购 3 格`);
    await waitFor(() => expect(screen.queryByTestId(`card-${lone}`)).toBeNull());
  });
});
