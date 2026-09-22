import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { OpsHome } from './OpsHome';

const renderHome = () => render(<MemoryRouter><OpsHome /></MemoryRouter>);

describe('运营首页', () => {
  it('只渲染够得着的三态，其余★不渲染（不是 0）', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    expect(within(counts).getByText('未提交')).toBeInTheDocument();
    expect(within(counts).getByText('已提交')).toBeInTheDocument();
    expect(within(counts).getByText('已撤销')).toBeInTheDocument();
    // ★ 阶段 A 够不着的：一个字都不许出现，包括写成 0
    for (const gone of ['已下单', '准备排货', '已排货', '进行中', '已提交未确认']) {
      expect(within(counts).queryByText(gone)).toBeNull();
    }
    expect(counts.textContent).not.toContain('0');
  });

  it('★ 接口说哪些态够不着，屏上就一个都不许有 —— 名单由接口给，不在前端硬编码', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    const unreachable = JSON.parse(screen.getByTestId('unreachable').textContent!) as string[];
    expect(unreachable.length).toBeGreaterThan(0);          // ★ 空名单等于这条断言没跑
    for (const s of unreachable) expect(counts.textContent).not.toContain(s);
  });

  it('★ 完结的计划不显示，但被挡掉的两侧都要有个数', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    expect(within(table).queryByText('2026 Q1 首发计划')).toBeNull();
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已完结 1 张');
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已归档 2 张');
  });

  it('两栏：未提交 / 提交后又改过（第二栏点名是跟哪一版比的）', async () => {
    renderHome();
    expect(await screen.findByTestId('never-submitted')).toHaveTextContent('2026 Q4 销售计划');
    const changed = screen.getByTestId('changed-since-submit');
    expect(changed).toHaveTextContent('2026 Q3 补货计划');
    expect(changed).toHaveTextContent('rev 2');
  });

  it('按状态筛选，列表跟着变', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.selectOptions(screen.getByLabelText('状态'), '已撤销');
    const table = screen.getByTestId('plan-list');
    expect(within(table).getByText('2026 Q2 清库计划')).toBeInTheDocument();
    expect(within(table).queryByText('2026 Q4 销售计划')).toBeNull();
  });

  it('新建销售计划 → 走到网格', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByTestId('nav-to')).toHaveTextContent('/plans/5');
  });
});
