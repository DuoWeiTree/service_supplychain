"""选目的仓时现查三方 sku 配对（10-13 负责人：「在选择目标海外仓时给出提示……必须配对产品后才能提交备货单」）。
★ 只读领星（AR-D1）；与提交时的判据同一个函数（CB-D8）。"""
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import OVERSEA, stock, world  # noqa: F401


def check(client, seed, wid, sku):
    return client.get(f"/v1/oversea-sku-match?wid={wid}&sku={sku}", headers=H(seed.actor))


def as_3pl(product_id, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE warehouse SET kind='oversea_3pl' WHERE wid=%s", (OVERSEA,))
        cur.execute("UPDATE sku_catalog SET product_id=%s WHERE sku=%s", (product_id, seed.sku_a))


def test_unmatched_sku_on_api_warehouse_is_named(client, seed, world, lingxing):
    as_3pl(741805, seed)
    lingxing.set_match(OVERSEA, None, "SOMETHING-ELSE", matched=False)
    r = check(client, seed, OVERSEA, seed.sku_a)
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in ("third_party", "needs_match", "matched", "codes")} == \
        {"third_party": True, "needs_match": True, "matched": False, "codes": []}


def test_matched_sku_returns_the_code(client, seed, world, lingxing):
    as_3pl(741805, seed)
    lingxing.set_match(OVERSEA, 741805, "DCC-3PL-1")
    assert {k: check(client, seed, OVERSEA, seed.sku_a).json()[k] for k in ("matched", "codes")} == \
        {"matched": True, "codes": ["DCC-3PL-1"]}


def test_self_owned_warehouse_needs_no_match_and_calls_nothing(client, seed, world, lingxing):
    lingxing.enqueue("ows_sku_match", "not_sent")                  # 万一调了就会失败 —— 自有仓不该调
    assert {k: check(client, seed, OVERSEA, seed.sku_a).json()[k] for k in ("third_party", "needs_match")} == \
        {"third_party": False, "needs_match": False}


def test_unreadable_match_list_is_named_not_guessed(client, seed, world, lingxing):
    as_3pl(741805, seed)
    lingxing.enqueue("ows_sku_match", "not_sent")
    r = check(client, seed, OVERSEA, seed.sku_a)
    assert r.status_code == 503 and r.json()["error"] == "erp_unavailable"


def test_sku_without_product_id_is_named(client, seed, world, lingxing):
    as_3pl(None, seed)
    lingxing.set_match(OVERSEA, None, "X", matched=False)
    assert check(client, seed, OVERSEA, seed.sku_a).json()["product_id"] is None
