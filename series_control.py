"""Public series.control@1.0 adapter for environment runtime switches."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FIELDS = {
    "proactive_enabled": {"type": "bool", "default": False},
    "opportunity_cache_enabled": {"type": "bool", "default": True},
    "opportunity_refresh_seconds": {
        "type": "int",
        "default": 900,
        "minimum": 300,
        "maximum": 21600,
    },
}


def _path(plugin):
    return Path(plugin._usage.path).parent / "series-control.json"


def _load(plugin):
    try:
        data = json.loads(_path(plugin).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _native(plugin, field):
    getter = getattr(plugin.config, "get", None)
    return (
        getter(field, FIELDS[field]["default"])
        if callable(getter)
        else FIELDS[field]["default"]
    )


def _remember_native(plugin, field):
    saved = getattr(plugin, "_series_control_native_values", None)
    if saved is None:
        saved = plugin._series_control_native_values = {}
    if field not in saved:
        getter = getattr(plugin.config, "get", None)
        present = (
            field in plugin.config if hasattr(plugin.config, "__contains__") else False
        )
        saved[field] = (
            present,
            getter(field) if present and callable(getter) else None,
        )


def _restore_native(plugin, fields):
    saved = getattr(plugin, "_series_control_native_values", {})
    for field in fields:
        if field not in saved:
            continue
        present, value = saved[field]
        if present:
            plugin.config[field] = value
        elif hasattr(plugin.config, "pop"):
            plugin.config.pop(field, None)


def contract(plugin):
    return {
        "name": "series.control@1.0",
        "version": "1.0",
        "series_id": "ningxin_suxi",
        "plugin_id": "astrbot_plugin_environment_awareness",
        "plugin_name": "凝心溯溪-境",
        "capabilities": [
            "read_schema",
            "read_snapshot",
            "validate_patch",
            "apply_patch",
            "reset_override",
        ],
        "read_only": False,
        "secrets_in_response": False,
        "max_patch_fields": len(FIELDS),
    }


def schema(plugin):
    fields = {}
    for name, spec in FIELDS.items():
        fields[name] = {
            **spec,
            "control": "overrideable",
            "secret": False,
            "restart_required": False,
        }
    return {
        "contract_name": "series.control@1.0",
        "contract_version": "1.0",
        "plugin_id": "astrbot_plugin_environment_awareness",
        "revision": int(_load(plugin).get("revision", 0) or 0),
        "fields": fields,
    }


def snapshot(plugin):
    state = _load(plugin)
    overrides = (
        state.get("overrides", {})
        if isinstance(state.get("overrides", {}), dict)
        else {}
    )
    return {
        "status": "ok",
        "revision": int(state.get("revision", 0) or 0),
        "fields": {
            name: {
                "native_configured": name in plugin.config,
                "managed_configured": name in overrides,
                "effective_source": "managed"
                if name in overrides
                and getattr(plugin, "_series_control_mode", "native") == "managed"
                else "plugin",
            }
            for name in FIELDS
        },
    }


def validate(plugin, patch, *, expected_revision):
    state = _load(plugin)
    current = int(state.get("revision", 0) or 0)
    if current != int(expected_revision):
        return {"valid": False, "reason": "REVISION_CONFLICT"}
    if not isinstance(patch, dict) or not patch or any(k not in FIELDS for k in patch):
        return {"valid": False, "reason": "PATCH_INVALID"}
    for name, value in patch.items():
        spec = FIELDS[name]
        if spec["type"] == "bool" and not isinstance(value, bool):
            return {"valid": False, "reason": "PATCH_INVALID"}
        if spec["type"] == "int" and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not spec["minimum"] <= value <= spec["maximum"]
        ):
            return {"valid": False, "reason": "PATCH_INVALID"}
    return {"valid": True, "revision": current}


def _write(plugin, state):
    path = _path(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".series-control.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply(plugin, patch, *, expected_revision):
    result = validate(plugin, patch, expected_revision=expected_revision)
    if not result.get("valid"):
        return {"success": False, **result}
    state = _load(plugin)
    overrides = dict(state.get("overrides", {}) or {})
    overrides.update(patch)
    next_state = {
        "schema_version": 1,
        "revision": int(expected_revision) + 1,
        "overrides": overrides,
    }
    _write(plugin, next_state)
    if getattr(plugin, "_series_control_mode", "native") == "managed":
        for field in patch:
            _remember_native(plugin, field)
        plugin.config.update(patch)
    return {"success": True, "revision": next_state["revision"]}


def reset(plugin, fields=None, *, expected_revision=None):
    state = _load(plugin)
    current = int(state.get("revision", 0) or 0)
    if expected_revision is not None and current != int(expected_revision):
        return {"success": False, "reason": "REVISION_CONFLICT"}
    overrides = dict(state.get("overrides", {}) or {})
    for field in fields or list(overrides):
        overrides.pop(field, None)
    _write(
        plugin, {"schema_version": 1, "revision": current + 1, "overrides": overrides}
    )
    if getattr(plugin, "_series_control_mode", "native") == "managed":
        _restore_native(
            plugin,
            fields or list(getattr(plugin, "_series_control_native_values", {})),
        )
    return {"success": True, "revision": current + 1}


def set_mode(plugin, mode):
    next_mode = mode if mode in {"native", "managed"} else "native"
    previous_mode = getattr(plugin, "_series_control_mode", "native")
    if next_mode == "native" and previous_mode == "managed":
        _restore_native(plugin, FIELDS)
    plugin._series_control_mode = next_mode
    if plugin._series_control_mode == "managed":
        state = _load(plugin)
        overrides = state.get("overrides", {}) or {}
        for field in overrides:
            if field in FIELDS:
                _remember_native(plugin, field)
        plugin.config.update({k: v for k, v in overrides.items() if k in FIELDS})
    return {"success": True, "mode": plugin._series_control_mode}
