-- 015 · 阶段 C-a 维度补齐：本地货品 id · msku 的 fnsku（C-b 建备货单要用）
-- 出处：spec 2026-10-10-dispatch-draft-design §4（10-10 实测：库存表 product_id = local_products.id 960/960；
--       listing fnsku FBA 9,774 有 / 85 空，FBM 全空）
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。可空：空 = 源里没有，不是 0 / ''。
ALTER TABLE sku_catalog ADD COLUMN IF NOT EXISTS product_id bigint;
ALTER TABLE msku_bridge ADD COLUMN IF NOT EXISTS fnsku text;
