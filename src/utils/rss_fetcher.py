"""Google News RSS フィード取得モジュール"""
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

import feedparser

logger = logging.getLogger(__name__)

# 日本株・経済ニュース用RSSフィードURL
RSS_FEEDS = [
    # 株式・投資
    "https://news.google.com/rss/search?q=日本株+投資+テーマ株&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=株式市場+注目+銘柄&hl=ja&gl=JP&ceid=JP:ja",
    # 経済・産業
    "https://news.google.com/rss/search?q=日本経済+新産業+成長+政策&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=東証+IPO+決算+業績&hl=ja&gl=JP&ceid=JP:ja",
    # トレンド
    "https://news.google.com/rss/search?q=AI+半導体+EV+再生可能エネルギー+日本&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=防衛+宇宙+半導体+日本株&hl=ja&gl=JP&ceid=JP:ja",
]


def fetch_news(days: int = 30, max_per_feed: int = 50) -> List[Dict]:
    """
    複数のRSSフィードからニュースを取得し、指定日数以内の記事を返す。

    Args:
        days: 取得対象の過去日数（デフォルト30日）
        max_per_feed: フィードあたりの最大取得件数

    Returns:
        ニュース記事のリスト（title, summary, published, link, source）
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles: List[Dict] = []
    seen_titles = set()

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                if count >= max_per_feed:
                    break

                title = entry.get("title", "").strip()
                if not title or title in seen_titles:
                    continue

                # 日付フィルタ
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

                if published and published < cutoff:
                    continue

                summary = entry.get("summary", "")
                # HTMLタグを簡易除去
                import re
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                articles.append({
                    "title": title,
                    "summary": summary[:300],
                    "published": published.isoformat() if published else "",
                    "link": entry.get("link", ""),
                    "source": feed.feed.get("title", url),
                })
                seen_titles.add(title)
                count += 1

            logger.info(f"Fetched {count} articles from {url[:60]}...")
            time.sleep(0.5)  # レート制限回避

        except Exception as e:
            logger.warning(f"Failed to fetch RSS {url[:60]}: {e}")

    # 日付降順ソート
    articles.sort(key=lambda x: x["published"], reverse=True)
    logger.info(f"Total articles collected: {len(articles)}")
    return articles
