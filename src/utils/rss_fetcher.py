"""Google News RSS フィード取得モジュール"""
import re
import time
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=日本株+投資+テーマ株&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=株式市場+注目+銘柄&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=日本経済+新産業+成長+政策&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=東証+IPO+決算+業績&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=AI+半導体+EV+再生可能エネルギー+日本&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=防衛+宇宙+半導体+日本株&hl=ja&gl=JP&ceid=JP:ja",
]

_ATOM = "http://www.w3.org/2005/Atom"


def _parse_date(text: str):
    """RFC 822 or ISO 8601 date string → datetime (UTC), or None."""
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def _parse_feed(content: bytes, source_url: str) -> List[Dict]:
    """Parse RSS 2.0 or Atom XML bytes into a list of article dicts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.warning("XML parse error for %s: %s", source_url[:60], exc)
        return []

    tag = root.tag.lower()
    articles = []

    if "atom" in tag or root.tag == f"{{{_ATOM}}}feed":
        # Atom feed
        feed_title = root.findtext(f"{{{_ATOM}}}title") or source_url
        for entry in root.findall(f"{{{_ATOM}}}entry"):
            title = entry.findtext(f"{{{_ATOM}}}title") or ""
            link_el = entry.find(f"{{{_ATOM}}}link[@rel='alternate']") or entry.find(f"{{{_ATOM}}}link")
            link = link_el.get("href", "") if link_el is not None else ""
            summary = entry.findtext(f"{{{_ATOM}}}summary") or entry.findtext(f"{{{_ATOM}}}content") or ""
            published = _parse_date(entry.findtext(f"{{{_ATOM}}}published") or entry.findtext(f"{{{_ATOM}}}updated") or "")
            articles.append({"title": title, "link": link, "summary": summary, "published": published, "source": feed_title})
    else:
        # RSS 2.0
        channel = root.find("channel")
        if channel is None:
            return []
        feed_title = channel.findtext("title") or source_url
        for item in channel.findall("item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            summary = item.findtext("description") or ""
            published = _parse_date(item.findtext("pubDate") or item.findtext("dc:date") or "")
            articles.append({"title": title, "link": link, "summary": summary, "published": published, "source": feed_title})

    return articles


def fetch_news(days: int = 30, max_per_feed: int = 50) -> List[Dict]:
    """複数のRSSフィードからニュースを取得し、指定日数以内の記事を返す。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: List[Dict] = []
    seen_titles: set = set()

    headers = {"User-Agent": "Mozilla/5.0 (compatible; stock-picker-bot/1.0; +https://github.com)"}

    for url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            entries = _parse_feed(resp.content, url)
            count = 0
            for entry in entries:
                if count >= max_per_feed:
                    break
                title = entry["title"].strip()
                if not title or title in seen_titles:
                    continue
                published = entry["published"]
                if published and published < cutoff:
                    continue
                summary = re.sub(r"<[^>]+>", "", entry["summary"]).strip()
                result.append({
                    "title": title,
                    "summary": summary[:300],
                    "published": published.isoformat() if published else "",
                    "link": entry["link"],
                    "source": entry["source"],
                })
                seen_titles.add(title)
                count += 1
            logger.info("Fetched %d articles from %s...", count, url[:60])
            time.sleep(0.5)
        except Exception as e:
            logger.warning("Failed to fetch RSS %s: %s", url[:60], e)

    result.sort(key=lambda x: x["published"], reverse=True)
    logger.info("Total articles collected: %d", len(result))
    return result
