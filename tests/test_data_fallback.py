"""フォールバックチェーン（yfinance → Stooq）の動作確認テスト"""
import pandas as pd
import pytest
from unittest import mock


# ─────────────────────────────────────────────────────────────────────────────
# テスト用フィクスチャ
# ─────────────────────────────────────────────────────────────────────────────

def _make_yfinance_result(code: str = "6758") -> dict:
    """yfinance 成功時の戻り値サンプル。"""
    return {
        "ticker": f"{code}.T",
        "code": code,
        "name": "テスト株式会社",
        "current_price": 1000.0,
        "previous_close": 990.0,
        "change_pct": 1.01,
        "market_cap": 1_000_000_000.0,
        "per": 15.0,
        "pbr": 1.2,
        "dividend_yield": 0.02,
        "52w_high": 1200.0,
        "52w_low": 800.0,
        "sector": "Technology",
        "industry": "Software",
        "quarterly": {},
        "technicals": {
            "ma25": 980.0,
            "ma75": 960.0,
            "rsi14": 55.0,
            "volume_ratio_5_30": 1.1,
            "pct_from_52w_high": -16.7,
            "pct_from_52w_low": 25.0,
        },
        "price_history_6m": {"dates": ["2026-01-01"], "closes": [1000.0]},
    }


def _make_stooq_history_df() -> pd.DataFrame:
    """Stooq から返ってくる history DataFrame のサンプル。"""
    return pd.DataFrame({
        "Date": pd.date_range("2025-11-01", periods=130, freq="B"),
        "Open":  [1000.0] * 130,
        "High":  [1050.0] * 130,
        "Low":   [950.0] * 130,
        "Close": [1000.0] * 130,
        "Volume": [500000] * 130,
    })


def _make_stooq_result(code: str = "6758") -> dict:
    """stooq_fetcher.fetch_from_stooq の成功戻り値サンプル。"""
    return {
        "current_price": 1000.0,
        "history": _make_stooq_history_df(),
        "data_source": "stooq",
    }


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 1: yfinance 成功時は Stooq が呼ばれないこと
# ─────────────────────────────────────────────────────────────────────────────

def test_yfinance_success_stooq_not_called():
    import src.utils.yfinance_fetcher as yf_mod

    yf_result = _make_yfinance_result()

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=yf_result) as mock_yf, \
         mock.patch.object(yf_mod, "fetch_from_stooq") as mock_stooq, \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    mock_yf.assert_called_once_with("6758", "JP")
    mock_stooq.assert_not_called()
    assert result is not None
    assert result["data_source"] == "yfinance"


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 2: yfinance 失敗時に Stooq が呼ばれること
# ─────────────────────────────────────────────────────────────────────────────

def test_yfinance_failure_triggers_stooq():
    import src.utils.yfinance_fetcher as yf_mod

    stooq_result = _make_stooq_result()

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None) as mock_yf, \
         mock.patch.object(yf_mod, "fetch_from_stooq", return_value=stooq_result) as mock_stooq, \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    mock_yf.assert_called_once_with("6758", "JP")
    mock_stooq.assert_called_once_with("6758", market="JP")
    assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 3: Stooq の戻り値が期待形式（price, history を含む）になっていること
# ─────────────────────────────────────────────────────────────────────────────

def test_stooq_fallback_result_has_expected_fields():
    import src.utils.yfinance_fetcher as yf_mod

    stooq_result = _make_stooq_result()

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None), \
         mock.patch.object(yf_mod, "fetch_from_stooq", return_value=stooq_result), \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    assert result is not None
    assert "current_price" in result
    assert result["current_price"] == 1000.0
    assert "price_history_6m" in result
    assert result["price_history_6m"] is not None
    assert "technicals" in result


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 4: 両方失敗時に None が返ること（例外は伝播しない）
# ─────────────────────────────────────────────────────────────────────────────

def test_both_sources_fail_returns_none():
    import src.utils.yfinance_fetcher as yf_mod

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None), \
         mock.patch.object(yf_mod, "fetch_from_stooq", return_value=None), \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("9999")

    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 5: data_source フィールドが正しく入ること
# ─────────────────────────────────────────────────────────────────────────────

def test_data_source_field_yfinance():
    import src.utils.yfinance_fetcher as yf_mod

    yf_result = _make_yfinance_result()

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=yf_result), \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    assert result["data_source"] == "yfinance"


def test_data_source_field_stooq():
    import src.utils.yfinance_fetcher as yf_mod

    stooq_result = _make_stooq_result()

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None), \
         mock.patch.object(yf_mod, "fetch_from_stooq", return_value=stooq_result), \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    assert result["data_source"] == "stooq"


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 6: ENABLE_STOOQ_FALLBACK=False のとき Stooq が呼ばれないこと
# ─────────────────────────────────────────────────────────────────────────────

def test_stooq_fallback_disabled():
    import src.utils.yfinance_fetcher as yf_mod

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None), \
         mock.patch.object(yf_mod, "fetch_from_stooq") as mock_stooq, \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", False):

        result = yf_mod.fetch_stock_data("6758")

    mock_stooq.assert_not_called()
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# テストケース 7: Stooq 結果に None フィールドが含まれること（ファンダメンタル不可）
# ─────────────────────────────────────────────────────────────────────────────

def test_stooq_result_fundamental_fields_are_none():
    import src.utils.yfinance_fetcher as yf_mod

    stooq_result = _make_stooq_result()
    none_fields = ["per", "pbr", "dividend_yield", "market_cap", "sector", "industry"]

    with mock.patch.object(yf_mod, "_fetch_via_yfinance", return_value=None), \
         mock.patch.object(yf_mod, "fetch_from_stooq", return_value=stooq_result), \
         mock.patch.object(yf_mod, "ENABLE_STOOQ_FALLBACK", True):

        result = yf_mod.fetch_stock_data("6758")

    for field in ["per", "pbr", "dividend_yield", "market_cap"]:
        assert result[field] is None, f"Expected {field} to be None for Stooq data"
    assert result["sector"] == ""
    assert result["industry"] == ""
