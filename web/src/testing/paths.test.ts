import { afterEach, describe, expect, it, vi } from 'vitest';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { webRoot } from './paths';

describe('paths', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('webRoot() 指向含 vite.config.ts 的目录', () => {
    const root = webRoot();
    expect(existsSync(resolve(root, 'vite.config.ts'))).toBe(true);
  });

  it('★ 不在 web/ 下跑就点名报错，提示 cd web', () => {
    const repoRoot = resolve(process.cwd(), '..');
    vi.spyOn(process, 'cwd').mockReturnValue(repoRoot);
    expect(() => webRoot()).toThrow(/cd web/);
  });
});
