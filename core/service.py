from __future__ import annotations

import asyncio
from collections.abc import Mapping, MutableMapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .cache import AsyncTTLCache
from .calendar import build_calendar_snapshot, significant_calendar_fragment
from .models import CacheResult, EarthquakeEvent, Location
from .providers import (
    OpenDataProvider,
    ProviderError,
)
from .relevance import evaluate_earthquake, evaluate_weather_risks
from .settings import EnvironmentSettings
from .warnings import filter_nmc_warnings, warning_region


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_after(seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=max(0, seconds))).isoformat()


def _slice_series(series: dict[str, Any], start: int, count: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, raw in series.items():
        if isinstance(raw, list):
            result[key] = raw[start : start + count]
    return result


def _hourly_start_index(hourly: dict[str, Any], current_time: str) -> int:
    times = hourly.get("time") or []
    if not isinstance(times, list):
        return 0
    for index, value in enumerate(times):
        if str(value) >= current_time:
            return index
    return 0


def _drop_empty_series(series: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, raw in series.items():
        if not isinstance(raw, list):
            continue
        if key == "time" or any(value is not None for value in raw):
            result[key] = raw
    return result


def _nowcast_summary(series: dict[str, Any]) -> dict[str, Any]:
    times = list(series.get("time") or [])
    precipitation = list(series.get("precipitation") or [])
    values: list[float] = []
    first_at = ""
    for index, raw in enumerate(precipitation):
        try:
            value = max(0.0, float(raw))
        except (TypeError, ValueError):
            value = 0.0
        values.append(value)
        if not first_at and value >= 0.1 and index < len(times):
            first_at = str(times[index])
    return {
        "interval_minutes": 15,
        "interval_count": len(times),
        "rain_expected": any(value >= 0.1 for value in values),
        "first_precipitation_at": first_at or None,
        "total_precipitation_mm": round(sum(values), 2),
        "max_interval_precipitation_mm": round(max(values, default=0.0), 2),
        "window_start": str(times[0]) if times else None,
        "window_end": str(times[-1]) if times else None,
        "note": "15 分钟模型数据，不等同于雷达临近预警",
    }


class EnvironmentService:
    def __init__(
        self,
        config: Mapping[str, Any],
        provider: OpenDataProvider,
        cache: AsyncTTLCache,
    ) -> None:
        self._config = config
        self._provider = provider
        self._cache = cache
        self._last_provider_success: dict[str, str] = {}
        self._last_provider_error: dict[str, str] = {}
        self._last_filter_stats = {
            "evaluated": 0,
            "suppressed_as_irrelevant": 0,
            "returned": 0,
        }
        self._last_warning_filter_stats = {
            "evaluated": 0,
            "suppressed_as_stale": 0,
            "suppressed_as_irrelevant": 0,
            "suppressed_as_malformed": 0,
            "returned": 0,
        }

    def settings(self) -> EnvironmentSettings:
        return EnvironmentSettings.from_mapping(self._config)

    async def resolve_location(self, location: str = "") -> Location:
        settings = self.settings()
        query = str(location or "").strip() or settings.default_location
        if not query:
            raise ValueError(
                "尚未设置常驻地点。请在“境”页面填写城市，或在本次查询中直接提供地点。"
            )
        if not location and query == settings.default_location:
            profile = Location.from_profile(
                self._config.get("_resolved_location_profile")
            )
            if profile is not None and profile.query.casefold() == query.casefold():
                return profile
        key = f"location:{settings.language}:{query.casefold()}"
        try:
            result = await self._cache.get_or_create(
                key,
                ttl_seconds=30 * 86400,
                stale_seconds=30 * 86400,
                factory=lambda: self._provider.resolve_location(
                    query, settings.language
                ),
            )
        except ProviderError as exc:
            self._mark_error(exc.provider, str(exc))
            raise
        if result.stale:
            self._mark_error("open-meteo-geocoding", "刷新失败，使用旧缓存")
        else:
            self._mark_success("open-meteo-geocoding")
        if query == settings.default_location:
            self.remember_default_location(query, result.value)
        return result.value

    def remember_default_location(self, query: str, location: Location) -> None:
        if not isinstance(self._config, MutableMapping):
            return
        profile = location.profile_dict()
        profile["query"] = str(query or location.query).strip()
        self._config["_resolved_location_profile"] = profile
        save = getattr(self._config, "save_config", None)
        if callable(save):
            save()

    def cached_default_location(self) -> Location | None:
        settings = self.settings()
        if not settings.default_location:
            return None
        profile = Location.from_profile(self._config.get("_resolved_location_profile"))
        if profile is None:
            return None
        if profile.query.casefold() != settings.default_location.casefold():
            return None
        return profile

    async def datetime_snapshot(self, location: str = "") -> dict[str, Any]:
        resolved = await self.resolve_location(location)
        timezone_name = resolved.timezone or "UTC"
        timezone_fallback = False
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone = UTC
            timezone_name = "UTC"
            timezone_fallback = True
        now = datetime.now(timezone)
        return {
            "contract": "environment.snapshot",
            "version": 1,
            "kind": "datetime",
            "location": resolved.public_dict(),
            "local_datetime": now.isoformat(),
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "timezone": timezone_name,
            "timezone_fallback": timezone_fallback,
            "source": {"provider": "IANA zoneinfo", "network": False},
            "fetched_at": _utc_now().isoformat(),
            "stale": False,
        }

    async def calendar_snapshot(
        self, location: str = "", date_text: str = ""
    ) -> dict[str, Any]:
        settings = self.settings()
        resolved = await self.resolve_location(location)
        if date_text:
            try:
                target = date.fromisoformat(str(date_text).strip())
            except ValueError as exc:
                raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc
        else:
            try:
                target = datetime.now(ZoneInfo(resolved.timezone or "UTC")).date()
            except ZoneInfoNotFoundError:
                target = _utc_now().date()
        return await asyncio.to_thread(
            build_calendar_snapshot,
            resolved,
            target,
            language=settings.language,
            country_code_override=settings.calendar_country_code,
            subdivision=settings.holiday_subdivision,
        )

    def cached_calendar_awareness(self) -> tuple[str, str] | None:
        settings = self.settings()
        if not settings.calendar_awareness_enabled:
            return None
        resolved = self.cached_default_location()
        if resolved is None:
            return None
        try:
            target = datetime.now(ZoneInfo(resolved.timezone or "UTC")).date()
            snapshot = build_calendar_snapshot(
                resolved,
                target,
                language=settings.language,
                country_code_override=settings.calendar_country_code,
                subdivision=settings.holiday_subdivision,
            )
        except (ValueError, ZoneInfoNotFoundError):
            return None
        fragment = significant_calendar_fragment(snapshot)
        if not fragment:
            return None
        return target.isoformat(), fragment

    async def _weather_result(
        self, location: Location, days: int, ttl_seconds: int
    ) -> CacheResult:
        settings = self.settings()
        key = (
            f"weather:{location.latitude:.4f}:{location.longitude:.4f}:"
            f"{max(1, min(7, days))}"
        )
        try:
            result = await self._cache.get_or_create(
                key,
                ttl_seconds=ttl_seconds,
                stale_seconds=settings.stale_cache_seconds,
                factory=lambda: self._provider.weather(location, days),
            )
        except ProviderError as exc:
            self._mark_error(exc.provider, str(exc))
            raise
        if result.stale:
            self._mark_error("open-meteo-weather", "刷新失败，使用旧缓存")
        else:
            self._mark_success("open-meteo-weather")
        return result

    async def weather_snapshot(
        self,
        location: str = "",
        forecast_range: str = "current",
        days: int | None = None,
    ) -> dict[str, Any]:
        settings = self.settings()
        forecast_range = str(forecast_range or "current").strip().lower()
        if forecast_range not in {"current", "nowcast", "hourly", "daily"}:
            raise ValueError("forecast_range 只能是 current、nowcast、hourly 或 daily")
        requested_days = max(1, min(7, int(days or settings.forecast_days)))
        resolved = await self.resolve_location(location)
        ttl = (
            settings.weather_current_ttl_seconds
            if forecast_range == "current"
            else settings.weather_forecast_ttl_seconds
        )
        result = await self._weather_result(resolved, requested_days, ttl)
        raw = result.value
        current = raw.get("current") or {}
        current_time = str(current.get("time") or "")
        daily = raw.get("daily") or {}
        astronomy = {
            key: (values[0] if isinstance(values, list) and values else None)
            for key, values in daily.items()
            if key in {"time", "sunrise", "sunset", "daylight_duration"}
        }
        payload: dict[str, Any] = {
            "current": current,
            "astronomy": astronomy,
        }
        if forecast_range == "nowcast":
            minutely = raw.get("minutely_15") or {}
            start = _hourly_start_index(minutely, current_time)
            sliced = _slice_series(minutely, start, 24)
            payload["minutely_15"] = sliced
            payload["near_term_precipitation"] = _nowcast_summary(sliced)
        elif forecast_range == "hourly":
            hourly = raw.get("hourly") or {}
            start = _hourly_start_index(hourly, current_time)
            payload["hourly"] = _slice_series(hourly, start, 24)
        elif forecast_range == "daily":
            payload["daily"] = _slice_series(raw.get("daily") or {}, 0, requested_days)

        return {
            "contract": "environment.snapshot",
            "version": 1,
            "kind": "weather",
            "range": forecast_range,
            "location": resolved.public_dict(),
            "source": {
                "provider": "Open-Meteo",
                "url": "https://open-meteo.com/en/docs",
                "data_kind": "model forecast",
            },
            "observed_at": current_time,
            "fetched_at": _utc_now().isoformat(),
            "expires_at": None if result.stale else _iso_after(ttl),
            "stale": result.stale,
            "units": {
                "current": raw.get("current_units") or {},
                "hourly": raw.get("hourly_units") or {},
                "daily": raw.get("daily_units") or {},
                "minutely_15": raw.get("minutely_15_units") or {},
            },
            "payload": payload,
        }

    async def _air_quality_result(self, location: Location) -> CacheResult:
        settings = self.settings()
        key = f"air-quality:{location.latitude:.4f}:{location.longitude:.4f}"
        try:
            result = await self._cache.get_or_create(
                key,
                ttl_seconds=settings.air_quality_ttl_seconds,
                stale_seconds=settings.stale_cache_seconds,
                factory=lambda: self._provider.air_quality(location, 2),
            )
        except ProviderError as exc:
            self._mark_error(exc.provider, str(exc))
            raise
        if result.stale:
            self._mark_error("open-meteo-air-quality", "刷新失败，使用旧缓存")
        else:
            self._mark_success("open-meteo-air-quality")
        return result

    async def air_quality_snapshot(
        self, location: str = "", forecast_hours: int = 0
    ) -> dict[str, Any]:
        settings = self.settings()
        resolved = await self.resolve_location(location)
        hours = max(0, min(24, int(forecast_hours or 0)))
        result = await self._air_quality_result(resolved)
        raw = result.value
        current_raw = raw.get("current") or {}
        unavailable = sorted(
            key
            for key, value in current_raw.items()
            if key not in {"time", "interval"} and value is None
        )
        current = {
            key: value for key, value in current_raw.items() if value is not None
        }
        payload: dict[str, Any] = {"current": current}
        if hours:
            hourly = raw.get("hourly") or {}
            start = _hourly_start_index(hourly, str(current.get("time") or ""))
            payload["hourly"] = _drop_empty_series(
                _slice_series(hourly, start, hours)
            )
        pollen_keys = {
            "alder_pollen",
            "birch_pollen",
            "grass_pollen",
            "mugwort_pollen",
            "ragweed_pollen",
        }
        return {
            "contract": "environment.snapshot",
            "version": 1,
            "kind": "air_quality",
            "location": resolved.public_dict(),
            "forecast_hours": hours,
            "source": {
                "provider": "Open-Meteo Air Quality",
                "url": "https://open-meteo.com/en/docs/air-quality-api",
                "data_kind": "modelled air quality forecast, not official monitoring",
            },
            "observed_at": current.get("time"),
            "fetched_at": _utc_now().isoformat(),
            "expires_at": None
            if result.stale
            else _iso_after(settings.air_quality_ttl_seconds),
            "stale": result.stale,
            "units": {
                "current": raw.get("current_units") or {},
                "hourly": raw.get("hourly_units") or {},
            },
            "availability": {
                "pollen": any(
                    current.get(key) is not None for key in pollen_keys
                ),
                "unavailable_current_variables": unavailable,
            },
            "payload": payload,
            "disclaimer": (
                "空气质量、紫外线和花粉为模型数据，"
                "不替代当地监测与医疗建议。"
            ),
        }

    async def _earthquake_result(self, hours: int) -> CacheResult:
        settings = self.settings()
        period = "day" if hours <= 24 else "week"
        try:
            result = await self._cache.get_or_create(
                f"earthquakes:2.5:{period}",
                ttl_seconds=settings.hazard_ttl_seconds,
                stale_seconds=settings.stale_cache_seconds,
                factory=lambda: self._provider.earthquakes(hours),
            )
        except ProviderError as exc:
            self._mark_error(exc.provider, str(exc))
            raise
        if result.stale:
            self._mark_error("usgs-earthquake", "刷新失败，使用旧缓存")
        else:
            self._mark_success("usgs-earthquake")
        return result

    async def _official_warning_result(
        self, province: str
    ) -> CacheResult:
        settings = self.settings()
        try:
            result = await self._cache.get_or_create(
                f"nmc-warning:{province}",
                ttl_seconds=settings.hazard_ttl_seconds,
                stale_seconds=settings.stale_cache_seconds,
                factory=lambda: self._provider.official_weather_warnings(province),
            )
        except ProviderError as exc:
            self._mark_error(exc.provider, str(exc))
            raise
        if result.stale:
            self._mark_error("nmc-weather-warning", "刷新失败，使用旧缓存")
        else:
            self._mark_success("nmc-weather-warning")
        return result

    async def alerts_snapshot(
        self, location: str = "", hours: int = 24
    ) -> dict[str, Any]:
        settings = self.settings()
        resolved = await self.resolve_location(location)
        hours = max(1, min(168, int(hours or 24)))

        weather_task = self._weather_result(
            resolved, settings.forecast_days, settings.weather_forecast_ttl_seconds
        )
        earthquake_task = self._earthquake_result(hours)
        province = ""
        if settings.official_weather_warnings_enabled:
            province = warning_region(resolved, settings.official_warning_province)
        warning_task = self._official_warning_result(province) if province else None
        tasks = [weather_task, earthquake_task]
        if warning_task is not None:
            tasks.append(warning_task)
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        weather_result, earthquake_result = gathered[:2]
        warning_result = gathered[2] if len(gathered) > 2 else None

        errors: dict[str, str] = {}
        weather_signals: list[dict[str, Any]] = []
        earthquakes: list[EarthquakeEvent] = []
        official_warnings: list[dict[str, Any]] = []
        confirmed_sources: list[str] = []
        stale = False

        if isinstance(weather_result, Exception):
            errors["open-meteo-weather"] = self._safe_provider_error(weather_result)
        else:
            confirmed_sources.append("open-meteo-weather")
            stale = stale or weather_result.stale
            weather_signals = evaluate_weather_risks(weather_result.value, settings)

        evaluated = 0
        suppressed = 0
        if isinstance(earthquake_result, Exception):
            errors["usgs-earthquake"] = self._safe_provider_error(earthquake_result)
        else:
            confirmed_sources.append("usgs-earthquake")
            stale = stale or earthquake_result.stale
            cutoff = _utc_now() - timedelta(hours=hours)
            for feature in earthquake_result.value.get("features") or []:
                if not isinstance(feature, dict):
                    continue
                props = feature.get("properties") or {}
                try:
                    occurred = datetime.fromtimestamp(
                        float(props.get("time")) / 1000, UTC
                    )
                except (TypeError, ValueError, OSError):
                    continue
                if occurred < cutoff:
                    continue
                evaluated += 1
                event = evaluate_earthquake(feature, resolved, settings)
                if event is None:
                    suppressed += 1
                    continue
                earthquakes.append(event)

        if not settings.official_weather_warnings_enabled:
            official_warning_status = "disabled"
        elif not province:
            official_warning_status = "unsupported_region"
        elif isinstance(warning_result, Exception):
            official_warning_status = "unavailable"
            errors["nmc-weather-warning"] = self._safe_provider_error(warning_result)
        elif warning_result is not None:
            official_warning_status = "confirmed"
            confirmed_sources.append("nmc-weather-warning")
            stale = stale or warning_result.stale
            official_warnings, self._last_warning_filter_stats = filter_nmc_warnings(
                warning_result.value,
                resolved,
                province,
                max_age_hours=settings.official_warning_max_age_hours,
            )
        else:
            official_warning_status = "unsupported_region"

        earthquakes.sort(
            key=lambda event: (
                event.effective_distance_km / max(event.relevance_radius_km, 1),
                -event.magnitude,
                event.occurred_at,
            )
        )
        earthquakes = earthquakes[: settings.max_hazard_events]
        weather_signals = weather_signals[: settings.max_hazard_events]
        official_warnings = official_warnings[: settings.max_hazard_events]
        self._last_filter_stats = {
            "evaluated": evaluated,
            "suppressed_as_irrelevant": suppressed,
            "returned": len(earthquakes),
        }

        has_events = bool(earthquakes or weather_signals or official_warnings)
        if not confirmed_sources:
            status = "unable_to_confirm"
        elif errors:
            status = (
                "relevant_events_partial" if has_events else "partially_unavailable"
            )
        elif has_events:
            status = "relevant_events"
        else:
            status = "no_relevant_events"

        return {
            "contract": "environment.alert",
            "version": 1,
            "status": status,
            "location": resolved.public_dict(),
            "window_hours": hours,
            "filtering": {
                "local_relevance_required": True,
                "max_distance_km": settings.earthquake_max_distance_km,
                "min_magnitude": settings.earthquake_min_magnitude,
                "note": "只返回通过距离、震级和影响范围筛选的事件",
            },
            "weather_risk_signals": weather_signals,
            "official_weather_warnings": official_warnings,
            "official_warning_status": official_warning_status,
            "official_warning_region": province or None,
            "earthquakes": [event.public_dict() for event in earthquakes],
            "confirmed_sources": confirmed_sources,
            "provider_errors": errors,
            "stale": stale,
            "fetched_at": _utc_now().isoformat(),
            "sources": [
                {
                    "provider": "Open-Meteo",
                    "url": "https://open-meteo.com/en/docs",
                    "kind": "model forecast, not an official warning",
                },
                {
                    "provider": "中央气象台",
                    "url": "https://www.nmc.cn/publish/alarm.html",
                    "kind": "official active weather warning listing",
                    "coverage": "中国大陆；按省查询后再按城市/区县过滤",
                },
                {
                    "provider": "USGS",
                    "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php",
                    "kind": "official earthquake feed",
                },
            ],
            "disclaimer": "本结果用于环境感知，不能替代当地官方预警和应急建议。",
        }

    def list_locations(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "default_location": settings.default_location or None,
            "configured": bool(settings.default_location),
            "temporary_location_supported": True,
            "setup": "在插件页面填写一个常驻城市，或在工具调用时直接提供地点。",
        }

    def diagnostics(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "ready": True,
            "default_location": settings.default_location,
            "zero_key_providers": [
                "Open-Meteo",
                "中央气象台",
                "USGS",
                "python-holidays",
            ],
            "provider_last_success": dict(self._last_provider_success),
            "provider_last_error": dict(self._last_provider_error),
            "cache": self._cache.stats(),
            "last_relevance_filter": dict(self._last_filter_stats),
            "last_official_warning_filter": dict(
                self._last_warning_filter_stats
            ),
            "filters": {
                "earthquake_min_magnitude": settings.earthquake_min_magnitude,
                "earthquake_max_distance_km": settings.earthquake_max_distance_km,
                "earthquake_nearby_radius_km": settings.earthquake_nearby_radius_km,
                "weather_risk_enabled": settings.weather_risk_enabled,
                "official_weather_warnings_enabled": (
                    settings.official_weather_warnings_enabled
                ),
                "official_warning_max_age_hours": (
                    settings.official_warning_max_age_hours
                ),
            },
            "calendar_awareness_enabled": settings.calendar_awareness_enabled,
            "automatic_prompt_injection": settings.calendar_awareness_enabled,
            "prompt_injection_mode": "significant_calendar_once_per_local_day"
            if settings.calendar_awareness_enabled
            else "off",
            "constant_prompt_injection": False,
            "active_push": False,
        }

    def _mark_success(self, provider: str) -> None:
        self._last_provider_success[provider] = _utc_now().isoformat()
        self._last_provider_error.pop(provider, None)

    def _mark_error(self, provider: str, error: str) -> None:
        self._last_provider_error[provider] = error[:160]

    @staticmethod
    def _safe_provider_error(error: Exception) -> str:
        if isinstance(error, ProviderError):
            return str(error)
        return "数据源暂时不可用"
