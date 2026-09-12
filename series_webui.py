"""``series.webui@2.0`` adapter for environment awareness.

The adapter only presents and validates data.  Configuration writes still go
through the existing page/config helpers or ``series.control`` implementation,
so both standalone and managed surfaces share one state owner.
"""

from __future__ import annotations

import copy
import hashlib
import json
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
        return {"success": False, "error": "UNKNOWN_PANEL"}

    def _status_data(self) -> dict[str, Any]:
        diagnostics = self.plugin._runtime_diagnostics()
        settings = self.plugin.service.settings()
        candidate = self.plugin.get_cached_opportunity(allow_stale=True)
        opportunity = diagnostics.get("opportunity_cache")
        opportunity = opportunity if isinstance(opportunity, dict) else {}
        rows = [
            {"item": "默认地点", "value": settings.default_location or "未配置"},
            {
                "item": "主动关心",
                "value": "已启用" if settings.proactive_enabled else "未启用",
            },
            {
                "item": "主动提醒暂停",
                "value": "是" if settings.proactive_paused else "否",
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

    def _settings_actions(self) -> list[dict[str, Any]]:
        actions = copy.deepcopy(self.contract()["panels"][1]["actions"])
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
        if panel != "settings":
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
            return {
                "success": False,
                "error": "PROBE_FAILED",
                "probe": kind,
            }
        return {
            "success": True,
            "message": f"{kind} 连通性测试完成",
            "probe": kind,
            "result": result,
        }

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
