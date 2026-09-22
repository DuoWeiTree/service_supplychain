# service_supplychain

供应链后端服务。设计与论证在 `docs/`，这里只写怎么跑。

## 跑起来

    uv sync
    cp config.example.toml config.toml     # 填 [business_pg] / [clickhouse] 口令
    python -m migrations.pg.apply          # 应用迁移到 config.toml 里的 schema（默认 scm）
    uvicorn --factory api:create_app --port 8090

## 测试

    uv run pytest -q                       # 全部（需要能连到 PG 192.168.66.210）

测试跑在 schema `scm_test` 上，跑完整个 `DROP SCHEMA`；`scm` 与 `inv` 被夹具硬拦
（`tests/conftest.py` 的 `assert_not_production_schema`）——写错 schema 不会安静地
跑过去，会在夹具这一层就炸。

### 离线可跑的那部分（纯层）

    JXD_SCM_CONFIG=/nonexistent uv run pytest -q \
        tests/test_layering.py tests/test_forecast_estimate.py \
        tests/test_forecast_projection.py tests/test_rules_submit.py \
        tests/test_dim_fixture.py

`models` / `forecast` / `rules` / `dim` 这几层只喂 fixture 就能跑通，配置文件
指向一个不存在的路径也不影响——这条命令就是这件事的证明，不是口号。

### 前端 mock 用的 fixture

`tests/fixtures/grid_response.json` 是 `GET /v1/plans/{id}/grid` 的固化响应，
`web/` 的 mock 层直接读它，保证 mock 与真 API 是同一份数据。改了 grid 的响应
形状后，重新固化：

    UPDATE_GRID_FIXTURE=1 uv run pytest -q tests/test_grid_fixture.py

固化是**整份重写**，不是合并——重跑一次才能把已经删掉的字段也从 fixture 里去掉。
