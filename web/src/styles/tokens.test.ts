import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { prototypeAsset, webRoot } from '../testing/paths';

// ★ 路径解析交给 testing/paths.ts 的 prototypeAsset() / webRoot() 统一做
//   （不在这里各自 resolve(process.cwd(), …)一遍）—— 不在 web/ 下跑，
//   会在 webRoot() 那一处点名报错，而不是这里 7 个用例各自 ENOENT。
const SRC = prototypeAsset('shell.css');
const DST = resolve(webRoot(), 'src/styles/shell.css');

// ★ 墨迹系统的六个承重 token —— 值写死在这里，因为 #F0F1ED（冷纸）与 #F4F1EA（暖奶油）
//   只差几个色阶而方向相反，"顺手微调" 不会有任何东西报错。
const TOKENS: Record<string, string> = {
  '--paper': '#F0F1ED',
  '--paper-lo': '#E7E9E3',
  '--ink': '#16293D',
  '--pencil': '#8B939B',
  '--vermilion': '#C4342A',
  '--jade': '#2E6B54',
};

describe('shell.css 原样带走', () => {
  it('与原型的那一份逐字节相同', () => {
    const src = readFileSync(SRC);
    const dst = readFileSync(DST);
    const h = (b: Buffer) => createHash('sha256').update(b).digest('hex');
    expect(h(dst)).toBe(h(src));
  });

  it.each(Object.entries(TOKENS))('%s = %s', (name, value) => {
    const css = readFileSync(DST, 'utf8');
    const m = new RegExp(`${name}:\\s*([#A-Za-z0-9(),. %-]+);`).exec(css);
    expect(m?.[1]?.trim()).toBe(value);
  });
});
