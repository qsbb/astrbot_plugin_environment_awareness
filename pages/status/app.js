const bridge = window.AstrBotPluginPage;

if (!bridge) {
  const banner = document.getElementById("bridge-error");
  if (banner) banner.hidden = false;
  throw new Error("AstrBot 页面桥接未加载");
}

const elements = {
  runtimeStatus: document.getElementById("runtime-status"),
  version: document.getElementById("plugin-version"),
  location: document.getElementById("default-location"),
  locationState: document.getElementById("location-state"),
  setupResult: document.getElementById("setup-result"),
  setupForm: document.getElementById("setup-form"),
  save: document.getElementById("save"),
  locate: document.getElementById("use-device-location"),
  refresh: document.getElementById("refresh"),
  configForm: document.getElementById("config-form"),
  configGroups: document.getElementById("config-groups"),
  configResult: document.getElementById("config-result"),
  saveConfig: document.getElementById("save-config"),
  usageTotal: document.getElementById("usage-total"),
  usageSuccess: document.getElementById("usage-success"),
  usageFailed: document.getElementById("usage-failed"),
  usageTopSource: document.getElementById("usage-top-source"),
  usageRecent: document.getElementById("usage-recent"),
  usagePrivacy: document.getElementById("usage-privacy"),
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
  pageNotice: document.getElementById("page-notice"),
};

const tabButtons = [...document.querySelectorAll('.tabs button[data-tab]')];

function activateTab(target) {
  tabButtons.forEach((button) => {
    const active = button.dataset.tab === target;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".panel[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === target);
  });
}

tabButtons.forEach((button, index) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    let targetIndex;
    if (event.key === "ArrowLeft") {
      targetIndex = (index - 1 + tabButtons.length) % tabButtons.length;
    } else if (event.key === "ArrowRight") {
      targetIndex = (index + 1) % tabButtons.length;
    } else if (event.key === "Home") {
      targetIndex = 0;
    } else if (event.key === "End") {
      targetIndex = tabButtons.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const target = tabButtons[targetIndex];
    activateTab(target.dataset.tab);
    target.focus();
  });
});

const weatherNames = new Map([
  [0, "晴"], [1, "大致晴朗"], [2, "局部多云"], [3, "阴"],
  [45, "雾"], [48, "雾凇"], [51, "小毛毛雨"], [53, "毛毛雨"],
  [55, "强毛毛雨"], [61, "小雨"], [63, "中雨"], [65, "大雨"],
  [71, "小雪"], [73, "中雪"], [75, "大雪"], [80, "小阵雨"],
  [81, "阵雨"], [82, "强阵雨"], [95, "雷暴"],
  [96, "雷暴伴冰雹"], [99, "强雷暴伴冰雹"],
]);

const configGroups = [
  {
    title: "页面与基础",
    keys: ["language", "forecast_days"],
  },
  {
    title: "日历与官方预警",
    keys: [
      "calendar_awareness_enabled", "calendar_country_code", "holiday_subdivision",
      "official_weather_warnings_enabled", "official_warning_max_age_hours",
      "official_warning_province",
    ],
  },
  {
    title: "环境关心候选",
    keys: [
      "opportunity_cache_enabled", "opportunity_refresh_seconds",
      "opportunity_min_severity", "opportunity_european_aqi_threshold",
      "opportunity_us_aqi_threshold", "opportunity_uv_threshold",
      "opportunity_temperature_drop_c",
    ],
  },
  {
    title: "主动环境关心",
    keys: [
      "care_person_id", "care_recipient_umo", "proactive_enabled",
      "proactive_paused", "proactive_min_severity", "proactive_quiet_start",
      "proactive_quiet_end", "proactive_daily_limit",
    ],
  },
  {
    title: "风险相关性与阈值",
    keys: [
      "earthquake_min_magnitude", "earthquake_max_distance_km",
      "earthquake_nearby_radius_km", "tsunami_relevance_distance_km",
      "max_hazard_events", "weather_risk_enabled", "heavy_rain_mm",
      "strong_wind_kmh", "extreme_heat_c", "extreme_cold_c",
    ],
  },
  {
    title: "数据源与缓存",
    keys: [
      "request_timeout_seconds", "weather_current_ttl_seconds",
      "weather_forecast_ttl_seconds", "air_quality_ttl_seconds",
      "hazard_ttl_seconds", "stale_cache_seconds",
    ],
  },
];

