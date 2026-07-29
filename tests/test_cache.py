from __future__ import annotations

import asyncio

import pytest
from astrbot_plugin_environment_awareness.core.cache import AsyncTTLCache


def test_cache_hit_avoids_duplicate_load():
    async def scenario():
        cache = AsyncTTLCache()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return {"value": 1}

        first = await cache.get_or_create(
            "key", ttl_seconds=60, stale_seconds=60, factory=load
        )
        second = await cache.get_or_create(
            "key", ttl_seconds=60, stale_seconds=60, factory=load
        )
        assert first.value == second.value == {"value": 1}
        assert calls == 1
        assert cache.stats()["hits"] == 1

    asyncio.run(scenario())


def test_single_flight_merges_concurrent_requests():
    async def scenario():
        cache = AsyncTTLCache()
        calls = 0
        release = asyncio.Event()

        async def load():
            nonlocal calls
            calls += 1
            await release.wait()
            return "ok"

        tasks = [
            asyncio.create_task(
                cache.get_or_create(
                    "same", ttl_seconds=60, stale_seconds=0, factory=load
                )
            )
            for _ in range(3)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks)
        assert [item.value for item in results] == ["ok", "ok", "ok"]
        assert calls == 1

    asyncio.run(scenario())


def test_stale_cache_is_marked_when_refresh_fails():
    async def scenario():
        cache = AsyncTTLCache()

        async def initial():
            return "old"

        await cache.get_or_create(
            "key", ttl_seconds=0, stale_seconds=60, factory=initial
        )

        async def failing():
            raise RuntimeError("offline")

        result = await cache.get_or_create(
            "key", ttl_seconds=60, stale_seconds=60, factory=failing
        )
        assert result.value == "old"
        assert result.stale is True
        assert cache.stats()["stale_hits"] == 1

    asyncio.run(scenario())


def test_refresh_failure_without_stale_cache_is_not_hidden():
    async def scenario():
        cache = AsyncTTLCache()

        async def failing():
            raise RuntimeError("offline")

        with pytest.raises(RuntimeError, match="offline"):
            await cache.get_or_create(
                "key", ttl_seconds=60, stale_seconds=0, factory=failing
            )

    asyncio.run(scenario())


def test_clear_removes_entries():
    async def scenario():
        cache = AsyncTTLCache()
        await cache.get_or_create(
            "key", ttl_seconds=60, stale_seconds=0, factory=lambda: _value("ok")
        )
        await cache.clear()
        assert cache.stats()["entries"] == 0

    async def _value(value):
        return value

    asyncio.run(scenario())
