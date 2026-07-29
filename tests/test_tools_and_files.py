from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_environment_awareness.tools import create_tools

ROOT = Path(__file__).resolve().parents[1]


class FakeService:
    async def datetime_snapshot(self, location=""):
        return {"kind": "datetime", "location": location}

    async def weather_snapshot(self, location="", forecast_range="current", days=3):
        return {"kind": "weather", "range": forecast_range, "days": days}

    async def calendar_snapshot(self, location="", date_text=""):
        return {"kind": "calendar", "date": date_text}

    async def air_quality_snapshot(self, location="", forecast_hours=0):
        return {"kind": "air_quality", "hours": forecast_hours}

    async def alerts_snapshot(self, location="", hours=24):
        return {"kind": "alerts", "hours": hours, "earthquakes": []}

    def list_locations(self):
        return {"configured": False}


def test_create_tools_registers_environment_surface():
    tools = create_tools(SimpleNamespace(service=FakeService()))
    assert [tool.name for tool in tools] == [
        "get_local_datetime",
        "get_local_calendar",
        "get_weather",
        "get_air_quality",
        "get_environment_alerts",
        "list_environment_locations",
    ]


def test_alert_tool_description_states_irrelevant_events_are_filtered():
    tools = create_tools(SimpleNamespace(service=FakeService()))
    alert_tool = next(tool for tool in tools if tool.name == "get_environment_alerts")
    assert "距离过远" in alert_tool.description
    assert "不会返回给模型" in alert_tool.description


def test_tool_calls_return_structured_json():
    async def scenario():
        tools = create_tools(SimpleNamespace(service=FakeService()))
        weather_tool = next(tool for tool in tools if tool.name == "get_weather")
        result = json.loads(
            await weather_tool.call(
                None, location="杭州", forecast_range="daily", days=5
            )
        )
        assert result == {"kind": "weather", "range": "daily", "days": 5}

    asyncio.run(scenario())


def test_tool_error_is_bounded_and_structured():
    class FailingService(FakeService):
        async def datetime_snapshot(self, location=""):
            raise ValueError("bad location")

    async def scenario():
        tools = create_tools(SimpleNamespace(service=FailingService()))
        result = json.loads(await tools[0].call(None, location="bad"))
        assert result == {"status": "error", "message": "bad location"}

    asyncio.run(scenario())


def test_metadata_schema_and_development_version_are_consistent():
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "version: 0.1.0" in metadata
    assert 'PLUGIN_VERSION = "0.1.0"' in main
    assert schema["default_location"]["default"] == ""
    assert schema["earthquake_max_distance_km"]["default"] == 1200
    assert schema["calendar_awareness_enabled"]["default"] is True
    assert schema["official_weather_warnings_enabled"]["default"] is True


def test_plugin_page_has_quick_setup_and_probe_controls():
    html = (ROOT / "pages/status/index.html").read_text(encoding="utf-8")
    app = (ROOT / "pages/status/app.js").read_text(encoding="utf-8")
    assert 'id="default-location"' in html
    assert 'id="probe"' in html
    assert 'id="probe-pollen"' in html
    assert 'bridge.apiPost("setup"' in app
    assert 'bridge.apiPost("probe"' in app


def test_main_has_only_selective_calendar_prompt_injection():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "@filter.on_llm_request(priority=550)" in main
    assert "cached_calendar_awareness" in main
    assert "不要主动播报或生硬提及" in (
        ROOT / "core/calendar.py"
    ).read_text(encoding="utf-8")
    assert "active_push=off" in main


def test_docs_name_official_sources_and_current_limitations():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Open-Meteo" in readme
    assert "中央气象台" in readme
    assert "python-holidays" in readme
    assert "USGS" in readme
    assert "不会冒充气象部门发布的预警" in readme
    assert "## 0.1.0 - 2026-07-29" in changelog
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 qsbb" in license_text
