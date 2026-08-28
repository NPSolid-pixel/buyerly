"""Read-only production contracts executed inside the API container."""

import asyncio
import json

from sqlalchemy import text

from core.config import settings
from database.db import async_session_maker
from database.migrations import (
    _assert_database_at_head,
    _assert_schema_contract,
    alembic_config,
)


async def _count(query: str) -> int:
    async with async_session_maker() as session:
        return int((await session.execute(text(query))).scalar_one())


async def collect_checks() -> list[dict]:
    checks: list[dict] = []

    try:
        config = alembic_config()
        await _assert_database_at_head(config)
        await _assert_schema_contract()
        checks.append({"name": "database_migrations", "ok": True})
    except Exception:
        checks.append({
            "name": "database_migrations",
            "ok": False,
            "error": "database revision or schema contract mismatch",
        })

    meta_required = {
        "app_id": bool(settings.META_APP_ID.strip()),
        "app_secret": bool(settings.META_APP_SECRET.strip()),
        "login_config_id": bool(settings.META_LOGIN_CONFIG_ID.strip()),
        "redirect_uri": bool(settings.META_OAUTH_REDIRECT_URI.strip()),
        "encryption_key": bool(settings.META_TOKEN_ENCRYPTION_KEY.strip()),
        "graph_version": bool(settings.META_GRAPH_VERSION.strip()),
    }
    checks.append({
        "name": "meta_configuration",
        "ok": all(meta_required.values()),
        "details": meta_required,
    })

    isolation_queries = {
        "account_connection_workspace_mismatch": """
            SELECT count(*)
            FROM accounts AS account
            JOIN meta_connections AS connection
              ON connection.id = account.meta_connection_id
            WHERE account.workspace_id IS NULL
               OR account.workspace_id <> connection.workspace_id
        """,
        "account_group_workspace_mismatch": """
            SELECT count(*)
            FROM account_group_members AS membership
            JOIN account_groups AS account_group
              ON account_group.id = membership.group_id
            JOIN accounts AS account
              ON account.id = membership.account_id
            WHERE account_group.workspace_id IS NULL
               OR account.workspace_id IS NULL
               OR account_group.workspace_id <> account.workspace_id
        """,
    }
    for name, query in isolation_queries.items():
        try:
            violations = await _count(query)
            checks.append({
                "name": name,
                "ok": violations == 0,
                "violations": violations,
            })
        except Exception:
            checks.append({
                "name": name,
                "ok": False,
                "error": "workspace isolation query failed",
            })

    try:
        violations = await _count(
            "SELECT count(*) FROM summary_snapshots WHERE workspace_id IS NULL"
        )
        checks.append({
            "name": "summary_workspace_scope",
            "ok": violations == 0,
            "violations": violations,
        })
    except Exception:
        checks.append({
            "name": "summary_workspace_scope",
            "ok": False,
            "error": "summary workspace query failed",
        })

    return checks


async def main() -> int:
    checks = await collect_checks()
    payload = {
        "ok": all(item["ok"] for item in checks),
        "mode": "read-only",
        "meta_budget_mutations": 0,
        "checks": checks,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
