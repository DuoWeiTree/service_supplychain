import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { CandidateLines, PurchaseOptions, PurchaseOrder } from '../../api/types';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const OPTIONS: PurchaseOptions = {
  suppliers: [{ supplier_id: 'S-1001', name: '东莞宏达' }],
  warehouses: [{ wid: 'W-SZ', name: '深圳本地仓' }, { wid: 'W-NB', name: '宁波本地仓' }],
  purchasers: [{ purchaser_id: '111', source: '领星历史', count: 135 }, { purchaser_id: '185', source: '本服务', count: 2 }],
  opt_uids: [{ opt_uid: '10925905', name: '陈彩凤', source: '领星历史', count: 143 }],
  history: { available: true, cause: null, as_of: '2026-10-08', dropped: 0 },
};
const CANDS: CandidateLines = {
  lines: [
    { line_id: 101, plan_id: 3, rev: 1, sku: 'SKU-A', period: '2026-10', total_units: 500, demand_at_submit: 60,
      demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 120, blocked: null,
      sellers: { '11072': { expected: 40, cap: 80, used: 0, remaining: 80 },
                 '11094': { expected: 20, cap: 40, used: 40, remaining: 0 } } },
    { line_id: 103, plan_id: 3, rev: 1, sku: 'SKU-B', period: '2026-11', total_units: 80, demand_at_submit: 0,
      demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 80,
      blocked: { reason: 'no_seller_basis', sellers: ['11072'] },
      sellers: { '11072': { expected: null, cap: null, used: 0, remaining: null } } },
  ],
  groups: {},
  excluded: { state: 1, sku: 0, category: 0, period: 0, fully_confirmed: 2 },
};
const DRAFT: PurchaseOrder = {
  po_id: 1, supplier_id: 'S-1001', purchaser_id: '111', opt_uid: '10925905', qc_type: 1, wid: 'W-SZ',
  custom_order_sn: 'SCM-PO-1', order_sn: null, state: '草稿', placed_at: null, draft_version: 4,
  created_by: 'ops.zhang', is_void: false, discarded_at: null,
  lines: [{ line_no: 1, sku: 'SKU-A', wid: 'W-SZ', units: 30, price: '12.5000', expect_arrive_date: null,
    unplanned_reason: null, lx_item_id: null, sources: [{ line_id: 101, seller_id: '11072', units: 30 }],
    sources_units: 30, unplanned_units: 0 }],
  source_line_states: [{ line_id: 101, state: '已提交' }], attention: [], preflight: [], confirm_token: 't',
};

