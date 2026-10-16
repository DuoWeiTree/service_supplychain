import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AttentionQueue } from '../api/types';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

/** n 张单、各一项 */
const q = (n: number): AttentionQueue => ({ count: n, items: Array.from({ length: n }, (_, i) => ({
  attention_id: i + 1, po_id: i + 1, kind: 'needs_verify' as const, opened_at: '2026-10-09T10:00:00+08:00',
  custom_order_sn: `SCM-PO-${i + 1}`, supplier_id: 'S', timeline: `/v1/purchase-orders/${i + 1}/timeline` })) });

async function mount(listAttention: () => Promise<AttentionQueue>) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({ api: { listAttention }, ApiError }));
  const mod = await import('./AttentionBanner');
  const utils = render(<MemoryRouter><mod.AttentionBanner /></MemoryRouter>);
  await act(async () => {});
  return { ...utils, ...mod, ApiError };
}

describe('AttentionBanner', () => {
  it('count>0 → 红条点名张数，链到 /purchase，没有关闭按钮', async () => {
    await mount(async () => (q(3)));
    const region = screen.getByTestId('attention-region');
    expect(region).toHaveTextContent('⛔ 3 张采购单需要人工处理');
    expect(screen.getByRole('link', { name: '去处理' })).toHaveAttribute('href', '/purchase');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('★ 数的是单不是项：一张单同时开着两项（待核实 + 不一致）→ 1 张', async () => {
    const one = q(1);
    const item = one.items[0]!;
    await mount(async () => ({ count: 2, items: [item, { ...item, attention_id: 2, kind: 'landed_mismatch' }] }));
    const region = screen.getByTestId('attention-region');
    expect(region).toHaveTextContent('1 张采购单需要人工处理');
    // 按类型数的是项：两项各一
    expect(region).toHaveTextContent('下单结果待核实 1');
    expect(region).toHaveTextContent('领星明细不一致 1');
  });

  it('count=0 → 什么都不渲染', async () => {
    const { container } = await mount(async () => (q(0)));
    expect(container.textContent).toBe('');
  });

  it('★ 取数失败 → 不隐藏（空 ≠ 0）：「待处理数取不到」+ 错误明细', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { ApiError } = await import('../api/client');
    await mount(async () => { throw new ApiError(503, 'upstream_down', '后端不可达'); });
    expect(screen.getByText('待处理数取不到')).toBeInTheDocument();
    expect(screen.getByText('后端不可达')).toBeInTheDocument();
    expect(screen.getByText(/503 upstream_down/)).toBeInTheDocument();
    expect(warn).toHaveBeenCalled();
  });

  it('★ 非 ApiError 的失败（如 fetch 断了）同样不隐藏', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    await mount(async () => { throw new TypeError('fetch failed'); });
    expect(screen.getByText('待处理数取不到')).toBeInTheDocument();
  });

  it('refreshAttention() 重新取数：0 → 2 红条出现', async () => {
    let n = 0;
    const fn = vi.fn(async () => (q(n)));
    const { refreshAttention } = await mount(fn);
    expect(fn).toHaveBeenCalledTimes(1);
    n = 2;
    await act(async () => { refreshAttention(); });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('attention-region')).toHaveTextContent('2 张采购单需要人工处理');
  });

  it('★ 红条在 aria-live=polite 的 live region 里，且容器先于内容存在', async () => {
    let release!: (q: AttentionQueue) => void;
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      api: { listAttention: () => new Promise<AttentionQueue>((r) => { release = r; }) }, ApiError,
    }));
    const { AttentionBanner } = await import('./AttentionBanner');
    render(<MemoryRouter><AttentionBanner /></MemoryRouter>);
    await act(async () => {});
    const region = screen.getByTestId('attention-region');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region.textContent).toBe('');
    await act(async () => { release(q(4)); });
    expect(screen.getByTestId('attention-region')).toBe(region);
    expect(region).toHaveTextContent('4 张采购单需要人工处理');
  });

  it('★ 先成功后刷新失败 → 切到失败态，不留旧红条', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    let fail = false;
    const { ApiError } = await import('../api/client');
    const { refreshAttention } = await mount(async () => {
      if (fail) throw new ApiError(502, 'bad_gateway', '网关错误');
      return q(2);
    });
    expect(screen.getByTestId('attention-region')).toHaveTextContent('2 张');
    fail = true;
    await act(async () => { refreshAttention(); });
    expect(screen.getByText('待处理数取不到')).toBeInTheDocument();
    expect(screen.getByTestId('attention-region')).not.toHaveTextContent('2 张');
    expect(screen.queryByRole('link', { name: '去处理' })).toBeNull();
  });
});
