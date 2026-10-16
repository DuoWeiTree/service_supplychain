import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { webRoot } from '../testing/paths';

// ★ 颜色只许有一个出处：tokens.css（由设计系统「供应链工作台」的 tokens.json 转写）。
//   值写死在这里，是因为「顺手微调」一个色值不会让任何别的东西报错 ——
//   而对比度（text-3 在 surface-2 上 ≥4.5:1 等）是按这些值算过的。
const TOKENS = resolve(webRoot(), 'src/styles/tokens.css');
const LIGHT: Record<string, string> = {
  '--bg': '#f5f6f8', '--surface': '#ffffff', '--text': '#141a22', '--text-3': '#626b78',
  '--primary': '#0a716b', '--danger': '#c92f22', '--warn': '#9a5200', '--ok': '#1b6e44',
};
const DARK: Record<string, string> = {
  '--bg': '#0e1115', '--surface': '#15191f', '--text': '#e8ebef', '--text-3': '#8f99a6',
  '--primary': '#43c6b8', '--danger': '#ff7a6b', '--warn': '#f0a950', '--ok': '#5bc98f',
};

function block(css: string, selector: string): string {
  const i = css.indexOf(`${selector} {`);
  expect(i, `${selector} 这一段不见了`).toBeGreaterThanOrEqual(0);
  return css.slice(i, css.indexOf('}', i));
}
const valueIn = (text: string, name: string) =>
  new RegExp(`${name}:\\s*([^;]+);`).exec(text)?.[1]?.trim();

describe('tokens.css', () => {
  const css = readFileSync(TOKENS, 'utf8');

  it.each(Object.entries(LIGHT))('浅色 %s = %s', (name, value) => {
    expect(valueIn(block(css, ':root, [data-theme="light"]'), name)).toBe(value);
  });

  it.each(Object.entries(DARK))('深色 %s = %s', (name, value) => {
    expect(valueIn(block(css, '[data-theme="dark"]'), name)).toBe(value);
    // ★ 跟随系统的那份与强制深色的那份必须一致 —— 两份各改一边不会报错
    expect(valueIn(block(css, ':root:not([data-theme="light"])'), name)).toBe(value);
  });
});

describe('样式表不写颜色字面量', () => {
  it.each(['shell.css', 'app.css'])('%s 只用 var(--…)', (file) => {
    const text = readFileSync(resolve(webRoot(), 'src/styles', file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    expect(text.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g) ?? []).toEqual([]);
  });
});

describe('样式表的注释成对', () => {
  // 少一个 /* 时，注释尾会把紧跟的那条规则吞成非法选择器，整条静默失效，不会报任何错
  it.each(['tokens.css', 'shell.css', 'app.css', 'purchase.css', 'dispatch.css'])('%s', (file) => {
    const text = readFileSync(resolve(webRoot(), 'src/styles', file), 'utf8');
    expect((text.match(/\/\*/g) ?? []).length).toBe((text.match(/\*\//g) ?? []).length);
  });
});
