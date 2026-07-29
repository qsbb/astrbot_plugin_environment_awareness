const bridge = window.AstrBotPluginPage;

const elements = {
  runtimeStatus: document.getElementById("runtime-status"),
  version: document.getElementById("plugin-version"),
  location: document.getElementById("default-location"),
  locationState: document.getElementById("location-state"),
  setupResult: document.getElementById("setup-result"),
  setupForm: document.getElementById("setup-form"),
  save: document.getElementById("save"),
  refresh: document.getElementById("refresh"),
  probe: document.getElementById("probe"),
  probeResult: document.getElementById("probe-result"),
  probeLocation: document.getElementById("probe-location"),
  probeWeather: document.getElementById("probe-weather"),
  probeAirQuality: document.getElementById("probe-air-quality"),
  probePollen: document.getElementById("probe-pollen"),
  probeCalendar: document.getElementById("probe-calendar"),
  probeWeatherRisks: document.getElementById("probe-weather-risks"),
  probeOfficialWarnings: document.getElementById("probe-official-warnings"),
  probeEarthquakes: document.getElementById("probe-earthquakes"),
  filterMagnitude: document.getElementById("filter-magnitude"),
  filterDistance: document.getElementById("filter-distance"),
  filterNearby: document.getElementById("filter-nearby"),
  filterCalendarAwareness: document.getElementById("filter-calendar-awareness"),
  filterOfficialWarnings: document.getElementById("filter-official-warnings"),
  opportunityCache: document.getElementById("opportunity-cache"),
  proactiveStatus: document.getElementById("proactive-status"),
};

const weatherNames = new Map([
  [0, "晴"],
  [1, "大致晴朗"],
  [2, "局部多云"],
  [3, "阴"],
  [45, "雾"],
  [48, "雾凇"],
  [51, "小毛毛雨"],
  [53, "毛毛雨"],
  [55, "强毛毛雨"],
  [61, "小雨"],
  [63, "中雨"],
  [65, "大雨"],
  [71, "小雪"],
  [73, "中雪"],
  [75, "大雪"],
  [80, "小阵雨"],
  [81, "阵雨"],
  [82, "强阵雨"],
  [95, "雷暴"],
  [96, "雷暴伴冰雹"],
  [99, "强雷暴伴冰雹"],
]);

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function setResult(element, message, type = "") {
  element.textContent = message;
  element.className = `result ${type}`.trim();
}

function setLocationState(configured) {
  elements.locationState.textContent = configured ? "已设置" : "未设置";
  elements.locationState.className = `state ${configured ? "ready" : "neutral"}`;
}

async function loadStatus() {
  elements.runtimeStatus.textContent = "读取中";
  try {
    const status = await bridge.apiGet("status");
    elements.runtimeStatus.textContent = status.ready ? "正常" : "异常";
    elements.version.textContent = status.plugin?.version || "-";
    elements.location.value = status.default_location || "";
    setLocationState(Boolean(status.default_location));
    elements.filterMagnitude.textContent = String(
      status.filters?.earthquake_min_magnitude ?? "-",
    );
    elements.filterDistance.textContent = status.filters?.earthquake_max_distance_km != null
      ? `${status.filters.earthquake_max_distance_km} km`
      : "-";
    elements.filterNearby.textContent = status.filters?.earthquake_nearby_radius_km != null
      ? `${status.filters.earthquake_nearby_radius_km} km`
      : "-";
    elements.filterCalendarAwareness.textContent = status.calendar_awareness_enabled
      ? "当地当天首次"
      : "关闭";
    elements.filterOfficialWarnings.textContent = status.filters?.official_weather_warnings_enabled
      ? "开启"
      : "关闭";
    const opportunity = status.opportunity_cache || {};
    const candidate = opportunity.candidate;
    elements.opportunityCache.textContent = !opportunity.enabled
      ? "关闭"
      : candidate
        ? `${candidate.severity || "-"} · ${candidate.kind || "-"}${candidate.stale ? " · 旧" : ""}`
        : opportunity.background_task_running
          ? "后台运行 · 暂无候选"
          : "等待常驻地点";
    const proactive = status.proactive_delivery || {};
    elements.proactiveStatus.textContent = !proactive.enabled
      ? "关闭"
      : proactive.paused
        ? "已暂停"
        : proactive.status || "等待检查";
  } catch (error) {
    elements.runtimeStatus.textContent = "连接失败";
    setResult(elements.setupResult, error?.message || "无法读取插件状态", "error");
  }
}

elements.setupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const defaultLocation = elements.location.value.trim();
  if (!defaultLocation) {
    setResult(elements.setupResult, "请填写城市或经度,纬度", "error");
    return;
  }
  setBusy(elements.save, true);
  setResult(elements.setupResult, "正在校验地点…");
  try {
    const result = await bridge.apiPost("setup", { default_location: defaultLocation });
    const resolved = result.resolved?.name || defaultLocation;
    elements.location.value = result.default_location || defaultLocation;
    setLocationState(true);
    setResult(elements.setupResult, `已保存：${resolved}`, "success");
  } catch (error) {
    setResult(elements.setupResult, error?.message || "保存失败", "error");
  } finally {
    setBusy(elements.save, false);
  }
});

elements.probe.addEventListener("click", async () => {
  setBusy(elements.probe, true);
  setResult(elements.probeResult, "正在查询当地日历、天气、空气与相关事件…");
  try {
    const result = await bridge.apiPost("probe", { location: elements.location.value.trim() });
    elements.probeLocation.textContent = result.location?.name || "-";
    const code = Number(result.weather?.weather_code);
    const weather = weatherNames.get(code) || `天气代码 ${code}`;
    const temperature = result.weather?.temperature;
    elements.probeWeather.textContent = temperature == null
      ? weather
      : `${weather} · ${temperature}°C`;
    const aqi = result.air_quality?.european_aqi;
    const uv = result.air_quality?.uv_index;
    elements.probeAirQuality.textContent = aqi == null && uv == null
      ? "无数据"
      : `AQI ${aqi ?? "-"} · UV ${uv ?? "-"}`;
    elements.probePollen.textContent = result.air_quality?.pollen_available
      ? "可用"
      : "当前地区无数据";
    const calendarName = result.calendar?.holiday_name;
    elements.probeCalendar.textContent = calendarName
      || ({
        adjusted_workday: "调休工作日",
        weekend: "周末",
        working_day: "工作日",
        day_off: "休息日",
      }[result.calendar?.day_type] || "-");
    elements.probeWeatherRisks.textContent = String(result.alerts?.weather_signal_count ?? 0);
    const officialCount = result.alerts?.official_warning_count ?? 0;
    const officialStatus = result.alerts?.official_warning_status;
    elements.probeOfficialWarnings.textContent = officialStatus === "unsupported_region"
      ? "当前地区不支持"
      : officialStatus === "unavailable"
        ? "数据源不可用"
        : String(officialCount);
    elements.probeEarthquakes.textContent = String(result.alerts?.earthquake_count ?? 0);
    const partial = Object.keys(result.alerts?.provider_errors || {}).length > 0
      || Object.keys(result.component_errors || {}).length > 0;
    setResult(
      elements.probeResult,
      partial ? "已完成，部分数据源暂不可用" : "数据源与相关性过滤正常",
      partial ? "warning" : "success",
    );
  } catch (error) {
    setResult(elements.probeResult, error?.message || "测试失败", "error");
  } finally {
    setBusy(elements.probe, false);
  }
});

elements.refresh.addEventListener("click", loadStatus);

await bridge.ready();
await loadStatus();
