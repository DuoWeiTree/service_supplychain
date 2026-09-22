"""在指定 schema 上幂等应用迁移。SQL 文件是表结构的唯一真源。

★ 与邻居 service_social 的唯一差别：多一列 checksum 并在 apply 时核对 ——
  「已执行的迁移不可改」这条铁律，不核对就只是一句口号。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from shared.config import business_pg
from shared.pg_client import check_schema, pg_conn, timed

MIGRATION_DIR = Path(__file__).resolve().parent


class MigrationChanged(Exception):
    def __init__(self, name: str, applied: str, current: str):
        super().__init__(
            f"迁移 {name} 的内容变了（已执行 {applied[:12]}… / 现在 {current[:12]}…）。"
            "已执行的迁移不可改 —— 要改结构就追加一个新文件。")


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def migrations() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATION_DIR.glob("*.sql"))]


def apply(schema: str) -> list[str]:
    """幂等建 schema + 应用全部迁移，返回本次**新应用**的文件名。"""
    check_schema(schema)
    newly: list[str] = []
    with timed("migrate", schema=schema), pg_conn() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(f"SET search_path TO {schema}")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            "  version text PRIMARY KEY,"
            "  checksum text NOT NULL,"
            "  applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT version, checksum FROM schema_migration")
        done = dict(cur.fetchall())
        for name, sql in migrations():
            digest = _checksum(sql)
            if name in done:
                if done[name] != digest:
                    raise MigrationChanged(name, done[name], digest)
                continue
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migration (version, checksum) VALUES (%s, %s)",
                        (name, digest))
            newly.append(name)
    return newly


def applied_versions(schema: str) -> list[str]:
    check_schema(schema)
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}")
        cur.execute("SELECT version FROM schema_migration ORDER BY version")
        return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else business_pg().get("schema", "scm")
    print(f"applied {apply(target)} to schema {target}")
