from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from .models import Location

NMC_BASE_URL = "https://www.nmc.cn"

_DIRECT_MUNICIPALITIES = {"北京", "天津", "上海", "重庆"}
_PROVINCE_NAMES = {
    "anhui": "安徽省",
    "beijing": "北京市",
    "chongqing": "重庆市",
    "fujian": "福建省",
    "gansu": "甘肃省",
    "guangdong": "广东省",
    "guangxi": "广西壮族自治区",
    "guizhou": "贵州省",
    "hainan": "海南省",
    "hebei": "河北省",
    "heilongjiang": "黑龙江省",
    "henan": "河南省",
    "hubei": "湖北省",
    "hunan": "湖南省",
    "inner mongolia": "内蒙古自治区",
    "jiangsu": "江苏省",
    "jiangxi": "江西省",
    "jilin": "吉林省",
    "liaoning": "辽宁省",
    "ningxia": "宁夏回族自治区",
    "qinghai": "青海省",
    "shaanxi": "陕西省",
    "shandong": "山东省",
    "shanghai": "上海市",
    "shanxi": "山西省",
    "sichuan": "四川省",
    "tianjin": "天津市",
    "tibet": "西藏自治区",
    "xinjiang": "新疆维吾尔自治区",
    "yunnan": "云南省",
    "zhejiang": "浙江省",
}
_AUTONOMOUS_REGIONS = {
    "广西": "广西壮族自治区",
    "内蒙古": "内蒙古自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
}
_SPECIAL_REGIONS = {"香港": "香港特别行政区", "澳门": "澳门特别行政区"}
_ADMIN_SUFFIXES = (
    "特别行政区",
    "壮族自治区",
    "回族自治区",
    "维吾尔自治区",
    "自治区",
    "自治州",
    "地区",
    "省",
    "市",
    "区",
    "县",
    "州",
    "盟",
    "旗",
)
_WARNING_TITLE = re.compile(
    r"^(?P<issuer>.+?)发布(?P<kind>.+?)(?P<level>蓝色|黄色|橙色|红色)"
    r"预警(?:信号)?$"
)


def _strip_admin_suffix(value: str) -> str:
    value = str(value or "").strip()
    for suffix in _ADMIN_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def normalize_china_province(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    translated = _PROVINCE_NAMES.get(raw.casefold())
    if translated:
        return translated
    if raw.endswith(("省", "市", "自治区", "特别行政区")):
        return raw
    if raw in _DIRECT_MUNICIPALITIES:
        return f"{raw}市"
    if raw in _AUTONOMOUS_REGIONS:
        return _AUTONOMOUS_REGIONS[raw]
    if raw in _SPECIAL_REGIONS:
        return _SPECIAL_REGIONS[raw]
    return f"{raw}省"


def warning_region(location: Location, override: str = "") -> str:
    if location.country_code.upper() not in {"", "CN"}:
        return ""
    return normalize_china_province(override or location.admin1)


def _location_terms(location: Location, province: str) -> set[str]:
    province_stem = _strip_admin_suffix(province)
    terms: set[str] = set()
    for value in (
        location.name,
        location.admin2,
        location.admin3,
        location.admin4,
        location.query,
    ):
        value = str(value or "").strip()
        if not value or not re.search(r"[\u4e00-\u9fff]", value):
            continue
        stem = _strip_admin_suffix(value)
        if stem and stem != province_stem and len(stem) >= 2:
            terms.add(stem)
    return terms


def _issued_at(value: Any) -> datetime | None:
    try:
        local = datetime.strptime(str(value), "%Y/%m/%d %H:%M")
        return local.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def filter_nmc_warnings(
    payload: dict[str, Any],
    location: Location,
    province: str,
    *,
    max_age_hours: int,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=max(1, max_age_hours))
    terms = _location_terms(location, province)
    province_stem = _strip_admin_suffix(province)
    query_stem = _strip_admin_suffix(location.query)
    name_stem = _strip_admin_suffix(location.name)
    province_query = query_stem == province_stem or name_stem == province_stem
    stats = {
        "evaluated": 0,
        "suppressed_as_stale": 0,
        "suppressed_as_irrelevant": 0,
        "suppressed_as_malformed": 0,
    }
    warnings: list[dict[str, Any]] = []

    for raw in payload.get("warnings") or []:
        if not isinstance(raw, dict):
            stats["suppressed_as_malformed"] += 1
            continue
        stats["evaluated"] += 1
        title = str(raw.get("title") or "").strip()
        issued = _issued_at(raw.get("issuetime"))
        match = _WARNING_TITLE.match(title)
        if not title or issued is None or match is None:
            stats["suppressed_as_malformed"] += 1
            continue
        if issued < cutoff or issued > now + timedelta(minutes=10):
            stats["suppressed_as_stale"] += 1
            continue

        province_level = title.startswith(f"{province}气象台发布")
        matched_terms = sorted(term for term in terms if term in title)
        if not (province_query or province_level or matched_terms):
            stats["suppressed_as_irrelevant"] += 1
            continue

        scope = "province" if province_query or province_level else "locality"
        warnings.append(
            {
                "alert_id": str(raw.get("alertid") or ""),
                "title": title,
                "issuer": match.group("issuer"),
                "kind": match.group("kind"),
                "level": match.group("level"),
                "issued_at": issued.isoformat(),
                "scope": scope,
                "matched_localities": matched_terms,
                "source_url": urljoin(NMC_BASE_URL, str(raw.get("url") or "")),
            }
        )

    warnings.sort(key=lambda item: item["issued_at"], reverse=True)
    stats["returned"] = len(warnings)
    return warnings, stats
