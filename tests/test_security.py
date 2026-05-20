"""XSSリスクに対するURL検証のテスト"""
import pytest

from src.utils.helpers import build_source_links_html, safe_url
from src.step2_report import build_performance_html, build_sector_warning_html

_XSS_SCRIPT = "<script>alert(1)</script>"
_XSS_IMG = '<img src=x onerror=alert(1)>'


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/article", "https://example.com/article"),
    ("http://example.com/article",  "http://example.com/article"),
    ("javascript:alert(1)",         "#"),
    ("javascript:void(0)",          "#"),
    ("data:text/html,<script>",     "#"),
    ("vbscript:msgbox(1)",          "#"),
    ("",                            "#"),
    ("//example.com",               "#"),
    ("/relative/path",              "#"),
    ("ftp://example.com",           "#"),
])
def test_safe_url(url, expected):
    assert safe_url(url) == expected


def test_safe_url_preserves_query_and_fragment():
    url = "https://news.google.com/articles/abc?hl=ja&gl=JP#section"
    assert safe_url(url) == url


# ─────────────────────────────────────────────────────────────────────────────
# build_sector_warning_html XSS回帰テスト
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_sector_warning_html_escapes_sector_name(payload):
    themes_data = {
        "sector_overlap_warning": True,
        "dominant_sectors": [payload],
    }
    result = build_sector_warning_html(themes_data)
    assert "<script>" not in result
    assert "<img" not in result  # unescaped img tag must not be injected
    assert payload not in result


# ─────────────────────────────────────────────────────────────────────────────
# build_performance_html XSS回帰テスト
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_performance_html_escapes_name(payload):
    perf = [{"name": payload, "code": "1234", "theme": "tech",
              "price_at_report": 1000, "current_price": 1100, "change_pct": 10.0}]
    result = build_performance_html(perf, "2026-04")
    assert "<script>" not in result
    assert "onerror=" not in result
    assert payload not in result


@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_performance_html_escapes_code(payload):
    perf = [{"name": "テスト", "code": payload, "theme": "tech",
              "price_at_report": 1000, "current_price": 1100, "change_pct": 10.0}]
    result = build_performance_html(perf, "2026-04")
    assert "<script>" not in result
    assert "onerror=" not in result
    assert payload not in result


@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_performance_html_escapes_theme(payload):
    perf = [{"name": "テスト", "code": "1234", "theme": payload,
              "price_at_report": 1000, "current_price": 1100, "change_pct": 10.0}]
    result = build_performance_html(perf, "2026-04")
    assert "<script>" not in result
    assert "onerror=" not in result
    assert payload not in result


# ─────────────────────────────────────────────────────────────────────────────
# build_source_links_html XSS回帰テスト
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_source_links_html_escapes_title(payload):
    themes = [{"name": "テーマ1", "icon": "💹", "source_articles": [1], "keywords": []}]
    articles = [{"title": payload, "link": "https://example.com", "source": "Reuters"}]
    result = build_source_links_html(themes, articles)
    assert "<script>" not in result
    assert "<img" not in result  # unescaped img tag must not be injected
    assert payload not in result


@pytest.mark.parametrize("payload", [_XSS_SCRIPT, _XSS_IMG])
def test_build_source_links_html_escapes_source(payload):
    themes = [{"name": "テーマ1", "icon": "💹", "source_articles": [1], "keywords": []}]
    articles = [{"title": "正常タイトル", "link": "https://example.com", "source": payload}]
    result = build_source_links_html(themes, articles)
    assert "<script>" not in result
    assert "onerror=" not in result
    assert payload not in result
