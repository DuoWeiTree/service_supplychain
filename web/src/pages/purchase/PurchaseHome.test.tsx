import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { AttentionQueue, PoList, PurchaseChecks } from '../../api/types';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); });

const PO_LIST: PoList = { purchase_orders: [
  { po_id: 2, custom_order_sn: 'SCM-PO-2', supplier_id: 'S-2', supplier_name: '宁波远景', state: '已下单', order_sn: 'PO260930001', is_void: false,
    lines: 1, units: 80, open_attention: 2, created_by: 'buyer.wang', created_at: '2026-10-08T10:00:00+08:00' },
  { po_id: 1, custom_order_sn: 'SCM-PO-1', supplier_id: 'S-1', supplier_name: '东莞宏达', state: '草稿', order_sn: null, is_void: false,
    lines: 2, units: 150, open_attention: 0, created_by: 'ops.zhang', created_at: '2026-10-08T09:00:00+08:00' },
] };
const OK_CHECKS: PurchaseChecks = { ok: true, counts: { po_source: 0, line_state: 0, po_unplanned: 0, po_landed: 0 }, violations: [] };
const BAD_CHECKS: PurchaseChecks = { ok: false, counts: { po_source: 1, line_state: 0, po_unplanned: 0, po_landed: 1 },
  violations: [{ check_name: 'po_source', ref: 'po=2 line=1' }, { check_name: 'po_landed', ref: 'po=2' }] };
const ATTN: AttentionQueue = { count: 3, items: [
  { attention_id: 1, po_id: 2, kind: 'needs_verify', opened_at: 'x', custom_order_sn: 'SCM-PO-2', supplier_id: 'S-2', timeline: '' },
  { attention_id: 2, po_id: 2, kind: 'landed_mismatch', opened_at: 'x', custom_order_sn: 'SCM-PO-2', supplier_id: 'S-2', timeline: '' },
  { attention_id: null, po_id: 3, kind: 'stuck_call', opened_at: 'x', custom_order_sn: 'SCM-PO-3', supplier_id: 'S-2', timeline: '' },
] };

