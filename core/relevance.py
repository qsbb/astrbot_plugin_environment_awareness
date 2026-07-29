from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from .models import EarthquakeEvent, Location
from .settings import EnvironmentSettings

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r, lat2_r, lon2_r = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def magnitude_relevance_radius_km(magnitude: float) -> float:
    if magnitude < 3.0:
        return 15.0
    if magnitude < 4.0:
        return 40.0
    if magnitude < 5.0:
        return 100.0
    if magnitude < 6.0:
        return 250.0
    if magnitude < 7.0:
        return 600.0
    if magnitude < 8.0:
        return 1200.0
    return 2500.0


def _iso_from_millis(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000, UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def evaluate_earthquake(
    feature: dict[str, Any],
    location: Location,
    settings: EnvironmentSettings,
) -> EarthquakeEvent | None:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        return None
    try:
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
        depth_km = max(0.0, float(coordinates[2] if len(coordinates) > 2 else 0.0))
        magnitude = float(props.get("mag"))
    except (TypeError, ValueError):
        return None
    if magnitude < settings.earthquake_min_magnitude:
        return None

    distance_km = haversine_km(
        location.latitude, location.longitude, latitude, longitude
    )
    effective_distance_km = math.hypot(distance_km, depth_km)
    natural_radius = magnitude_relevance_radius_km(magnitude)
    radius = min(natural_radius, settings.earthquake_max_distance_km)
    if distance_km <= settings.earthquake_nearby_radius_km:
        radius = max(radius, settings.earthquake_nearby_radius_km)

    tsunami = bool(props.get("tsunami"))
    if tsunami and magnitude >= 6.5:
        radius = max(
            radius,
            min(
                settings.tsunami_relevance_distance_km,
                settings.earthquake_max_distance_km,
            ),
        )

    if distance_km > settings.earthquake_max_distance_km:
        return None
    if effective_distance_km > radius:
        return None

    ratio = effective_distance_km / max(radius, 1.0)
    if ratio <= 0.3:
        relevance = "较强相关"
    elif ratio <= 0.65:
        relevance = "相关"
    else:
        relevance = "边缘相关"

    return EarthquakeEvent(
        event_id=str(feature.get("id") or props.get("code") or "unknown"),
        title=str(props.get("title") or "地震事件"),
        magnitude=magnitude,
        place=str(props.get("place") or "未知地点"),
        occurred_at=_iso_from_millis(props.get("time")),
        updated_at=_iso_from_millis(props.get("updated")),
        latitude=latitude,
        longitude=longitude,
        depth_km=round(depth_km, 1),
        distance_km=distance_km,
        effective_distance_km=effective_distance_km,
        relevance_radius_km=radius,
        relevance=relevance,
        alert_level=str(props.get("alert") or ""),
        tsunami=tsunami,
        source_url=str(props.get("url") or ""),
    )


def evaluate_weather_risks(
    weather: dict[str, Any], settings: EnvironmentSettings
) -> list[dict[str, Any]]:
    if not settings.weather_risk_enabled:
        return []
    daily = weather.get("daily") or {}
    dates = list(daily.get("time") or [])
    precipitation = list(daily.get("precipitation_sum") or [])
    gusts = list(daily.get("wind_gusts_10m_max") or [])
    maximums = list(daily.get("temperature_2m_max") or [])
    minimums = list(daily.get("temperature_2m_min") or [])
    weather_codes = list(daily.get("weather_code") or [])
    signals: list[dict[str, Any]] = []

    def value(items: list[Any], index: int) -> float | None:
        try:
            return float(items[index])
        except (IndexError, TypeError, ValueError):
            return None

    for index, date in enumerate(dates):
        rain = value(precipitation, index)
        gust = value(gusts, index)
        high = value(maximums, index)
        low = value(minimums, index)
        code = value(weather_codes, index)
        if rain is not None and rain >= settings.heavy_rain_mm:
            signals.append(
                {
                    "kind": "heavy_rain_forecast",
                    "date": date,
                    "value": rain,
                    "unit": "mm/day",
                    "summary": "预计日累计降水达到本地感知阈值",
                }
            )
        if gust is not None and gust >= settings.strong_wind_kmh:
            signals.append(
                {
                    "kind": "strong_wind_forecast",
                    "date": date,
                    "value": gust,
                    "unit": "km/h",
                    "summary": "预计阵风达到本地感知阈值",
                }
            )
        if high is not None and high >= settings.extreme_heat_c:
            signals.append(
                {
                    "kind": "extreme_heat_forecast",
                    "date": date,
                    "value": high,
                    "unit": "°C",
                    "summary": "预计最高温达到高温感知阈值",
                }
            )
        if low is not None and low <= settings.extreme_cold_c:
            signals.append(
                {
                    "kind": "extreme_cold_forecast",
                    "date": date,
                    "value": low,
                    "unit": "°C",
                    "summary": "预计最低温达到低温感知阈值",
                }
            )
        if code is not None and int(code) in {95, 96, 99}:
            signals.append(
                {
                    "kind": "thunderstorm_forecast",
                    "date": date,
                    "value": int(code),
                    "unit": "WMO code",
                    "summary": "天气模型出现雷暴或强对流信号",
                }
            )

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for signal in signals:
        unique[(signal["kind"], str(signal["date"]))] = signal
    return list(unique.values())
