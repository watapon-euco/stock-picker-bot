"""Google News RSS フィード取得モジュール"""
import re
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


# ─────────────────────────────────────────────────────────────────────────────
# 近重複ニュースの除去
# ─────────────────────────────────────────────────────────────────────────────

# Google News の見出し末尾の媒体名（" - 日本経済新聞" 等）を除去するパターン
_SOURCE_SUFFIX_RE = re.compile(r"\s*[-–—|]\s*[^-–—|]+$")
_NON_CONTENT_RE = re.compile(r"[\s　、。・,.!?！？「」『』（）()\[\]【】:：;；\"'”“]+")


def _normalize_title(title: str) -> str:
    """見出しを比較用に正規化する（媒体名サフィックス除去・記号/空白除去・小文字化）。"""
    t = title.strip()
    # 末尾の媒体名を1回だけ落とす（"記事 - 媒体" → "記事"）
    stripped = _SOURCE_SUFFIX_RE.sub("", t)
    # 全部消える（タイトル自体が短い等）場合は元に戻す
    if stripped.strip():
        t = stripped
    t = _NON_CONTENT_RE.sub("", t)
    return t.lower()


def _char_bigrams(s: str) -> set:
    """文字バイグラム集合（日本語の分かち書き不要で類似度を測れる）。"""
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def deduplicate_articles(articles: List[Dict], threshold: float = 0.72) -> List[Dict]:
    """同一・近重複の見出しを持つ記事を除去する（入力順で先に出たものを優先保持）。

    Google News は複数の検索フィードで同じ記事を媒体名違いで返すため、
    見出しを正規化したうえで完全一致・包含・文字バイグラム Jaccard 類似度で重複を判定する。
    これにより Gemini に渡すニュースの多様性が増し、テーマ抽出の質が向上する。

    Args:
        articles: ニュース記事 dict のリスト（"title" を持つこと）。
        threshold: この Jaccard 類似度以上を近重複とみなす（0〜1）。
    Returns:
        重複を除いた記事リスト（入力順を保持）。
    """
    kept: List[Dict] = []
    kept_norms: List[str] = []
    kept_grams: List[set] = []
    seen_exact: set = set()

    for art in articles:
        norm = _normalize_title(art.get("title", ""))
        if not norm or norm in seen_exact:
            continue

        grams = _char_bigrams(norm)
        is_dup = False
        for prev_norm, prev_grams in zip(kept_norms, kept_grams):
            if norm in prev_norm or prev_norm in norm or _jaccard(grams, prev_grams) >= threshold:
                is_dup = True
                break
        if is_dup:
            continue

        seen_exact.add(norm)
        kept.append(art)
        kept_norms.append(norm)
        kept_grams.append(grams)

    return kept


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

    # 近重複（媒体違いの同一記事など）を除去してニュースの多様性を確保
    before = len(articles)
    articles = deduplicate_articles(articles)
    logger.info(
        f"Total articles collected: {len(articles)} "
        f"(removed {before - len(articles)} near-duplicates)"
    )
    return articles