const sourceNames = {
  command: "手动命令",
  llm_tool: "LLM 工具",
  awareness: "轻量感知",
  proactive: "主动关心",
};

const statusNames = {
  success: "成功",
  error: "失败",
  suppressed: "未发送",
};

const optionLabels = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "严重",
  zh: "中文",
  en: "英文",
};

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function setResult(element, message, type = "") {
  element.textContent = message;
  element.className = `result ${type}`.trim();
}

function setPageNotice(message = "") {
  elements.pageNotice.textContent = message;
  elements.pageNotice.hidden = !message;
}

function setLocationState(configured) {
  elements.locationState.textContent = configured ? "已设置" : "未设置";
  elements.locationState.className = `pill ${configured ? "ready" : "neutral"}`;
}

function formatTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function renderUsage(usage = {}) {
  elements.usageTotal.textContent = String(usage.total ?? 0);
  elements.usageSuccess.textContent = String(usage.successful ?? 0);
  elements.usageFailed.textContent = String(usage.failed ?? 0);
  const sourceEntries = Object.entries(usage.by_source || {});
  sourceEntries.sort((left, right) => Number(right[1]) - Number(left[1]));
  const topSource = sourceEntries[0];
  elements.usageTopSource.textContent = topSource
    ? `${sourceNames[topSource[0]] || topSource[0]} · ${topSource[1]}`
    : "-";
  elements.usagePrivacy.textContent = usage.privacy || "";

  elements.usageRecent.replaceChildren();
  const recent = Array.isArray(usage.recent) ? usage.recent : [];
  if (recent.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty-cell";
    cell.textContent = "暂无调用记录";
    row.append(cell);
    elements.usageRecent.append(row);
    return;
  }
  for (const item of recent) {
    const row = document.createElement("tr");
    const values = [
      formatTimestamp(item.timestamp),
      sourceNames[item.source] || item.source || "-",
      item.action || "-",
      statusNames[item.status] || item.status || "-",
      `${Number(item.duration_ms || 0)} ms`,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      if (index === 3) cell.dataset.status = item.status || "";
      row.append(cell);
    });
    elements.usageRecent.append(row);
  }
}

function renderRuntime(status) {
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
  renderUsage(status.usage);
}

async function loadStatus() {
  elements.runtimeStatus.textContent = "读取中";
  elements.runtimeStatus.className = "";
  try {
    const status = await bridge.apiGet("status");
    elements.version.textContent = status.plugin?.version || "-";
    elements.runtimeStatus.textContent = status.ready ? "正常" : "异常";
    elements.runtimeStatus.className = status.ready ? "accent" : "danger";
    renderRuntime(status);
    return true;
  } catch (error) {
    elements.runtimeStatus.textContent = "连接失败";
    elements.runtimeStatus.className = "danger";
    setResult(elements.setupResult, error?.message || "无法读取插件状态", "error");
    return false;
  }
}

