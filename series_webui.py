"""``series.webui@2.0`` adapter for environment awareness.

The adapter only presents and validates data.  Configuration writes still go
through the existing page/config helpers or ``series.control`` implementation,
so both standalone and managed surfaces share one state owner.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections.abc import Mapping
from typing import Any

CONTROL_FIELDS = frozenset(
    {
        "proactive_enabled",
        "opportunity_cache_enabled",
        "opportunity_refresh_seconds",
    }
)
SECRET_KEY_PARTS = ("password", "secret", "token", "api_key", "apikey", "credential")
PROBE_COOLDOWN_SECONDS = 5.0
MAX_LOCATION_CHARS = 100
MAX_DATE_CHARS = 10
MAX_PROBE_RESULT_CHARS = 180

_PROBE_COMPONENT_LABELS = {
    "weather": "天气",
    "air": "空气质量",
    "air_quality": "空气质量",
    "calendar": "日历",
    "alerts": "预警聚合",
}
_PROBE_COMPONENT_IMPACTS = {
    "weather": "气温与天气信号不可用",
    "air": "空气质量与紫外线不可用",
    "air_quality": "空气质量与紫外线不可用",
    "calendar": "当地日历不可用",
    "alerts": "预警与地震信息不可用",
}
# 单源探测失败时快照里只存内部代号，上屏前换成用户能看懂的说法。
_PROBE_REASON_ALIASES = {
    "PROBE_FAILED": "本次探测失败",
    "RATE_LIMITED": "触发限流，稍后再试",
    "TIMEOUT": "请求超时",
}
_MAX_PROBE_FAILURE_ROWS = 6
_PROBE_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_PROBE_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_-]?key|apikey|secret|password|authorization|cookie|"
    r"credential|signature|(?:access|private)[_-]?key)\b(\s*[=:]\s*)([^\s&,;，；]+)"
)


def _sanitize_probe_reason(value: Any) -> str:
    """把失败原因压成一行展示文本，并剥掉 URL 查询串与常见凭据赋值。"""
    text = " ".join(str(value or "").split())
    if not text:
        return "未知原因"

    def _strip_query(match: re.Match[str]) -> str:
        url = match.group(0)
        return url.split("?", 1)[0].split("#", 1)[0] or "<url>"

    text = _PROBE_URL_PATTERN.sub(_strip_query, text)
    text = _PROBE_SECRET_ASSIGNMENT.sub(r"\1=<已隐藏>", text)
    return text if len(text) <= 120 else text[:119] + "…"

_USAGE_SOURCE_LABELS = {
    "command": "手动命令",
    "llm_tool": "AI 工具调用",
    "awareness": "轻量感知",
    "proactive": "主动关心",
}
_USAGE_STATUS_LABELS = {
    "success": "成功",
    "error": "失败",
    "suppressed": "未发送",
}
_CALENDAR_DAY_TYPE_LABELS = {
    "adjusted_workday": "调休工作日",
    "weekend": "周末",
    "working_day": "工作日",
    "workday": "工作日",
    "day_off": "休息日",
}


def _now_text() -> str:
    """本地时间文本，仅用于面板展示最近一次探测时间。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _secret_field(name: str, field: Mapping[str, Any]) -> bool:
    if bool(field.get("secret")):
        return True
    lowered = name.casefold()
    return any(part in lowered for part in SECRET_KEY_PARTS)


def _field_type(field: Mapping[str, Any]) -> str:
    kind = str(field.get("type") or "string")
    if kind in {"bool", "boolean"}:
        return "bool"
    if kind in {"int", "float"}:
        return "number"
    if field.get("options"):
        return "select"
    return "text"


def _public_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value)
    return str(value)


