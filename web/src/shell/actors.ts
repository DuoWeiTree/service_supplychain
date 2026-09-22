// ★ 阶段 A 没有 actor 接口（08 §1.4 只有 skus/sellers/warehouses/categories）。
//   两种数据源都读这一份常量，登记在计划附录 B。
export interface Actor { actor_id: string; name: string }

export const ACTORS: readonly Actor[] = [
  { actor_id: 'ops.zhang', name: '张 · 运营' },
  { actor_id: 'ops.li', name: '李 · 运营' },
  { actor_id: 'admin.wu', name: '吴 · 管理员' },
];
