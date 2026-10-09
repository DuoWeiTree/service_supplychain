import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { AttentionQueue, CandidateLines, PoList, PurchaseChecks } from '../../api/types';

beforeEach(() => { vi.resetModules(); });
function NewPage() { const loc = useLocation(); return <div>NEW-PAGE {loc.search}</div>; }
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

const NO_EXCL = { state: 0, sku: 0, category: 0, period: 0, fully_confirmed: 0 };
const CANDS: CandidateLines = {
  lines: [
    { line_id: 11, plan_id: 1, plan_title: '10 月计划', rev: 5, sku: 'DCC1045014AI', period: '2026-10', total_units: 200,
      demand_at_submit: 100, demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 200,
      blocked: null,
      sellers: { '11072': { expected: 75, cap: 150, used: 0, remaining: 150 },
                 '11094': { expected: 25, cap: 50, used: 0, remaining: 50 } } },
    { line_id: 12, plan_id: 1, plan_title: '10 月计划', rev: 5, sku: 'DCC1041294BW', period: '2026-10', total_units: 150,
      demand_at_submit: 0, demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 150,
      blocked: { reason: 'no_seller_basis', sellers: ['11072'] },
      sellers: { '11072': { expected: null, cap: null, used: 0, remaining: null } } },
    { line_id: 14, plan_id: 1, plan_title: '10 月计划', rev: 5, sku: 'DCC1045014AI', period: '2026-11', total_units: 80,
      demand_at_submit: 40, demand_by_seller: {}, state: '已确认', confirmed_units: 30, ordered_units: 0, remaining: 50,
      blocked: null, sellers: { '11072': { expected: 40, cap: 80, used: 30, remaining: 50 } } },
  ],
  groups: {},
  excluded: { ...NO_EXCL, state: 3, fully_confirmed: 2 },
};
const SELLERS = [{ seller_id: '11072', name: 'A4Pet-BS-US-US', market: 'US', has_fba: true, platform: 'amazon' }];

