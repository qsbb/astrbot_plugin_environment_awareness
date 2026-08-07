from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_environment_awareness.core.cache import AsyncTTLCache
from astrbot_plugin_environment_awareness.core.models import Location
from astrbot_plugin_environment_awareness.core.providers import ProviderError
from astrbot_plugin_environment_awareness.core.service import EnvironmentService


def _weather(*, severe: bool = False):
    return {
        "timezone": "Asia/Shanghai",
        "current": {
            "time": "2026-07-29T12:00",
            "temperature_2m": 31,
            "apparent_temperature": 34,
            "relative_humidity_2m": 70,
            "weather_code": 2,
            "wind_speed_10m": 12,
        },
        "current_units": {"temperature_2m": "°C"},
        "hourly": {
            "time": ["2026-07-29T11:00", "2026-07-29T12:00", "2026-07-29T13:00"],
            "temperature_2m": [30, 31, 32],
        },
        "daily": {
            "time": ["2026-07-29"],
            "precipitation_sum": [60 if severe else 10],
            "wind_gusts_10m_max": [70 if severe else 20],
            "temperature_2m_max": [41 if severe else 32],
            "temperature_2m_min": [22],
            "weather_code": [95 if severe else 2],
            "sunrise": ["2026-07-29T05:16"],
            "sunset": ["2026-07-29T18:55"],
            "daylight_duration": [49184.4],
        },
        "minutely_15": {
            "time": ["2026-07-29T12:00", "2026-07-29T12:15"],
            "precipitation": [0.0, 0.4],
            "rain": [0.0, 0.4],
            "snowfall": [0.0, 0.0],
            "weather_code": [2, 61],
        },
    }


def _air_quality():
    return {
        "current": {
            "time": "2026-07-29T12:00",
            "european_aqi": 42,
            "us_aqi": 55,
            "pm10": 20.0,
            "pm2_5": 12.0,
            "uv_index": 5.0,
            "grass_pollen": None,
        },
        "current_units": {"pm2_5": "μg/m³"},
        "hourly": {
            "time": ["2026-07-29T12:00", "2026-07-29T13:00"],
            "european_aqi": [42, 44],
            "grass_pollen": [None, None],
        },
    }


def _quake(latitude: float, magnitude: float = 4.5):
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "id": f"quake-{latitude}",
        "properties": {
            "mag": magnitude,
            "place": "测试地震",
            "title": "测试地震",
            "time": now_ms,
            "updated": now_ms,
            "url": "https://earthquake.usgs.gov/test",
            "tsunami": 0,
        },
        "geometry": {"coordinates": [120.1551, latitude, 5]},
    }


class FakeProvider:
    def __init__(self):
        self.fail_weather = False
        self.fail_earthquakes = False
        self.fail_warnings = False
        self.weather_data = _weather()
        self.air_quality_data = _air_quality()
        self.features = []
        self.warnings = []
        self.calls = {
            "resolve": 0,
            "weather": 0,
            "air_quality": 0,
            "earthquakes": 0,
            "warnings": 0,
        }

    async def resolve_location(self, query, language="zh"):
        self.calls["resolve"] += 1
        return Location(
            query=query,
            name="杭州市",
            latitude=30.2741,
            longitude=120.1551,
            timezone="Asia/Shanghai",
            country="中国",
            country_code="CN",
            admin1="浙江",
            admin2="杭州市",
        )

    async def weather(self, location, days):
        self.calls["weather"] += 1
        if self.fail_weather:
            raise ProviderError("open-meteo-weather", "offline")
        return self.weather_data

    async def air_quality(self, location, days=2):
        self.calls["air_quality"] += 1
        return self.air_quality_data

    async def earthquakes(self, hours):
        self.calls["earthquakes"] += 1
        if self.fail_earthquakes:
            raise ProviderError("usgs-earthquake", "offline")
        return {"features": self.features}

    async def official_weather_warnings(self, province):
        self.calls["warnings"] += 1
        if self.fail_warnings:
            raise ProviderError("nmc-weather-warning", "offline")
        return {"warnings": self.warnings, "province": province}


def _service(config=None, provider=None):
    return EnvironmentService(config or {}, provider or FakeProvider(), AsyncTTLCache())


def test_missing_default_location_requires_explicit_location():
    async def scenario():
        with pytest.raises(ValueError, match="尚未设置常驻地点"):
            await _service().datetime_snapshot()

    asyncio.run(scenario())


