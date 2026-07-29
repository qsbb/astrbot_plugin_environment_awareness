from __future__ import annotations

import asyncio
import copy
import inspect
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

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
from .core.opportunity import select_opportunity, severity_at_least
from .core.proactive import ProactiveDeliveryState, local_delivery_window
from .core.providers import OpenDataProvider
from .core.service import EnvironmentService
from .core.settings import EnvironmentSettings
from .tools import create_tools

PLUGIN_NAME = "astrbot_plugin_environment_awareness"
PLUGIN_VERSION = "0.1.1"
_TOOL_NAMES = {
    "get_local_datetime",
    "get_local_calendar",
    "get_weather",
    "get_air_quality",
    "get_environment_alerts",
    "list_environment_locations",
}
CONVERSATION_FLOW_PLUGIN_NAME = "astrbot_plugin_conversation_flow"
PROACTIVE_DELIVERY_CONTRACT_NAME = "conversation.proactive_delivery"
PROACTIVE_DELIVERY_CONTRACT_MAJOR = "1"
_ALERT_OPPORTUNITY_KINDS = frozenset(
    {
        "official_weather_warning",
        "earthquake",
        "heavy_rain_forecast",
        "strong_wind_forecast",
        "extreme_heat_forecast",
        "extreme_cold_forecast",
        "thunderstorm_forecast",
    }
)
_AIR_OPPORTUNITY_KINDS = frozenset({"high_air_quality_index", "high_uv_index"})


