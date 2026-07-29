from __future__ import annotations

from datetime import date
from typing import Any

import holidays

from .models import Location


def _calendar_language(language: str) -> str:
    return "zh_CN" if str(language).lower().startswith("zh") else "en_US"


def _next_distinct_holiday(calendar: Any, target: date, current_name: str) -> dict:
    for day in sorted(day for day in calendar if day > target):
        name = str(calendar.get(day) or "")
        if current_name and name == current_name:
            continue
        return {
            "date": day.isoformat(),
            "name": name,
            "days_away": (day - target).days,
        }
    return {}


def build_calendar_snapshot(
    location: Location,
    target: date,
    *,
    language: str,
    country_code_override: str = "",
    subdivision: str = "",
) -> dict[str, Any]:
    country_code = (
        str(country_code_override or location.country_code or "").strip().upper()
    )
    if not country_code:
        raise ValueError(
            "该地点缺少国家代码。使用城市名查询，或在 "
            "calendar_country_code 中填写 ISO 国家代码。"
        )

    years = [target.year, target.year + 1]
    try:
        calendar = holidays.country_holidays(
            country_code,
            subdiv=subdivision or None,
            years=years,
            language=_calendar_language(language),
        )
    except (KeyError, NotImplementedError) as exc:
        raise ValueError(f"暂不支持国家/地区节假日：{country_code}") from exc

    holiday_name = str(calendar.get(target) or "")
    adjusted_workday = target in getattr(calendar, "weekend_workdays", set())
    working_day = bool(calendar.is_working_day(target))
    weekend = bool(calendar.is_weekend(target))

    if adjusted_workday:
        day_type = "adjusted_workday"
    elif holiday_name:
        day_type = "public_holiday"
    elif weekend:
        day_type = "weekend"
    elif working_day:
        day_type = "working_day"
    else:
        day_type = "day_off"

    return {
        "contract": "environment.calendar",
        "version": 1,
        "kind": "calendar",
        "location": location.public_dict(),
        "date": target.isoformat(),
        "weekday": target.strftime("%A"),
        "country_code": country_code,
        "subdivision": subdivision or None,
        "day_type": day_type,
        "is_public_holiday": bool(holiday_name),
        "holiday_name": holiday_name or None,
        "is_adjusted_workday": adjusted_workday,
        "is_weekend": weekend,
        "is_working_day": working_day,
        "is_day_off": not working_day,
        "next_holiday": _next_distinct_holiday(calendar, target, holiday_name),
        "source": {
            "provider": "python-holidays",
            "version": holidays.__version__,
            "network": False,
            "note": "离线节假日规则；年度调休以已收录的官方安排为准",
        },
    }


def significant_calendar_fragment(snapshot: dict[str, Any]) -> str:
    if snapshot.get("is_adjusted_workday"):
        fact = "今天虽是周末，但属于调休工作日。"
    elif snapshot.get("is_public_holiday"):
        fact = f"今天是{snapshot.get('holiday_name') or '公共假日'}，属于休息日。"
    else:
        return ""
    return (
        "[境·当地日历] "
        f"当地日期 {snapshot.get('date')}，{fact}"
        "仅在与当前对话自然相关时参考，不要主动播报或生硬提及。"
    )