def test_weather_without_location_or_default_fails_before_provider_query():
    async def scenario():
        provider = FakeProvider()
        with pytest.raises(ValueError, match="尚未设置常驻地点"):
            await _service(provider=provider).weather_snapshot()
        assert provider.calls["resolve"] == 0
        assert provider.calls["weather"] == 0

    asyncio.run(scenario())


def test_explicit_location_works_without_configuration():
    async def scenario():
        snapshot = await _service().datetime_snapshot("杭州")
        assert snapshot["location"]["name"] == "杭州市 · 浙江 · 中国"
        assert snapshot["timezone"] == "Asia/Shanghai"

    asyncio.run(scenario())


def test_calendar_distinguishes_public_holiday_and_makeup_workday():
    async def scenario():
        service = _service({"default_location": "杭州"})
        holiday = await service.calendar_snapshot(date_text="2026-02-17")
        makeup = await service.calendar_snapshot(date_text="2026-02-14")
        assert holiday["holiday_name"] == "春节"
        assert holiday["is_day_off"] is True
        assert makeup["day_type"] == "adjusted_workday"
        assert makeup["is_working_day"] is True

    asyncio.run(scenario())


def test_configured_default_location_is_used_and_cached():
    async def scenario():
        provider = FakeProvider()
        service = _service({"default_location": "杭州"}, provider)
        await service.datetime_snapshot()
        await service.datetime_snapshot()
        assert provider.calls["resolve"] == 1

    asyncio.run(scenario())


def test_calendar_awareness_computes_once_per_local_day(monkeypatch):
    from astrbot_plugin_environment_awareness.core import service as service_module

    provider = FakeProvider()
    profile = asyncio.run(provider.resolve_location("杭州"))
    service = _service(
        {
            "default_location": "杭州",
            "_resolved_location_profile": profile.profile_dict(),
        },
        provider,
    )
    original = service_module.build_calendar_snapshot
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_calendar_snapshot", counted)
    service.cached_calendar_awareness()
    service.cached_calendar_awareness()
    assert calls == 1


def test_persisted_location_profile_avoids_geocoding_on_next_start():
    async def scenario():
        provider = FakeProvider()
        profile = await provider.resolve_location("杭州")
        provider.calls["resolve"] = 0
        config = {
            "default_location": "杭州",
            "_resolved_location_profile": profile.profile_dict(),
        }
        snapshot = await _service(config, provider).datetime_snapshot()
        assert snapshot["timezone"] == "Asia/Shanghai"
        assert provider.calls["resolve"] == 0

    asyncio.run(scenario())


def test_hourly_weather_starts_at_current_hour():
    async def scenario():
        snapshot = await _service({"default_location": "杭州"}).weather_snapshot(
            forecast_range="hourly"
        )
        assert snapshot["payload"]["hourly"]["time"] == [
            "2026-07-29T12:00",
            "2026-07-29T13:00",
        ]

    asyncio.run(scenario())


def test_current_weather_contains_sunrise_sunset_and_daylight():
    async def scenario():
        snapshot = await _service({"default_location": "杭州"}).weather_snapshot()
        astronomy = snapshot["payload"]["astronomy"]
        assert astronomy["sunrise"] == "2026-07-29T05:16"
        assert astronomy["sunset"] == "2026-07-29T18:55"
        assert astronomy["daylight_duration"] == 49184.4

    asyncio.run(scenario())


def test_nowcast_summarizes_15_minute_precipitation():
    async def scenario():
        snapshot = await _service({"default_location": "杭州"}).weather_snapshot(
            forecast_range="nowcast"
        )
        summary = snapshot["payload"]["near_term_precipitation"]
        assert summary["rain_expected"] is True
        assert summary["first_precipitation_at"] == "2026-07-29T12:15"
        assert summary["total_precipitation_mm"] == 0.4

    asyncio.run(scenario())


def test_air_quality_removes_unavailable_pollen_series():
    async def scenario():
        snapshot = await _service({"default_location": "杭州"}).air_quality_snapshot(
            forecast_hours=2
        )
        assert snapshot["payload"]["current"]["european_aqi"] == 42
        assert snapshot["availability"]["pollen"] is False
        assert "grass_pollen" not in snapshot["payload"]["hourly"]

    asyncio.run(scenario())


