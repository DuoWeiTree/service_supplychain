import { AppShell } from '../shell/AppShell';

// Task 4~6 落地真页面后整个文件删除
export function PurchasePlaceholder({ crumb }: { crumb: string }) {
  return (
    <AppShell crumb={crumb}>
      <h1 className="head">{crumb}</h1>
      <p className="muted">此页尚未实现：后续任务落地后替换本占位，当前没有可操作内容。</p>
    </AppShell>
  );
}
