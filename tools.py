from __future__ import annotations

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

try:
    from astrbot.core.agent.tool import FunctionTool, ToolExecResult
except ImportError:
    FunctionTool = object  # type: ignore[assignment,misc]
    ToolExecResult = str  # type: ignore[assignment,misc]

from .core.formatters import to_tool_json


def _error(message: str) -> str:
    return to_tool_json({"status": "error", "message": str(message)[:300]})


@pydantic_dataclass
class GetLocalDatetimeTool(FunctionTool):  # type: ignore[misc]
    name: str = "get_local_datetime"
    description: str = (
        "查询某地当前的日期、时间、星期和时区。仅在用户询问当前时间日期，"
        "或答案确实依赖当地时间时调用；普通闲聊不要调用。location 留空时使用常驻地点。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名或经度,纬度；留空使用常驻地点",
                    "default": "",
                }
            },
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        try:
            data = await self._plugin.service.datetime_snapshot(
                str(kwargs.get("location") or "")
            )
            return to_tool_json(data)
        except Exception as exc:
            return _error(str(exc))


@pydantic_dataclass
class GetLocalCalendarTool(FunctionTool):  # type: ignore[misc]
    name: str = "get_local_calendar"
    description: str = (
        "查询某地某天是否为公共假日、周末、普通工作日或调休工作日，"
        "并给出下一个节假日。仅在问题涉及节假日、放假、补班、工作日或日期安排时调用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名；留空使用常驻地点",
                    "default": "",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD；留空使用当地今天",
                    "default": "",
                },
            },
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        try:
            data = await self._plugin.service.calendar_snapshot(
                str(kwargs.get("location") or ""),
                str(kwargs.get("date") or ""),
            )
            return to_tool_json(data)
        except Exception as exc:
            return _error(str(exc))


@pydantic_dataclass
class GetWeatherTool(FunctionTool):  # type: ignore[misc]
    name: str = "get_weather"
    description: str = (
        "查询某地当前、逐小时或逐日天气。适用于用户明确询问天气、出行条件，"
        "或回答依赖实时天气的情况；不按关键词强制调用，也不要用于无关闲聊。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名或经度,纬度；留空使用常驻地点",
                    "default": "",
                },
                "forecast_range": {
                    "type": "string",
                    "enum": ["current", "nowcast", "hourly", "daily"],
                    "description": "当前、未来6小时15分钟降水、未来24小时或逐日预报",
                    "default": "current",
                },
                "days": {
                    "type": "integer",
                    "description": "逐日预报天数，1到7",
                    "default": 3,
                },
            },
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        try:
            data = await self._plugin.service.weather_snapshot(
                str(kwargs.get("location") or ""),
                str(kwargs.get("forecast_range") or "current"),
                int(kwargs.get("days") or 3),
            )
            return to_tool_json(data)
        except Exception as exc:
            return _error(str(exc))


@pydantic_dataclass
class GetAirQualityTool(FunctionTool):  # type: ignore[misc]
    name: str = "get_air_quality"
    description: str = (
        "查询某地 AQI、PM2.5、PM10、臭氧、紫外线和可用的花粉模型数据。"
        "仅在用户询问空气质量、污染、紫外线、过敏原或户外暴露条件时调用；"
        "结果不是医疗建议或当地官方监测。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名或经度,纬度；留空使用常驻地点",
                    "default": "",
                },
                "forecast_hours": {
                    "type": "integer",
                    "description": "需要的逐小时预报长度，0到24；0只返回当前值",
                    "default": 0,
                },
            },
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        try:
            data = await self._plugin.service.air_quality_snapshot(
                str(kwargs.get("location") or ""),
                int(kwargs.get("forecast_hours") or 0),
            )
            return to_tool_json(data)
        except Exception as exc:
            return _error(str(exc))


@pydantic_dataclass
class GetEnvironmentAlertsTool(FunctionTool):  # type: ignore[misc]
    name: str = "get_environment_alerts"
    description: str = (
        "查询与某个地点确实相关的环境风险：中央气象台官方预警、"
        "本地强天气模型信号和地震事件。"
        "仅在用户询问灾害、预警、安全状况或当前外界风险时调用。"
        "距离过远、震级不足或基本无影响的事件已在代码层过滤，不会返回给模型。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "关注地点；留空使用常驻地点",
                    "default": "",
                },
                "hours": {
                    "type": "integer",
                    "description": "回看时间，1到168小时",
                    "default": 24,
                },
            },
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        try:
            data = await self._plugin.service.alerts_snapshot(
                str(kwargs.get("location") or ""), int(kwargs.get("hours") or 24)
            )
            return to_tool_json(data)
        except Exception as exc:
            return _error(str(exc))


@pydantic_dataclass
class ListEnvironmentLocationsTool(FunctionTool):  # type: ignore[misc]
    name: str = "list_environment_locations"
    description: str = (
        "查看是否已经设置常驻地点。仅在需要确定默认环境地点或排查地点配置时调用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(self, context, **kwargs) -> ToolExecResult:  # type: ignore[override]
        return to_tool_json(self._plugin.service.list_locations())


def create_tools(plugin) -> list:
    tools = []
    for cls in (
        GetLocalDatetimeTool,
        GetLocalCalendarTool,
        GetWeatherTool,
        GetAirQualityTool,
        GetEnvironmentAlertsTool,
        ListEnvironmentLocationsTool,
    ):
        tool = cls()
        object.__setattr__(tool, "_plugin", plugin)
        tools.append(tool)
    return tools
