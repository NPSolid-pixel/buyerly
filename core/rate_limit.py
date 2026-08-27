import asyncio
import hashlib
import ipaddress
import logging
import math
import secrets
import time
from collections import defaultdict
from functools import lru_cache
from typing import Callable, Optional, Tuple

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.config import settings


logger = logging.getLogger(__name__)


class RateLimitBackendUnavailable(RuntimeError):
    pass


_REDIS_SLIDING_WINDOW = """
local stamp = redis.call('TIME')
local now_ms = (tonumber(stamp[1]) * 1000) + math.floor(tonumber(stamp[2]) / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local window_start = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, window_start)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_ms = window_ms
    if oldest[2] then
        retry_ms = math.max(1, tonumber(oldest[2]) + window_ms - now_ms)
    end
    redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
    return {0, retry_ms}
end

redis.call('ZADD', KEYS[1], now_ms, member)
redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
return {1, 0}
"""


class RateLimiter:
    """Sliding-window limiter with an atomic shared Redis backend."""

    def __init__(
        self,
        cleanup_interval_seconds: int = 300,
        *,
        redis_url: str = "",
        namespace: str = "buyerly:rate-limit",
    ):
        self._records: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = cleanup_interval_seconds
        self._redis_url = redis_url.strip()
        self._namespace = namespace.rstrip(":")
        self._redis: Optional[Redis] = None

    def _cleanup_stale(self, now: float) -> None:
        """Evict records where all timestamps are older than 10 minutes."""
        threshold = now - 600
        stale_keys = [
            key
            for key, timestamps in self._records.items()
            if not timestamps or timestamps[-1] < threshold
        ]
        for key in stale_keys:
            del self._records[key]
        self._last_cleanup = now

    def _redis_client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )
        return self._redis

    def _redis_key(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{self._namespace}:{digest}"

    async def _is_allowed_redis(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int]:
        try:
            result = await self._redis_client().eval(
                _REDIS_SLIDING_WINDOW,
                1,
                self._redis_key(key),
                int(window_seconds * 1000),
                limit,
                secrets.token_hex(12),
            )
        except RedisError as exc:
            logger.error("Shared rate-limit backend is unavailable: %s", type(exc).__name__)
            raise RateLimitBackendUnavailable from exc

        allowed = bool(int(result[0]))
        retry_after = 0 if allowed else max(1, math.ceil(int(result[1]) / 1000))
        return allowed, retry_after

    async def _is_allowed_memory(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int]:
        now = time.time()
        window_start = now - window_seconds

        async with self._lock:
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_stale(now)

            timestamps = [stamp for stamp in self._records[key] if stamp > window_start]
            self._records[key] = timestamps
            if len(timestamps) < limit:
                timestamps.append(now)
                return True, 0

            retry_after = max(1, math.ceil(timestamps[0] + window_seconds - now))
            return False, retry_after

    async def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int]:
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")
        if self._redis_url:
            return await self._is_allowed_redis(key, limit, window_seconds)
        return await self._is_allowed_memory(key, limit, window_seconds)

    async def reset(self, key: Optional[str] = None) -> None:
        if self._redis_url:
            client = self._redis_client()
            try:
                if key is not None:
                    await client.delete(self._redis_key(key))
                    return
                async for redis_key in client.scan_iter(match=f"{self._namespace}:*"):
                    await client.delete(redis_key)
                await client.aclose()
                self._redis = None
                return
            except RedisError as exc:
                raise RateLimitBackendUnavailable from exc

        async with self._lock:
            if key is None:
                self._records.clear()
            else:
                self._records.pop(key, None)

    async def ready(self) -> bool:
        if not self._redis_url:
            return True
        try:
            return bool(await self._redis_client().ping())
        except RedisError:
            return False


limiter = RateLimiter(redis_url=settings.REDIS_URL)


def _normalize_ip(raw_value: str) -> Optional[str]:
    value = raw_value.strip()
    if not value:
        return None
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        if value.count(":") == 1 and "." in value:
            try:
                parsed = ipaddress.ip_address(value.rsplit(":", 1)[0])
            except ValueError:
                return None
        else:
            return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return parsed.compressed


@lru_cache(maxsize=32)
def _trusted_proxy_networks(raw_config: str):
    networks = []
    for item in raw_config.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid trusted proxy CIDR")
    return tuple(networks)


def _is_trusted_proxy(ip_value: str) -> bool:
    parsed = ipaddress.ip_address(ip_value)
    return any(
        parsed.version == network.version and parsed in network
        for network in _trusted_proxy_networks(settings.TRUSTED_PROXY_CIDRS)
    )


def get_client_ip(request: Request) -> str:
    """Use forwarded headers only when every traversed proxy is trusted."""
    peer_ip = _normalize_ip(request.client.host if request.client else "")
    if peer_ip is None:
        return "unknown"
    if not _is_trusted_proxy(peer_ip):
        return peer_ip

    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        chain = [_normalize_ip(item) for item in forwarded.split(",")]
        if not chain or any(item is None for item in chain):
            return peer_ip
        current = peer_ip
        for hop in reversed(chain):
            if not _is_trusted_proxy(current):
                break
            current = hop
        return current

    real_ip = _normalize_ip(request.headers.get("X-Real-IP", ""))
    return real_ip or peer_ip


async def _request_identity(request: Request, fields: tuple[str, ...]) -> str:
    if not fields:
        return ""
    payload = {}
    try:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type == "application/json":
            payload = await request.json()
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        return ""
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def rate_limit_dep(
    limit: int,
    window_seconds: int = 60,
    scope: str = "default",
    identity_fields: tuple[str, ...] = (),
) -> Callable:
    """Enforce independent shared limits for the client IP and account key."""

    async def _dependency(request: Request) -> None:
        client_ip = get_client_ip(request)
        identity = await _request_identity(request, identity_fields)
        keys = [f"{scope}:ip:{client_ip}"]
        if identity:
            identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            keys.append(f"{scope}:identity:{identity_hash}")

        retry_after = 0
        try:
            for key in keys:
                allowed, key_retry_after = await limiter.is_allowed(
                    key,
                    limit,
                    window_seconds,
                )
                if not allowed:
                    retry_after = max(retry_after, key_retry_after)
        except RateLimitBackendUnavailable:
            raise HTTPException(
                status_code=503,
                detail="Защита от частых запросов временно недоступна. Повторите попытку позже.",
                headers={"Retry-After": "1"},
            )

        if retry_after:
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Пожалуйста, повторите попытку через {retry_after} сек.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency
