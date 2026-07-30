from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class UsageTracker:
    """Persist privacy-minimal invocation counters and a bounded recent list."""

    def __init__(self, path: str | Path, *, max_recent: int = 30) -> None:
        self._path = Path(path)
        self._max_recent = max(1, int(max_recent))
        self._lock = threading.RLock()
        self._state = self._load()
        self._persistence_error = ""

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 1,
            "total": 0,
            "status_counts": {},
            "by_source": {},
            "by_action": {},
            "recent": [],
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_state()
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return self._empty_state()
        state = self._empty_state()
        try:
            state["total"] = max(0, int(raw.get("total") or 0))
            for key in ("status_counts", "by_source", "by_action"):
                values = raw.get(key)
                if isinstance(values, dict):
                    state[key] = {
                        str(name)[:80]: max(0, int(count or 0))
                        for name, count in values.items()
                    }
            recent = raw.get("recent")
            if isinstance(recent, list):
                state["recent"] = [
                    item for item in recent if isinstance(item, dict)
                ][: self._max_recent]
        except (TypeError, ValueError):
            return self._empty_state()
        return state

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._path)
            self._persistence_error = ""
        except OSError as exc:
            self._persistence_error = f"{type(exc).__name__}: {str(exc)[:120]}"

    def record(
        self,
        source: str,
        action: str,
        *,
        status: str = "success",
        duration_ms: int = 0,
    ) -> None:
        source = str(source or "unknown").strip()[:40] or "unknown"
        action = str(action or "unknown").strip()[:80] or "unknown"
        status = str(status or "success").strip()[:24] or "success"
        duration_ms = max(0, min(3_600_000, int(duration_ms or 0)))
        entry = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            "action": action,
            "status": status,
            "duration_ms": duration_ms,
        }
        with self._lock:
            self._state["total"] += 1
            for key, name in (
                ("status_counts", status),
                ("by_source", source),
                ("by_action", action),
            ):
                counts = self._state[key]
                counts[name] = int(counts.get(name) or 0) + 1
            self._state["recent"].insert(0, entry)
            del self._state["recent"][self._max_recent :]
            self._save()

    def snapshot(self, *, recent_limit: int = 12) -> dict[str, Any]:
        with self._lock:
            status_counts = dict(self._state["status_counts"])
            return {
                "total": int(self._state["total"]),
                "successful": int(status_counts.get("success") or 0),
                "failed": int(status_counts.get("error") or 0),
                "status_counts": status_counts,
                "by_source": dict(self._state["by_source"]),
                "by_action": dict(self._state["by_action"]),
                "recent": [
                    dict(item)
                    for item in self._state["recent"][: max(0, recent_limit)]
                ],
                "persistence_error": self._persistence_error or None,
                "privacy": (
                    "仅记录时间、入口、功能、结果与耗时；不记录消息、用户标识或地点"
                ),
            }
