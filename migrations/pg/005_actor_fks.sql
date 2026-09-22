-- 005 · 审计列补外键：谁认领 · 谁释放 · 谁改的格子 · 谁推的状态
-- 出处：复核 M9（2026-09-22）。`plan.owner_actor` / `plan.created_by` 从 002 起就有外键，
-- 其余五列没有 —— 而这五列恰恰是「出了事回头查是谁干的」要依赖的那几列。
--
-- ★ 今天没有脏数据是因为 API 在每次写之前都校验 x-actor（api/ui/deps.py）。
--   但那是**接口层**的自觉：迁移脚本、阶段 B 的 jobs/、将来任何绕过 api/ 的写入
--   都不经过它。审计列的正确性只能由库层担保，否则「担保」等于「目前还没人写错」。
--
-- ★ 不需要 NOT VALID：`scm_test` 每轮从空 schema 建起，生产 `scm` 尚未落地。
--   将来对着有数据的库加外键才需要 NOT VALID + 单独 VALIDATE。

ALTER TABLE msku_claim
    ADD CONSTRAINT msku_claim_claimed_by_fk
    FOREIGN KEY (claimed_by) REFERENCES actor(actor_id);

ALTER TABLE msku_claim
    ADD CONSTRAINT msku_claim_released_by_fk
    FOREIGN KEY (released_by) REFERENCES actor(actor_id);

-- ★ updated_by 可空（002 里建表时就没有 NOT NULL）：外键不管空值，
--   「还没有人改过」与「改的人不存在」仍然分得开。
ALTER TABLE plan_demand_cell
    ADD CONSTRAINT plan_demand_cell_updated_by_fk
    FOREIGN KEY (updated_by) REFERENCES actor(actor_id);

ALTER TABLE plan_purchase_cell
    ADD CONSTRAINT plan_purchase_cell_updated_by_fk
    FOREIGN KEY (updated_by) REFERENCES actor(actor_id);

-- ★ 事件表是只追加的（003 的 t_event_append_only）：一条 actor 写错的事件
--   连改都改不了，只能再追加一条——所以它比其余几列更需要在写入当下就挡住。
ALTER TABLE plan_line_event
    ADD CONSTRAINT plan_line_event_actor_fk
    FOREIGN KEY (actor) REFERENCES actor(actor_id);
