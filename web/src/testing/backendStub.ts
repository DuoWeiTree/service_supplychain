import { vi } from 'vitest';

/** http 打桩：按「METHOD /v1/path」答。★ 走真的 http.ts 拆包 —— 409 的点名字段平铺在顶层，
 *  只有经过 http.ts 才知道界面拿到的 fields 长什么样。没覆盖的调用答 599，页面会把它当错显示，测试看得见。 */
/** `raw`：原样回一段非 JSON（代理错误页之类） */
export interface Reply { status: number; json: unknown; raw?: string }
export type Handler = (body: unknown) => Reply | Promise<Reply>;
export interface StubCall { method: string; path: string; body: unknown }

export const ok = (json: unknown): Reply => ({ status: 200, json });
export const fail = (status: number, error: string, hint: string, fields: Record<string, unknown> = {}): Reply =>
  ({ status, json: { error, hint, ...fields } });

export function stubBackend(routes: Record<string, Handler>): StubCall[] {
  const calls: StubCall[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://stub');
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) as unknown : undefined;
    calls.push({ method, path: url.pathname, body });
    const h = routes[`${method} ${url.pathname}`];
    const r = h ? await h(body) : fail(599, 'stub_missing', `桩没有覆盖 ${method} ${url.pathname}`);
    return new Response(r.raw ?? (r.json === undefined ? '' : JSON.stringify(r.json)),
      { status: r.status, headers: { 'content-type': 'application/json' } });
  });
  return calls;
}
