import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    api: { putLeadTime: vi.fn(async (i: object) => ({ route: 'oversea_sea', make_days: 10, ship_days: 20, anchor_day: 15, valid_from: { making: '2026-09-24', shipping: '2026-09-24' }, changed: [], ...i })) },
  };
});
import { api } from '../api';
import { getToasts } from '../shell/toastStore';
import { LeadTimeBar } from './LeadTimeBar';

const LT = { route: 'oversea_sea' as const, make_days: 30, ship_days: 60, anchor_day: 15,
  valid_from: { making: '2026-01-01', shipping: '2026-01-01' } };

describe('提前期设置（spec §4.4）', () => {
  it('显示当前值', () => {
    render(<LeadTimeBar leadTime={LT} onChanged={vi.fn(async () => {})} fail={vi.fn()} succeed={vi.fn()} />);
    const meta = screen.getByTestId('lt-bar-meta');
    // ★ 与页头元数据同一条纪律（F16）：整屏禁 ' · ' 元串——三段各自成句，不拼接单一串比对
    expect(meta.textContent).not.toContain('·');
    expect(meta).toHaveTextContent('生产');
    expect(meta).toHaveTextContent('30');
    expect(meta).toHaveTextContent('货运');
    expect(meta).toHaveTextContent('60');
    expect(meta).toHaveTextContent('下单月');
    expect(meta).toHaveTextContent('15');
  });

  it('改完立即重算（14 B12）：PUT 之后调 onChanged 重拉网格，并按钮动词=toast动词 push 成功 toast', async () => {
    const onChanged = vi.fn(async () => {});
    const succeed = vi.fn();
    const before = getToasts().length;
    render(<LeadTimeBar leadTime={LT} onChanged={onChanged} fail={vi.fn()} succeed={succeed} />);
    await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
    await userEvent.clear(screen.getByLabelText('生产天数'));
    await userEvent.type(screen.getByLabelText('生产天数'), '10');
    await userEvent.clear(screen.getByLabelText('货运天数'));
    await userEvent.type(screen.getByLabelText('货运天数'), '20');
    await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
    expect(api.putLeadTime).toHaveBeenCalledWith({ make_days: 10, ship_days: 20 });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(succeed).toHaveBeenCalledWith('lead-time');
    // ★ 按钮说「保存」，toast 也说「已保存」——同一个动作一个名
    const toasts = getToasts().slice(before);
    expect(toasts.some((t) => t.kind === 'ok' && t.text.includes('已保存') && t.text.includes('生产 10 天') && t.text.includes('货运 20 天'))).toBe(true);
  });

  it('★ API 拒绝（真后端失败，不是客户端校验）：走 fail()，不 push 成功 toast', async () => {
    const rejected = { status: 409, error: 'lead_time_conflict', hint: '有一版还没生效',
      fields: { pending_from: '2026-09-25' } };
    (api.putLeadTime as ReturnType<typeof vi.fn>).mockRejectedValueOnce(rejected);
    const fail = vi.fn();
    const succeed = vi.fn();
    const before = getToasts().length;
    render(<LeadTimeBar leadTime={LT} onChanged={vi.fn(async () => {})} fail={fail} succeed={succeed} />);
    await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
    await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
    await waitFor(() => expect(fail).toHaveBeenCalledWith('lead-time', rejected));
    expect(succeed).not.toHaveBeenCalled();
    // ★ 页尾 ErrorDetail 靠 fail() 传下去的这个 err 对象把 status/error/fields 原样显示——
    //   fail() 拿到的是完整对象（未见半路被拆成只剩 hint），components/ErrorDetail.test.tsx
    //   已经单独证明 ErrorDetail 会把 status/error/fields 都画出来
    const toasts = getToasts().slice(before);
    expect(toasts.some((t) => t.kind === 'ok')).toBe(false);
  });

  it.each([
    ['-1', '不是非负整数'], ['3.5', '不是非负整数'], ['721', '不能超过 720 天'], ['', '必填'],
  ])(
    '输入 %s 就地报错、不发请求', async (raw, why) => {
      render(<LeadTimeBar leadTime={LT} onChanged={vi.fn(async () => {})} fail={vi.fn()} succeed={vi.fn()} />);
      await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
      await userEvent.clear(screen.getByLabelText('生产天数'));
      if (raw !== '') await userEvent.type(screen.getByLabelText('生产天数'), raw);
      await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
      expect(screen.getByRole('alert')).toHaveTextContent(why);
      expect(api.putLeadTime).not.toHaveBeenCalledWith(expect.objectContaining({ make_days: Number(raw) }));
    });
});
