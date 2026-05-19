"""
決算発表アラート ステップ

ウォッチリスト + 過去レポート推奨銘柄の決算発表日が近づいたら LINE Flex Message で通知する。
重複通知を防ぐため data/earnings_notified.json に通知済み情報を永続化する。

使用方法:
  python -m src.step_earnings_check
"""
import json
import logging
import os
import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Set

from src.utils.earnings_fetcher import fetch_upcoming_earnings
from src.utils.helpers import safe_url

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHLIST_PATH = os.path.join(_PROJECT_ROOT, "data", "watchlist.json")
THEME_HISTORY_PATH = os.path.join(_PROJECT_ROOT, "data", "theme_history.json")
NOTIFIED_PATH = os.path.join(_PROJECT_ROOT, "data", "earnings_notified.json")

LOOKAHEAD_DAYS = 3
HISTORY_MONTHS = 3


# ─────────────────────────────────────────────────────────────────────────────
# 銘柄収集
# ─────────────────────────────────────────────────────────────────────────────

def _load_watchlist_codes() -> List[Dict]:
    """watchlist.json から銘柄リストを読み込む。"""
    if not os.path.exists(WATCHLIST_PATH):
        logger.info(f"watchlist.json が見つかりません: {WATCHLIST_PATH}")
        return []
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("stocks", [])


def _load_theme_history_codes() -> List[Dict]:
    """theme_history.json の直近3ヶ月分のテーマから推奨銘柄を収集する。"""
    if not os.path.exists(THEME_HISTORY_PATH):
        logger.info(f"theme_history.json が見つかりません: {THEME_HISTORY_PATH}")
        return []

    with open(THEME_HISTORY_PATH, encoding="utf-8") as f:
        data = json.load(f)

    today = date.today()
    cutoff_year = today.year
    cutoff_month = today.month - HISTORY_MONTHS
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1

    stocks = []
    for theme in data.get("themes", []):
        ym = theme.get("year_month", "")
        try:
            theme_year, theme_month = int(ym[:4]), int(ym[5:7])
        except (ValueError, IndexError):
            continue
        if (theme_year, theme_month) < (cutoff_year, cutoff_month):
            continue
        for stock in theme.get("stocks", []):
            code = str(stock.get("code", "")).strip()
            if code:
                stocks.append({"code": code, "name": stock.get("name", code)})

    return stocks


def _collect_target_stocks() -> List[Dict]:
    """ウォッチリストと過去テーマ推奨銘柄を重複なしで収集する。"""
    seen: Set[str] = set()
    result: List[Dict] = []

    for entry in _load_watchlist_codes():
        code = str(entry.get("code", "")).strip()
        if code and code not in seen:
            seen.add(code)
            result.append({"code": code, "name": entry.get("name", code)})

    for entry in _load_theme_history_codes():
        code = str(entry.get("code", "")).strip()
        if code and code not in seen:
            seen.add(code)
            result.append({"code": code, "name": entry.get("name", code)})

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 重複通知防止
# ─────────────────────────────────────────────────────────────────────────────

