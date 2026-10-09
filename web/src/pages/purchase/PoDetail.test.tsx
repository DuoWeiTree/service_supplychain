import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import { fail } from '../../testing/backendStub';
import { EMPTY_TIMELINE, PLACED, mountPurchase, purchaseRoutes, type BackendState } from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const placed = (): BackendState => ({ po: PLACED, timeline: EMPTY_TIMELINE, queue: [] });

describe('采购单详情', () => {
  it('头部两轴并排：本地状态 | 领星状态「未读回」；领星单号、幂等键', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    expect(await screen.findByRole('heading', { name: '采购单 SCM-PO-1' })).toBeInTheDocument();
    const axes = screen.getByTestId('po-axes');
    expect(axes).toHaveTextContent('本地状态已下单');
    expect(axes).toHaveTextContent('领星状态未读回');
    const head = screen.getByTestId('po-detail-head');
    expect(head).toHaveTextContent('PO261009001');
    expect(head).toHaveTextContent('SCM-PO-1');
    expect(screen.queryByTestId('attention-panel')).toBeNull();
  });

  it('明细带领星行号（未回填写明）与来源反查的已确认 / 已下单', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    const rows = await screen.findAllByTestId('detail-line');
    expect(rows[0]).toHaveTextContent('88001');
    expect(rows[1]).toHaveTextContent('未回填');
    expect(rows[1]).toHaveTextContent('2');   // 无计划来源
    const tr = await within(rows[0]!).findByTestId('trace-101');
    expect(tr).toHaveTextContent('已确认 30');
    expect(tr).toHaveTextContent('已下单 0');
  });

  it('反查失败只落在那一条来源上，明细照常', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/plan-lines/102/trace': () => fail(404, 'line_not_found', '没有这条记录', { line_id: 102 }),
    }));
    const rows = await screen.findAllByTestId('detail-line');
    expect(await within(rows[1]!).findByText('反查取不到')).toBeInTheDocument();
    expect(await within(rows[0]!).findByTestId('trace-101')).toHaveTextContent('已确认 30');
  });

  it('时间线取不到：点名错误，单头明细照常', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/purchase-orders/1/timeline': () => fail(500, 'internal', '服务端错误'),
    }));
    expect(await screen.findByText('服务端错误')).toBeInTheDocument();
    expect(screen.getAllByTestId('detail-line')).toHaveLength(2);
  });
});
