from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from astrbot.api import AstrBotConfig
from astrbot_plugin_environment_awareness.main import EnvironmentAwarenessPlugin


class FakeContext:
    def __init__(self):
        self.tools = []
        self.routes = []

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def register_web_api(self, route, handler, methods, description):
        self.routes.append((route, tuple(methods), description))

    def unregister_llm_tool(self, name):
        return None


def _plugin(config=None):
    return EnvironmentAwarenessPlugin(FakeContext(), AstrBotConfig(config or {}))


def _owner_context(revision, request_id="req-test"):
    return {
        "actor": {"role": "owner"},
        "expected_revision": revision,
        "request_id": request_id,
    }


def test_contract_declares_status_settings_and_controlled_probes():
    plugin = _plugin()
    contract = plugin.webui_panels_contract()
    assert [panel["id"] for panel in contract["panels"]] == ["status", "settings"]
    assert contract["managed"]["level"] == "actions"
    assert {
        "generic_table",
        "generic_actions",
        "revision",
        "idempotency",
    }.issubset(set(contract["capabilities"]))
    settings = contract["panels"][1]
    actions = {action["id"]: action for action in settings["actions"]}
    assert set(actions) == {
        "save_config",
        "set_control",
        "set_default_location",
        "probe_weather",
        "probe_air",
        "probe_calendar",
    }
    for action in actions.values():
        assert action["effect"] == "idempotent"
        assert action["min_role"] == "admin"
        assert "timeout_seconds" in action
    asyncio.run(plugin.terminate())


def test_settings_data_redacts_secret_fields_and_never_returns_them(monkeypatch):
    plugin = _plugin({"api_key": "super-secret-value", "forecast_days": 5})
    original_schema = plugin._page_schema()

    monkeypatch.setattr(
        type(plugin),
        "_page_schema",
        staticmethod(
            lambda: {
                **original_schema,
                "api_key": {
                    "description": "数据源 API Key",
                    "type": "string",
                    "secret": True,
                    "default": "",
                },
            }
        ),
    )
    data = plugin.webui_panel_data("settings")
    serialized = json.dumps(data, ensure_ascii=False)
    assert data["success"] is True
    assert "super-secret-value" not in serialized
    assert "已配置（不回显）" in serialized
    save_action = next(
        item for item in data["actions"] if item["id"] == "save_config"
    )
    assert "api_key" not in {field["name"] for field in save_action["payload_fields"]}
    forecast = next(
        field
        for field in save_action["payload_fields"]
        if field["name"] == "forecast_days"
    )
    assert forecast["default"] == 5
    asyncio.run(plugin.terminate())


def test_save_config_reuses_schema_validation_and_hot_apply():
    async def scenario():
        plugin = _plugin()
        revision = plugin.webui_panel_data("settings")["revision"]
        result = await plugin.webui_panel_action(
            "settings",
            "save_config",
            {
                "forecast_days": 5,
                "calendar_awareness_enabled": False,
                "request_timeout_seconds": 8,
            },
            _owner_context(revision),
        )
        assert result["success"] is True
        assert result["changed"] == [
            "calendar_awareness_enabled",
            "forecast_days",
            "request_timeout_seconds",
        ]
        assert plugin.config["forecast_days"] == 5
        assert plugin.service.settings().calendar_awareness_enabled is False
        assert plugin._http_client.timeout.read == 8

        invalid = await plugin.webui_panel_action(
            "settings",
            "save_config",
            {"forecast_days": 99},
            _owner_context(plugin.webui_panel_data("settings")["revision"]),
        )
        assert invalid["success"] is False
        assert invalid["error"] == "VALIDATION_FAILED"
        assert plugin.config["forecast_days"] == 5
        await plugin.terminate()

    asyncio.run(scenario())


def test_save_config_rejects_control_fields_and_stale_revision():
    async def scenario():
        plugin = _plugin()
        revision = plugin.webui_panel_data("settings")["revision"]
        control = await plugin.webui_panel_action(
            "settings",
            "save_config",
            {"proactive_enabled": True},
            _owner_context(revision),
        )
        assert control["error"] == "CONTROL_FIELD_REQUIRES_SET_CONTROL"
        with pytest.raises(ValueError, match="REVISION_CONFLICT"):
            await plugin.webui_panel_action(
                "settings",
                "save_config",
                {"forecast_days": 4},
                _owner_context("stale-revision"),
            )
        await plugin.terminate()

    asyncio.run(scenario())


