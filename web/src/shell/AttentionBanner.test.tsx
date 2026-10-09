import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AttentionQueue } from '../api/types';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

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
    await mount(async () => ({ count: 3, items: [] }));
    const link = screen.getByRole('link', { name: /3 张采购单需要人工处理/ });
    expect(link).toHaveAttribute('href', '/purchase');
    expect(link.textContent).toContain('⛔');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('count=0 → 什么都不渲染', async () => {
    const { container } = await mount(async () => ({ count: 0, items: [] }));
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
    const fn = vi.fn(async () => ({ count: n, items: [] }));
    const { refreshAttention } = await mount(fn);
    expect(fn).toHaveBeenCalledTimes(1);
    n = 2;
    await act(async () => { refreshAttention(); });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('link', { name: /2 张采购单需要人工处理/ })).toBeInTheDocument();
  });
});
