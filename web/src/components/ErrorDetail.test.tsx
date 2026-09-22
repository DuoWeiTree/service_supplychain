import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorDetail } from './ErrorDetail';
import { ApiError } from '../api/client';

describe('ErrorDetail', () => {
  it('★ 逐项点名，不说「保存失败」', () => {
    render(<ErrorDetail err={new ApiError(409, 'msku_already_claimed', 'MSKU-C 已被占用',
      { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' })} />);
    expect(screen.getByText('MSKU-C 已被占用')).toBeInTheDocument();
    expect(screen.getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(screen.getByText('ops.li')).toBeInTheDocument();
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
