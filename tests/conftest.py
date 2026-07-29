from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _install_astrbot_stub() -> None:
    if "astrbot.core.agent.tool" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api_event = types.ModuleType("astrbot.api.event")
    api_star = types.ModuleType("astrbot.api.star")
    api_web = types.ModuleType("astrbot.api.web")
    core = types.ModuleType("astrbot.core")
    core.__path__ = []
    agent = types.ModuleType("astrbot.core.agent")
    agent.__path__ = []
    tool = types.ModuleType("astrbot.core.agent.tool")

    class FunctionTool:
        pass

    class AstrBotConfig(dict):
        def save_config(self):
            return None

    class Star:
        def __init__(self, context=None, *args, **kwargs):
            self.context = context

    class _Filter:
        @staticmethod
        def command(*args, **kwargs):
            return lambda function: function

        @staticmethod
        def on_llm_request(*args, **kwargs):
            return lambda function: function

    def register(*args, **kwargs):
        return lambda cls: cls

    def json_response(payload, status_code=200):
        return {"payload": payload, "status_code": status_code}

    def error_response(message, status_code=400):
        return {"error": message, "status_code": status_code}

    tool.FunctionTool = FunctionTool
    tool.ToolExecResult = str
    api.AstrBotConfig = AstrBotConfig
    api.logger = logging.getLogger("environment-awareness-tests")
    api_event.AstrMessageEvent = object
    api_event.filter = _Filter()
    api_star.Context = object
    api_star.Star = Star
    api_star.register = register
    api_web.json_response = json_response
    api_web.error_response = error_response
    api_web.request = types.SimpleNamespace()
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.api.event", api_event)
    sys.modules.setdefault("astrbot.api.star", api_star)
    sys.modules.setdefault("astrbot.api.web", api_web)
    sys.modules.setdefault("astrbot.core", core)
    sys.modules.setdefault("astrbot.core.agent", agent)
    sys.modules.setdefault("astrbot.core.agent.tool", tool)


_install_astrbot_stub()
