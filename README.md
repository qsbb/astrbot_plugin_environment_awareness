# 凝心溯溪-境

为 AstrBot 提供按需的当地日历、天气、空气质量、官方气象预警与自然事件感知。普通聊天不请求环境网络数据、不常驻注入天气；只有模型或用户实际调用工具时才联网查询。公共假日或调休工作日可以在当地当天首次对话提供一条离线轻量事实，并明确要求模型只在自然相关时参考。

当前正式版本为 `0.1.0`。

## 特点

- 开箱即用：Open-Meteo、中央气象台公开预警列表、USGS 和 python-holidays 均不要求 API Key。
- 只需一个地点：在插件页面填写常驻城市即可；临时查询可以直接写地点，无需配置。
- 模型自主决定：注册语义清晰的只读工具，不增加一次“要不要查询”的 LLM 请求，不使用关键词硬路由。
- 现实日历：区分公共假日、普通周末和调休工作日；不把中国周末简单等同于休息日。
- 环境细节：提供 AQI、颗粒物、紫外线、可用花粉、日出日落、白昼长度、能见度和未来约 6 小时的 15 分钟降水模型数据。
- 预警分源：中央气象台官方预警与 Open-Meteo 阈值风险信号使用独立字段和标签，不互相冒充。
- 本地相关性过滤：距离过远、震级不足或预计基本无影响的地震不会返回给模型。
- 不误报无风险：数据源失败与“已查询但没有相关事件”是两种不同状态。
- 隐私克制：经纬度不进入工具公共结果，远处事件的详情和数量也不进入模型上下文。

## 快速开始

1. 安装插件并重载。
2. 打开插件详情中的“status”页面。
3. 在“常驻地点”填写城市，例如 `杭州`，点击“保存并校验”。
4. 点击“测试数据源”确认日历、天气、空气质量、官方预警与本地相关性过滤可用。

没有设置常驻地点时也可以直接询问“上海现在天气怎么样”，模型会把上海作为临时地点传给工具；只有未带地点的“我这里天气如何”需要常驻地点。

无需注册天气账号，无需配置密钥，也不会在安装后启动主动消息。保存地点后会在插件配置中保留一份内部解析缓存，用于让重要日感知在重启后也不需要先联网；经纬度不会进入 LLM 工具公共结果。

## LLM 工具

| 工具 | 用途 |
|---|---|
| `get_local_datetime` | 查询地点当前日期、时间、星期和时区 |
| `get_local_calendar` | 查询公共假日、周末、调休工作日和下一个节假日 |
| `get_weather` | 查询当前、未来约 6 小时 15 分钟降水、未来 24 小时或 1-7 日天气 |
| `get_air_quality` | 查询 AQI、污染物、紫外线和可用花粉数据 |
| `get_environment_alerts` | 查询本地官方气象预警、强天气模型信号与相关地震 |
| `list_environment_locations` | 检查常驻地点是否已配置 |

工具说明会引导模型只在问题依赖现实时间、节假日、天气、空气或环境风险时调用。插件不会把天气塞进每轮 system prompt。`calendar_awareness_enabled=true` 时，只有公共假日或调休工作日会在当地当天首次 LLM 请求加入一条短日历事实；普通日期、同日后续对话和未校验地点均不注入，也不会为该轻量感知发起网络请求。

## 手动命令

```text
/境
/境时间 [地点]
/境日历 [地点] [YYYY-MM-DD]
/境天气 [地点] [current|nowcast|hourly|daily]
/境空气 [地点] [预报小时数]
/境预警 [地点] [回看小时数]
```

示例：

```text
/境天气 杭州
/境天气 杭州 nowcast
/境天气 上海 daily
/境日历 杭州 2026-02-14
/境空气 杭州 12
/境预警 成都 24
```

## 节假日与调休

节假日由 `python-holidays` 离线计算。中国日历会使用库中已收录的年度官方放假安排区分法定假日和周末补班；例如 `2026-02-17` 是春节休息日，而 `2026-02-14` 是调休工作日。

- 城市查询自动使用地点的 ISO 国家代码。
- 纯经纬度无法确认国家时，可设置 `calendar_country_code`。
- 部分国家的地方性假日可通过 `holiday_subdivision` 指定。
- 尚未公布或尚未被依赖库收录的未来调休不会被猜测；应更新依赖或等待正式安排。

## 空气质量、日照与短临天气

- 空气质量来自 Open-Meteo CAMS 模型，包括 European AQI、US AQI、PM2.5、PM10、气态污染物、UV 和当前地区可用的花粉数据。
- 花粉字段在许多地区可能为空；插件会明确返回不可用，不补造数值。
- 当前天气结果附带当日日出、日落、白昼长度和能见度。
- `nowcast` 返回未来 24 个 15 分钟间隔，即约 6 小时的降水模型序列与摘要。它不是气象雷达临近预警。

这些数据用于环境感知，不替代当地空气监测、医疗建议或防晒指导。

## 中央气象台官方预警

中国地点启用 `official_weather_warnings_enabled` 后，插件查询中央气象台当前预警公开列表：

