/** 从 Excel 粘一块到计划整表（UX-D16）。纯函数：只决定「哪一格写什么」与「哪些没写、为什么」。
 *  ★ 对不上的一律进 notes 点名 —— 静默丢掉的格子和「没粘到」长得一样。 */
import type { Period } from '../api/types';

export type RowKind = 'purchase' | 'demand';
export interface PasteRow<T> { kind: RowKind; name: string; ref: T }
export interface PasteCell<T> { row: PasteRow<T>; period: Period; raw: string }
export interface PastePlan<T> {
  mode: 'msku' | 'position';
  cells: PasteCell<T>[];
  notes: string[];
  /** 按位置铺开时选区落到的右下角（行 / 列下标）；msku 对齐时不动选区 */
  end: { r: number; c: number } | null;
}
export interface Sel { r0: number; r1: number; c0: number; c1: number }

const KIND: Record<RowKind, string> = { purchase: '采购', demand: '销量' };

export function parseClipboard(text: string): string[][] {
  return text.replace(/\r/g, '').replace(/\n$/, '').split('\n').map((l) => l.split('\t'));
}

/**
 * - 首列有一格认得出是本计划的 msku → 按 msku 对齐：每行找那个 msku，值从选区左列起铺。
 * - 否则按位置从选区左上角铺开；单个值铺满整个选区。落到与左上角**不同类**的行（采购 / 销量）不写。
 */
export function planPaste<T>(
  text: string, rows: PasteRow<T>[], periods: Period[], sel: Sel, byMsku: Map<string, PasteRow<T>[]>,
): PastePlan<T> {
  const lines = parseClipboard(text);
  const notes: string[] = [];
  const cells: PasteCell<T>[] = [];
  let overCols = 0;

  if (lines.some((l) => byMsku.has((l[0] ?? '').trim()))) {
    for (const l of lines) {
      const name = (l[0] ?? '').trim();
      const hit = byMsku.get(name) ?? [];
      if (hit.length === 0) { notes.push(`${name || '（空）'}：本计划里没有这个 msku，没写`); continue; }
      if (hit.length > 1) { notes.push(`${name}：本计划里有 ${hit.length} 家店用这个 msku，分不出写哪家，没写`); continue; }
      l.slice(1).forEach((raw, j) => {
        const p = periods[sel.c0 + j];
        if (p === undefined) { overCols++; return; }
        cells.push({ row: hit[0]!, period: p, raw });
      });
    }
    if (overCols > 0) notes.push(`${overCols} 格超出计划月份，没写`);
    return { mode: 'msku', cells, notes, end: null };
  }

  const top = rows[sel.r0];
  if (!top) return { mode: 'position', cells, notes, end: null };
  const single = lines.length === 1 && lines[0]!.length === 1;
  const height = single ? sel.r1 - sel.r0 + 1 : lines.length;
  const width = single ? sel.c1 - sel.c0 + 1 : Math.max(...lines.map((l) => l.length));
  let overRows = 0;
  for (let i = 0; i < height; i++) {
    const row = rows[sel.r0 + i];
    if (!row) { overRows++; continue; }
    if (row.kind !== top.kind) {
      notes.push(`第 ${i + 1} 行落在${KIND[row.kind]}行 ${row.name}，与左上角的${KIND[top.kind]}行不同类，没写`);
      continue;
    }
    for (let j = 0; j < width; j++) {
      const raw = single ? lines[0]![0]! : lines[i]?.[j];
      if (raw === undefined) continue;
      const p = periods[sel.c0 + j];
      if (p === undefined) { overCols++; continue; }
      cells.push({ row, period: p, raw });
    }
  }
  if (overRows > 0) notes.push(`${overRows} 行超出表尾，没写`);
  if (overCols > 0) notes.push(`${overCols} 格超出计划月份，没写`);
  const end = {
    r: Math.min(rows.length - 1, sel.r0 + height - 1),
    c: Math.min(periods.length - 1, sel.c0 + width - 1),
  };
  return { mode: 'position', cells, notes, end };
}
