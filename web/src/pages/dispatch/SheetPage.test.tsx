import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { OwsSubmission, SheetDetail, SheetGrid } from '../../api/types';
import grid from '../../testing/fixtures/contract/sheet-grid.json';
import detail from '../../testing/fixtures/contract/sheet-detail.json';
import detailOs from '../../testing/fixtures/contract/sheet-detail-oversea.json';
import gridOs from '../../testing/fixtures/contract/sheet-grid-oversea.json';
import preflightOs from '../../testing/fixtures/contract/sheet-preflight-oversea.json';
import preflight from '../../testing/fixtures/contract/sheet-preflight.json';
import blocked from '../../testing/fixtures/contract/sheet-confirm-blocked.json';
import cellResult from '../../testing/fixtures/contract/sheet-cell.json';
import channels from '../../testing/fixtures/contract/logistics-channels.json';
import dispatched from '../../testing/fixtures/contract/sheet-detail-dispatched.json';
import subPending from '../../testing/fixtures/contract/ows-submission-pending.json';
import submitDone from '../../testing/fixtures/contract/ows-submit-done.json';
import cancelOws from '../../testing/fixtures/contract/sheet-cancel-ows.json';
import receiptSample from '../../testing/fixtures/contract/sheet-receipt.json';
import fbaSub from '../../testing/fixtures/contract/fba-submission.json';
import fbaToLock from '../../testing/fixtures/contract/fba-submission-to-lock.json';
import fbaRelocked from '../../testing/fixtures/contract/fba-submit-relocked.json';
import fbaDetail from '../../testing/fixtures/contract/sheet-detail-fba-dispatched.json';
import fbaCancel from '../../testing/fixtures/contract/sheet-cancel-fba.json';
import autofillDone from '../../testing/fixtures/contract/sheet-detail-autofill.json';
import autofillFailed from '../../testing/fixtures/contract/sheet-detail-autofill-failed.json';
import { fail, ok, stubBackend, type Handler } from '../../testing/backendStub';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const G = grid as unknown as SheetGrid;
const D = detail as unknown as SheetDetail;
const CELL_A = '件数 MSKU-A 测试仓 FBA A4Pet-US';

/** 海外仓表（CA-D13）：同一个 /v1/sheets/1 换成海外仓样本 */
const OS: Record<string, Handler> = {
  'GET /v1/sheets/1': () => ok({ ...detailOs, sheet_id: 1 }),
  'GET /v1/sheets/1/grid': () => ok({ ...gridOs, sheet: { ...gridOs.sheet, sheet_id: 1 } }),
  'GET /v1/sheets/1/preflight': () => ok({ ...preflightOs, sheet_id: 1 }),
};

function routes(over: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    'GET /v1/attention': () => ok({ count: 0, items: [] }),
    'GET /v1/sheets/1': () => ok(D),
    'GET /v1/sheets/1/grid': () => ok(G),
    'GET /v1/sheets/1/preflight': () => ok(preflight),
    'PUT /v1/sheets/1/cells': () => ok(cellResult),
    ...over,
  };
}