class EnvironmentWebUIAdapter:
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._last_probe: dict[str, float] = {}
        self._last_probe_snapshot: dict[str, Any] = {}

    # -- contract ---------------------------------------------------------

    def contract(self) -> dict[str, object]:
        return {
            "name": "series.webui@2.0",
            "version": "2.0",
            "series_id": "ningxin_suxi",
            "plugin_id": "astrbot_plugin_environment_awareness",
            "state_owner": "plugin",
            "preferred_surface": "kernel",
            "managed": {"supported": True, "level": "actions"},
            "standalone": {"available": True, "pages": ["status"]},
            "capabilities": [
                "generic_table",
                "generic_actions",
                "revision",
                "idempotency",
            ],
            "panels": [
                {
                    "id": "status",
                    "title": "环境状态",
                    "description": "只读查看地点、缓存与主动关心状态",
                    "actions": [],
                },
                {
                    "id": "settings",
                    "title": "环境设置",
                    "description": "维护日常配置、主动关心窗口和数据源连通性",
                    "actions": [
                        self._action_declaration("save_config"),
                        self._action_declaration("set_control"),
                        self._action_declaration("set_default_location"),
                        self._action_declaration("probe_weather"),
                        self._action_declaration("probe_air"),
                        self._action_declaration("probe_calendar"),
                    ],
                },
                {
                    "id": "probe",
                    "title": "实时检查",
                    "description": "按需检查天气、空气质量、日历与官方预警数据源",
                    "actions": [
                        self._action_declaration("probe_weather"),
                        self._action_declaration("probe_air"),
                        self._action_declaration("probe_calendar"),
                        self._action_declaration("probe_all"),
                    ],
                },
            ],
        }

    def _action_declaration(self, action: str) -> dict[str, Any]:
        declarations = self._action_declarations()
        return declarations[action]

    def _action_declarations(self) -> dict[str, dict[str, Any]]:
        schema = self.plugin._page_schema()
        editable = []
        for name, field in schema.items():
            if name in CONTROL_FIELDS or name == "default_location":
                continue
            if _secret_field(name, field):
                continue
            item: dict[str, Any] = {
                "name": name,
                "type": _field_type(field),
                "label": str(field.get("description") or name),
                "required": False,
                "hint": str(field.get("hint") or ""),
            }
            if field.get("options"):
                item["options"] = [
                    (str(value), str(value)) for value in field["options"]
                ]
            if field.get("default") is not None:
                item["default"] = field["default"]
            editable.append(item)
        return {
            "save_config": {
                "id": "save_config",
                "label": "保存环境配置",
                "confirm": "确定保存这些环境配置？",
                "effect": "idempotent",
                "revision_required": True,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 12,
                "payload_fields": editable,
            },
            "set_control": {
                "id": "set_control",
                "label": "应用统一接管字段",
                "confirm": "确定应用这些统一接管字段？",
                "effect": "idempotent",
                "revision_required": True,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 8,
                "payload_fields": [
                    {
                        "name": "proactive_enabled",
                        "type": "bool",
                        "label": "允许主动环境关心",
                        "required": False,
                    },
                    {
                        "name": "opportunity_cache_enabled",
                        "type": "bool",
                        "label": "后台刷新环境候选",
                        "required": False,
                    },
                    {
                        "name": "opportunity_refresh_seconds",
                        "type": "number",
                        "label": "候选刷新间隔（秒）",
                        "required": False,
                    },
                ],
            },
            "set_default_location": {
                "id": "set_default_location",
                "label": "校验并保存常驻地点",
                "confirm": "确定把该地点设为全局常驻地点？",
                "effect": "idempotent",
                "revision_required": True,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 12,
                "payload_fields": [
                    {
                        "name": "location",
                        "type": "text",
                        "label": "常驻城市或经度,纬度",
                        "required": True,
                        "hint": "最多 100 字符，保存前会调用现有地点解析服务校验。",
                    }
                ],
            },
            "probe_weather": {
                "id": "probe_weather",
                "label": "测试天气数据源",
                "effect": "idempotent",
                "revision_required": False,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 22,
                "payload_fields": self._probe_location_fields(),
            },
            "probe_air": {
                "id": "probe_air",
                "label": "测试空气质量数据源",
                "effect": "idempotent",
                "revision_required": False,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 22,
                "payload_fields": self._probe_location_fields(),
            },
            "probe_calendar": {
                "id": "probe_calendar",
                "label": "测试日历服务",
                "effect": "idempotent",
                "revision_required": False,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 12,
                "payload_fields": [
                    *self._probe_location_fields(),
                    {
                        "name": "date",
                        "type": "text",
                        "label": "日期（可选）",
                        "required": False,
                        "hint": "YYYY-MM-DD；留空使用目标地点当地日期。",
                    },
                ],
            },
            "probe_all": {
                "id": "probe_all",
                "label": "测试全部数据源",
                "confirm": "确定按当前地点测试全部环境数据源？",
                "effect": "idempotent",
                "revision_required": False,
                "idempotency_required": False,
                "min_role": "admin",
                "timeout_seconds": 30,
                "payload_fields": self._probe_location_fields(),
            },
        }

    @staticmethod
    def _probe_location_fields() -> list[dict[str, Any]]:
        return [
            {
                "name": "location",
                "type": "text",
                "label": "地点（可选）",
                "required": False,
                "hint": "留空使用当前常驻地点；最多 100 字符。",
            }
        ]

    def _declared_action(self, action: str) -> Mapping[str, Any]:
        declaration = self._action_declarations().get(action)
        if declaration is None:
            raise ValueError("UNKNOWN_ACTION")
        return declaration

    # -- data -------------------------------------------------------------

    def panel_data(self, panel: str) -> dict[str, Any]:
        if panel == "status":
            return self._status_data()
        if panel == "settings":
            return self._settings_data()
        if panel == "probe":
            return self._probe_data()
        return {"success": False, "error": "UNKNOWN_PANEL"}

    def _status_data(self) -> dict[str, Any]:
        diagnostics = self.plugin._runtime_diagnostics()
        settings = self.plugin.service.settings()
        candidate = self.plugin.get_cached_opportunity(allow_stale=True)
        opportunity = diagnostics.get("opportunity_cache")
        opportunity = opportunity if isinstance(opportunity, dict) else {}
        proactive = diagnostics.get("proactive_delivery")
        proactive = proactive if isinstance(proactive, dict) else {}
        filters = diagnostics.get("filters")
        filters = filters if isinstance(filters, dict) else {}
        usage = diagnostics.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        providers = diagnostics.get("zero_key_providers")
        providers_text = (
            " · ".join(str(item) for item in providers)
            if isinstance(providers, (list, tuple)) and providers
            else "—"
        )
        health = self.plugin.plugin_health()
        rows = [
            {
                "item": "运行状态",
                "value": "正常" if diagnostics.get("ready") else "异常",
            },
            {"item": "版本", "value": str(health.get("version") or "—")},
            {"item": "数据源", "value": providers_text},
            {"item": "模式", "value": "按需 + 后台候选"},
            {"item": "默认地点", "value": settings.default_location or "未配置"},
            {
                "item": "最低震级",
                "value": self._number_text(filters.get("earthquake_min_magnitude")),
            },
            {
                "item": "绝对最远距离",
                "value": self._distance_text(
                    filters.get("earthquake_max_distance_km")
                ),
            },
            {
                "item": "近场半径",
                "value": self._distance_text(
                    filters.get("earthquake_nearby_radius_km")
                ),
            },
            {
                "item": "重要日轻量感知",
                "value": "当地当天首次"
                if diagnostics.get("calendar_awareness_enabled")
                else "关闭",
            },
            {
                "item": "官方预警查询",
                "value": "开启"
                if filters.get("official_weather_warnings_enabled")
                else "关闭",
            },
            {
                "item": "主动关心",
                "value": "已启用" if settings.proactive_enabled else "未启用",
            },
            {
                "item": "主动提醒暂停",
                "value": "是" if settings.proactive_paused else "否",
            },
            {"item": "主动消息状态", "value": self._proactive_status(proactive)},
            {
                "item": "关心对象",
                "value": (
                    "已配置"
                    if proactive.get("recipient_configured")
                    else "未配置"
                ),
            },
            {
                "item": "每日上限",
                "value": self._integer_text(proactive.get("daily_limit")),
            },
            {
                "item": "安静时段",
                "value": str(proactive.get("quiet_hours") or "—"),
            },
            {
                "item": "机会缓存",
                "value": (
                    f"{candidate.get('kind') or '未知'} · "
                    f"{candidate.get('severity') or '未知'}"
                    + (" · 已过期" if candidate.get("stale") else "")
                    if candidate
                    else "无候选"
                ),
            },
            {
                "item": "后台刷新",
                "value": "运行中"
                if opportunity.get("background_task_running")
                else "未运行",
            },
            {
                "item": "最近刷新",
                "value": str(opportunity.get("last_refresh") or "无记录"),
            },
            {
                "item": "累计调用",
                "value": self._integer_text(usage.get("total")),
            },
            {
                "item": "调用成功",
                "value": self._integer_text(usage.get("successful")),
            },
            {
                "item": "调用失败",
                "value": self._integer_text(usage.get("failed")),
            },
            {"item": "最常用入口", "value": self._top_source_text(usage)},
            {"item": "最近调用", "value": self._recent_usage_text(usage)},
        ]
        return {
            "success": True,
            "title": "环境状态",
            "description": "运行状态只读；配置与连通性测试在“环境设置”面板。",
            "revision": self.revision(),
            "columns": [
                {"key": "item", "label": "项目"},
                {"key": "value", "label": "状态"},
            ],
            "rows": rows,
            "actions": [],
        }

    @staticmethod
    def _number_text(value: Any) -> str:
        if value is None or value == "":
            return "—"
        return str(value)

    @staticmethod
    def _distance_text(value: Any) -> str:
        if value is None or value == "":
            return "—"
        return f"{value} km"

    @staticmethod
    def _integer_text(value: Any) -> int | str:
        try:
            return int(value)
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _proactive_status(proactive: Mapping[str, Any]) -> str:
        if not proactive.get("enabled"):
            return "关闭"
        if proactive.get("paused"):
            return "已暂停"
        return str(proactive.get("status") or "等待检查")

    @staticmethod
    def _top_source_text(usage: Mapping[str, Any]) -> str:
        by_source = usage.get("by_source")
        if not isinstance(by_source, Mapping) or not by_source:
            return "—"
        try:
            source, count = max(
                by_source.items(), key=lambda item: int(item[1] or 0)
            )
        except (TypeError, ValueError):
            return "—"
        label = _USAGE_SOURCE_LABELS.get(str(source), str(source))
        return f"{label} · {count}"

    @staticmethod
    def _recent_usage_text(usage: Mapping[str, Any]) -> str:
        recent = usage.get("recent")
        if not isinstance(recent, (list, tuple)) or not recent:
            return "暂无记录"
        item = recent[0]
        if not isinstance(item, Mapping):
            return "暂无记录"
        source = str(item.get("source") or "")
        status = str(item.get("status") or "")
        parts = [
            str(item.get("timestamp") or ""),
            _USAGE_SOURCE_LABELS.get(source, source),
            str(item.get("action") or ""),
            _USAGE_STATUS_LABELS.get(status, status),
            f"{item.get('duration_ms') or 0} ms",
        ]
        return " · ".join(part for part in parts if part)

    def _probe_data(self) -> dict[str, Any]:
        return {
            "success": True,
            "title": "实时检查",
            "description": (
                "按需检查天气、空气质量、日历与官方预警数据源；结果仅展示摘要。"
            ),
            "revision": self.revision(),
            "columns": [
                {"key": "item", "label": "项目"},
                {"key": "value", "label": "结果"},
            ],
            "rows": self._probe_rows(self._last_probe_snapshot),
            "actions": self._probe_actions(),
            "footer": "单次探测间隔不少于 5 秒；探测结果不计入调用统计。",
        }

    def _probe_rows(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        weather = snapshot.get("weather")
        weather = weather if isinstance(weather, Mapping) else {}
        air = snapshot.get("air_quality")
        air = air if isinstance(air, Mapping) else {}
        calendar = snapshot.get("calendar")
        calendar = calendar if isinstance(calendar, Mapping) else {}
        alerts = snapshot.get("alerts")
        alerts = alerts if isinstance(alerts, Mapping) else {}
        component_errors = snapshot.get("component_errors")
        component_errors = (
            component_errors if isinstance(component_errors, Mapping) else {}
        )
        provider_errors = alerts.get("provider_errors")
        provider_errors = (
            provider_errors if isinstance(provider_errors, Mapping) else {}
        )
        rows = [
            {"item": "地点", "value": str(snapshot.get("location") or "尚未测试")},
            {"item": "天气", "value": self._weather_summary(weather)},
            {"item": "空气质量 / 紫外线", "value": self._air_summary(air)},
            {"item": "花粉数据", "value": self._pollen_summary(air)},
            {"item": "当地日历", "value": self._calendar_summary(calendar)},
            {
                "item": "相关天气信号",
                "value": self._count_text(alerts.get("weather_signal_count")),
            },
            {
                "item": "官方气象预警",
                "value": self._official_warning_summary(alerts),
            },
            {
                "item": "相关地震",
                "value": self._count_text(alerts.get("earthquake_count")),
            },
            {"item": "组件错误数", "value": len(component_errors)},
            {"item": "数据源错误数", "value": len(provider_errors)},
        ]
        rows.extend(self._probe_failure_rows(component_errors, provider_errors))
        rows.append(
            {
                "item": "最近测试",
                "value": str(snapshot.get("updated_at") or "无记录"),
            }
        )
        return rows

    def _probe_failure_rows(
        self, component_errors: Mapping[str, Any], provider_errors: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """为每个失败来源生成"哪坏了 + 什么影响 / 什么原因"的可操作明细行。"""
        rows: list[dict[str, Any]] = []
        for name, reason in component_errors.items():
            key = str(name)
            label = _PROBE_COMPONENT_LABELS.get(key, key)
            impact = _PROBE_COMPONENT_IMPACTS.get(key, "该分项结果不可用")
            text = _PROBE_REASON_ALIASES.get(str(reason).strip())
            detail = text or _sanitize_probe_reason(reason)
            rows.append(
                {
                    "item": f"失败明细 · {label}",
                    "value": f"{detail}（影响：{impact}）",
                }
            )
        provider_items = list(provider_errors.items())
        for name, reason in provider_items[:_MAX_PROBE_FAILURE_ROWS]:
            rows.append(
                {
                    "item": f"预警源失败 · {_sanitize_probe_reason(name)}",
                    "value": _sanitize_probe_reason(reason),
                }
            )
        extra = len(provider_items) - _MAX_PROBE_FAILURE_ROWS
        if extra > 0:
            rows.append(
                {
                    "item": "预警源失败",
                    "value": f"另有 {extra} 个数据源失败，详见日志",
                }
            )
        return rows

    @staticmethod
    def _count_text(value: Any) -> int | str:
        if value is None:
            return "—"
        try:
            return int(value)
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _weather_summary(weather: Mapping[str, Any]) -> str:
        parts = []
        temperature = weather.get("temperature")
        code = weather.get("weather_code")
        if temperature is not None:
            parts.append(f"{temperature}°C")
        if code is not None:
            parts.append(f"天气代码 {code}")
        if not parts:
            return "—"
        suffix = " · 旧数据" if weather.get("stale") else ""
        return " · ".join(parts) + suffix

    @staticmethod
    def _air_summary(air: Mapping[str, Any]) -> str:
        parts = []
        aqi = air.get("european_aqi")
        uv = air.get("uv_index")
        if aqi is not None:
            parts.append(f"空气质量指数 {aqi}")
        if uv is not None:
            parts.append(f"紫外线指数 {uv}")
        return " · ".join(parts) if parts else "—"

    @staticmethod
    def _pollen_summary(air: Mapping[str, Any]) -> str:
        if "pollen_available" not in air:
            return "—"
        return "可用" if air.get("pollen_available") else "当前地区无数据"

    @staticmethod
    def _calendar_summary(calendar: Mapping[str, Any]) -> str:
        holiday = str(calendar.get("holiday_name") or "").strip()
        if holiday:
            return holiday
        day_type = str(calendar.get("day_type") or "").strip()
        if day_type:
            return _CALENDAR_DAY_TYPE_LABELS.get(day_type, day_type)
        date = str(calendar.get("date") or "").strip()
        return date or "—"

    @staticmethod
    def _official_warning_summary(alerts: Mapping[str, Any]) -> str:
        status = str(alerts.get("official_warning_status") or "")
        if status == "unsupported_region":
            return "当前地区不支持"
        if status == "unavailable":
            return "数据源不可用"
        count = alerts.get("official_warning_count")
        if count is None:
            return "—"
        try:
            return str(int(count))
        except (TypeError, ValueError):
            return "—"

    def _settings_data(self) -> dict[str, Any]:
        schema = self.plugin._page_schema()
        rows: list[dict[str, Any]] = []
        for name, field in schema.items():
            if _secret_field(name, field):
                rows.append(
                    {
                        "key": name,
                        "value": "已配置（不回显）"
                        if self.plugin.config.get(name)
                        else "未配置",
                        "scope": "secret",
                    }
                )
                continue
            rows.append(
                {
                    "key": name,
                    "value": _public_value(
                        self.plugin.config.get(name, field.get("default"))
                    ),
                    "scope": "control" if name in CONTROL_FIELDS else "plugin",
                }
            )
        control = self.plugin.series_control_snapshot()
        return {
            "success": True,
            "title": "环境设置",
            "description": "配置写入复用现有 schema 校验、持久化与热应用路径。",
            "revision": self.revision(),
            "columns": [
                {"key": "key", "label": "配置项"},
                {"key": "value", "label": "当前值"},
                {"key": "scope", "label": "归属"},
            ],
            "rows": rows,
            "actions": self._settings_actions(),
            "footer": (
                f"统一接管字段 revision={control.get('revision', 0)}；"
                "API Key、Token 等秘密字段不会回显。"
            ),
        }

    def _declared_panel(self, panel_id: str) -> Mapping[str, Any]:
        for panel in self.contract()["panels"]:
            if isinstance(panel, Mapping) and str(panel.get("id") or "") == panel_id:
                return panel
        raise ValueError("UNKNOWN_PANEL")

    def _probe_actions(self) -> list[dict[str, Any]]:
        actions = copy.deepcopy(dict(self._declared_panel("probe"))["actions"])
        location = self.plugin.service.settings().default_location or ""
        for action in actions:
            for field in action.get("payload_fields", []):
                if str(field.get("name") or "") == "location":
                    field["default"] = location
        return actions

    def _settings_actions(self) -> list[dict[str, Any]]:
        actions = copy.deepcopy(dict(self._declared_panel("settings"))["actions"])
        current = self.plugin._public_config()
        control = self.plugin.series_control_snapshot()
        control_fields = (
            control.get("fields") if isinstance(control, dict) else {}
        )
        control_fields = control_fields if isinstance(control_fields, dict) else {}
        default_location = self.plugin.service.settings().default_location
        for action in actions:
            for field in action.get("payload_fields", []):
                name = str(field.get("name") or "")
                if action.get("id") == "save_config":
                    field["default"] = current.get(name, field.get("default"))
                elif action.get("id") == "set_control":
                    value = control_fields.get(name)
                    value = value if isinstance(value, dict) else {}
                    field["default"] = value.get(
                        "effective_value", self.plugin.config.get(name)
                    )
                elif action.get("id") == "set_default_location":
                    field["default"] = default_location
        return actions

    def revision(self) -> str:
        payload = {
            "config": self.plugin._public_config(),
            "control": self.plugin.series_control_snapshot(),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    # -- actions ----------------------------------------------------------

    async def action(
        self,
        panel: str,
        action: str,
        payload: Mapping[str, Any] | None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if panel not in {"settings", "probe"}:
            if panel == "status":
                return {"success": False, "error": "UNKNOWN_ACTION"}
            raise ValueError("UNKNOWN_PANEL")
        try:
            declaration = self._declared_action(action)
        except ValueError as exc:
            return {"success": False, "error": str(exc) or "UNKNOWN_ACTION"}
        if not isinstance(payload, Mapping):
            return {"success": False, "error": "INVALID_JSON_PAYLOAD"}
        self._require_context(declaration, context)
        try:
            if action == "save_config":
                return await self._save_config(dict(payload))
            if action == "set_control":
                return self._set_control(dict(payload))
            if action == "set_default_location":
                return await self._set_default_location(dict(payload))
            if action == "probe_weather":
                return await self._probe("weather", dict(payload))
            if action == "probe_air":
                return await self._probe("air", dict(payload))
            if action == "probe_calendar":
                return await self._probe("calendar", dict(payload))
            if action == "probe_all":
                return await self._probe_all(dict(payload))
        except PermissionError:
            raise
        except ValueError as exc:
            code = str(exc) or "INVALID_ACTION"
            return {"success": False, "error": code}
        except Exception:
            return {"success": False, "error": "PANEL_ACTION_FAILED"}
        raise ValueError("UNKNOWN_ACTION")

    def _require_context(
        self,
        declaration: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(context, Mapping):
            return
        actor = context.get("actor")
        actor = actor if isinstance(actor, Mapping) else {}
        role = str(context.get("role") or actor.get("role") or "").strip()
        ranks = {"viewer": 0, "admin": 1, "owner": 2}
        required_role = str(declaration.get("min_role") or "admin")
        if role and ranks.get(role, -1) < ranks.get(required_role, 1):
            raise PermissionError("ROLE_FORBIDDEN")
        if declaration.get("revision_required"):
            expected = context.get("expected_revision")
            if expected in (None, ""):
                raise ValueError("REVISION_REQUIRED")
            if str(expected) != self.revision():
                raise ValueError("REVISION_CONFLICT")

    async def _save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        forbidden = {key for key in payload if key in CONTROL_FIELDS}
        if forbidden:
            return {
                "success": False,
                "error": "CONTROL_FIELD_REQUIRES_SET_CONTROL",
                "fields": sorted(forbidden),
            }
        if "default_location" in payload:
            return {
                "success": False,
                "error": "USE_SET_DEFAULT_LOCATION",
            }
        secret_fields = {
            key
            for key in payload
            if _secret_field(key, self.plugin._page_schema().get(key, {}))
        }
        if secret_fields:
            return {
                "success": False,
                "error": "SECRET_FIELD_NOT_WRITABLE",
                "fields": sorted(secret_fields),
            }
        result = await self.plugin._save_config_payload(payload)
        if not result.get("success"):
            return {
                "success": False,
                "error": str(result.get("error") or "VALIDATION_FAILED"),
                "fields": result.get("fields") or {},
                "message": str(result.get("message") or "配置校验失败"),
            }
        return {
            "success": True,
            "message": "环境配置已保存并热应用",
            "changed": list(result.get("changed") or []),
            "config": result.get("config") or self.plugin._public_config(),
            "revision": self.revision(),
        }

    def _set_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload or any(key not in CONTROL_FIELDS for key in payload):
            return {"success": False, "error": "INVALID_CONTROL_FIELD"}
        if getattr(self.plugin, "_series_control_mode", "native") != "managed":
            self.plugin.series_control_set_mode("managed")
        snapshot = self.plugin.series_control_snapshot()
        result = self.plugin.apply_series_control_patch(
            payload, expected_revision=int(snapshot.get("revision", 0) or 0)
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": str(result.get("reason") or "CONTROL_APPLY_FAILED"),
            }
        return {
            "success": True,
            "message": "统一接管字段已应用",
            "changed": sorted(payload),
            "revision": self.revision(),
        }

    async def _set_default_location(self, payload: dict[str, Any]) -> dict[str, Any]:
        location = str(payload.get("location") or "").strip()
        if not location:
            return {"success": False, "error": "LOCATION_REQUIRED"}
        if len(location) > MAX_LOCATION_CHARS:
            return {"success": False, "error": "LOCATION_TOO_LONG"}
        try:
            resolved = await self.plugin.service.resolve_location(location)
        except Exception:
            return {"success": False, "error": "LOCATION_RESOLVE_FAILED"}
        self.plugin.config["default_location"] = location
        try:
            self.plugin.service.remember_default_location(location, resolved)
        except Exception:
            return {"success": False, "error": "CONFIG_PERSIST_FAILED"}
        await self.plugin._restart_background_task(clear_candidate=True)
        return {
            "success": True,
            "message": "常驻地点已校验并保存",
            "location": resolved.public_dict(),
            "revision": self.revision(),
        }

    async def _probe(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"location", "date"} if kind == "calendar" else {"location"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            return {
                "success": False,
                "error": "INVALID_PROBE_PARAMETER",
                "fields": unknown,
            }
        location = str(payload.get("location") or "").strip()
        if len(location) > MAX_LOCATION_CHARS:
            return {"success": False, "error": "LOCATION_TOO_LONG"}
        date_text = str(payload.get("date") or "").strip()
        if len(date_text) > MAX_DATE_CHARS:
            return {"success": False, "error": "INVALID_DATE"}
        limited = self._rate_limit(f"probe_{kind}")
        if limited:
            return limited
        try:
            if kind == "weather":
                snapshot = await self.plugin.service.weather_snapshot(
                    location, "current", 1
                )
                current = (snapshot.get("payload") or {}).get("current") or {}
                result = {
                    "kind": "weather",
                    "location": snapshot.get("location") or {},
                    "observed_at": snapshot.get("observed_at"),
                    "stale": bool(snapshot.get("stale")),
                    "temperature": current.get("temperature_2m"),
                    "weather_code": current.get("weather_code"),
                }
            elif kind == "air":
                snapshot = await self.plugin.service.air_quality_snapshot(location, 0)
                current = (snapshot.get("payload") or {}).get("current") or {}
                result = {
                    "kind": "air",
                    "location": snapshot.get("location") or {},
                    "observed_at": snapshot.get("observed_at"),
                    "stale": bool(snapshot.get("stale")),
                    "european_aqi": current.get("european_aqi"),
                    "us_aqi": current.get("us_aqi"),
                    "uv_index": current.get("uv_index"),
                }
            else:
                snapshot = await self.plugin.service.calendar_snapshot(
                    location, date_text
                )
                result = {
                    "kind": "calendar",
                    "location": snapshot.get("location") or {},
                    "date": snapshot.get("date"),
                    "day_type": snapshot.get("day_type"),
                    "holiday_name": snapshot.get("holiday_name"),
                }
        except Exception:
            self._remember_probe_failure(kind)
            return {
                "success": False,
                "error": "PROBE_FAILED",
                "probe": kind,
            }
        self._remember_probe(kind, result)
        return {
            "success": True,
            "message": f"{kind} 连通性测试完成",
            "probe": kind,
            "result": result,
        }

    async def _probe_all(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - {"location"})
        if unknown:
            return {
                "success": False,
                "error": "INVALID_PROBE_PARAMETER",
                "fields": unknown,
            }
        location = str(payload.get("location") or "").strip()
        if len(location) > MAX_LOCATION_CHARS:
            return {"success": False, "error": "LOCATION_TOO_LONG"}
        limited = self._rate_limit("probe_all")
        if limited:
            return limited
        try:
            snapshot = await self.plugin.environment_probe(location)
        except Exception:
            self._remember_probe_failure("all")
            return {"success": False, "error": "PROBE_FAILED", "probe": "all"}
        self._store_probe_snapshot(snapshot)
        if not snapshot.get("ok"):
            return {
                "success": False,
                "error": "PROBE_FAILED",
                "probe": "all",
                "message": "所有环境数据源均不可用",
            }
        return {"success": True, "message": "环境数据源连通性测试完成"}

    def _store_probe_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        weather = snapshot.get("weather")
        weather = weather if isinstance(weather, Mapping) else {}
        air = snapshot.get("air_quality")
        air = air if isinstance(air, Mapping) else {}
        calendar = snapshot.get("calendar")
        calendar = calendar if isinstance(calendar, Mapping) else {}
        alerts = snapshot.get("alerts")
        alerts = alerts if isinstance(alerts, Mapping) else {}
        errors = snapshot.get("component_errors")
        errors = errors if isinstance(errors, Mapping) else {}
        location = snapshot.get("location")
        name = location.get("name") if isinstance(location, Mapping) else None
        self._last_probe_snapshot = {
            "location": str(name or self._last_probe_snapshot.get("location") or ""),
            "weather": dict(weather),
            "air_quality": dict(air),
            "calendar": dict(calendar),
            "alerts": dict(alerts),
            "component_errors": {
                str(key): str(value)[:MAX_PROBE_RESULT_CHARS]
                for key, value in errors.items()
            },
            "updated_at": _now_text(),
        }

    def _remember_probe(self, kind: str, result: Mapping[str, Any]) -> None:
        snapshot = self._last_probe_snapshot
        location = result.get("location")
        name = location.get("name") if isinstance(location, Mapping) else None
        if name:
            snapshot["location"] = str(name)
        if kind == "weather":
            snapshot["weather"] = {
                "temperature": result.get("temperature"),
                "weather_code": result.get("weather_code"),
                "observed_at": result.get("observed_at"),
                "stale": bool(result.get("stale")),
            }
        elif kind == "air":
            snapshot["air_quality"] = {
                "european_aqi": result.get("european_aqi"),
                "us_aqi": result.get("us_aqi"),
                "uv_index": result.get("uv_index"),
            }
        elif kind == "calendar":
            snapshot["calendar"] = {
                "date": result.get("date"),
                "day_type": result.get("day_type"),
                "holiday_name": result.get("holiday_name"),
            }
        errors = snapshot.get("component_errors")
        if isinstance(errors, dict):
            errors.pop(kind, None)
        snapshot["updated_at"] = _now_text()

    def _remember_probe_failure(self, kind: str) -> None:
        snapshot = self._last_probe_snapshot
        errors = snapshot.get("component_errors")
        if not isinstance(errors, dict):
            errors = {}
            snapshot["component_errors"] = errors
        errors[kind] = "PROBE_FAILED"
        snapshot["updated_at"] = _now_text()

    def _rate_limit(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        previous = self._last_probe.get(key)
        if previous is not None:
            remaining = PROBE_COOLDOWN_SECONDS - (now - previous)
            if remaining > 0:
                return {
                    "success": False,
                    "error": "RATE_LIMITED",
                    "retry_after_ms": int(remaining * 1000) + 1,
                }
        self._last_probe[key] = now
        return None
