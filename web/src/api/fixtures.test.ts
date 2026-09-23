import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { repoRoot, webRoot } from '../testing/paths';

// ★ 路径解析交给 testing/paths.ts 的 repoRoot() / webRoot() 统一做（Task 1 修过的那处）——
//   不用 import.meta.url，也不用 process.cwd() 内联；vitest 必须从 web/ 下跑，
//   跑错目录会在 webRoot() 那一处点名报错，不是这里散落 ENOENT。
const BACKEND = () => resolve(repoRoot(), 'tests/fixtures/grid_response.json');
const MINE = () => resolve(webRoot(), 'src/api/fixtures/grid-1.json');

describe('grid fixture 与后端同源', () => {
  it('★ 逐字节相同 —— 两份各自维护的话，前端跑 mock 全绿、切 api 才发现字段名不一样', () => {
    const h = (p: string) => createHash('sha256').update(readFileSync(p)).digest('hex');
    expect(h(MINE())).toBe(h(BACKEND()));
  });

  it('★ fixture 覆盖三种长得像的形态，缺一种前端就永远画不出它', () => {
    const g = JSON.parse(readFileSync(MINE(), 'utf8'));
    const inv = g.inventory as { closing: number | null; onhand: number | null; inbound: null;
                                 basis: { reason: string; closing_reason: string | null } }[];
    expect(inv.some((r) => r.basis.closing_reason === 'not_applicable')).toBe(true);  // 不适用
    expect(inv.some((r) => r.basis.closing_reason === 'unknown_demand')).toBe(true);  // 未知
    expect(inv.some((r) => typeof r.closing === 'number')).toBe(true);                // 有数
    // ★ 两种 null 成因必须靠 closing_reason 分得开，不能只看 closing===null
    expect(new Set(inv.filter((r) => r.closing === null).map((r) => r.basis.closing_reason)).size)
      .toBeGreaterThan(1);
    // ★ inbound 恒 null（不是 0）；reason 恒定 —— 它解释的是 inbound，不是 closing
    expect(inv.every((r) => r.inbound === null)).toBe(true);
    expect(new Set(inv.map((r) => r.basis.reason))).toEqual(new Set(['no_seller_attribution']));
    expect(g.demand.some((d: { basis: string }) => d.basis === 'human')).toBe(true);
    expect(g.demand.some((d: { basis: string }) => d.basis === 'system')).toBe(true);
  });
});
