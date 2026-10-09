import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { createHttpApi } from '../../api/http';
import { handlerThrew, requestThrew } from './fmt';
import { isUnknownOutcome } from './poStatus';

describe('结果未知的判定', () => {
  it('★ 请求发出后才抛的非 ApiError（读响应体时断开）算「不知道」，不能再放开确认下单', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(isUnknownOutcome(requestThrew('po-place', 'place', new TypeError('terminated')))).toBe(true);
  });
  it('界面自己在发请求之前抛的仍是确定的「没做」', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(isUnknownOutcome(handlerThrew('po-place', 'place', new TypeError('x')))).toBe(false);
  });
  it('★ 501 不是后端的明确答复（api/ui 没有路由回 501）→ 与其他 5xx 一样算「不知道」', () => {
    expect(isUnknownOutcome(new ApiError(501, 'not_implemented', '代理不支持'))).toBe(true);
  });
  it('后端的 4xx 明确拒绝与 502 lookup_failed 是确定的「没做」', () => {
    expect(isUnknownOutcome(new ApiError(409, 'nothing_to_resolve', '这张单没有待处理的项'))).toBe(false);
    expect(isUnknownOutcome(new ApiError(502, 'lookup_failed', '查询领星本身失败'))).toBe(false);
  });
});

describe('http：响应体读到一半失败', () => {
  const half = (e: unknown) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    { status: 200, ok: true, text: () => Promise.reject(e) } as unknown as Response);

  it('★ 读体时超时 → timeout（status 0，结果未知），日志点名阶段', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    half(new DOMException('The operation was aborted due to timeout', 'TimeoutError'));
    const err = await createHttpApi({ base: '/v1', timeoutMs: 5 }).placePurchaseOrder(1, 't').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).error).toBe('timeout');
    expect(isUnknownOutcome(err as ApiError)).toBe(true);
    expect(logged[0]!.join(' ')).toContain('"phase":"body"');
  });

  it('★ 读体时断开 → network 带 cause code（status 0，结果未知）', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const boom = new TypeError('terminated');
    (boom as { cause?: unknown }).cause = { code: 'UND_ERR_SOCKET' };
    half(boom);
    const err = await createHttpApi({ base: '/v1', timeoutMs: 5 }).placePurchaseOrder(1, 't').catch((e) => e);
    expect((err as ApiError).error).toBe('network');
    expect((err as ApiError).fields['cause_code']).toBe('UND_ERR_SOCKET');
    expect(isUnknownOutcome(err as ApiError)).toBe(true);
  });
});
