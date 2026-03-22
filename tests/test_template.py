"""HTMLテンプレートのプレースホルダー整合性テスト"""
import re
from pathlib import Path

TEMPLATE_PATH = Path("src/templates/report_template.html")

# step2_report.py が置換するプレースホルダーの一覧
EXPECTED_PLACEHOLDERS = {
    "{{YEAR_MONTH}}",
    "{{GENERATED_DATE}}",
    "{{THEME_COUNT}}",
    "{{TOTAL_STOCKS}}",
    "{{ARCHIVE_LINKS}}",
    "{{AI_MODELS_TEXT}}",
    "{{NEWS_STRATEGY_SECTION}}",
    "{{THEME_SUMMARY_CARDS}}",
    "{{CHANGES_SECTION}}",
    "{{PERFORMANCE_SECTION}}",
    "{{THEME_RANKING_SECTIONS}}",
    "{{SOURCE_LINKS_SECTION}}",
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
