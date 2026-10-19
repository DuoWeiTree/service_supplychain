import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter } from 'react-router-dom';
import type { CandidateLines, PurchaseOptions, PurchaseOrder } from '../../api/types';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const OPTIONS: PurchaseOptions = {
  suppliers: [{ supplier_id: 'S-1001', name: '东莞宏达' }],
  warehouses: [{ wid: 101, name: '深圳本地仓' }, { wid: 102, name: '宁波本地仓' }],
  purchasers: [{ purchaser_id: 111, source: '领星历史', count: 135 }, { purchaser_id: 185, source: '本服务', count: 2 }],
  opt_uids: [{ opt_uid: 10925905, name: '陈彩凤', source: '领星历史', count: 143 }],
  history: { available: true, cause: null, as_of: '2026-10-08', dropped: 0 },
};
const CANDS: CandidateLines = {
  lines: [
    { line_id: 101, plan_id: 3, plan_title: '春季计划', rev: 1, sku: 'SKU-A', period: '2026-10', total_units: 500, demand_at_submit: 60,
      demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 120,
      sellers: { '11072': { expected: 40, used: 0 },
                 '11094': { expected: 20, used: 40 } } },
    { line_id: 103, plan_id: 3, plan_title: '春季计划', rev: 1, sku: 'SKU-B', period: '2026-11', total_units: 80, demand_at_submit: 0,
      demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0, remaining: 80,
      sellers: { '11072': { expected: null, used: 0 } } },
  ],
  groups: {},
  excluded: { state: 1, sku: 0, category: 0, period: 0, fully_confirmed: 2 },
};
/** 被本单组满的记录 200：候选里没有它，来源链只能靠单自己带 */
const USED_UP = { plan_id: 4, plan_title: '夏季计划', rev: 3, sku: 'SKU-Z', period: '2026-12' };
const DRAFT: PurchaseOrder = {
  po_id: 1, supplier_id: 'S-1001', purchaser_id: 111, opt_uid: 10925905, qc_type: 1, wid: 101,
  custom_order_sn: 'SCM-PO-1', order_sn: null, state: '草稿', placed_at: null, draft_version: 4,
  created_by: 'ops.zhang', is_void: false, discarded_at: null,
  lines: [{ line_no: 1, sku: 'SKU-A', wid: 101, units: 30, price: '12.5000', expect_arrive_date: null,
    unplanned_reason: null, lx_item_id: null, sources: [{ line_id: 101, seller_id: '11072', units: 30,
      plan_id: 3, plan_title: '春季计划', rev: 1, sku: 'SKU-A', period: '2026-10' }],
    sources_units: 30, unplanned_units: 0 }],
  source_line_states: [{ line_id: 101, state: '已提交' }], attention: [], preflight: [], confirm_token: 't',
  lx: null,
};

/** `history`：给了就用 data router（离开保护的 useBlocker 只在 data router 里生效），entry 是栈顶 */
async function mount(entry: string, over: Record<string, unknown> = {}, history?: string[]) {
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
  if (history) {
    // data router 每次导航 new Request({ signal })：jsdom 的 AbortSignal 过不了 node 的 Request 校验。
    // 路由只读 url / method / signal，给它一个只带这三样的 Request
    vi.stubGlobal('Request', class {
      url: string; method: string; signal: AbortSignal | undefined;
      constructor(url: string | URL, init?: { method?: string; signal?: AbortSignal }) {
        this.url = String(url); this.method = init?.method ?? 'GET'; this.signal = init?.signal;
      }
    });
    const router = createMemoryRouter([
      { path: '/purchase', element: <div>HOME-PAGE</div> },
      { path: '/purchase/new', element: <PoEditor poId={null} /> },
      { path: '/purchase/:poId', element: <PoRoute /> },
    ], { initialEntries: [...history, entry], initialIndex: history.length });
    render(<RouterProvider router={router} />);
    await act(async () => {});
    return { api, ApiError, router };
  }
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
  return { api, ApiError, router: null };
}