1. 根据地点解析得到省级行政区，只查询对应省份。
2. 对城市和区县再次匹配发布机构；同省其它城市的预警不会返回。
3. 省级气象台发布的预警视为省域相关，并标记 `scope=province`。
4. 缺少发布时间、标题结构异常、超过 `official_warning_max_age_hours` 或未来时间异常的记录会被过滤。

非中国地点返回 `official_warning_status=unsupported_region`，不把“不支持”描述成“没有预警”。使用纯经纬度时可设置 `official_warning_province`，但缺少城市信息时只保留省级发布的预警。该公开接口不可用时会返回 `unavailable`，Open-Meteo 模型风险信号仍保持独立结果。

## 地震相关性过滤

地震必须同时满足：

1. 不低于 `earthquake_min_magnitude`。
2. 不超过管理员设置的 `earthquake_max_distance_km`。
3. 震中距与深度合成后的有效距离，不超过震级对应的感知半径。

默认震级半径：

| 震级 | 半径上限 |
|---:|---:|
| `< 3.0` | 15 km |
| `3.0-3.9` | 40 km |
| `4.0-4.9` | 100 km |
| `5.0-5.9` | 250 km |
| `6.0-6.9` | 600 km |
| `7.0-7.9` | 1200 km |
| `>= 8.0` | 2500 km，但仍受绝对最远距离限制 |

达到最低震级且处于 `earthquake_nearby_radius_km` 内的近场事件会保留。带海啸标记的强震可以使用单独半径，但同样不能突破绝对最远距离。

这是一套保守的“是否值得感知”启发式算法，不是烈度预测。返回内容会明确保留该限制。

## 本地天气风险信号

Open-Meteo 不提供中国官方预警。插件只把当地模型预报达到以下阈值的结果称为“风险信号”，并与中央气象台官方预警分栏：

- 日累计降水默认 `50 mm`
- 阵风默认 `62 km/h`
- 最高温默认 `40°C`
- 最低温默认 `-20°C`
- WMO 雷暴代码 `95/96/99`

所有阈值均可调整。风险信号不会冒充气象部门发布的预警。

## 数据源

- [Open-Meteo Weather Forecast API](https://open-meteo.com/en/docs)：地点解析、当前天气、逐小时与逐日模型预报。
- [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)：空气质量、紫外线和可用花粉模型数据。
- [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api)：城市名解析与时区。
- [中央气象台预警信息](https://www.nmc.cn/publish/alarm.html)：中国当前官方气象预警公开列表。
- [USGS Earthquake GeoJSON Feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)：全球地震结构化数据。
- [python-holidays](https://python-holidays.readthedocs.io/)：离线公共假日、工作日和已收录调休规则。

USGS 对不同地区、不同震级的收录完整性可能不同。插件不能替代中国地震台网、中国气象局、当地应急部门或其它正式信息渠道。

## 主要配置

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `default_location` | 空 | 常驻城市或经度,纬度 |
| `forecast_days` | 3 | 默认逐日预报天数，范围 1-7 |
| `calendar_awareness_enabled` | true | 重要节假日/调休在当地当天首次轻量感知 |
| `calendar_country_code` | 空 | 节假日 ISO 国家代码手动覆盖 |
| `holiday_subdivision` | 空 | 可选地方节假日行政区代码 |
| `official_weather_warnings_enabled` | true | 是否查询中央气象台当前预警 |
| `official_warning_max_age_hours` | 72 | 官方预警最大发布时长 |
| `official_warning_province` | 空 | 省级地区手动覆盖 |
| `earthquake_min_magnitude` | 2.5 | 进入相关性判断的最低震级 |
| `earthquake_max_distance_km` | 1200 | 任何地震都不能突破的最远距离 |
| `earthquake_nearby_radius_km` | 30 | 近场关注半径 |
| `max_hazard_events` | 5 | 单次最多返回事件数 |
| `weather_risk_enabled` | true | 是否计算本地天气模型风险信号 |
| `air_quality_ttl_seconds` | 1800 | 空气质量缓存时间 |
| `request_timeout_seconds` | 6 | 单次数据源请求超时 |
| `stale_cache_seconds` | 3600 | 故障时允许使用的旧缓存时长 |

旧缓存会带 `stale=true`，不会伪装成实时结果。

## 当前边界

- 初步版本不做主动灾害推送，避免刚安装就打扰用户。
- 已接入中央气象台当前气象预警公开列表；暂未接入中国地震速报和海啸预警接口。
- 官方预警目前只支持能确定中国省级行政区的地点，不使用 IP 猜测位置。
- 不推测用户位置，不从聊天内容静默保存住址。
- 不把天气和灾害事件写入“知”的长期记忆。
- 不读取“情”的好感值，也不因关系高低改变安全阈值。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
python -m compileall -q .
node --check pages/status/app.js
```

每次增删改功能时，应同步更新 README、CHANGELOG、配置说明和对应测试。

## 许可证

本项目采用 [MIT License](LICENSE)。
