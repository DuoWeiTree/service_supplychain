-- 008 · 计划整表的预测销量与预测库存
-- 出处：docs/superpowers/specs/2026-09-24-plan-forecast-design.md §6；表结构照 03 §38 / §41 原文。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由（事务边界由 apply() 持有）。

-- spec §3.3：预测销量的依据随数一起存（base_rate、窗口、censor 天数……）。
-- ★ 可空：不适用 / 没算过的格子没有依据，空 ≠ 「依据为空对象」。
ALTER TABLE plan_demand_cell ADD COLUMN system_basis jsonb;

-- 03 §38：段做成数据，不做成代码。★ directed 就是 14 §1 那条横线。
CREATE TABLE pipeline_segment (
    segment  text PRIMARY KEY,
    ord      int  NOT NULL UNIQUE,
    label    text NOT NULL,
    directed boolean NOT NULL
);

INSERT INTO pipeline_segment (segment, ord, label, directed) VALUES
    ('making',   1, '生产中',            false),
    ('local',    2, '本地仓在库(待排货)', false),
    ('staged',   3, '排货中(已排未发)',   true ),
    ('shipping', 4, '货运中(已发未到)',   true );

-- 03 §41：提前期（★ 天，用户自己改；不许写死在代码里）。
-- ★ 当前生效行 = valid_to IS NULL。改动走 PUT /v1/lead-time：关旧行、插新行（OP-4）。
CREATE TABLE lead_time (
    route      text NOT NULL,
    segment    text NOT NULL REFERENCES pipeline_segment(segment),
    days       integer NOT NULL CHECK (days >= 0 AND days <= 720),
    valid_from date NOT NULL,
    valid_to   date,
    PRIMARY KEY (route, segment, valid_from)
);

INSERT INTO lead_time (route, segment, days, valid_from) VALUES
    ('oversea_sea', 'making',   30, DATE '2026-01-01'),
    ('oversea_sea', 'shipping', 60, DATE '2026-01-01'),
    ('air',         'shipping', 10, DATE '2026-01-01'),
    ('domestic',    'shipping',  3, DATE '2026-01-01');
