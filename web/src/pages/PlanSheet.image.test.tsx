import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GridResponse } from '../api/types';
import { PlanSheet } from './PlanSheet';
import { buildGridModel } from './planGridModel';
import { LABEL_GRID, LABEL_SELLERS } from './__fixtures__/labelGrid';

afterEach(cleanup);

/** LABEL_GRID 里 `forecast_sku` 全部固定 `has_image: false`（label-grid 只测标签/依据，
 *  见 __fixtures__/labelGrid.ts 的注释）——这里克隆一份改成想要的状态，不碰冻结的 JSON。 */
function withHasImage(has: boolean): GridResponse {
  const g = structuredClone(LABEL_GRID) as GridResponse;
  g.forecast_sku = g.forecast_sku.map((r) => ({ ...r, has_image: has }));
  return g;
}

function renderSheet(grid: GridResponse) {
  const model = buildGridModel(grid, LABEL_SELLERS);
  expect(model.orphans).toEqual([]);
  render(<PlanSheet model={model} pending={new Set()} inputErr={{}} onDemand={vi.fn()}
                    onPurchase={vi.fn()} onRemoveSku={vi.fn()} onRemoveStore={vi.fn()} onRemoveMsku={vi.fn()} />);
}

describe('sku-image 计划 Task 4：按需预览', () => {
  it('has_image 为真的货号行有一个可键盘到达、带 aria-label 的图片入口', () => {
    renderSheet(withHasImage(true));
    const btn = screen.getByRole('button', { name: '查看 SKU-1 的主图' });
    btn.focus();
    expect(btn).toHaveFocus();
  });

  it('★ 负责人裁定 I-A 的证人：has_image 为假的行一个入口都没有', () => {
    renderSheet(withHasImage(false));
    expect(screen.queryByRole('button', { name: /查看 SKU-1 的主图/ })).not.toBeInTheDocument();
  });

  it('点开后 <img> 的 src 指向本仓的 /v1/skus/{sku}/image（不是 PM 的地址），alt 带货号', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '查看 SKU-1 的主图' }));

    const img = screen.getByAltText('SKU-1 主图') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/v1/skus/SKU-1/image');
    expect(img.getAttribute('src')).not.toMatch(/zestrade|pm\./); // ★ 反证：不是 PM 的地址
  });

  it('加载中有可读状态，不是一片空白', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '查看 SKU-1 的主图' }));
    expect(screen.getByRole('status')).toHaveTextContent('加载中');
  });

  it('★ 加载失败给一句人话（走既有 ErrorDetail 形状），不留破图图标', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '查看 SKU-1 的主图' }));

    const img = screen.getByAltText('SKU-1 主图') as HTMLImageElement;
    await act(async () => { fireEvent.error(img); });

    expect(screen.getByRole('alert')).toHaveTextContent('图片加载失败');
    // ★ 不留破图图标：<img> 本身被隐藏，不是让它带着裂图标继续占位
    expect(img.style.display).toBe('none');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('加载成功后图片可见，不再显示加载中/错误', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '查看 SKU-1 的主图' }));

    const img = screen.getByAltText('SKU-1 主图') as HTMLImageElement;
    await act(async () => { fireEvent.load(img); });

    expect(img.style.display).toBe('block');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // ══════════════════════════════════════════════════════════════════════
  //  实测主图可达 7 MB（PM 原图，无缩略图接口）——同一个货号在飞时不许
  //  重复发起，照既有 useInFlight 护栏（PlanSheet.tsx::openImage）。
  // ══════════════════════════════════════════════════════════════════════

  it('★ 图片加载中，入口按钮进入 disabled——不许同一个货号重复发起', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    const btn = screen.getByRole('button', { name: '查看 SKU-1 的主图' });
    await user.click(btn);
    expect(btn).toBeDisabled();
  });

  it('加载落地（成功）后，入口按钮恢复可点', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    const btn = screen.getByRole('button', { name: '查看 SKU-1 的主图' });
    await user.click(btn);
    expect(btn).toBeDisabled();

    const img = screen.getByAltText('SKU-1 主图') as HTMLImageElement;
    await act(async () => { fireEvent.load(img); });

    expect(btn).not.toBeDisabled();
  });

  it('★ 图片还没加载完就关闭弹窗，在飞标记也要放掉——不然这个货号的按钮永远 disabled', async () => {
    renderSheet(withHasImage(true));
    const user = userEvent.setup();
    const btn = screen.getByRole('button', { name: '查看 SKU-1 的主图' });
    await user.click(btn);
    expect(btn).toBeDisabled();

    // Modal 的关闭：点背景（role="presentation"）触发 onClose
    const backdrop = document.querySelector('.modal-backdrop') as HTMLElement;
    await act(async () => { fireEvent.click(backdrop); });

    expect(btn).not.toBeDisabled();
  });
});
