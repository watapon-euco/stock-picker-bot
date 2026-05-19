"""
バックテスト計算ロジック

theme_history.json の推奨銘柄と現在価格を比較してパフォーマンスを算出する。
yfinance を直接使用して過去価格・現在価格を取得する。

Note: price_at_pick は yfinance auto_adjust=True 経由の修正済み終値を前提とする。
      yfinance_fetcher._fetch_via_yfinance は auto_adjust=True で履歴を取得しており、
      step1_research.py の _record_theme_history が stock_data["current_price"]
      （yfinance info.regularMarketPrice）を保存する。regularMarketPrice は
      分割調整済みではないため、分割イベント後に乖離が生じる可能性がある点に注意。
      backtest 側は auto_adjust=True で現在価格を取得しており、長期保有銘柄で
      株式分割が発生した場合はリターン計算が過大評価になりうる。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

_DOWNLOAD_CHUNK_SIZE = 20  # yfinance.download の1回あたり最大銘柄数

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 価格取得（yfinance）
# ─────────────────────────────────────────────────────────────────────────────

def _to_yf_ticker(code: str, market: str = None) -> str:
    """銘柄コードを yfinance ティッカーに変換する。market 未指定時は自動検出。"""
    from src.utils.ticker_utils import detect_market, normalize_ticker
    if market is None:
        try:
            market = detect_market(code)
        except ValueError:
            market = "JP"
    return normalize_ticker(code, market)


def fetch_current_prices(codes: List[str], market_map: Dict[str, str] = None) -> Dict[str, Optional[float]]:
    """
    銘柄コードリストの現在価格を一括取得する。

    Args:
        codes: 証券コードのリスト（例: ["7203", "8035"]）
    Returns:
        {code: price or None} — 取得失敗の場合は None
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance がインストールされていません: pip install yfinance")
        return {c: None for c in codes}

    if not codes:
        return {}

    if market_map is None:
        market_map = {}

    tickers = [_to_yf_ticker(c, market_map.get(c)) for c in codes]
    ticker_to_code = {_to_yf_ticker(c, market_map.get(c)): c for c in codes}

    result: Dict[str, Optional[float]] = {c: None for c in codes}
    failed_tickers: List[str] = []

    for chunk_start in range(0, len(tickers), _DOWNLOAD_CHUNK_SIZE):
        chunk = tickers[chunk_start: chunk_start + _DOWNLOAD_CHUNK_SIZE]
        try:
            data = yf.download(chunk, period="5d", auto_adjust=True, progress=False)
            if data.empty:
                failed_tickers.extend(chunk)
            else:
                close = data["Close"] if "Close" in data.columns else data
                if hasattr(close, "columns"):
                    for ticker in chunk:
                        code = ticker_to_code.get(ticker)
                        if code is None:
                            continue
                        if ticker in close.columns:
                            series = close[ticker].dropna()
                            if not series.empty:
                                result[code] = float(series.iloc[-1])
                            else:
                                failed_tickers.append(ticker)
                else:
                    # 単一銘柄の場合
                    series = close.dropna()
                    if not series.empty:
                        code = ticker_to_code.get(chunk[0])
                        if code:
                            result[code] = float(series.iloc[-1])
                    else:
                        failed_tickers.extend(chunk)
        except Exception as e:
            logger.warning(f"価格チャンク取得エラー ({chunk}): {e}")
            failed_tickers.extend(chunk)

        if chunk_start + _DOWNLOAD_CHUNK_SIZE < len(tickers):
            time.sleep(0.5)

    if failed_tickers:
        logger.warning(f"価格取得失敗ティッカー: {failed_tickers}")

    return result


