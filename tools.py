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
        "获取某地此刻的日期、时间、星期和时区。仅当准确的当地时刻会改变回答时调用，"
        "例如用户明确需要判断作息时间、跨时区联系或时间窗口。普通问候、泛泛提到"
        "早晚、回忆和虚构场景不要调用。地点使用规则见 location 参数。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "地点名或经度,纬度；多用户部署必须显式提供；"
                        "仅单用户部署可留空使用插件级全局常驻地点"
                    ),
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
        "获取某地某天的公共假日、周末、普通或调休工作日及下一个节假日。约见、办事、"
        "工作或出行确实受放假、补班状态影响时调用。个人排班、学校安排和商家营业"
        "无法确认；无关计划、节日文化历史或闲聊不要调用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "地点名；多用户部署必须显式提供；"
                        "仅单用户部署可留空使用插件级全局常驻地点"
                    ),
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
        "获取某地当前、未来约6小时降水、24小时或1至7日天气。用户正为穿衣或带伞、"
        "通勤或旅行、户外活动、晾晒、开窗、日出日落安排做决定，且天气会改变回答时"
        "调用。仅提到出门、上班、季节、心情，或回忆、虚构、闲聊时不要调用；不按"
        "关键词强制调用。选择最小够用范围；调用后只使用与当前决定直接相关的数据，"
        "不扩展无关环境信息或强行转题。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "地点名或经度,纬度；多用户部署必须显式提供；"
                        "仅单用户部署可留空使用插件级全局常驻地点"
                    ),
                    "default": "",
                },
                "forecast_range": {
                    "type": "string",
                    "enum": ["current", "nowcast", "hourly", "daily"],
                    "description": (
                        "current=此刻；nowcast=未来约6小时的15分钟降水；"
                        "hourly=未来24小时；daily=1至7日"
                    ),
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
        "获取某地当前或未来24小时 AQI、PM2.5、PM10、臭氧、紫外线和可用花粉模型"
        "数据。户外运动、开窗、防晒或过敏暴露的决定会受这些数据影响时调用。普通"
        "天气先用 get_weather；无关闲聊或只描述症状时不要调用。仅作环境参考，异常"
        "数据只在与当前决定相关时使用，不作医疗结论，也不冒充当地官方监测。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "地点名或经度,纬度；多用户部署必须显式提供；"
                        "仅单用户部署可留空使用插件级全局常驻地点"
                    ),
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
        "获取与地点相关的中央气象台官方预警、本地强天气模型信号和地震。用户询问"
        "灾害、预警、震感、异常天气或当前安全风险时调用；普通天气先用 get_weather，"
        "无风险线索的闲聊、影视游戏、比喻和无地点新闻不要调用。距离过远、震级不足"
        "或基本无影响的事件不会返回给模型。只使用与当前地点和问题相关的事实与安全"
        "信息，不渲染恐慌；数据源不可用不等于无风险。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "关注地点；多用户部署必须显式提供；"
                        "仅单用户部署可留空使用插件级全局常驻地点"
                    ),
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
        "查看插件级全局常驻地点是否已配置。只在管理员询问或排查地点配置时调用。"
        "多用户会话不要调用，应直接向当前用户确认地点；单用户部署查询其它环境信息"
        "时也不要预先调用，直接给目标工具传地点或使用全局常驻地点，避免增加一次往返。"
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
