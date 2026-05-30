"""HTMLテンプレートのプレースホルダー整合性テスト"""
import re
from pathlib import Path

from src.step2_report import build_chart_init_script

TEMPLATE_PATH = Path("src/templates/report_template.html")

# step2_report.py が置換するプレースホルダーの一覧
EXPECTED_PLACEHOLDERS = {
    "{{YEAR_MONTH}}",
    "{{GENERATED_DATE}}",
    "{{ARCHIVE_LINKS}}",
    "{{AI_MODELS_TEXT}}",
    "{{COVER_SECTION}}",
    "{{KPI_STRIP_SECTION}}",
    "{{NEWS_STRATEGY_SECTION}}",
    "{{THEME_SUMMARY_CARDS}}",
    "{{CHANGES_SECTION}}",
    "{{PERFORMANCE_SECTION}}",
    "{{THEME_RANKING_SECTIONS}}",
    "{{TRADE_PLAN_SECTION}}",
    "{{SOURCE_LINKS_SECTION}}",
    "{{SECTOR_WARNING_BANNER}}",
    "{{RISK_SCENARIOS_SECTION}}",
    "{{SUPPLY_CHAIN_SECTION}}",
    "{{STOCK_COMPARISON_SECTION}}",
    "{{CHART_INIT_SCRIPT}}",
    "{{CHAT_WIDGET_SECTION}}",
}


def _load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_template_exists():
    assert TEMPLATE_PATH.exists(), f"Template not found: {TEMPLATE_PATH}"


def test_all_expected_placeholders_present():
    content = _load_template()
    for ph in EXPECTED_PLACEHOLDERS:
        assert ph in content, f"Missing placeholder in template: {ph}"


def test_no_unexpected_placeholders():
    content = _load_template()
    found = set(re.findall(r"\{\{[A-Z_]+\}\}", content))
    unknown = found - EXPECTED_PLACEHOLDERS
    assert not unknown, f"Unknown placeholders in template: {unknown}"


def test_gemini_model_not_hardcoded():
    """モデル名がハードコードされていないことを確認"""
    content = _load_template()
    assert "gemini-" not in content.lower(), (
        "Gemini model name is hardcoded in template. Use {{AI_MODELS_TEXT}} instead."
    )
    assert "claude-sonnet" not in content.lower(), (
        "Claude model name is hardcoded in template. Use {{AI_MODELS_TEXT}} instead."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chart tooltip の currency 分岐テスト
# ─────────────────────────────────────────────────────────────────────────────

def _make_stock_with_currency(code: str, currency: str) -> dict:
    return {
        "theme_name": "テスト",
        "stocks": [
            {
                "code": code,
                "currency": currency,
                "price_history_6m": {
                    "dates": ["2025-01-01"],
                    "closes": [1000],
                },
            }
        ],
    }


def test_chart_script_jp_stock_uses_yen_symbol():
    """JP 銘柄（JPY）の chartData に currency='JPY' が埋め込まれる"""
    stock_data = [_make_stock_with_currency("7203", "JPY")]
    result = build_chart_init_script(stock_data)
    assert '"currency": "JPY"' in result or '"currency":"JPY"' in result


def test_chart_script_us_stock_uses_usd_symbol():
    """US 銘柄（USD）の chartData に currency='USD' が埋め込まれる"""
    stock_data = [_make_stock_with_currency("AAPL", "USD")]
    result = build_chart_init_script(stock_data)
    assert '"currency": "USD"' in result or '"currency":"USD"' in result


def test_chart_script_tooltip_uses_currency_variable():
    """生成された JS の tooltip コールバックが currency 変数で通貨記号を分岐する"""
    stock_data = [_make_stock_with_currency("TSLA", "USD")]
    result = build_chart_init_script(stock_data)
    assert "currency" in result
    assert "USD" in result
    assert "$" in result or "\\u0024" in result or "'$'" in result


def test_chart_script_no_hardcoded_yen_only():
    """tooltip が '¥' のみをハードコードしていない（currency 変数経由）"""
    stock_data = [_make_stock_with_currency("AAPL", "USD")]
    result = build_chart_init_script(stock_data)
    # '¥' がシンボルとして単体で直書きされていないこと
    # （currency 変数を参照して分岐しているかを確認）
    assert "d.currency" in result or "data.currency" in result
