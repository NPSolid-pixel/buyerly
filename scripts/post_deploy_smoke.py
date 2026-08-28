#!/usr/bin/env python3
"""Persist a secret-free, read-only production smoke result."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "").strip()
APP_DIR = Path(os.environ.get("APP_DIR", "/opt/buyerly"))
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
RESULT_DIR = Path(os.environ.get("SMOKE_RESULT_DIR", str(APP_DIR / "logs" / "smoke")))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http(path: str) -> tuple[int, dict]:
    request = Request(
        f"{BASE_URL}{path}",
        headers={"User-Agent": "buyerly-read-only-smoke/1"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        return error.code, payload


def _command(name: str, command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")
    return completed.stdout.strip()


def _record(checks: list[dict], name: str, operation) -> None:
    started = time.monotonic()
    try:
        details = operation()
        checks.append({
            "name": name,
            "ok": True,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "details": details,
        })
    except Exception as error:
        checks.append({
            "name": name,
            "ok": False,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": str(error)[:240],
        })


def _health(path: str, expected_status: str) -> dict:
    status, payload = _http(path)
    if status != 200 or payload.get("status") != expected_status:
        raise RuntimeError(f"{path} returned an unexpected health response")
    if payload.get("version") != EXPECTED_SHA:
        raise RuntimeError(f"{path} does not report the expected release")
    return {"status": expected_status, "version": EXPECTED_SHA}


def _auth_boundary() -> dict:
    protected_paths = (
        "/api/workspaces",
        "/api/summary",
        "/api/meta/oauth/config",
    )
    statuses = {}
    for path in protected_paths:
        status, _ = _http(path)
        statuses[path] = status
        if status != 401:
            raise RuntimeError(f"{path} did not reject an unauthenticated request")
    return statuses


def _runtime_versions() -> dict:
    containers = {
        "buyerly-api": f"buyerly-app:{EXPECTED_SHA}",
        "buyerly-telegram-bot": f"buyerly-app:{EXPECTED_SHA}",
        "buyerly-worker": f"buyerly-app:{EXPECTED_SHA}",
        "buyerly-web": f"buyerly-web:{EXPECTED_SHA}",
    }
    observed = {}
    for container_name, expected_image in containers.items():
        image = _command(
            "runtime image inspection",
            ["docker", "inspect", "--format", "{{.Config.Image}}", container_name],
        )
        observed[container_name] = image
        if image != expected_image:
            raise RuntimeError(f"{container_name} does not use the expected release")
    return observed


def _worker_heartbeat() -> dict:
    output = _command(
        "worker heartbeat",
        [
            "docker",
            "exec",
            "buyerly-worker",
            "python",
            "-c",
            (
                "import json,time; from pathlib import Path; "
                "p=Path('/tmp/buyerly-worker-heartbeat'); "
                "cycle=Path('/tmp/buyerly-worker-day-boundary-cycle-complete'); "
                "age=time.time()-p.stat().st_mtime; "
                "assert age < 45 and cycle.is_file(); "
                "print(json.dumps({'heartbeat_age_seconds':round(age,3),'day_boundary_cycle':True}))"
            ),
        ],
    )
    return json.loads(output.splitlines()[-1])


def _internal_contracts() -> dict:
    output = _command(
        "in-container production contracts",
        ["docker", "exec", "buyerly-api", "python", "-m", "services.smoke_checks"],
    )
    payload = json.loads(output.splitlines()[-1])
    if not payload.get("ok"):
        failed = [item.get("name") for item in payload.get("checks", []) if not item.get("ok")]
        raise RuntimeError("internal contracts failed: " + ", ".join(str(item) for item in failed))
    return payload


def _publish_reliability_metrics(checks: list[dict]) -> dict:
    endpoint_checks = [
        item for item in checks if item.get("name") in {"health_live", "health_ready"}
    ]
    durations = sorted(int(item.get("duration_ms", 0) or 0) for item in endpoint_checks)
    passed = sum(1 for item in endpoint_checks if item.get("ok"))
    payload = {
        "release_sha": EXPECTED_SHA,
        "availability_percent": round(passed / len(endpoint_checks) * 100, 3) if endpoint_checks else 0,
        "latency_p95_ms": durations[-1] if durations else 0,
        "checks_total": len(endpoint_checks),
        "checks_passed": passed,
    }
    completed = subprocess.run(
        ["docker", "exec", "-i", "buyerly-api", "python", "-m", "services.reliability_metrics"],
        cwd=APP_DIR,
        input=json.dumps(payload, separators=(",", ":")),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("failed to persist secret-free synthetic metrics")
    return payload


def _write_result(payload: dict) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    destination = RESULT_DIR / f"post-deploy-{EXPECTED_SHA}.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=RESULT_DIR,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def main() -> int:
    if len(EXPECTED_SHA) != 40 or any(character not in "0123456789abcdef" for character in EXPECTED_SHA):
        print("[ERROR] EXPECTED_SHA must be a full lowercase git SHA.")
        return 1

    checks: list[dict] = []
    started_at = _utc_now()
    _record(checks, "health_live", lambda: _health("/health/live", "alive"))
    _record(checks, "health_ready", lambda: _health("/health/ready", "ready"))
    _record(checks, "authentication_boundary", _auth_boundary)
    _record(checks, "runtime_versions", _runtime_versions)
    _record(checks, "worker_heartbeat", _worker_heartbeat)
    _record(checks, "database_meta_isolation_summary", _internal_contracts)
    _record(checks, "reliability_metrics", lambda: _publish_reliability_metrics(checks))

    payload = {
        "schema_version": 1,
        "release_sha": EXPECTED_SHA,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "mode": "read-only",
        "meta_budget_mutations": 0,
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }
    result_path = _write_result(payload)
    print(json.dumps({
        "ok": payload["ok"],
        "release_sha": EXPECTED_SHA,
        "result_path": str(result_path),
        "failed_checks": [item["name"] for item in checks if not item["ok"]],
    }, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
