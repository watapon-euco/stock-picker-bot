"""週次速報レポートのユニットテスト"""
import html
import json
import re
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.step_weekly_flash import (
    SURGE_PCT_THRESHOLD,
    SURGE_VOL_RATIO,
    _build_flex_contents,
    _build_surging_stocks_html,
    _build_top5,
    _build_top_news_html,
    _collect_codes,
    _render_html,
    _select_top_news,
    _update_weekly_index,
    is_surging,
    _build_flex_footer,
)

TEMPLATE_PATH = Path("src/templates/weekly_flash_template.html")

# ─────────────────────────────────────────────────────────────────────────────
# 急騰判定ロジック
# ─────────────────────────────────────────────────────────────────────────────

class TestIsSurging:
    def test_pct_above_threshold(self):
        assert is_surging(SURGE_PCT_THRESHOLD, None, None) is True

    def test_pct_exactly_threshold(self):
        assert is_surging(SURGE_PCT_THRESHOLD, None, None) is True

    def test_pct_below_threshold_no_vol(self):
        assert is_surging(4.9, None, None) is False

    def test_vol_above_threshold_with_sufficient_avg_volume_and_price(self):
        # 出来高2倍以上 + 平均出来高10,000以上 + 価格変化1%以上 → True
        assert is_surging(1.0, SURGE_VOL_RATIO, 10_000) is True

    def test_vol_above_threshold_thin_market(self):
        # 平均出来高が基準未満の薄商い銘柄 → False
        assert is_surging(1.0, SURGE_VOL_RATIO, 9_999) is False

    def test_vol_above_threshold_no_price_change(self):
        # 出来高2倍以上だが価格変化が1%未満 → False
        assert is_surging(0.5, SURGE_VOL_RATIO, 50_000) is False

    def test_vol_below_threshold(self):
        assert is_surging(1.0, 1.9, 50_000) is False

    def test_both_criteria_met(self):
        assert is_surging(10.0, 3.0, 100_000) is True

    def test_negative_change_no_vol(self):
        assert is_surging(-5.0, None, None) is False

    def test_none_values(self):
        assert is_surging(None, None, None) is False

    def test_pct_none_vol_above_threshold(self):
        # price_change_pct が None なら出来高条件も満たせない → False
        assert is_surging(None, SURGE_VOL_RATIO, 50_000) is False


# ─────────────────────────────────────────────────────────────────────────────
# HTML 生成 — 急騰銘柄
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildSurgingStocksHtml:
    def _sample_stocks(self) -> List[Dict]:
        return [
            {"code": "6758", "name": "ソニーグループ", "current_price": 12500.0,
             "week_change_pct": 7.5, "vol_ratio": 2.5},
            {"code": "7203", "name": "トヨタ自動車", "current_price": 3200.0,
             "week_change_pct": 5.1, "vol_ratio": 1.1},
        ]

    def test_empty_returns_empty_state(self):
        out = _build_surging_stocks_html([])
        assert "ありませんでした" in out

    def test_contains_code_and_name(self):
        stocks = self._sample_stocks()
        out = _build_surging_stocks_html(stocks)
        assert "6758" in out
        assert "ソニーグループ" in out

    def test_positive_change_class(self):
        out = _build_surging_stocks_html(self._sample_stocks())
        assert "#7dc679" in out  # editorial-dark green for positive change

    def test_negative_change_class(self):
        stocks = [{"code": "9999", "name": "サンプル", "current_price": 1000.0,
                   "week_change_pct": -3.0, "vol_ratio": None}]
        out = _build_surging_stocks_html(stocks)
        assert "#e16158" in out  # editorial-dark red for negative change

    def test_fallback_shows_note(self):
        stocks = [{"code": "1234", "name": "サンプル", "current_price": 1000.0,
                   "week_change_pct": 2.0, "vol_ratio": None, "is_fallback": True}]
        out = _build_surging_stocks_html(stocks)
        assert "上昇率トップ" in out

    def test_xss_escaped_in_name(self):
        stocks = [{"code": "1234", "name": '<script>alert(1)</script>',
                   "current_price": 100.0, "week_change_pct": 6.0, "vol_ratio": None}]
        out = _build_surging_stocks_html(stocks)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_max_5_rows(self):
        stocks = [
            {"code": str(i), "name": f"株{i}", "current_price": float(i * 100),
             "week_change_pct": float(i), "vol_ratio": None}
            for i in range(1, 6)
        ]
        out = _build_surging_stocks_html(stocks)
        # 5 stock rows — each has exactly one rank element
        assert out.count('tp-surge-row__rank') == 5


