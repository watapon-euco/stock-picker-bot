"""yfinance を使った日本株データ取得モジュール"""
import logging
import time
from typing import Dict, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2
REQUEST_INTERVAL = 1.0  # リクエスト間の待機秒数


def _to_ticker(code: str) -> str:
    """証券コードをyfinanceティッカーに変換（日本株は末尾に .T）"""
    code = str(code).strip()
    if not code.endswith(".T") and not code.endswith(".OS"):
        code = code + ".T"
    return code


def _safe_float(value, default=None) -> Optional[float]:
    try:
        v = float(value)
        return None if (v != v) else v  # NaN チェック
    except (TypeError, ValueError):
        return default


def fetch_stock_data(ticker_code: str) -> Optional[Dict]:
    """
    1銘柄の株価・指標・決算データを取得する。

    Args:
        ticker_code: 証券コード（例: "6758" or "6758.T"）

    Returns:
        銘柄データのdict、取得失敗時は None
    """
    ticker = _to_ticker(ticker_code)
    last_error: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info

            if not info or info.get("regularMarketPrice") is None:
                logger.warning(f"No market data for {ticker}")
                return None

            # 直近四半期決算
            quarterly = {}
            try:
                fin = yf_ticker.quarterly_financials
                if fin is not None and not fin.empty:
                    latest_col = fin.columns[0]
                    quarterly = {
                        "revenue": _safe_float(fin.loc["Total Revenue", latest_col])
                        if "Total Revenue" in fin.index else None,
                        "operating_income": _safe_float(fin.loc["Operating Income", latest_col])
                        if "Operating Income" in fin.index else None,
                        "net_income": _safe_float(fin.loc["Net Income", latest_col])
                        if "Net Income" in fin.index else None,
                        "period": str(latest_col.date()) if hasattr(latest_col, "date") else str(latest_col),
                    }
            except Exception as e:
                logger.debug(f"Could not fetch quarterly financials for {ticker}: {e}")

            data = {
                "ticker": ticker,
                "code": ticker_code.replace(".T", "").replace(".OS", ""),
                "name": info.get("longName") or info.get("shortName", ticker),
                "current_price": _safe_float(info.get("regularMarketPrice")),
                "previous_close": _safe_float(info.get("previousClose")),
                "change_pct": _safe_float(info.get("regularMarketChangePercent")),
                "market_cap": _safe_float(info.get("marketCap")),
                "per": _safe_float(info.get("trailingPE")),
                "pbr": _safe_float(info.get("priceToBook")),
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "52w_high": _safe_float(info.get("fiftyTwoWeekHigh")),
                "52w_low": _safe_float(info.get("fiftyTwoWeekLow")),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "quarterly": quarterly,
            }
            logger.info(f"Fetched data for {ticker}: {data['name']}")
            return data

        except Exception as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                f"yfinance error for {ticker} (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                f"Retrying in {delay}s..."
            )
            time.sleep(delay)

    logger.error(f"Failed to fetch {ticker} after {MAX_RETRIES} retries: {last_error}")
    return None


def fetch_multiple(
    ticker_codes: List[str], min_success: int = 5
) -> Dict[str, Optional[Dict]]:
    """
    複数銘柄のデータを取得する。

    Args:
        ticker_codes: 証券コードのリスト
        min_success: 最低成功件数（下回った場合は例外を発生）

    Returns:
        {証券コード: データ} のdict（失敗したものは None）

    Raises:
        RuntimeError: 成功件数が min_success を下回った場合
    """
    results: Dict[str, Optional[Dict]] = {}
    success_count = 0

    for code in ticker_codes:
        data = fetch_stock_data(code)
        results[code] = data
        if data is not None:
            success_count += 1
        time.sleep(REQUEST_INTERVAL)

    if success_count < min_success:
        raise RuntimeError(
            f"Stock data fetch failed: only {success_count}/{len(ticker_codes)} succeeded "
            f"(minimum required: {min_success})"
        )

    logger.info(f"Stock data fetch complete: {success_count}/{len(ticker_codes)} succeeded")
    return results