/** UX-D5：左栏货号默认收起（带记录进来 / 本单已填的除外）；要看某条记录先全部展开 */
const showRec = async (lineId: number) => {
  await screen.findByTestId('candidates');
  if (!screen.queryByTestId(`cand-${lineId}`)) {
    const btn = await screen.findByRole('button', { name: '全部展开' });
    await userEvent.click(btn);
  }
  return screen.findByTestId(`cand-${lineId}`);
};
const sellerRow = async (lineId: number, sid: string) =>
  within(await showRec(lineId)).getByTestId(`seller-${sid}`);
/** 设计稿：在店铺行的件数框里直接打数，Enter 采用（绝对量） */
const allot = async (row: HTMLElement, n: string) => {
  const box = within(row).getByRole('textbox');
  await userEvent.clear(box);
  await userEvent.type(box, `${n}{Enter}`);
};

describe('采购单编辑器：新建', () => {
  it('★ 设计稿：店铺行直接打件数 → 明细出现该货号，数量默认 = 来源合计', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await allot(row, '30');
    const line = await screen.findByTestId('po-line-1');
    expect(line).toHaveTextContent('SKU-A');
    expect(within(line).getByTestId('sources')).toHaveTextContent('A4Pet-US 30 件');
    expect(within(line).getByLabelText('数量')).toHaveValue(30);
    // 本稿已加的量从记录剩余里扣掉
    expect(await screen.findByTestId('cand-101')).toHaveTextContent('剩 90 / 500');
  });

  it('★ PB-D22 没有分店上限：一家店能拿满记录剩余；超过才拒并点名', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await allot(row, '121');
    expect(row).toHaveTextContent('超过该记录剩余，最多还能加 120');
    expect(screen.queryByTestId('po-line-1')).toBeNull();
  });

  it('记录剩余 0：各店件数框禁用、占位写「已无剩余」，「剩余全给」也禁用', async () => {
    await mount('/purchase/new', { listCandidateLines: vi.fn(async () =>
      ({ ...CANDS, lines: [{ ...CANDS.lines[0]!, remaining: 0 }] })) });
    for (const sid of ['11072', '11094']) {
      const row = await sellerRow(101, sid);
      expect(within(row).getByRole('textbox')).toBeDisabled();
      expect(within(row).getByRole('textbox')).toHaveAttribute('placeholder', '已无剩余');
      expect(within(row).getByRole('button', { name: '剩余全给' })).toBeDisabled();
    }
  });

  it('★ 设计稿：再打一次是改量不是叠加；「剩余全给」一键给满；清空 = 去掉这条来源', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await allot(row, '30');
    await allot(row, '20');
    expect(within(await screen.findByTestId('po-line-1')).getByTestId('sources')).toHaveTextContent('A4Pet-US 20 件');
    await userEvent.click(within(await sellerRow(101, '11094')).getByRole('button', { name: '剩余全给' }));
    expect(screen.getByTestId('cand-101')).toHaveTextContent('剩 0 / 500');
    const box = within(await sellerRow(101, '11072')).getByRole('textbox');
    await userEvent.clear(box);
    await userEvent.type(box, '{Enter}');
    expect(within(screen.getByTestId('po-line-1')).getByTestId('sources')).not.toHaveTextContent('A4Pet-US');
  });

  it('★ PB-D22 期望为空（本月断货）的记录：不灰显、期望写未知、照样能加', async () => {
    await mount('/purchase/new');
    const rec = await showRec(103);
    expect(rec).not.toHaveClass('muted');
    expect(rec).not.toHaveTextContent('无分店依据');
    const row = within(rec).getByTestId('seller-11072');
    expect(within(row).getByTestId('expected')).not.toHaveTextContent('0');
    await allot(row, '80');
    expect(await screen.findByTestId('po-line-1')).toHaveTextContent('SKU-B');
  });

  it('被滤掉的计数：总数可见，分项进折叠（不是错误，默认收起）', async () => {
    await mount('/purchase/new');
    const fold = await screen.findByTestId('cand-dropped');
    expect(fold.tagName).toBe('DETAILS');
    expect(fold).not.toHaveAttribute('open');
    expect(fold.querySelector('summary')).toHaveTextContent('未列入 2 条');
    const drops = Array.from(fold.querySelectorAll('.dropline')).map((x) => x.textContent);
    expect(drops).toEqual(['已组满 2 条']);
    // ★ PB-D29：状态不可组单（旧版被替换 / 已完结的历史）不显示
    expect(document.body).not.toHaveTextContent('状态不可组单');
  });

  it('数量改大 → 出现「无计划来源 N 件」与理由；对应闸红色点名', async () => {
    await mount('/purchase/new');
    const row = await sellerRow(101, '11072');
    await allot(row, '30');
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
    await allot(row, '30');
    const line = screen.getByTestId('po-line-1');
    // 设计稿：仓在单头，整张单一个仓（PB-D25），写到每一行
    await userEvent.selectOptions(screen.getByLabelText('仓库'), '102');
    await userEvent.type(within(line).getByLabelText('单价'), '3.20');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(api.createPurchaseOrder).toHaveBeenCalledWith({
      supplier_id: 'S-1001', purchaser_id: 222, opt_uid: 10925905, qc_type: 2,
      lines: [{ sku: 'SKU-A', wid: 102, units: 30, price: '3.20',
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
    // 本单占着的 30 件算回记录剩余：后端 remaining 120 + 本单 30 − 本稿 30
    expect(await screen.findByTestId('cand-101')).toHaveTextContent('剩 120 / 500');
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

  it('后端 409 seller_not_in_line：点名字段', async () => {
    const { ApiError } = await import('../../api/client');
    await mount('/purchase/1', { updatePurchaseOrder: vi.fn(async () => {
      throw new ApiError(409, 'seller_not_in_line', '来源店不在这条记录里（只能记到计划里认领了该货号的店）',
        { line_id: 101, seller_ids: ['99999'], allowed: ['11072', '11094'] }); }) });
    await screen.findByTestId('po-line-1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    const box = (await screen.findByText('来源店不在这条记录里（只能记到计划里认领了该货号的店）')).closest('[role=alert]')!;
    expect(box).toHaveTextContent('seller_ids');
    expect(box).toHaveTextContent('99999');
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
      sources: [{ line_id: 200, seller_id: '11072', units: 50, ...USED_UP }], sources_units: 50 }] };
    await mount('/purchase/1', { getPurchaseOrder: vi.fn(async () => full) });
    const own = await screen.findByTestId('own-200');
    expect(own).toHaveTextContent('本单原有来源');
    const row = within(own).getByTestId('seller-11072');
    expect(within(own).getByTestId('own-avail')).toHaveTextContent('0');
    expect(within(row).getByRole('textbox')).toHaveValue('50');      // 本单现有的量，可改小

    await userEvent.click(within(screen.getByTestId('po-line-1')).getByRole('button', { name: '移除' }));
    expect(screen.queryByTestId('po-line-1')).toBeNull();
    const row2 = within(screen.getByTestId('own-200')).getByTestId('seller-11072');
    expect(within(screen.getByTestId('own-200')).getByTestId('own-avail')).toHaveTextContent('50');
    await allot(row2, '51');
    expect(within(screen.getByTestId('own-200')).getByTestId('seller-11072')).toHaveTextContent('最多还能加 50');
    expect(screen.queryByTestId('po-line-1')).toBeNull();
    await allot(within(screen.getByTestId('own-200')).getByTestId('seller-11072'), '50');
    const line = await screen.findByTestId('po-line-1');
    expect(line).toHaveTextContent('SKU-Z');
    expect(within(line).getByTestId('sources')).toHaveTextContent('50');
  });

  it('记录级剩余加回本单已保存', async () => {
    await mount('/purchase/1');
    expect(await screen.findByTestId('cand-101')).toHaveTextContent('剩 120 / 500');
    await userEvent.click(within(screen.getByTestId('po-line-1')).getByRole('button', { name: '移除' }));
    expect(screen.getByTestId('cand-101')).toHaveTextContent('剩 150 / 500');
  });

  it('稿变了，旧的拒绝理由收掉', async () => {
    await mount('/purchase/1');
    const row = await sellerRow(101, '11072');
    await allot(row, '999');
    expect(await sellerRow(101, '11072')).toHaveTextContent('最多还能加');
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
    expect(within(await showRec(103)).getByTestId('seller-11072')).toHaveTextContent('11072');
  });
});

describe('后端 409 点名字段', () => {
  const cases: [string, string, Record<string, unknown>, string[]][] = [
    ['over_total', '超过记录总量', { line_id: 101, total: 500, used: 480, requested: 30 }, ['line_id', 'total', 'used', 'requested', '480']],
    ['duplicate_line', '同一张单里同货号同仓只能一行', { sku: 'SKU-A', wid: 101 }, ['sku', 'SKU-A', 'wid', '101']],
    ['unplanned_reason_required', '超出计划来源的数量必须写理由', { sku: 'SKU-A', unplanned: 20 }, ['unplanned', '20']],
    ['seller_not_in_line', '来源店不在这条记录里（只能记到计划里认领了该货号的店）', { line_id: 101, seller_ids: ['99999'], allowed: ['11072'] }, ['seller_ids', '["99999"]']],
    ['line_not_orderable', '只有 已提交/已确认 的记录能组单', { line_id: 101, state: '已撤销' }, ['state', '已撤销']],
    ['unknown_supplier', '没有这个供应商（不在供应商镜像里）', { supplier_id: 'S-9' }, ['supplier_id', 'S-9']],
    ['missing_required', '单价必填且为数字', { missing: ['lines[].price'], sku: 'SKU-A' }, ['missing', 'lines[].price']],
  ];
  for (const [code, hint, fields, shown] of cases) {
    it(`${code}：hint、错误码与字段都在`, async () => {
      const { ApiError } = await import('../../api/client');
      await mount('/purchase/1', { updatePurchaseOrder: vi.fn(async () => {
        throw new ApiError(code === 'over_total' || code === 'seller_not_in_line' || code === 'line_not_orderable' ? 409 : 400,
          code, hint, fields); }) });
      await screen.findByTestId('po-line-1');
      await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
      const box = (await screen.findByText(hint)).closest('[role=alert]')!;
      expect(box).toHaveTextContent(code);
      for (const t of shown) expect(box).toHaveTextContent(t);
    });
  }
});


describe('★ 写动作的处理函数自己抛了：不许静默', () => {
  afterEach(() => { vi.doUnmock('./poEditorModel'); });

  it('toBody 抛 TypeError → 屏上 ErrorDetail 点名，日志带异常名', async () => {
    vi.doMock('./poEditorModel', async (orig) => ({
      ...(await orig<typeof import('./poEditorModel')>()),
      toBody: () => { throw new TypeError('h[k].trim is not a function'); },
    }));
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { api } = await mount('/purchase/1');
    await screen.findByTestId('po-line-1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('界面内部出错，这一步没有完成（TypeError: h[k].trim is not a function）');
    expect(alert).toHaveTextContent('client_exception');
    expect(api.updatePurchaseOrder).not.toHaveBeenCalled();
    expect(err.mock.calls.some((c) => String(c[0]).includes('[po-editor] save threw'))).toBe(true);
  });
});

describe('★ 从待采购带过来（?lines=，R3）', () => {
  const trace = (state: string, over: Record<string, unknown> = {}) => ({ line_id: 13, sku: 'SKU-X', period: '2026-10',
    total_units: 50, state, confirmed_units: 0, ordered_units: 0, purchase_orders: [], ...over });

  it('左栏只列带来的记录；顶部写明来自哪张计划哪一版几条', async () => {
    await mount('/purchase/new?lines=101');
    expect(await screen.findByTestId('cand-101')).toBeInTheDocument();
    expect(screen.queryByTestId('cand-103')).toBeNull();
    expect(screen.getByTestId('source-summary')).toHaveTextContent('来自 销售计划「春季计划」rev1 的 1 条记录');
    // 带来的记录默认展开：分店表就在眼前
    expect(within(screen.getByTestId('cand-101')).getByTestId('seller-11072')).toBeVisible();
  });

  it('「加入其他待采购记录」恢复全部候选', async () => {
    await mount('/purchase/new?lines=101');
    await screen.findByTestId('cand-101');
    await userEvent.click(screen.getByRole('button', { name: '加入其他待采购记录' }));
    // 带来的那条仍展开；新放开的货号默认收起，货号行写得出它有几条
    expect(screen.getByTestId('cand-101')).toBeInTheDocument();
    expect(await screen.findByTestId('cand-sku-SKU-B')).toHaveTextContent('1 条记录，剩 80，本单已填 0');
    expect(await showRec(103)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加入其他待采购记录' })).toBeNull();
  });

  it('★ 带来的记录已不可组单 / 不存在 / 不是记录号：逐条点名，不静默丢', async () => {
    const { ApiError } = await import('../../api/client');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { api } = await mount('/purchase/new?lines=101,13,15,abc', {
      getLineTrace: vi.fn(async (id: number) => {
        if (id === 13) return trace('已撤销');
        throw new ApiError(404, 'line_not_found', '没有这条记录', { line_id: id });
      }),
    });
    const box = await screen.findByTestId('preselect-missing');
    expect(await within(box).findByText('记录 13 已不可组单（状态 已撤销）')).toBeInTheDocument();
    expect(await within(box).findByText('记录 15 不存在')).toBeInTheDocument();
    expect(within(box).getByText('「abc」不是记录号')).toBeInTheDocument();
    expect(api.getLineTrace).not.toHaveBeenCalledWith(101);
    // 摘要只数列得出的
    expect(screen.getByTestId('source-summary')).toHaveTextContent('的 1 条记录');
    warn.mockRestore();
  });

  it('带来的记录组满了：说「已组满」，不说「已不可组单（状态 已确认）」', async () => {
    await mount('/purchase/new?lines=13', {
      getLineTrace: vi.fn(async () => trace('已确认', { confirmed_units: 50 })),
    });
    expect(await screen.findByText('记录 13 已组满（共 50 件）')).toBeInTheDocument();
  });

  it('反查状态失败：点名取不到，ErrorDetail 给原因', async () => {
    const { ApiError } = await import('../../api/client');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    await mount('/purchase/new?lines=13', {
      getLineTrace: vi.fn(async () => { throw new ApiError(500, 'boom', '后端炸了', {}); }),
    });
    expect(await screen.findByText('记录 13 状态取不到')).toBeInTheDocument();
    expect(screen.getByText('后端炸了')).toBeInTheDocument();
    expect(warn.mock.calls.some((c) => String(c[0]).includes('getLineTrace'))).toBe(true);
    warn.mockRestore();
  });

  it('没带 ?lines=：列全部，不出摘要', async () => {
    await mount('/purchase/new');
    expect(await showRec(103)).toBeInTheDocument();
    expect(screen.queryByTestId('source-summary')).toBeNull();
  });
});

describe('★ 明细里的来源链：不展开就看得到', () => {
  it('新加的来源：计划 rev 货号 月份 → 店名 件数', async () => {
    await mount('/purchase/new?lines=101');
    const row = await sellerRow(101, '11072');
    await allot(row, '30');
    const src = within(await screen.findByTestId('po-line-1')).getByTestId('sources');
    expect(src).toHaveTextContent('春季计划 rev1 SKU-A 2026-10 → A4Pet-US 30 件');
  });

  it('★ 草稿把记录组满后重新载入（候选不再列它）：来源链照样完整', async () => {
    const full: PurchaseOrder = { ...DRAFT, lines: [{ ...DRAFT.lines[0]!, sku: 'SKU-Z', units: 50,
      sources: [{ line_id: 200, seller_id: '11072', units: 50, ...USED_UP }], sources_units: 50 }] };
    await mount('/purchase/1', { getPurchaseOrder: vi.fn(async () => full) });
    const src = within(await screen.findByTestId('po-line-1')).getByTestId('sources');
    expect(src).toHaveTextContent('夏季计划 rev3 SKU-Z 2026-12 → A4Pet-US 50 件');
  });

  it('已保存草稿的来源同样给链', async () => {
    await mount('/purchase/1');
    const src = within(await screen.findByTestId('po-line-1')).getByTestId('sources');
    expect(src).toHaveTextContent('春季计划 rev1 SKU-A 2026-10 → A4Pet-US 30 件');
  });
});

describe('★ 续编草稿与量大时的左栏（UX-D4 / D5）', () => {
  const OTHER = { ...CANDS.lines[1]!, line_id: 105, plan_id: 7, plan_title: '别的计划', sku: 'SKU-C' };
  const WITH_OTHER: CandidateLines = { ...CANDS, lines: [...CANDS.lines, OTHER] };

  it('★ 续编只列本单来源计划的记录，写明来自哪个计划；「加入其他待采购记录」放开', async () => {
    await mount('/purchase/1', { listCandidateLines: vi.fn(async () => WITH_OTHER) });
    expect(await screen.findByTestId('cand-101')).toBeInTheDocument();
    expect(screen.getByTestId('source-summary')).toHaveTextContent('来自 销售计划「春季计划」rev1 的 1 条记录');
    expect(screen.queryByTestId('cand-sku-SKU-C')).toBeNull();
    expect(screen.getByTestId('cand-sku-SKU-B')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '加入其他待采购记录' }));
    expect(await screen.findByTestId('cand-sku-SKU-C')).toBeInTheDocument();
  });

  it('本单没有任何计划来源：不收窄', async () => {
    const blank: PurchaseOrder = { ...DRAFT, lines: [{ ...DRAFT.lines[0]!, sources: [], sources_units: 0, unplanned_units: 30, unplanned_reason: '补' }] };
    await mount('/purchase/1', { getPurchaseOrder: vi.fn(async () => blank), listCandidateLines: vi.fn(async () => WITH_OTHER) });
    expect(await screen.findByTestId('cand-sku-SKU-C')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加入其他待采购记录' })).toBeNull();
  });

  it('★ 按货号折叠：本单已填的展开，其余收起，货号行写记录数 · 剩 · 本单已填', async () => {
    await mount('/purchase/1');
    expect(await screen.findByTestId('cand-101')).toBeInTheDocument();
    expect(screen.queryByTestId('cand-103')).toBeNull();
    const head = within(screen.getByTestId('cand-sku-SKU-A')).getByRole('button', { name: /^SKU-A/ });
    expect(head).toHaveAttribute('aria-expanded', 'true');
    expect(head).toHaveTextContent('1 条记录，剩 120，本单已填 30');
    await userEvent.click(head);
    expect(screen.queryByTestId('cand-101')).toBeNull();
  });

  it('★ 搜货号：只留命中的货号并展开；搜不到写没有匹配', async () => {
    await mount('/purchase/new');
    await userEvent.type(await screen.findByRole('searchbox', { name: '搜货号' }), 'sku-b');
    expect(screen.getByTestId('cand-103')).toBeInTheDocument();
    expect(screen.queryByTestId('cand-sku-SKU-A')).toBeNull();
    await userEvent.clear(screen.getByRole('searchbox', { name: '搜货号' }));
    await userEvent.type(screen.getByRole('searchbox', { name: '搜货号' }), 'NOPE');
    expect(screen.getByText('没有匹配「NOPE」的货号')).toBeInTheDocument();
  });

  it('★ 右栏一行一货号：来源折在行内，写条数与件数', async () => {
    await mount('/purchase/1');
    const line = await screen.findByTestId('po-line-1');
    expect(line.tagName).toBe('TR');
    expect(line).toHaveTextContent('1 条来源，30 件');
  });
});

describe('★ 回列表与离开保护（UX-D6 · 评审 P1）', () => {
  it('面包屑「采购」链回列表并定位到来源计划', async () => {
    await mount('/purchase/1');
    const crumbs = await screen.findByTestId('po-crumbs');
    expect(within(crumbs).getByRole('link', { name: '采购' })).toHaveAttribute('href', '/purchase?plan=3');
    expect(crumbs).toHaveTextContent('SCM-PO-1');
  });

  it('新建带记录进来：面包屑定位到带来的计划', async () => {
    await mount('/purchase/new?lines=101');
    await screen.findByTestId('cand-101');
    expect(within(screen.getByTestId('po-crumbs')).getByRole('link', { name: '采购' })).toHaveAttribute('href', '/purchase?plan=3');
  });

  it('★ 有未保存的改动：点「采购」先问；留下不走，离开才走', async () => {
    await mount('/purchase/1', {}, ['/purchase']);
    const line = await screen.findByTestId('po-line-1');
    await userEvent.type(within(line).getByLabelText('单价'), '9');
    await userEvent.click(within(screen.getByTestId('po-crumbs')).getByRole('link', { name: '采购' }));
    const dlg = await screen.findByRole('dialog', { name: '有未保存的改动' });
    await userEvent.click(within(dlg).getByRole('button', { name: '留下' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('po-line-1')).toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId('po-crumbs')).getByRole('link', { name: '采购' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '离开，丢掉改动' }));
    expect(await screen.findByText('HOME-PAGE')).toBeInTheDocument();
  });

  it('没改过：直接走，不问', async () => {
    await mount('/purchase/1', {}, ['/purchase']);
    await screen.findByTestId('po-line-1');
    await userEvent.click(within(screen.getByTestId('po-crumbs')).getByRole('link', { name: '采购' }));
    expect(await screen.findByText('HOME-PAGE')).toBeInTheDocument();
  });

  it('★ 有未保存的改动时挂 beforeunload；保存后撤掉', async () => {
    const add = vi.spyOn(window, 'addEventListener');
    const rm = vi.spyOn(window, 'removeEventListener');
    await mount('/purchase/1', {}, ['/purchase']);
    const line = await screen.findByTestId('po-line-1');
    await userEvent.type(within(line).getByLabelText('单价'), '9');
    expect(add.mock.calls.some(([t]) => t === 'beforeunload')).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await vi.waitFor(() => expect(rm.mock.calls.some(([t]) => t === 'beforeunload')).toBe(true));
  });

  it('★ 新建保存成功：replace 到 /purchase/:id，不问离开；返回落回列表而不是空编辑器', async () => {
    const { router } = await mount('/purchase/new?lines=101', {}, ['/purchase']);
    await userEvent.selectOptions(await screen.findByLabelText('供应商'), 'S-1001');
    await userEvent.selectOptions(screen.getByLabelText('采购方'), '111');
    await userEvent.selectOptions(screen.getByLabelText('采购员'), '10925905');
    await userEvent.selectOptions(screen.getByLabelText('质检方式'), '2');
    await allot(await sellerRow(101, '11072'), '30');
    await userEvent.selectOptions(screen.getByLabelText('仓库'), '101');
    await userEvent.type(within(screen.getByTestId('po-line-1')).getByLabelText('单价'), '1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await vi.waitFor(() => expect(router!.state.location.pathname).toBe('/purchase/9'));
    expect(screen.queryByRole('dialog', { name: '有未保存的改动' })).toBeNull();
    await act(async () => { await router!.navigate(-1); });
    expect(router!.state.location.pathname).toBe('/purchase');
  });
});
