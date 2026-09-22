import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHttpApi } from './http';
import { ApiError } from './client';
import { setActor } from '../shell/actorStore';
import gridFixture from './fixtures/grid-1.json';
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

  it('★ PUT 的 body 字段名按契约：期望销量 expected_units、采购量 planned_units', async () => {
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

  it('★ putDemand 拿到的是完整九键，不是接口签名替它"声称"出来的 —— stub 用真实 fixture 的一整行', async () => {
    // ★ team-lead 09-22 裁定（找到 4）：http.ts 曾经用 Awaited<ReturnType<...>> 从
    //   SupplyChainApi['putDemand'] 的签名反推类型，签名说 9 键，真实后端一度只发 7 键
    //   （漏 sku/effective_units），TS 却因为这个反推走过场而不报错。改用显式的
    //   PutDemandResult 信封后，这条测试拿 grid-1.json 里一整行真实 9 键数据当 stub，
    //   证明 http.ts 原样透传而不是自己拼了一份缺字段的假格子。
    const realCell = (gridFixture as { demand: DemandCell[] }).demand[0]!;
    expect(Object.keys(realCell).sort()).toEqual(
      ['basis', 'effective_units', 'expected_units', 'period', 'seller_sku',
       'sid', 'sku', 'system_extrapolated', 'system_units'].sort(),
    );
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ cell: realCell }));
    const got = await mk().putDemand(1, realCell.seller_sku, realCell.sid, realCell.period, null);
    expect(got).toEqual(realCell);
    expect(got.sku).toBe(realCell.sku);
    expect(got.effective_units).toBe(realCell.effective_units);
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
});
