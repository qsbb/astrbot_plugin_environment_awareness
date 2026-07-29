from __future__ import annotations

from datetime import UTC, datetime

from astrbot_plugin_environment_awareness.core.models import Location
from astrbot_plugin_environment_awareness.core.warnings import (
    filter_nmc_warnings,
    normalize_china_province,
)


def _location(name: str = "杭州市") -> Location:
    return Location(
        query="杭州",
        name=name,
        latitude=30.2741,
        longitude=120.1551,
        timezone="Asia/Shanghai",
        country="中国",
        country_code="CN",
        admin1="浙江",
        admin2="杭州市",
    )


def _warning(alert_id: str, title: str, issue: str = "2026/07/29 12:00") -> dict:
    return {
        "alertid": alert_id,
        "issuetime": issue,
        "title": title,
        "url": f"/publish/alarm/{alert_id}.html",
    }


def test_province_names_are_normalized_for_nmc_query():
    assert normalize_china_province("浙江") == "浙江省"
    assert normalize_china_province("北京") == "北京市"
    assert normalize_china_province("Inner Mongolia") == "内蒙古自治区"


def test_city_filter_suppresses_other_city_in_same_province():
    payload = {
        "warnings": [
            _warning("local", "浙江省杭州市气象台发布暴雨红色预警信号"),
            _warning("other", "浙江省湖州市气象台发布暴雨红色预警信号"),
        ]
    }
    warnings, stats = filter_nmc_warnings(
        payload,
        _location(),
        "浙江省",
        max_age_hours=72,
        now=datetime(2026, 7, 29, 6, tzinfo=UTC),
    )
    assert [item["alert_id"] for item in warnings] == ["local"]
    assert stats["suppressed_as_irrelevant"] == 1


def test_province_level_warning_is_relevant_to_city():
    payload = {"warnings": [_warning("province", "浙江省气象台发布台风橙色预警信号")]}
    warnings, _ = filter_nmc_warnings(
        payload,
        _location(),
        "浙江省",
        max_age_hours=72,
        now=datetime(2026, 7, 29, 6, tzinfo=UTC),
    )
    assert warnings[0]["scope"] == "province"


def test_stale_or_malformed_warning_is_not_returned():
    payload = {
        "warnings": [
            _warning(
                "old",
                "浙江省杭州市气象台发布高温橙色预警信号",
                "2026/07/20 12:00",
            ),
            _warning("bad", "不是合法预警标题"),
        ]
    }
    warnings, stats = filter_nmc_warnings(
        payload,
        _location(),
        "浙江省",
        max_age_hours=72,
        now=datetime(2026, 7, 29, 6, tzinfo=UTC),
    )
    assert warnings == []
    assert stats["suppressed_as_stale"] == 1
    assert stats["suppressed_as_malformed"] == 1
