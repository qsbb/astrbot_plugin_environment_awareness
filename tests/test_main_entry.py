from __future__ import annotations

import asyncio
from types import SimpleNamespace

import astrbot_plugin_environment_awareness.main as main_module
from astrbot.api import AstrBotConfig
from astrbot_plugin_environment_awareness.main import EnvironmentAwarenessPlugin


class FakeContext:
    def __init__(self, plugins=None):
        self.tools = []
        self.routes = []
        self.removed = []
        self.plugins = dict(plugins or {})

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def register_web_api(self, route, handler, methods, description):
        self.routes.append((route, tuple(methods), description))

    def unregister_llm_tool(self, name):
        self.removed.append(name)

    def get_star_instance(self, name):
        return self.plugins.get(name)


def test_plugin_initialization_registers_tools_and_page_apis_without_network():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    assert len(context.tools) == 6
    assert len(context.routes) == 5
    assert plugin.service.diagnostics()["provider_last_success"] == {}
    asyncio.run(plugin.terminate())


def test_page_status_and_config_are_always_available():
    plugin = EnvironmentAwarenessPlugin(FakeContext(), AstrBotConfig())
    status = asyncio.run(plugin._page_status())
    assert status["status_code"] == 200
    assert "page_enabled" not in status["payload"]
    assert "usage" in status["payload"]
    config = asyncio.run(plugin._page_config())
    assert config["status_code"] == 200
    assert config["payload"]["ok"] is True
    asyncio.run(plugin.terminate())


def test_webui_panel_provides_readonly_status():
    plugin = EnvironmentAwarenessPlugin(FakeContext(), AstrBotConfig())
    contract = plugin.webui_panels_contract()
    assert contract["name"] == "series.webui@2.0"
    assert contract["panels"][0]["id"] == "status"
    data = plugin.webui_panel_data("status")
    assert data["success"] is True
    assert data["columns"]
    assert data["rows"]
    assert data["actions"] == []
    assert (
        asyncio.run(plugin.webui_panel_action("status", "anything", {}))["success"]
        is False
    )
    asyncio.run(plugin.terminate())


def test_page_config_validates_persists_and_applies_values(monkeypatch):
    class SavingConfig(AstrBotConfig):
        saves = 0

        def save_config(self):
            self.saves += 1

    async def scenario():
        config = SavingConfig()
        plugin = EnvironmentAwarenessPlugin(FakeContext(), config)

        async def valid_json(default=None):
            del default
            return {
                "forecast_days": 5,
                "calendar_awareness_enabled": False,
                "request_timeout_seconds": 8,
            }

        monkeypatch.setattr(main_module, "request", SimpleNamespace(json=valid_json))
        response = await plugin._page_save_config()
        assert response["status_code"] == 200
        assert response["payload"]["changed"] == [
            "calendar_awareness_enabled",
            "forecast_days",
            "request_timeout_seconds",
        ]
        assert config["forecast_days"] == 5
        assert plugin.service.settings().calendar_awareness_enabled is False
        assert plugin._http_client.timeout.read == 8
        assert config.saves == 1

        async def invalid_json(default=None):
            del default
            return {"forecast_days": 99}

        monkeypatch.setattr(main_module, "request", SimpleNamespace(json=invalid_json))
        invalid = await plugin._page_save_config()
        assert invalid["status_code"] == 400
        assert config["forecast_days"] == 5

        async def invalid_clock_json(default=None):
            del default
            return {"proactive_quiet_start": "25:00"}

        monkeypatch.setattr(
            main_module, "request", SimpleNamespace(json=invalid_clock_json)
        )
        invalid_clock = await plugin._page_save_config()
        assert invalid_clock["status_code"] == 400
        await plugin.terminate()

    asyncio.run(scenario())


def test_plugin_health_matches_update_manager_contract_without_requiring_location():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    health = plugin.plugin_health()
    assert plugin.PLUGIN_HEALTH_CONTRACT == "plugin.health@1.0"
    assert health == {
        "status": "ok",
        "checks": {
            "http_client_ready": True,
            "tools_registered": True,
        },
        "reasons": [],
        "version": "0.6.2",
    }
    asyncio.run(plugin.terminate())


def test_plugin_health_reports_failed_runtime_checks():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    plugin._tools = []
    health = plugin.plugin_health()
    assert health["status"] == "unhealthy"
    assert health["checks"]["tools_registered"] is False
    assert health["reasons"] == ["TOOLS_REGISTERED"]
    asyncio.run(plugin.terminate())


def test_terminate_unregisters_all_tools_and_closes_client():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    asyncio.run(plugin.terminate())
    assert set(context.removed) == {
        "get_local_datetime",
        "get_local_calendar",
        "get_weather",
        "get_air_quality",
        "get_environment_alerts",
        "list_environment_locations",
    }
    assert plugin._http_client.is_closed is True


def test_significant_calendar_awareness_is_injected_once_per_origin_and_day():
    class FakeEvent:
        unified_msg_origin = "platform:private:user"

        @staticmethod
        def get_message_str():
            return "早上好"

    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    plugin.service.cached_calendar_awareness = lambda: (
        "2026-02-17",
        "[境·当地日历] 当地日期 2026-02-17，今天是春节。",
    )
    first = SimpleNamespace(system_prompt="base")
    second = SimpleNamespace(system_prompt="base")
    asyncio.run(plugin.on_llm_request(FakeEvent(), first))
    asyncio.run(plugin.on_llm_request(FakeEvent(), second))
    assert "[境·当地日历]" in first.system_prompt
    assert second.system_prompt == "base"
    asyncio.run(plugin.terminate())