async function mount(r: Record<string, Handler>) {
  const calls = stubBackend(r);
  const { ApiError } = await import('../../api/client');
  const { createHttpApi } = await import('../../api/http');
  vi.doMock('../../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  const { SheetPage } = await import('./SheetPage');
  render(
    <MemoryRouter initialEntries={['/dispatch/sheets/1']}>
      <Routes><Route path="/dispatch/sheets/:sheetId" element={<SheetPage />} /></Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  return calls;
}

const puts = (calls: { method: string; path: string; body: unknown }[], path = '/v1/sheets/1/cells') =>
  calls.filter((c) => c.method === 'PUT' && c.path === path).map((c) => c.body);

describe('排货工作台 · 页头（问题 1：这张计划还差多少）', () => {
  it('写明对应的销售计划；需求 / 本表已排 / 别表 / 已投送 / 还差 / 条数 并排', async () => {
    await mount(routes());
    expect(screen.getByTestId('sheet-plan')).toHaveTextContent('销售计划 t rev1');
    const k = screen.getByTestId('plan-kpis');
    for (const t of ['需求100', '本表已排30', '别的表已排未发0', '已投送0', '还差70', '1 条还差，0 条已满足']) {
      expect(k.textContent!.replace(/\s/g, '')).toContain(t.replace(/\s/g, ''));
    }
    expect(screen.getByTestId('stock-as-of')).toHaveTextContent('库存截至 2026-10-10');
  });
});

describe('左栏 · 记录清单', () => {
  it('每条记录：还差 · 需求 · 本表 · 几个仓有货；默认只列还差 > 0', async () => {
    await mount(routes({ 'GET /v1/sheets/1/grid': () => ok({ ...G, demand: [G.demand[0]!, { ...G.demand[0]!, line_id: 9, pending: 0 }] }) }));
    const rec = screen.getByTestId('rec-1');
    expect(rec).toHaveTextContent('还差 70');
    expect(rec).toHaveTextContent('1 个仓有货');
    expect(screen.queryByTestId('rec-9')).toBeNull();
    await userEvent.click(screen.getByText('全部'));
    expect(screen.getByTestId('rec-9')).toHaveTextContent('已满足');
  });

  it('「未列入」把被丢掉的每一类都给出计数', async () => {
    await mount(routes());
    expect(screen.getByTestId('excluded')).toHaveTextContent('库存行店铺认不出 1 行 / 26 件');
  });
});

describe('右栏 · 安排（问题 2：货在哪）', () => {
  it('需求 / 采购 / 本地库存 三块并排，不相减', async () => {
    await mount(routes());
    expect(screen.getByTestId('side-need')).toHaveTextContent('还差70');
    const p = screen.getByTestId('side-purchase');
    expect(p).toHaveTextContent('已组单100');
    expect(p).toHaveTextContent('已入库（整单）85');
    expect(p).toHaveTextContent('待到货（整单）15');
    expect(p).toHaveTextContent('PO-1');
    const st = screen.getByTestId('stock-table');
    expect(st).toHaveTextContent('测试仓');
    expect(st).toHaveTextContent('55含本店桶 5');
    expect(screen.queryByText(/可排量/)).toBeNull();
  });

  it('每个数带来源：每日采集写「采集」、领星现读写「领星现读」', async () => {
    await mount(routes({ 'GET /v1/sheets/1/grid': () => ok({ ...G,
      stock: G.stock!.map((r) => (r.sku === 'DCC1800264G1' ? { ...r, source: 'api', as_of: '2026-10-12T09:30:00+00:00' } : r)),
      demand: G.demand.map((d) => ({ ...d, purchase_orders: d.purchase_orders.map((p) => ({ ...p, source: 'api' })) })) }) }));
    expect(screen.getByTestId('side-stock')).toHaveTextContent('领星现读');
    expect(screen.getByTestId('side-purchase')).toHaveTextContent('领星读回');
  });

  it('「现查领星」：POST 这个货号，完成后写出结果并重新取', async () => {
    const calls = await mount(routes({ 'POST /v1/sheets/1/live-read': () => ok({ sku: 'DCC1800264G1',
      stock: { read_id: 1, read_at: '2026-10-12T09:30:00+00:00', buckets: [{ wid: 1, buckets: { shared: 70 } }], dropped: {} },
      purchase_orders: [{ po_id: 1, order_sn: 'PO-1', outcome: 'ok', cause: null }] }) }));
    await userEvent.click(screen.getByText('现查领星'));
    expect(calls.find((c) => c.path === '/v1/sheets/1/live-read')?.body).toEqual({ sku: 'DCC1800264G1' });
    expect(screen.getByTestId('ds-flash')).toHaveTextContent('已现查领星 DCC1800264G1：库存 1 个仓，采购单 1 张');
    expect(calls.filter((c) => c.path === '/v1/sheets/1/grid').length).toBe(2);
  });

  it('「现查领星」失败：点名打的谁、怎么失败的，不当成 0', async () => {
    await mount(routes({ 'POST /v1/sheets/1/live-read': () => ok({ sku: 'DCC1800264G1',
      stock: { error: { target: '/erp/sc/routing/data/local_inventory/inventoryDetails', outcome: 'not_sent', cause: 'ECONNREFUSED' } },
      purchase_orders: [{ po_id: 1, order_sn: 'PO-1', outcome: 'unknown', cause: 'timeout' }] }) }));
    await userEvent.click(screen.getByText('现查领星'));
    const f = screen.getByTestId('ds-flash');
    expect(f).toHaveTextContent('库存现读失败（not_sent）');
    expect(f).toHaveTextContent('PO-1 读回 unknown');
  });

  it('关联采购单写明入库仓（采购单行的仓）', async () => {
    await mount(routes());
    const po = within(screen.getByTestId('side-purchase')).getByTestId('po-1');
    expect(po).toHaveTextContent('入库仓 测试仓');
  });

  it('入库仓不在本地仓列表里：写仓号，不编名字', async () => {
    await mount(routes({ 'GET /v1/sheets/1/grid': () => ok({ ...G,
      demand: G.demand.map((d) => ({ ...d, purchase_orders: d.purchase_orders.map((p) => ({ ...p, wid: 999 })) })) }) }));
    expect(within(screen.getByTestId('side-purchase')).getByTestId('po-1')).toHaveTextContent('入库仓 #999');
  });

  it('改已有格子的件数：回车即 PUT（同一格，不带 replaces）', async () => {
    const calls = await mount(routes());
    const input = screen.getByLabelText(CELL_A);
    await userEvent.clear(input);
    await userEvent.type(input, '40{Enter}');
    expect(puts(calls)).toEqual([{ line_id: 1, seller_sku: 'MSKU-A', sid: '11072', from_wid: 1, channel: 'fba',
      dest_wid: null, dest_sid: '11072', units: 40 }]);
    expect(screen.getByTestId('ds-flash')).toHaveTextContent('已排 40 件 MSKU-A → FBA A4Pet-US');
  });

  it('撤回 = 件数 0', async () => {
    const calls = await mount(routes());
    await userEvent.click(within(screen.getByTestId('cell-1')).getByText('撤回'));
    expect((puts(calls)[0] as { units: number }).units).toBe(0);
  });

  it('加一格：来源仓只列有货的仓；填件数回车 → PUT', async () => {
    const calls = await mount(routes());
    const add = screen.getByTestId('add-MSKU-B');
    expect(within(add).getByLabelText('MSKU-B 来源仓')).toHaveTextContent('测试仓 可安排 55');
    await userEvent.selectOptions(within(add).getByLabelText('MSKU-B 目的地'), 'fba/11072');
    await userEvent.type(within(add).getByLabelText('MSKU-B 件数'), '5{Enter}');
    expect(puts(calls)).toEqual([{ line_id: 1, seller_sku: 'MSKU-B', sid: '11072', from_wid: 1, channel: 'fba',
      dest_wid: null, dest_sid: '11072', units: 5 }]);
  });

  it('「排 N」= min(还差, 可安排)，落成一个格子', async () => {
    const calls = await mount(routes());
    await userEvent.selectOptions(within(screen.getByTestId('add-MSKU-B')).getByLabelText('MSKU-B 目的地'), 'fba/11072');
    await userEvent.click(within(screen.getByTestId('add-MSKU-B')).getByText('排 55'));
    expect((puts(calls)[0] as { units: number }).units).toBe(55);
  });

  it('加一格撞上已有格子（同来源仓同目的地）：不发请求，让人去改那一格', async () => {
    const calls = await mount(routes());
    const add = screen.getByTestId('add-MSKU-A');
    await userEvent.selectOptions(within(add).getByLabelText('MSKU-A 目的地'), 'fba/11072');
    await userEvent.type(within(add).getByLabelText('MSKU-A 件数'), '5{Enter}');
    expect(puts(calls)).toEqual([]);
    expect(within(add).getByRole('alert')).toHaveTextContent('这一格已有安排，直接改那一格');
  });

  it('服务端拒绝：原地点名错误码，不出 toast', async () => {
    await mount(routes({ 'PUT /v1/sheets/1/cells': () => fail(409, 'over_total', '超过记录总量', { total: 100, used: 70, requested: 40 }) }));
    const input = screen.getByLabelText(CELL_A);
    await userEvent.clear(input);
    await userEvent.type(input, '40{Enter}');
    const f = screen.getByTestId('ds-flash');
    expect(f).toHaveTextContent('超过记录总量');
    expect(f).toHaveTextContent('409 over_total');
    expect(input).toHaveValue('30');
  });

  it('超出可安排只警告', async () => {
    await mount(routes({ 'PUT /v1/sheets/1/cells': () => ok({ ...cellResult, warnings: [{ code: 'over_available', sku: 'DCC1800264G1', from_wid: 1, sid: '11072', available: -5 }] }) }));
    const input = screen.getByLabelText(CELL_A);
    await userEvent.clear(input);
    await userEvent.type(input, '90{Enter}');
    expect(screen.getByTestId('ds-flash')).toHaveTextContent('超出可安排 5 件（确认时 G3 会挡）');
  });

  it('库存读不到：写原因，可安排未知，不能加格子', async () => {
    await mount(routes({ 'GET /v1/sheets/1/grid': () => ok({ ...G, stock: null, stock_as_of: null,
      stock_error: { target: 'jxd_raw.x', cause: { kind: 'connect_timeout' } } }) }));
    expect(screen.getByTestId('stock-as-of')).toHaveTextContent('库存读不到（connect_timeout）');
    expect(screen.getByTestId('side-stock')).toHaveTextContent('库存读不到：可安排未知');
    expect(screen.getByTestId('add-MSKU-B')).toHaveTextContent('库存读不到，先不能加');
  });

  it('记录已撤销：写明只能撤回，不给加格子', async () => {
    await mount(routes({ 'GET /v1/sheets/1/grid': () => ok({ ...G, demand: G.demand.map((d) => ({ ...d, state: '已撤销' })) }) }));
    await userEvent.click(screen.getByText('全部'));
    expect(screen.getByTestId('arrange')).toHaveTextContent('记录已撤销，只能撤回');
    expect(screen.queryByTestId('add-MSKU-B')).toBeNull();
    expect(screen.getByTestId('cell-1')).toBeInTheDocument();
  });

  it('无计划来源：选货号，line_id 为空写格子', async () => {
    const calls = await mount(routes());
    await userEvent.click(screen.getByRole('tab', { name: /无计划来源/ }));
    await userEvent.click(screen.getByTestId('urec-DCC1800264G1'));
    const add = screen.getByTestId('add-MSKU-C');
    await userEvent.selectOptions(within(add).getByLabelText('MSKU-C 目的地'), 'fba/11094');
    await userEvent.type(within(add).getByLabelText('MSKU-C 件数'), '2{Enter}');
    expect(puts(calls)[0]).toMatchObject({ line_id: null, seller_sku: 'MSKU-C', sid: '11094', units: 2 });
  });
});

describe('排货单与装箱 · 确认（问题 3）', () => {
  it('排货单只读派生；装箱只在 FBA 单渲染，失焦保存整行', async () => {
    const calls = await mount(routes({ 'PUT /v1/sheets/1/packing/MSKU-A/11072/1': () => ok({}) }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const box = screen.getByLabelText('箱数 MSKU-A');
    await userEvent.clear(box);
    await userEvent.type(box, '3');
    await userEvent.tab();
    expect(puts(calls, '/v1/sheets/1/packing/MSKU-A/11072/1')[0]).toMatchObject({ box_count: 3, box_len: 40, units_per_box: 15 });
    expect(screen.queryByText(/新建排货单|删除排货单/)).toBeNull();
  });

  it('底部常驻：每张排货单一个 chip', async () => {
    await mount(routes());
    const dock = screen.getByTestId('dock');
    expect(dock).toHaveTextContent('FBAA4Pet-US30');      // ★ CA-D13：FBA 表只有 FBA 单
  });

  it('确认被挡：切到确认检查，FBA 表六闸全列（不列 G7），未过在前，G5 是「？」不是通过', async () => {
    await mount(routes({ 'POST /v1/sheets/1/confirm': () => ({ status: 409, json: blocked }) }));
    await userEvent.click(screen.getByText('确认排货表'));
    const items = within(screen.getByTestId('gates')).getAllByRole('listitem');
    expect(items).toHaveLength(6);
    expect(items[0]).toHaveTextContent('G1');
    expect(items[0]).toHaveTextContent('1 行没填齐');
    expect(screen.getByTestId('gate-G5')).toHaveTextContent('？');
    expect(screen.getByTestId('blocking')).toHaveTextContent('未过：G1 FBA 行装箱填齐');
  });

  it('预检有未过 / 无法判定的闸（G5 除外）：确认按钮不可用，并点名是哪几道', async () => {
    const gates = preflight.gates.map((g) => (g.gate === 'G3' ? { ...g, ok: null } : g));
    await mount(routes({ 'GET /v1/sheets/1/preflight': () => ok({ ...preflight, ok: false, gates }) }));
    expect(screen.getByText('确认排货表')).toBeDisabled();
    expect(screen.getByTestId('blocking')).toHaveTextContent('未过：G3 本地可发够');
  });

  it('确认带上看到的水位', async () => {
    const calls = await mount(routes({ 'POST /v1/sheets/1/confirm': () => ok({ sheet_id: 1, state: '已确认', gates: [], gate_run: 2 }) }));
    await userEvent.click(screen.getByText('确认排货表'));
    expect(calls.find((c) => c.path === '/v1/sheets/1/confirm')?.body).toEqual({ seen_watermark: D.watermark });
  });

  it('取消要写理由', async () => {
    const calls = await mount(routes({ 'POST /v1/sheets/1/cancel': () => ok({ sheet_id: 1, state: '已取消', from: '草稿' }) }));
    await userEvent.click(screen.getByText('取消排货表'));
    expect(screen.getByText('确认取消')).toBeDisabled();
    await userEvent.type(screen.getByLabelText('取消理由'), '换方案');
    await userEvent.click(screen.getByText('确认取消'));
    expect(calls.find((c) => c.path === '/v1/sheets/1/cancel')?.body).toEqual({ reason: '换方案' });
  });

  it('已确认：格子只读、没有确认按钮，仍可取消（CA-D7）', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, state: '已确认',
      consignments: D.consignments.map((c) => ({ ...c, our_stage: '已确认' })) }) }));
    expect(screen.getByLabelText(CELL_A)).toBeDisabled();
    expect(screen.queryByTestId('add-MSKU-B')).toBeNull();
    expect(screen.queryByText('确认排货表')).toBeNull();
    expect(screen.getByLabelText(CELL_A)).toBeInTheDocument();
    expect(screen.getByText('取消排货表')).toBeInTheDocument();
    expect(screen.getByTestId('ows-sub-error')).toHaveTextContent('提交领星的预览读不到');   // 没桩 /submission：页面照常
  });
});

