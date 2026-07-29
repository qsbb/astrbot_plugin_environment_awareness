from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .opportunity import SEVERITY_ORDER


def _parse_clock(value: str, fallback: time) -> time:
    try:
        return time.fromisoformat(str(value or "").strip())
    except ValueError:
        return fallback


def local_delivery_window(
    timezone_name: str,
    quiet_start: str,
    quiet_end: str,
    *,
    now: datetime | None = None,
) -> tuple[str, bool]:
    try:
        timezone = ZoneInfo(str(timezone_name or "UTC"))
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    local_now = now.astimezone(timezone) if now else datetime.now(timezone)
    start = _parse_clock(quiet_start, time(23, 0))
    end = _parse_clock(quiet_end, time(7, 0))
    current = local_now.time().replace(tzinfo=None)
    if start == end:
        quiet = False
    elif start < end:
        quiet = start <= current < end
    else:
        quiet = current >= start or current < end
    return local_now.date().isoformat(), quiet


class ProactiveDeliveryState:
    """Small persistent ledger for dedupe and per-local-day limits."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._delivered: dict[str, dict[str, str]] = {}
        self._evaluated: dict[str, dict[str, str]] = {}
        self._daily: dict[str, int] = {}
        self._load()

    def can_send(
        self,
        event_key: str,
        severity: str,
        local_date: str,
        daily_limit: int,
    ) -> tuple[bool, str]:
        if not event_key:
            return False, "missing_event_key"
        previous = self._delivered.get(event_key)
        if previous is not None and SEVERITY_ORDER.get(
            str(previous.get("severity") or ""), -1
        ) >= SEVERITY_ORDER.get(str(severity), -1):
            return False, "duplicate_or_lower_severity"
        evaluated = self._evaluated.get(event_key)
        if evaluated is not None and SEVERITY_ORDER.get(
            str(evaluated.get("severity") or ""), -1
        ) >= SEVERITY_ORDER.get(str(severity), -1):
            return False, "already_evaluated_at_severity"
        if self._daily.get(local_date, 0) >= max(1, int(daily_limit)):
            return False, "daily_limit"
        return True, "allowed"

    def mark_sent(
        self,
        event_key: str,
        severity: str,
        revision: str,
        local_date: str,
        *,
        now: datetime | None = None,
    ) -> None:
        timestamp = (now or datetime.now().astimezone()).isoformat()
        self._delivered[str(event_key)] = {
            "severity": str(severity),
            "revision": str(revision),
            "sent_at": timestamp,
        }
        self._evaluated.pop(str(event_key), None)
        self._daily[str(local_date)] = self._daily.get(str(local_date), 0) + 1
        self._prune(now or datetime.now().astimezone())
        self._write()

    def mark_evaluated(
        self,
        event_key: str,
        severity: str,
        revision: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Remember a model-suppressed event without consuming the daily send cap."""
        timestamp = (now or datetime.now().astimezone()).isoformat()
        self._evaluated[str(event_key)] = {
            "severity": str(severity),
            "revision": str(revision),
            "evaluated_at": timestamp,
        }
        self._prune(now or datetime.now().astimezone())
        self._write()

    def snapshot(self) -> dict[str, Any]:
        return {
            "dedupe_entries": len(self._delivered),
            "evaluated_entries": len(self._evaluated),
            "daily_counts": dict(self._daily),
        }

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=30)
        kept: dict[str, dict[str, str]] = {}
        for key, value in self._delivered.items():
            try:
                parsed = datetime.fromisoformat(str(value.get("sent_at") or ""))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
            except ValueError:
                continue
            if parsed >= cutoff:
                kept[key] = dict(value)
        self._delivered = kept
        kept_evaluated: dict[str, dict[str, str]] = {}
        for key, value in self._evaluated.items():
            try:
                parsed = datetime.fromisoformat(str(value.get("evaluated_at") or ""))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
            except ValueError:
                continue
            if parsed >= cutoff:
                kept_evaluated[key] = dict(value)
        self._evaluated = kept_evaluated
        cutoff_day = cutoff.date().isoformat()
        self._daily = {
            day: count
            for day, count in self._daily.items()
            if day >= cutoff_day and isinstance(count, int) and count >= 0
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != self.SCHEMA_VERSION
        ):
            return
        delivered = payload.get("delivered")
        evaluated = payload.get("evaluated")
        daily = payload.get("daily")
        if isinstance(delivered, dict):
            for key, value in delivered.items():
                if isinstance(value, dict):
                    sent_at = str(value.get("sent_at") or "")
                    severity = str(value.get("severity") or "")
                    if str(key) and sent_at and severity in SEVERITY_ORDER:
                        self._delivered[str(key)] = {
                            "severity": severity,
                            "revision": str(value.get("revision") or ""),
                            "sent_at": sent_at,
                        }
                elif str(key) and str(value):
                    self._delivered[str(key)] = {
                        "severity": "critical",
                        "revision": "legacy",
                        "sent_at": str(value),
                    }
        if isinstance(evaluated, dict):
            for key, value in evaluated.items():
                if not isinstance(value, dict):
                    continue
                evaluated_at = str(value.get("evaluated_at") or "")
                severity = str(value.get("severity") or "")
                if str(key) and evaluated_at and severity in SEVERITY_ORDER:
                    self._evaluated[str(key)] = {
                        "severity": severity,
                        "revision": str(value.get("revision") or ""),
                        "evaluated_at": evaluated_at,
                    }
        if isinstance(daily, dict):
            for key, value in daily.items():
                try:
                    count = max(0, int(value))
                except (TypeError, ValueError):
                    continue
                self._daily[str(key)] = count

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "delivered": self._delivered,
            "evaluated": self._evaluated,
            "daily": self._daily,
        }
        fd, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
