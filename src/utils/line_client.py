"""LINE Messaging API クライアント"""
import logging
from typing import List

import requests

logger = logging.getLogger(__name__)

LINE_API_URL = "https://api.line.me/v2/bot/message/push"


class LineClient:
    def __init__(self, channel_token: str):
        self.channel_token = channel_token
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {channel_token}",
        }

    def send_text(self, to: str, text: str) -> bool:
        """
        テキストメッセージをグループまたはユーザーに送信する。

        Args:
            to: 送信先ID（グループIDまたはユーザーID）
            text: 送信するテキスト（最大5000文字）

        Returns:
            成功した場合 True、失敗した場合 False
        """
        payload = {
            "to": to,
            "messages": [
                {
                    "type": "text",
                    "text": text[:5000],
                }
            ],
        }

        try:
            response = requests.post(
                LINE_API_URL,
                headers=self.headers,
                json=payload,
                timeout=10,
            )
            if response.status_code == 200:
                logger.info(f"LINE message sent to {to}")
                return True
            else:
                logger.error(
                    f"LINE API error: status={response.status_code}, "
                    f"body={response.text[:200]}"
                )
                return False
        except requests.RequestException as e:
            logger.error(f"LINE API request failed: {e}")
            return False

    def send_report_notification(
        self,
        to: str,
        year_month: str,
        themes: List[dict],
        report_url: str,
    ) -> bool:
        """
        レポート公開通知メッセージを送信する。

        Args:
            to: 送信先ID
            year_month: 年月文字列（例: "2026年4月"）
            themes: テーマリスト [{"name": str, "stock_count": int, "icon": str}, ...]
            report_url: GitHub PagesのURL

        Returns:
            成功した場合 True
        """
        theme_lines = []
        for theme in themes:
            icon = theme.get("icon", "💹")
            name = theme.get("name", "不明なテーマ")
            count = theme.get("stock_count", 0)
            theme_lines.append(f"{icon} {name}（{count}銘柄）")

        theme_text = "\n".join(theme_lines) if theme_lines else "  （テーマなし）"

        message = (
            f"📊 {year_month}号テーマ株レポートが公開されました！\n"
            f"\n"
            f"今月の注目テーマ:\n"
            f"{theme_text}\n"
            f"\n"
            f"👉 {report_url}"
        )

        return self.send_text(to, message)
