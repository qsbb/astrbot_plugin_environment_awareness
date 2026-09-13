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


def test_weather_tool_treats_direct_temperature_and_rain_questions_as_realtime():
    weather = next(
        tool
        for tool in create_tools(SimpleNamespace(service=FakeService()))
        if tool.name == "get_weather"
    )
    for phrase in (
        "明确的实时事实请求",
        "应调用",
        "当前温度",
        "最近是否下雨",
        "你那今天多少度",
        "你那边最近有下雨吗",
        "不要求用户正在做出行决定",
    ):
        assert phrase in weather.description


def test_weather_tool_preserves_non_realtime_weather_non_call_boundaries():
    weather = next(
        tool
        for tool in create_tools(SimpleNamespace(service=FakeService()))
        if tool.name == "get_weather"
    )
    for phrase in ("文学比喻", "虚构场景", "天气回忆", "纯情绪化表达", "心里阴天"):
        assert phrase in weather.description
    assert "不按关键词强制调用" in weather.description


def test_weather_tool_schema_requires_location_question_when_deictic_place_is_unknown():
    weather = next(
        tool
        for tool in create_tools(SimpleNamespace(service=FakeService()))
        if tool.name == "get_weather"
    )
    location_hint = weather.parameters["properties"]["location"]["description"]
    for phrase in (
        "你那",
        "你那里",
        "你那边",
        "有效 default_location",
        "单用户部署",
        "多用户部署必须显式提供",
        "无法确定地点时先自然追问",
        "不得猜测地点、编造天气",
    ):
        assert phrase in location_hint


def test_tool_calls_return_structured_json():
    async def scenario():
        calls = []

        def record(*args, **kwargs):
            calls.append((args, kwargs))

        tools = create_tools(
            SimpleNamespace(service=FakeService(), record_invocation=record)
        )
        weather_tool = next(tool for tool in tools if tool.name == "get_weather")
        result = json.loads(
            await weather_tool.call(
                None, location="杭州", forecast_range="daily", days=5
            )
        )
        assert result == {"kind": "weather", "range": "daily", "days": 5}
        assert calls[0][0] == ("llm_tool", "get_weather")
        assert calls[0][1]["status"] == "success"

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
    assert "version: 0.6.3" in metadata
    assert 'PLUGIN_VERSION = "0.6.3"' in main
    assert schema["default_location"]["default"] == ""
    assert "page_enabled" not in schema
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
    assert "归属不明时先询问" in schema["default_location"]["hint"]


def test_plugin_page_is_a_kernel_webui_guidance_page():
    html = (ROOT / "pages/status/index.html").read_text(encoding="utf-8")
    app = (ROOT / "pages/status/app.js").read_text(encoding="utf-8")

    assert "凝心溯溪-境" in html
    assert "核 WebUI" in html
    assert "环境与时间" in html
    assert "插件面板" in html
    assert "/境天气" in html
    assert "AstrBot 原生插件配置页" in html
    assert "AstrBot 插件管理页" in html
    assert "data-series-ui" in html
    # 保持核 series.ui 审计要求的资源顺序：
    # style → series-ui.css → series-ui.js → app.js。
    positions = [
        html.find("style.css"),
        html.find("series-ui.css"),
        html.find("series-ui.js"),
        html.find("app.js"),
    ]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
    assert "style.css?v=0.6.3-1" in html
    assert "series-ui.css?v=0.6.3-1" in html
    assert "series-ui.js?v=0.6.3-1" in html
    assert "app.js?v=0.6.3-1" in html
    # 引导页不再调用桥接 API，也不会发起 status/config/probe 拉取。
    assert "AstrBotPluginPage" not in html
    assert "apiGet" not in html and "apiGet" not in app
    assert "apiPost" not in html and "apiPost" not in app
    assert "navigator.geolocation" not in html and "navigator.geolocation" not in app


def test_plugin_page_no_longer_owns_config_editing():
    html = (ROOT / "pages/status/index.html").read_text(encoding="utf-8")
    assert "config-form" not in html
    assert "readNumericConfig" not in html
    # 配置写入仍由插件侧共享校验路径处理，核面板不会绕过它。
    webui = (ROOT / "series_webui.py").read_text(encoding="utf-8")
    assert "self.plugin._save_config_payload(payload)" in webui


def test_page_config_schema_declares_numeric_boundaries():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    expected = {
        "forecast_days": (1, 7),
        "opportunity_refresh_seconds": (300, 21600),
        "earthquake_min_magnitude": (2.5, 9.9),
        "request_timeout_seconds": (2, 20),
        "stale_cache_seconds": (0, 86400),
    }
    for key, (minimum, maximum) in expected.items():
        assert schema[key]["minimum"] == minimum
        assert schema[key]["maximum"] == maximum


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


def test_docs_explain_kernel_webui_migration_and_usage_privacy():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "核 WebUI" in readme
    assert "实时检查" in readme
    assert "不记录消息内容、用户 ID、UMO 或查询地点" in readme
    assert "单次探测间隔不少于 5 秒" in readme
    assert "页面探测不计入真实调用次数" in changelog


def test_status_page_points_to_kernel_webui_panels():
    html = (ROOT / "pages/status/index.html").read_text(encoding="utf-8")
    assert "核 WebUI" in html
    assert "插件面板" in html
    assert "使用当前位置" not in html
    assert "保存校验" not in html
