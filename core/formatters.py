from __future__ import annotations

import json
from typing import Any

WMO_WEATHER = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


def to_tool_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def format_datetime(snapshot: dict[str, Any]) -> str:
    location = snapshot.get("location", {}).get("name", "当前地点")
    return (
        f"{location}：{snapshot.get('date', '')} {snapshot.get('time', '')}，"
        f"{snapshot.get('weekday', '')}，时区 {snapshot.get('timezone', '')}。"
    )


def format_calendar(snapshot: dict[str, Any]) -> str:
    location = snapshot.get("location", {}).get("name", "当前地点")
    day_type = snapshot.get("day_type")
    if day_type == "adjusted_workday":
        description = "调休工作日"
    elif day_type == "public_holiday":
        description = str(snapshot.get("holiday_name") or "公共假日")
    elif day_type == "weekend":
        description = "周末休息日"
    elif day_type == "working_day":
        description = "普通工作日"
    else:
        description = "休息日"
    lines = [
        f"{location}：{snapshot.get('date', '')}，{description}。",
    ]
    next_holiday = snapshot.get("next_holiday") or {}
    if next_holiday:
        lines.append(
            f"下一个节假日：{next_holiday.get('date')} "
            f"{next_holiday.get('name')}，还有 {next_holiday.get('days_away')} 天。"
        )
    lines.append("来源：python-holidays 离线规则；年度调休以已收录安排为准。")
    return "\n".join(lines)


def _weather_name(code: Any) -> str:
    try:
        return WMO_WEATHER.get(int(code), f"天气代码 {int(code)}")
    except (TypeError, ValueError):
        return "天气未知"


def format_weather(snapshot: dict[str, Any]) -> str:
    location = snapshot.get("location", {}).get("name", "当前地点")
    payload = snapshot.get("payload") or {}
    current = payload.get("current") or {}
    lines = [
        f"{location}：{_weather_name(current.get('weather_code'))}，"
        f"{current.get('temperature_2m', '?')}°C，"
        f"体感 {current.get('apparent_temperature', '?')}°C，"
        f"湿度 {current.get('relative_humidity_2m', '?')}%，"
        f"风速 {current.get('wind_speed_10m', '?')} km/h。"
    ]
    visibility = current.get("visibility")
    if isinstance(visibility, (int, float)):
        lines.append(f"能见度约 {visibility / 1000:.1f} km。")
    astronomy = payload.get("astronomy") or {}
    if astronomy.get("sunrise") and astronomy.get("sunset"):
        daylight = astronomy.get("daylight_duration")
        daylight_text = ""
        if isinstance(daylight, (int, float)):
            hours = int(daylight) // 3600
            minutes = (int(daylight) % 3600) // 60
            daylight_text = f"，白昼约 {hours} 小时 {minutes} 分"
        lines.append(
            f"日出 {astronomy.get('sunrise')}，日落 {astronomy.get('sunset')}"
            f"{daylight_text}。"
        )
    nowcast = payload.get("near_term_precipitation") or {}
    if nowcast:
        if nowcast.get("rain_expected"):
            lines.append(
                f"未来约 6 小时预计有降水，首次出现在 "
                f"{nowcast.get('first_precipitation_at')}，"
                f"累计约 {nowcast.get('total_precipitation_mm')} mm。"
            )
        else:
            lines.append("未来约 6 小时的 15 分钟模型数据暂未出现明显降水。")
    daily = payload.get("daily") or {}
    for index, date in enumerate(daily.get("time") or []):
        try:
            lines.append(
                f"{date}：{_weather_name(daily.get('weather_code', [])[index])}，"
                f"{daily.get('temperature_2m_min', [])[index]}~"
                f"{daily.get('temperature_2m_max', [])[index]}°C，"
                f"降水概率 {daily.get('precipitation_probability_max', [])[index]}%。"
            )
        except (IndexError, TypeError):
            continue
    if snapshot.get("stale"):
        lines.append("当前使用的是带时间标记的旧缓存，请留意数据时间。")
    lines.append("来源：Open-Meteo 天气模型。")
    return "\n".join(lines)


def format_air_quality(snapshot: dict[str, Any]) -> str:
    location = snapshot.get("location", {}).get("name", "当前地点")
    current = (snapshot.get("payload") or {}).get("current") or {}
    lines = [
        f"{location}：欧洲 AQI {current.get('european_aqi', '无数据')}，"
        f"美国 AQI {current.get('us_aqi', '无数据')}，"
        f"PM2.5 {current.get('pm2_5', '无数据')} μg/m³，"
        f"PM10 {current.get('pm10', '无数据')} μg/m³，"
        f"紫外线指数 {current.get('uv_index', '无数据')}。"
    ]
    pollen = [
        ("桤木", current.get("alder_pollen")),
        ("桦木", current.get("birch_pollen")),
        ("禾草", current.get("grass_pollen")),
        ("蒿草", current.get("mugwort_pollen")),
        ("豚草", current.get("ragweed_pollen")),
    ]
    available = [(name, value) for name, value in pollen if value is not None]
    if available:
        lines.append(
            "花粉："
            + "，".join(f"{name} {value} grains/m³" for name, value in available)
            + "。"
        )
    else:
        lines.append("当前地点暂无可用花粉数据。")
    if snapshot.get("stale"):
        lines.append("当前使用的是带时间标记的旧缓存。")
    lines.append("来源：Open-Meteo 模型数据，不替代当地监测与医疗建议。")
    return "\n".join(lines)


def format_alerts(snapshot: dict[str, Any]) -> str:
    status = snapshot.get("status")
    location = snapshot.get("location", {}).get("name", "当前地点")
    if status == "unable_to_confirm":
        return f"暂时无法确认 {location} 的环境风险：数据源均不可用，请稍后重试。"

    weather = snapshot.get("weather_risk_signals") or []
    official = snapshot.get("official_weather_warnings") or []
    earthquakes = snapshot.get("earthquakes") or []
    if not weather and not official and not earthquakes:
        suffix = "部分数据源不可用。" if snapshot.get("provider_errors") else ""
        return (
            f"在已确认的数据源和设定阈值内，暂未发现与 {location} 有实际关系的事件。"
            f"{suffix}"
        )

    lines = [f"{location} 当前相关环境信号："]
    for warning in official:
        lines.append(
            f"- [官方预警] {warning.get('title', '')}，"
            f"发布时间 {warning.get('issued_at', '')}。"
        )
    for signal in weather:
        lines.append(
            f"- {signal.get('date', '')}：{signal.get('summary', '')} "
            f"({signal.get('value', '')} {signal.get('unit', '')})"
        )
    for event in earthquakes:
        lines.append(
            f"- M{event.get('magnitude', '?')} {event.get('place', '')}，"
            f"距设定地点约 {event.get('distance_km', '?')} km，"
            f"{event.get('relevance', '相关')}。"
        )
    lines.append(
        "标有“官方预警”的条目来自中央气象台当前预警列表；"
        "Open-Meteo 天气项是模型风险信号，地震影响为距离启发式筛选。"
    )
    return "\n".join(lines)
