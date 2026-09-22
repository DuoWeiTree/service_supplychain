import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

/** 两条已被裁定过的文案纪律的守卫（终审 I7 + 建议 4）。
 *
 *  ★ 为什么要守：这两条在本分支各被裁定并修过一次（F16 让页头三段各自成句、
 *    Task 5 让 PlanAdd 的动词一名到底），而写在裁定之前的那两屏没有回头统一 ——
 *    OpsHome 与 usePlanGridSaves 就这样活到了终审。没有守卫，下一屏还会长回来。 */

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

async function app(entry: string) {
  const { ApiError } = await import('../api/client');
  const { createMockApi } = await import('../api/mock');
  vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
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
  return utils;
}

describe('文案纪律', () => {
  // ★ 四屏都要扫：终审抓到的 5 处里有 3 处在顶栏的 actor 下拉里（每一屏都有它）
  const screens: [string, string][] = [
    ['我的计划', '/'],
    ['计划编辑', '/plans/1'],
    ['添加货品', '/plans/1/add'],
    ['版本', '/plans/2/revs'],
  ];

  for (const [name, entry] of screens) {
    it(`★ ${name}：整屏一个 \` · \` 元串都不许有`, async () => {
      const { unmount } = await app(entry);
      // 等这一屏真的出了内容再扫 —— 空屏上扫不出元串，那是假绿
      await screen.findByRole('heading');
      const text = document.body.textContent ?? '';
      expect(text.length).toBeGreaterThan(50);
      expect(text).not.toContain(' · ');
      unmount();
    });
  }

  // ★ 一个动作一个名：按钮上的动词与 toast 里的动词必须同源。
  //   「删除货号」→「已移出 N 个 msku」是两个词，人会以为是两件事。
  it('★ 移出货号：按钮说「移出」，toast 也说「已移出」', async () => {
    await app('/plans/1');
    const block = await screen.findAllByTestId(/^block-/);
    const btn = within(block[0]!).getByRole('button', { name: '移出货号' });
    await userEvent.click(btn);
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('已移出');
    expect(toast.textContent).not.toContain('删除');
  });

  it('★ 添加货品：按钮说「添加」，报告也说「已添加」', async () => {
    await app('/plans/1/add');
    await userEvent.type(screen.getByLabelText('搜货号'), 'MSKU');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    const boxes = await screen.findAllByRole('checkbox');
    await userEvent.click(boxes.find((b) => !(b as HTMLInputElement).disabled)!);
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('已添加');
    expect(report.textContent).not.toContain('已认领');
  });

  it('★ 新建销售计划：按钮说「新建」，表单提交按钮也说「新建」，不混「创建」', async () => {
    await app('/');
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建' })).toBeNull();
  });
});
