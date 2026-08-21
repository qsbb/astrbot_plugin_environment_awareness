from types import SimpleNamespace

from astrbot_plugin_environment_awareness import series_control


def _plugin(tmp_path, config=None):
    return SimpleNamespace(
        config=dict(config or {"proactive_enabled": False}),
        _usage=SimpleNamespace(path=str(tmp_path / "usage.json")),
    )


def test_contract_exposes_only_safe_environment_fields(tmp_path):
    plugin = _plugin(tmp_path)
    assert series_control.contract(plugin)["name"] == "series.control@1.0"
    assert set(series_control.schema(plugin)["fields"]) == {
        "proactive_enabled",
        "opportunity_cache_enabled",
        "opportunity_refresh_seconds",
    }


def test_managed_override_and_native_restore(tmp_path):
    plugin = _plugin(tmp_path, {"proactive_enabled": False})
    assert series_control.set_mode(plugin, "managed")["mode"] == "managed"
    result = series_control.apply(
        plugin, {"proactive_enabled": True}, expected_revision=0
    )
    assert result == {"success": True, "revision": 1}
    assert plugin.config["proactive_enabled"] is True
    assert (
        series_control.snapshot(plugin)["fields"]["proactive_enabled"][
            "effective_source"
        ]
        == "managed"
    )
    series_control.set_mode(plugin, "native")
    assert plugin.config["proactive_enabled"] is False
    assert (
        series_control.snapshot(plugin)["fields"]["proactive_enabled"][
            "effective_source"
        ]
        == "plugin"
    )


def test_invalid_patch_and_revision_conflict_fail_closed(tmp_path):
    plugin = _plugin(tmp_path)
    assert (
        series_control.validate(plugin, {"unknown": True}, expected_revision=0)[
            "reason"
        ]
        == "PATCH_INVALID"
    )
    assert (
        series_control.validate(
            plugin, {"opportunity_refresh_seconds": 1}, expected_revision=0
        )["reason"]
        == "PATCH_INVALID"
    )
    assert (
        series_control.validate(
            plugin, {"proactive_enabled": True}, expected_revision=3
        )["reason"]
        == "REVISION_CONFLICT"
    )


def test_reset_removes_managed_value_and_restores_native(tmp_path):
    plugin = _plugin(tmp_path, {"opportunity_cache_enabled": False})
    series_control.set_mode(plugin, "managed")
    series_control.apply(
        plugin, {"opportunity_cache_enabled": True}, expected_revision=0
    )
    result = series_control.reset(
        plugin, ["opportunity_cache_enabled"], expected_revision=1
    )
    assert result == {"success": True, "revision": 2}
    assert plugin.config["opportunity_cache_enabled"] is False
