from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _number(
    config: Mapping[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class EnvironmentSettings:
    default_location: str
    language: str
    request_timeout_seconds: float
    weather_current_ttl_seconds: int
    weather_forecast_ttl_seconds: int
    air_quality_ttl_seconds: int
    hazard_ttl_seconds: int
    stale_cache_seconds: int
    forecast_days: int
    earthquake_min_magnitude: float
    earthquake_max_distance_km: float
    earthquake_nearby_radius_km: float
    tsunami_relevance_distance_km: float
    max_hazard_events: int
    weather_risk_enabled: bool
    heavy_rain_mm: float
    strong_wind_kmh: float
    extreme_heat_c: float
    extreme_cold_c: float
    calendar_awareness_enabled: bool
    calendar_country_code: str
    holiday_subdivision: str
    official_weather_warnings_enabled: bool
    official_warning_max_age_hours: int
    official_warning_province: str

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> EnvironmentSettings:
        language = str(config.get("language", "zh") or "zh").strip().lower()
        if language not in {"zh", "en"}:
            language = "zh"
        return cls(
            default_location=str(config.get("default_location", "") or "").strip(),
            language=language,
            request_timeout_seconds=_number(
                config, "request_timeout_seconds", 6.0, 2.0, 20.0
            ),
            weather_current_ttl_seconds=int(
                _number(config, "weather_current_ttl_seconds", 600, 60, 3600)
            ),
            weather_forecast_ttl_seconds=int(
                _number(config, "weather_forecast_ttl_seconds", 1800, 300, 21600)
            ),
            air_quality_ttl_seconds=int(
                _number(config, "air_quality_ttl_seconds", 1800, 300, 21600)
            ),
            hazard_ttl_seconds=int(
                _number(config, "hazard_ttl_seconds", 300, 60, 1800)
            ),
            stale_cache_seconds=int(
                _number(config, "stale_cache_seconds", 3600, 0, 86400)
            ),
            forecast_days=int(_number(config, "forecast_days", 3, 1, 7)),
            earthquake_min_magnitude=_number(
                config, "earthquake_min_magnitude", 2.5, 2.5, 9.9
            ),
            earthquake_max_distance_km=_number(
                config, "earthquake_max_distance_km", 1200, 10, 10000
            ),
            earthquake_nearby_radius_km=_number(
                config, "earthquake_nearby_radius_km", 30, 1, 500
            ),
            tsunami_relevance_distance_km=_number(
                config, "tsunami_relevance_distance_km", 1200, 10, 10000
            ),
            max_hazard_events=int(_number(config, "max_hazard_events", 5, 1, 20)),
            weather_risk_enabled=bool(config.get("weather_risk_enabled", True)),
            heavy_rain_mm=_number(config, "heavy_rain_mm", 50, 10, 500),
            strong_wind_kmh=_number(config, "strong_wind_kmh", 62, 20, 250),
            extreme_heat_c=_number(config, "extreme_heat_c", 40, 25, 60),
            extreme_cold_c=_number(config, "extreme_cold_c", -20, -60, 10),
            calendar_awareness_enabled=bool(
                config.get("calendar_awareness_enabled", True)
            ),
            calendar_country_code=str(
                config.get("calendar_country_code", "") or ""
            )
            .strip()
            .upper(),
            holiday_subdivision=str(
                config.get("holiday_subdivision", "") or ""
            ).strip(),
            official_weather_warnings_enabled=bool(
                config.get("official_weather_warnings_enabled", True)
            ),
            official_warning_max_age_hours=int(
                _number(config, "official_warning_max_age_hours", 72, 1, 168)
            ),
            official_warning_province=str(
                config.get("official_warning_province", "") or ""
            ).strip(),
        )
