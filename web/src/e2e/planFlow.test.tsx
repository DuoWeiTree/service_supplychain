import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import type { GridResponse } from '../api/types';

const G = gridFixture as GridResponse;
/** ★ 不写死后端 seed 出来的名字与数字，只从 fixture 里认形态 */
const FIRST_INV = G.inventory.find((i) => i.basis.closing_reason !== 'not_applicable' && i.onhand !== null)!;
const SIBLINGS = G.demand.filter((d) =>
  d.sku === FIRST_INV.sku && d.sid === FIRST_INV.sid && d.period === FIRST_INV.period);
const EDITED = SIBLINGS[0]!;
const OTHERS = SIBLINGS.slice(1);
const FIRST_SKU = G.purchase[0]!.sku;
const LAST_PERIOD = G.periods[G.periods.length - 1]!;

beforeEach(() => { vi.resetModules(); });

async function app(entry: string) {
  const { ApiError } = await import('../api/client');
  const { createMockApi } = await import('../api/mock');
  vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  const [{ OpsHome }, { PlanGrid }, { PlanAdd }, { PlanRevs }] = await Promise.all([
    import('../pages/OpsHome'), import('../pages/PlanGrid'),
    import('../pages/PlanAdd'), import('../pages/PlanRevs'),
  ]);
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<OpsHome />} />
        <Route path="/plans/:planId" element={<PlanGrid />} />
        <Route path="/plans/:planId/add" element={<PlanAdd />} />
        <Route path="/plans/:planId/revs" element={<PlanRevs />} />
      </Routes>
    </MemoryRouter>,
  );
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
    expect(screen.getByTestId('purchase-block')).toBeInTheDocument();
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
    await screen.findByTestId('purchase-block');
    const block = await screen.findByTestId(`block-${FIRST_INV.sid}-${FIRST_INV.sku}`);
    // ★ 可及名称带店铺与货号（F11 裁定），不是裸的「展开」——用前缀正则匹配
    await userEvent.click(within(block).getByRole('button', { name: /^展开 / }));

    // 期望销量（msku 级输入）。★ testid 带 sid（`cell-${seller_sku}-${sid}-${period}`），
    //   brief 一稿漏了 sid —— 同一 seller_sku 在不同 sid 下是不同 listing（CLAUDE.md 铁律）
    const cell = within(block).getByTestId(`cell-${EDITED.seller_sku}-${EDITED.sid}-${FIRST_INV.period}`);
    const demandInput = within(cell).getByRole('textbox');
    await userEvent.clear(demandInput);
    await userEvent.type(demandInput, '10');
    await userEvent.tab();

    // ★ 库存画在店铺·货号行上；期望值从 fixture 现算，不写死
    const othersUnknown = OTHERS.some((d) => d.effective_units === null);
    const expected = othersUnknown
      ? '—'
      : String(FIRST_INV.onhand! - (10 + OTHERS.reduce((a, d) => a + (d.effective_units as number), 0)));
    expect(await within(block).findByTestId(`sku-cell-${FIRST_INV.period}`)).toHaveTextContent(expected);

    // 计划采购量（货号 × 月，不带店铺）
    const purchase = within(screen.getByTestId('purchase-block'))
      .getByLabelText(`计划采购量 ${FIRST_SKU} ${LAST_PERIOD}`);
    await userEvent.clear(purchase);
    await userEvent.type(purchase, '300');
    await userEvent.tab();

    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 恒等式，不是写死的数：铸出 + 跳过 = 货号×月 的格子数
    const lines = Number(/铸出 (\d+) 条/.exec(report.textContent!)![1]);
    const skipped = Number(/跳过 (\d+) 条/.exec(report.textContent!)![1]);
    expect(lines + skipped).toBe(G.purchase.length);
    expect(lines).toBeGreaterThanOrEqual(1);   // ★ 刚填的 300 至少让一个月铸得出来
  });

  it('⑤ 版本页看得到流转中与当前使用', async () => {
    await app('/plans/2/revs');
    const row = await screen.findByTestId('rev-2');
    expect(within(row).getByText('当前使用')).toBeInTheDocument();
    expect(within(row).getByText('流转中')).toBeInTheDocument();
  });
});
