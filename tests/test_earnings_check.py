"""決算アラート機能のテスト"""
import json
import os
import tempfile
from datetime import date, timedelta
from unittest import mock

import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# earnings_fetcher: fetch_upcoming_earnings
# ─────────────────────────────────────────────────────────────────────────────

def _make_earnings_df(days_from_today: int, eps_estimate=1.50, eps_actual=None):
    """テスト用 earnings_dates DataFrame を生成する。"""
    today = date.today()
    target = today + timedelta(days=days_from_today)
    ts = pd.Timestamp(target)
    columns = ["EPS Estimate", "Reported EPS", "Surprise(%)"]
    data = {
        "EPS Estimate": [eps_estimate],
        "Reported EPS": [eps_actual],
        "Surprise(%)": [None],
    }
    return pd.DataFrame(data, index=[ts])


def test_fetch_upcoming_earnings_within_window():
    """3日以内の決算は情報が返ること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=2, eps_estimate=1.50)

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = df
        result = mod.fetch_upcoming_earnings("7203", lookahead_days=3)

    assert result is not None
    assert result["code"] == "7203"
    assert result["eps_estimate"] == 1.50
    assert "()" in result["earnings_date"] or "(" in result["earnings_date"]


def test_fetch_upcoming_earnings_on_today():
    """今日が決算日の場合も対象に含まれること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=0)

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = df
        result = mod.fetch_upcoming_earnings("7203", lookahead_days=3)

    assert result is not None


def test_fetch_upcoming_earnings_exactly_at_boundary():
    """lookahead_days 当日（3日後）も対象に含まれること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=3)

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = df
        result = mod.fetch_upcoming_earnings("7203", lookahead_days=3)

    assert result is not None


def test_fetch_upcoming_earnings_outside_window():
    """4日以上先の決算は None が返ること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=4)

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = df
        result = mod.fetch_upcoming_earnings("7203", lookahead_days=3)

    assert result is None