# ─────────────────────────────────────────────────────────────────────────────
# HTML 生成 — ニュース
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTopNewsHtml:
    def _sample_news(self) -> List[Dict]:
        return [
            {"title": "日本株上昇", "link": "https://example.com/1",
             "source": "日経新聞", "comment": "重要なニュースです"},
            {"title": "半導体需要拡大", "link": "https://example.com/2",
             "source": "Reuters", "comment": ""},
        ]

    def test_empty_returns_empty_state(self):
        out = _build_top_news_html([])
        assert "ありませんでした" in out

    def test_contains_title(self):
        out = _build_top_news_html(self._sample_news())
        assert "日本株上昇" in out

    def test_https_link_rendered(self):
        out = _build_top_news_html(self._sample_news())
        assert 'href="https://example.com/1"' in out

    def test_invalid_link_blocked(self):
        news = [{"title": "XSS", "link": "javascript:alert(1)",
                 "source": "test", "comment": ""}]
        out = _build_top_news_html(news)
        assert "javascript:" not in out

    def test_xss_in_title_escaped(self):
        news = [{"title": '<img src=x onerror=alert(1)>',
                 "link": "https://example.com", "source": "s", "comment": ""}]
        out = _build_top_news_html(news)
        assert "<img" not in out
        assert "&lt;img" in out


# ─────────────────────────────────────────────────────────────────────────────
# テンプレート置換
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderHtml:
    def test_template_exists(self):
        assert TEMPLATE_PATH.exists()

    def test_all_placeholders_replaced(self):
        out = _render_html(
            week_label="2026-W21",
            date_range="2026/05/14 〜 2026/05/20",
            stocks_html="<p>stocks</p>",
            news_html="<p>news</p>",
            monthly_url="https://example.com/index.html",
        )
        remaining = re.findall(r"\{\{[A-Z_]+\}\}", out)
        assert remaining == [], f"Unreplaced placeholders: {remaining}"

    def test_week_label_in_output(self):
        out = _render_html("2026-W21", "期間", "<p/>", "<p/>", "https://example.com")
        assert "2026-W21" in out

    def test_monthly_url_in_output(self):
        url = "https://example.com/reports/index.html"
        out = _render_html("2026-W21", "期間", "<p/>", "<p/>", url)
        assert url in out

    def test_invalid_monthly_url_blocked(self):
        out = _render_html("2026-W21", "期間", "<p/>", "<p/>", "javascript:void(0)")
        assert "javascript:" not in out

    def test_xss_in_week_label_escaped(self):
        out = _render_html('<script>', "期間", "<p/>", "<p/>", "https://example.com")
        assert "<script>" not in out


