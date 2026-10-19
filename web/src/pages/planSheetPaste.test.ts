import { describe, expect, it } from 'vitest';
import { planPaste, type PasteRow } from './planSheetPaste';

const P = ['2026-10', '2026-11', '2026-12'];
const buy: PasteRow<string> = { kind: 'purchase', name: 'SKU-1', ref: 'p:SKU-1' };
const a: PasteRow<string> = { kind: 'demand', name: 'MSKU-A', ref: 'd:A' };
const b: PasteRow<string> = { kind: 'demand', name: 'MSKU-B', ref: 'd:B' };
const rows = [buy, a, b];
const byMsku = new Map([['MSKU-A', [a]], ['MSKU-B', [b]], ['MSKU-DUP', [a, b]]]);
const flat = (p: ReturnType<typeof planPaste<string>>) => p.cells.map((c) => `${c.row.ref}@${c.period}=${c.raw}`);

describe('粘贴对齐（UX-D16）', () => {
  it('★ 首列是 msku → 按 msku 对齐，与屏上行序无关；值从选区左列起铺', () => {
    const plan = planPaste('MSKU-B\t7\t8\nMSKU-A\t1\n', rows, P, { r0: 0, r1: 0, c0: 1, c1: 1 }, byMsku);
    expect(plan.mode).toBe('msku');
    expect(flat(plan)).toEqual(['d:B@2026-11=7', 'd:B@2026-12=8', 'd:A@2026-11=1']);
    expect(plan.notes).toEqual([]);
  });

  it('★ 对不上的 msku、一个 msku 对多家店、超出月份的格 —— 逐条点名，不静默丢', () => {
    const plan = planPaste('MSKU-A\t1\t2\t3\nNOPE\t5\nMSKU-DUP\t6\n', rows, P, { r0: 0, r1: 0, c0: 1, c1: 1 }, byMsku);
    expect(flat(plan)).toEqual(['d:A@2026-11=1', 'd:A@2026-12=2']);
    expect(plan.notes).toEqual([
      'NOPE：本计划里没有这个 msku，没写',
      'MSKU-DUP：本计划里有 2 家店用这个 msku，分不出写哪家，没写',
      '1 格超出计划月份，没写',
    ]);
  });

  it('★ 按位置铺开：落到不同类的行不写并点名（采购行 / 销量行不串）', () => {
    const plan = planPaste('500\t600\n11\t12\n', rows, P, { r0: 0, r1: 0, c0: 0, c1: 0 }, byMsku);
    expect(plan.mode).toBe('position');
    expect(flat(plan)).toEqual(['p:SKU-1@2026-10=500', 'p:SKU-1@2026-11=600']);
    expect(plan.notes).toEqual(['第 2 行落在销量行 MSKU-A，与左上角的采购行不同类，没写']);
  });

  it('单个值铺满选区，同样不串行类；超出表尾的行点名', () => {
    const fill = planPaste('9', rows, P, { r0: 1, r1: 2, c0: 0, c1: 1 }, byMsku);
    expect(flat(fill)).toEqual(['d:A@2026-10=9', 'd:A@2026-11=9', 'd:B@2026-10=9', 'd:B@2026-11=9']);
    const tail = planPaste('1\n2\n3\n', rows, P, { r0: 1, r1: 1, c0: 2, c1: 2 }, byMsku);
    expect(flat(tail)).toEqual(['d:A@2026-12=1', 'd:B@2026-12=2']);
    expect(tail.notes).toEqual(['1 行超出表尾，没写']);
  });
});
