import type { ReactNode } from 'react';

// ★ 说明一律进折叠区，默认收起（CLAUDE.md：屏幕给数据，文档给论证）
export function Fold({ summary, children, testId }: { summary: string; children: ReactNode; testId?: string }) {
  return (
    <details className="fold" data-testid={testId}>
      <summary>{summary}</summary>
      <div className="fold__body">{children}</div>
    </details>
  );
}
