"""ウォッチリストのアラート条件を評価するヘルパー"""
from typing import List, Optional

from src.utils.ticker_utils import format_price, get_currency


def evaluate_alerts(
    stock_config: dict,
    current_price: float,
    prev_price: Optional[float],
    current_volume: float,
    avg_volume_5d: float,
) -> List[str]:
    """この銘柄に該当するアラートメッセージのリストを返す。

    Args:
        stock_config: watchlist.json の stocks[] の1エントリ
        current_price: 現在の株価
        prev_price: 前回チェック時の株価（初回チェックは None）
        current_volume: 当日の出来高（yfinance regularMarketVolume）
        avg_volume_5d: 平均出来高（yfinance averageVolume10days を渡す; 取得不可時は 0）

    Returns:
        人間可読なアラートメッセージのリスト。該当なしのときは空リスト。
        例: ["価格が +6.2% 変動しました（¥2,850 → ¥3,027）"]
    """
    alerts_config: dict = stock_config.get("alerts", {})
    market: str = stock_config.get("market", "JP")
    currency: str = get_currency(market)
    messages: List[str] = []

    def _fmt(price: float) -> str:
        return format_price(price, currency)

    # price_change_pct: 前回比で ±N% を超えた場合
    threshold_pct = alerts_config.get("price_change_pct")
    if threshold_pct is not None and prev_price is not None and prev_price != 0:
        change_pct = (current_price - prev_price) / prev_price * 100
        if abs(change_pct) >= threshold_pct:
            sign = "+" if change_pct >= 0 else ""
            messages.append(
                f"価格が {sign}{change_pct:.1f}% 変動しました"
                f"（{_fmt(prev_price)} → {_fmt(current_price)}）"
            )

    # price_above: 現在価格がしきい値を超えた場合
    price_above = alerts_config.get("price_above")
    if price_above is not None and current_price > price_above:
        messages.append(
            f"価格が {_fmt(price_above)} を超えました（現在 {_fmt(current_price)}）"
        )

    # price_below: 現在価格がしきい値を下回った場合
    price_below = alerts_config.get("price_below")
    if price_below is not None and current_price < price_below:
        messages.append(
            f"価格が {_fmt(price_below)} を下回りました（現在 {_fmt(current_price)}）"
        )

    # volume_spike_ratio: 当日出来高が直近5日平均の N 倍超の場合
    volume_ratio = alerts_config.get("volume_spike_ratio")
    if volume_ratio is not None and avg_volume_5d > 0:
        actual_ratio = current_volume / avg_volume_5d
        if actual_ratio > volume_ratio:
            messages.append(
                f"出来高急増: 直近5日平均の {actual_ratio:.1f} 倍"
                f"（{volume_ratio:.1f} 倍超で通知）"
            )

    return messages
