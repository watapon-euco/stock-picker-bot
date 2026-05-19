"""
ウォッチリスト監視ステップ

data/watchlist.json に登録された銘柄の価格変化・出来高急増を検知し、
アラート条件に該当する銘柄があれば LINE Flex Message で通知する。
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

WATCHLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "watchlist.json",
)


def _load_watchlist() -> dict:
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_watchlist(data: dict) -> None:
    from src.utils.helpers import atomic_write_json
    atomic_write_json(WATCHLIST_PATH, data)


def _build_flex_bubble(alert_items: List[Dict]) -> dict:
    """アラート銘柄リストから Flex Message バブルを構築する。"""
    body_contents = []

    for item in alert_items:
        name = item["name"]
        code = item["code"]
        reasons = item["reasons"]

        body_contents.append({
            "type": "text",
            "text": f"【{code}】{name}",
            "size": "sm",
            "weight": "bold",
            "color": "#1565C0",
            "wrap": True,
        })
        for reason in reasons:
            body_contents.append({
                "type": "text",
                "text": f"  • {reason}",
                "size": "xs",
                "color": "#555555",
                "wrap": True,
            })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1565C0",
            "contents": [
                {
                    "type": "text",
                    "text": "📊 ウォッチリスト・アラート",
                    "color": "#FFFFFF",
                    "size": "md",
                    "weight": "bold",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents if body_contents else [
                {
                    "type": "text",
                    "text": "（アラート該当銘柄なし）",
                    "size": "sm",
                    "color": "#888888",
                }
            ],
        },
    }


def run() -> None:
    """ウォッチリストをチェックし、アラート条件に該当する銘柄を通知。"""
    from src.utils.watchlist_checker import evaluate_alerts
    from src.utils.yfinance_fetcher import fetch_stock_data

    watchlist = _load_watchlist()
    stocks: List[dict] = watchlist.get("stocks", [])
    last_prices: Dict[str, float] = watchlist.get("last_prices", {})

    if not stocks:
        logger.info("ウォッチリストに銘柄が登録されていません。終了。")
        return

    alert_items: List[Dict] = []

    for stock_cfg in stocks:
        code: str = stock_cfg.get("code", "")
        name: str = stock_cfg.get("name", code)

        if not code:
            logger.warning(f"code が空の銘柄をスキップ: {stock_cfg}")
            continue

        market: str = stock_cfg.get("market", "JP")
        logger.info(f"チェック中: {code} {name} [{market}]")
        data = fetch_stock_data(code, market=market)

        if data is None:
            logger.warning(f"株価取得失敗: {code} — スキップ")
            continue

        current_price: Optional[float] = data.get("current_price")
        if current_price is None:
            logger.warning(f"current_price が None: {code} — スキップ")
            continue

        # 出来高スパイク検知: yfinance info の当日出来高と10日平均出来高を使う。
        # regularMarketVolume = 当日出来高、averageVolume10days = 10日平均出来高。
        # どちらかが取得できない場合は判定をスキップ（0を渡すと除算ガードが働く）。
        eff_current_vol: float = data.get("current_volume") or 0.0
        eff_avg_vol: float = data.get("avg_volume_10d") or 0.0

        prev_price: Optional[float] = last_prices.get(code)

        reasons = evaluate_alerts(
            stock_config=stock_cfg,
            current_price=current_price,
            prev_price=prev_price,
            current_volume=eff_current_vol,
            avg_volume_5d=eff_avg_vol,
        )

        # 価格を更新
        last_prices[code] = current_price

        if reasons:
            alert_items.append({"code": code, "name": name, "reasons": reasons})
            logger.info(f"アラート検出: {code} {name} — {reasons}")

    # watchlist.json を更新（アトミック書き込み）
    watchlist["last_prices"] = last_prices
    watchlist["last_check"] = datetime.now(timezone.utc).isoformat()
    _save_watchlist(watchlist)

    if not alert_items:
        logger.info("アラート該当銘柄なし。通知をスキップ。")
        return

    # LINE 通知
    channel_token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    group_id = os.environ.get("LINE_GROUP_ID", "")

    if not channel_token or not group_id:
        logger.warning(
            "LINE_CHANNEL_TOKEN または LINE_GROUP_ID が未設定。通知をスキップ。"
        )
        return

    from src.utils.line_client import LineClient

    client = LineClient(channel_token=channel_token)
    bubble = _build_flex_bubble(alert_items)
    names = "、".join(item["name"] for item in alert_items)
    alt_text = f"📊 ウォッチリスト・アラート: {names}"[:400]

    success = client.send_flex(to=group_id, alt_text=alt_text, flex_contents=bubble)
    if success:
        logger.info(f"LINE 通知送信完了: {len(alert_items)} 銘柄")
    else:
        logger.error("LINE 通知送信失敗")
