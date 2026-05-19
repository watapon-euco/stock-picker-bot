"""ticker_utils モジュールのテスト"""
import pytest

from src.utils.ticker_utils import detect_market, format_price, get_currency, normalize_ticker


# ─────────────────────────────────────────────────────────────────────────────
# detect_market
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_market_jp_plain():
    assert detect_market("7203") == "JP"


def test_detect_market_jp_with_t_suffix():
    assert detect_market("7203.T") == "JP"


def test_detect_market_jp_with_os_suffix():
    assert detect_market("4755.OS") == "JP"


def test_detect_market_us_simple():
    assert detect_market("AAPL") == "US"


def test_detect_market_us_short():
    assert detect_market("F") == "US"


def test_detect_market_us_dot_b():
    assert detect_market("BRK.B") == "US"


def test_detect_market_invalid_raises():
    with pytest.raises(ValueError):
        detect_market("TOOLONG_CODE")


def test_detect_market_mixed_invalid_raises():
    with pytest.raises(ValueError):
        detect_market("abc123")


# ─────────────────────────────────────────────────────────────────────────────
# normalize_ticker
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_ticker_jp_adds_suffix():
    assert normalize_ticker("7203", "JP") == "7203.T"


def test_normalize_ticker_jp_already_has_t():
    assert normalize_ticker("7203.T", "JP") == "7203.T"


def test_normalize_ticker_jp_already_has_os():
    assert normalize_ticker("4755.OS", "JP") == "4755.OS"


def test_normalize_ticker_us_passthrough():
    assert normalize_ticker("AAPL", "US") == "AAPL"


def test_normalize_ticker_us_dot_converted_to_hyphen():
    assert normalize_ticker("BRK.B", "US") == "BRK-B"


def test_normalize_ticker_auto_detect_jp():
    assert normalize_ticker("7203") == "7203.T"


def test_normalize_ticker_auto_detect_us():
    assert normalize_ticker("AAPL") == "AAPL"


# ─────────────────────────────────────────────────────────────────────────────
# get_currency
# ─────────────────────────────────────────────────────────────────────────────

def test_get_currency_jp():
    assert get_currency("JP") == "JPY"


def test_get_currency_us():
    assert get_currency("US") == "USD"


def test_get_currency_unknown_defaults_to_jpy():
    assert get_currency("XX") == "JPY"


# ─────────────────────────────────────────────────────────────────────────────
# format_price
# ─────────────────────────────────────────────────────────────────────────────

def test_format_price_jpy_no_decimal():
    assert format_price(2850.0, "JPY") == "¥2,850"


def test_format_price_jpy_large():
    assert format_price(12345678.0, "JPY") == "¥12,345,678"


def test_format_price_usd_two_decimal():
    assert format_price(185.5, "USD") == "$185.50"


def test_format_price_usd_large():
    assert format_price(1234.56, "USD") == "$1,234.56"


def test_format_price_usd_zero():
    assert format_price(0.0, "USD") == "$0.00"


def test_format_price_none_returns_dash():
    assert format_price(None, "JPY") == "—"


def test_format_price_none_usd_returns_dash():
    assert format_price(None, "USD") == "—"


# ─────────────────────────────────────────────────────────────────────────────
# 小文字入力
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_market_lowercase_us():
    """小文字の US ティッカーが US と判定される（silent JP 誤判定を防ぐ）"""
    assert detect_market("aapl") == "US"


def test_detect_market_lowercase_mixed():
    assert detect_market("tsla") == "US"


def test_normalize_ticker_lowercase_us_uppercased():
    """小文字の US ティッカーが normalize_ticker で大文字化される"""
    assert normalize_ticker("aapl", "US") == "AAPL"


def test_normalize_ticker_auto_detect_lowercase_us():
    """market 未指定でも小文字 US ティッカーが正しく正規化される"""
    assert normalize_ticker("aapl") == "AAPL"
