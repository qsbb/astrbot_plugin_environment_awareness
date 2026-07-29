from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_SOURCE_PRIORITY = {
    "official_weather_warning": 6,
    "earthquake": 5,
    "heavy_rain_forecast": 4,
    "strong_wind_forecast": 4,
    "extreme_heat_forecast": 4,
    "extreme_cold_forecast": 4,
    "thunderstorm_forecast": 4,
    "high_air_quality_index": 3,
    "high_uv_index": 2,
    "strong_temperature_drop": 1,
}
_WARNING_SEVERITY = {
    "蓝色": "low",
    "黄色": "medium",
    "橙色": "high",
    "红色": "critical",
}


def severity_at_least(value: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(str(value), -1) >= SEVERITY_ORDER.get(
        str(minimum), SEVERITY_ORDER["medium"]
    )


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _max_severity(*values: str | None) -> str:
    usable = [value for value in values if value in SEVERITY_ORDER]
    return max(usable, key=lambda value: SEVERITY_ORDER[value], default="low")


def _us_aqi_severity(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 301:
        return "critical"
    if value >= 151:
        return "high"
    if value >= 101:
        return "medium"
    return "low"


def _eu_aqi_severity(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 100:
        return "critical"
    if value >= 80:
        return "high"
    if value >= 60:
        return "medium"
    return "low"


def _uv_severity(value: float) -> str:
    if value >= 11:
        return "critical"
    if value >= 8:
        return "high"
    if value >= 6:
        return "medium"
    return "low"


def _drop_severity(value: float) -> str:
    if value >= 16:
        return "critical"
    if value >= 12:
        return "high"
    if value >= 8:
        return "medium"
    return "low"


def _key(kind: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part or "").strip() for part in (kind, *parts))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def _candidate(
    *,
    kind: str,
    severity: str,
    facts: dict[str, Any],
    location: dict[str, Any],
    observed_at: Any,
    fetched_at: Any,
    stale: bool,
    source_kind: str,
    source_provider: str,
    dedupe_parts: Iterable[Any],
    revision_parts: Iterable[Any],
    severity_basis: Iterable[str] = (),
) -> dict[str, Any]:
    location_name = str(location.get("name") or "当前地点")[:160]
    timezone_name = str(location.get("timezone") or "UTC")[:80]
    return {
        "contract": "environment.opportunity",
        "version": "1.0",
        "event_key": _key(kind, *dedupe_parts),
        "revision": _key(kind, *revision_parts),
        "kind": kind,
        "severity": severity,
        "severity_rank": SEVERITY_ORDER.get(severity, -1),
        "severity_basis": tuple(str(value) for value in severity_basis),
        "facts": dict(facts),
        "location": {
            "key": _key("location", location_name.casefold(), timezone_name),
            "name": location_name,
            "timezone": timezone_name,
        },
        "observed_at": str(observed_at or "")[:80] or None,
        "fetched_at": str(fetched_at or "")[:80] or None,
        "stale": bool(stale),
        "provenance": {
            "authority": source_kind,
            "provider": source_provider,
            "local_assessment": "source"
            if source_kind == "official_warning"
            else "plugin_filter",
        },
    }


def select_opportunity(
    alerts: dict[str, Any] | None,
    air_quality: dict[str, Any] | None,
    weather: dict[str, Any] | None,
    *,
    minimum_severity: str = "medium",
    european_aqi_threshold: float = 80,
    us_aqi_threshold: float = 151,
    uv_threshold: float = 8,
    temperature_drop_c: float = 8,
) -> dict[str, Any] | None:
    """Select one neutral, noteworthy fact from already-fetched snapshots."""
    alerts = alerts if isinstance(alerts, dict) else {}
    air_quality = air_quality if isinstance(air_quality, dict) else {}
    weather = weather if isinstance(weather, dict) else {}
    location = (
        alerts.get("location")
        or air_quality.get("location")
        or weather.get("location")
        or {}
    )
    if not isinstance(location, dict):
        location = {}
    location_name = str(location.get("name") or "当前地点")
    candidates: list[dict[str, Any]] = []

    for warning in alerts.get("official_weather_warnings") or []:
        if not isinstance(warning, dict):
            continue
        severity = _WARNING_SEVERITY.get(str(warning.get("level") or ""), "low")
        title = str(warning.get("title") or "").strip()
        if not title:
            continue
        candidates.append(
            _candidate(
                kind="official_weather_warning",
                severity=severity,
                facts={
                    "warning_title": title,
                    "warning_level": str(warning.get("level") or ""),
                    "warning_kind": str(warning.get("kind") or ""),
                    "issued_at": str(warning.get("issued_at") or ""),
                },
                location=location,
                observed_at=warning.get("issued_at"),
                fetched_at=alerts.get("fetched_at"),
                stale=alerts.get("stale", False),
                source_kind="official_warning",
                source_provider="中央气象台",
                dedupe_parts=(
                    location_name,
                    warning.get("alert_id") or title,
                    str(warning.get("issued_at") or "")[:10],
                ),
                revision_parts=(
                    warning.get("alert_id"),
                    title,
                    warning.get("issued_at"),
                    severity,
                ),
                severity_basis=(f"nmc_level:{warning.get('level') or 'unknown'}",),
            )
        )

    for event in alerts.get("earthquakes") or []:
        if not isinstance(event, dict):
            continue
        magnitude = _number(event.get("magnitude"))
        if magnitude is None:
            continue
        relevance = str(event.get("relevance") or "")
        if magnitude >= 6:
            base_rank = 3
        elif magnitude >= 5:
            base_rank = 2
        elif magnitude >= 4:
            base_rank = 1
        else:
            base_rank = 0
        downgrade = {"较强相关": 0, "相关": 1, "边缘相关": 2}.get(relevance, 2)
        final_rank = max(0, base_rank - downgrade)
        severity = next(
            name for name, rank in SEVERITY_ORDER.items() if rank == final_rank
        )
        distance = _number(event.get("distance_km"))
        place = str(event.get("place") or "附近")[:120]
        candidates.append(
            _candidate(
                kind="earthquake",
                severity=severity,
                facts={
                    "magnitude": magnitude,
                    "place": place,
                    "distance_km": distance,
                    "relevance": relevance,
                    "occurred_at": str(event.get("occurred_at") or ""),
                },
                location=location,
                observed_at=event.get("occurred_at"),
                fetched_at=alerts.get("fetched_at"),
                stale=alerts.get("stale", False),
                source_kind="official_feed",
                source_provider="USGS",
                dedupe_parts=(event.get("event_id") or event.get("occurred_at"),),
                revision_parts=(
                    event.get("event_id"),
                    event.get("updated_at"),
                    magnitude,
                    severity,
                ),
                severity_basis=(f"magnitude:{magnitude:g}", f"relevance:{relevance}"),
            )
        )

    weather_severity = {
        "heavy_rain_forecast": "high",
        "strong_wind_forecast": "high",
        "extreme_heat_forecast": "high",
        "extreme_cold_forecast": "high",
        "thunderstorm_forecast": "high",
    }
    for signal in alerts.get("weather_risk_signals") or []:
        if not isinstance(signal, dict):
            continue
        kind = str(signal.get("kind") or "")
        if kind not in weather_severity:
            continue
        date = str(signal.get("date") or "")
        value = signal.get("value")
        numeric_value = _number(value)
        unit = str(signal.get("unit") or "")
        if kind == "heavy_rain_forecast" and numeric_value is not None:
            severity = (
                "critical"
                if numeric_value >= 250
                else "high"
                if numeric_value >= 100
                else "medium"
            )
        elif kind == "strong_wind_forecast" and numeric_value is not None:
            severity = (
                "critical"
                if numeric_value >= 118
                else "high"
                if numeric_value >= 89
                else "medium"
            )
        elif kind == "extreme_heat_forecast" and numeric_value is not None:
            severity = "critical" if numeric_value >= 42 else "high"
        elif kind == "extreme_cold_forecast" and numeric_value is not None:
            severity = "critical" if numeric_value <= -30 else "high"
        elif kind == "thunderstorm_forecast" and numeric_value is not None:
            severity = (
                "critical"
                if int(numeric_value) == 99
                else "high"
                if int(numeric_value) == 96
                else "medium"
            )
        else:
            severity = weather_severity[kind]
        candidates.append(
            _candidate(
                kind=kind,
                severity=severity,
                facts={
                    "date": date,
                    "risk_kind": kind,
                    "value": value,
                    "unit": unit,
                },
                location=location,
                observed_at=date,
                fetched_at=alerts.get("fetched_at"),
                stale=alerts.get("stale", False),
                source_kind="model_risk_signal",
                source_provider="Open-Meteo",
                dedupe_parts=(location_name, kind, date),
                revision_parts=(kind, date, value, severity),
                severity_basis=(f"{kind}:{value}{unit}",),
            )
        )

    air_current = (air_quality.get("payload") or {}).get("current") or {}
    if isinstance(air_current, dict):
        eu_aqi = _number(air_current.get("european_aqi"))
        us_aqi = _number(air_current.get("us_aqi"))
        aqi_reached = (eu_aqi is not None and eu_aqi >= european_aqi_threshold) or (
            us_aqi is not None and us_aqi >= us_aqi_threshold
        )
        if aqi_reached:
            severity = _max_severity(_eu_aqi_severity(eu_aqi), _us_aqi_severity(us_aqi))
            candidates.append(
                _candidate(
                    kind="high_air_quality_index",
                    severity=severity,
                    facts={
                        "european_aqi": eu_aqi,
                        "us_aqi": us_aqi,
                        "observed_at": str(air_current.get("time") or ""),
                    },
                    location=location,
                    observed_at=air_current.get("time"),
                    fetched_at=air_quality.get("fetched_at"),
                    stale=air_quality.get("stale", False),
                    source_kind="model_air_quality",
                    source_provider="Open-Meteo Air Quality",
                    dedupe_parts=(
                        location_name,
                        str(air_current.get("time") or "")[:10],
                    ),
                    revision_parts=(
                        air_current.get("time"),
                        int(eu_aqi or -1),
                        int(us_aqi or -1),
                        severity,
                    ),
                    severity_basis=(
                        f"european_aqi:{eu_aqi}",
                        f"us_aqi:{us_aqi}",
                    ),
                )
            )
        uv = _number(air_current.get("uv_index"))
        if uv is not None and uv >= uv_threshold:
            candidates.append(
                _candidate(
                    kind="high_uv_index",
                    severity=_uv_severity(uv),
                    facts={
                        "uv_index": uv,
                        "observed_at": str(air_current.get("time") or ""),
                    },
                    location=location,
                    observed_at=air_current.get("time"),
                    fetched_at=air_quality.get("fetched_at"),
                    stale=air_quality.get("stale", False),
                    source_kind="model_uv",
                    source_provider="Open-Meteo Air Quality",
                    dedupe_parts=(
                        location_name,
                        str(air_current.get("time") or "")[:10],
                    ),
                    revision_parts=(
                        air_current.get("time"),
                        round(uv, 1),
                        _uv_severity(uv),
                    ),
                    severity_basis=(f"uv_index:{uv:g}",),
                )
            )

    daily = (weather.get("payload") or {}).get("daily") or {}
    if isinstance(daily, dict):
        dates = list(daily.get("time") or [])
        highs = list(daily.get("temperature_2m_max") or [])
        lows = list(daily.get("temperature_2m_min") or [])
        if len(dates) >= 2:
            high0, high1 = (
                _number(highs[0] if highs else None),
                _number(highs[1] if len(highs) > 1 else None),
            )
            low0, low1 = (
                _number(lows[0] if lows else None),
                _number(lows[1] if len(lows) > 1 else None),
            )
            drops = [
                old - new
                for old, new in ((high0, high1), (low0, low1))
                if old is not None and new is not None
            ]
            drop = max(drops, default=0.0)
            if drop >= temperature_drop_c:
                candidates.append(
                    _candidate(
                        kind="strong_temperature_drop",
                        severity=_drop_severity(drop),
                        facts={
                            "from_date": str(dates[0]),
                            "to_date": str(dates[1]),
                            "temperature_drop_c": round(drop, 1),
                        },
                        location=location,
                        observed_at=dates[1],
                        fetched_at=weather.get("fetched_at"),
                        stale=weather.get("stale", False),
                        source_kind="model_forecast",
                        source_provider="Open-Meteo",
                        dedupe_parts=(location_name, dates[0], dates[1]),
                        revision_parts=(dates[0], dates[1], round(drop, 1)),
                        severity_basis=(f"temperature_drop_c:{drop:g}",),
                    )
                )

    usable = [
        item
        for item in candidates
        if item.get("facts")
        and not item.get("stale")
        and severity_at_least(str(item.get("severity")), minimum_severity)
    ]
    if not usable:
        return None
    usable.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), -1),
            _SOURCE_PRIORITY.get(str(item.get("kind")), 0),
            str(item.get("observed_at") or ""),
        ),
        reverse=True,
    )
    return usable[0]
