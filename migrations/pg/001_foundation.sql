-- 001 · 地基：只追加守卫 · 操作人 · 四张维度镜像 · 镜像新鲜度视图
-- 出处：03 §0.1（forbid_update_delete）· 03 §8.1（2026-09-22 补，S-9）· 03 §7.1（E-4）
--
-- ★ 迁移里一律不写 scm. 前缀（连函数、触发器名）：靠 search_path 落到目标 schema，
--   同一份 SQL 才能既跑在 scm 上、也跑在测试的 scm_test 上。
-- ★ 本文件不含 BEGIN/COMMIT：事务边界由 apply() 持有。自带边界的 SQL 会让
--   外层的 rollback 兜不住，"预演"就变成真落库。

-- ★ CREATE OR REPLACE：PG 没有 CREATE FUNCTION IF NOT EXISTS，而 apply() 可能
--   被指到一个已有同名函数的 schema 上。表用 IF NOT EXISTS 就够。
CREATE OR REPLACE FUNCTION forbid_update_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% 是只追加表，不允许 %（证据一旦可改，就不再是证据）',
        TG_TABLE_NAME, TG_OP;
END $$;

CREATE TABLE IF NOT EXISTS actor (
    actor_id   text PRIMARY KEY,           -- ★ 阶段 A 内网裸跑：前端下拉选（Q-5，留痕可伪造）
    name       text NOT NULL,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seller (        -- 店铺（= 领星 sid，全字段字符串）
    seller_id    text PRIMARY KEY,
    name         text NOT NULL,
    market       text NOT NULL,            -- US / UK / DE …
    has_fba      boolean NOT NULL,         -- ★ 无 FBA 的平台显示「不适用」，不是 0（02 §3.1a）
    platform     text NOT NULL,            -- amazon / walmart / …
    refreshed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS sku_catalog (   -- 货号：物理产品
    sku          text PRIMARY KEY,
    name         text NOT NULL,
    refreshed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS msku_bridge (   -- msku（listing）→ 货号
    seller_sku   text NOT NULL,
    sid          text NOT NULL,
    sku          text NOT NULL,
    refreshed_at timestamptz NOT NULL,
    -- ★ 主键必须含 sid：同一 msku 字符串跨店是不同 listing，
    --   不含它会撞键并静默覆盖店铺归属。
    CONSTRAINT msku_bridge_pkey PRIMARY KEY (seller_sku, sid),
    CONSTRAINT msku_bridge_seller_fk FOREIGN KEY (sid) REFERENCES seller(seller_id),
    CONSTRAINT msku_bridge_sku_fk    FOREIGN KEY (sku) REFERENCES sku_catalog(sku)
);

CREATE TABLE IF NOT EXISTS warehouse (     -- 仓（★ 不是库位，实测没有实体库位）
    wid          int  PRIMARY KEY,
    name         text NOT NULL,
    kind         text NOT NULL CONSTRAINT warehouse_kind_known
                      CHECK (kind IN ('local','oversea_self','oversea_3pl','fba')),
    market       text,
    refreshed_at timestamptz NOT NULL
);

-- 启动钩子与就绪探针共用：任一维度镜像陈旧超阈值 → 拒绝服务（E-4）。
-- ★ 空表时 max() 返回 NULL —— 调用方必须把 NULL 判成「不可用」，
--   把它当新鲜就是拿一张空表在服务。
CREATE OR REPLACE VIEW v_mirror_freshness AS
         SELECT 'sku_catalog' AS mirror, max(refreshed_at) AS refreshed_at FROM sku_catalog
UNION ALL SELECT 'msku_bridge', max(refreshed_at) FROM msku_bridge
UNION ALL SELECT 'seller',      max(refreshed_at) FROM seller
UNION ALL SELECT 'warehouse',   max(refreshed_at) FROM warehouse;
