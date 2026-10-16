import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { AttentionQueue, PoList, PurchaseChecks, PurchasePlan, PurchasePlanRecord, PurchasePlans } from '../../api/types';

beforeEach(() => { vi.resetModules(); });
function NewPage() { const loc = useLocation(); return <div>NEW-PAGE {loc.search}</div>; }
afterEach(() => { vi.doUnmock('../../api'); });

const PO_LIST: PoList = { purchase_orders: [
  { po_id: 2, custom_order_sn: 'SCM-PO-2', supplier_id: 'S-2', supplier_name: '宁波远景', state: '已下单', order_sn: 'PO260930001', is_void: false,
    lines: 1, units: 80, open_attention: 2, created_by: 'buyer.wang', created_at: '2026-10-08T10:00:00+08:00' },
  { po_id: 3, custom_order_sn: 'SCM-PO-3', supplier_id: 'S-2', supplier_name: '宁波远景', state: '已下单', order_sn: 'PO260930002', is_void: true,
    lines: 1, units: 5, open_attention: 0, created_by: 'buyer.wang', created_at: '2026-10-07T10:00:00+08:00' },
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

const rec = (over: Partial<PurchasePlanRecord>): PurchasePlanRecord => ({
  line_id: 1, rev: 5, sku: 'DCC1045014AI', sku_name: '室内围栏', period: '2026-10', state: '已提交',
  total: 200, confirmed: 0, ordered: 0, remaining: 200, orderable: true, ...over,
});
const P1: PurchasePlan = {
  plan_id: 1, title: '10 月计划', rev: 5, lines: 3, total: 430, confirmed: 30, ordered: 0, orderable_units: 400, orderable_lines: 2,
  drafts: [{ po_id: 1, custom_order_sn: 'SCM-PO-1', units: 30, open_attention: 0 }],
  records: [
    rec({ line_id: 11 }),
    rec({ line_id: 12, sku: 'DCC1041294BW', sku_name: null, total: 150, confirmed: 150, remaining: 0, orderable: false }),
    rec({ line_id: 14, period: '2026-11', total: 80, confirmed: 30, remaining: 50, state: '已确认' }),
  ],
};
/** ★ 被草稿组满：没有可组记录，仍要列出并挂草稿（UX-D2） */
const P2: PurchasePlan = {
  plan_id: 2, title: '测试1', rev: 7, lines: 1, total: 180, confirmed: 180, ordered: 80, orderable_units: 0, orderable_lines: 0,
  drafts: [{ po_id: 5, custom_order_sn: 'SCM-PO-5', units: 100, open_attention: 0 }],
  records: [rec({ line_id: 13, sku: 'DCC9', total: 180, confirmed: 180, ordered: 80, remaining: 0, orderable: false, state: '已确认' })],
};
const PLANS: PurchasePlans = {
  plans: [P2, P1],
  unplanned_drafts: [],
  excluded: { nothing_to_order: [{ plan_id: 9, title: '老计划', rev: 2 }] },
};

async function mount(over: Record<string, () => Promise<unknown>>, entry = '/purchase') {
  const { ApiError } = await import('../../api/client');
  const base = {
    listAttention: async () => ({ count: 0, items: [] }),
    listPurchaseOrders: async () => PO_LIST,
    getPurchaseChecks: async () => OK_CHECKS,
    listPurchasePlans: async () => PLANS,
  };
  vi.doMock('../../api', () => ({ api: { ...base, ...over }, ApiError }));
  const { PurchaseHome } = await import('./PurchaseHome');
  render(
    <MemoryRouter initialEntries={[entry]}>
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

const openAllPos = async () => {
  const fold = await screen.findByTestId('all-pos');
  (fold as HTMLDetailsElement).open = true;
  return fold;
};

describe('采购首页 · 全部采购单（UX-D3）', () => {
  it('★ 收进折叠区、默认收起，标题带张数与作废张数；没有 KPI 小卡', async () => {
    await mount({});
    expect(await screen.findByText('全部采购单（3 张，含作废 1）')).toBeInTheDocument();
    expect(screen.getByTestId('all-pos')).not.toHaveAttribute('open');
    expect(screen.queryByTestId('counts')).toBeNull();
  });

  it('★ 「待处理」不单占一列：>0 时挂在状态旁；领星单号草稿写未下单', async () => {
    await mount({});
    await openAllPos();
    const table = await screen.findByTestId('po-list');
    const heads = Array.from(table.querySelectorAll('th')).map((t) => t.textContent);
    expect(heads).toEqual(['单号', '供应商', '本地状态', '领星单号', '行', '件数', '建单人']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]!).getByText('待处理 2')).toHaveClass('chip--bad');
    expect(rows[1]).toHaveTextContent('作废');
    expect(rows[2]).toHaveTextContent('未下单');
    expect(rows[2]!.querySelector('.chip--bad')).toBeNull();
  });

  it('核对通过写在折叠内', async () => {
    await mount({});
    const fold = await openAllPos();
    expect(await within(fold).findByText('核对全部通过')).toBeInTheDocument();
  });

  it('核对不一致：说 N 项，折叠区列明细', async () => {
    await mount({ getPurchaseChecks: async () => BAD_CHECKS });
    expect(await screen.findByText('核对有 2 项不一致')).toBeInTheDocument();
    const fold = screen.getByText('不一致明细').closest('details') as HTMLElement;
    expect(fold).toHaveTextContent('po=2 line=1');
    expect(fold).toHaveTextContent('po_landed');
  });

  it('★ 核对读失败：不显示「全部通过」', async () => {
    const { ApiError } = await import('../../api/client');
    await mount({ getPurchaseChecks: async () => { throw new ApiError(500, 'boom', '核对取不到', {}); } });
    expect(await screen.findByText('核对取不到')).toBeInTheDocument();
    await openAllPos();
    expect(screen.queryByText('核对全部通过')).toBeNull();
  });

  it('★ 采购单读失败：给错误，待采购照出', async () => {
    const { ApiError } = await import('../../api/client');
    await mount({ listPurchaseOrders: async () => { throw new ApiError(500, 'boom', '采购单取不到', {}); } });
    expect(await screen.findByText('采购单取不到')).toBeInTheDocument();
    expect(await screen.findByTestId('plan-1')).toBeInTheDocument();
  });

  it('空态写还没有采购单', async () => {
    await mount({ listPurchaseOrders: async () => ({ purchase_orders: [] }) });
    expect(await screen.findByText('全部采购单（0 张，含作废 0）')).toBeInTheDocument();
    await openAllPos();
    expect(screen.getByText('还没有采购单')).toBeInTheDocument();
  });

  it('点单号进详情', async () => {
    await mount({});
    await openAllPos();
    await userEvent.click(within(await screen.findByTestId('po-list')).getByRole('link', { name: 'SCM-PO-1' }));
    expect(await screen.findByText('DETAIL-PAGE')).toBeInTheDocument();
  });
});

describe('采购首页 · 待处理', () => {
  it('待处理区：三种类型文字、单号与打开链接', async () => {
    await mount({ listAttention: async () => ATTN });
    const box = await screen.findByTestId('attention-list');
    expect(box).toHaveTextContent('下单结果待核实');
    expect(box).toHaveTextContent('领星明细不一致');
    expect(box).toHaveTextContent('下单请求卡住');
    const links = within(box).getAllByRole('link', { name: '打开' });
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/purchase/2', '/purchase/2', '/purchase/3']);
  });

  it('★ 待处理一直没回：待采购与采购单照样出，不等它', async () => {
    await mount({ listAttention: () => new Promise(() => {}) });
    expect(await screen.findByTestId('plan-1')).toBeInTheDocument();
    expect(await screen.findByText('全部采购单（3 张，含作废 1）')).toBeInTheDocument();
    expect(screen.queryByTestId('attention-list')).toBeNull();
  });

  it('★ 失败日志带该次调用自己的耗时', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { ApiError } = await import('../../api/client');
    await mount({ getPurchaseChecks: async () => { throw new ApiError(500, 'boom', '核对取不到', {}); }, listAttention: () => new Promise(() => {}) });
    await screen.findByText('核对取不到');
    const line = warn.mock.calls.map((c) => String(c[0]) + String(c[1])).find((t) => t.includes('getPurchaseChecks'))!;
    expect(line).toContain('"ms":');
    expect(warn.mock.calls.some((c) => String(c[0]).includes('listAttention'))).toBe(false);
    warn.mockRestore();
  });
});

describe('采购首页 · 待采购按销售计划分组（UX-D1 / D2）', () => {
  it('★ 计划抬头：计划名 · rev · 货号与记录数 · 需求 / 已组单（含草稿）/ 已下单 分开 · 还可组；默认收起', async () => {
    await mount({});
    const p = await screen.findByTestId('plan-1');
    expect(p).toHaveTextContent('10 月计划');
    expect(p).toHaveTextContent('rev 5');
    expect(p).toHaveTextContent('2 个货号 · 3 条记录');
    expect(p).toHaveTextContent('已组单（含草稿）30');
    expect(p).toHaveTextContent('已下单0');
    expect(within(p).getByTestId('plan-left-1')).toHaveTextContent('400');
    expect(screen.queryByTestId('pivot-1')).toBeNull();
    expect(within(p).getByRole('button', { name: /10 月计划/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('★ 草稿组满的计划不消失：抬头挂草稿 chip「接着编辑」链到草稿', async () => {
    await mount({});
    const p = await screen.findByTestId('plan-2');
    expect(within(p).getByTestId('plan-left-2')).toHaveTextContent('0');
    const chip = within(p).getByTestId('draft-chip-5');
    expect(chip).toHaveTextContent('SCM-PO-5 草稿 · 100 件 · 接着编辑');
    expect(chip).toHaveAttribute('href', '/purchase/5');
    expect(within(p).getByRole('checkbox', { name: '选「测试1」全部可组记录' })).toBeDisabled();
  });

  it('草稿有待处理项：chip 写待处理', async () => {
    await mount({ listPurchasePlans: async () => ({ ...PLANS, plans: [{ ...P2, drafts: [{ ...P2.drafts[0]!, open_attention: 1 }] }] }) });
    expect(await screen.findByTestId('draft-chip-5')).toHaveTextContent('SCM-PO-5 待处理 1 项');
    expect(screen.getByTestId('draft-chip-5')).toHaveClass('chip--bad');
  });

  it('★ 没有可组、没有草稿的计划不列，但点名', async () => {
    await mount({});
    const fold = await screen.findByTestId('todo-dropped');
    expect(fold).toHaveTextContent('另有 1 个计划没有可组的记录、也没有草稿');
    expect(within(fold).getByRole('link', { name: '老计划' })).toHaveAttribute('href', '/plans/9');
  });

  it('不来自计划的草稿单独列出', async () => {
    await mount({ listPurchasePlans: async () => ({ ...PLANS, unplanned_drafts: [{ po_id: 8, custom_order_sn: 'SCM-PO-8', units: 5, open_attention: 0 }] }) });
    expect(within(await screen.findByTestId('unplanned-drafts')).getByTestId('draft-chip-8')).toHaveAttribute('href', '/purchase/8');
  });

  it('★ 展开是货号 × 月小表：可组 / 组满 / 没记录 三态分开', async () => {
    await mount({});
    await userEvent.click(await screen.findByRole('button', { name: /10 月计划/ }));
    const t = screen.getByTestId('pivot-1');
    expect(Array.from(t.querySelectorAll('th')).map((h) => h.textContent)).toEqual(['货号', '品名', '2026-10', '2026-11', '还可组']);
    expect(screen.getByTestId('cell-1-DCC1045014AI-2026-10')).toHaveTextContent('200/200');
    expect(screen.getByTestId('cell-1-DCC1041294BW-2026-10')).toHaveTextContent('已组满');
    expect(screen.getByTestId('cell-1-DCC1041294BW-2026-11')).toHaveTextContent('—');
    expect(within(screen.getByTestId('cell-1-DCC1041294BW-2026-10')).queryByRole('checkbox')).toBeNull();
  });

  it('★ 只有一个主按钮；「新建空白采购单」是次按钮；没有全局全选', async () => {
    await mount({});
    await screen.findByTestId('plan-1');
    const primaries = Array.from(document.querySelectorAll('button.btn--primary')).map((b) => b.textContent);
    expect(primaries).toEqual(['用选中的记录建采购单']);
    expect(screen.queryByRole('checkbox', { name: '全选' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '新建空白采购单' }));
    expect(await screen.findByText('NEW-PAGE')).toBeInTheDocument();
  });

  it('★ 抬头勾 = 本计划全部可组记录；格子勾；按清单顺序带进 /purchase/new?lines=', async () => {
    await mount({});
    await userEvent.click(await screen.findByRole('checkbox', { name: '选「10 月计划」全部可组记录' }));
    expect(screen.getByTestId('todo-sum')).toHaveTextContent('已选 2 条，还可组 250 件');
    await userEvent.click(screen.getByRole('button', { name: /10 月计划/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: '选 DCC1045014AI 2026-10' }));
    expect(screen.getByTestId('todo-sum')).toHaveTextContent('已选 1 条，还可组 50 件');
    expect((screen.getByRole('checkbox', { name: '选「10 月计划」全部可组记录' }) as HTMLInputElement).indeterminate).toBe(true);
    await userEvent.click(screen.getByRole('checkbox', { name: '选 DCC1045014AI 2026-10' }));
    await userEvent.click(screen.getByRole('button', { name: '用选中的 2 条建采购单' }));
    expect(await screen.findByText('NEW-PAGE ?lines=11,14')).toBeInTheDocument();
  });

  it('★ 搜货号：计划自动展开、命中行高亮；搜不到写没有匹配', async () => {
    await mount({});
    const box = await screen.findByRole('searchbox', { name: '搜计划名或货号' });
    await userEvent.type(box, '1294');
    expect(screen.getByTestId('pivot-row-1-DCC1041294BW')).toHaveClass('is-on');
    expect(screen.queryByTestId('plan-2')).toBeNull();
    await userEvent.clear(box);
    await userEvent.type(box, 'NOPE');
    expect(screen.getByText('没有匹配「NOPE」的计划或货号')).toBeInTheDocument();
    expect(screen.queryByText('没有待采购的记录')).toBeNull();
  });

  it('★ UX-D6：?plan= 进来展开那一组并滚到它', async () => {
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    await mount({}, '/purchase?plan=1');
    expect(await screen.findByTestId('pivot-1')).toBeInTheDocument();
    expect(screen.getByTestId('plan-1')).toHaveClass('is-focus');
    expect(spy).toHaveBeenCalled();
    expect(screen.queryByTestId('pivot-2')).toBeNull();
  });

  it('?plan= 指向不列出的计划：点名，不悄悄没反应', async () => {
    await mount({}, '/purchase?plan=9');
    expect(await screen.findByTestId('focus-missing')).toHaveTextContent('「老计划」没有可组的记录、也没有草稿');
  });

  it('★ 没有待采购：写「没有待采购的记录」并给去销售计划的链接；没有建单按钮', async () => {
    await mount({ listPurchasePlans: async () => ({ plans: [], unplanned_drafts: [], excluded: { nothing_to_order: [] } }) });
    expect(await screen.findByText('没有待采购的记录')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /用选中的/ })).toBeNull();
    await userEvent.click(screen.getByRole('link', { name: '去销售计划' }));
    expect(await screen.findByText('PLANS-PAGE')).toBeInTheDocument();
  });

  it('★ 待采购读失败：点名，不当空', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { ApiError } = await import('../../api/client');
    await mount({ listPurchasePlans: async () => { throw new ApiError(503, 'mirror_stale', '镜像过期', {}); } });
    expect(await screen.findByText('镜像过期')).toBeInTheDocument();
    expect(screen.queryByText('没有待采购的记录')).toBeNull();
    expect(screen.queryByRole('button', { name: /用选中的/ })).toBeNull();
    expect(warn.mock.calls.some((c) => String(c[0]).includes('listPurchasePlans'))).toBe(true);
    warn.mockRestore();
  });

  it('正在取', async () => {
    await mount({ listPurchasePlans: () => new Promise(() => {}) });
    expect(await screen.findByText('正在取待采购记录…')).toBeInTheDocument();
  });
});
