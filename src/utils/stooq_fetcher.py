"""Stooq.com を使った株価データ取得モジュール（yfinance のフォールバック用）"""
import io
import logging
import re
import time
from typing import Optional, Dict

import pandas as pd
import requests

from src.utils.cost_logger import log_api_call

logger = logging.getLogger(__name__)

STOOQ_BASE_URL = "https://stooq.com/q/d/l/"
REQUEST_TIMEOUT = 10
MAX_RETRIES = 1

VALID_SYMBOL_RE = re.compile(r"^[\w\-.]+$")  # 英数字、アンダースコア、ハイフン、ドットのみ


def _to_stooq_symbol(code: str, market: str = "JP") -> str:
    """証券コードを Stooq のシンボル形式に変換する。

    JP: 4桁数字 → "{code}.jp"
    US: 英字コード → "{code}.us"（小文字）
    すでにサフィックスが付いているコードはそのまま小文字化する。

    Raises:
        ValueError: 英数字・アンダースコア・ハイフン・ドット以外の文字が含まれる場合
    """
    code = str(code).strip()
    if not VALID_SYMBOL_RE.match(code):
        raise ValueError(f"Invalid stock code format: {code!r}")
    if re.fullmatch(r"\d{4}", code):
        return f"{code}.jp"
    # すでに .jp/.us 等が付いている場合はそのまま小文字化
    if "." in code and re.search(r"\.[a-zA-Z]{2}$", code):
        return code.lower()
    if market == "US":
        return f"{code.lower()}.us"
    return code.lower()


def fetch_from_stooq(code: str, period_days: int = 365, market: str = "JP") -> Optional[Dict]:
    """Stooq から日次株価データを取得する。

    Args:
        code: 証券コード（日本株4桁、または米国株英字コード）
        period_days: 取得する日数（Stooq の CSV は全履歴を返すので、末尾から period_days 行を使う）
                     1 未満の値は無効として None を返す。
        market: "JP" または "US"

    Returns:
        以下のフィールドを持つ dict、失敗時は None:
        - current_price: float
        - history: pandas.DataFrame (Date, Open, High, Low, Close, Volume)
        - data_source: "stooq"
    """
    if not isinstance(code, str) or not VALID_SYMBOL_RE.fullmatch(code.strip()):
        logger.warning(f"Invalid symbol rejected: {code!r}")
        return None
    if not isinstance(period_days, int) or period_days < 1:
        logger.warning(f"Invalid period_days rejected: {period_days!r}")
        return None
    try:
        symbol = _to_stooq_symbol(code, market=market)
    except ValueError as e:
        logger.error(f"Stooq: invalid code rejected: {e}")
        return None
    url = STOOQ_BASE_URL
    params = {"s": symbol, "i": "d"}

    start_time = time.monotonic()
    last_error: Optional[Exception] = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            duration = time.monotonic() - start_time

            if resp.status_code != 200:
                log_api_call(
                    provider="stooq",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": code, "symbol": symbol, "http_status": resp.status_code},
                )
                logger.warning(f"Stooq HTTP {resp.status_code} for {symbol}")
                return None

            csv_text = resp.text.strip()
            if not csv_text or csv_text.lower().startswith("no data"):
                log_api_call(
                    provider="stooq",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": code, "symbol": symbol, "reason": "empty_csv"},
                )
                logger.warning(f"Stooq returned empty data for {symbol}")
                return None

            df = pd.read_csv(io.StringIO(csv_text))

            required_cols = {"Date", "Open", "High", "Low", "Close", "Volume"}
            if not required_cols.issubset(df.columns):
                log_api_call(
                    provider="stooq",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": code, "symbol": symbol, "reason": "missing_columns", "cols": list(df.columns)},
                )
                logger.warning(f"Stooq CSV missing expected columns for {symbol}: {df.columns.tolist()}")
                return None

            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").reset_index(drop=True)

            if df.empty:
                log_api_call(
                    provider="stooq",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": code, "symbol": symbol, "reason": "empty_after_parse"},
                )
                logger.warning(f"Stooq CSV parsed to empty DataFrame for {symbol}")
                return None

            df = df.tail(period_days).reset_index(drop=True)

            current_price = float(df["Close"].iloc[-1])

            log_api_call(
                provider="stooq",
                model="",
                operation="fetch_stock_data",
                duration_sec=duration,
                success=True,
                extra={"code": code, "symbol": symbol, "rows": len(df)},
            )
            logger.info(f"Stooq: fetched {len(df)} rows for {symbol}, current_price={current_price}")

            return {
                "current_price": current_price,
                "history": df,
                "data_source": "stooq",
            }

        except Exception as e:
            last_error = e
            duration = time.monotonic() - start_time
            if attempt < MAX_RETRIES:
                logger.warning(f"Stooq error for {symbol} (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}. Retrying...")
                time.sleep(2)
            else:
                log_api_call(
                    provider="stooq",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": code, "symbol": symbol, "error": str(e)},
                )
                logger.error(f"Stooq fetch failed for {symbol}: {e}")

    return None
