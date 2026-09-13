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
