from __future__ import annotations

import json

from astrbot_plugin_environment_awareness.core.usage import UsageTracker


def test_usage_tracker_persists_bounded_privacy_minimal_history(tmp_path):
    path = tmp_path / "usage.json"
    tracker = UsageTracker(path, max_recent=2)
    tracker.record("llm_tool", "get_weather", duration_ms=125)
    tracker.record("command", "境天气", status="error", duration_ms=34)
    tracker.record("awareness", "calendar_prompt", duration_ms=0)

    snapshot = UsageTracker(path, max_recent=2).snapshot(recent_limit=10)
    assert snapshot["total"] == 3
    assert snapshot["successful"] == 2
    assert snapshot["failed"] == 1
    assert snapshot["by_source"] == {
        "llm_tool": 1,
        "command": 1,
        "awareness": 1,
    }
    assert [item["action"] for item in snapshot["recent"]] == [
        "calendar_prompt",
        "境天气",
    ]
    assert all(
        set(item) == {"timestamp", "source", "action", "status", "duration_ms"}
        for item in snapshot["recent"]
    )
    assert "不记录消息、用户标识或地点" in snapshot["privacy"]


def test_usage_tracker_recovers_from_invalid_file(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{broken", encoding="utf-8")
    tracker = UsageTracker(path)
    assert tracker.snapshot()["total"] == 0
    tracker.record("command", "境")
    assert json.loads(path.read_text(encoding="utf-8"))["total"] == 1
