-- 002 · 需求域：计划 · 两张格子 · 认领 · 版本 · 记录 · 跳过留痕
-- 出处：03 §2.1 / §2.2 / §2.3（P1 与 P2 已于 2026-09-22 落地）· S-1/S-3/S-4/S-6/S-7/S-14/S-17
-- ★ 建表顺序按外键依赖：plan → msku_claim → 两张格子 → plan_rev → plan_line → skip。

CREATE TABLE IF NOT EXISTS plan (
    plan_id      bigserial PRIMARY KEY,
    title        text        NOT NULL,
    period_start date        NOT NULL,
    -- ★ M-10 裁定 1..24（03 §2.1 早期写过 1..12，按 S-7 统一到这里）
    months       int         NOT NULL DEFAULT 3
                             CONSTRAINT plan_months_1_24 CHECK (months BETWEEN 1 AND 24),
    owner_actor  text        NOT NULL CONSTRAINT plan_owner_fk REFERENCES actor(actor_id),
    archived_at  timestamptz,
    created_by   text        NOT NULL CONSTRAINT plan_created_by_fk REFERENCES actor(actor_id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_period_start_is_month_start
        CHECK (date_trunc('month', period_start) = period_start)
);

CREATE TABLE IF NOT EXISTS msku_claim (
    plan_id      bigint NOT NULL REFERENCES plan(plan_id),
    seller_sku   text   NOT NULL,
    sid          text   NOT NULL,            -- ★ 同一字符串跨店是不同 listing
    claimed_by   text   NOT NULL,
    claimed_at   timestamptz NOT NULL DEFAULT now(),
    released_by  text,
    released_at  timestamptz,                -- ★ 释放不删行，保留留痕
    plan_ordered boolean NOT NULL DEFAULT false,  -- 物化：该计划是否已进入已下单段（阶段 B 写）
    CONSTRAINT msku_claim_pkey PRIMARY KEY (plan_id, seller_sku, sid)
);

-- ★ 独占由库层裁决，不用先查后写（判据③）。窗口到「已下单」为止。
CREATE UNIQUE INDEX IF NOT EXISTS msku_claim_one_active_idx
    ON msku_claim (seller_sku, sid)
    WHERE released_at IS NULL AND NOT plan_ordered;

-- ① 期望销量：msku 级。★ 行本身就是「构成」，没有第二张构成表，
--    所以原先守「格子 = 构成之和」的约束触发器随拆表取消（S-16）。
CREATE TABLE IF NOT EXISTS plan_demand_cell (
    plan_id        bigint NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    seller_sku     text   NOT NULL,
    sid            text   NOT NULL,
    period_start   date   NOT NULL,
    system_units   int    CONSTRAINT plan_demand_cell_system_nonneg CHECK (system_units >= 0),
    -- ★ 外推出来的预估不是预估（M-13）：标记必须跟着数一起存，
    --   否则界面只能回头读别处，同一个数两个来源迟早分叉。
    system_extrapolated boolean NOT NULL DEFAULT false,
    expected_units int    CONSTRAINT plan_demand_cell_expected_nonneg CHECK (expected_units >= 0),
    updated_by     text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_demand_cell_pkey PRIMARY KEY (plan_id, seller_sku, sid, period_start),
    CONSTRAINT plan_demand_cell_bridge_fk FOREIGN KEY (seller_sku, sid)
        REFERENCES msku_bridge(seller_sku, sid),
    -- ★ 没认领就没格子（释放不删行，所以这条外键长期成立）
    CONSTRAINT plan_demand_cell_claim_fk FOREIGN KEY (plan_id, seller_sku, sid)
        REFERENCES msku_claim(plan_id, seller_sku, sid) ON DELETE CASCADE
);

-- ② 计划采购量：货号级，★ 不带店铺 —— 供应链备的是总量
CREATE TABLE IF NOT EXISTS plan_purchase_cell (
    plan_id       bigint NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    sku           text   NOT NULL REFERENCES sku_catalog(sku),
    period_start  date   NOT NULL,
    planned_units int    CONSTRAINT plan_purchase_cell_nonneg CHECK (planned_units >= 0),
    updated_by    text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_purchase_cell_pkey PRIMARY KEY (plan_id, sku, period_start)
);

CREATE TABLE IF NOT EXISTS plan_rev (
    plan_id        bigint  NOT NULL REFERENCES plan(plan_id),
    rev            int     NOT NULL,
    content_digest text    NOT NULL,      -- ★ A-2 / S-13：提交时全部生效值的哈希
    is_current     boolean NOT NULL DEFAULT false,
    in_flight      boolean NOT NULL DEFAULT true,  -- ★ 全部记录进终态后由触发器置 false
    submitted_by   text    NOT NULL,
    submitted_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_rev_pkey PRIMARY KEY (plan_id, rev)   -- ★ 并发提交撞 rev 由主键裁决
);

CREATE UNIQUE INDEX IF NOT EXISTS plan_rev_one_current_idx
    ON plan_rev (plan_id) WHERE is_current;

-- ★ S-4：同一计划最多 1 版在流转。提交新版撞上在流转的旧版 → 唯一索引冲突
--   → 409 rev_in_flight 点名旧版。
CREATE UNIQUE INDEX IF NOT EXISTS plan_rev_one_in_flight_idx
    ON plan_rev (plan_id) WHERE in_flight;

CREATE TABLE IF NOT EXISTS plan_line (
    line_id      bigserial PRIMARY KEY,   -- 代理键：承重墙要引用它，业务键太宽
    plan_id      bigint NOT NULL,
    rev          int    NOT NULL,
    -- ★ 没有 seller_id（P2 / S-3）：记录是货号级，店铺归属在组单时由采购员决定
    sku          text   NOT NULL REFERENCES sku_catalog(sku),
    period_start date   NOT NULL,
    total_units  int    NOT NULL CONSTRAINT plan_line_total_positive CHECK (total_units > 0),
    -- ★ 冻结「提交那一刻各店的期望销量」（02:958 / S-6）：
    --   {sid: {units, basis, mskus[]}}，basis ∈ human|system|unknown
    demand_by_seller jsonb NOT NULL,
    demand_at_submit int   NOT NULL
        CONSTRAINT plan_line_demand_nonneg CHECK (demand_at_submit >= 0),
    -- ★ 物化列；插入时写 '已提交'，此后只能由事件触发器写（003 挂拦截器）
    state        text   NOT NULL,
    CONSTRAINT plan_line_one_per_cell UNIQUE (plan_id, rev, sku, period_start),
    CONSTRAINT plan_line_rev_fk FOREIGN KEY (plan_id, rev) REFERENCES plan_rev(plan_id, rev)
);

-- ★ 提交时被跳过的格子，逐条留痕（判据②，不静默丢）
CREATE TABLE IF NOT EXISTS plan_submit_skip (
    skip_id      bigserial PRIMARY KEY,
    plan_id      bigint NOT NULL,
    rev          int    NOT NULL,
    sku          text   NOT NULL,
    period_start date   NOT NULL,
    reason       text   NOT NULL CONSTRAINT plan_submit_skip_reason_known
                        CHECK (reason IN ('zero_purchase','no_claimed_msku')),   -- S-14
    at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_submit_skip_rev_fk FOREIGN KEY (plan_id, rev) REFERENCES plan_rev(plan_id, rev)
);

-- ★ S-17：清单把它归为只追加，这里兑现 —— 留痕可改就不再是留痕。
DROP TRIGGER IF EXISTS t_skip_append_only ON plan_submit_skip;
CREATE TRIGGER t_skip_append_only BEFORE UPDATE OR DELETE ON plan_submit_skip
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
