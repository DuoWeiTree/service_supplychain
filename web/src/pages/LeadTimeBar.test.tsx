import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return { ...actual, api: { getLeadTime: vi.fn(async () => ({ route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15, valid_from: { making: '2026-01-01', shipping: '2026-01-01' } })),
                             putLeadTime: vi.fn(async (i: object) => ({ route: 'oversea_sea', make_days: 10, ship_days: 20, anchor_day: 15, valid_from: { making: '2026-09-24', shipping: '2026-09-24' }, changed: [], ...i })) } };
});
import { api } from '../api';
import { LeadTimeBar } from './LeadTimeBar';

describe('提前期设置（spec §4.4）', () => {
  // ★ 与 F16（页头元数据）同一条纪律：整屏禁 ' · ' 元串（e2e/copyDiscipline.test.tsx
  //   的 `/plans/1` 一屏会扫到这一块）—— 三段各自成句，不用 findByText 找单一拼接串
  it('显示当前值', async () => {
    render(<LeadTimeBar onChanged={vi.fn(async () => {})} />);
    const meta = await screen.findByTestId('lt-bar-meta');
    expect(meta.textContent).not.toContain('·');
    expect(meta).toHaveTextContent('生产');
    expect(meta).toHaveTextContent('30');
    expect(meta).toHaveTextContent('货运');
    expect(meta).toHaveTextContent('60');
    expect(meta).toHaveTextContent('下单月');
    expect(meta).toHaveTextContent('15');
  });

  it('改完立即重算（14 B12）：PUT 之后调 onChanged 重拉网格', async () => {
    const onChanged = vi.fn(async () => {});
    render(<LeadTimeBar onChanged={onChanged} />);
    await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
    await userEvent.clear(screen.getByLabelText('生产天数'));
    await userEvent.type(screen.getByLabelText('生产天数'), '10');
    await userEvent.clear(screen.getByLabelText('货运天数'));
    await userEvent.type(screen.getByLabelText('货运天数'), '20');
    await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
    expect(api.putLeadTime).toHaveBeenCalledWith({ make_days: 10, ship_days: 20 });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it.each([['-1', '不是非负整数'], ['3.5', '不是非负整数'], ['721', '不能超过 720 天']])(
    '输入 %s 就地报错、不发请求', async (raw, why) => {
      render(<LeadTimeBar onChanged={vi.fn(async () => {})} />);
      await userEvent.click(await screen.findByRole('button', { name: '改提前期' }));
      await userEvent.clear(screen.getByLabelText('生产天数'));
      await userEvent.type(screen.getByLabelText('生产天数'), raw);
      await userEvent.click(screen.getByRole('button', { name: '保存提前期' }));
      expect(screen.getByRole('alert')).toHaveTextContent(why);
      expect(api.putLeadTime).not.toHaveBeenCalledWith(expect.objectContaining({ make_days: Number(raw) }));
    });
});
