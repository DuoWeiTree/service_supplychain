-- 004 · S2 整体状态：派生，不存储（04 §3）
-- ★ rank 做成表而不是代码里的 CASE：木桶口径有三个调用者（列表 / 看板 / 前端），
--   写三遍必然分叉，而分叉的形态是「同一张计划在两个屏幕上状态不同」。

CREATE TABLE IF NOT EXISTS plan_line_state_rank (
    state text PRIMARY KEY,
    rank  int  NOT NULL UNIQUE
);

INSERT INTO plan_line_state_rank VALUES
 ('已提交',1), ('已确认',2), ('已下单',3), ('准备排货',4), ('已排货',5), ('已完结',6)
ON CONFLICT DO NOTHING;
-- ★ 已撤销刻意不在表里：它是旁路终态，不参与 rank 比较（04:814-819）。
--   放进来的话，撤掉一条记录会把整张计划的木桶拉到「已撤销」。

CREATE OR REPLACE VIEW v_plan_overall_state AS
WITH pick AS (
    -- 一张计划最多 1 版在流转（S-4）；没有在流转的就看最近一版
    SELECT DISTINCT ON (plan_id) plan_id, rev
      FROM plan_rev ORDER BY plan_id, in_flight DESC, rev DESC
)
SELECT p.plan_id,
       pick.rev AS state_rev,
       CASE
         -- ★ 从未提交 → NULL。它不是「已撤销」，两者在看板上的处置相反
         WHEN pick.rev IS NULL THEN NULL
         -- ★ 全部撤销、以及一条记录都没有的空计划单 → 已撤销（04 §3.1）
         --   不显式定义，MIN() 会返回 NULL，而 NULL 在下游每一处表现都不一样
         WHEN agg.live = 0 THEN '已撤销'
         ELSE agg.min_state
       END AS overall
  FROM plan p
  LEFT JOIN pick ON pick.plan_id = p.plan_id
  LEFT JOIN LATERAL (
      SELECT count(*) FILTER (WHERE l.state <> '已撤销') AS live,
             (SELECT l2.state
                FROM plan_line l2 JOIN plan_line_state_rank r ON r.state = l2.state
               WHERE l2.plan_id = p.plan_id AND l2.rev = pick.rev
               ORDER BY r.rank LIMIT 1) AS min_state
        FROM plan_line l
       WHERE l.plan_id = p.plan_id AND l.rev = pick.rev
  ) agg ON true;
