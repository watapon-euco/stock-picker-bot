"""XSSリスクに対するURL検証のテスト"""
import pytest

from src.utils.helpers import safe_url


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
