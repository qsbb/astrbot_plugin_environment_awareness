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


def _boolean(config: Mapping[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _severity(config: Mapping[str, Any], key: str, default: str) -> str:
    value = str(config.get(key, default) or default).strip().lower()
    return value if value in {"low", "medium", "high", "critical"} else default


def _clock(config: Mapping[str, Any], key: str, default: str) -> str:
    value = str(config.get(key, default) or default).strip()
    parts = value.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (IndexError, TypeError, ValueError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


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
    opportunity_cache_enabled: bool
    opportunity_refresh_seconds: int
    opportunity_min_severity: str
    opportunity_european_aqi_threshold: float
    opportunity_us_aqi_threshold: float
    opportunity_uv_threshold: float
    opportunity_temperature_drop_c: float
    care_person_id: str
    care_recipient_umo: str
    proactive_enabled: bool
    proactive_paused: bool
    proactive_min_severity: str
    proactive_quiet_start: str
    proactive_quiet_end: str
    proactive_daily_limit: int

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
            weather_risk_enabled=_boolean(config, "weather_risk_enabled", True),
            heavy_rain_mm=_number(config, "heavy_rain_mm", 50, 10, 500),
            strong_wind_kmh=_number(config, "strong_wind_kmh", 62, 20, 250),
            extreme_heat_c=_number(config, "extreme_heat_c", 40, 25, 60),
            extreme_cold_c=_number(config, "extreme_cold_c", -20, -60, 10),
            calendar_awareness_enabled=_boolean(
                config, "calendar_awareness_enabled", True
            ),
            calendar_country_code=str(config.get("calendar_country_code", "") or "")
            .strip()
            .upper(),
            holiday_subdivision=str(
                config.get("holiday_subdivision", "") or ""
            ).strip(),
            official_weather_warnings_enabled=_boolean(
                config, "official_weather_warnings_enabled", True
            ),
            official_warning_max_age_hours=int(
                _number(config, "official_warning_max_age_hours", 72, 1, 168)
            ),
            official_warning_province=str(
                config.get("official_warning_province", "") or ""
            ).strip(),
            opportunity_cache_enabled=_boolean(
                config, "opportunity_cache_enabled", True
            ),
            opportunity_refresh_seconds=int(
                _number(config, "opportunity_refresh_seconds", 900, 300, 21600)
            ),
            opportunity_min_severity=_severity(
                config, "opportunity_min_severity", "medium"
            ),
            opportunity_european_aqi_threshold=_number(
                config, "opportunity_european_aqi_threshold", 80, 20, 500
            ),
            opportunity_us_aqi_threshold=_number(
                config, "opportunity_us_aqi_threshold", 151, 50, 500
            ),
            opportunity_uv_threshold=_number(
                config, "opportunity_uv_threshold", 8, 3, 20
            ),
            opportunity_temperature_drop_c=_number(
                config, "opportunity_temperature_drop_c", 8, 3, 30
            ),
            care_person_id=str(config.get("care_person_id", "") or "").strip(),
            care_recipient_umo=str(config.get("care_recipient_umo", "") or "").strip(),
            proactive_enabled=_boolean(config, "proactive_enabled", False),
            proactive_paused=_boolean(config, "proactive_paused", False),
            proactive_min_severity=_severity(config, "proactive_min_severity", "high"),
            proactive_quiet_start=_clock(config, "proactive_quiet_start", "23:00"),
            proactive_quiet_end=_clock(config, "proactive_quiet_end", "07:00"),
            proactive_daily_limit=int(
                _number(config, "proactive_daily_limit", 1, 1, 10)
            ),
        )
