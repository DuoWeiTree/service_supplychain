import { afterEach, describe, expect, it, vi } from 'vitest';
import { READ_BACK_TIMEOUT_MS, createHttpApi } from './http';
import { ApiError } from './client';
import { setActor } from '../shell/actorStore';
import gridFixture from '../testing/fixtures/grid-1.json';
import type { DemandCell } from './types';

afterEach(() => { vi.restoreAllMocks(); });

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

const mk = () => createHttpApi({ base: '/v1', timeoutMs: 1000 });

describe('http 数据源', () => {
  it('发 x-actor 头，取自顶栏下拉', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok(gridFixture));
    setActor('ops.li');
    await mk().getGrid(1);
    expect(new Headers((spy.mock.calls[0]![1] as RequestInit).headers).get('x-actor')).toBe('ops.li');
  });

  it('★ 只发声明过的查询参数 —— undefined 的不拼进 URL（未声明参数后端一律 400）', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ plans: [], excluded: { archived: 0 } }));
    await mk().listPlans({ archived: false });
    expect(String(spy.mock.calls[0]![0])).toBe('/v1/plans?archived=false');
  });

  it('★ 列表响应带一层包裹，数据层拆开 —— 页面不该知道这层', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({
      sellers: [{ seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' }],
    }));
    expect(await mk().listSellers()).toEqual([
      { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
    ]);
  });

  it('★ PUT 的 body 字段名按契约：计划销量 expected_units、采购量 planned_units', async () => {
    // ★ mockResolvedValue 复用同一个 Response 实例，两次调用共享同一份 body ——
    //   第二次读就会炸 "Body has already been read"（真实 fetch 每次都发一个新对象，
    //   这里必须显式用 mockImplementation 每次构一份新的）。
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(ok({ cell: {} })));
    const api = mk();
    await api.putDemand(1, 'MSKU-A', '11072', '2026-10', null);
    await api.putPurchase(1, 'SKU-1', '2026-10', 500);
    expect(JSON.parse((spy.mock.calls[0]![1] as RequestInit).body as string)).toEqual({ expected_units: null });
    expect(JSON.parse((spy.mock.calls[1]![1] as RequestInit).body as string)).toEqual({ planned_units: 500 });
  });

  it('★ putDemand 拿到的是完整十键，不是接口签名替它"声称"出来的 —— stub 用真实 fixture 的一整行', async () => {
    // ★ team-lead 09-22 裁定（找到 4）：http.ts 曾经用 Awaited<ReturnType<...>> 从
    //   SupplyChainApi['putDemand'] 的签名反推类型，签名说 9 键，真实后端一度只发 7 键
    //   （漏 sku/effective_units），TS 却因为这个反推走过场而不报错。改用显式的
    //   PutDemandResult 信封后，这条测试拿 grid-1.json 里一整行真实数据当 stub，
    //   证明 http.ts 原样透传而不是自己拼了一份缺字段的假格子。
    // ★ F16：claim() 现在种 system_basis（第十键），fixture 重固化后第一次真实带出这个字段。
    const realCell = (gridFixture as { demand: DemandCell[] }).demand[0]!;
    expect(Object.keys(realCell).sort()).toEqual(
      ['basis', 'effective_units', 'expected_units', 'period', 'seller_sku',
       'sid', 'sku', 'system_basis', 'system_extrapolated', 'system_units'].sort(),
    );
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ cell: realCell }));
    const got = await mk().putDemand(1, realCell.seller_sku, realCell.sid, realCell.period, null);
    expect(got).toEqual(realCell);
    expect(got.sku).toBe(realCell.sku);
    expect(got.effective_units).toBe(realCell.effective_units);
  });

  // ★ M15（复审 R4）：路径的**每一段**都要转义。putDemand 补上了 sid，releaseClaim 当时漏了 ——
  //   「下一个人照抄旁边那段」正是这种漏活下去的方式。当前 sid 都是数字串，所以这里
  //   拿一个真会被转义的值当靶子，否则两种写法给同一个 URL，这条断言什么都证不了。
  it('★ 路径每一段都过 encodeURIComponent —— 不是只转义第一段', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(ok({ cell: {} })));
    const api = mk();
    await api.putDemand(1, 'MSKU/A', '110 72', '2026-10', null);
    await api.releaseClaim(1, 'MSKU/A', '110 72');
    await api.removeSku(1, 'SKU/1 X');
    expect(String(spy.mock.calls[0]![0])).toBe('/v1/plans/1/demand/MSKU%2FA/110%2072/2026-10');
    expect(String(spy.mock.calls[1]![0])).toBe('/v1/plans/1/claims/MSKU%2FA/110%2072');
    expect(String(spy.mock.calls[2]![0])).toBe('/v1/plans/1/skus/SKU%2F1%20X');
    expect((spy.mock.calls[2]![1] as RequestInit).method).toBe('DELETE');
  });

  it('409 抛 ApiError，点名字段从顶层收进 fields', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ error: 'rev_in_flight', hint: '先处理 rev 2', in_flight_rev: 2 }),
      { status: 409, headers: { 'content-type': 'application/json' } }));
    const err = await mk().submit(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('rev_in_flight');
    expect((err as ApiError).hint).toBe('先处理 rev 2');
    expect((err as ApiError).fields).toEqual({ in_flight_rev: 2 });
  });

  it('★ 日志三问：打的谁 · 多久 · 怎么失败的（带 cause.code）', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    const boom = new TypeError('fetch failed');
    (boom as { cause?: unknown }).cause = { code: 'UND_ERR_CONNECT_TIMEOUT' };
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(boom);

    await mk().getGrid(1).catch(() => undefined);

    // ★ logged[0] 是 console.error 的两个实参 ['[api]', jsonStr]；jsonStr 本身已经是
    //   JSON.stringify 过的一份。再套一层 JSON.stringify(logged[0]) 会把内层的引号
    //   转义成 \" ——"ms": 就变成 \"ms\":，正则再也匹配不上。join(' ') 才是那份原始日志行。
    const line = logged[0]!.join(' ');
    expect(line).toContain('GET /v1/plans/1/grid');   // 打的谁
    expect(line).toMatch(/"ms":\d+/);                  // 多久
    expect(line).toContain('UND_ERR_CONNECT_TIMEOUT'); // ★ 不是「fetch failed」五个字
  });

  it('★ 超时与连不上分得开：超时记 TimeoutError，不是 cause code', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new DOMException('The operation was aborted due to timeout', 'TimeoutError'));
    const err = await createHttpApi({ base: '/v1', timeoutMs: 5 }).getGrid(1).catch((e) => e);
    expect((err as ApiError).error).toBe('timeout');
    expect(logged[0]!.join(' ')).toContain('TimeoutError');
  });

  it('★ 读回用自己的长超时（要等后端打完领星），其余请求仍用全局超时', async () => {
    const spy = vi.spyOn(AbortSignal, 'timeout');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ checked: true, read_outcome: 'ok', read_cause: null,
      read_back_call: 1, problems: [], lx: null }));
    await mk().readBack(3);
    await mk().listSellers().catch(() => undefined);
    expect(spy.mock.calls.map((c) => c[0])).toEqual([READ_BACK_TIMEOUT_MS, 1000]);
    expect(READ_BACK_TIMEOUT_MS).toBeGreaterThan(60_000);   // > 后端两次各 30 s 的外呼
  });

  it('★ 计划 grid 也现查领星（海外仓在途，缓存未命中实测冷启 13.6 s）：用读回的长超时，不用全局 8 s', async () => {
    const spy = vi.spyOn(AbortSignal, 'timeout');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({}));
    await mk().getGrid(1).catch(() => undefined);
    expect(spy.mock.calls.map((c) => c[0])).toEqual([READ_BACK_TIMEOUT_MS]);
  });

  it('★ 提交预览也现查领星（海外仓三方配对实测 7.5~16 s）：用读回的长超时，不用全局 8 s', async () => {
    const spy = vi.spyOn(AbortSignal, 'timeout');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ orders: [], blockers: [] }));
    await mk().getSubmission(6);
    expect(spy.mock.calls.map((c) => c[0])).toEqual([READ_BACK_TIMEOUT_MS]);
  });

  it('★ 读回超时的提示写的是读回自己的时限，不是全局的', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new DOMException('The operation was aborted due to timeout', 'TimeoutError'));
    const err = await mk().readBack(3).catch((e) => e);
    expect((err as ApiError).hint).toBe(`${READ_BACK_TIMEOUT_MS}ms 内没有响应`);
  });

  it('★ 提前期：putLeadTime 发 PUT /v1/lead-time，body 带 make_days，getLeadTime 发 GET', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(ok({
        route: 'oversea_sea', make_days: 10, ship_days: 60, anchor_day: 15,
        valid_from: { making: '2026-09-24', shipping: '2026-01-01' }, changed: [],
      })));
    const api = mk();
    await api.putLeadTime({ make_days: 10 });
    expect(String(spy.mock.calls[0]![0])).toBe('/v1/lead-time');
    expect((spy.mock.calls[0]![1] as RequestInit).method).toBe('PUT');
    expect(JSON.parse((spy.mock.calls[0]![1] as RequestInit).body as string)).toEqual({ make_days: 10 });
    expect(new Headers((spy.mock.calls[0]![1] as RequestInit).headers).get('x-actor')).not.toBeNull();

    await api.getLeadTime();
    expect(String(spy.mock.calls[1]![0])).toBe('/v1/lead-time');
    expect((spy.mock.calls[1]![1] as RequestInit).method).toBe('GET');
  });

  describe('采购接口：method / URL / body / x-actor', () => {
    const run = async (fn: (api: ReturnType<typeof mk>) => Promise<unknown>, resp: Response = ok({})) => {
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(resp.clone()));
      setActor('ops.li');
      await fn(mk());
      const [url, init] = spy.mock.calls[0]! as [string, RequestInit];
      return { url, method: init.method, actor: new Headers(init.headers).get('x-actor'),
               body: init.body === undefined ? undefined : JSON.parse(init.body as string) };
    };
    const body = { supplier_id: 'S-1001', purchaser_id: 111, opt_uid: 10925905, qc_type: 1 as const, lines: [] };

    it.each([
      ['listPurchaseOrders()', (a: ReturnType<typeof mk>) => a.listPurchaseOrders(), 'GET', '/v1/purchase-orders', undefined],
      ['listPurchaseOrders(草稿)', (a: ReturnType<typeof mk>) => a.listPurchaseOrders('草稿'), 'GET',
        `/v1/purchase-orders?state=${encodeURIComponent('草稿')}`, undefined],
      ['getPurchaseOrder', (a: ReturnType<typeof mk>) => a.getPurchaseOrder(7), 'GET', '/v1/purchase-orders/7', undefined],
      ['createPurchaseOrder', (a: ReturnType<typeof mk>) => a.createPurchaseOrder(body), 'POST', '/v1/purchase-orders', body],
      ['updatePurchaseOrder', (a: ReturnType<typeof mk>) => a.updatePurchaseOrder(7, { ...body, draft_version: 3 }), 'PUT',
        '/v1/purchase-orders/7', { ...body, draft_version: 3 }],
      ['getPreflight', (a: ReturnType<typeof mk>) => a.getPreflight(7), 'GET', '/v1/purchase-orders/7/preflight', undefined],
      ['placePurchaseOrder', (a: ReturnType<typeof mk>) => a.placePurchaseOrder(7, 'tok'), 'POST',
        '/v1/purchase-orders/7/place', { confirm_token: 'tok' }],
      ['getTimeline', (a: ReturnType<typeof mk>) => a.getTimeline(7), 'GET', '/v1/purchase-orders/7/timeline', undefined],
      ['recheck', (a: ReturnType<typeof mk>) => a.recheck(7), 'POST', '/v1/purchase-orders/7/recheck', undefined],
      ['registerExisting', (a: ReturnType<typeof mk>) => a.registerExisting(7, 'PO1'), 'POST',
        '/v1/purchase-orders/7/register-existing', { order_sn: 'PO1' }],
      ['confirmAbsent', (a: ReturnType<typeof mk>) => a.confirmAbsent(7, '已核实'), 'POST',
        '/v1/purchase-orders/7/confirm-absent', { reason: '已核实' }],
      ['acknowledgeMismatch', (a: ReturnType<typeof mk>) => a.acknowledgeMismatch(7, '知悉'), 'POST',
        '/v1/purchase-orders/7/acknowledge-mismatch', { reason: '知悉' }],
      ['listAttention', (a: ReturnType<typeof mk>) => a.listAttention(), 'GET', '/v1/attention', undefined],
      ['getPurchaseOptions', (a: ReturnType<typeof mk>) => a.getPurchaseOptions(), 'GET', '/v1/purchase-options', undefined],
      ['getPurchaseChecks', (a: ReturnType<typeof mk>) => a.getPurchaseChecks(), 'GET', '/v1/checks/purchase', undefined],
      ['listCandidateLines 全空仍带 orderable=1', (a: ReturnType<typeof mk>) => a.listCandidateLines({}), 'GET',
        '/v1/plan-lines?orderable=1', undefined],
      ['listCandidateLines plan_ids+sku', (a: ReturnType<typeof mk>) => a.listCandidateLines({ plan_ids: [1, 2], sku: 'A/B' }),
        'GET', '/v1/plan-lines?plan_ids=1%2C2&sku=A%2FB&orderable=1', undefined],
      ['getLineTrace', (a: ReturnType<typeof mk>) => a.getLineTrace(101), 'GET', '/v1/plan-lines/101/trace', undefined],
    ])('%s', async (_n, fn, method, url, b) => {
      const r = await run(fn);
      expect(r).toEqual({ url, method, actor: 'ops.li', body: b });
    });

    it('deletePurchaseOrder：DELETE，204 空体不炸', async () => {
      const r = await run((a) => a.deletePurchaseOrder(7), new Response(null, { status: 204 }));
      expect(r).toEqual({ url: '/v1/purchase-orders/7', method: 'DELETE', actor: 'ops.li', body: undefined });
    });

    it('★ 409 contradicts_evidence：点名字段平铺，解析进 ApiError.fields', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
        error: 'contradicts_evidence', hint: '系统查重在领星找到过本单幂等键的单', po_id: 7,
        found_order_sns: ['PO260930001'], lookup_call_id: 12, open_landed_mismatch: false,
        custom_order_sn: 'SCM-PO-7',
      }), { status: 409, headers: { 'content-type': 'application/json' } }));
      const e = await mk().confirmAbsent(7, 'x').catch((x: unknown) => x);
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect([err.status, err.error]).toEqual([409, 'contradicts_evidence']);
      expect(err.fields.found_order_sns).toEqual(['PO260930001']);
      expect(err.fields.po_id).toBe(7);
    });
  });
});