def test_fetch_upcoming_earnings_past_date():
    """過去の決算日は None が返ること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=-1)

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = df
        result = mod.fetch_upcoming_earnings("7203", lookahead_days=3)

    assert result is None


def test_fetch_upcoming_earnings_none_df():
    """earnings_dates が None を返す場合は None が返ること（グレースフル退避）。"""
    import src.utils.earnings_fetcher as mod

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = None
        result = mod.fetch_upcoming_earnings("7203")

    assert result is None


def test_fetch_upcoming_earnings_empty_df():
    """earnings_dates が空 DataFrame の場合は None が返ること。"""
    import src.utils.earnings_fetcher as mod

    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.earnings_dates = pd.DataFrame()
        result = mod.fetch_upcoming_earnings("7203")

    assert result is None


def test_fetch_upcoming_earnings_api_exception():
    """earnings_dates が例外を発生させても None が返り、例外が伝播しないこと。"""
    import src.utils.earnings_fetcher as mod

    with mock.patch("yfinance.Ticker") as MockTicker:
        type(MockTicker.return_value).earnings_dates = mock.PropertyMock(
            side_effect=RuntimeError("network error")
        )
        result = mod.fetch_upcoming_earnings("7203")

    assert result is None


def test_fetch_upcoming_earnings_adds_T_suffix():
    """証券コードに .T サフィックスが付与されること。"""
    import src.utils.earnings_fetcher as mod

    df = _make_earnings_df(days_from_today=1)
    captured = {}

    def mock_ticker(sym):
        captured["sym"] = sym
        t = mock.MagicMock()
        t.earnings_dates = df
        return t

    with mock.patch("yfinance.Ticker", side_effect=mock_ticker):
        mod.fetch_upcoming_earnings("7203")

    assert captured.get("sym") == "7203.T"


# ─────────────────────────────────────────────────────────────────────────────
# step_earnings_check: 重複通知防止ロジック
# ─────────────────────────────────────────────────────────────────────────────

def test_notified_key_format():
    """_notified_key が 'CODE:YYYY-MM-DD' 形式を返すこと。"""
    from src.step_earnings_check import _notified_key
    key = _notified_key("7203", date(2026, 5, 20))
    assert key == "7203:2026-05-20"


def test_cleanup_notified_removes_past_entries():
    """決算日が過去のエントリが cleanup_notified で除去されること。"""
    from src.step_earnings_check import _cleanup_notified

    today = date.today()
    past_key = f"1234:{(today - timedelta(days=1)).isoformat()}"
    future_key = f"5678:{(today + timedelta(days=1)).isoformat()}"
    today_key = f"9999:{today.isoformat()}"

    notified = {
        past_key: "2026-01-01T00:00:00+00:00",
        future_key: "2026-01-01T00:00:00+00:00",
        today_key: "2026-01-01T00:00:00+00:00",
    }

    cleaned = _cleanup_notified(notified)

    assert past_key not in cleaned
    assert future_key in cleaned
    assert today_key in cleaned


def test_cleanup_notified_handles_malformed_key():
    """不正なキーは過去日として扱われ除去されること。"""
    from src.step_earnings_check import _cleanup_notified

    notified = {"BAD_KEY_NO_DATE": "2026-01-01T00:00:00+00:00"}
    cleaned = _cleanup_notified(notified)
    assert "BAD_KEY_NO_DATE" not in cleaned


# ─────────────────────────────────────────────────────────────────────────────
# step_earnings_check: _collect_target_stocks
# ─────────────────────────────────────────────────────────────────────────────

def test_collect_target_stocks_deduplicates():
    """ウォッチリストとテーマ履歴に同一銘柄コードがある場合、1件のみ収集されること。"""
    import src.step_earnings_check as mod

    watchlist_data = {
        "stocks": [{"code": "7203", "name": "トヨタ"}],
        "last_check": None,
        "last_prices": {},
    }
    theme_history_data = {
        "themes": [
            {
                "name": "テストテーマ",
                "year_month": date.today().strftime("%Y-%m"),
                "icon": "🚀",
                "stocks": [
                    {"code": "7203", "name": "トヨタ（テーマ）"},
                    {"code": "6758", "name": "ソニー"},
                ],
            }
        ]
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        wl_path = os.path.join(tmpdir, "watchlist.json")
        th_path = os.path.join(tmpdir, "theme_history.json")
        with open(wl_path, "w", encoding="utf-8") as f:
            json.dump(watchlist_data, f)
        with open(th_path, "w", encoding="utf-8") as f:
            json.dump(theme_history_data, f)

        with mock.patch.object(mod, "WATCHLIST_PATH", wl_path), \
             mock.patch.object(mod, "THEME_HISTORY_PATH", th_path):
            result = mod._collect_target_stocks()

    codes = [s["code"] for s in result]
    assert codes.count("7203") == 1
    assert "6758" in codes


def test_collect_target_stocks_missing_files():
    """ウォッチリストおよびテーマ履歴ファイルが存在しなくても空リストを返すこと。"""
    import src.step_earnings_check as mod

    with mock.patch.object(mod, "WATCHLIST_PATH", "/nonexistent/watchlist.json"), \
         mock.patch.object(mod, "THEME_HISTORY_PATH", "/nonexistent/theme_history.json"):
        result = mod._collect_target_stocks()

    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# step_earnings_check: run() — 銘柄ゼロのケース
# ─────────────────────────────────────────────────────────────────────────────

def test_run_no_stocks_does_not_notify():
    """対象銘柄が0件のとき LINE 通知が呼ばれないこと。"""
    import src.step_earnings_check as mod

    with mock.patch.object(mod, "_collect_target_stocks", return_value=[]), \
         mock.patch("src.utils.line_client.LineClient") as MockClient:
        mod.run()

    MockClient.assert_not_called()


def test_run_no_upcoming_earnings_does_not_notify():
    """全銘柄の earnings_dates が None のとき通知が発生しないこと。"""
    import src.step_earnings_check as mod

    stocks = [{"code": "7203", "name": "トヨタ"}]

    with mock.patch.object(mod, "_collect_target_stocks", return_value=stocks), \
         mock.patch.object(mod, "_load_notified", return_value={}), \
         mock.patch("src.step_earnings_check.fetch_upcoming_earnings", return_value=None), \
         mock.patch("src.utils.line_client.LineClient") as MockClient:
        mod.run()

    MockClient.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# step_earnings_check: run() — 通知フロー
# ─────────────────────────────────────────────────────────────────────────────

def test_run_notifies_when_earnings_upcoming():
    """3日以内の決算銘柄があるとき LINE 通知が送信されること。"""
    import src.step_earnings_check as mod

    today = date.today()
    earnings_result = {
        "code": "7203",
        "ticker": "7203.T",
        "earnings_date": (today + timedelta(days=2)).strftime("%Y-%m-%d (%a)"),
        "earnings_date_raw": today + timedelta(days=2),
        "eps_estimate": 2.50,
        "eps_actual": None,
    }

    stocks = [{"code": "7203", "name": "トヨタ"}]

    with mock.patch.object(mod, "_collect_target_stocks", return_value=stocks), \
         mock.patch.object(mod, "_load_notified", return_value={}), \
         mock.patch.object(mod, "_save_notified") as mock_save, \
         mock.patch("src.step_earnings_check.fetch_upcoming_earnings", return_value=earnings_result), \
         mock.patch.dict(os.environ, {"LINE_CHANNEL_TOKEN": "tok", "LINE_GROUP_ID": "grp"}), \
         mock.patch("src.utils.line_client.LineClient") as MockClient:

        mock_client_instance = MockClient.return_value
        mock_client_instance.send_flex.return_value = True

        mod.run()

    mock_client_instance.send_flex.assert_called_once()
    mock_save.assert_called_once()


def test_run_skips_already_notified():
    """通知済み銘柄は再通知されないこと。"""
    import src.step_earnings_check as mod

    today = date.today()
    future = today + timedelta(days=2)
    key = f"7203:{future.isoformat()}"

    earnings_result = {
        "code": "7203",
        "ticker": "7203.T",
        "earnings_date": future.strftime("%Y-%m-%d (%a)"),
        "earnings_date_raw": future,
        "eps_estimate": None,
        "eps_actual": None,
    }

    stocks = [{"code": "7203", "name": "トヨタ"}]

    with mock.patch.object(mod, "_collect_target_stocks", return_value=stocks), \
         mock.patch.object(mod, "_load_notified", return_value={key: "2026-01-01T00:00:00+00:00"}), \
         mock.patch("src.step_earnings_check.fetch_upcoming_earnings", return_value=earnings_result), \
         mock.patch("src.utils.line_client.LineClient") as MockClient:

        mod.run()

    MockClient.assert_not_called()


def test_run_does_not_save_notified_when_line_fails():
    """LINE 送信失敗時は通知済みログを更新しないこと。"""
    import src.step_earnings_check as mod

    today = date.today()
    earnings_result = {
        "code": "7203",
        "ticker": "7203.T",
        "earnings_date": (today + timedelta(days=1)).strftime("%Y-%m-%d (%a)"),
        "earnings_date_raw": today + timedelta(days=1),
        "eps_estimate": None,
        "eps_actual": None,
    }

    stocks = [{"code": "7203", "name": "トヨタ"}]

    with mock.patch.object(mod, "_collect_target_stocks", return_value=stocks), \
         mock.patch.object(mod, "_load_notified", return_value={}), \
         mock.patch.object(mod, "_save_notified") as mock_save, \
         mock.patch("src.step_earnings_check.fetch_upcoming_earnings", return_value=earnings_result), \
         mock.patch.dict(os.environ, {"LINE_CHANNEL_TOKEN": "tok", "LINE_GROUP_ID": "grp"}), \
         mock.patch("src.utils.line_client.LineClient") as MockClient:

        mock_client_instance = MockClient.return_value
        mock_client_instance.send_flex.return_value = False

        mod.run()

    mock_save.assert_not_called()
