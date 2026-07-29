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


def test_tool_descriptions_cover_natural_intents_without_forcing_unrelated_calls():
    tools = {
        tool.name: tool for tool in create_tools(SimpleNamespace(service=FakeService()))
    }
    expected_phrases = {
        "get_local_datetime": ("跨时区联系", "作息", "普通问候", "虚构场景"),
        "get_local_calendar": ("出行", "个人排班", "节日文化历史"),
        "get_weather": ("带伞", "通勤", "晾晒", "仅提到出门"),
        "get_air_quality": ("开窗", "户外运动", "只描述症状", "不作医疗结论"),
        "get_environment_alerts": ("震感", "影视游戏", "不渲染恐慌", "不等于无风险"),
        "list_environment_locations": ("管理员", "多用户会话不要调用", "一次往返"),
    }
    for name, phrases in expected_phrases.items():
        description = tools[name].description
        assert all(phrase in description for phrase in phrases)
    assert "不按关键词强制调用" in tools["get_weather"].description
    assert tools["get_weather"].parameters["properties"]["forecast_range"][
        "description"
    ] == (
        "current=此刻；nowcast=未来约6小时的15分钟降水；hourly=未来24小时；daily=1至7日"
    )
    for name in (
        "get_local_datetime",
        "get_local_calendar",
        "get_weather",
        "get_air_quality",
        "get_environment_alerts",
    ):
        location_hint = tools[name].parameters["properties"]["location"]["description"]
        assert "多用户部署必须显式提供" in location_hint
        assert "插件级全局常驻地点" in location_hint


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
    assert "version: 0.1.1" in metadata
    assert 'PLUGIN_VERSION = "0.1.1"' in main
    assert schema["default_location"]["default"] == ""
    assert schema["earthquake_max_distance_km"]["default"] == 1200
    assert schema["calendar_awareness_enabled"]["default"] is True
    assert schema["official_weather_warnings_enabled"]["default"] is True
    assert schema["opportunity_cache_enabled"]["default"] is True
    assert schema["proactive_enabled"]["default"] is False
    assert schema["proactive_paused"]["default"] is False
    assert schema["proactive_min_severity"]["default"] == "high"
    assert schema["proactive_daily_limit"]["default"] == 1
    assert "插件级全局地点" in schema["default_location"]["hint"]
    assert "多用户部署" in schema["default_location"]["hint"]


def test_plugin_page_has_quick_setup_and_probe_controls():
    html = (ROOT / "pages/status/index.html").read_text(encoding="utf-8")
    app = (ROOT / "pages/status/app.js").read_text(encoding="utf-8")
    assert 'id="default-location"' in html
    assert 'id="probe"' in html
    assert 'id="probe-pollen"' in html
    assert 'id="opportunity-cache"' in html
    assert 'id="proactive-status"' in html
    assert 'bridge.apiPost("setup"' in app
    assert 'bridge.apiPost("probe"' in app
    assert "status.opportunity_cache" in app
    assert "status.proactive_delivery" in app


def test_main_has_only_selective_calendar_prompt_injection():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "@filter.on_llm_request(priority=550)" in main
    assert "cached_calendar_awareness" in main
    calendar = (ROOT / "core/calendar.py").read_text(encoding="utf-8")
    assert "不代表用户个人安排" in calendar
    assert "简短节日问候、调休关心" in calendar
    assert "不要单独播报、连续追问" in calendar
    assert '"on" if settings.proactive_enabled else "off"' in main


def test_docs_explain_fail_closed_environment_delivery_boundaries():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "environment.opportunity@1.0" in readme
    assert "identity.proactive_authorization@1" in readme
    assert "relationship.delivery_identity@1" in readme
    assert "conversation.proactive_delivery@1" in readme
    assert "真正主动消息默认关闭" in readme
    assert "旧候选不会触发" in readme


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
