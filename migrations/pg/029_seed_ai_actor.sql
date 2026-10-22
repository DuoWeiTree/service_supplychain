-- 029 · 灌入 AI 员工 actor
-- 出处：IM-D8（docs/00 IM 组）。pub 写接口只认 ai.agent，x-actor 仍要过 actor 表校验。
-- ★ 不进 web/src/shell/actors.ts：它是对外调用的身份，不是界面下拉里的人。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001（事务边界由 apply() 持有）。

INSERT INTO actor (actor_id, name, active) VALUES
    ('ai.agent', 'AI 员工', true)
ON CONFLICT (actor_id) DO NOTHING;
