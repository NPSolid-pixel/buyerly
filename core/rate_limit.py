import asyncio
import time
from collections import defaultdict
from typing import Callable, Optional, Tuple
from fastapi import HTTPException, Request


class RateLimiter:
    """
    In-memory sliding-window rate limiter with automatic cleanup of stale keys.
    Thread-safe within asyncio event loop.
    """

    def __init__(self, cleanup_interval_seconds: int = 300):
        self._records: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = cleanup_interval_seconds

    def _cleanup_stale(self, now: float) -> None:
        """Evict records where all timestamps are older than 10 minutes."""
        threshold = now - 600
        stale_keys = [
            k for k, timestamps in self._records.items()
            if not timestamps or timestamps[-1] < threshold
        ]
        for k in stale_keys:
            del self._records[k]
        self._last_cleanup = now

    async def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int]:
        """
        Check if a request under `key` is allowed within `limit` hits per `window_seconds`.
        Returns (allowed: bool, retry_after_seconds: int).
        """
        now = time.time()
        window_start = now - window_seconds

        async with self._lock:
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_stale(now)

            timestamps = self._records[key]
            # Discard timestamps outside the current window
            timestamps = [t for t in timestamps if t > window_start]
            self._records[key] = timestamps

            if len(timestamps) < limit:
                timestamps.append(now)
                return True, 0

            # Rate limit exceeded: calculate retry after
            oldest_in_window = timestamps[0]
            retry_after = max(1, int(oldest_in_window + window_seconds - now + 0.999))
            return False, retry_after

    async def reset(self, key: Optional[str] = None) -> None:
        """Reset history for a specific key or all keys (useful in testing)."""
        async with self._lock:
            if key is None:
                self._records.clear()
            else:
                self._records.pop(key, None)


# Global rate limiter instance
limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Extract reliable client IP from headers or connection client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First entry in X-Forwarded-For is client origin
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return client_ip
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def rate_limit_dep(
    limit: int,
    window_seconds: int = 60,
    scope: str = "default",
) -> Callable:
    """
    FastAPI dependency factory enforcing rate limits on endpoints.
    Key format: '{scope}:{client_ip}'
    """
    async def _dependency(request: Request) -> None:
        client_ip = get_client_ip(request)
        key = f"{scope}:{client_ip}"
        allowed, retry_after = await limiter.is_allowed(key, limit, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Слишком много запросов. Пожалуйста, повторите попытку через {retry_after} сек.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency
