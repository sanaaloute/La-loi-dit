"""Cache abstraction with Redis backend and in-memory fallback.

Used for embeddings, search results, retrieval results, prompt cache,
LLM responses and frequent legal questions. All entries are namespaced
and TTL-bound; failures degrade silently to a cache miss, never an error.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional, Protocol

from backend.core.config import Settings


class CacheProtocol(Protocol):
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear_prefix(self, prefix: str) -> None: ...


class InMemoryCache:
    """Process-local TTL cache — default for dev/tests and Redis outages."""

    def __init__(self, default_ttl: int = 3600):
        self.default_ttl = default_ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        async with self._lock:
            self._store[key] = (time.time() + (ttl or self.default_ttl), value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> None:
        async with self._lock:
            for key in [k for k in self._store if k.startswith(prefix)]:
                self._store.pop(key, None)


class RedisCache:
    def __init__(self, url: str, default_ttl: int = 3600):
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)
        self.default_ttl = default_ttl

    async def get(self, key: str) -> Optional[Any]:
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            await self._redis.set(key, json.dumps(value, default=str), ex=ttl or self.default_ttl)
        except Exception:
            pass

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception:
            pass

    async def clear_prefix(self, prefix: str) -> None:
        try:
            async for key in self._redis.scan_iter(f"{prefix}*"):
                await self._redis.delete(key)
        except Exception:
            pass


async def get_cache(settings: Settings) -> CacheProtocol:
    """Redis when enabled and reachable, otherwise in-memory."""
    if settings.redis_enabled:
        cache = RedisCache(settings.redis_url, settings.cache_ttl_seconds)
        try:
            await cache._redis.ping()
            return cache
        except Exception:
            pass
    return InMemoryCache(settings.cache_ttl_seconds)
