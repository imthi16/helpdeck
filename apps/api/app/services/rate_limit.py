"""Fixed-window rate limiting backed by Redis."""

import time
from dataclasses import dataclass

import redis.asyncio as redis


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int  # seconds until the window resets (0 when allowed)


class RateLimiter:
    def __init__(self, client: redis.Redis, *, limit: int, window_seconds: int = 60) -> None:
        self._client = client
        self._limit = limit
        self._window = window_seconds

    async def hit(self, identifier: str) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % self._window)
        key = f"helpdeck:rl:{identifier}:{window_start}"

        count = await self._client.incr(key)
        if count == 1:
            await self._client.expire(key, self._window)

        if count > self._limit:
            reset_at = window_start + self._window
            return RateLimitResult(allowed=False, retry_after=max(1, reset_at - now))
        return RateLimitResult(allowed=True, retry_after=0)
