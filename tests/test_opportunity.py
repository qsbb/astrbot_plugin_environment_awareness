from __future__ import annotations

from datetime import UTC, datetime

import pytest
from astrbot_plugin_environment_awareness.core.opportunity import select_opportunity
from astrbot_plugin_environment_awareness.core.proactive import (
    ProactiveDeliveryState,
    local_delivery_window,
)


def _location() -> dict[str, str]:
    return {"name": "杭州市 · 浙江 · 中国", "timezone": "Asia/Shanghai"}


def _alerts(*, warning_level: str = "", rain: float | None = None):
    warnings = []
    if warning_level:
        warnings.append(
            {
                "alert_id": "warning-1",
                "title": f"杭州市气象台发布暴雨{warning_level}预警信号",
                "kind": "暴雨",
                "level": warning_level,
                "issued_at": "2026-07-30T08:00:00+08:00",
            }
        )
    risks = []
    if rain is not None:
        risks.append(
            {
                "kind": "heavy_rain_forecast",
                "date": "2026-07-30",
                "value": rain,
                "unit": "mm/day",
                "summary": "这段可变中文不应进入候选事实",
            }
        )
    return {
        "location": _location(),
        "official_weather_warnings": warnings,
        "weather_risk_signals": risks,
        "earthquakes": [],
        "fetched_at": "2026-07-30T08:05:00+08:00",
        "stale": False,
    }


def _air(*, us_aqi: float | None = None, eu_aqi: float | None = None, uv=0):
    return {
        "location": _location(),
        "payload": {
            "current": {
                "time": "2026-07-30T08:00",
                "us_aqi": us_aqi,
                "european_aqi": eu_aqi,
                "uv_index": uv,
            }
        },
        "fetched_at": "2026-07-30T08:05:00+08:00",
        "stale": False,
    }


def _weather(*, high_today=32, high_tomorrow=31, low_today=24, low_tomorrow=23):
    return {
        "location": _location(),
        "payload": {
            "daily": {
                "time": ["2026-07-30", "2026-07-31"],
                "temperature_2m_max": [high_today, high_tomorrow],
                "temperature_2m_min": [low_today, low_tomorrow],
            }
        },
        "fetched_at": "2026-07-30T08:05:00+08:00",
        "stale": False,
    }


def test_candidate_is_structured_fact_without_identity_or_message_fields():
    candidate = select_opportunity(_alerts(warning_level="橙色"), _air(), _weather())
    assert candidate is not None
    assert candidate["kind"] == "official_weather_warning"
    assert candidate["severity"] == "high"
    assert candidate["facts"]["warning_level"] == "橙色"
    assert "fact" not in candidate
    assert "message" not in candidate
    assert "person_id" not in str(candidate)
    assert "umo" not in str(candidate).lower()
    assert set(candidate["location"]) == {"key", "name", "timezone"}


def test_official_warning_wins_same_severity_model_signal():
    candidate = select_opportunity(
        _alerts(warning_level="橙色", rain=120), _air(), _weather()
    )
    assert candidate is not None
    assert candidate["kind"] == "official_weather_warning"


def test_stale_high_priority_source_cannot_hide_fresh_candidate():
    stale_alerts = _alerts(warning_level="红色")
    stale_alerts["stale"] = True
    candidate = select_opportunity(
        stale_alerts,
        _air(us_aqi=151),
        _weather(),
        minimum_severity="high",
    )
    assert candidate is not None
    assert candidate["kind"] == "high_air_quality_index"
    assert candidate["stale"] is False


@pytest.mark.parametrize(
    ("us_aqi", "expected"),
    [(150, None), (151, "high"), (300, "high"), (301, "critical")],
)
def test_us_aqi_default_threshold_boundaries(us_aqi, expected):
    candidate = select_opportunity(
        _alerts(), _air(us_aqi=us_aqi), _weather(), minimum_severity="high"
    )
    assert (candidate or {}).get("severity") == expected


@pytest.mark.parametrize(
    ("eu_aqi", "expected"),
    [(79.9, None), (80, "high"), (100, "high"), (100.1, "critical")],
)
def test_european_aqi_default_threshold_boundaries(eu_aqi, expected):
    candidate = select_opportunity(
        _alerts(), _air(eu_aqi=eu_aqi), _weather(), minimum_severity="high"
    )
    assert (candidate or {}).get("severity") == expected


@pytest.mark.parametrize(
    ("uv", "expected"),
    [(7.9, None), (8, "high"), (10.9, "high"), (11, "critical")],
)
def test_uv_default_threshold_boundaries(uv, expected):
    candidate = select_opportunity(
        _alerts(), _air(uv=uv), _weather(), minimum_severity="high"
    )
    assert (candidate or {}).get("severity") == expected


@pytest.mark.parametrize(
    ("drop", "expected"),
    [(7.9, None), (8, "medium"), (11.9, "medium"), (12, "high"), (16, "critical")],
)
def test_temperature_drop_boundaries(drop, expected):
    candidate = select_opportunity(
        _alerts(),
        _air(),
        _weather(high_today=30, high_tomorrow=30 - drop),
        minimum_severity="medium",
    )
    assert (candidate or {}).get("severity") == expected


def test_refresh_changes_revision_but_not_event_key():
    first = select_opportunity(_alerts(rain=100), _air(), _weather())
    second = select_opportunity(_alerts(rain=120), _air(), _weather())
    assert first and second
    assert first["event_key"] == second["event_key"]
    assert first["revision"] != second["revision"]


def test_delivery_ledger_allows_only_severity_upgrade_and_persists(tmp_path):
    path = tmp_path / "state.json"
    state = ProactiveDeliveryState(path)
    assert state.can_send("event-1", "medium", "2026-07-30", 5)[0]
    state.mark_sent("event-1", "medium", "rev-1", "2026-07-30")
    assert not state.can_send("event-1", "low", "2026-07-30", 5)[0]
    assert not state.can_send("event-1", "medium", "2026-07-30", 5)[0]
    assert state.can_send("event-1", "high", "2026-07-30", 5)[0]
    reloaded = ProactiveDeliveryState(path)
    assert not reloaded.can_send("event-1", "medium", "2026-07-30", 5)[0]
    assert reloaded.can_send("event-1", "critical", "2026-07-30", 5)[0]


def test_suppressed_decision_is_not_retried_until_severity_upgrade(tmp_path):
    path = tmp_path / "state.json"
    state = ProactiveDeliveryState(path)
    state.mark_evaluated("event-1", "high", "rev-1")
    allowed, reason = state.can_send("event-1", "high", "2026-07-30", 1)
    assert allowed is False
    assert reason == "already_evaluated_at_severity"
    assert state.can_send("event-1", "critical", "2026-07-30", 1)[0]
    assert state.snapshot()["daily_counts"] == {}

    reloaded = ProactiveDeliveryState(path)
    assert not reloaded.can_send("event-1", "high", "2026-07-30", 1)[0]


def test_quiet_hours_can_cross_midnight():
    now = datetime(2026, 7, 30, 16, 30, tzinfo=UTC)  # 00:30 in Shanghai
    local_date, quiet = local_delivery_window(
        "Asia/Shanghai", "23:00", "07:00", now=now
    )
    assert local_date == "2026-07-31"
    assert quiet is True
