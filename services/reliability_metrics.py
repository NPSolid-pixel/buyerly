"""Validate and persist secret-free production synthetic measurements."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

from database.db import async_session_maker
from database.models import AutomationRuntimeState


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def normalize_synthetic_metrics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("synthetic metrics must be an object")
    release_sha = str(payload.get("release_sha") or "")
    if not SHA_PATTERN.fullmatch(release_sha):
        raise ValueError("release_sha must be a full lowercase git SHA")
    availability = float(payload.get("availability_percent", 0))
    latency_p95_ms = int(payload.get("latency_p95_ms", 0))
    checks_total = int(payload.get("checks_total", 0))
    checks_passed = int(payload.get("checks_passed", 0))
    if not 0 <= availability <= 100:
        raise ValueError("availability_percent is outside 0..100")
    if not 0 <= latency_p95_ms <= 120_000:
        raise ValueError("latency_p95_ms is outside 0..120000")
    if checks_total < 1 or checks_passed < 0 or checks_passed > checks_total:
        raise ValueError("invalid synthetic check counters")
    return {
        "release_sha": release_sha,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "availability_percent": round(availability, 3),
        "latency_p95_ms": latency_p95_ms,
        "checks_total": checks_total,
        "checks_passed": checks_passed,
        "ok": checks_passed == checks_total,
    }


async def persist_synthetic_metrics(payload: Any) -> dict[str, Any]:
    normalized = normalize_synthetic_metrics(payload)
    async with async_session_maker() as session:
        row = await session.get(AutomationRuntimeState, "monitoring")
        if row is None:
            row = AutomationRuntimeState(state_key="monitoring", payload={})
            session.add(row)
        runtime = dict(row.payload or {})
        runtime["synthetic"] = normalized
        row.payload = runtime
        await session.commit()
    return normalized


async def main() -> int:
    raw = sys.stdin.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("synthetic metrics payload is too large")
    normalized = await persist_synthetic_metrics(json.loads(raw))
    print(json.dumps({"ok": True, "release_sha": normalized["release_sha"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
