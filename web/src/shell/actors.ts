// ★ 阶段 A 没有 actor 接口（08 §1.4 只有 skus/sellers/warehouses/categories）。
//   两种数据源都读这一份常量，登记在计划附录 B。
export interface Actor { actor_id: string; name: string; role: string }

export const ACTORS: readonly Actor[] = [
  { actor_id: 'ops.zhang', name: '张', role: '运营' },
  { actor_id: 'ops.li', name: '李', role: '运营' },
  { actor_id: 'admin.wu', name: '吴', role: '管理员' },
];

/** ★ I7 裁定：不用 `A · B` 拼元串。`<option>` 里放不了并列的 span，
 *  就用中文括号 —— 姓名与角色是两个字段，拼法只有这一处。 */
export const actorLabel = (a: Actor): string => `${a.name}（${a.role}）`;