function createConfigField(key, field, value) {
  const wrapper = document.createElement("div");
  wrapper.className = `config-field ${field.type === "bool" ? "toggle-field" : ""}`;
  const inputId = `config-${key}`;
  const label = document.createElement("label");
  label.htmlFor = inputId;
  label.textContent = field.description || key;

  let input;
  if (field.type === "string" && Array.isArray(field.options)) {
    input = document.createElement("select");
    for (const option of field.options) {
      const item = document.createElement("option");
      item.value = option;
      item.textContent = optionLabels[option] || option;
      input.append(item);
    }
    input.value = value ?? field.default ?? "";
  } else {
    input = document.createElement("input");
    if (field.type === "bool") {
      input.type = "checkbox";
      input.checked = Boolean(value);
    } else if (field.type === "int" || field.type === "float") {
      input.type = "number";
      input.step = field.type === "int" ? "1" : "any";
      if (field.minimum != null) input.min = String(field.minimum);
      if (field.maximum != null) input.max = String(field.maximum);
      input.value = String(value ?? field.default ?? "");
    } else {
      input.type = "text";
      input.value = String(value ?? field.default ?? "");
      input.maxLength = 512;
      input.autocomplete = "off";
    }
  }
  input.id = inputId;
  input.name = key;
  input.classList.add("config-input");
  input.dataset.kind = field.type || "string";
  input.dataset.label = field.description || key;
  input.addEventListener("input", () => input.removeAttribute("aria-invalid"));

  if (field.type === "bool") {
    const line = document.createElement("div");
    line.className = "toggle-line";
    line.append(input, label);
    wrapper.append(line);
  } else {
    wrapper.append(label, input);
  }
  if (field.hint) {
    const hint = document.createElement("p");
    hint.className = "field-hint";
    hint.textContent = field.hint;
    wrapper.append(hint);
  }
  return wrapper;
}

function renderConfig(schema, config) {
  elements.configGroups.replaceChildren();
  const rendered = new Set(["default_location"]);
  configGroups.forEach((group, index) => {
    const fields = group.keys.filter((key) => schema[key]);
    if (fields.length === 0) return;
    const details = document.createElement("details");
    details.className = "config-group";
    details.open = index < 2;
    const summary = document.createElement("summary");
    summary.textContent = group.title;
    const grid = document.createElement("div");
    grid.className = "config-grid";
    for (const key of fields) {
      rendered.add(key);
      grid.append(createConfigField(key, schema[key], config[key]));
    }
    details.append(summary, grid);
    elements.configGroups.append(details);
  });
  const remaining = Object.keys(schema).filter((key) => !rendered.has(key));
  if (remaining.length > 0) {
    const details = document.createElement("details");
    details.className = "config-group";
    const summary = document.createElement("summary");
    summary.textContent = "其它";
    const grid = document.createElement("div");
    grid.className = "config-grid";
    remaining.forEach((key) => grid.append(createConfigField(key, schema[key], config[key])));
    details.append(summary, grid);
    elements.configGroups.append(details);
  }
}

async function loadConfig() {
  try {
    const result = await bridge.apiGet("config");
    renderConfig(result.schema || {}, result.config || {});
    setPageNotice();
  } catch (error) {
    const message = error?.message || "无法读取配置";
    setResult(elements.configResult, message, "error");
    setPageNotice(`配置读取失败：${message}`);
    throw error;
  }
}

function readNumericConfig(input, integer) {
  const value = input.value.trim();
  const parsed = Number(value);
  const label = `“${input.dataset.label || input.name}”`;
  if (!value || !Number.isFinite(parsed) || (integer && !Number.isInteger(parsed))) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
    throw new Error(`${label}需要填写${integer ? "整数" : "数字"}`);
  }
  const minimum = input.min === "" ? null : Number(input.min);
  const maximum = input.max === "" ? null : Number(input.max);
  if (minimum != null && parsed < minimum) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
    throw new Error(`${label}不能小于 ${minimum}`);
  }
  if (maximum != null && parsed > maximum) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
    throw new Error(`${label}不能大于 ${maximum}`);
  }
  input.removeAttribute("aria-invalid");
  return parsed;
}

function collectConfig() {
  const payload = {};
  for (const input of elements.configForm.querySelectorAll(".config-input")) {
    if (input.dataset.kind === "bool") {
      payload[input.name] = input.checked;
    } else if (input.dataset.kind === "int") {
      payload[input.name] = readNumericConfig(input, true);
    } else if (input.dataset.kind === "float") {
      payload[input.name] = readNumericConfig(input, false);
    } else {
      payload[input.name] = input.value;
    }
  }
  return payload;
}

