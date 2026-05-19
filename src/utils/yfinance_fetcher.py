"""yfinance を使った株データ取得モジュール（Stooq フォールバック付き、日本株・米国株対応）"""
import logging
import time
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from src.config import ENABLE_STOOQ_FALLBACK
from src.utils.cost_logger import log_api_call
from src.utils.stooq_fetcher import fetch_from_stooq
from src.utils.ticker_utils import detect_market, get_currency, normalize_ticker

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2
REQUEST_INTERVAL = 1.0  # リクエスト間の待機秒数


def _to_ticker(code: str, market: str = "JP") -> str:
    """証券コードをyfinanceティッカーに変換（後方互換ラッパー）"""
    return normalize_ticker(code, market)


def _safe_float(value, default=None) -> Optional[float]:
    try:
        v = float(value)
        return None if (v != v) else v  # NaN チェック
    except (TypeError, ValueError):
        return default


def _normalize_history_df(history_df) -> Optional[pd.DataFrame]:
    """yfinance (Date インデックス) と Stooq (Date カラム) の DataFrame を統一形式に変換する。

    戻り値は Close/Volume カラムを持つ DataFrame（インデックスは整数リセット済み）。
    どちら形式も Close/Volume カラムが存在していれば変換できる。
    """
    if history_df is None or history_df.empty:
        return None
    df = history_df.copy()
    # Date がインデックスになっている場合はリセットする（yfinance 形式）
    if df.index.name == "Date" or isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
    return df


def _extract_6m_history(history_df) -> Optional[Dict]:
    """OHLCV DataFrame から直近6ヶ月分の終値履歴を生成する。"""
    df = _normalize_history_df(history_df)
    if df is None or "Close" not in df.columns:
        return None
    close_6m = df["Close"].dropna().iloc[-126:]
    if close_6m.empty:
        return None

    # Date カラムがあれば使う、なければ整数インデックスを文字列にフォールバック
    if "Date" in df.columns:
        dates_series = df.loc[close_6m.index, "Date"]
        dates = [
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            for d in dates_series
        ]
    else:
        dates = [str(i) for i in close_6m.index]

    return {
        "dates": dates,
        "closes": [round(float(v), 2) for v in close_6m.values],
    }


def _calculate_technical_indicators(history_df) -> Dict:
    """
    OHLCV の日次履歴DataFrameからテクニカル指標を計算する。

    Args:
        history_df: yf.Ticker.history() の戻り値、または Stooq の DataFrame。
                    Close / Volume カラムを含むこと。Date はインデックスでもカラムでも可。

    Returns:
        テクニカル指標のdict。データ不足時は各値を None とする。
    """
    result: Dict = {
        "ma25": None,
        "ma75": None,
        "rsi14": None,
        "volume_ratio_5_30": None,
        "pct_from_52w_high": None,
        "pct_from_52w_low": None,
    }

    normalized = _normalize_history_df(history_df)
    if normalized is None or normalized.empty:
        return result

    close = normalized["Close"].dropna()
    volume = normalized["Volume"].dropna() if "Volume" in normalized.columns else pd.Series([], dtype=float)

    # 25日・75日移動平均（最低でも n 本必要）
    if len(close) >= 25:
        result["ma25"] = _safe_float(close.rolling(25).mean().iloc[-1])
    if len(close) >= 75:
        result["ma75"] = _safe_float(close.rolling(75).mean().iloc[-1])

    # RSI(14)
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean().iloc[-1]
        avg_loss = loss.rolling(14).mean().iloc[-1]
        if avg_loss is not None and not pd.isna(avg_loss) and avg_loss != 0:
            rs = avg_gain / avg_loss
            result["rsi14"] = _safe_float(100 - (100 / (1 + rs)))
        elif avg_gain is not None:
            result["rsi14"] = 100.0

    # 出来高変化率: 直近5日平均 / 過去30日平均
    if len(volume) >= 30:
        vol_5 = volume.iloc[-5:].mean()
        vol_30 = volume.iloc[-30:].mean()
        if vol_30 and vol_30 != 0:
            result["volume_ratio_5_30"] = _safe_float(vol_5 / vol_30)

    # 52週高値・安値からの乖離率
    if len(close) >= 1:
        current = close.iloc[-1]
        high_52w = close.max()
        low_52w = close.min()
        if high_52w is not None and not pd.isna(high_52w) and high_52w != 0:
            result["pct_from_52w_high"] = _safe_float((current - high_52w) / high_52w * 100)
        if low_52w is not None and not pd.isna(low_52w) and low_52w != 0:
            result["pct_from_52w_low"] = _safe_float((current - low_52w) / low_52w * 100)

    return result


def _is_data_incomplete(data: Dict) -> bool:
    """yfinance から取得したデータが不完全かどうかを判定する。

    current_price が None の場合を「不完全」とみなす。
    """
    return data.get("current_price") is None


