import asyncio
import json
from types import SimpleNamespace

from astrbot_plugin_environment_awareness import series_control
from astrbot_plugin_environment_awareness.main import EnvironmentAwarenessPlugin


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


class SavingConfig(dict):
    """假的平台托管配置：save_config 真写文件。"""

    def __init__(self, values=None, *, committed=True, raises=False, path=None):
        super().__init__(values or {})
        self.committed = committed
        self.raises = raises
        self.path = path
        self.saves = 0

    def save_config(self):
        self.saves += 1
        if self.raises:
            raise RuntimeError("disk full")
        if not self.committed:
            return False
        if self.path is not None:
            self.path.write_text(
                json.dumps(dict(self), ensure_ascii=False), encoding="utf-8"
            )
        return True


def _plugin_with_native(tmp_path, *, committed=True, raises=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    native_path = tmp_path / "native-config.json"
    config = SavingConfig(
        {"proactive_enabled": False},
        committed=committed,
        raises=raises,
        path=native_path,
    )
    config.config_path = str(native_path)
    native_path.write_text(json.dumps(dict(config)), encoding="utf-8")
    plugin = EnvironmentAwarenessPlugin.__new__(EnvironmentAwarenessPlugin)
    plugin.config = config
    plugin._usage = SimpleNamespace(path=str(tmp_path / "usage-stats.json"))
    return plugin


def test_snapshot_exposes_native_and_effective_values(tmp_path):
    """核「一键读取」依赖 native_value：接管覆盖后原生值必须保持真值。"""
    plugin = _plugin(tmp_path, {"proactive_enabled": False})
    fields = series_control.snapshot(plugin)["fields"]
    assert all("native_value" in item for item in fields.values())
    assert fields["proactive_enabled"]["native_value"] is False
    assert fields["proactive_enabled"]["effective_value"] is False

    series_control.set_mode(plugin, "managed")
    series_control.apply(plugin, {"proactive_enabled": True}, expected_revision=0)
    fields = series_control.snapshot(plugin)["fields"]
    assert fields["proactive_enabled"]["native_value"] is False
    assert fields["proactive_enabled"]["effective_value"] is True
    assert fields["proactive_enabled"]["effective_source"] == "managed"


def test_native_write_persists_and_backs_up(tmp_path):
    """一键固化：先备份、再落盘；固化后原生值成为新真值。"""
    plugin = _plugin_with_native(tmp_path)
    result = asyncio.run(
        plugin.series_control_native_write(
            {"proactive_enabled": True}, expected_revision=0
        )
    )
    assert result["status"] == "ok" and result["reason"] == "APPLIED"
    assert result["written"] == ["proactive_enabled"]
    assert result["backup_id"]
    disk = json.loads((tmp_path / "native-config.json").read_text(encoding="utf-8"))
    assert disk["proactive_enabled"] is True
    backups = sorted(tmp_path.glob("native-backup-*.json"))
    assert backups, "固化前必须先写备份"
    assert (
        json.loads(backups[-1].read_text(encoding="utf-8"))["proactive_enabled"]
        is False
    )
    assert (
        plugin.series_control_snapshot()["fields"]["proactive_enabled"]["native_value"]
        is True
    )
    # 接管记忆同步刷新：回退到原生模式时不会倒回旧值
    assert plugin._series_control_native_values["proactive_enabled"] == (True, True)


def test_native_write_rolls_back_when_persist_fails(tmp_path):
    plugin = _plugin_with_native(tmp_path, raises=True)
    result = asyncio.run(
        plugin.series_control_native_write(
            {"proactive_enabled": True}, expected_revision=0
        )
    )
    assert result["status"] == "error"
    assert result["reason"].startswith("PERSIST_FAILED")
    assert plugin.config["proactive_enabled"] is False
    assert (
        json.loads((tmp_path / "native-config.json").read_text(encoding="utf-8"))[
            "proactive_enabled"
        ]
        is False
    )


def test_native_write_rejects_unknown_field_type_and_revision(tmp_path):
    plugin = _plugin(tmp_path)

    def call(patch, revision):
        return asyncio.run(
            series_control.native_write(plugin, patch, expected_revision=revision)
        )

    assert call({"unknown": True}, 0)["reason"] == "UNKNOWN_FIELD"
    assert call({"proactive_enabled": "yes"}, 0)["reason"] == "INVALID_TYPE"
    assert call({"opportunity_refresh_seconds": 1}, 0)["reason"] == "INVALID_VALUE"
    assert call({"proactive_enabled": True}, 3)["reason"] == "REVISION_CONFLICT"