async function mount(entry: string, over: Record<string, unknown> = {}) {
  const { ApiError } = await import('../../api/client');
  const api = {
    listAttention: vi.fn(async () => ({ count: 0, items: [] })),
    listSellers: vi.fn(async () => [{ seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' }]),
    getPurchaseOptions: vi.fn(async () => OPTIONS),
    listCandidateLines: vi.fn(async () => CANDS),
    getPurchaseOrder: vi.fn(async () => DRAFT),
    // 路由判可编辑要看调用记录（在途下单）；详情页要反查来源
    getTimeline: vi.fn(async () => ({ po_id: 1, conclusion: null, attempts: [], events: [], attention: [] })),
    getLineTrace: vi.fn(async (id: number) => ({ line_id: id, sku: 'SKU-A', period: '2026-10', total_units: 500,
      state: '已下单', confirmed_units: 30, ordered_units: 30, purchase_orders: [] })),
    getPreflight: vi.fn(async () => ({ po_id: 1, ok: false, gates: [
      { gate: 'supplier', ok: true, detail: {} },
      { gate: 'sources_orderable', ok: false, detail: { offenders: [{ line_id: 101, state: '已撤销' }] } },
    ] })),
    createPurchaseOrder: vi.fn(async () => ({ po_id: 9 })),
    updatePurchaseOrder: vi.fn(async () => ({ po_id: 1, draft_version: 5 })),
    deletePurchaseOrder: vi.fn(async () => undefined),
    ...over,
  };
  vi.doMock('../../api', () => ({ api, ApiError }));
  const { PoEditor, PoRoute } = await import('./PoEditor');
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/purchase" element={<div>HOME-PAGE</div>} />
        <Route path="/purchase/new" element={<PoEditor poId={null} />} />
        <Route path="/purchase/:poId" element={<PoRoute />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  return { api, ApiError };
}

const sellerRow = async (lineId: number, sid: string) =>
  within(await screen.findByTestId(`cand-${lineId}`)).getByTestId(`seller-${sid}`);

describe('采购单编辑器：新建', () => {
  it('加入来源 → 明细出现该货号，数量默认 = 来源合计', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await userEvent.type(within(row).getByRole('spinbutton'), '30');
    await userEvent.click(within(row).getByRole('button', { name: '加入' }));
    const line = await screen.findByTestId('po-line-1');
    expect(line).toHaveTextContent('SKU-A');
    expect(within(line).getByTestId('sources')).toHaveTextContent('11072');
    expect(within(line).getByTestId('sources')).toHaveTextContent('30');
    expect(within(line).getByLabelText('数量')).toHaveValue(30);
    // 本稿已加的量从剩余里扣掉
    expect(within(await sellerRow(101, '11072')).getByTestId('avail')).toHaveTextContent('50');
  });

  it('超过该店剩余 → 不加，点名最多还能加多少', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await userEvent.type(within(row).getByRole('spinbutton'), '81');
    await userEvent.click(within(row).getByRole('button', { name: '加入' }));
    expect(row).toHaveTextContent('最多还能加 80');
    expect(screen.queryByTestId('po-line-1')).toBeNull();
  });

  it('剩余 0 的店：加入禁用并写原因', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11094');
    expect(within(row).getByRole('button', { name: '加入' })).toBeDisabled();
    expect(row).toHaveTextContent('已无剩余');
  });

  it('★ blocked 记录照样列出、灰显、点名原因，不能加', async () => {
    await mount('/purchase/new');
    const rec = await screen.findByTestId('cand-103');
    expect(rec).toHaveTextContent('无分店依据：A4Pet-US（11072）');
    expect(rec).toHaveClass('muted');
    expect(within(rec).queryByRole('button', { name: '加入' })).toBeNull();
  });

  it('被滤掉的计数用 dropline 列出', async () => {
    await mount('/purchase/new');
    const box = await screen.findByTestId('candidates');
    const drops = Array.from(box.querySelectorAll('.dropline')).map((x) => x.textContent);
    expect(drops).toContain('已组满 2 条');
    expect(drops).toContain('状态不可组单 1 条');
  });

  it('数量改大 → 出现「无计划来源 N 件」与理由；对应闸红色点名', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await userEvent.type(within(row).getByRole('spinbutton'), '30');
    await userEvent.click(within(row).getByRole('button', { name: '加入' }));
    const line = await screen.findByTestId('po-line-1');
    const qty = within(line).getByLabelText('数量');
    await userEvent.clear(qty);
    await userEvent.type(qty, '50');
    expect(line).toHaveTextContent('无计划来源 20 件');
    expect(within(line).getByLabelText('理由')).toBeInTheDocument();
    const fail = within(screen.getByTestId('preflight')).getByText('第 1 行无计划来源的理由');
    expect(fail.closest('li')).toHaveClass('gate__item--fail');
    await userEvent.type(within(line).getByLabelText('理由'), '备货');
    expect(within(screen.getByTestId('preflight')).getByText('第 1 行无计划来源的理由').closest('li'))
      .toHaveClass('gate__item--pass');
  });

  it('新建未保存时就逐闸列出（本地校验），未过的红色', async () => {
    await mount('/purchase/new');
    const box = await screen.findByTestId('preflight');
    for (const t of ['供应商', '采购方', '采购员', '质检方式', '至少一行明细']) {
      expect(within(box).getByText(t).closest('li')).toHaveClass('gate__item--fail');
    }
  });

  it('领星历史的选项铅笔灰并标出处；可手填采购方', async () => {
    const { api } = await mount('/purchase/new');
    const sel = await screen.findByLabelText('采购方');
    const opt = within(sel).getByRole('option', { name: /^111/ });
    expect(opt).toHaveTextContent('领星历史');
    expect(opt).toHaveClass('i-pencil');
    expect(within(sel).getByRole('option', { name: /^185/ })).not.toHaveClass('i-pencil');
    await userEvent.selectOptions(sel, '手填');
    await userEvent.type(screen.getByLabelText('采购方（手填）'), '222');
    // 填齐其余，保存看 body
    await userEvent.selectOptions(screen.getByLabelText('供应商'), 'S-1001');
    await userEvent.selectOptions(screen.getByLabelText('采购员'), '10925905');
    await userEvent.selectOptions(screen.getByLabelText('质检方式'), '2');
    const row = await sellerRow(101, '11072');
    await userEvent.type(within(row).getByRole('spinbutton'), '30');
    await userEvent.click(within(row).getByRole('button', { name: '加入' }));
    const line = screen.getByTestId('po-line-1');
    await userEvent.selectOptions(within(line).getByLabelText('仓'), 'W-NB');
    await userEvent.type(within(line).getByLabelText('单价'), '3.20');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(api.createPurchaseOrder).toHaveBeenCalledWith({
      supplier_id: 'S-1001', purchaser_id: '222', opt_uid: '10925905', qc_type: 2,
      lines: [{ sku: 'SKU-A', wid: 'W-NB', units: 30, price: '3.20',
        sources: [{ line_id: 101, seller_id: '11072', units: 30 }] }],
    });
    // 跳到 /purchase/9 后按新单号载入
    await vi.waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(9));
  });

  it('★ 领星历史取不到：写「领星历史暂不可用」，原因进折叠区；不是空下拉', async () => {
    await mount('/purchase/new', { getPurchaseOptions: vi.fn(async () => ({
      ...OPTIONS, purchasers: [], opt_uids: [],
      history: { available: false, cause: 'unreachable: ConnectError (ECONNREFUSED)', as_of: null, dropped: 0 } })) });
    expect(await screen.findByText('领星历史暂不可用')).toBeInTheDocument();
    const fold = Array.from(document.querySelectorAll('details.fold')).find((f) => f.textContent?.includes('ECONNREFUSED'));
    expect(fold).toBeTruthy();
    const sel = screen.getByLabelText('采购方');
    expect(within(sel).getByRole('option', { name: '无已知值，可手填' })).toBeInTheDocument();
  });

  it('缺单头时保存：不发请求，点名缺什么', async () => {
    const { api } = await mount('/purchase/new');
    await userEvent.click(await screen.findByRole('button', { name: '保存草稿' }));
    expect(api.createPurchaseOrder).not.toHaveBeenCalled();
    expect(screen.getByTestId('save-missing')).toHaveTextContent('供应商、采购方、采购员、质检方式、明细');
  });

  it('★ 候选 503 mirror_stale：错误点名，单头与明细照常', async () => {
    const { ApiError } = await import('../../api/client');
    await mount('/purchase/new', { listCandidateLines: vi.fn(async () => {
      throw new ApiError(503, 'mirror_stale', '镜像过期', { mirrors: ['seller'] }); }) });
    const box = await screen.findByTestId('candidates');
    expect(await within(box).findByText('镜像过期')).toBeInTheDocument();
    expect(within(box).getByText(/mirror_stale/)).toBeInTheDocument();
    expect(screen.getByLabelText('供应商')).toBeInTheDocument();
    expect(screen.getByTestId('po-lines')).toBeInTheDocument();
  });

  it('加载与空态措辞不同', async () => {
    let release: (v: CandidateLines) => void = () => {};
    await mount('/purchase/new', { listCandidateLines: vi.fn(() => new Promise<CandidateLines>((r) => { release = r; })) });
    expect(screen.getByText('正在取可组单记录…')).toBeInTheDocument();
    await act(async () => { release({ ...CANDS, lines: [], excluded: { state: 0, sku: 0, category: 0, period: 0, fully_confirmed: 0 } }); });
    expect(screen.getByText('没有可组单的记录')).toBeInTheDocument();
    expect(screen.queryByText('正在取可组单记录…')).toBeNull();
  });
});