def _fetch_via_yfinance(ticker_code: str, market: str = "JP") -> Optional[Dict]:
    """yfinance から1銘柄のデータを取得する（リトライ付き）。

    Args:
        ticker_code: 証券コード（例: "6758" or "AAPL"）
        market: "JP" または "US"

    Returns:
        銘柄データのdict（data_source フィールドなし）、取得失敗時は None
    """
    ticker = normalize_ticker(ticker_code, market)
    last_error: Optional[Exception] = None
    loop_start = time.monotonic()

    for attempt in range(MAX_RETRIES):
        start_time = time.monotonic()
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info

            if not info or info.get("regularMarketPrice") is None:
                duration = time.monotonic() - start_time
                log_api_call(
                    provider="yfinance",
                    model="",
                    operation="fetch_stock_data",
                    duration_sec=duration,
                    success=False,
                    extra={"code": ticker_code, "ticker": ticker, "reason": "no_market_data"},
                )
                logger.warning(f"No market data for {ticker}")
                return None

            technicals: Dict = {}
            price_history_6m = None
            try:
                hist = yf_ticker.history(period="1y")
                technicals = _calculate_technical_indicators(hist)
                price_history_6m = _extract_6m_history(hist)
            except Exception as e:
                logger.debug(f"Could not fetch history for {ticker}: {e}")

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

            duration = time.monotonic() - start_time
            log_api_call(
                provider="yfinance",
                model="",
                operation="fetch_stock_data",
                duration_sec=duration,
                success=True,
                extra={"code": ticker_code, "ticker": ticker},
            )

            currency = get_currency(market)
            code_clean = ticker_code.replace(".T", "").replace(".OS", "").replace("-", ".") if market == "US" else ticker_code.replace(".T", "").replace(".OS", "")

            return {
                "ticker": ticker,
                "code": code_clean,
                "market": market,
                "currency": currency,
                "name": info.get("longName") or info.get("shortName", ticker),
                "current_price": _safe_float(info.get("regularMarketPrice")),
                "current_volume": _safe_float(info.get("regularMarketVolume")),
                "avg_volume_10d": _safe_float(info.get("averageVolume10days")),
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
                "technicals": technicals,
                "price_history_6m": price_history_6m,
            }

        except Exception as e:
            last_error = e
            duration = time.monotonic() - start_time
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                f"yfinance error for {ticker} (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                f"Retrying in {delay}s..."
            )
            time.sleep(delay)

    log_api_call(
        provider="yfinance",
        model="",
        operation="fetch_stock_data",
        duration_sec=time.monotonic() - loop_start,
        success=False,
        extra={"code": ticker_code, "ticker": ticker, "error": str(last_error)},
    )
    logger.error(f"Failed to fetch {ticker} after {MAX_RETRIES} retries: {last_error}")
    return None


def fetch_stock_data(ticker_code: str, market: str = None) -> Optional[Dict]:
    """
    1銘柄の株価・指標・決算データを取得する。yfinance が失敗または不完全な場合は Stooq にフォールバックする。

    Args:
        ticker_code: 証券コード（例: "6758", "6758.T", "AAPL"）
        market: "JP" または "US"。None の場合は ticker_code から自動検出。

    Returns:
        銘柄データのdict（data_source, market, currency, ticker フィールド付き）、取得失敗時は None
    """
    if market is None:
        try:
            market = detect_market(ticker_code)
        except ValueError:
            market = "JP"

    yf_data = _fetch_via_yfinance(ticker_code, market)
    if yf_data and not _is_data_incomplete(yf_data):
        yf_data["data_source"] = "yfinance"
        logger.info(f"Fetched data for {yf_data.get('ticker', ticker_code)}: {yf_data.get('name', '')}")
        return yf_data

    if not ENABLE_STOOQ_FALLBACK:
        logger.warning(f"yfinance 失敗 or 不完全: {ticker_code}. Stooq フォールバックは無効")
        return None

    logger.info(f"yfinance 失敗 or 不完全: {ticker_code}. Stooq にフォールバック中...")
    stooq_data = fetch_from_stooq(ticker_code, market=market)
    if stooq_data:
        code_clean = str(ticker_code).replace(".T", "").replace(".OS", "").strip()
        currency = get_currency(market)
        stooq_data["ticker"] = normalize_ticker(ticker_code, market)
        stooq_data["code"] = code_clean
        stooq_data["market"] = market
        stooq_data["currency"] = currency
        stooq_data["name"] = code_clean
        stooq_data["previous_close"] = None
        stooq_data["change_pct"] = None
        stooq_data["market_cap"] = None
        stooq_data["per"] = None
        stooq_data["pbr"] = None
        stooq_data["dividend_yield"] = None
        stooq_data["52w_high"] = None
        stooq_data["52w_low"] = None
        stooq_data["sector"] = ""
        stooq_data["industry"] = ""
        stooq_data["quarterly"] = {}
        stooq_data["technicals"] = _calculate_technical_indicators(stooq_data["history"])
        stooq_data["price_history_6m"] = _extract_6m_history(stooq_data["history"])
        logger.info(f"Stooq フォールバック成功: {ticker_code}, price={stooq_data['current_price']}")
        return stooq_data

    logger.warning(f"yfinance と Stooq の両方で取得失敗: {ticker_code}")
    return None


def fetch_multiple(
    ticker_codes: List, min_success: int = 5
) -> Dict[str, Optional[Dict]]:
    """
    複数銘柄のデータを取得する。

    Args:
        ticker_codes: 証券コードのリスト。文字列（例: "6758"）または
                      {"code": "6758", "market": "JP"} のdict形式どちらも受け付ける。
                      dict形式では market フィールドで取引所を明示できる。
        min_success: 最低成功件数（下回った場合は例外を発生）

    Returns:
        {証券コード: データ} のdict（失敗したものは None）

    Raises:
        RuntimeError: 成功件数が min_success を下回った場合
    """
    results: Dict[str, Optional[Dict]] = {}
    success_count = 0

    for entry in ticker_codes:
        if isinstance(entry, dict):
            code = entry["code"]
            market = entry.get("market")
        else:
            code = entry
            market = None
        data = fetch_stock_data(code, market=market)
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