def test_fresh_environment_candidate_uses_flow_preflight_without_network():
    class FakeFlow:
        calls = 0

        @staticmethod
        def proactive_delivery_contract():
            return {"name": "conversation.proactive_delivery", "version": "1.0"}

        async def prepare_environment_reply_context(self, candidate, person_id, umo):
            self.calls += 1
            assert person_id == "owner-person"
            assert umo == "platform:FriendMessage:user"
            assert candidate["event_key"] == "warning-1"
            return {
                "allowed": True,
                "prompt_fragment": "[境·环境关心候选]\n可选事实",
            }

    class FakeEvent:
        unified_msg_origin = "platform:FriendMessage:user"

        @staticmethod
        def get_message_str():
            return "今天有点累"

    flow = FakeFlow()
    context = FakeContext({"astrbot_plugin_conversation_flow": flow})
    config = AstrBotConfig(
        {
            "care_person_id": "owner-person",
            "care_recipient_umo": FakeEvent.unified_msg_origin,
        }
    )
    plugin = EnvironmentAwarenessPlugin(context, config)
    plugin._cached_opportunity = {
        "contract": "environment.opportunity",
        "version": "1.0",
        "event_key": "warning-1",
        "kind": "official_weather_warning",
        "severity": "high",
        "facts": {"level": "橙色"},
        "location": {"name": "杭州", "timezone": "Asia/Shanghai"},
        "stale": False,
    }
    plugin._opportunity_cached_at = __import__("time").monotonic()
    request = SimpleNamespace(system_prompt="base")
    asyncio.run(plugin.on_llm_request(FakeEvent(), request))
    assert "[境·环境关心候选]" in request.system_prompt
    assert flow.calls == 1
    asyncio.run(plugin.terminate())


def test_stale_environment_candidate_is_not_injected():
    class FakeFlow:
        calls = 0

        @staticmethod
        def proactive_delivery_contract():
            return {"name": "conversation.proactive_delivery", "version": "1.0"}

        async def prepare_environment_reply_context(self, *args):
            self.calls += 1
            return {"allowed": True, "prompt_fragment": "should not appear"}

    class FakeEvent:
        unified_msg_origin = "platform:FriendMessage:user"

        @staticmethod
        def get_message_str():
            return "你好"

    flow = FakeFlow()
    config = AstrBotConfig(
        {
            "care_person_id": "owner-person",
            "care_recipient_umo": FakeEvent.unified_msg_origin,
        }
    )
    plugin = EnvironmentAwarenessPlugin(
        FakeContext({"astrbot_plugin_conversation_flow": flow}), config
    )
    plugin._cached_opportunity = {
        "event_key": "old",
        "stale": True,
    }
    plugin._opportunity_cached_at = __import__("time").monotonic()
    request = SimpleNamespace(system_prompt="base")
    asyncio.run(plugin.on_llm_request(FakeEvent(), request))
    assert request.system_prompt == "base"
    assert flow.calls == 0
    asyncio.run(plugin.terminate())


def test_missing_umo_never_falls_back_to_sender_uid():
    class FakeEvent:
        unified_msg_origin = ""

        @staticmethod
        def get_message_str():
            return "早上好"

        @staticmethod
        def get_sender_id():
            raise AssertionError("境不应读取发送者 UID")

    plugin = EnvironmentAwarenessPlugin(FakeContext(), AstrBotConfig())
    plugin.service.cached_calendar_awareness = lambda: (
        "2026-02-17",
        "[境·当地日历] 当地日期 2026-02-17，今天是春节。",
    )
    request = SimpleNamespace(system_prompt="base")
    asyncio.run(plugin.on_llm_request(FakeEvent(), request))
    assert "[境·当地日历]" in request.system_prompt
    asyncio.run(plugin.terminate())


def test_partial_source_failure_preserves_fresh_candidate_from_that_source():
    async def run():
        config = AstrBotConfig(
            {
                "default_location": "杭州",
                "opportunity_cache_enabled": True,
            }
        )
        plugin = EnvironmentAwarenessPlugin(FakeContext(), config)
        old = {
            "contract": "environment.opportunity",
            "version": "1.0",
            "event_key": "warning-old",
            "revision": "rev-old",
            "kind": "official_weather_warning",
            "severity": "critical",
            "severity_rank": 3,
            "facts": {"warning_level": "红色"},
            "location": {"name": "杭州", "timezone": "Asia/Shanghai"},
            "stale": False,
        }
        plugin._cached_opportunity = old
        plugin._opportunity_cached_at = __import__("time").monotonic()
        plugin._opportunity_location_setting = "杭州"

        async def alerts(*args):
            raise RuntimeError("warning provider unavailable")

        async def air(*args):
            return {
                "location": {"name": "杭州", "timezone": "Asia/Shanghai"},
                "payload": {"current": {}},
                "stale": False,
            }

        async def weather(*args):
            return {
                "location": {"name": "杭州", "timezone": "Asia/Shanghai"},
                "payload": {"daily": {}},
                "stale": False,
            }

        plugin.service.alerts_snapshot = alerts
        plugin.service.air_quality_snapshot = air
        plugin.service.weather_snapshot = weather
        refreshed = await plugin._refresh_opportunity()
        assert refreshed == old
        assert plugin.get_cached_opportunity(allow_stale=False) == old
        await plugin.terminate()

    asyncio.run(run())


def test_background_loop_exits_when_cache_and_proactive_delivery_are_disabled():
    plugin = EnvironmentAwarenessPlugin(
        FakeContext(),
        AstrBotConfig(
            {
                "default_location": "杭州",
                "opportunity_cache_enabled": False,
                "proactive_enabled": False,
            }
        ),
    )
    asyncio.run(asyncio.wait_for(plugin._opportunity_loop(), timeout=0.1))
    assert plugin._proactive_last_status == "disabled"
    asyncio.run(plugin.terminate())
