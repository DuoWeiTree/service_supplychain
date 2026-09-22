import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorDetail } from './ErrorDetail';
import { ApiError } from '../api/client';

describe('ErrorDetail', () => {
  it('★ 逐项点名，不说「保存失败」', () => {
    // ★ team-lead 09-22 裁定（找到 5）：真实后端 409 的 claimed_by 嵌套一层
    //  （api/ui/plans.py:153-156，现已带 title），不是目录端点那种把 plan_id/title/actor
    //  拍平在顶层的写法——之前这条测试的输入形状抄错了目录端点的样子。
    const { container } = render(<ErrorDetail err={new ApiError(409, 'msku_already_claimed', 'MSKU-C 已被占用',
      { seller_sku: 'MSKU-C', sid: '11094',
        claimed_by: { plan_id: 2, actor: 'ops.li', title: '2026 Q3 补货计划' } })} />);
    expect(screen.getByText('MSKU-C 已被占用')).toBeInTheDocument();
    expect(screen.getByText('MSKU-C')).toBeInTheDocument();   // seller_sku 是顶层字段，独立一行
    // ★ claimed_by 嵌套，组件把它整体 JSON.stringify 成一行——review 结论「组件本身是对的」，
    //   这里只验证点名信息还在文本里认得出来，不强求拆成独立的 li（那是后续 Task 的事）
    expect(container.textContent).toContain('2026 Q3 补货计划');
    expect(container.textContent).toContain('ops.li');
    expect(screen.queryByText(/失败$/)).toBeNull();
  });

  it('★ 409 与 400 的样子不同 —— 一个该刷新重来，一个该改表单', () => {
    const { container: conflict } = render(
      <ErrorDetail err={new ApiError(409, 'rev_in_flight', '先处理 rev 2', { in_flight_rev: 2 })} />);
    const { container: bad } = render(
      <ErrorDetail err={new ApiError(400, 'reason_required', '撤销必须填理由', {})} />);
    expect(conflict.querySelector('.flash--bad')).not.toBeNull();
    expect(conflict.querySelector('.gate__code')?.textContent).toBe('409 rev_in_flight');
    expect(bad.querySelector('.gate__code')?.textContent).toBe('400 reason_required');
  });
});
