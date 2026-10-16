"""CB-D3：物流方式 —— 领星头程物流渠道镜像 + 每张海外仓排货单必选 + G7。"""
from shared.pg_client import pg_conn
from tests.helpers import H
from tests.test_api_sheets import OVERSEA, new_sheet, put, stock, world  # noqa: F401


def test_channels_auto_refresh_when_empty_then_served_from_mirror(client, seed, world, lingxing):
    body = client.get("/v1/logistics-channels", headers=H(seed.actor)).json()
    assert [(c["logistics_id"], c["channel_name"], c["enabled"]) for c in body["channels"]] == [(28762, "美森快船", True)]
    assert body["refreshed_at"] is not None and lingxing.channel_calls == 1
    client.get("/v1/logistics-channels", headers=H(seed.actor))
    assert lingxing.channel_calls == 1, "镜像新鲜时不再打领星"


def test_refresh_failure_is_named(client, seed, world, lingxing):
    lingxing.enqueue("logistics_channel_list", "not_sent")
    r = client.post("/v1/logistics-channels/refresh", headers=H(seed.actor))
    assert r.status_code == 503 and r.json()["error"] == "erp_unavailable" and r.json()["outcome"] == "not_sent"


def oversea_sheet(client, seed, world):  # noqa: F811
    sid = new_sheet(client, seed, route="oversea")
    put(client, seed, sid, line_id=world["line_id"], units=5, channel="oversea", dest_wid=OVERSEA)
    client.get("/v1/logistics-channels", headers=H(seed.actor))
    return sid


def test_pick_logistics_for_an_oversea_consignment(client, seed, world, lingxing):
    sid = oversea_sheet(client, seed, world)
    r = client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762}, headers=H(seed.actor))
    assert r.status_code == 200, r.text
    gates = client.get(f"/v1/sheets/{sid}/preflight").json()["gates"]
    assert next(g for g in gates if g["gate"] == "G7")["ok"] is True


def test_g7_blocks_until_picked(client, seed, world, lingxing):
    sid = oversea_sheet(client, seed, world)
    g7 = next(g for g in client.get(f"/v1/sheets/{sid}/preflight").json()["gates"] if g["gate"] == "G7")
    assert g7["ok"] is False and g7["detail"] == [{"dest_wid": OVERSEA}]


def test_logistics_only_for_oversea_destinations_and_enabled_channels(client, seed, world, lingxing):
    sid = oversea_sheet(client, seed, world)
    r = client.put(f"/v1/sheets/{sid}/logistics/{seed.wid}", json={"logistics_id": 28762}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "not_an_oversea_destination"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE logistics_channel SET enabled=false")
    r = client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "logistics_not_available"
    r = client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 1}, headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "logistics_not_available"


def test_sheet_detail_carries_the_choice(client, seed, world, lingxing):
    sid = oversea_sheet(client, seed, world)
    client.put(f"/v1/sheets/{sid}/logistics/{OVERSEA}", json={"logistics_id": 28762}, headers=H(seed.actor))
    d = client.get(f"/v1/sheets/{sid}").json()
    assert d["logistics"] == [{"dest_wid": OVERSEA, "logistics_id": 28762, "channel_name": "美森快船", "enabled": True}]
