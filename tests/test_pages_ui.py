from pathlib import Path

PAGE_DIR = Path(__file__).resolve().parents[1] / "pages" / "status"


def test_environment_page_is_a_kernel_webui_guidance_page() -> None:
    html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")
    js = (PAGE_DIR / "app.js").read_text(encoding="utf-8")

    assert "凝心溯溪-境" in html
    assert "核 WebUI" in html
    assert "环境与时间" in html
    assert "AstrBot 原生插件配置页" in html
    assert "AstrBotPluginPage" not in html
    assert "apiGet" not in html and "apiGet" not in js
    assert "apiPost" not in html and "apiPost" not in js


def test_environment_page_keeps_series_ui_asset_order() -> None:
    html = (PAGE_DIR / "index.html").read_text(encoding="utf-8")

    assert "data-series-ui" in html
    positions = [
        html.find("style.css"),
        html.find("series-ui.css"),
        html.find("series-ui.js"),
        html.find("app.js"),
    ]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
    assert "style.css?v=0.6.3-1" in html
    assert "series-ui.css?v=0.6.3-1" in html
    assert "series-ui.js?v=0.6.3-1" in html
    assert "app.js?v=0.6.3-1" in html
    assert (PAGE_DIR / "series-ui.css").is_file()
    assert (PAGE_DIR / "series-ui.js").is_file()