def _load_notified() -> Dict[str, str]:
    """
    通知済みデータを読み込む。

    Returns:
        {"CODE:YYYY-MM-DD": "<ISO timestamp>"} のdict。
    """
    if not os.path.exists(NOTIFIED_PATH):
        return {}
    try:
        with open(NOTIFIED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_notified(notified: Dict[str, str]) -> None:
    from src.utils.helpers import atomic_write_json
    atomic_write_json(NOTIFIED_PATH, notified)


def _cleanup_notified(notified: Dict[str, str]) -> Dict[str, str]:
    """決算日が過去のエントリを削除する。"""
    today = date.today()
    return {
        key: ts
        for key, ts in notified.items()
        if _parse_notified_key_date(key) >= today
    }


def _parse_notified_key_date(key: str) -> date:
    """"CODE:YYYY-MM-DD" から date を抽出する。日付パース失敗時は過去日を返す。"""
    try:
        date_part = key.split(":", 1)[1]
        return date.fromisoformat(date_part)
    except Exception:
        return date(2000, 1, 1)


def _notified_key(code: str, earnings_date: date) -> str:
    return f"{code}:{earnings_date.isoformat()}"


# ─────────────────────────────────────────────────────────────────────────────
# LINE Flex Message 構築
# ─────────────────────────────────────────────────────────────────────────────

def _build_flex_bubble(alert_items: List[Dict], report_url: Optional[str]) -> dict:
    """決算アラート銘柄リストから Flex Message バブルを構築する。"""
    body_contents = []

    for item in alert_items:
        code = item["code"]
        name = item["name"][:40]
        earnings_date = item["earnings_date"]

        body_contents.append({
            "type": "text",
            "text": f"【{code}】{name}",
            "size": "sm",
            "weight": "bold",
            "color": "#1565C0",
            "wrap": True,
        })
        body_contents.append({
            "type": "text",
            "text": f"  決算日: {earnings_date}",
            "size": "xs",
            "color": "#333333",
            "wrap": True,
        })

        eps_lines = []
        if item.get("eps_estimate") is not None:
            eps_lines.append(f"予想EPS: {item['eps_estimate']:.2f}")
        if item.get("eps_actual") is not None:
            eps_lines.append(f"実績EPS: {item['eps_actual']:.2f}")
        if eps_lines:
            body_contents.append({
                "type": "text",
                "text": "  " + "  /  ".join(eps_lines),
                "size": "xs",
                "color": "#555555",
                "wrap": True,
            })

        body_contents.append({"type": "separator", "margin": "sm"})

    # 末尾の separator を除去
    if body_contents and body_contents[-1].get("type") == "separator":
        body_contents.pop()

    safe_report_url = safe_url(report_url) if report_url else "#"

    footer_contents: List[dict] = []
    if safe_report_url and safe_report_url != "#":
        footer_contents.append({
            "type": "button",
            "style": "secondary",
            "action": {
                "type": "uri",
                "label": "銘柄の特徴を再確認",
                "uri": safe_report_url,
            },
        })
    else:
        footer_contents.append({
            "type": "text",
            "text": "銘柄の特徴を再確認",
            "size": "xs",
            "color": "#888888",
            "align": "center",
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
                    "text": "📅 決算アラート",
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
                    "text": "（該当銘柄なし）",
                    "size": "sm",
                    "color": "#888888",
                }
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    """決算発表アラートをチェックし、3日以内の銘柄があれば LINE 通知する。"""
    from src.utils.cost_logger import log_api_call

    stocks = _collect_target_stocks()
    if not stocks:
        logger.info("対象銘柄が0件。終了。")
        return

    logger.info(f"決算チェック対象: {len(stocks)} 銘柄")

    notified = _load_notified()
    notified = _cleanup_notified(notified)

    alert_items: List[Dict] = []
    newly_notified_keys: List[str] = []

    for stock in stocks:
        code = stock["code"]
        name = stock["name"]

        t_start = time.monotonic()
        result = fetch_upcoming_earnings(code, lookahead_days=LOOKAHEAD_DAYS)
        log_api_call(
            provider="yfinance",
            model="",
            operation="fetch_upcoming_earnings",
            duration_sec=time.monotonic() - t_start,
            success=(result is not None),
            extra={"code": code},
        )

        if result is None:
            continue

        earnings_date_raw: date = result["earnings_date_raw"]
        key = _notified_key(code, earnings_date_raw)

        if key in notified:
            logger.debug(f"通知済みスキップ: {code} {earnings_date_raw}")
            continue

        logger.info(f"決算アラート検出: {code} {name} — {result['earnings_date']}")
        alert_items.append({
            "code": code,
            "name": name,
            "earnings_date": result["earnings_date"],
            "eps_estimate": result.get("eps_estimate"),
            "eps_actual": result.get("eps_actual"),
        })
        newly_notified_keys.append(key)

    if not alert_items:
        logger.info("決算アラート該当銘柄なし。通知をスキップ。")
        return

    channel_token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    group_id = os.environ.get("LINE_GROUP_ID", "")
    raw_url = os.environ.get("REPORT_URL", "") or ""
    report_url = safe_url(raw_url) if raw_url else ""
    if report_url == "#":
        report_url = ""

    if not channel_token or not group_id:
        logger.warning("LINE_CHANNEL_TOKEN または LINE_GROUP_ID が未設定。通知をスキップ。")
        # 通知できなくても notified は更新しない（次回再試行できるように）
        return

    from src.utils.line_client import LineClient

    client = LineClient(channel_token=channel_token)
    bubble = _build_flex_bubble(alert_items, report_url)
    names = "、".join(item["name"] for item in alert_items)
    alt_text = f"📅 決算アラート: {names}"[:400]

    success = client.send_flex(to=group_id, alt_text=alt_text, flex_contents=bubble)

    if success:
        logger.info(f"LINE 通知送信完了: {len(alert_items)} 銘柄")
        now_ts = datetime.now(timezone.utc).isoformat()
        for key in newly_notified_keys:
            notified[key] = now_ts
        _save_notified(notified)
    else:
        logger.error("LINE 通知送信失敗。通知済みログは更新しません。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()