@register(
    PLUGIN_NAME,
    "凌溪",
    "凝心溯溪-境：按需感知本地时间、天气和与设定地点相关的自然事件",
    PLUGIN_VERSION,
    "https://github.com/qsbb/astrbot_plugin_environment_awareness",
)
class EnvironmentAwarenessPlugin(Star):
    PLUGIN_HEALTH_CONTRACT = "plugin.health@1.0"

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
        self._opportunity_awareness_seen: set[tuple[str, str]] = set()
        self._cached_opportunity: dict[str, Any] | None = None
        self._opportunity_cached_at = 0.0
        self._opportunity_location_setting = ""
        self._opportunity_last_refresh = ""
        self._opportunity_last_error = ""
        self._proactive_last_status = "disabled"
        self._background_task: asyncio.Task[None] | None = None
        self._stopping = False
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self._delivery_state = ProactiveDeliveryState(
            f"{data_dir}/proactive-delivery-state.json"
        )
        self.context.add_llm_tools(*self._tools)

        if _WEB_AVAILABLE:
            try:
                self._register_page_apis()
            except Exception as exc:
                logger.warning("境的页面 API 注册失败: %s", exc)

        self._ensure_background_task()
        logger.info(
            "凝心溯溪-境 %s 已加载 | default_location=%s | "
            "opportunity_cache=%s | active_push=%s",
            PLUGIN_VERSION,
            settings.default_location or "未设置",
            "on" if settings.opportunity_cache_enabled else "off",
            "on" if settings.proactive_enabled else "off",
        )

    def environment_opportunity_contract(self) -> dict[str, object]:
        """Declare a cache-only neutral-fact contract for sibling plugins."""
        return {
            "name": "environment.opportunity",
            "version": "1.0",
            "plugin": PLUGIN_NAME,
            "capabilities": ("cached_read", "background_refresh"),
            "identity_fields": False,
            "request_hook_network": False,
        }

    def get_cached_opportunity(
        self, *, allow_stale: bool = True
    ) -> dict[str, Any] | None:
        candidate = self._cached_opportunity
        if not isinstance(candidate, dict):
            return None
        settings = self.service.settings()
        if (
            self._opportunity_location_setting
            and self._opportunity_location_setting != settings.default_location
        ):
            return None
        age = max(0.0, time.monotonic() - self._opportunity_cached_at)
        fresh_for = float(settings.opportunity_refresh_seconds * 2)
        if age > fresh_for + settings.stale_cache_seconds:
            return None
        result = copy.deepcopy(candidate)
        if age > fresh_for:
            result["stale"] = True
        if result.get("stale") and not allow_stale:
            return None
        return result

    def _ensure_background_task(self) -> None:
        if self._stopping:
            return
        settings = self.service.settings()
        if not settings.default_location or not (
            settings.opportunity_cache_enabled or settings.proactive_enabled
        ):
            return
        if self._background_task is not None and not self._background_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._background_task = loop.create_task(
            self._opportunity_loop(), name="environment-opportunity-refresh"
        )

    async def _opportunity_loop(self) -> None:
        while not self._stopping:
            settings = self.service.settings()
            if not settings.default_location or not (
                settings.opportunity_cache_enabled or settings.proactive_enabled
            ):
                self._proactive_last_status = "disabled"
                return
            try:
                await self._refresh_opportunity()
                await self._maybe_send_proactive()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._opportunity_last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
                logger.warning("境的后台环境候选刷新失败: %s", exc)
            interval = self.service.settings().opportunity_refresh_seconds
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def _refresh_opportunity(self) -> dict[str, Any] | None:
        settings = self.service.settings()
        if not settings.default_location:
            self._opportunity_last_error = "default_location_required"
            return self.get_cached_opportunity()
        alerts, air_quality, weather = await asyncio.gather(
            self.service.alerts_snapshot("", 24),
            self.service.air_quality_snapshot("", 0),
            self.service.weather_snapshot("", "daily", 2),
            return_exceptions=True,
        )
        successful = [
            value for value in (alerts, air_quality, weather) if isinstance(value, dict)
        ]
        if not successful:
            errors = ", ".join(
                type(value).__name__
                for value in (alerts, air_quality, weather)
                if isinstance(value, Exception)
            )
            self._opportunity_last_error = errors or "all_sources_unavailable"
            return self.get_cached_opportunity()
        candidate = select_opportunity(
            alerts if isinstance(alerts, dict) else None,
            air_quality if isinstance(air_quality, dict) else None,
            weather if isinstance(weather, dict) else None,
            minimum_severity=settings.opportunity_min_severity,
            european_aqi_threshold=settings.opportunity_european_aqi_threshold,
            us_aqi_threshold=settings.opportunity_us_aqi_threshold,
            uv_threshold=settings.opportunity_uv_threshold,
            temperature_drop_c=settings.opportunity_temperature_drop_c,
        )
        previous = self.get_cached_opportunity(allow_stale=False)
        if previous is not None:
            previous_kind = str(previous.get("kind") or "")
            source_unavailable = (
                (
                    previous_kind in _ALERT_OPPORTUNITY_KINDS
                    and (not isinstance(alerts, dict) or bool(alerts.get("stale")))
                )
                or (
                    previous_kind in _AIR_OPPORTUNITY_KINDS
                    and (
                        not isinstance(air_quality, dict)
                        or bool(air_quality.get("stale"))
                    )
                )
                or (
                    previous_kind == "strong_temperature_drop"
                    and (not isinstance(weather, dict) or bool(weather.get("stale")))
                )
            )
            previous_rank = int(previous.get("severity_rank") or -1)
            candidate_rank = int((candidate or {}).get("severity_rank") or -1)
            if source_unavailable and previous_rank >= candidate_rank:
                self._opportunity_last_error = "source_unavailable_preserved_candidate"
                return previous
        if candidate is not None:
            now = datetime.now(UTC)
            candidate["valid_from"] = now.isoformat()
            candidate["valid_until"] = (
                now + timedelta(seconds=settings.opportunity_refresh_seconds * 2)
            ).isoformat()
        self._cached_opportunity = candidate
        self._opportunity_cached_at = time.monotonic()
        self._opportunity_location_setting = settings.default_location
        self._opportunity_last_refresh = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        component_errors = [
            type(value).__name__
            for value in (alerts, air_quality, weather)
            if isinstance(value, Exception)
        ]
        self._opportunity_last_error = ", ".join(component_errors)
        return copy.deepcopy(candidate) if candidate else None

    def _get_plugin_instance(self, plugin_name: str) -> Any | None:
        getter = getattr(self.context, "get_star_instance", None)
        if not callable(getter):
            return None
        try:
            return getter(plugin_name)
        except Exception as exc:
            logger.debug("境查询协同插件 %s 失败: %s", plugin_name, exc)
            return None

    @staticmethod
    def _compatible_contract(
        provider: Any, declaration: str, name: str, major: str
    ) -> bool:
        declare = getattr(provider, declaration, None)
        if not callable(declare):
            return False
        try:
            contract = declare()
        except Exception:
            return False
        if not isinstance(contract, dict):
            return False
        version = str(contract.get("version") or "")
        return contract.get("name") == name and version.split(".", 1)[0] == major

    async def _flow_reply_fragment(
        self, candidate: dict[str, Any], person_id: str, recipient_umo: str
    ) -> str:
        flow = self._get_plugin_instance(CONVERSATION_FLOW_PLUGIN_NAME)
        if flow is None or not self._compatible_contract(
            flow,
            "proactive_delivery_contract",
            PROACTIVE_DELIVERY_CONTRACT_NAME,
            PROACTIVE_DELIVERY_CONTRACT_MAJOR,
        ):
            return ""
        prepare = getattr(flow, "prepare_environment_reply_context", None)
        if not callable(prepare):
            return ""
        try:
            result = prepare(candidate, person_id, recipient_umo)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.debug("境请求言准备环境候选失败: %s", exc)
            return ""
        if not isinstance(result, dict) or not result.get("allowed"):
            return ""
        return str(result.get("prompt_fragment") or "").strip()

    async def _maybe_send_proactive(self) -> None:
        settings = self.service.settings()
        if not settings.proactive_enabled:
            self._proactive_last_status = "disabled"
            return
        if settings.proactive_paused:
            self._proactive_last_status = "paused"
            return
        if not settings.care_person_id or not settings.care_recipient_umo:
            self._proactive_last_status = "recipient_not_configured"
            return
        candidate = self.get_cached_opportunity(allow_stale=False)
        if candidate is None:
            self._proactive_last_status = "no_fresh_candidate"
            return
        if not severity_at_least(
            str(candidate.get("severity") or ""), settings.proactive_min_severity
        ):
            self._proactive_last_status = "below_severity_threshold"
            return
        location = candidate.get("location") or {}
        timezone_name = str(location.get("timezone") or "UTC")
        local_date, quiet = local_delivery_window(
            timezone_name,
            settings.proactive_quiet_start,
            settings.proactive_quiet_end,
        )
        if quiet:
            self._proactive_last_status = "quiet_hours"
            return
        event_key = str(candidate.get("event_key") or "")
        severity = str(candidate.get("severity") or "")
        allowed, reason = self._delivery_state.can_send(
            event_key,
            severity,
            local_date,
            settings.proactive_daily_limit,
        )
        if not allowed:
            self._proactive_last_status = reason
            return
        flow = self._get_plugin_instance(CONVERSATION_FLOW_PLUGIN_NAME)
        if flow is None or not self._compatible_contract(
            flow,
            "proactive_delivery_contract",
            PROACTIVE_DELIVERY_CONTRACT_NAME,
            PROACTIVE_DELIVERY_CONTRACT_MAJOR,
        ):
            self._proactive_last_status = "conversation_flow_unavailable"
            return
        deliver = getattr(flow, "deliver_environment_opportunity", None)
        if not callable(deliver):
            self._proactive_last_status = "delivery_method_unavailable"
            return
        try:
            result = deliver(
                candidate,
                settings.care_person_id,
                settings.care_recipient_umo,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self._proactive_last_status = f"delivery_error:{type(exc).__name__}"
            logger.warning("境请求言发送环境关心失败: %s", exc)
            return
        if isinstance(result, dict) and result.get("sent") is True:
            self._delivery_state.mark_sent(
                event_key,
                severity,
                str(candidate.get("revision") or ""),
                local_date,
            )
            self._proactive_last_status = "sent"
        else:
            reason = str(
                result.get("reason") if isinstance(result, dict) else "suppressed"
            )[:80]
            if reason in {
                "dialogue_model_suppressed",
                "empty_message",
                "service_followup_rejected",
                "internal_reference_rejected",
            }:
                self._delivery_state.mark_evaluated(
                    event_key,
                    severity,
                    str(candidate.get("revision") or ""),
                )
            self._proactive_last_status = reason

    @filter.command("environment", alias={"境"})
    async def environment_status(self, event: AstrMessageEvent):
        """查看境的配置与数据源状态。"""
        diagnostics = self._runtime_diagnostics()
        location = diagnostics["default_location"] or "未设置"
        candidate = diagnostics["opportunity_cache"].get("candidate")
        candidate_text = (
            f"{candidate.get('severity')} / {candidate.get('kind')}"
            if isinstance(candidate, dict)
            else "暂无"
        )
        yield event.plain_result(
            "凝心溯溪-境\n"
            f"常驻地点：{location}\n"
            "数据源：Open-Meteo / 中央气象台 / USGS（无需 API Key）\n"
            f"环境关心候选：{candidate_text}\n"
            f"主动消息：{diagnostics['proactive_delivery']['status']}\n"
            "模式：按需工具；普通回复只读后台缓存，不同步联网\n"
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
            snapshot = await self.service.air_quality_snapshot(location, forecast_hours)
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
        """只读离线/后台缓存，按需提供一次轻量环境事实。"""
        del args, kwargs
        self._ensure_background_task()
        try:
            text = str(event.get_message_str() or "").strip()
        except Exception:
            text = ""
        if text.startswith("/"):
            return
        origin = getattr(event, "unified_msg_origin", "")
        if callable(origin):
            try:
                origin = origin()
            except Exception:
                origin = ""
        origin = str(origin or "").strip()
        calendar_scope = origin or "calendar:unknown-session"
        prompt = str(getattr(req, "system_prompt", "") or "")

        awareness = self.service.cached_calendar_awareness()
        if awareness is not None:
            local_date, fragment = awareness
            seen_key = (calendar_scope, local_date)
            if seen_key not in self._calendar_awareness_seen:
                if "[境·当地日历]" not in prompt:
                    prompt = f"{prompt}\n\n{fragment}" if prompt else fragment
                self._calendar_awareness_seen.add(seen_key)
                if len(self._calendar_awareness_seen) > 4096:
                    self._calendar_awareness_seen = {seen_key}

        settings = self.service.settings()
        candidate = self.get_cached_opportunity(allow_stale=False)
        if (
            candidate is not None
            and bool(origin)
            and settings.care_person_id
            and settings.care_recipient_umo
            and str(origin) == settings.care_recipient_umo
        ):
            event_key = str(candidate.get("event_key") or "")
            seen_key = (str(origin), event_key)
            if event_key and seen_key not in self._opportunity_awareness_seen:
                fragment = await self._flow_reply_fragment(
                    candidate,
                    settings.care_person_id,
                    settings.care_recipient_umo,
                )
                if fragment and "[境·环境关心候选]" not in prompt:
                    prompt = f"{prompt}\n\n{fragment}" if prompt else fragment
                    self._opportunity_awareness_seen.add(seen_key)
                    if len(self._opportunity_awareness_seen) > 4096:
                        self._opportunity_awareness_seen = {seen_key}

        req.system_prompt = prompt

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
                **self._runtime_diagnostics(),
            }
        )

    def _runtime_diagnostics(self) -> dict[str, Any]:
        diagnostics = self.service.diagnostics()
        settings = self.service.settings()
        candidate = self.get_cached_opportunity(allow_stale=True)
        diagnostics.update(
            {
                "opportunity_cache": {
                    "enabled": settings.opportunity_cache_enabled,
                    "background_task_running": bool(
                        self._background_task is not None
                        and not self._background_task.done()
                    ),
                    "last_refresh": self._opportunity_last_refresh or None,
                    "last_error": self._opportunity_last_error or None,
                    "candidate": (
                        {
                            "kind": candidate.get("kind"),
                            "severity": candidate.get("severity"),
                            "stale": bool(candidate.get("stale")),
                            "observed_at": candidate.get("observed_at"),
                        }
                        if candidate
                        else None
                    ),
                    "request_hook_network": False,
                },
                "proactive_delivery": {
                    "enabled": settings.proactive_enabled,
                    "paused": settings.proactive_paused,
                    "recipient_configured": bool(
                        settings.care_person_id and settings.care_recipient_umo
                    ),
                    "status": self._proactive_last_status,
                    "minimum_severity": settings.proactive_min_severity,
                    "daily_limit": settings.proactive_daily_limit,
                    "quiet_hours": (
                        f"{settings.proactive_quiet_start}-"
                        f"{settings.proactive_quiet_end}"
                    ),
                    "state": self._delivery_state.snapshot(),
                },
                "active_push": settings.proactive_enabled,
            }
        )
        return diagnostics

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
        self._cached_opportunity = None
        self._opportunity_cached_at = 0.0
        self._opportunity_location_setting = ""
        self._opportunity_awareness_seen.clear()
        task = self._background_task
        self._background_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._ensure_background_task()
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
            return error_response("数据源测试失败：所有组件均不可用", status_code=502)
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
                    "official_warning_status": alerts.get("official_warning_status"),
                    "provider_errors": alerts.get("provider_errors") or {},
                },
            }
        )

    def plugin_health(self) -> dict[str, object]:
        checks = {
            "http_client_ready": not self._http_client.is_closed,
            "tools_registered": len(self._tools) == 6,
        }
        reasons = [name.upper() for name, passed in checks.items() if not passed]
        return {
            "status": "ok" if not reasons else "unhealthy",
            "checks": checks,
            "reasons": reasons,
            "version": PLUGIN_VERSION,
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
        self._stopping = True
        task = self._background_task
        self._background_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._cleanup_tools()
        await self._cache.clear()
        await self._http_client.aclose()
        logger.info("凝心溯溪-境已卸载，缓存、工具和 HTTP 会话已回收")