def test_set_control_calls_existing_series_control_and_hot_applies(
    monkeypatch, tmp_path
):
    async def scenario():
        plugin = _plugin({"proactive_enabled": False})
        monkeypatch.setattr(plugin._usage, "_path", tmp_path / "usage.json")
        plugin.series_control_set_mode("managed")
        revision = plugin.webui_panel_data("settings")["revision"]
        result = await plugin.webui_panel_action(
            "settings",
            "set_control",
            {"proactive_enabled": True},
            _owner_context(revision),
        )
        assert result["success"] is True
        assert plugin.config["proactive_enabled"] is True
        snapshot = plugin.series_control_snapshot()
        assert snapshot["fields"]["proactive_enabled"]["managed_configured"] is True
        await plugin.terminate()

    asyncio.run(scenario())


def test_set_default_location_uses_shared_resolution_service(monkeypatch):
    async def scenario():
        plugin = _plugin()
        resolved = SimpleNamespace(
            public_dict=lambda: {"name": "杭州", "timezone": "Asia/Shanghai"},
            profile_dict=lambda: {
                "query": "杭州",
                "name": "杭州",
                "latitude": 30.25,
                "longitude": 120.17,
                "timezone": "Asia/Shanghai",
            },
        )
        called = []

        async def resolve_location(location):
            called.append(location)
            return resolved

        monkeypatch.setattr(plugin.service, "resolve_location", resolve_location)
        revision = plugin.webui_panel_data("settings")["revision"]
        result = await plugin.webui_panel_action(
            "settings",
            "set_default_location",
            {"location": "杭州"},
            _owner_context(revision),
        )
        assert result["success"] is True
        assert called == ["杭州"]
        assert plugin.config["default_location"] == "杭州"
        await plugin.terminate()

    asyncio.run(scenario())


def test_probe_actions_are_bounded_sanitized_and_rate_limited(monkeypatch):
    async def scenario():
        plugin = _plugin()

        async def weather(location, forecast_range, days):
            assert (location, forecast_range, days) == ("", "current", 1)
            return {
                "location": {"name": "杭州"},
                "observed_at": "2026-09-12T10:00:00+08:00",
                "stale": False,
                "payload": {
                    "current": {"temperature_2m": 25.5, "weather_code": 1}
                },
            }

        async def air(location, days):
            raise RuntimeError("https://provider.invalid/?token=secret")

        async def calendar(location, date_text):
            return {
                "location": {"name": "杭州"},
                "date": date_text or "2026-09-12",
                "day_type": "workday",
                "holiday_name": "",
            }

        monkeypatch.setattr(plugin.service, "weather_snapshot", weather)
        monkeypatch.setattr(plugin.service, "air_quality_snapshot", air)
        monkeypatch.setattr(plugin.service, "calendar_snapshot", calendar)

        weather_result = await plugin.webui_panel_action(
            "settings", "probe_weather", {"location": ""}
        )
        assert weather_result["success"] is True
        assert weather_result["result"]["temperature"] == 25.5
        assert "provider.invalid" not in json.dumps(weather_result)

        limited = await plugin.webui_panel_action(
            "settings", "probe_weather", {"location": ""}
        )
        assert limited["error"] == "RATE_LIMITED"

        failed = await plugin.webui_panel_action(
            "settings", "probe_air", {"location": "杭州"}
        )
        assert failed == {"success": False, "error": "PROBE_FAILED", "probe": "air"}
        assert "secret" not in json.dumps(failed)

        calendar_result = await plugin.webui_panel_action(
            "settings",
            "probe_calendar",
            {"location": "杭州", "date": "2026-09-12"},
        )
        assert calendar_result["success"] is True
        assert calendar_result["result"]["date"] == "2026-09-12"

        too_long = await plugin.webui_panel_action(
            "settings", "probe_air", {"location": "x" * 101}
        )
        assert too_long["error"] == "LOCATION_TOO_LONG"
        await plugin.terminate()

    asyncio.run(scenario())


def test_unknown_action_and_role_fail_closed():
    async def scenario():
        plugin = _plugin()
        unknown = await plugin.webui_panel_action(
            "settings", "drop_everything", {}
        )
        assert unknown == {"success": False, "error": "UNKNOWN_ACTION"}
        with pytest.raises(PermissionError, match="ROLE_FORBIDDEN"):
            await plugin.webui_panel_action(
                "settings",
                "save_config",
                {"forecast_days": 4},
                {"actor": {"role": "viewer"}, "expected_revision": "ignored"},
            )
        await plugin.terminate()

    asyncio.run(scenario())
