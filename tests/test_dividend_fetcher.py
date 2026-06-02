"""配当・権利落ち日取得（dividend_fetcher）のテスト"""
from datetime import date, datetime, timedelta, timezone
from unittest import mock


def _epoch_for(days_from_today: int) -> int:
    """今日から days_from_today 日後の UTC 0時の epoch 秒を返す。"""
    target = datetime.now(timezone.utc).date() + timedelta(days=days_from_today)
    dt = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    return int(dt.timestamp())


def _info(days_from_today, yield_=0.025, rate=50.0):
    return {
        "exDividendDate": _epoch_for(days_from_today),
        "dividendYield": yield_,
        "dividendRate": rate,
    }


def test_within_window_returns_data():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = _info(5)
        result = mod.fetch_upcoming_dividend("7203", lookahead_days=14)
    assert result is not None
    assert result["code"] == "7203"
    assert result["dividend_rate"] == 50.0
    assert "(" in result["ex_date"]


def test_today_boundary_included():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = _info(0)
        result = mod.fetch_upcoming_dividend("7203", lookahead_days=14)
    assert result is not None


def test_far_future_excluded():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = _info(30)
        result = mod.fetch_upcoming_dividend("7203", lookahead_days=14)
    assert result is None


def test_past_excluded():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = _info(-3)
        result = mod.fetch_upcoming_dividend("7203", lookahead_days=14)
    assert result is None


def test_missing_ex_date_returns_none():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = {"dividendYield": 0.02}
        result = mod.fetch_upcoming_dividend("7203")
    assert result is None


def test_string_ex_date_supported():
    import src.utils.dividend_fetcher as mod
    target = (date.today() + timedelta(days=4)).strftime("%Y-%m-%d")
    with mock.patch("yfinance.Ticker") as MockTicker:
        MockTicker.return_value.info = {"exDividendDate": target, "dividendYield": 0.03}
        result = mod.fetch_upcoming_dividend("7203", lookahead_days=14)
    assert result is not None


def test_api_exception_returns_none():
    import src.utils.dividend_fetcher as mod
    with mock.patch("yfinance.Ticker") as MockTicker:
        type(MockTicker.return_value).info = mock.PropertyMock(
            side_effect=RuntimeError("network error")
        )
        result = mod.fetch_upcoming_dividend("7203")
    assert result is None


def test_jp_ticker_suffix():
    import src.utils.dividend_fetcher as mod
    captured = {}

    def mock_ticker(sym):
        captured["sym"] = sym
        m = mock.MagicMock()
        m.info = _info(5)
        return m

    with mock.patch("yfinance.Ticker", side_effect=mock_ticker):
        mod.fetch_upcoming_dividend("7203")
    assert captured.get("sym") == "7203.T"


def test_us_ticker_no_suffix():
    import src.utils.dividend_fetcher as mod
    captured = {}

    def mock_ticker(sym):
        captured["sym"] = sym
        m = mock.MagicMock()
        m.info = _info(5)
        return m

    with mock.patch("yfinance.Ticker", side_effect=mock_ticker):
        mod.fetch_upcoming_dividend("AAPL", market="US")
    assert captured.get("sym") == "AAPL"
