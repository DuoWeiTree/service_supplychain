-- 009 · A-1：PM 品类镜像 `sku_category` + 刷新留痕 `category_refresh`
-- 出处：docs/00-待裁定清单 A-1（"要做，镜像一张映射表，但是维护的时候要记得
--   同步更新！"）· docs/03 §8/§34 的草案（图片两列 `main_image_url` /
--   `image_is_placeholder` 与匹配出处两列 `pm_sku_matched` / `match_path`
--   是本迁移在草案之上追加的 —— 草案本身 OQ-9 记着"一张 DDL 都没有"）。
--
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由 —— apply() 持有事务边界，
--   同一份 SQL 靠 search_path 落到 scm（生产）/ scm_test（测试）。
-- ★ 只追加：009 之后要改结构，开新文件，不改这一个（已执行的迁移不可改）。

CREATE TABLE IF NOT EXISTS sku_category (   -- ★ A-1：PM 品类镜像，按货号粒度
    sku                   text NOT NULL REFERENCES sku_catalog(sku),
    category_id           text NOT NULL,     -- PM 品类树的叶子 category_code
    category_path         text NOT NULL,     -- 品类树根→叶的面包屑（"/" 分隔）
    -- ★ provenance（模块 pm_source.py 的两段匹配）：这一行是靠 PM 的哪个
    --   sku_code、走哪一段对上的 —— 不落库，读接口没法回答"这条是怎么来的"。
    pm_sku_matched        text NOT NULL,
    match_path            text NOT NULL
        CONSTRAINT sku_category_match_path_known
        CHECK (match_path IN ('direct', 'base')),
    main_image_url         text,             -- NULL = PM 该产品没填主图
    -- ★ 默认 true：还没判过之前，宁可当「没有真图」，也不许默认当真图 ——
    --   计划 Global Constraints「占位图判定必须在任何出站之前完成」的另一半：
    --   判不出就先当占位，不能反过来。
    image_is_placeholder   boolean NOT NULL DEFAULT true,
    refreshed_at            timestamptz NOT NULL,
    CONSTRAINT sku_category_pkey PRIMARY KEY (sku)
);

-- ★ A-1 连带：刷新留痕 + 陈旧度守卫（docs/03 §8 草案原样，本迁移只是把它
--   从草案落成真表 —— OQ-9 点名过"migrations/pg/*.sql 里一张都没有"）。
-- ★ 与通用的 dim_refresh_run 是两件事：后者是四张 gate_503 镜像共用的刷新
--   留痕表，这里是给 sku_category 这张 label_only 镜像专用的历史留痕位——
--   本迁移只建表，写入由后续任务接线（不在 Task 1 范围内，见计划 Task 1
--   "Produces" 一节：本任务只要求表存在）。
CREATE TABLE IF NOT EXISTS category_refresh (
    refresh_id  bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    n_sku       int,
    n_changed   int,
    n_dropped   int,
    outcome     text NOT NULL,
    note        text
);
