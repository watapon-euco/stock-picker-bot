"""ソースリンクHTML生成のテスト"""
from src.utils.helpers import build_source_links_html


THEMES = [
    {
        "name": "防衛・宇宙テック",
        "icon": "🚀",
        "keywords": ["防衛", "宇宙"],
        "source_articles": [1, 2],
    }
]

ARTICLES = [
    {"title": "防衛省、予算拡大を発表", "link": "https://news.example.com/1", "source": "日経"},
    {"title": "宇宙ビジネス急成長",      "link": "https://news.example.com/2", "source": "Bloomberg"},
    {"title": "無関係な記事",             "link": "https://news.example.com/3", "source": "Other"},
]


def test_returns_html_with_links():
    html = build_source_links_html(THEMES, ARTICLES)
    assert "https://news.example.com/1" in html
    assert "https://news.example.com/2" in html
    assert "防衛省、予算拡大を発表" in html


def test_excludes_unrelated_articles():
    html = build_source_links_html(THEMES, ARTICLES)
    assert "無関係な記事" not in html


def test_empty_articles_returns_fallback():
    html = build_source_links_html(THEMES, [])
    assert html == ""


def test_xss_url_is_sanitized():
    themes = [{
        "name": "テーマ",
        "icon": "💹",
        "keywords": ["test"],
        "source_articles": [1],
    }]
    articles = [{"title": "XSS記事", "link": "javascript:alert(1)", "source": "evil"}]
    html = build_source_links_html(themes, articles)
    assert 'href="#"' in html
    assert "javascript:" not in html


def test_keyword_fallback_when_no_source_articles():
    themes = [{
        "name": "テーマ",
        "icon": "💹",
        "keywords": ["防衛"],
        "source_articles": [],
    }]
    articles = [
        {"title": "防衛省が新計画", "link": "https://news.example.com/def", "source": "NHK"},
        {"title": "無関係",          "link": "https://news.example.com/unrela", "source": "X"},
    ]
    html = build_source_links_html(themes, articles)
    assert "防衛省が新計画" in html
