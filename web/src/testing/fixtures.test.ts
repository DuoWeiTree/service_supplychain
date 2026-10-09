import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { repoRoot, webRoot } from './paths';

// ★ 路径解析交给 testing/paths.ts 的 repoRoot() / webRoot() 统一做（Task 1 修过的那处）——
//   不用 import.meta.url，也不用 process.cwd() 内联；vitest 必须从 web/ 下跑，
//   跑错目录会在 webRoot() 那一处点名报错，不是这里散落 ENOENT。
const BACKEND = () => resolve(repoRoot(), 'tests/fixtures/grid_response.json');
const MINE = () => resolve(webRoot(), 'src/testing/fixtures/grid-1.json');

describe('grid fixture 与后端同源', () => {
  it('★ 逐字节相同 —— 两份各自维护的话，前端跑 mock 全绿、切 api 才发现字段名不一样', () => {
    const h = (p: string) => createHash('sha256').update(readFileSync(p)).digest('hex');
    expect(h(MINE())).toBe(h(BACKEND()));
  });

  it('★ fixture 覆盖三种长得像的形态，缺一种前端就永远画不出它', () => {
    // ★ Task 21：旧库存块 inventory[] 已退役（spec §6），改从 forecast_store 取等价事实。
    const g = JSON.parse(readFileSync(MINE(), 'utf8'));
    const fs = g.forecast_store as { state: string; reason: string | null;
                                      closing: number | null; inbound_planned: number | null }[];
    expect(fs.some((r) => r.state === 'not_applicable' && r.reason === 'not_applicable')).toBe(true); // 不适用
    expect(fs.some((r) => r.state === 'unknown' && r.reason === 'unknown_demand')).toBe(true);        // 未知
    expect(fs.some((r) => typeof r.closing === 'number')).toBe(true);                                 // 有数
    // ★ 两种 null 成因必须靠 reason 分得开，不能只看 closing===null
    expect(new Set(fs.filter((r) => r.closing === null).map((r) => r.reason)).size).toBeGreaterThan(1);
    // ★ 已有在途恒无店铺归属：inbound_planned 已知时恒为 0，不会因货号已有在途而非零
    expect(fs.every((r) => r.inbound_planned === null || r.inbound_planned === 0)).toBe(true);
    expect(g.demand.some((d: { basis: string }) => d.basis === 'human')).toBe(true);
    expect(g.demand.some((d: { basis: string }) => d.basis === 'system')).toBe(true);
  });
});
