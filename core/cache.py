from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .models import CacheResult


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float
    stale_until: float


class AsyncTTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
        self.stale_hits = 0

    async def get_or_create(
        self,
        key: str,
        *,
        ttl_seconds: float,
        stale_seconds: float,
        factory: Callable[[], Awaitable[Any]],
    ) -> CacheResult:
        now = time.monotonic()
        stale_entry: _Entry | None = None
        async with self._lock:
            entry = self._entries.get(key)
            if entry and now < entry.expires_at:
                self.hits += 1
                return CacheResult(entry.value, False, entry.expires_at)
            if entry and now < entry.stale_until:
                stale_entry = entry

            task = self._inflight.get(key)
            if task is None:
                self.misses += 1
                task = asyncio.create_task(
                    factory(), name=f"environment-cache:{key[:40]}"
                )
                self._inflight[key] = task

        try:
            value = await asyncio.shield(task)
        except Exception:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
            if stale_entry is not None:
                self.stale_hits += 1
                return CacheResult(stale_entry.value, True, stale_entry.expires_at)
            raise

        expires_at = time.monotonic() + max(0.0, ttl_seconds)
        async with self._lock:
            self._entries[key] = _Entry(
                value=value,
                expires_at=expires_at,
                stale_until=expires_at + max(0.0, stale_seconds),
            )
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
        return CacheResult(value, False, expires_at)

    async def clear(self) -> None:
        async with self._lock:
            tasks = list(self._inflight.values())
            self._inflight.clear()
            self._entries.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._entries),
            "inflight": len(self._inflight),
            "hits": self.hits,
            "misses": self.misses,
            "stale_hits": self.stale_hits,
        }