def fetch_month_end_price(code: str, year_month: str, market: str = None) -> Optional[float]:
    """
    指定年月の月末終値を取得する（推奨時価格の代替）。

    Args:
        code: 証券コード
        year_month: "YYYY-MM" 形式
        market: "JP" または "US"。None の場合は自動検出。
    Returns:
        月末終値、取得失敗時は None
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance がインストールされていません")
        return None

    try:
        year, month = map(int, year_month.split("-"))
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        start = f"{year:04d}-{month:02d}-01"
        end = f"{next_year:04d}-{next_month:02d}-01"

        ticker = _to_yf_ticker(code, market)
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"月末価格取得エラー ({code}, {year_month}): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# theme_history.json の正規化
# ─────────────────────────────────────────────────────────────────────────────

def _extract_monthly_entries(theme_history: dict) -> List[dict]:
    """
    theme_history.json の構造を月別エントリのリストに変換する。

    実際の構造:
      {"themes": [{"name": "...", "year_month": "2026-03", "icon": "...", ...}, ...]}

    Returns:
        [{"year_month": "2026-03", "themes": [...]}, ...]  (年月でグループ化)
    """
    themes_flat = theme_history.get("themes", [])

    months: Dict[str, List[dict]] = {}
    for t in themes_flat:
        ym = t.get("year_month", "")
        if not ym:
            continue
        months.setdefault(ym, []).append(t)

    return [
        {"year_month": ym, "themes": tlist}
        for ym, tlist in sorted(months.items())
    ]


# ─────────────────────────────────────────────────────────────────────────────
# パフォーマンス計算
# ─────────────────────────────────────────────────────────────────────────────

def calculate_performance(theme_history: dict, current_prices: Dict[str, Optional[float]]) -> dict:
    """
    過去推奨銘柄の現在パフォーマンスを計算する。

    Args:
        theme_history: theme_history.json の中身（dictとして読み込んだもの）
        current_prices: {code: current_price} の dict（None は価格不明）
    Returns:
        monthly リストと cumulative サマリーを含む dict
    """
    monthly_entries = _extract_monthly_entries(theme_history)

    monthly_results = []
    all_picks = []

    for entry in monthly_entries:
        year_month = entry["year_month"]
        themes = entry.get("themes", [])

        picks_this_month = []

        for theme in themes:
            theme_name = theme.get("name", "")
            icon = theme.get("icon", "")
            stocks = theme.get("stocks", [])

            for stock in stocks:
                code = str(stock.get("code", "")).strip()
                name = stock.get("name", code)
                rank = stock.get("rank")
                price_at_pick = stock.get("price_at_pick")
                stock_market = stock.get("market", "JP")

                current_price = current_prices.get(code)

                if current_price is None:
                    continue

                if price_at_pick is None:
                    price_at_pick = fetch_month_end_price(code, year_month, market=stock_market)

                if price_at_pick is None or price_at_pick == 0:
                    continue

                return_pct = (current_price - price_at_pick) / price_at_pick * 100

                from src.utils.ticker_utils import get_currency
                currency = get_currency(stock_market)
                pick = {
                    "code": code,
                    "name": name,
                    "rank": rank,
                    "theme": theme_name,
                    "theme_icon": icon,
                    "year_month": year_month,
                    "market": stock_market,
                    "currency": currency,
                    "price_at_pick": price_at_pick,
                    "current_price": current_price,
                    "return_pct": round(return_pct, 2),
                    "is_winner": return_pct > 0,
                }
                picks_this_month.append(pick)
                all_picks.append(pick)

        if not picks_this_month:
            monthly_results.append({
                "year_month": year_month,
                "themes": [t.get("name", "") for t in themes],
                "pick_count": 0,
                "avg_return_pct": None,
                "win_rate": None,
                "top_performer": None,
                "worst_performer": None,
                "picks": [],
            })
            continue

        returns = [p["return_pct"] for p in picks_this_month]
        winners = [p for p in picks_this_month if p["is_winner"]]
        avg_return = sum(returns) / len(returns)
        win_rate = len(winners) / len(picks_this_month)

        sorted_by_return = sorted(picks_this_month, key=lambda p: p["return_pct"])
        top = sorted_by_return[-1]
        worst = sorted_by_return[0]

        monthly_results.append({
            "year_month": year_month,
            "themes": [t.get("name", "") for t in themes],
            "pick_count": len(picks_this_month),
            "avg_return_pct": round(avg_return, 2),
            "win_rate": round(win_rate, 4),
            "top_performer": {
                "code": top["code"],
                "name": top["name"],
                "return_pct": top["return_pct"],
            },
            "worst_performer": {
                "code": worst["code"],
                "name": worst["name"],
                "return_pct": worst["return_pct"],
            },
            "picks": picks_this_month,
        })

    # 累計集計
    total_picks = len(all_picks)
    winning_picks = sum(1 for p in all_picks if p["is_winner"])
    overall_win_rate = (winning_picks / total_picks) if total_picks > 0 else 0.0
    avg_return_overall = (
        sum(p["return_pct"] for p in all_picks) / total_picks if total_picks > 0 else 0.0
    )

    best_pick = max(all_picks, key=lambda p: p["return_pct"]) if all_picks else None
    worst_pick = min(all_picks, key=lambda p: p["return_pct"]) if all_picks else None

    return {
        "monthly": monthly_results,
        "cumulative": {
            "total_picks": total_picks,
            "winning_picks": winning_picks,
            "overall_win_rate": round(overall_win_rate, 4),
            "avg_return_pct": round(avg_return_overall, 2),
            "best_pick_ever": best_pick,
            "worst_pick_ever": worst_pick,
        },
    }
