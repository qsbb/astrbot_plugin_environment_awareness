from pathlib import Path


PAGE_DIR = Path(__file__).resolve().parents[1] / "pages" / "status"


def test_environment_mobile_metrics_keep_two_columns() -> None:
    css = (PAGE_DIR / "style.css").read_text(encoding="utf-8")
    narrow = css[css.index("@media (max-width: 520px)") : css.index("/* 操作按钮不拆字")]

    assert "body[data-series-ui] .cards," in narrow
    assert "body[data-series-ui] .usage-summary," in narrow
    assert "body[data-series-ui] .probe-grid {" in narrow
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in narrow
    assert "body[data-series-ui] .filter-list {" in narrow


def test_environment_config_stays_savable_and_explains_consequences() -> None:
    html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
    js = (PAGE_DIR / "app.js").read_text(encoding="utf-8")
    css = (PAGE_DIR / "style.css").read_text(encoding="utf-8")

    # sticky 保存栏 + 未保存计数
    assert 'id="config-dirty"' in html
    assert 'class="config-save-actions"' in html
    assert "body[data-series-ui] .config-card > .section-heading" in css
    assert "position: sticky;" in css
    assert "classList.toggle(\"is-dirty\", dirty > 0)" in js or "classList.toggle(\"is-dirty\", dirty > 0);" in js
    assert "function updateConfigDirty()" in js
    assert "configSnapshot = {};" in js
    assert 'elements.configForm.addEventListener("input", updateConfigDirty);' in js

    # 危险开关影响说明：至少覆盖主动发送与官方预警
    assert "const consequenceNotes = {" in js
    for key in (
        "proactive_enabled",
        "proactive_paused",
        "official_weather_warnings_enabled",
        "opportunity_cache_enabled",
        "calendar_awareness_enabled",
        "weather_risk_enabled",
    ):
        assert f"{key}:" in js
    assert "consequence-note is-${note.level}" in js
    assert "body[data-series-ui] .consequence-note" in css
    assert "body[data-series-ui] .consequence-note.is-danger" in css


def test_environment_config_groups_cover_sources_and_cache() -> None:
    js = (PAGE_DIR / "app.js").read_text(encoding="utf-8")

    assert '{ title: "数据源与缓存"' not in js  # 组定义使用多行对象，避免误匹配
    assert 'title: "数据源与缓存"' in js
    for key in (
        "request_timeout_seconds",
        "weather_current_ttl_seconds",
        "weather_forecast_ttl_seconds",
        "air_quality_ttl_seconds",
        "hazard_ttl_seconds",
        "stale_cache_seconds",
    ):
        assert f'"{key}"' in js
