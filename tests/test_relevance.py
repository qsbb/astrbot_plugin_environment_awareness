from __future__ import annotations

from datetime import UTC, datetime

import pytest
from astrbot_plugin_environment_awareness.core.models import Location
from astrbot_plugin_environment_awareness.core.relevance import (
    evaluate_earthquake,
    evaluate_weather_risks,
    haversine_km,
    magnitude_relevance_radius_km,
)
from astrbot_plugin_environment_awareness.core.settings import EnvironmentSettings


def _settings(**overrides) -> EnvironmentSettings:
    return EnvironmentSettings.from_mapping(overrides)


def _location() -> Location:
    return Location("home", "杭州", 30.2741, 120.1551, "Asia/Shanghai", "中国")


def _feature(
    *,
    latitude: float = 30.2741,
    longitude: float = 120.1551,
    magnitude: float = 4.0,
    depth: float = 10,
    tsunami: int = 0,
):
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "id": "test-event",
        "properties": {
            "mag": magnitude,
            "place": "测试震中",
            "title": "M test earthquake",
            "time": now_ms,
            "updated": now_ms,
            "url": "https://earthquake.usgs.gov/test",
            "tsunami": tsunami,
        },
        "geometry": {
            "type": "Point",
            "coordinates": [longitude, latitude, depth],
        },
    }


def test_haversine_one_degree_latitude_is_about_111_km():
    assert haversine_km(30, 120, 31, 120) == pytest.approx(111.2, rel=0.01)


@pytest.mark.parametrize(
    ("magnitude", "radius"),
    [
        (2.5, 15),
        (3.5, 40),
        (4.5, 100),
        (5.5, 250),
        (6.5, 600),
        (7.5, 1200),
        (8.1, 2500),
    ],
)
def test_magnitude_radius_is_deliberately_conservative(magnitude, radius):
    assert magnitude_relevance_radius_km(magnitude) == radius


def test_nearby_earthquake_is_returned():
    event = evaluate_earthquake(_feature(magnitude=4.2), _location(), _settings())
    assert event is not None
    assert event.relevance == "较强相关"


def test_far_moderate_earthquake_is_suppressed():
    event = evaluate_earthquake(
        _feature(latitude=33.0, longitude=120.1551, magnitude=5.0),
        _location(),
        _settings(),
    )
    assert event is None


def test_deep_event_with_little_surface_relevance_is_suppressed():
    event = evaluate_earthquake(
        _feature(magnitude=4.5, depth=180), _location(), _settings()
    )
    assert event is None


def test_below_minimum_magnitude_is_suppressed_even_when_nearby():
    event = evaluate_earthquake(
        _feature(magnitude=2.0, depth=1), _location(), _settings()
    )
    assert event is None


def test_nearby_radius_keeps_small_local_event():
    event = evaluate_earthquake(
        _feature(latitude=30.4, magnitude=2.7, depth=2),
        _location(),
        _settings(earthquake_nearby_radius_km=30),
    )
    assert event is not None


def test_absolute_distance_cannot_be_bypassed_by_large_magnitude():
    event = evaluate_earthquake(
        _feature(latitude=42.0, magnitude=8.5, tsunami=1),
        _location(),
        _settings(earthquake_max_distance_km=500),
    )
    assert event is None


def test_public_event_does_not_expose_coordinates():
    event = evaluate_earthquake(_feature(), _location(), _settings())
    assert event is not None
    public = event.public_dict()
    assert "latitude" not in public
    assert "longitude" not in public
    assert "启发式" in public["impact_assessment"]


def test_weather_risks_ignore_below_threshold_values():
    weather = {
        "daily": {
            "time": ["2026-07-29"],
            "precipitation_sum": [20],
            "wind_gusts_10m_max": [30],
            "temperature_2m_max": [32],
            "temperature_2m_min": [20],
            "weather_code": [2],
        }
    }
    assert evaluate_weather_risks(weather, _settings()) == []


def test_weather_risks_return_only_local_threshold_crossings():
    weather = {
        "daily": {
            "time": ["2026-07-29"],
            "precipitation_sum": [60],
            "wind_gusts_10m_max": [70],
            "temperature_2m_max": [41],
            "temperature_2m_min": [-21],
            "weather_code": [95],
        }
    }
    kinds = {item["kind"] for item in evaluate_weather_risks(weather, _settings())}
    assert kinds == {
        "heavy_rain_forecast",
        "strong_wind_forecast",
        "extreme_heat_forecast",
        "extreme_cold_forecast",
        "thunderstorm_forecast",
    }


def test_weather_risks_can_be_disabled():
    weather = {"daily": {"time": ["2026-07-29"], "weather_code": [99]}}
    assert evaluate_weather_risks(weather, _settings(weather_risk_enabled=False)) == []
