# service_supplychain

供应链后端服务。设计与论证在 `docs/`，这里只写怎么跑。

## 跑起来

    uv sync
    cp config.example.toml config.toml     # 填 [business_pg] / [clickhouse] 口令
    uv run python -m migrations.pg.apply   # 应用迁移到 config.toml 里的 schema（默认 scm）
    uv run uvicorn --factory api:create_app --port 8091

`config.toml` 不入库（`.gitignore`），模板是 `config.example.toml` —— 新机器上
**必须先 cp**，否则第一次取配置就会以 `FileNotFoundError` 挂掉。
配置路径由环境变量 `JXD_SCM_CONFIG` 整体覆盖（覆盖的是**文件路径**，不是单个字段）：
测试夹具用它指向一份把 `[business_pg] schema` 改成 `scm_test` 的临时副本，
离线那条命令用它指向一个不存在的路径来证明纯层压根不读配置。

日志由 `create_app()` 配（`shared/logging.py`）：`scm` 这个 logger 在 INFO 上
挂到 stderr，格式 `时间 级别 logger 消息`。不配的话 uvicorn 只管 `uvicorn*`，
`scm.*` 会落进 `logging.lastResort` 被按 WARNING 丢掉 —— 耗时、启动检查、
写入结果三类日志一条都不会出现。

    uv run python -m jobs.probe_ch              # 探源表列名（只读，需内网）
    uv run python -m jobs.refresh_dims          # 手动刷一轮四张维度镜像
    uv run python -m jobs.refresh_dims --only warehouse

镜像刷新平时由进程内的 APScheduler 每日跑一次（`[freshness] refresh_at`，默认
06:30 Asia/Shanghai，落在 CH 采集窗口之后）。三个入口——调度、上面那条 CLI、
`POST /v1/jobs/refresh-dims`——共用一把 PG advisory lock，第二个来的会拿到
409 / 退出码 3，不排队也不假装成功。

刷新失败或掉档超 `coverage_drop_threshold` 时**整批拒绝**、旧镜像原封不动，
`dim_refresh_run` 里会有一行 `ok=false` 说明是哪一步、丢了多少行、为什么丢。

启动只做记录 + 触发：`[freshness] startup_gate = true` 时，若有维度镜像仍陈旧，
启动检查会记一条日志并触发一次立即刷新，**不会**阻断进程启动——`/health` 与
`/v1/readiness` 永远可达（OQ-8 裁定 09-22）。`startup_gate = false` 时只记录，
不触发刷新。不管哪种配置，业务端点的拒绝服务都只发生在按请求判的
`require_fresh_mirrors`（`docs/03` §7.1 E-4）。

## 预测取数（`[forecast]`）

阶段 A 的销量/在仓/采购在途取数走 `dim/ch_source.py::ChSource`（详细口径见
`docs/superpowers/specs/2026-09-23-chsource-design.md`）。换源只改一行：

    [forecast]
    source = "fixture"   # 改成 "ch" 即切到真 ClickHouse，无需改代码

`[forecast]` 段（`config.example.toml`）七组键：

| 键 | 作用 |
|---|---|
| `source` | `"fixture"`（默认，`tests/fixtures/` 的 5 个假 msku，离线可跑）或 `"ch"`（真 ClickHouse）。漏配不该变成「去连生产」，所以默认不是 `"ch"` |
| `snapshot_lookback_days` / `snapshot_settle_minutes` | 快照日候选窗口，以及「这批采集算写完了」的静置时长——防止读到还没写完的半截批次 |
| `cache_ttl_seconds` | `ChSource` 内部缓存：快照批次按采集日缓存（同一天不可变），`as_of` 的解析结果按这个秒数缓存 |
| `sales_months_max` | 一次最多回溯多少个完整自然月 |
| `drop_threshold` | 掉档阈值——相邻两个候选日行数掉幅超过它就拒绝该候选日 |
| `min_rows` / `min_distinct_sid` | 绝对地板——纯粹「比对相邻候选日」测不出整窗口同步塌陷，这两个数字是最后一道底线 |
| `purchase_staleness_days` | 采购单快照的陈旧阈值（默认 3 天）：超过这么多天没有新采集日，整批在途视为「未知」（抛 `PurchaseTableStale`），不是悄悄当 0 |

三个已知缺口（阶段 A 不解决，见 design §8）：

- **`sid=0` 共享池不分摊，只排除计数**（OQ-2）：欧洲共享池（PL+SE 合池，实测占全部可售 27.6%）不折进任何一个 sid，响应带 `shared_pool_excluded` 计数，PL/SE 两店的在仓因此系统性偏低。
- **逾期在途归入 `sku_pipeline[].bucket="overdue"` 单列展示，不重定日期**（OQ-6）：`expect_arrive_time` 大面积逾期的行不会被悄悄挪到未来某个月，只会单独标出来。
- **L-3 晚到订单占比未与重复采集分开**（OQ-1）：去重已消掉「同一行跨采集日重复计数」，但「真·晚到订单」占比还有多少未实测，`monthly_sales_history` 只取**完整月**规避，响应带 `history_window` 口径标记。

## 测试

    uv run pytest -q                       # 全部（需要能连到 PG 192.168.66.210）

测试跑在 schema `scm_test` 上，跑完整个 `DROP SCHEMA`；`scm` 与 `inv` 被夹具硬拦
（`tests/conftest.py` 的 `assert_not_production_schema`）——写错 schema 不会安静地
跑过去，会在夹具这一层就炸。

### 离线可跑的那部分（纯层）

    JXD_SCM_CONFIG=/nonexistent uv run pytest -q \
        tests/test_layering.py tests/test_forecast_estimate.py \
        tests/test_forecast_projection.py tests/test_rules_submit.py \
        tests/test_dim_fixture.py tests/test_mirror_registry.py \
        tests/test_dim_ch_source.py tests/test_ch_client.py

`models` / `forecast` / `rules` / `dim` 这几层只喂 fixture 就能跑通，配置文件
指向一个不存在的路径也不影响——这条命令就是这件事的证明，不是口号。

★ 上面列的每个文件都**不许**有要连库的断言。镜像登记表的门禁 (b)（「登记了
就得有真表、真视图、真 fetch」）要连 PG，所以它单独放在
`tests/test_mirror_registry_pg.py`，不在这条命令里——拆文件而不是在命令后面
挂 `-k "not ..."`，是因为 `-k` 是一句会被人抄漏的话，抄漏之后命令照样「能跑」，
只是又开始连库；文件边界抄不漏。

### 前端 mock 用的 fixture

`tests/fixtures/grid_response.json` 是 `GET /v1/plans/{id}/grid` 的固化响应，
`web/` 的 mock 层直接读它，保证 mock 与真 API 是同一份数据。改了 grid 的响应
形状后，重新固化：

    UPDATE_GRID_FIXTURE=1 uv run pytest -q tests/test_grid_fixture.py

固化是**整份重写**，不是合并——重跑一次才能把已经删掉的字段也从 fixture 里去掉。
