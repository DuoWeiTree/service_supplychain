import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const WEB_PACKAGE_NAME = 'service-supplychain-web';

// ★ vitest 必须从 web/ 目录跑（cd web && npx vitest run）。
//   这条假设以前是隐式的：tokens.test.ts 直接 resolve(process.cwd(), …)，
//   从别处跑就是 7× ENOENT，看起来像文件丢了，其实是 cwd 猜错了。
//   现在把假设做成一处检查、抛错时点名 —— 而不是散落在每个读文件的测试里
//   各自假设一遍（那正是「散落解析」的同一类问题）。
export function webRoot(): string {
  const cwd = process.cwd();
  const pkgPath = resolve(cwd, 'package.json');
  if (existsSync(pkgPath)) {
    try {
      const pkg = JSON.parse(readFileSync(pkgPath, 'utf8')) as { name?: string };
      if (pkg.name === WEB_PACKAGE_NAME) return cwd;
    } catch {
      // 解析失败也落到下面统一报错，不在这里悄悄放过
    }
  }
  throw new Error(`vitest 必须从 web/ 目录运行（cd web && npx vitest run）；当前 cwd=${cwd}`);
}

export function repoRoot(): string {
  return resolve(webRoot(), '..');
}

export function prototypeAsset(name: string): string {
  return resolve(repoRoot(), 'docs/prototype/assets', name);
}
