"""
外部ライブラリ依存なしの純粋ユーティリティ関数。
テストから直接インポート可能。
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# セキュリティ
# ─────────────────────────────────────────────────────────────────────────────

def safe_url(url: str) -> str:
    """XSS対策: http/https 以外のURLスキームを無効化する"""
    if url and (url.startswith("https://") or url.startswith("http://")):
        return url
    return "#"


# ─────────────────────────────────────────────────────────────────────────────
# Gemini出力バリデーション
# ─────────────────────────────────────────────────────────────────────────────

def validate_themes(themes: list) -> List[Dict]:
    """Geminiのテーマ出力を検証し、不正エントリをスキップしてデフォルト値を補完する"""
    valid = []
    for t in themes:
        if not isinstance(t, dict):
            logger.warning(f"Skipping invalid theme (not dict): {type(t)}")
            continue
        if not t.get("name") or not isinstance(t.get("name"), str):
            logger.warning(f"Skipping theme with missing/invalid name: {t}")
            continue
        t.setdefault("summary", "")
        t.setdefault("keywords", [])
        t.setdefault("scores", {"policy_impact": 0, "market_size": 0, "novelty": 0, "sustainability": 0})
        if "total_score" not in t:
            t["total_score"] = sum(t["scores"].values())
        t.setdefault("icon", "💹")
        t.setdefault("source_articles", [])
        t.setdefault("investment_angle", "")
        if not isinstance(t["source_articles"], list):
            t["source_articles"] = []
        t["source_articles"] = [x for x in t["source_articles"] if isinstance(x, int)]
        valid.append(t)
    return valid


def validate_candidates(candidates: list) -> List[Dict]:
    """Geminiの銘柄候補出力を検証し、無効な証券コードのエントリをスキップする"""
    valid = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code", "")).strip().lstrip("0")
        # 証券コードは3〜5桁の数字
        if not code or not code.isdigit() or not (3 <= len(code) <= 5):
            logger.warning(f"Skipping candidate with invalid code: {c.get('code')!r}")
            continue
        c["code"] = code.zfill(4)
        c.setdefault("name", c["code"])
        c.setdefault("relation", "indirect")
        c.setdefault("reason", "")
        valid.append(c)
    return valid


# ─────────────────────────────────────────────────────────────────────────────
# ソースリンクHTML生成
# ─────────────────────────────────────────────────────────────────────────────

def build_source_links_html(themes: List[Dict], articles: List[Dict]) -> str:
    """テーマ別の主要ソースリンクHTMLを生成する"""
    if not articles:
        return '<p style="color:#555577;font-size:13px">ソース記事データがありません。</p>'

    html_parts = []

    for theme in themes:
        theme_name = theme.get("name", "")
        icon = theme.get("icon", "💹")
        source_indices = theme.get("source_articles", [])

        theme_articles = []
        for idx in source_indices:
            i = idx - 1 if isinstance(idx, int) else -1
            if 0 <= i < len(articles):
                art = articles[i]
                if art.get("link"):
                    theme_articles.append(art)

        if not theme_articles:
            keywords = theme.get("keywords", [])
            for art in articles[:50]:
                title = art.get("title", "")
                if any(kw in title for kw in keywords):
                    if art.get("link"):
                        theme_articles.append(art)
                if len(theme_articles) >= 5:
                    break

        if not theme_articles:
            continue

        items = []
        for art in theme_articles[:5]:
            source = art.get("source", "")
            title = art.get("title", "")
            link = safe_url(art.get("link", ""))
            source_tag = f'<span class="source-tag">{source}</span>' if source else ""
            items.append(
                f'<li>{source_tag}<a href="{link}" target="_blank" rel="noopener">{title}</a></li>'
            )

        html_parts.append(
            f'<div class="source-group">\n'
            f'  <h4>{icon} {theme_name}</h4>\n'
            f'  <ul class="source-list">\n'
            f'    {"".join(items)}\n'
            f'  </ul>\n'
            f'</div>'
        )

    if not html_parts:
        return '<p style="color:#555577;font-size:13px">関連ソース記事が見つかりませんでした。</p>'

    return "\n".join(html_parts)
