"""LINE Messaging API クライアント"""
import logging
from typing import Dict, List

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
        report_url: str | None,
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

        url_line = f"👉 {report_url}" if report_url else "（レポートURL未設定）"
        message = (
            f"📊 {year_month}号テーマ株レポートが公開されました！\n"
            f"\n"
            f"今月の注目テーマ:\n"
            f"{theme_text}\n"
            f"\n"
            f"{url_line}"
        )

        return self.send_text(to, message)

    def send_flex(self, to: str, alt_text: str, flex_contents: Dict) -> bool:
        """
        Flex Message を送信する。

        Args:
            to: 送信先ID（グループIDまたはユーザーID）
            alt_text: 通知バー・履歴に表示される代替テキスト（最大400文字）
            flex_contents: Flex Message の構造を定義した dict（bubble or carousel）

        Returns:
            成功した場合 True、失敗した場合 False
        """
        payload = {
            "to": to,
            "messages": [
                {
                    "type": "flex",
                    "altText": alt_text[:400],
                    "contents": flex_contents,
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
                logger.info(f"LINE Flex Message sent to {to}")
                return True
            else:
                logger.error(
                    f"LINE Flex API error: status={response.status_code}, "
                    f"body={response.text[:200]}"
                )
                return False
        except requests.RequestException as e:
            logger.error(f"LINE Flex API request failed: {e}")
            return False

    def send_flex_report_notification(
        self,
        to: str,
        year_month: str,
        themes: List[Dict],
        stock_data_themes: List[Dict],
        report_url: str,
    ) -> bool:
        """
        レポート公開をFlex Carouselで通知し、失敗時はテキストにフォールバックする。

        Args:
            to: 送信先ID
            year_month: 年月文字列（例: "2026年4月"）
            themes: テーマリスト（themes.json の themes 配列）
            stock_data_themes: stock_data.json の themes 配列
            report_url: レポートURL

        Returns:
            成功した場合 True
        """
        stock_map: Dict[str, List[Dict]] = {
            td["theme_name"]: td.get("stocks", [])
            for td in stock_data_themes
            if isinstance(td, dict) and "theme_name" in td
        }

        header_colors = ["#1565C0", "#0D47A1", "#283593"]
        bubbles = []

        for i, theme in enumerate(themes):
            theme_name = theme.get("name", "不明なテーマ")
            icon = theme.get("icon", "💹")
            summary = theme.get("summary", "")
            summary_text = (summary[:79] + "…") if len(summary) > 80 else summary
            has_warning = theme.get("sector_overlap_warning", False)

            header_color = header_colors[i % len(header_colors)]

            stocks = stock_map.get(theme_name, [])[:3]
            stock_rows = []
            for s in stocks:
                code = s.get("code", "----")
                name = s.get("name", code)
                price = s.get("current_price")
                change_pct = s.get("change_pct")

                if price is not None and change_pct is not None:
                    price_text = f"¥{price:,.0f} ({change_pct:+.1f}%)"
                elif price is not None:
                    price_text = f"¥{price:,.0f}"
                else:
                    price_text = "---"

                stock_rows.append({
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"{code} {name}"[:40],
                            "size": "sm",
                            "color": "#333333",
                            "flex": 3,
                            "wrap": False,
                        },
                        {
                            "type": "text",
                            "text": price_text,
                            "size": "sm",
                            "color": "#1565C0",
                            "flex": 2,
                            "align": "end",
                        },
                    ],
                    "spacing": "sm",
                })

            body_contents = []
            if summary_text:
                body_contents.append({
                    "type": "text",
                    "text": summary_text,
                    "size": "sm",
                    "color": "#555555",
                    "wrap": True,
                })
            if stock_rows:
                body_contents.append({"type": "separator", "margin": "md"})
                body_contents.extend(stock_rows)
            if has_warning:
                body_contents.append({
                    "type": "text",
                    "text": "⚠️ 分散注意",
                    "size": "xs",
                    "color": "#E65100",
                    "margin": "md",
                })

            bubble = {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": header_color,
                    "contents": [
                        {
                            "type": "text",
                            "text": f"{icon} {theme_name}"[:40],
                            "color": "#FFFFFF",
                            "size": "md",
                            "weight": "bold",
                            "wrap": True,
                        }
                    ],
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": body_contents if body_contents else [
                        {"type": "text", "text": "詳細はレポートを参照", "size": "sm", "color": "#555555"}
                    ],
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "color": "#1565C0",
                            "action": {
                                "type": "uri",
                                "label": "詳細レポートを見る",
                                "uri": report_url,
                            },
                        }
                    ],
                },
            }
            bubbles.append(bubble)

        if not bubbles:
            logger.warning("No bubbles generated; falling back to text notification")
            return self.send_report_notification(
                to=to,
                year_month=year_month,
                themes=[
                    {
                        "name": t.get("name", "不明"),
                        "icon": t.get("icon", "💹"),
                        "stock_count": len(stock_map.get(t.get("name", ""), [])),
                    }
                    for t in themes
                ],
                report_url=report_url,
            )

        flex_contents = {"type": "carousel", "contents": bubbles}
        alt_text = f"📊 {year_month}号テーマ株レポートが公開されました！"

        success = self.send_flex(to=to, alt_text=alt_text, flex_contents=flex_contents)
        if not success:
            logger.warning("Flex Message failed; falling back to text notification")
            return self.send_report_notification(
                to=to,
                year_month=year_month,
                themes=[
                    {
                        "name": t.get("name", "不明"),
                        "icon": t.get("icon", "💹"),
                        "stock_count": len(stock_map.get(t.get("name", ""), [])),
                    }
                    for t in themes
                ],
                report_url=report_url,
            )
        return True