# ─────────────────────────────────────────────────────────────────────────────
# LINE Flex 構造
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFlexContents:
    def _stocks(self):
        return [
            {"code": "6758", "name": "ソニー", "week_change_pct": 8.0, "vol_ratio": 2.1},
        ]

    def _news(self):
        return [{"title": "注目ニュース1", "link": "https://example.com",
                 "source": "日経", "comment": "重要"}]

    def test_type_is_bubble(self):
        contents = _build_flex_contents("2026-W21", self._stocks(), self._news(), "https://ex.com")
        assert contents["type"] == "bubble"

    def test_has_header_body_footer(self):
        contents = _build_flex_contents("2026-W21", self._stocks(), self._news(), "https://ex.com")
        assert "header" in contents
        assert "body" in contents
        assert "footer" in contents

    def test_footer_has_uri_action(self):
        contents = _build_flex_contents("2026-W21", self._stocks(), self._news(), "https://ex.com")
        button = contents["footer"]["contents"][0]
        assert button["action"]["type"] == "uri"
        assert button["action"]["uri"] == "https://ex.com"

    def test_footer_no_button_for_empty_url(self):
        contents = _build_flex_contents("2026-W21", self._stocks(), self._news(), "")
        item = contents["footer"]["contents"][0]
        assert item["type"] == "text"

    def test_footer_no_button_for_invalid_url(self):
        contents = _build_flex_contents("2026-W21", self._stocks(), self._news(), "javascript:void(0)")
        item = contents["footer"]["contents"][0]
        assert item["type"] == "text"

    def test_empty_stocks_and_news(self):
        contents = _build_flex_contents("2026-W21", [], [], "https://ex.com")
        assert contents["type"] == "bubble"
        body_texts = [c.get("text", "") for c in contents["body"]["contents"]]
        assert any("ありません" in t for t in body_texts)

    def test_week_label_in_header(self):
        contents = _build_flex_contents("2026-W21", [], [], "https://ex.com")
        header_text = contents["header"]["contents"][0]["text"]
        assert "2026-W21" in header_text


# ─────────────────────────────────────────────────────────────────────────────
# _collect_codes
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectCodes:
    def test_deduplicates_codes(self, tmp_path, monkeypatch):
        stock_data = {
            "themes": [
                {"theme_name": "AI", "stocks": [{"code": "6758"}, {"code": "7203"}]},
                {"theme_name": "EV", "stocks": [{"code": "7203"}, {"code": "6501"}]},
            ]
        }
        wl = {"stocks": [{"code": "6758"}, {"code": "9999"}]}

        (tmp_path / "stock_data.json").write_text(json.dumps(stock_data), encoding="utf-8")
        (tmp_path / "watchlist.json").write_text(json.dumps(wl), encoding="utf-8")

        import src.step_weekly_flash as swf
        monkeypatch.setattr(swf, "DATA_DIR", tmp_path)
        monkeypatch.setattr(swf, "WATCHLIST_FILE", tmp_path / "watchlist.json")
        monkeypatch.setattr(swf, "THEME_HISTORY_FILE", tmp_path / "theme_history.json")

        codes = swf._collect_codes()
        assert len(codes) == len(set(codes)), "Codes must be unique"
        assert "6758" in codes
        assert "7203" in codes
        assert "9999" in codes

    def test_missing_files_returns_empty(self, tmp_path, monkeypatch):
        import src.step_weekly_flash as swf
        monkeypatch.setattr(swf, "DATA_DIR", tmp_path)
        monkeypatch.setattr(swf, "WATCHLIST_FILE", tmp_path / "watchlist.json")
        monkeypatch.setattr(swf, "THEME_HISTORY_FILE", tmp_path / "theme_history.json")
        codes = swf._collect_codes()
        assert codes == []


# ─────────────────────────────────────────────────────────────────────────────
# _build_top5 — yfinance 失敗シナリオ
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTop5:
    def test_all_failures_returns_empty(self):
        with patch("src.step_weekly_flash._fetch_weekly_changes_batch", return_value=[]):
            result = _build_top5(["6758", "7203"])
        assert result == []

    def test_no_surge_returns_fallback(self):
        non_surging = {"code": "1234", "name": "X", "current_price": 100.0,
                       "week_change_pct": 1.0, "vol_ratio": 1.0, "avg_volume_30d": 5_000}
        with patch("src.step_weekly_flash._fetch_weekly_changes_batch", return_value=[non_surging]):
            result = _build_top5(["1234"])
        assert len(result) == 1
        assert result[0]["is_fallback"] is True

    def test_surging_included(self):
        surging = {"code": "6758", "name": "ソニー", "current_price": 12000.0,
                   "week_change_pct": 8.0, "vol_ratio": None, "avg_volume_30d": None}
        with patch("src.step_weekly_flash._fetch_weekly_changes_batch", return_value=[surging]):
            result = _build_top5(["6758"])
        assert len(result) == 1
        assert result[0]["code"] == "6758"

    def test_capped_at_5(self):
        surging_list = [
            {"code": str(i), "name": "X", "current_price": 100.0,
             "week_change_pct": 10.0, "vol_ratio": None, "avg_volume_30d": None}
            for i in range(7)
        ]
        with patch("src.step_weekly_flash._fetch_weekly_changes_batch", return_value=surging_list):
            result = _build_top5([str(i) for i in range(7)])
        assert len(result) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# _select_top_news
