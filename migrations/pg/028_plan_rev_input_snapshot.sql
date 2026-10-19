-- 028 · 提交那一刻人填的那一层（UX-D12：「提交后又改过」只算人改的）
-- 出处：00 UX-D12 · 评审 2026-10-16__plan-list P1 —— content_digest 含 system_units，重算预估也会让计划进「改过」
-- 形状：{"purchase": {"sku|YYYY-MM": planned_units|null}, "demand": {"seller_sku|sid|YYYY-MM": expected_units|null}}
-- ★ NULL = 028 之前提交的版本（当时没存）—— 不是「没人填」；看板对它退回 content_digest 并标 basis。
-- ★ 只在提交 INSERT 时写；content_digest 不动（S-13：算法一变等同迁移）。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

ALTER TABLE plan_rev ADD COLUMN IF NOT EXISTS input_snapshot jsonb;
