"""IM-D8：pub 写接口只认 ai.agent；人类 actor 一律 403。"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from api.common.deps import AI_ACTOR, require_ai_actor
from api.common.errors import ApiError
from shared.pg_client import pg_conn


def _req(who: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-actor", who.encode())]})


@pytest.fixture
def actors(wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO actor (actor_id, name, active) VALUES "
                    "('alice', 'Alice', true), ('ai.agent', 'AI 员工', true)")


def test_require_ai_actor_rejects_human_actor(actors):
    with pytest.raises(ApiError) as ei:
        require_ai_actor(_req("alice"))
    assert ei.value.status == 403 and ei.value.error == "actor_not_allowed"
    assert ei.value.fields == {"actor": "alice"}


def test_require_ai_actor_accepts_ai_agent(actors):
    assert require_ai_actor(_req("ai.agent")) == AI_ACTOR
