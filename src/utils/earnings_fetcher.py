"""決算予定日取得ユーティリティ（yfinance earnings_dates ラッパー）"""
import logging
import math
from datetime import date, timedelta
from typing import Dict, Optional

import yfinance as yf

logger = logging.getLogger(__name__)


def _to_ticker(code: str) -> str:
    """証券コードを yfinance ティッカーに変換（日本株は .T サフィックス）。"""
    code = str(code).strip()
    if not code.endswith(".T") and not code.endswith(".OS"):
        code = code + ".T"
    return code


def fetch_upcoming_earnings(code: str, lookahead_days: int = 3) -> Optional[Dict]:
    """
    指定銘柄の今後 N 日以内の決算予定を取得する。

    Args:
        code: 証券コード（例: "7203"）
        lookahead_days: 今日から何日先までを対象にするか（デフォルト 3）

    Returns:
        決算情報の dict、該当なしまたは取得失敗時は None。
        dict の形式:
        {
            "code": str,
            "ticker": str,
            "earnings_date": str,       # "%Y-%m-%d (%a)" 形式
            "earnings_date_raw": date,  # date オブジェクト
            "eps_estimate": float | None,
            "eps_actual": float | None,
        }
    """
    ticker_str = _to_ticker(code)
    today = date.today()
    cutoff = today + timedelta(days=lookahead_days)

    try:
        ticker = yf.Ticker(ticker_str)
        df = ticker.earnings_dates
        if df is None or df.empty:
            return None

        for idx in df.index:
            try:
                # インデックスは Timestamp 型、date() で date に変換
                earning_date: date = idx.date() if hasattr(idx, "date") else idx
            except Exception:
                continue

            if today <= earning_date <= cutoff:
                eps_estimate = None
                eps_actual = None
                try:
                    row = df.loc[idx]
                    # カラム名は "EPS Estimate" / "Reported EPS" など yfinance バージョンで変化するため柔軟に取得
                    for col in df.columns:
                        col_lower = col.lower()
                        if "estimate" in col_lower:
                            v = row[col]
                            if v is not None:
                                try:
                                    f = float(v)
                                    eps_estimate = None if math.isnan(f) else f
                                except (TypeError, ValueError):
                                    pass
                        elif "reported" in col_lower or "actual" in col_lower or col_lower == "eps":
                            v = row[col]
                            if v is not None:
                                try:
                                    f = float(v)
                                    eps_actual = None if math.isnan(f) else f
                                except (TypeError, ValueError):
                                    pass
                except Exception:
                    pass

                return {
                    "code": str(code).replace(".T", "").replace(".OS", ""),
                    "ticker": ticker_str,
                    "earnings_date": earning_date.strftime("%Y-%m-%d (%a)"),
                    "earnings_date_raw": earning_date,
                    "eps_estimate": eps_estimate,
                    "eps_actual": eps_actual,
                }

        return None

    except Exception as e:
        logger.debug(f"earnings_dates failed for {code} ({ticker_str}): {e}")
        return None