describe('采购单编辑器：已保存草稿', () => {
  it('载入草稿：单头与明细回填；检查用后端 preflight（含只有后端能判的闸）', async () => {
    const { api } = await mount('/purchase/1');
    expect(await screen.findByRole('heading', { name: /SCM-PO-1/ })).toBeInTheDocument();
    expect(screen.getByLabelText('供应商')).toHaveValue('S-1001');
    const line = screen.getByTestId('po-line-1');
    expect(within(line).getByLabelText('单价')).toHaveValue('12.5000');
    expect(api.getPreflight).toHaveBeenCalledWith(1);
    const box = screen.getByTestId('preflight');
    expect(within(box).getByText('来源记录仍可组单').closest('li')).toHaveClass('gate__item--fail');
    // 本单占着的 30 件算回该店可用：后端 remaining 80 + 本单 30 − 本稿 30
    expect(within(await sellerRow(101, '11072')).getByTestId('avail')).toHaveTextContent('80');
  });

  it('保存：带 draft_version，toast「已保存草稿」，之后重新取 preflight', async () => {
    const { api } = await mount('/purchase/1');
    const line = await screen.findByTestId('po-line-1');
    await userEvent.clear(within(line).getByLabelText('单价'));
    await userEvent.type(within(line).getByLabelText('单价'), '13');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(api.updatePurchaseOrder).toHaveBeenCalledWith(1, expect.objectContaining({ draft_version: 4 }));
    expect(await screen.findByRole('status')).toHaveTextContent('已保存草稿');
    expect(api.getPreflight).toHaveBeenCalledTimes(2);
  });

  it('★ 409 stale_draft：ErrorDetail +「重新载入」；本地改动留到点了才丢', async () => {
    const { ApiError } = await import('../../api/client');
    const { api } = await mount('/purchase/1', { updatePurchaseOrder: vi.fn(async () => {
      throw new ApiError(409, 'stale_draft', '草稿已被别人改过，请刷新后再改', { po_id: 1, current_version: 5, got: 4 }); }) });
    const line = await screen.findByTestId('po-line-1');
    await userEvent.clear(within(line).getByLabelText('单价'));
    await userEvent.type(within(line).getByLabelText('单价'), '99');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    const alert = await screen.findByText('草稿已被别人改过，请刷新后再改');
    expect(alert.closest('[role=alert]')).toHaveTextContent('current_version');
    expect(within(screen.getByTestId('po-line-1')).getByLabelText('单价')).toHaveValue('99');
    api.getPurchaseOrder.mockResolvedValueOnce({ ...DRAFT, draft_version: 5,
      lines: [{ ...DRAFT.lines[0]!, price: '15.0000' }] });
    await userEvent.click(screen.getByRole('button', { name: '重新载入' }));
    expect(within(screen.getByTestId('po-line-1')).getByLabelText('单价')).toHaveValue('15.0000');
    expect(screen.queryByText('草稿已被别人改过，请刷新后再改')).toBeNull();
  });

  it('后端 409 over_seller_cap：点名字段', async () => {
    const { ApiError } = await import('../../api/client');
    await mount('/purchase/1', { updatePurchaseOrder: vi.fn(async () => {
      throw new ApiError(409, 'over_seller_cap', '超过该店上限（按期望销量占比）',
        { line_id: 101, seller_id: '11072', cap: 80, used: 60, requested: 30 }); }) });
    await screen.findByTestId('po-line-1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    const box = (await screen.findByText('超过该店上限（按期望销量占比）')).closest('[role=alert]')!;
    expect(box).toHaveTextContent('seller_id');
    expect(box).toHaveTextContent('11072');
  });

  it('删除草稿：二次确认（非 danger），成功回采购首页', async () => {
    const { api } = await mount('/purchase/1');
    await userEvent.click(await screen.findByRole('button', { name: '删除草稿' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg.querySelector('.irreversible')).toBeNull();
    await userEvent.click(within(dlg).getByRole('button', { name: '删除草稿' }));
    expect(api.deletePurchaseOrder).toHaveBeenCalledWith(1);
    expect(await screen.findByText('HOME-PAGE')).toBeInTheDocument();
  });

  it('移除来源 → 该行消失，检查改用本地（未保存）', async () => {
    await mount('/purchase/1');
    const line = await screen.findByTestId('po-line-1');
    await userEvent.click(within(line).getByRole('button', { name: '移除' }));
    expect(screen.queryByTestId('po-line-1')).toBeNull();
    const box = screen.getByTestId('preflight');
    expect(within(box).getByText('至少一行明细').closest('li')).toHaveClass('gate__item--fail');
    expect(within(box).queryByText('来源记录仍可组单')).toBeNull();
  });

  it('已下单的单不进编辑器', async () => {
    await mount('/purchase/1', { getPurchaseOrder: vi.fn(async () => ({ ...DRAFT, state: '已下单', order_sn: 'PO1' })) });
    expect(await screen.findByTestId('po-axes')).toHaveTextContent('已下单');
    expect(await screen.findByTestId('trace-101')).toHaveTextContent('已下单 30');
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
  });

  it('单号不存在：错误点名，不白屏', async () => {
    const { ApiError } = await import('../../api/client');
    await mount('/purchase/77', { getPurchaseOrder: vi.fn(async () => {
      throw new ApiError(404, 'po_not_found', '采购单不存在', { po_id: 77 }); }) });
    expect(await screen.findByText('采购单不存在')).toBeInTheDocument();
    expect(screen.getByRole('heading')).toBeInTheDocument();
  });

  it('★ 本单把记录组满（候选不列）：单列「本单原有来源」，移除后能加回，超过原有量拒绝', async () => {
    const full: PurchaseOrder = { ...DRAFT, lines: [{ ...DRAFT.lines[0]!, sku: 'SKU-Z', units: 50,
      sources: [{ line_id: 200, seller_id: '11072', units: 50 }], sources_units: 50 }] };
    await mount('/purchase/1', { getPurchaseOrder: vi.fn(async () => full) });
    const own = await screen.findByTestId('own-200');
    expect(own).toHaveTextContent('本单原有来源');
    const row = within(own).getByTestId('seller-11072');
    expect(within(row).getByTestId('avail')).toHaveTextContent('0');
    expect(within(row).getByRole('button', { name: '加入' })).toBeDisabled();

    await userEvent.click(within(screen.getByTestId('po-line-1')).getByRole('button', { name: '移除' }));
    expect(screen.queryByTestId('po-line-1')).toBeNull();
    const row2 = within(screen.getByTestId('own-200')).getByTestId('seller-11072');
    expect(within(row2).getByTestId('avail')).toHaveTextContent('50');
    await userEvent.type(within(row2).getByRole('spinbutton'), '51');
    await userEvent.click(within(row2).getByRole('button', { name: '加入' }));
    expect(row2).toHaveTextContent('最多还能加 50');
    expect(screen.queryByTestId('po-line-1')).toBeNull();
    await userEvent.clear(within(row2).getByRole('spinbutton'));
    await userEvent.type(within(row2).getByRole('spinbutton'), '50');
    await userEvent.click(within(row2).getByRole('button', { name: '加入' }));
    const line = await screen.findByTestId('po-line-1');
    expect(line).toHaveTextContent('SKU-Z');
    expect(within(line).getByTestId('sources')).toHaveTextContent('50');
  });

  it('记录级剩余与分店可用同口径（加回本单已保存）', async () => {
    await mount('/purchase/1');
    expect(await screen.findByTestId('cand-101')).toHaveTextContent('剩 120/500');
    await userEvent.click(within(screen.getByTestId('po-line-1')).getByRole('button', { name: '移除' }));
    expect(screen.getByTestId('cand-101')).toHaveTextContent('剩 150/500');
  });

  it('稿变了，旧的拒绝理由收掉', async () => {
    await mount('/purchase/1');
    const row = await sellerRow(101, '11072');
    await userEvent.type(within(row).getByRole('spinbutton'), '999');
    await userEvent.click(within(row).getByRole('button', { name: '加入' }));
    expect(row).toHaveTextContent('最多还能加');
    await userEvent.click(within(screen.getByTestId('po-line-1')).getByRole('button', { name: '移除' }));
    expect(await sellerRow(101, '11072')).not.toHaveTextContent('最多还能加');
  });

  it('数量填小数或负数：不截断，明说未采用', async () => {
    await mount('/purchase/1');
    const line = await screen.findByTestId('po-line-1');
    const qty = within(line).getByLabelText('数量');
    await userEvent.clear(qty);
    await userEvent.type(qty, '-5');
    expect(line).toHaveTextContent('数量要是非负整数，未采用');
    expect(qty).toHaveAttribute('aria-invalid', 'true');
    // 没被采用：仍跟随来源合计，不出「少于来源合计」
    expect(line).not.toHaveTextContent('少于来源合计');
    await userEvent.clear(qty);
    await userEvent.type(qty, '40.5');
    expect(line).toHaveTextContent('数量要是非负整数，未采用');
    expect(line).toHaveTextContent('无计划来源 10 件');   // 停在最后一个合法值 40，没截成别的
  });
});

describe('店名', () => {
  it('★ listSellers 失败：按店编号显示，且明说取不到', async () => {
    const { ApiError } = await import('../../api/client');
    await mount('/purchase/new', { listSellers: vi.fn(async () => { throw new ApiError(500, 'boom', '店铺取不到', {}); }) });
    const box = await screen.findByTestId('seller-names-failed');
    expect(box).toHaveTextContent('店名取不到，按店编号显示');
    expect(box).toHaveTextContent('店铺取不到');
    expect(await screen.findByTestId('cand-103')).toHaveTextContent('无分店依据：11072');
  });
});

describe('后端 409 点名字段', () => {
  const cases: [string, string, Record<string, unknown>, string[]][] = [
    ['over_total', '超过记录总量', { line_id: 101, total: 500, used: 480, requested: 30 }, ['line_id', 'total', 'used', 'requested', '480']],
    ['duplicate_line', '同一张单里同货号同仓只能一行', { sku: 'SKU-A', wid: 'W-SZ' }, ['sku', 'SKU-A', 'wid', 'W-SZ']],
    ['unplanned_reason_required', '超出计划来源的数量必须写理由', { sku: 'SKU-A', unplanned: 20 }, ['unplanned', '20']],
    ['no_seller_basis', '期望销量为 0 或 unknown 的店无法定分店上限', { line_id: 103, sellers: ['11072'] }, ['sellers', '["11072"]']],
    ['line_not_orderable', '只有 已提交/已确认 的记录能组单', { line_id: 101, state: '已撤销' }, ['state', '已撤销']],
    ['unknown_supplier', '没有这个供应商（不在供应商镜像里）', { supplier_id: 'S-9' }, ['supplier_id', 'S-9']],
    ['missing_required', '单价必填且为数字', { missing: ['lines[].price'], sku: 'SKU-A' }, ['missing', 'lines[].price']],
  ];
  for (const [code, hint, fields, shown] of cases) {
    it(`${code}：hint、错误码与字段都在`, async () => {
      const { ApiError } = await import('../../api/client');
      await mount('/purchase/1', { updatePurchaseOrder: vi.fn(async () => {
        throw new ApiError(code === 'over_total' || code === 'no_seller_basis' || code === 'line_not_orderable' ? 409 : 400,
          code, hint, fields); }) });
      await screen.findByTestId('po-line-1');
      await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
      const box = (await screen.findByText(hint)).closest('[role=alert]')!;
      expect(box).toHaveTextContent(code);
      for (const t of shown) expect(box).toHaveTextContent(t);
    });
  }
});