async function saveDefaultLocation(defaultLocation, progressMessage = "正在校验地点…") {
  if (!defaultLocation) {
    setResult(elements.setupResult, "请填写城市或经度,纬度", "error");
    return false;
  }
  setBusy(elements.save, true);
  setBusy(elements.locate, true);
  setResult(elements.setupResult, progressMessage);
  try {
    const result = await bridge.apiPost("setup", { default_location: defaultLocation });
    const resolved = result.resolved?.name || defaultLocation;
    elements.location.value = result.default_location || defaultLocation;
    setLocationState(true);
    setResult(elements.setupResult, `已保存：${resolved}`, "success");
    return true;
  } catch (error) {
    setResult(elements.setupResult, error?.message || "保存失败", "error");
    return false;
  } finally {
    setBusy(elements.save, false);
    setBusy(elements.locate, false);
  }
}

elements.setupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveDefaultLocation(elements.location.value.trim());
});

elements.locate.addEventListener("click", async () => {
  if (!window.isSecureContext) {
    setResult(elements.setupResult, "浏览器定位仅允许在 HTTPS 或 localhost 页面使用", "error");
    return;
  }
  if (!navigator.geolocation) {
    setResult(elements.setupResult, "当前浏览器不支持设备定位", "error");
    return;
  }
  setBusy(elements.locate, true);
  setResult(elements.setupResult, "正在请求设备定位权限…");
  try {
    const position = await new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: false,
        timeout: 12000,
        maximumAge: 300000,
      });
    });
    const longitude = Number(position.coords.longitude).toFixed(6);
    const latitude = Number(position.coords.latitude).toFixed(6);
    const value = `${longitude},${latitude}`;
    elements.location.value = value;
    await saveDefaultLocation(value, "已获取设备位置，正在校验并保存…");
  } catch (error) {
    const messages = {
      1: "定位权限被拒绝",
      2: "暂时无法获取设备位置",
      3: "获取设备位置超时",
    };
    setResult(elements.setupResult, messages[error?.code] || "设备定位失败", "error");
    setBusy(elements.locate, false);
  }
});

elements.configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy(elements.saveConfig, true);
  setResult(elements.configResult, "正在保存配置…");
  try {
    const result = await bridge.apiPost("config", collectConfig());
    const count = Array.isArray(result.changed) ? result.changed.length : 0;
    setResult(
      elements.configResult,
      count > 0 ? `已保存 ${count} 项配置` : "配置没有变化",
      "success",
    );
    await Promise.all([loadStatus(), loadConfig()]);
  } catch (error) {
    setResult(elements.configResult, error?.message || "配置保存失败", "error");
  } finally {
    setBusy(elements.saveConfig, false);
  }
});

elements.probe.addEventListener("click", async () => {
  setBusy(elements.probe, true);
  setResult(elements.probeResult, "正在查询当地日历、天气、空气与相关事件…");
  try {
    const result = await bridge.apiPost("probe", { location: elements.location.value.trim() });
    elements.probeLocation.textContent = result.location?.name || "-";
    const code = Number(result.weather?.weather_code);
    const weather = Number.isFinite(code) ? (weatherNames.get(code) || `天气代码 ${code}`) : "无数据";
    const temperature = result.weather?.temperature;
    elements.probeWeather.textContent = temperature == null ? weather : `${weather} · ${temperature}°C`;
    const aqi = result.air_quality?.european_aqi;
    const uv = result.air_quality?.uv_index;
    elements.probeAirQuality.textContent = aqi == null && uv == null
      ? "无数据"
      : `AQI ${aqi ?? "-"} · UV ${uv ?? "-"}`;
    elements.probePollen.textContent = result.air_quality?.pollen_available ? "可用" : "当前地区无数据";
    const calendarName = result.calendar?.holiday_name;
    elements.probeCalendar.textContent = calendarName || ({
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
      : officialStatus === "unavailable" ? "数据源不可用" : String(officialCount);
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

async function refreshAll() {
  await Promise.all([
    loadStatus(),
    loadConfig().catch(() => false),
  ]);
}

elements.refresh.addEventListener("click", async () => {
  setBusy(elements.refresh, true);
  try {
    await refreshAll();
  } finally {
    setBusy(elements.refresh, false);
  }
});

await bridge.ready();
await refreshAll();