async function mount(over: Record<string, () => Promise<unknown>>) {
  const { ApiError } = await import('../../api/client');
  const base = {
    listAttention: async () => ({ count: 0, items: [] }),
    listPurchaseOrders: async () => PO_LIST,
    getPurchaseChecks: async () => OK_CHECKS,
  };
  vi.doMock('../../api', () => ({ api: { ...base, ...over }, ApiError }));
  const { PurchaseHome } = await import('./PurchaseHome');
  render(
    <MemoryRouter initialEntries={['/purchase']}>
      <Routes>
        <Route path="/purchase" element={<PurchaseHome />} />
        <Route path="/purchase/new" element={<div>NEW-PAGE</div>} />
        <Route path="/purchase/:poId" element={<div>DETAIL-PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('采购首页', () => {
  it('表格列、行数据、领星单号无则 —、待处理 >0 为 danger chip', async () => {
    await mount({});
    const table = await screen.findByTestId('po-list');
    const heads = Array.from(table.querySelectorAll('th')).map((t) => t.textContent);
    expect(heads).toEqual(['单号', '供应商', '状态', '领星单号', '行', '件数', '待处理', '建单人']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('SCM-PO-2');
    expect(rows[0]).toHaveTextContent('PO260930001');
    expect(within(rows[0]!).getByText('2')).toHaveClass('chip--bad');
    expect(rows[1]).toHaveTextContent('SCM-PO-1');
    expect(within(rows[1]!).getAllByText('—').length).toBeGreaterThan(0);
    expect(rows[1]!.querySelector('.chip--bad')).toBeNull();
  });

  it('草稿 N 与已下单 N 分开计数', async () => {
    await mount({});
    const k = await screen.findByTestId('counts');
    expect(k.textContent).toContain('1草稿');
    expect(k.textContent).toContain('1已下单');
  });

  it('没有待处理项时不出现待处理区；核对通过说「核对全部通过」', async () => {
    await mount({});
    await screen.findByTestId('po-list');
    expect(screen.queryByTestId('attention-list')).toBeNull();
    expect(await screen.findByText('核对全部通过')).toBeInTheDocument();
  });

  it('待处理区：三种类型文字、单号与打开链接', async () => {
    await mount({ listAttention: async () => ATTN });
    const box = await screen.findByTestId('attention-list');
    expect(box).toHaveTextContent('下单结果待核实');
    expect(box).toHaveTextContent('领星明细不一致');
    expect(box).toHaveTextContent('下单请求卡住');
    expect(box).toHaveTextContent('SCM-PO-3');
    const links = within(box).getAllByRole('link', { name: '打开' });
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/purchase/2', '/purchase/2', '/purchase/3']);
  });

  it('核对不一致：说 N 项，折叠区列明细', async () => {
    await mount({ getPurchaseChecks: async () => BAD_CHECKS });
    expect(await screen.findByText('核对有 2 项不一致')).toBeInTheDocument();
    const fold = document.querySelector('details.fold') as HTMLElement;
    expect(fold).not.toBeNull();
    expect(fold).toHaveTextContent('po_source');
    expect(fold).toHaveTextContent('po=2 line=1');
    expect(fold).toHaveTextContent('po_landed');
  });

  it('★ 待处理读失败：列表与核对照样出，计数不落回 0 而是 —', async () => {
    vi.resetModules();
    const { ApiError } = await import('../../api/client');
    await mount({ listAttention: async () => { throw new ApiError(500, 'boom', '待处理取不到', {}); } });
    expect(await screen.findByTestId('po-list')).toBeInTheDocument();
    expect(await screen.findByText('核对全部通过')).toBeInTheDocument();
    expect((await screen.findAllByText('待处理取不到')).length).toBeGreaterThan(0);
  });

  it('★ 采购单读失败：计数为 —，其余照出，列表区给错误', async () => {
    vi.resetModules();
    const { ApiError } = await import('../../api/client');
    await mount({
      listPurchaseOrders: async () => { throw new ApiError(500, 'boom', '采购单取不到', {}); },
      listAttention: async () => ATTN,
    });
    expect(await screen.findByTestId('attention-list')).toBeInTheDocument();
    expect(await screen.findByText('核对全部通过')).toBeInTheDocument();
    expect(await screen.findByText('采购单取不到')).toBeInTheDocument();
    expect(screen.queryByTestId('po-list')).toBeNull();
    const k = screen.getByTestId('counts');
    expect(k.textContent).toContain('—草稿');
    expect(k.textContent).not.toContain('0草稿');
  });

  it('★ 核对读失败：不显示「全部通过」', async () => {
    vi.resetModules();
    const { ApiError } = await import('../../api/client');
    await mount({ getPurchaseChecks: async () => { throw new ApiError(500, 'boom', '核对取不到', {}); } });
    expect(await screen.findByText('核对取不到')).toBeInTheDocument();
    expect(screen.queryByText('核对全部通过')).toBeNull();
  });

  it('★ 待处理一直没回：列表与核对照样出，不等它', async () => {
    await mount({ listAttention: () => new Promise(() => {}) });
    expect(await screen.findByTestId('po-list')).toBeInTheDocument();
    expect(await screen.findByText('核对全部通过')).toBeInTheDocument();
    expect(screen.queryByTestId('attention-list')).toBeNull();
  });

  it('★ 失败日志带该次调用自己的耗时', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { ApiError } = await import('../../api/client');
    await mount({
      getPurchaseChecks: async () => { throw new ApiError(500, 'boom', '核对取不到', {}); },
      listAttention: () => new Promise(() => {}),
    });
    await screen.findByText('核对取不到');
    const line = warn.mock.calls.map((c) => String(c[0]) + String(c[1])).find((t) => t.includes('getPurchaseChecks'))!;
    expect(line).toContain('"ms":');
    expect(warn.mock.calls.some((c) => String(c[0]).includes('listAttention'))).toBe(false);
    warn.mockRestore();
  });

  it('加载与空态措辞不同', async () => {
    await mount({ listPurchaseOrders: async () => ({ purchase_orders: [] }) });
    expect(screen.getByText('正在取采购单…')).toBeInTheDocument();
    expect(await screen.findByText('还没有采购单')).toBeInTheDocument();
    expect(screen.queryByText('正在取采购单…')).toBeNull();
  });

  it('点单号进详情；「新建采购单」进 /purchase/new', async () => {
    await mount({});
    const table = await screen.findByTestId('po-list');
    await userEvent.click(within(table).getByRole('link', { name: 'SCM-PO-1' }));
    expect(await screen.findByText('DETAIL-PAGE')).toBeInTheDocument();
  });

  it('新建采购单按钮', async () => {
    await mount({});
    await userEvent.click(await screen.findByRole('button', { name: '新建采购单' }));
    expect(await screen.findByText('NEW-PAGE')).toBeInTheDocument();
  });
});