// ── C-b 提交领星（spec 2026-10-12 §6）──────────────────────────────

const DISP = dispatched as unknown as SheetDetail;
const SUB = subPending as unknown as OwsSubmission;
const CONFIRMED: SheetDetail = { ...DISP, ext_refs: [], consignments: DISP.consignments.map((c) => ({ ...c, our_stage: '已确认' })) };
const FRESH: OwsSubmission = { ...SUB, attention: [] };

function confirmedRoutes(d: SheetDetail, sub: OwsSubmission, over: Record<string, Handler> = {}) {
  return routes({ 'GET /v1/sheets/1': () => ok(d), 'GET /v1/sheets/1/submission': () => ok(sub),
    'POST /v1/sheets/1/receipt': () => ok(receiptSample), ...over });
}

describe('提交领星 · 物流方式（G7）', () => {
  it('草稿：海外仓排货单有物流方式下拉，选了就 PUT 到这个目的仓', async () => {
    const two = { ...channels, channels: [...channels.channels, { ...channels.channels[0]!, logistics_id: 999, channel_name: '快递' }] };
    const calls = await mount(routes({
      ...OS,
      'GET /v1/logistics-channels': () => ok(two),
      'PUT /v1/sheets/1/logistics/2': () => ok({ dest_wid: 2, logistics_id: 999, channel_name: '快递' }),
    }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const sel = within(screen.getByTestId('consignment-2')).getByRole('combobox', { name: /物流方式/ });
    expect(sel).toHaveValue('28762');
    await userEvent.selectOptions(sel, '999');
    expect(puts(calls, '/v1/sheets/1/logistics/2')).toEqual([{ logistics_id: 999 }]);
  });
});

describe('提交领星 · 提交', () => {
  it('已确认：「提交领星」⛔ 确认框逐张列出要建的备货单，确认才带 token 发出', async () => {
    const calls = await mount(confirmedRoutes(CONFIRMED, FRESH, { 'POST /v1/sheets/1/submit': () => ok(submitDone) }));
    await userEvent.click(screen.getByRole('button', { name: '提交领星（2 张）' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('SCM-CN-1-1');
    expect(dlg).toHaveTextContent('SCM-CN-1-3');
    expect(dlg).toHaveTextContent('美森快船');
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/v1/sheets/1/submit')).toEqual([]);
    await userEvent.click(within(dlg).getByRole('button', { name: '确认提交' }));
    expect(calls.filter((c) => c.path === '/v1/sheets/1/submit').map((c) => c.body)).toEqual([{ confirm_token: FRESH.confirm_token }]);
    const res = screen.getByTestId('ows-results');
    expect(res).toHaveTextContent('SCM-CN-1-1');
    expect(res).toHaveTextContent('OWS261012001');
  });

  it('有阻断项：按钮不可点，写明缺什么', async () => {
    await mount(confirmedRoutes(CONFIRMED, { ...FRESH, blockers: [{ code: 'product_id_missing', sku: 'SKU-X' }] }));
    expect(screen.getByRole('button', { name: '提交领星（2 张）' })).toBeDisabled();
    expect(screen.getByTestId('ows-blockers')).toHaveTextContent('SKU-X');
  });

  it('已投送：排货单上写出领星备货单号，不再有提交按钮', async () => {
    await mount(confirmedRoutes(DISP, { ...FRESH, orders: FRESH.orders.map((o, i) => ({ ...o, ext_no: `OWS26101200${i + 1}` })) }));
    expect(screen.queryByRole('button', { name: /提交领星/ })).toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const c = screen.getByTestId('consignment-1');
    expect(c).toHaveTextContent('已投送');
    expect(c).toHaveTextContent('OWS261012001');
    expect(c).toHaveTextContent('OWS261012002');
  });
});

describe('提交领星 · 待核实三出口', () => {
  it('红条点名哪一张；三个出口各打各的端点', async () => {
    const calls = await mount(confirmedRoutes(CONFIRMED, SUB, {
      'POST /v1/sheets/1/ext-refs/verify': () => fail(409, 'still_unknown', '领星里还查不到'),
      'POST /v1/sheets/1/ext-refs/register': () => ok({ attention_id: 1, ext_no: 'OWS9', dispatched: [] }),
      'POST /v1/sheets/1/ext-refs/confirm-absent': () => ok({ attention_id: 1, closed: 'confirm_absent' }),
    }));
    const bar = screen.getByTestId('ows-attention');
    expect(bar).toHaveTextContent('SCM-CN-1-1');
    expect(screen.getByRole('button', { name: /提交领星/ })).toBeDisabled();
    await userEvent.click(within(bar).getByRole('button', { name: '重新查' }));
    expect(screen.getByTestId('ds-flash')).toHaveTextContent('领星里还查不到');
    await userEvent.type(within(bar).getByLabelText('领星备货单号'), 'OWS9');
    await userEvent.click(within(bar).getByRole('button', { name: '登记' }));
    await userEvent.type(within(bar).getByLabelText('确认领星无此单的理由'), '搜过');
    await userEvent.click(within(bar).getByRole('button', { name: '确认领星无此单' }));
    const exits = ['/v1/sheets/1/ext-refs/verify', '/v1/sheets/1/ext-refs/register', '/v1/sheets/1/ext-refs/confirm-absent'];
    const posts = calls.filter((c) => c.method === 'POST' && exits.includes(c.path));
    expect(posts.map((c) => [c.path.split('/').pop(), c.body])).toEqual([
      ['verify', { attention_id: 1 }], ['register', { attention_id: 1, ext_no: 'OWS9' }],
      ['confirm-absent', { attention_id: 1, reason: '搜过' }]]);
  });
});

describe('提交领星 · 取消', () => {
  it('取消框写明会删几张领星备货单', async () => {
    const calls = await mount(confirmedRoutes(DISP, FRESH, { 'POST /v1/sheets/1/cancel': () => ok(cancelOws) }));
    await userEvent.click(screen.getByRole('button', { name: '取消排货表' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('会同时删除领星备货单 2 张');
    expect(dlg).toHaveTextContent('OWS261012001');
    await userEvent.type(within(dlg).getByLabelText('取消理由'), '换方案');
    await userEvent.click(within(dlg).getByRole('button', { name: '确认取消' }));
    expect(calls.filter((c) => c.path === '/v1/sheets/1/cancel').map((c) => c.body)).toEqual([{ reason: '换方案' }]);
  });
});

describe('终审修复', () => {
  it('I5：取消进行中按钮不可再点；超时写「结果未知」而不是失败', async () => {
    const calls = await mount(confirmedRoutes(DISP, FRESH, { 'POST /v1/sheets/1/cancel': async () => {
      await new Promise((r) => setTimeout(r, 20));
      throw new DOMException('The operation was aborted due to timeout', 'TimeoutError');
    } }));
    await userEvent.click(screen.getByRole('button', { name: '取消排货表' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('删到一半失败的，已删的照样留痕');
    await userEvent.type(within(dlg).getByLabelText('取消理由'), '换方案');
    await userEvent.dblClick(within(dlg).getByRole('button', { name: '确认取消' }));
    expect(calls.filter((c) => c.path === '/v1/sheets/1/cancel')).toHaveLength(1);
    expect(await screen.findByTestId('ds-flash')).toHaveTextContent('结果未知');
  });

  it('C1：取消做了一半 → 阻断项写明先把取消做完', async () => {
    await mount(confirmedRoutes(CONFIRMED, { ...FRESH, blockers: [{ code: 'cancel_incomplete', ext_nos: ['OWS1'] }] }));
    expect(screen.getByTestId('ows-blockers')).toHaveTextContent('取消只做了一半');
  });
});

describe('不让人误以为发到了领星', () => {
  it('海外仓表：加一格的目的地只列海外仓、不预选；没选就点「排」只提示，不发请求', async () => {
    const calls = await mount(routes(OS));
    const add = screen.getAllByTestId(/^add-/)[0]!;
    const dest = within(add).getByRole('combobox', { name: /目的地/ });
    expect(dest).toHaveValue('');
    expect([...dest.querySelectorAll('option')].map((o) => o.value).filter(Boolean).every((v) => v.startsWith('oversea/'))).toBe(true);
    await userEvent.type(within(add).getByRole('textbox', { name: /件数/ }), '3');
    await userEvent.click(within(add).getByRole('button', { name: '排' }));
    expect(within(add).getByRole('alert')).toHaveTextContent('先选目的地');
    expect(puts(calls)).toEqual([]);
  });

  it('平台仓 · FBA 表缺 fnsku：提交按钮不可点，点名哪个 msku', async () => {
    const fbaDone: SheetDetail = { ...D, state: '已确认', consignments: D.consignments.map((c) => ({ ...c, our_stage: '已确认' })) };
    await mount(confirmedRoutes(fbaDone, { ...(fbaSub as unknown as OwsSubmission),
      blockers: [{ code: 'fnsku_missing', seller_sku: 'MSKU-A', sid: '11072' }] }));
    expect(screen.getByRole('button', { name: '提交领星（1 张）' })).toBeDisabled();
    expect(screen.getByTestId('ows-blockers')).toHaveTextContent('MSKU-A 在领星 listing 里没有 FNSKU');
  });
});

describe('★ CA-D13 按去向分表', () => {
  it('页头写明去向', async () => {
    await mount(routes());
    expect(screen.getByTestId('sheet-route')).toHaveTextContent('平台仓 · 亚马逊 FBA');
  });

  it('平台仓 · FBA 表：加一格目的地只有本店 FBA，已带上', async () => {
    await mount(routes());
    const dest = within(screen.getByTestId('add-MSKU-B')).getByRole('combobox', { name: /目的地/ });
    expect(dest).toHaveValue('fba/11072');
    expect([...dest.querySelectorAll('option')].map((o) => o.value)).toEqual(['fba/11072']);
  });

  it('海外仓表：排货单没有装箱栏', async () => {
    await mount(routes(OS));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    expect(within(screen.getByTestId('consignment-2')).queryByLabelText(/箱数/)).toBeNull();
  });

  it('旧版混合表：写明只能取消，不能加格子', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, route: null }),
      'GET /v1/sheets/1/grid': () => ok({ ...G, sheet: { ...G.sheet, route: null } }) }));
    expect(screen.getByTestId('sheet-route')).toHaveTextContent('旧版混合表');
    expect(screen.queryAllByTestId(/^add-/)).toEqual([]);
    expect(screen.getByRole('button', { name: '取消排货表' })).toBeInTheDocument();
  });
});

describe('G7 物流方式：找得到、分得清', () => {
  const noLg = { ...detailOs, sheet_id: 1, logistics: [] };
  const g7bad = { ...preflightOs, sheet_id: 1, ok: false,
    gates: preflightOs.gates.map((g) => (g.gate === 'G7' ? { ...g, ok: false, code: 'logistics_missing', detail: [{ dest_wid: 2 }] } : g)) };

  it('确认检查里 G7 没过：带「去选物流方式」，点了跳到排货单那一页', async () => {
    await mount(routes({ ...OS, 'GET /v1/sheets/1': () => ok(noLg), 'GET /v1/sheets/1/preflight': () => ok(g7bad),
      'GET /v1/logistics-channels': () => ok(channels) }));
    await userEvent.click(screen.getByRole('tab', { name: /确认检查/ }));
    await userEvent.click(within(screen.getByTestId('gate-G7')).getByRole('button', { name: '去选物流方式' }));
    expect(within(screen.getByTestId('consignment-2')).getByRole('combobox', { name: /物流方式/ })).toBeInTheDocument();
  });

  it('没选物流方式的海外仓单醒目点名；同名渠道带上物流商区分', async () => {
    const dup = { ...channels, channels: [channels.channels[0]!, { ...channels.channels[0]!, logistics_id: 999, provider_name: '另一家货代' }] };
    await mount(routes({ ...OS, 'GET /v1/sheets/1': () => ok(noLg), 'GET /v1/logistics-channels': () => ok(dup) }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const con = screen.getByTestId('consignment-2');
    expect(within(con).getByTestId('logistics-missing')).toHaveTextContent('还没选物流方式');
    const opts = within(within(con).getByRole('combobox', { name: /物流方式/ })).getAllByRole('option').map((o) => o.textContent);
    expect(opts).toContain('美森快船 · 海卡 · 厦门快驿通');
    expect(opts).toContain('美森快船 · 海卡 · 另一家货代');
  });
});

describe('三方海外仓 sku 编码', () => {
  it('阻断项点名：哪个货号在哪个仓没配对 / 配对读不到', async () => {
    await mount(confirmedRoutes(CONFIRMED, { ...FRESH, blockers: [
      { code: 'third_party_sku_missing', dest_wid: 2, sku: 'DCC1041294BW', product_id: 741792 },
      { code: 'sku_match_unavailable', dest_wid: 2 }] }));
    const b = screen.getByTestId('ows-blockers');
    expect(b).toHaveTextContent('货号 DCC1041294BW 在 30-LA自有海外仓 还没配对三方 sku');
    expect(b).toHaveTextContent('30-LA自有海外仓 的三方 sku 配对读不到');
  });
});

describe('★ CA-D14 退回修改 · 改目的地', () => {
  it('已确认、还没发到领星：有「退回修改」，要理由，发 /reopen', async () => {
    const calls = await mount(confirmedRoutes(CONFIRMED, FRESH, { 'POST /v1/sheets/1/reopen': () => ok({ sheet_id: 1, state: '草稿', from: '已确认' }) }));
    await userEvent.click(screen.getByRole('button', { name: '退回修改' }));
    const dlg = screen.getByRole('dialog');
    expect(within(dlg).getByRole('button', { name: '确认退回' })).toBeDisabled();
    await userEvent.type(within(dlg).getByLabelText('退回理由'), '选错仓库');
    await userEvent.click(within(dlg).getByRole('button', { name: '确认退回' }));
    expect(calls.filter((c) => c.path === '/v1/sheets/1/reopen').map((c) => c.body)).toEqual([{ reason: '选错仓库' }]);
  });

  it('已有单发到过领星：不给「退回修改」（只能取消）', async () => {
    await mount(confirmedRoutes(DISP, FRESH));
    expect(screen.queryByRole('button', { name: '退回修改' })).toBeNull();
    expect(screen.getByRole('button', { name: '取消排货表' })).toBeInTheDocument();
  });

  it('海外仓草稿表：已有格子可以改目的地 —— 原子挪格，件数不变', async () => {
    const two = { ...gridOs, sheet: { ...gridOs.sheet, sheet_id: 1 },
      warehouses: { ...gridOs.warehouses, oversea: [...gridOs.warehouses.oversea, { wid: 7, name: '另一个海外仓', market: 'US', kind: 'oversea_self' }] } };
    const calls = await mount(routes({ ...OS, 'GET /v1/sheets/1/grid': () => ok(two) }));
    await userEvent.click(screen.getByText('全部'));
    await userEvent.click(screen.getByRole('tab', { name: /无计划来源/ }));
    const cell = screen.getAllByTestId(/^cell-/)[0]!;
    await userEvent.selectOptions(within(cell).getByRole('combobox', { name: /改目的地/ }), 'oversea/7');
    const [body] = puts(calls) as Record<string, unknown>[];
    expect(body).toMatchObject({ channel: 'oversea', dest_wid: 7, units: 3,
      replaces: { channel: 'oversea', dest_wid: 2, dest_sid: null, from_wid: 1 } });
  });
});

describe('领星回执', () => {
  it('每张领星单显示领星进度与读回时刻；两轴不一致醒目点名', async () => {
    const d: SheetDetail = { ...DISP, ext_refs: DISP.ext_refs.map((e, i) => (i === 0 ? e
      : { ...e, erp_stage: '领星无此单', drift: '领星里没有这张单' })) };
    await mount(confirmedRoutes(d, FRESH));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    expect(screen.getByTestId('ext-OWS261012001')).toHaveTextContent('领星 待收货');
    expect(screen.getByTestId('ext-OWS261012002')).toHaveTextContent('领星里没有这张单');
  });

  const receipts = (calls: { method: string; path: string }[]) =>
    calls.filter((c) => c.method === 'POST' && c.path === '/v1/sheets/1/receipt');

  it('打开已有领星单的表即现查一次（同采购单）；每张单两轴并排：本地 | 领星 + 读回时刻', async () => {
    const calls = await mount(confirmedRoutes(DISP, FRESH));
    expect(receipts(calls)).toHaveLength(1);
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const row = screen.getByTestId('ext-OWS261012001');
    expect(within(row).getByTestId('axis-local')).toHaveTextContent('本地 已投送');
    expect(within(row).getByTestId('axis-lx')).toHaveTextContent(/领星 待收货 读回于 /);
  });

  it('每张领星单旁有「重新读回」小按钮：点了再现查一次', async () => {
    const calls = await mount(confirmedRoutes(DISP, FRESH));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    await userEvent.click(within(screen.getByTestId('ext-OWS261012002')).getByRole('button', { name: '重新读回' }));
    expect(receipts(calls)).toHaveLength(2);
  });

  it('这一张没读到：只在这一张上点名，写明显示的是上次读回', async () => {
    const r = { ...receiptSample, results: [receiptSample.results[0],
      { ext_no: 'OWS261012002', outcome: 'unknown', lx_msg: null, cause: 'ConnectTimeout: connect timeout' }] };
    await mount(confirmedRoutes(DISP, FRESH, { 'POST /v1/sheets/1/receipt': () => ok(r) }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    expect(screen.getByTestId('ext-OWS261012002')).toHaveTextContent('这次读回失败（unknown），显示的是上次读回的状态');
    expect(screen.getByTestId('ext-OWS261012002')).toHaveTextContent('ConnectTimeout');
    expect(screen.getByTestId('ext-OWS261012001')).not.toHaveTextContent('这次读回失败');
  });

  it('读回请求本身失败（如 409 正被提交占用）：每张单点名，不当成领星状态', async () => {
    await mount(confirmedRoutes(DISP, FRESH, { 'POST /v1/sheets/1/receipt': () => fail(409, 'sheet_busy', '这张表正在提交 / 取消 / 读回') }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    expect(screen.getByTestId('ext-OWS261012001')).toHaveTextContent('读回请求失败：这张表正在提交 / 取消 / 读回');
    expect(screen.getByTestId('ext-OWS261012001')).toHaveTextContent('领星 待收货');
  });

  it('底部排货单芯片带领星进度：不用切页签就看得到', async () => {
    await mount(confirmedRoutes(DISP, FRESH));
    expect(screen.getByTestId('dock')).toHaveTextContent('领星 待收货');
  });

  it('还没有单发到领星：不现查、不给「重新读回」', async () => {
    const calls = await mount(confirmedRoutes(CONFIRMED, FRESH));
    expect(receipts(calls)).toHaveLength(0);
    expect(screen.queryByRole('button', { name: '重新读回' })).toBeNull();
  });
});

describe('★ C-c1 FBA 发货计划提交', () => {
  const FD = fbaDetail as unknown as SheetDetail;
  const FBA_CONFIRMED: SheetDetail = { ...FD, ext_refs: [], consignments: FD.consignments.map((c) => ({ ...c, our_stage: '已确认' })) };

  it('已确认的平台仓 · FBA 表：有「提交领星」，确认框写明建 FBA 发货计划并锁库存', async () => {
    const calls = await mount(confirmedRoutes(FBA_CONFIRMED, fbaSub as unknown as OwsSubmission,
      { 'POST /v1/sheets/1/submit': () => ok(fbaRelocked) }));
    expect(screen.queryByTestId('fba-not-submitted')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '提交领星（1 张）' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('FBA 发货计划');
    expect(dlg).toHaveTextContent('锁定本地仓库存');
    await userEvent.click(within(dlg).getByRole('button', { name: '确认提交' }));
    expect(calls.filter((c) => c.path === '/v1/sheets/1/submit')).toHaveLength(1);
  });

  it('建了没锁上：按钮变「补锁库存（1 张）」', async () => {
    await mount(confirmedRoutes(FBA_CONFIRMED, fbaToLock as unknown as OwsSubmission));
    expect(screen.getByRole('button', { name: '补锁库存（1 张）' })).toBeEnabled();
  });

  it('★ P0 只需补锁：弹窗里「确认提交」点得下去，发出 submit；标题与正文写补锁、列出要补锁的那张', async () => {
    const calls = await mount(confirmedRoutes(FBA_CONFIRMED, fbaToLock as unknown as OwsSubmission,
      { 'POST /v1/sheets/1/submit': () => ok(fbaRelocked) }));
    await userEvent.click(screen.getByRole('button', { name: '补锁库存（1 张）' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('补锁库存：1 张 FBA 发货计划');
    expect(dlg).not.toHaveTextContent('海外仓备货单');
    expect(within(dlg).getByTestId('ows-orders')).toHaveTextContent('SCM-FBA-1-1');
    const go = within(dlg).getByRole('button', { name: '确认补锁' });
    expect(go).toBeEnabled();
    await userEvent.click(go);
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/v1/sheets/1/submit')).toHaveLength(1);
    expect(await within(dlg).findByTestId('ows-results')).toHaveTextContent('已补锁库存');
  });

  it('FBA 提交弹窗标题写 FBA 发货计划，不写海外仓备货单', async () => {
    await mount(confirmedRoutes(FBA_CONFIRMED, fbaSub as unknown as OwsSubmission));
    await userEvent.click(screen.getByRole('button', { name: '提交领星（1 张）' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('提交领星：建 1 张 FBA 发货计划');
  });

  it('已投送：排货单写出批次号、计划号、已锁库存；底部提示去领星做 STA', async () => {
    await mount(confirmedRoutes(FD, { ...(fbaSub as unknown as OwsSubmission), orders: [] }));
    await userEvent.click(screen.getByRole('tab', { name: /排货单与装箱/ }));
    const e = screen.getByTestId(`ext-${FD.ext_refs[0]!.ext_no}`);
    expect(e).toHaveTextContent('领星发货计划');
    expect(e).toHaveTextContent(FD.ext_refs[0]!.order_sns![0]!);
    expect(e).toHaveTextContent('已锁库存');
    expect(screen.getByTestId('fba-next')).toHaveTextContent('在领星完成 STA');
  });

  it('取消：框里写明会释放库存、计划要在领星手工作废；结果点名计划号', async () => {
    await mount(confirmedRoutes(FD, { ...(fbaSub as unknown as OwsSubmission), orders: [] },
      { 'POST /v1/sheets/1/cancel': () => ok(fbaCancel) }));
    await userEvent.click(screen.getByRole('button', { name: '取消排货表' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('释放领星发货计划的库存');
    expect(dlg).toHaveTextContent('需在领星手工作废');
    await userEvent.type(within(dlg).getByLabelText('取消理由'), '换方案');
    await userEvent.click(within(dlg).getByRole('button', { name: '确认取消' }));
    expect(await screen.findByTestId('manual-void')).toHaveTextContent(fbaCancel.manual_void[0]!.plans[0]!);
  });
});

describe('两条链提交按钮一致', () => {
  it('提交预览还在核对时：按钮已在，写「核对中」，不可点（海外仓要现查三方配对，实测 4.5 s）', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    await mount(confirmedRoutes(CONFIRMED, FRESH, { 'GET /v1/sheets/1/submission': async () => { await gate; return ok(FRESH); } }));
    const b = screen.getByRole('button', { name: /提交领星（核对中…）/ });
    expect(b).toBeDisabled();
    release();
    expect(await screen.findByRole('button', { name: '提交领星（2 张）' })).toBeEnabled();
  });

  it('fnsku 提示说清原因与去哪改；listing 读不到单独点名', async () => {
    const fbaDone: SheetDetail = { ...D, state: '已确认', consignments: D.consignments.map((c) => ({ ...c, our_stage: '已确认' })) };
    await mount(confirmedRoutes(fbaDone, { ...(fbaSub as unknown as OwsSubmission), blockers: [
      { code: 'fnsku_missing', seller_sku: 'DCC1041294BWZ2B', sid: '11072' }, { code: 'fnsku_unavailable' }] }));
    const b = screen.getByTestId('ows-blockers');
    expect(b).toHaveTextContent('亚马逊配送');
    expect(b).toHaveTextContent('领星 listing 读不到');
  });
});

describe('选第三方海外仓：当场提示要配对，并现查配对状态', () => {
  const G3 = { ...gridOs, sheet: { ...gridOs.sheet, sheet_id: 1 }, warehouses: { ...gridOs.warehouses,
    oversea: [...gridOs.warehouses.oversea, { wid: 24741, name: '魔方云仓BCI NL-TX', market: 'US', kind: 'oversea_3pl' }] } };

  it('下拉里第三方仓带标注；选中后提示须配对，并显示现查结果「未配对」', async () => {
    const calls = await mount(routes({ ...OS, 'GET /v1/sheets/1/grid': () => ok(G3),
      'GET /v1/oversea-sku-match': () => ok({ wid: 24741, sku: 'A4P-TOY-003', product_id: 1, third_party: true,
        needs_match: true, matched: false, codes: [] }) }));
    await userEvent.click(screen.getByText('全部'));
    await userEvent.click(screen.getByRole('tab', { name: /无计划来源/ }));
    const add = screen.getAllByTestId(/^add-/)[0]!;
    const dest = within(add).getByRole('combobox', { name: /目的地/ });
    expect(within(dest).getByRole('option', { name: /魔方云仓BCI NL-TX（第三方仓 · 需配对）/ })).toBeInTheDocument();
    await userEvent.selectOptions(dest, 'oversea/24741');
    const hint = await within(add).findByTestId('tp-hint');
    expect(hint).toHaveTextContent('第三方海外仓：货号须在领星配对三方 sku 后才能提交备货单');
    expect(await within(add).findByText(/✗ 未配对/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/v1/oversea-sku-match')).toHaveLength(1);
  });

  it('选自有海外仓：不提示、不查', async () => {
    const calls = await mount(routes({ ...OS, 'GET /v1/sheets/1/grid': () => ok(G3) }));
    await userEvent.click(screen.getByText('全部'));
    await userEvent.click(screen.getByRole('tab', { name: /无计划来源/ }));
    const add = screen.getAllByTestId(/^add-/)[0]!;
    await userEvent.selectOptions(within(add).getByRole('combobox', { name: /目的地/ }), 'oversea/2');
    expect(within(add).queryByTestId('tp-hint')).toBeNull();
    expect(calls.filter((c) => c.path === '/v1/oversea-sku-match')).toEqual([]);
  });
});

describe('★ UX-D7 新建时的默认分配', () => {
  const AF = (autofillDone as unknown as SheetDetail).autofill!;
  /** 网格里那一格（arrangement_id 1）就是系统填的那格 */
  const withAf = (units: number): SheetDetail => ({ ...D, autofill: { ...AF,
    filled: [{ arrangement_id: 1, line_id: 1, seller_sku: 'MSKU-A', sid: '11072', from_wid: 1, units }] } });

  it('系统填的格子标「默认」；页头写填了几格几件、跳过几条，规则与跳过清单在折叠区、默认收起', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok(withAf(30)) }));
    expect(screen.getByTestId('cell-1')).toHaveTextContent('默认');
    expect(screen.getByTestId('cell-1')).not.toHaveTextContent('已改');
    const note = screen.getByTestId('autofill');
    expect(note).toHaveTextContent('新建时系统填了 1 格 30 件默认排货量，跳过 1 条');
    const fold = within(note).getByTestId('autofill-fold');
    expect(fold).not.toHaveAttribute('open');
    expect(fold).toHaveTextContent('库存不够，没排满');
    expect(fold).toHaveTextContent('按各店期望比例');
  });

  it('人改过件数：写「默认 N，已改」', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok(withAf(75)) }));
    expect(screen.getByTestId('cell-1')).toHaveTextContent('默认 75，已改');
  });

  it('★ 没做成：不折叠，点名原因与错误码 —— 不当成「系统没填」', async () => {
    const F = (autofillFailed as unknown as SheetDetail).autofill!;
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, autofill: F }) }));
    const note = screen.getByTestId('autofill');
    expect(note).toHaveTextContent('默认分配没做成：本地库存读不到，没法按可安排封顶');
    expect(note).toHaveTextContent('stock_unavailable');
    expect(screen.getByTestId('cell-1')).not.toHaveTextContent('默认');
  });

  it('没有默认分配记录（海外仓表 / 迁移前的表）：什么都不写', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, autofill: null }) }));
    expect(screen.queryByTestId('autofill')).toBeNull();
  });
});

describe('排货工作台 · 在途写调用（DL-D8）', () => {
  it('有在途写调用：底部写「正在提交…」', async () => {
    await mount(routes({ 'GET /v1/sheets/1': () => ok({ ...D, in_flight: 1 }) }));
    expect(within(screen.getByTestId('dock')).getByTestId('in-flight')).toHaveTextContent('正在提交…');
  });

  it('没有在途写调用：不写', async () => {
    await mount(routes());
    expect(screen.queryByTestId('in-flight')).toBeNull();
    expect(screen.queryByTestId('in-flight-missing')).toBeNull();
  });

  it('★ 旧后端没有 in_flight：点名并留痕，不当成「没在提交」，其余照常显示', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { in_flight: _drop, ...old } = D;
    await mount(routes({ 'GET /v1/sheets/1': () => ok(old) }));
    expect(within(screen.getByTestId('dock')).getByTestId('in-flight-missing'))
      .toHaveTextContent('后端缺字段 in_flight');
    expect(screen.queryByTestId('in-flight')).toBeNull();
    expect(screen.getByLabelText(CELL_A)).toBeInTheDocument();
    expect(err).toHaveBeenCalledWith('[sheet] detail shape mismatch',
      JSON.stringify({ call: 'GET /v1/sheets/1', missing: ['in_flight'] }));
    err.mockRestore();
  });
});