# ─────────────────────────────────────────────────────────────────────────────

class TestSelectTopNews:
    def _articles(self):
        return [
            {"title": f"ニュース{i}", "link": f"https://example.com/{i}",
             "source": "テスト", "summary": f"概要{i}"}
            for i in range(1, 10)
        ]

    def test_returns_up_to_3(self):
        mock_gemini = MagicMock()
        mock_gemini.generate_json.return_value = [
            {"index": 1, "comment": "コメント1"},
            {"index": 2, "comment": "コメント2"},
            {"index": 3, "comment": "コメント3"},
        ]
        result = _select_top_news(self._articles(), mock_gemini)
        assert len(result) == 3

    def test_fallback_on_gemini_error(self):
        mock_gemini = MagicMock()
        mock_gemini.generate_json.side_effect = RuntimeError("API error")
        result = _select_top_news(self._articles(), mock_gemini)
        assert len(result) <= 3
        assert all("title" in r for r in result)

    def test_empty_articles_returns_empty(self):
        mock_gemini = MagicMock()
        result = _select_top_news([], mock_gemini)
        assert result == []

    def test_invalid_index_skipped(self):
        mock_gemini = MagicMock()
        mock_gemini.generate_json.return_value = [
            {"index": 999, "comment": "out of range"},
            {"index": 1, "comment": "valid"},
        ]
        result = _select_top_news(self._articles(), mock_gemini)
        assert len(result) == 1
        assert result[0]["title"] == "ニュース1"


# ─────────────────────────────────────────────────────────────────────────────
# _update_weekly_index
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateWeeklyIndex:
    def test_creates_index_html(self, tmp_path, monkeypatch):
        import src.step_weekly_flash as swf
        monkeypatch.setattr(swf, "DOCS_WEEKLY_DIR", tmp_path)
        (tmp_path / "2026-W21.html").write_text("<html/>", encoding="utf-8")
        (tmp_path / "2026-W20.html").write_text("<html/>", encoding="utf-8")

        swf._update_weekly_index("2026-W21")

        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "2026-W21" in index
        assert "2026-W20" in index

    def test_index_links_most_recent_first(self, tmp_path, monkeypatch):
        import src.step_weekly_flash as swf
        monkeypatch.setattr(swf, "DOCS_WEEKLY_DIR", tmp_path)
        for w in ["2026-W18", "2026-W20", "2026-W19"]:
            (tmp_path / f"{w}.html").write_text("<html/>", encoding="utf-8")

        swf._update_weekly_index("2026-W20")

        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        pos_20 = index.find("2026-W20")
        pos_19 = index.find("2026-W19")
        pos_18 = index.find("2026-W18")
        assert pos_20 < pos_19 < pos_18


# ─────────────────────────────────────────────────────────────────────────────
# テンプレートプレースホルダー整合性
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_WEEKLY_PLACEHOLDERS = {
    "{{WEEK_LABEL}}",
    "{{GENERATED_DATE}}",
    "{{DATE_RANGE}}",
    "{{SURGING_STOCKS_SECTION}}",
    "{{TOP_NEWS_SECTION}}",
    "{{MONTHLY_REPORT_URL}}",
    "{{MARKET_INDICES_SECTION}}",
    "{{EARNINGS_CALENDAR_SECTION}}",
}


def test_weekly_template_exists():
    assert TEMPLATE_PATH.exists()


def test_weekly_template_placeholders():
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    for ph in EXPECTED_WEEKLY_PLACEHOLDERS:
        assert ph in content, f"Missing placeholder: {ph}"


def test_weekly_template_no_unexpected_placeholders():
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    found = set(re.findall(r"\{\{[A-Z_]+\}\}", content))
    unknown = found - EXPECTED_WEEKLY_PLACEHOLDERS
    assert not unknown, f"Unknown placeholders in weekly template: {unknown}"


