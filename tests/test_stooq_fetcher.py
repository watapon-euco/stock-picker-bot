"""stooq_fetcher モジュールのテスト"""
import io
from unittest import mock

import pandas as pd
import pytest
import requests

from src.utils.stooq_fetcher import fetch_from_stooq, _to_stooq_symbol, VALID_SYMBOL_RE


# ─────────────────────────────────────────────────────────────────────────────
# _to_stooq_symbol
# ─────────────────────────────────────────────────────────────────────────────

def test_to_stooq_symbol_japanese_4digit():
    assert _to_stooq_symbol("7203") == "7203.jp"


def test_to_stooq_symbol_us_passthrough():
    assert _to_stooq_symbol("AAPL.US") == "aapl.us"


def test_to_stooq_symbol_strips_whitespace():
    assert _to_stooq_symbol("  6758  ") == "6758.jp"


def test_to_stooq_symbol_rejects_path_traversal():
    """パストラバーサル文字列が ValueError を送出すること（C2）。"""
    with pytest.raises(ValueError, match="Invalid stock code format"):
        _to_stooq_symbol("../etc/passwd")


def test_to_stooq_symbol_rejects_null_byte():
    """null バイトを含む文字列が ValueError を送出すること（C2）。"""
    with pytest.raises(ValueError, match="Invalid stock code format"):
        _to_stooq_symbol("AAPL\x00.US")


def test_to_stooq_symbol_rejects_space():
    """スペースを含む文字列が ValueError を送出すること（C2）。"""
    with pytest.raises(ValueError, match="Invalid stock code format"):
        _to_stooq_symbol("AA PL")


def test_fetch_from_stooq_invalid_code_returns_none():
    """不正なコードを fetch_from_stooq に渡すと None が返ること（C2）。"""
    result = fetch_from_stooq("../etc/passwd")
    assert result is None


def test_fetch_from_stooq_space_in_code_returns_none():
    """スペース入りコード（例: 'AAPL evil'）を渡すと None が返ること（C2）。"""
    result = fetch_from_stooq("AAPL evil")
    assert result is None


def test_fetch_from_stooq_empty_string_returns_none():
    """空文字を渡すと None が返ること（C2）。"""
    result = fetch_from_stooq("")
    assert result is None


def test_fetch_from_stooq_none_returns_none():
    """None を渡すと None が返ること（C2）。"""
    result = fetch_from_stooq(None)
    assert result is None


def test_fetch_from_stooq_invalid_period_days_returns_none():
    """period_days が 0 以下の場合は None が返ること（C2）。"""
    assert fetch_from_stooq("7203", period_days=0) is None
    assert fetch_from_stooq("7203", period_days=-5) is None


# ─────────────────────────────────────────────────────────────────────────────
# fetch_from_stooq: 正常系
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-05-13,2850.00,2870.50,2840.00,2860.25,1234567\n"
    "2026-05-14,2860.25,2880.00,2855.00,2875.50,1456789\n"
    "2026-05-15,2875.50,2895.00,2870.00,2890.00,987654\n"
)


def _mock_response(status_code: int, text: str):
    resp = mock.MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


def test_fetch_from_stooq_success_japanese_code():
    """4桁証券コードで CSV が正常にパースされること。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, SAMPLE_CSV)):
        result = fetch_from_stooq("7203")

    assert result is not None
    assert result["data_source"] == "stooq"
    assert isinstance(result["current_price"], float)
    assert result["current_price"] == pytest.approx(2890.00)
    assert isinstance(result["history"], pd.DataFrame)
    assert len(result["history"]) == 3
    assert set(["Date", "Open", "High", "Low", "Close", "Volume"]).issubset(result["history"].columns)


def test_fetch_from_stooq_uses_jp_suffix_in_url():
    """日本株のリクエストが .jp サフィックス付きシンボルで送られること。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, SAMPLE_CSV)) as mock_get:
        fetch_from_stooq("6758")

    call_kwargs = mock_get.call_args
    params = call_kwargs[1].get("params") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]["params"]
    assert params["s"] == "6758.jp"


def test_fetch_from_stooq_period_days_limits_rows():
    """period_days=2 を指定したとき末尾 2 行のみ返ること。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, SAMPLE_CSV)):
        result = fetch_from_stooq("7203", period_days=2)

    assert result is not None
    assert len(result["history"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# fetch_from_stooq: 異常系
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_from_stooq_http_404_returns_none():
    """HTTP 404 が返ったとき None を返すこと。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(404, "Not Found")):
        result = fetch_from_stooq("9999")

    assert result is None


def test_fetch_from_stooq_empty_csv_returns_none():
    """空 CSV（ヘッダのみ）が返ったとき None を返すこと。"""
    empty_csv = "Date,Open,High,Low,Close,Volume\n"
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, empty_csv)):
        result = fetch_from_stooq("1234")

    assert result is None


def test_fetch_from_stooq_blank_body_returns_none():
    """レスポンスボディが空文字のとき None を返すこと。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, "")):
        result = fetch_from_stooq("1234")

    assert result is None


def test_fetch_from_stooq_missing_close_column_returns_none():
    """必須カラム (Close) が欠落した CSV のとき None を返すこと。"""
    bad_csv = "Date,Open,High,Low,Volume\n2026-05-15,100,110,90,999\n"
    with mock.patch("src.utils.stooq_fetcher.requests.get", return_value=_mock_response(200, bad_csv)):
        result = fetch_from_stooq("5555")

    assert result is None


def test_fetch_from_stooq_network_error_returns_none():
    """ネットワーク例外が発生したとき None を返すこと（リトライ後も失敗）。"""
    with mock.patch("src.utils.stooq_fetcher.requests.get", side_effect=requests.exceptions.ConnectionError("timeout")):
        with mock.patch("src.utils.stooq_fetcher.time.sleep"):  # リトライ待機をスキップ
            result = fetch_from_stooq("7203")

    assert result is None
