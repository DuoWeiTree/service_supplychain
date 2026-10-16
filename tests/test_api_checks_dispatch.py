from shared.pg_client import pg_conn
from tests.test_ddl_017_views import placed  # noqa: F401
from tests.test_ddl_016_dispatch import world  # noqa: F401


def test_dispatch_checks_empty_then_names_violation(client, placed):  # noqa: F811
    body = client.get("/v1/checks/dispatch").json()
    assert body == {"ok": True, "counts": {"map_ne_arrangement": 0, "line_ne_sum_map": 0,
                                           "over_total": 0, "msku_sku_drift": 0,
                                           "line_not_dispatchable": 0}, "violations": []}
    _, a1, _, _ = placed
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE dispatch_arrangement SET units = 31 WHERE arrangement_id=%s", (a1,))
    body = client.get("/v1/checks/dispatch").json()
    assert body["ok"] is False and body["counts"]["map_ne_arrangement"] == 1
    assert body["violations"][0]["ref"]["arrangement_id"] == a1
