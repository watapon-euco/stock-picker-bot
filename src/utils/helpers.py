"""
外部ライブラリ依存なしの純粋ユーティリティ関数。
テストから直接インポート可能。
"""
import html as _html
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# アトミックJSON書き込み
# ─────────────────────────────────────────────────────────────────────────────

def atomic_write_json(path: str, data: Any) -> None:
    """
    JSONデータを一時ファイルに書き込んでからos.replace()で差し替える。
    プロセスが途中でkillされてもファイルが空にならない。
    シングルプロセスでは安全。複数プロセスが同一ファイルに並列書き込みする場合は保証しない。
    """
    dir_ = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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
        t.setdefault("market", "JP")
        if not isinstance(t["source_articles"], list):
            t["source_articles"] = []
        t["source_articles"] = [x for x in t["source_articles"] if isinstance(x, int)]
        valid.append(t)
    return valid


def validate_candidates(candidates: list) -> List[Dict]:
    """Geminiの銘柄候補出力を検証し、無効な証券コードのエントリをスキップする。

    JP銘柄: 3〜5桁の数字コード（ゼロパディングして4桁に正規化）
    US銘柄: 1〜5文字の大文字英字（BRK.B のようなドット付きも可）
    market フィールドがない旧データは "JP" をデフォルトとして扱う。
    """
    import re
    valid = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        raw_code = str(c.get("code", "")).strip()
        market = c.get("market", None)

        # JP 判定: 数字のみのコード（market が明示されている場合も含む）
        bare_code = raw_code.lstrip("0")
        if (market == "JP" or market is None) and bare_code and bare_code.isdigit() and (3 <= len(bare_code) <= 5):
            c["code"] = bare_code.zfill(4)
            c["market"] = "JP"
            c.setdefault("name", c["code"])
            c.setdefault("relation", "indirect")
            c.setdefault("reason", "")
            valid.append(c)
            continue

        # US 判定: 1〜5文字の大文字英字（BRK.B のようなドット付きも許容）
        if market == "US" or (market is None and re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", raw_code)):
            c["code"] = raw_code
            c["market"] = "US"
            c.setdefault("name", c["code"])
            c.setdefault("relation", "indirect")
            c.setdefault("reason", "")
            valid.append(c)
            continue

        logger.warning(f"Skipping candidate with invalid code: {c.get('code')!r}")
    return valid


# ─────────────────────────────────────────────────────────────────────────────
# ソースリンクHTML生成
# ─────────────────────────────────────────────────────────────────────────────

def build_source_links_html(themes: List[Dict], articles: List[Dict]) -> str:
    """テーマ別の主要ソースリンクHTMLを生成する"""
    if not articles:
        return ""

    candidate_articles = articles[:50]
    title_index = [art.get("title", "") for art in candidate_articles]

    html_parts = ['<section class="tp-section">',
                  '  <div class="tp-section__head"><div class="tp-kicker">主要ソース</div></div>',
                  '  <div class="tp-sources">']

    found_any = False
    for theme in themes:
        theme_name = theme.get("name", "")
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
            for i, title in enumerate(title_index):
                if any(kw in title for kw in keywords) and candidate_articles[i].get("link"):
                    theme_articles.append(candidate_articles[i])
                if len(theme_articles) >= 5:
                    break

        if not theme_articles:
            continue

        found_any = True
        html_parts.append(f'    <div class="tp-sources__group">{_html.escape(theme_name)}</div>')
        for art in theme_articles[:5]:
            link = safe_url(art.get("link", ""))
            title = _html.escape(art.get("title", ""))
            html_parts.append(f'    <a class="tp-sources__link" href="{link}" target="_blank" rel="noopener">{title}</a>')

    html_parts.extend(['  </div>', '</section>'])

    if not found_any:
        return ""

    return "\n".join(html_parts) + "\n"