def test_alerts_hide_far_earthquakes_from_model_result():
    async def scenario():
        provider = FakeProvider()
        provider.features = [_quake(40.0, magnitude=5.0)]
        service = _service({"default_location": "杭州"}, provider)
        snapshot = await service.alerts_snapshot()
        assert snapshot["earthquakes"] == []
        assert "suppressed" not in str(snapshot).lower()
        diagnostics = service.diagnostics()
        assert diagnostics["last_relevance_filter"]["suppressed_as_irrelevant"] == 1

    asyncio.run(scenario())


def test_alerts_return_nearby_earthquake():
    async def scenario():
        provider = FakeProvider()
        provider.features = [_quake(30.3, magnitude=4.0)]
        snapshot = await _service(
            {"default_location": "杭州"}, provider
        ).alerts_snapshot()
        assert len(snapshot["earthquakes"]) == 1
        assert snapshot["status"] == "relevant_events"

    asyncio.run(scenario())


def test_local_weather_risk_is_returned_without_unrelated_events():
    async def scenario():
        provider = FakeProvider()
        provider.weather_data = _weather(severe=True)
        snapshot = await _service(
            {"default_location": "杭州"}, provider
        ).alerts_snapshot()
        kinds = {item["kind"] for item in snapshot["weather_risk_signals"]}
        assert "heavy_rain_forecast" in kinds
        assert "strong_wind_forecast" in kinds

    asyncio.run(scenario())


def test_official_warning_is_filtered_to_local_city():
    async def scenario():
        provider = FakeProvider()
        issued = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y/%m/%d %H:%M")
        provider.warnings = [
            {
                "alertid": "huzhou",
                "issuetime": issued,
                "title": "浙江省湖州市气象台发布高温橙色预警信号",
                "url": "/publish/alarm/huzhou.html",
            },
            {
                "alertid": "hangzhou",
                "issuetime": issued,
                "title": "浙江省杭州市气象台发布高温橙色预警信号",
                "url": "/publish/alarm/hangzhou.html",
            },
        ]
        snapshot = await _service(
            {"default_location": "杭州"}, provider
        ).alerts_snapshot()
        assert [item["alert_id"] for item in snapshot["official_weather_warnings"]] == [
            "hangzhou"
        ]
        assert snapshot["official_warning_status"] == "confirmed"

    asyncio.run(scenario())


def test_one_failed_provider_returns_partial_status_not_false_clear():
    async def scenario():
        provider = FakeProvider()
        provider.fail_earthquakes = True
        snapshot = await _service(
            {"default_location": "杭州"}, provider
        ).alerts_snapshot()
        assert snapshot["status"] == "partially_unavailable"
        assert "usgs-earthquake" in snapshot["provider_errors"]

    asyncio.run(scenario())


def test_non_china_location_does_not_query_nmc_or_claim_no_warning():
    class InternationalProvider(FakeProvider):
        async def resolve_location(self, query, language="zh"):
            self.calls["resolve"] += 1
            return Location(
                query=query,
                name="London",
                latitude=51.5072,
                longitude=-0.1276,
                timezone="Europe/London",
                country="United Kingdom",
                country_code="GB",
                admin1="England",
            )

    async def scenario():
        provider = InternationalProvider()
        snapshot = await _service(
            {"default_location": "London"}, provider
        ).alerts_snapshot()
        assert snapshot["official_warning_status"] == "unsupported_region"
        assert provider.calls["warnings"] == 0

    asyncio.run(scenario())


def test_all_failed_providers_return_unable_to_confirm():
    async def scenario():
        provider = FakeProvider()
        provider.fail_weather = True
        provider.fail_earthquakes = True
        provider.fail_warnings = True
        snapshot = await _service(
            {"default_location": "杭州"}, provider
        ).alerts_snapshot()
        assert snapshot["status"] == "unable_to_confirm"
        assert snapshot["confirmed_sources"] == []

    asyncio.run(scenario())


def test_public_location_never_contains_coordinates():
    async def scenario():
        snapshot = await _service({"default_location": "杭州"}).weather_snapshot()
        assert set(snapshot["location"]) == {"name", "timezone"}

    asyncio.run(scenario())


def test_list_locations_explains_temporary_query_mode():
    result = _service().list_locations()
    assert result["configured"] is False
    assert result["temporary_location_supported"] is True


def test_diagnostics_confirm_no_prompt_injection_or_active_push():
    diagnostics = _service({"default_location": "杭州"}).diagnostics()
    assert diagnostics["automatic_prompt_injection"] is True
    assert diagnostics["constant_prompt_injection"] is False
    assert diagnostics["prompt_injection_mode"] == (
        "significant_calendar_once_per_local_day"
    )
    assert diagnostics["active_push"] is False
