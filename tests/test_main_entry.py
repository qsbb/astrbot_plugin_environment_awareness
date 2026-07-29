from __future__ import annotations

import asyncio
from types import SimpleNamespace

from astrbot.api import AstrBotConfig
from astrbot_plugin_environment_awareness.main import EnvironmentAwarenessPlugin


class FakeContext:
    def __init__(self):
        self.tools = []
        self.routes = []
        self.removed = []

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def register_web_api(self, route, handler, methods, description):
        self.routes.append((route, tuple(methods), description))

    def unregister_llm_tool(self, name):
        self.removed.append(name)


def test_plugin_initialization_registers_tools_and_page_apis_without_network():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    assert len(context.tools) == 6
    assert len(context.routes) == 3
    assert plugin.service.diagnostics()["provider_last_success"] == {}
    asyncio.run(plugin.terminate())


def test_plugin_health_reports_optional_location_separately_from_health():
    context = FakeContext()
    plugin = EnvironmentAwarenessPlugin(context, AstrBotConfig())
    health = plugin.plugin_health()
    assert health["healthy"] is True
    assert health["checks"]["default_location_configured"] is False
    assert health["checks"]["tools_registered"] is True
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
