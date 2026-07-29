from __future__ import annotations

import asyncio
from typing import Any

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.web import error_response, json_response, request

    _WEB_AVAILABLE = True
except ImportError:
    _WEB_AVAILABLE = False

from .core.cache import AsyncTTLCache
from .core.formatters import (
    format_air_quality,
    format_alerts,
    format_calendar,
    format_datetime,
    format_weather,
)
from .core.providers import OpenDataProvider
from .core.service import EnvironmentService
from .core.settings import EnvironmentSettings
from .tools import create_tools

PLUGIN_NAME = "astrbot_plugin_environment_awareness"
PLUGIN_VERSION = "0.1.0"
_TOOL_NAMES = {
    "get_local_datetime",
    "get_local_calendar",
    "get_weather",
    "get_air_quality",
    "get_environment_alerts",
    "list_environment_locations",
}


@register(
    PLUGIN_NAME,
    "凌溪",
    "凝心溯溪-境：按需感知本地时间、天气和与设定地点相关的自然事件",
    PLUGIN_VERSION,
    "https://github.com/qsbb/astrbot_plugin_environment_awareness",
)
class EnvironmentAwarenessPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        settings = EnvironmentSettings.from_mapping(config)
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    f"{PLUGIN_NAME}/{PLUGIN_VERSION} "
                    "(+https://github.com/qsbb/astrbot_plugin_environment_awareness)"
                )
            },
        )
        self._cache = AsyncTTLCache()
        self._provider = OpenDataProvider(self._http_client)
        self.service = EnvironmentService(config, self._provider, self._cache)
        self._tools = create_tools(self)
        self._calendar_awareness_seen: set[tuple[str, str]] = set()
        self.context.add_llm_tools(*self._tools)

        if _WEB_AVAILABLE:
            try:
                self._register_page_apis()
            except Exception as exc:
                logger.warning("境的页面 API 注册失败: %s", exc)

        logger.info(
            "凝心溯溪-境 %s 已加载 | default_location=%s | "
            "prompt_injection=significant-calendar-once | active_push=off",
            PLUGIN_VERSION,
            settings.default_location or "未设置",
        )

    @filter.command("environment", alias={"境"})
    async def environment_status(self, event: AstrMessageEvent):
        """查看境的配置与数据源状态。"""
        diagnostics = self.service.diagnostics()
        location = diagnostics["default_location"] or "未设置"
        yield event.plain_result(
            "凝心溯溪-境\n"
            f"常驻地点：{location}\n"
            "数据源：Open-Meteo / 中央气象台 / USGS（无需 API Key）\n"
            "模式：按需调用；重要日仅当地当天首次轻量感知；不主动推送\n"
            "命令：/境时间、/境日历、/境天气、/境空气、/境预警"
        )

    @filter.command("env_time", alias={"境时间"})
    async def environment_time(self, event: AstrMessageEvent, location: str = ""):
        """查询当地时间。"""
        try:
            snapshot = await self.service.datetime_snapshot(location)
            yield event.plain_result(format_datetime(snapshot))
        except Exception as exc:
            yield event.plain_result(f"时间查询失败：{str(exc)[:200]}")

    @filter.command("env_calendar", alias={"境日历"})
    async def environment_calendar(
        self,
        event: AstrMessageEvent,
        location: str = "",
        date_text: str = "",
    ):
        """查询当地节假日、调休与工作日。"""
        try:
            snapshot = await self.service.calendar_snapshot(location, date_text)
            yield event.plain_result(format_calendar(snapshot))
        except Exception as exc:
            yield event.plain_result(f"日历查询失败：{str(exc)[:200]}")

    @filter.command("env_weather", alias={"境天气"})
    async def environment_weather(
        self,
        event: AstrMessageEvent,
        location: str = "",
        forecast_range: str = "current",
    ):
        """查询当前天气或预报。"""
        try:
            snapshot = await self.service.weather_snapshot(
                location, forecast_range, self.service.settings().forecast_days
            )
            yield event.plain_result(format_weather(snapshot))
        except Exception as exc:
            yield event.plain_result(f"天气查询失败：{str(exc)[:200]}")

    @filter.command("env_air", alias={"境空气"})
    async def environment_air(
        self,
        event: AstrMessageEvent,
        location: str = "",
        forecast_hours: int = 0,
    ):
        """查询空气质量、紫外线与花粉。"""
        try:
            snapshot = await self.service.air_quality_snapshot(
                location, forecast_hours
            )
            yield event.plain_result(format_air_quality(snapshot))
        except Exception as exc:
            yield event.plain_result(f"空气质量查询失败：{str(exc)[:200]}")

    @filter.command("env_alerts", alias={"境预警"})
    async def environment_alerts(
        self, event: AstrMessageEvent, location: str = "", hours: int = 24
    ):
        """查询与地点相关的环境风险。"""
        try:
            snapshot = await self.service.alerts_snapshot(location, hours)
            yield event.plain_result(format_alerts(snapshot))
        except Exception as exc:
            yield event.plain_result(f"环境风险查询失败：{str(exc)[:200]}")

    # 情 600 之后、言 500 之前：只补离线日历事实，不改变权限与表达约束。
    @filter.on_llm_request(priority=550)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: Any, *args: Any, **kwargs: Any
    ) -> None:
        """只在重大节假日或调休工作日的当地当天首次提供轻量事实。"""
        del args, kwargs
        try:
            text = str(event.get_message_str() or "").strip()
        except Exception:
            text = ""
        if text.startswith("/"):
            return
        awareness = self.service.cached_calendar_awareness()
        if awareness is None:
            return
        local_date, fragment = awareness
        origin = getattr(event, "unified_msg_origin", "")
        if callable(origin):
            try:
                origin = origin()
            except Exception:
                origin = ""
        if not origin:
            try:
                origin = event.get_sender_id()
            except Exception:
                origin = "unknown"
        seen_key = (str(origin), local_date)
        if seen_key in self._calendar_awareness_seen:
            return
        prompt = str(getattr(req, "system_prompt", "") or "")
        if "[境·当地日历]" not in prompt:
            req.system_prompt = f"{prompt}\n\n{fragment}" if prompt else fragment
        self._calendar_awareness_seen.add(seen_key)
        if len(self._calendar_awareness_seen) > 4096:
            self._calendar_awareness_seen = {seen_key}

    def _register_page_apis(self) -> None:
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/status",
            self._page_status,
            ["GET"],
            "获取境的运行状态",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/setup",
            self._page_setup,
            ["POST"],
            "保存并校验常驻地点",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/probe",
            self._page_probe,
            ["POST"],
            "测试境的数据源与本地相关性",
        )

    async def _page_status(self):
        return json_response(
            {
                "plugin": {
                    "name": PLUGIN_NAME,
                    "display_name": "凝心溯溪-境",
                    "version": PLUGIN_VERSION,
                },
                **self.service.diagnostics(),
            }
        )

    async def _page_setup(self):
        payload = await request.json(default={}) or {}
        if not isinstance(payload, dict):
            return error_response("请求格式错误", status_code=400)
        location = str(payload.get("default_location") or "").strip()
        if not location:
            return error_response("请填写常驻城市或经度,纬度", status_code=400)
        if len(location) > 100:
            return error_response("地点名称过长", status_code=400)
        try:
            resolved = await self.service.resolve_location(location)
        except Exception as exc:
            return error_response(f"地点校验失败：{str(exc)[:180]}", status_code=400)
        self.config["default_location"] = location
        self.service.remember_default_location(location, resolved)
        return json_response(
            {
                "ok": True,
                "default_location": location,
                "resolved": resolved.public_dict(),
            }
        )

    async def _page_probe(self):
        payload = await request.json(default={}) or {}
        if not isinstance(payload, dict):
            return error_response("请求格式错误", status_code=400)
        location = str(payload.get("location") or "").strip()
        names = ("weather", "air_quality", "calendar", "alerts")
        results = await asyncio.gather(
            self.service.weather_snapshot(location, "current", 1),
            self.service.air_quality_snapshot(location, 0),
            self.service.calendar_snapshot(location, ""),
            self.service.alerts_snapshot(location, 24),
            return_exceptions=True,
        )
        component_errors = {
            name: str(result)[:180]
            for name, result in zip(names, results, strict=True)
            if isinstance(result, Exception)
        }
        successful = [result for result in results if isinstance(result, dict)]
        if not successful:
            return error_response(
                "数据源测试失败：所有组件均不可用", status_code=502
            )
        weather = results[0] if isinstance(results[0], dict) else {}
        air_quality = results[1] if isinstance(results[1], dict) else {}
        calendar = results[2] if isinstance(results[2], dict) else {}
        alerts = results[3] if isinstance(results[3], dict) else {}
        current = weather.get("payload", {}).get("current", {})
        return json_response(
            {
                "ok": True,
                "location": next(
                    (
                        item.get("location", {})
                        for item in successful
                        if item.get("location")
                    ),
                    {},
                ),
                "component_errors": component_errors,
                "weather": {
                    "temperature": current.get("temperature_2m"),
                    "weather_code": current.get("weather_code"),
                    "observed_at": weather.get("observed_at"),
                    "stale": weather.get("stale", False),
                },
                "air_quality": {
                    "european_aqi": (
                        air_quality.get("payload", {})
                        .get("current", {})
                        .get("european_aqi")
                    ),
                    "uv_index": (
                        air_quality.get("payload", {})
                        .get("current", {})
                        .get("uv_index")
                    ),
                    "pollen_available": air_quality.get("availability", {}).get(
                        "pollen", False
                    ),
                },
                "calendar": {
                    "date": calendar.get("date"),
                    "day_type": calendar.get("day_type"),
                    "holiday_name": calendar.get("holiday_name"),
                },
                "alerts": {
                    "status": alerts.get("status"),
                    "weather_signal_count": len(
                        alerts.get("weather_risk_signals") or []
                    ),
                    "earthquake_count": len(alerts.get("earthquakes") or []),
                    "official_warning_count": len(
                        alerts.get("official_weather_warnings") or []
                    ),
                    "official_warning_status": alerts.get(
                        "official_warning_status"
                    ),
                    "provider_errors": alerts.get("provider_errors") or {},
                },
            }
        )

    def plugin_health(self) -> dict[str, object]:
        diagnostics = self.service.diagnostics()
        return {
            "contract": "plugin.health@1.0",
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "healthy": True,
            "checks": {
                "http_client_ready": not self._http_client.is_closed,
                "tools_registered": len(self._tools) == 6,
                "default_location_configured": bool(
                    diagnostics.get("default_location")
                ),
            },
        }

    def _cleanup_tools(self) -> None:
        unregister = getattr(self.context, "unregister_llm_tool", None)
        if callable(unregister):
            for name in _TOOL_NAMES:
                try:
                    unregister(name)
                except Exception:
                    pass

    async def terminate(self):
        self._cleanup_tools()
        await self._cache.clear()
        await self._http_client.aclose()
        logger.info("凝心溯溪-境已卸载，缓存、工具和 HTTP 会话已回收")
