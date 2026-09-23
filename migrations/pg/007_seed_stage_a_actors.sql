-- 007 · 灌入阶段 A 的三个 actor
-- 出处：生产 `scm.actor` 实测 0 行（2026-09-23）——前端 `x-actor` 下拉带着这三个
--   身份打请求，库里却一个都不认，于是每一条请求都在 `deps.py` 校验处炸
--   `400 unknown_actor`。四张维度镜像（seller/sku_catalog/msku_bridge/warehouse）
--   都有各自的刷新任务在灌，唯独 `actor` 从来没有 bootstrap 路径。
--
-- ★ 阶段 A 按 S-28 内网裸跑、无鉴权（001 里 `actor_id` 那行注释的 Q-5 就是这句话
--   的来源），`x-actor` 下拉读的是前端常量 `web/src/shell/actors.ts::ACTORS`。
--   这三行**不是从任何外部系统同步来的**，就是那份前端常量在库里的落地 ——
--   所以这是一次性的种子迁移，不是像 seller/sku_catalog 那样的周期性镜像。
--   `actor` 表本身也没有 `role` 列（见 001），TS 常量里的 `role` 字段（运营/
--   管理员）阶段 A 不落库，只落 `name`。
--
-- ★ ON CONFLICT DO NOTHING：这份种子只负责「不存在就插入」。已经存在的行
--   （不论是运营手工加的真实 actor，还是这三行本身）一律不碰 —— 迁移不做更新，
--   更新是运营的事，不是部署的事。
--
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由（事务边界由 apply() 持有）。

INSERT INTO actor (actor_id, name, active) VALUES
    ('ops.zhang', '张', true),
    ('ops.li', '李', true),
    ('admin.wu', '吴', true)
ON CONFLICT (actor_id) DO NOTHING;
