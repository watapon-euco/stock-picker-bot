"""
Step 4: LINE グループへの更新通知

LINE Messaging API (Push Message) でレポート公開を通知する。
失敗してもレポート公開自体はブロックしない。
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.utils.line_client import LineClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

THEMES_FILE = Path("data/themes.json")

# ティアに対応したデフォルトアイコン（テーマのicon未設定時のフォールバック）
DEFAULT_ICONS = ["💹", "📈", "🚀"]


def run():
    channel_token = os.environ.get("LINE_CHANNEL_TOKEN")
    group_id = os.environ.get("LINE_GROUP_ID")
    report_url = os.environ.get("REPORT_URL", "https://github.com")

    if not channel_token:
        logger.warning("LINE_CHANNEL_TOKEN is not set. Skipping LINE notification.")
        return
    if not group_id:
        logger.warning("LINE_GROUP_ID is not set. Skipping LINE notification.")
        return

    # テーマ情報を読み込み
    themes_for_notify = []
    if THEMES_FILE.exists():
        try:
            with open(THEMES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for i, theme in enumerate(data.get("themes", [])):
                themes_for_notify.append({
                    "name": theme.get("name", "不明なテーマ"),
                    "icon": theme.get("icon", DEFAULT_ICONS[i % len(DEFAULT_ICONS)]),
                    "stock_count": theme.get("stock_count", 0),
                })
        except Exception as e:
            logger.warning(f"Failed to read themes.json: {e}")

    # stock_count が 0 の場合は stock_data.json から補完
    if themes_for_notify:
        stock_data_file = Path("data/stock_data.json")
        if stock_data_file.exists():
            try:
                with open(stock_data_file, encoding="utf-8") as f:
                    stock_data = json.load(f)
                count_map = {
                    td["theme_name"]: len(td.get("stocks", []))
                    for td in stock_data.get("themes", [])
                }
                for t in themes_for_notify:
                    if t["stock_count"] == 0:
                        t["stock_count"] = count_map.get(t["name"], 0)
            except Exception as e:
                logger.warning(f"Failed to read stock_data.json for count: {e}")

    # 年月文字列
    now = datetime.now(timezone.utc).astimezone()
    year_month_label = f"{now.year}年{now.month}月"

    line_client = LineClient(channel_token)
    success = line_client.send_report_notification(
        to=group_id,
        year_month=year_month_label,
        themes=themes_for_notify,
        report_url=report_url,
    )

    if success:
        logger.info("Step 4 complete: LINE notification sent.")
    else:
        logger.warning("Step 4: LINE notification failed (non-blocking).")


if __name__ == "__main__":
    run()
