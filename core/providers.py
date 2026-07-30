from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from .models import Location

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
USGS_FEED_BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"
NMC_WARNING_URL = "https://www.nmc.cn/rest/findAlarm"

_COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$")
_CJK = re.compile(r"[\u3400-\u9fff]")
_BARE_CJK_PLACE = re.compile(r"^[\u3400-\u9fff]{2,12}$")
_ADMIN_SUFFIX = re.compile(
    r"特别行政区|自治区|自治州|地区|省|市|盟|区|县|旗"
)
_ADMIN_SUFFIXES = (
    "特别行政区",
    "自治区",
    "自治州",
    "地区",
    "省",
    "市",
    "盟",
    "区",
    "县",
    "旗",
)
_FEATURE_RANK = {
    "PPLC": 6,
    "PPLA": 5,
    "PPLA2": 4,
    "PPLA3": 3,
    "PPLA4": 2,
    "PPL": 1,
    "PPLX": 0,
}


def _canonical_place(value: Any) -> str:
    text = re.sub(r"[\s,，、/\\·]+", "", str(value or "").strip()).casefold()
    for prefix in ("中华人民共和国", "中国"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    for suffix in _ADMIN_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            return text[: -len(suffix)]
    return text


def _admin_components(query: str) -> tuple[str, ...]:
    compact = re.sub(r"[\s,，、/\\·]+", "", query)
    for prefix in ("中华人民共和国", "中国"):
        if compact.startswith(prefix):
            compact = compact[len(prefix) :]
            break
    if not _CJK.search(compact):
        return ()
    components: list[str] = []
    start = 0
    for match in _ADMIN_SUFFIX.finditer(compact):
        component = compact[start : match.end()]
        if component:
            components.append(component)
        start = match.end()
    if start < len(compact):
        components.append(compact[start:])
    return tuple(component for component in components if component)


def _geocoding_attempts(query: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    attempts: list[tuple[str, tuple[str, ...]]] = []

    def add(value: str, ancestors: tuple[str, ...] = ()) -> None:
        value = value.strip()
        if value and all(existing[0] != value for existing in attempts):
            attempts.append((value, ancestors))

    add(query)
    components = _admin_components(query)
    if len(components) > 1:
        for index in range(len(components) - 1, -1, -1):
            component = components[index]
            ancestors = components[:index]
            add(component, ancestors)
            canonical = _canonical_place(component)
            if canonical != component.casefold():
                add(canonical, ancestors)
    elif _BARE_CJK_PLACE.fullmatch(query) and not _ADMIN_SUFFIX.search(query):
        add(f"{query}市")
    return tuple(attempts)


def _text_matches(left: str, right: str) -> bool:
    left_value = _canonical_place(left)
    right_value = _canonical_place(right)
    return bool(
        len(left_value) >= 2
        and len(right_value) >= 2
        and (left_value in right_value or right_value in left_value)
    )


def _ancestor_matches(result: dict[str, Any], ancestors: tuple[str, ...]) -> int:
    fields = tuple(
        str(result.get(key) or "")
        for key in ("name", "admin1", "admin2", "admin3", "admin4", "country")
    )
    return sum(
        any(_text_matches(hint, field) for field in fields)
        for hint in ancestors
    )


def _best_geocoding_result(
    results: Any, target: str, ancestors: tuple[str, ...]
) -> dict[str, Any] | None:
    usable = [
        item
        for item in (results or [])
        if isinstance(item, dict)
        and item.get("latitude") is not None
        and item.get("longitude") is not None
    ]
    if ancestors:
        matched = [item for item in usable if _ancestor_matches(item, ancestors)]
        if not matched:
            return None
        usable = matched
    target_value = _canonical_place(target)

    def score(item: dict[str, Any]) -> tuple[int, int, int, int]:
        name = _canonical_place(item.get("name"))
        exact = int(bool(name) and name == target_value)
        contains = int(bool(name) and (name in target_value or target_value in name))
        ancestor_score = _ancestor_matches(item, ancestors)
        feature_score = _FEATURE_RANK.get(str(item.get("feature_code") or ""), -1)
        try:
            population = max(0, int(item.get("population") or 0))
        except (TypeError, ValueError):
            population = 0
        return (
            ancestor_score,
            exact * 2 + contains,
            feature_score,
            population,
        )

    return max(usable, key=score, default=None)


def _should_retry_as_city(query: str, attempted: str, result: dict[str, Any]) -> bool:
    if attempted != query or not _BARE_CJK_PLACE.fullmatch(query):
        return False
    if _ADMIN_SUFFIX.search(query):
        return False
    feature = str(result.get("feature_code") or "")
    try:
        population = int(result.get("population") or 0)
    except (TypeError, ValueError):
        population = 0
    return feature in {"PPL", "PPLX"} and population < 100_000


class ProviderError(RuntimeError):
    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider


class OpenDataProvider:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _json(
        self, provider: str, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError(provider, "请求超时") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(provider, f"HTTP {exc.response.status_code}") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise ProviderError(provider, "网络或响应格式异常") from exc
        if not isinstance(data, dict):
            raise ProviderError(provider, "响应不是 JSON 对象")
        if data.get("error") is True:
            raise ProviderError(provider, str(data.get("reason") or "接口返回错误"))
        return data

    async def resolve_location(self, query: str, language: str = "zh") -> Location:
        query = str(query or "").strip()
        if not query:
            raise ValueError("未设置常驻地点，也没有提供临时查询地点")
        match = _COORDINATES.fullmatch(query)
        if match:
            first, second = float(match.group(1)), float(match.group(2))
            if abs(first) > 90:
                longitude, latitude = first, second
            elif abs(second) > 90:
                latitude, longitude = first, second
            else:
                longitude, latitude = first, second
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("经纬度超出有效范围")
            timezone = await self._timezone_for_coordinates(latitude, longitude)
            return Location(
                query=query,
                name=f"{longitude:.4f},{latitude:.4f}",
                latitude=latitude,
                longitude=longitude,
                timezone=timezone,
            )

        fallback: dict[str, Any] | None = None
        best: dict[str, Any] | None = None
        search_language = "zh" if _CJK.search(query) else language
        for attempted, ancestors in _geocoding_attempts(query):
            data = await self._json(
                "open-meteo-geocoding",
                GEOCODING_URL,
                {
                    "name": attempted,
                    "count": 10,
                    "language": search_language,
                    "format": "json",
                },
            )
            candidate = _best_geocoding_result(
                data.get("results"), attempted, ancestors
            )
            if candidate is None:
                continue
            if _should_retry_as_city(query, attempted, candidate):
                fallback = candidate
                continue
            best = candidate
            break
        best = best or fallback
        if best is None:
            raise ValueError(f"没有找到地点：{query}")
        try:
            return Location(
                query=query,
                name=str(best.get("name") or query),
                latitude=float(best["latitude"]),
                longitude=float(best["longitude"]),
                timezone=str(best.get("timezone") or "UTC"),
                country=str(best.get("country") or ""),
                country_code=str(best.get("country_code") or "").upper(),
                admin1=str(best.get("admin1") or ""),
                admin2=str(best.get("admin2") or ""),
                admin3=str(best.get("admin3") or ""),
                admin4=str(best.get("admin4") or ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("open-meteo-geocoding", "地点响应缺少必要字段") from exc

    async def _timezone_for_coordinates(self, latitude: float, longitude: float) -> str:
        data = await self._json(
            "open-meteo-weather",
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m",
                "timezone": "auto",
                "forecast_days": 1,
            },
        )
        return str(data.get("timezone") or "UTC")

    async def weather(self, location: Location, days: int) -> dict[str, Any]:
        return await self._json(
            "open-meteo-weather",
            FORECAST_URL,
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "timezone": "auto",
                "forecast_days": max(1, min(7, int(days))),
                "current": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "relative_humidity_2m",
                        "precipitation",
                        "rain",
                        "weather_code",
                        "cloud_cover",
                        "visibility",
                        "surface_pressure",
                        "wind_speed_10m",
                        "wind_direction_10m",
                        "wind_gusts_10m",
                    ]
                ),
                "hourly": ",".join(
                    [
                        "temperature_2m",
                        "apparent_temperature",
                        "precipitation_probability",
                        "precipitation",
                        "weather_code",
                        "visibility",
                        "wind_speed_10m",
                        "wind_gusts_10m",
                    ]
                ),
                "daily": ",".join(
                    [
                        "weather_code",
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "apparent_temperature_max",
                        "apparent_temperature_min",
                        "precipitation_sum",
                        "precipitation_probability_max",
                        "wind_speed_10m_max",
                        "wind_gusts_10m_max",
                        "sunrise",
                        "sunset",
                        "daylight_duration",
                    ]
                ),
                "minutely_15": ",".join(
                    ["precipitation", "rain", "snowfall", "weather_code"]
                ),
                "forecast_minutely_15": 24,
            },
        )

    async def air_quality(self, location: Location, days: int = 2) -> dict[str, Any]:
        variables = ",".join(
            [
                "european_aqi",
                "us_aqi",
                "pm10",
                "pm2_5",
                "carbon_monoxide",
                "nitrogen_dioxide",
                "sulphur_dioxide",
                "ozone",
                "uv_index",
                "alder_pollen",
                "birch_pollen",
                "grass_pollen",
                "mugwort_pollen",
                "ragweed_pollen",
            ]
        )
        return await self._json(
            "open-meteo-air-quality",
            AIR_QUALITY_URL,
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "timezone": "auto",
                "forecast_days": max(1, min(3, int(days))),
                "current": variables,
                "hourly": variables,
            },
        )

    async def official_weather_warnings(self, province: str) -> dict[str, Any]:
        async def page(number: int) -> dict[str, Any]:
            return await self._json(
                "nmc-weather-warning",
                NMC_WARNING_URL,
                {
                    "pageNo": number,
                    "pageSize": 100,
                    "signaltype": "",
                    "signallevel": "",
                    "province": province,
                },
            )

        first = await page(1)
        page_info = (first.get("data") or {}).get("page") or {}
        warnings = list(page_info.get("list") or [])
        try:
            total_pages = max(1, min(5, int(page_info.get("totalPage") or 1)))
        except (TypeError, ValueError):
            total_pages = 1
        if total_pages > 1:
            remaining = await asyncio.gather(
                *(page(number) for number in range(2, total_pages + 1))
            )
            for item in remaining:
                page_data = (item.get("data") or {}).get("page") or {}
                warnings.extend(page_data.get("list") or [])
        return {
            "warnings": warnings,
            "province": province,
            "truncated": int(page_info.get("totalPage") or 1) > total_pages,
        }

    async def earthquakes(self, hours: int) -> dict[str, Any]:
        period = "day" if hours <= 24 else "week"
        return await self._json(
            "usgs-earthquake",
            f"{USGS_FEED_BASE}/2.5_{period}.geojson",
        )
