from __future__ import annotations

import asyncio

import httpx
import pytest
from astrbot_plugin_environment_awareness.core.providers import (
    OpenDataProvider,
    ProviderError,
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_named_location_uses_first_geocoding_match():
    async def scenario():
        def handler(request: httpx.Request):
            assert request.url.params["name"] == "杭州"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "杭州市",
                            "latitude": 30.2741,
                            "longitude": 120.1551,
                            "timezone": "Asia/Shanghai",
                            "country": "中国",
                            "country_code": "CN",
                            "admin1": "浙江",
                            "admin2": "杭州市",
                        }
                    ]
                },
            )

        async with _client(handler) as client:
            location = await OpenDataProvider(client).resolve_location("杭州")
        assert location.name == "杭州市"
        assert location.timezone == "Asia/Shanghai"
        assert location.country_code == "CN"
        assert location.admin2 == "杭州市"
        assert location.public_dict() == {
            "name": "杭州市 · 浙江 · 中国",
            "timezone": "Asia/Shanghai",
        }

    asyncio.run(scenario())


def test_coordinates_use_lon_lat_order_and_lookup_timezone():
    async def scenario():
        def handler(request: httpx.Request):
            assert float(request.url.params["latitude"]) == pytest.approx(30.27)
            assert float(request.url.params["longitude"]) == pytest.approx(120.15)
            return httpx.Response(200, json={"timezone": "Asia/Shanghai"})

        async with _client(handler) as client:
            location = await OpenDataProvider(client).resolve_location("120.15,30.27")
        assert location.latitude == pytest.approx(30.27)
        assert location.longitude == pytest.approx(120.15)
        assert location.timezone == "Asia/Shanghai"

    asyncio.run(scenario())


def test_invalid_coordinates_are_rejected():
    async def scenario():
        async with _client(lambda request: httpx.Response(500)) as client:
            with pytest.raises(ValueError, match="超出有效范围"):
                await OpenDataProvider(client).resolve_location("200,95")

    asyncio.run(scenario())


def test_missing_geocoding_result_is_explicit():
    async def scenario():
        async with _client(
            lambda request: httpx.Response(200, json={"results": []})
        ) as client:
            with pytest.raises(ValueError, match="没有找到地点"):
                await OpenDataProvider(client).resolve_location("不存在地点")

    asyncio.run(scenario())


def test_weather_request_contains_current_hourly_and_daily_fields():
    async def scenario():
        captured = {}

        def handler(request: httpx.Request):
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"timezone": "Asia/Shanghai"})

        async with _client(handler) as client:
            provider = OpenDataProvider(client)
            location = await provider.resolve_location("120.15,30.27")
            await provider.weather(location, 3)
        assert "temperature_2m" in captured["current"]
        assert "precipitation_probability" in captured["hourly"]
        assert "wind_gusts_10m_max" in captured["daily"]
        assert "sunrise" in captured["daily"]
        assert "precipitation" in captured["minutely_15"]
        assert captured["forecast_minutely_15"] == "24"
        assert captured["forecast_days"] == "3"

    asyncio.run(scenario())


def test_air_quality_request_contains_aqi_uv_and_pollen_fields():
    async def scenario():
        captured = {}

        def handler(request: httpx.Request):
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"current": {}})

        async with _client(handler) as client:
            location = await OpenDataProvider(client).resolve_location(
                "120.15,30.27"
            )
            await OpenDataProvider(client).air_quality(location)
        assert "european_aqi" in captured["current"]
        assert "uv_index" in captured["current"]
        assert "grass_pollen" in captured["current"]
        assert captured["forecast_days"] == "2"

    asyncio.run(scenario())


def test_nmc_warning_provider_combines_pages():
    async def scenario():
        seen_pages = []

        def handler(request: httpx.Request):
            page = int(request.url.params["pageNo"])
            seen_pages.append(page)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "page": {
                            "totalPage": 2,
                            "list": [{"alertid": f"warning-{page}"}],
                        }
                    },
                },
            )

        async with _client(handler) as client:
            result = await OpenDataProvider(client).official_weather_warnings(
                "浙江省"
            )
        assert sorted(seen_pages) == [1, 2]
        assert [item["alertid"] for item in result["warnings"]] == [
            "warning-1",
            "warning-2",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("hours", "suffix"), [(24, "2.5_day.geojson"), (48, "2.5_week.geojson")]
)
def test_earthquake_feed_period_follows_requested_window(hours, suffix):
    async def scenario():
        seen = ""

        def handler(request: httpx.Request):
            nonlocal seen
            seen = str(request.url)
            return httpx.Response(200, json={"features": []})

        async with _client(handler) as client:
            await OpenDataProvider(client).earthquakes(hours)
        assert seen.endswith(suffix)

    asyncio.run(scenario())


def test_provider_error_response_is_not_treated_as_valid_data():
    async def scenario():
        async with _client(
            lambda request: httpx.Response(
                200, json={"error": True, "reason": "bad request"}
            )
        ) as client:
            with pytest.raises(ProviderError, match="bad request"):
                await OpenDataProvider(client).resolve_location("杭州")

    asyncio.run(scenario())