# ─────────────────────────────────────────────────────────────────────────────
# yfinance バッチ取得の列パース（急騰銘柄が空になる回帰防止）
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchWeeklyChangesBatch:
    """yf.download のデフォルト列グルーピング (field, ticker) を正しくパースし、
    急騰銘柄が常に空になる不具合 (group_by="ticker" 由来) を再発させないことを確認する。
    """

    def _make_multi_df(self):
        import numpy as np
        import pandas as pd

        dates = pd.date_range("2026-01-01", periods=22, freq="B")
        tickers = ["7203.T", "6758.T"]
        fields = ["Open", "High", "Low", "Close", "Volume"]
        cols = pd.MultiIndex.from_product([fields, tickers])
        df = pd.DataFrame(index=dates, columns=cols, dtype=float)
        # 7203.T: 横ばい後に +10% 急騰、出来高は終盤に増加
        close_7203 = [1000.0] * 17 + [1000, 1020, 1050, 1080, 1100]
        # 6758.T: 緩やかに上昇 (+約3%)
        close_6758 = list(np.linspace(2000, 2060, 22))
        for t, closes in (("7203.T", close_7203), ("6758.T", close_6758)):
            df[("Close", t)] = closes
            df[("Open", t)] = closes
            df[("High", t)] = closes
            df[("Low", t)] = closes
            df[("Volume", t)] = [100_000] * 17 + [300_000] * 5
        return df

    def test_parses_default_grouped_columns(self):
        from src.step_weekly_flash import _fetch_weekly_changes_batch

        df = self._make_multi_df()
        with patch("src.step_weekly_flash.yf.download", return_value=df) as m:
            results = _fetch_weekly_changes_batch(
                ["7203", "6758"], {"7203": "トヨタ", "6758": "ソニー"}
            )

        # group_by="ticker" を渡していないこと（列アクセスと整合）
        assert "group_by" not in m.call_args.kwargs
        codes = {r["code"] for r in results}
        assert codes == {"7203", "6758"}, f"全銘柄がパースされるべき: {results}"
        by_code = {r["code"]: r for r in results}
        assert by_code["7203"]["week_change_pct"] > 5.0
        assert by_code["7203"]["vol_ratio"] is not None

    def test_surging_detected_end_to_end(self):
        from src.step_weekly_flash import _build_top5

        df = self._make_multi_df()
        with patch("src.step_weekly_flash.yf.download", return_value=df):
            top5 = _build_top5(["7203", "6758"], {"7203": "トヨタ", "6758": "ソニー"})

        assert top5, "急騰銘柄が検出されるべき（空ではない）"
        assert top5[0]["code"] == "7203"
        assert not top5[0].get("is_fallback", False)


# ─────────────────────────────────────────────────────────────────────────────
# 週次URL導出（/weekly 二重化による 404 防止）
# ─────────────────────────────────────────────────────────────────────────────

class TestDeriveWeeklyUrl:
    def test_from_root_url(self):
        from src.step_weekly_flash import _derive_weekly_url
        assert _derive_weekly_url("https://ex.github.io/repo/", "2026-W25") == \
            "https://ex.github.io/repo/weekly/2026-W25.html"

    def test_strips_index_html(self):
        from src.step_weekly_flash import _derive_weekly_url
        assert _derive_weekly_url("https://ex.github.io/repo/index.html", "2026-W25") == \
            "https://ex.github.io/repo/weekly/2026-W25.html"

    def test_no_double_weekly(self):
        from src.step_weekly_flash import _derive_weekly_url
        # README の例どおり末尾に /weekly を付けて設定しても二重化しない
        assert _derive_weekly_url("https://ex.github.io/repo/weekly", "2026-W25") == \
            "https://ex.github.io/repo/weekly/2026-W25.html"
        assert _derive_weekly_url("https://ex.github.io/repo/weekly/", "2026-W25") == \
            "https://ex.github.io/repo/weekly/2026-W25.html"

    def test_invalid_scheme_blocked(self):
        from src.step_weekly_flash import _derive_weekly_url
        assert _derive_weekly_url("javascript:alert(1)", "2026-W25") == "#"
