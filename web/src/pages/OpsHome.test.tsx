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
    // ★ 穷尽相等，不是举例排除 —— 举例排除拦不住"已确认/已完结"这类漏在名单外的态悄悄混进来
    const labels = Array.from(counts.querySelectorAll('.kpi__d')).map((el) => el.textContent);
    expect(new Set(labels)).toEqual(new Set(['未提交', '已提交', '已撤销']));
    expect(counts.textContent).not.toContain('0');
  });

  it('计划列表里 state===null 的行渲染"未提交"芯片，不是"已撤销"或空白', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    const row = within(table).getByText('2026 Q4 销售计划').closest('tr');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText('未提交')).toHaveClass('chip', 'chip--dim');
    expect(within(row as HTMLElement).queryByText('已撤销')).toBeNull();
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