async function mount(over: Record<string, () => Promise<unknown>>) {
  const { ApiError } = await import('../../api/client');
  const base = {
    listAttention: async () => ({ count: 0, items: [] }),
    listPurchaseOrders: async () => PO_LIST,
    getPurchaseChecks: async () => OK_CHECKS,
    listCandidateLines: async () => CANDS,
    listSellers: async () => SELLERS,
  };
  vi.doMock('../../api', () => ({ api: { ...base, ...over }, ApiError }));
  const { PurchaseHome } = await import('./PurchaseHome');
  render(
    <MemoryRouter initialEntries={['/purchase']}>
      <Routes>
        <Route path="/purchase" element={<PurchaseHome />} />
        <Route path="/purchase/new" element={<NewPage />} />
        <Route path="/" element={<div>PLANS-PAGE</div>} />
        <Route path="/plans/:planId" element={<div>PLAN-PAGE</div>} />
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
    // 草稿没有领星单号是「不适用」，不是「未知」
    expect(rows[1]).toHaveTextContent('未下单');
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
    expect(within(screen.getByTestId('attention-group-needs_verify')).getAllByRole('link')).toHaveLength(1);
    expect(screen.getByTestId('attention-group-stuck_call')).toHaveTextContent('SCM-PO-3');
  });

  it('核对不一致：说 N 项，折叠区列明细', async () => {
    await mount({ getPurchaseChecks: async () => BAD_CHECKS });
    expect(await screen.findByText('核对有 2 项不一致')).toBeInTheDocument();
    // 页上不止一个折叠区（待采购的「未列入」也是）：按标题找
    const fold = screen.getByText('不一致明细').closest('details') as HTMLElement;
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

describe('采购首页：待采购（R3）', () => {
  it('★ 第一屏是待采购：列计划、rev、货号、月份、总量、还可组、各店期望占比上限', async () => {
    await mount({});
    const t = await screen.findByTestId('todo-list');
    const heads = Array.from(t.querySelectorAll('th')).map((h) => h.textContent);
    expect(heads).toEqual(['', '计划', 'rev', '货号', '月份', '总量', '还可组', '各店']);
    const row = screen.getByTestId('todo-11');
    expect(within(row).getByRole('link', { name: '10 月计划' })).toHaveAttribute('href', '/plans/1');
    expect(row).toHaveTextContent('rev5');
    expect(row).toHaveTextContent('DCC1045014AI');
    expect(row).toHaveTextContent('2026-10');
    expect(row).toHaveTextContent('200');
    const shops = within(row).getByTestId('todo-shops');
    // 店名取得到就写店名；取不到店名的那家写店编号
    expect(shops).toHaveTextContent('A4Pet-BS-US-US 期望 75（占比 75%）上限 150 剩 150');
    expect(shops).toHaveTextContent('11094 期望 25（占比 25%）上限 50 剩 50');
    // ★ 待采购在采购单列表之前（第一屏）
    const po = await screen.findByTestId('po-list');
    expect(t.compareDocumentPosition(po) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('heading', { name: '采购单' })).toBeInTheDocument();
  });

  it('别的草稿占了该店额度：写剩多少，不只写上限', async () => {
    await mount({});
    const shops = within(await screen.findByTestId('todo-14')).getByTestId('todo-shops');
    expect(shops).toHaveTextContent('上限 80 剩 50');
  });

  it('还可组 ≠ 总量时两个数都在', async () => {
    await mount({});
    const row = await screen.findByTestId('todo-14');
    expect(row).toHaveTextContent('80');
    expect(within(row).getByTestId('todo-remaining')).toHaveTextContent('50');
  });

  it('★ 无分店依据：不隐藏，灰显点名原因，勾不了', async () => {
    await mount({});
    const row = await screen.findByTestId('todo-12');
    expect(row).toHaveClass('muted');
    expect(row).toHaveTextContent('无分店依据：店铺期望销量待填');
    expect(row).toHaveTextContent('A4Pet-BS-US-US');
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('后端给了没见过的原因：原样点名，照样勾不了', async () => {
    const odd = { ...CANDS, lines: [{ ...CANDS.lines[1]!, blocked: { reason: 'weird' as 'no_seller_basis', sellers: [] } }] };
    await mount({ listCandidateLines: async () => odd });
    const row = await screen.findByTestId('todo-12');
    expect(row).toHaveTextContent('weird');
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('★ 勾选 → 按钮写 N；点了进 /purchase/new?lines=…', async () => {
    await mount({});
    const btn = await screen.findByRole('button', { name: '用选中的 0 条建采购单' });
    expect(btn).toBeDisabled();
    await userEvent.click(within(screen.getByTestId('todo-11')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('todo-14')).getByRole('checkbox'));
    const go = screen.getByRole('button', { name: '用选中的 2 条建采购单' });
    expect(go).toBeEnabled();
    await userEvent.click(go);
    expect(await screen.findByText('NEW-PAGE ?lines=11,14')).toBeInTheDocument();
  });

  it('被滤掉的一侧要说：已组满 N 条、状态不可组单 N 条', async () => {
    await mount({});
    const t = await screen.findByTestId('todo-dropped');
    expect(t).toHaveTextContent('未列入 5 条');
    expect(t).toHaveTextContent('已组满 2 条');
    expect(t).toHaveTextContent('状态不可组单 3 条');
  });

  it('★ 没有待采购：写「没有待采购的记录」并给去销售计划的链接', async () => {
    await mount({ listCandidateLines: async () => ({ lines: [], groups: {}, excluded: NO_EXCL }) });
    expect(await screen.findByText('没有待采购的记录')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: '去销售计划' }));
    expect(await screen.findByText('PLANS-PAGE')).toBeInTheDocument();
  });

  it('★ 加载、失败、空 三种说法各不相同；失败不当空', async () => {
    await mount({ listCandidateLines: () => new Promise(() => {}) });
    expect(await screen.findByText('正在取待采购记录…')).toBeInTheDocument();
    expect(screen.queryByText('没有待采购的记录')).toBeNull();
  });

  it('★ 待采购读失败：ErrorDetail 点名，不写「没有待采购的记录」，采购单列表照出', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { ApiError } = await import('../../api/client');
    await mount({ listCandidateLines: async () => { throw new ApiError(503, 'mirror_stale', '镜像过期', {}); } });
    expect(await screen.findByText('镜像过期')).toBeInTheDocument();
    expect(screen.queryByText('没有待采购的记录')).toBeNull();
    expect(screen.queryByRole('button', { name: /用选中的/ })).toBeNull();
    expect(await screen.findByTestId('po-list')).toBeInTheDocument();
    expect(warn.mock.calls.some((c) => String(c[0]).includes('listCandidateLines'))).toBe(true);
    warn.mockRestore();
  });

  it('店名取不到：按店编号显示，且明说', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { ApiError } = await import('../../api/client');
    await mount({ listSellers: async () => { throw new ApiError(500, 'boom', '店取不到', {}); } });
    const shops = within(await screen.findByTestId('todo-11')).getByTestId('todo-shops');
    expect(shops).toHaveTextContent('11072 期望 75');
    expect(screen.getByText('店名取不到，按店编号显示')).toBeInTheDocument();
    warn.mockRestore();
  });
});
