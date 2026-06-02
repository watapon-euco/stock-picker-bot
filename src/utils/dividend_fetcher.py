"""配当・権利落ち日取得ユーティリティ（yfinance ラッパー）

決算アラート（earnings_fetcher）と対になる投資イベント。権利付き最終日に向けた
仕込みや、配当目当ての保有判断に使う。直近の権利落ち日（ex-dividend date）が
近い銘柄を検出する。
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

import yfinance as yf

from src.utils.ticker_utils import normalize_ticker

logger = logging.getLogger(__name__)


def _to_ticker(code: str, market: str = None) -> str:
    """証券コードを yfinance ティッカーに変換する（market 自動判定対応）。"""
    return normalize_ticker(str(code).strip(), market)


def _ex_date_from_info(info: dict) -> Optional[date]:
    """yfinance info の exDividendDate（UNIX秒 or 文字列）を date に変換する。"""
    raw = info.get("exDividendDate")
    if raw is None:
        return None
    # UNIX エポック秒（yfinance の一般的な形式）
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    # "YYYY-MM-DD" 文字列
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    # date / datetime
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return None


def fetch_upcoming_dividend(
    code: str, lookahead_days: int = 14, market: str = None
) -> Optional[Dict]:
    """
    指定銘柄の今後 N 日以内の権利落ち日（ex-dividend date）を取得する。

    Args:
        code: 証券コード（例: "7203", "AAPL"）
        lookahead_days: 今日から何日先までを対象にするか（デフォルト 14）
        market: "JP" または "US"。None の場合はコード形式から自動判定。

    Returns:
        以下の dict、該当なし・取得失敗時は None:
        {
            "code": str,
            "ticker": str,
            "ex_date": str,              # "%Y-%m-%d (%a)" 形式
            "ex_date_raw": date,
            "dividend_yield": float | None,  # info の dividendYield をそのまま
            "dividend_rate": float | None,   # 1株あたり配当（info の dividendRate）
        }
    """
    ticker_str = _to_ticker(code, market)
    today = date.today()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        info = yf.Ticker(ticker_str).info
        if not info:
            return None

        ex_date = _ex_date_from_info(info)
        if ex_date is None or not (today <= ex_date <= cutoff):
            return None

        def _num(v):
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "code": str(code).replace(".T", "").replace(".OS", ""),
            "ticker": ticker_str,
            "ex_date": ex_date.strftime("%Y-%m-%d (%a)"),
            "ex_date_raw": ex_date,
            "dividend_yield": _num(info.get("dividendYield")),
            "dividend_rate": _num(info.get("dividendRate")),
        }
    except Exception as e:
        logger.debug(f"dividend fetch failed for {code} ({ticker_str}): {e}")
        return None
