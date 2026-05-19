"""
Step 1: ニュース収集 → テーマ抽出 → 銘柄候補リストアップ → 株価データ取得

Phase A: Google News RSS からニュース収集 + Gemini でテーマ抽出
Phase B: Gemini で各テーマの関連銘柄をリストアップ
Phase C: yfinance で株価データ取得 + Gemini で構造化
"""
import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from src.utils.gemini_client import GeminiClient
from src.utils.helpers import atomic_write_json, validate_candidates, validate_themes
from src.utils.rss_fetcher import fetch_news
from src.utils.yfinance_fetcher import fetch_multiple

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
THEMES_FILE = DATA_DIR / "themes.json"
CANDIDATES_FILE = DATA_DIR / "candidates.json"
STOCK_DATA_FILE = DATA_DIR / "stock_data.json"
THEME_HISTORY_FILE = DATA_DIR / "theme_history.json"
NEWS_ARTICLES_FILE = DATA_DIR / "news_articles.json"


def _load_theme_history() -> List[str]:
    if THEME_HISTORY_FILE.exists():
        with open(THEME_HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [t.get("name", "") for t in data.get("themes", [])]
    return []


def _load_full_theme_history() -> Dict:
    if THEME_HISTORY_FILE.exists():
        with open(THEME_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"themes": []}


def _record_theme_history(structured_themes: List[Dict], themes_meta: List[Dict]) -> None:
    """
    Phase C で確定した推奨銘柄を theme_history.json に追記する。
    同年月・同テーマ名のエントリが既に存在する場合は上書き（冪等）。
    書き込み失敗してもメインパイプラインを止めない。
    """
    year_month = datetime.now().strftime("%Y-%m")

    # themes.json のメタ情報（icon）を引くための辞書
    icon_map = {t.get("name", ""): t.get("icon", "📊") for t in themes_meta}

    new_entries = []
    for theme_data in structured_themes:
        theme_name = theme_data.get("theme_name", "")
        stocks = theme_data.get("stocks", [])
        icon = icon_map.get(theme_name, "📊")

        stock_records = [
            {
                "code": str(s.get("code", "")).strip(),
                "market": s.get("market", "JP"),
                "name": s.get("name", ""),
                "rank": s.get("rank", idx + 1),
                "price_at_pick": s.get("current_price"),
            }
            for idx, s in enumerate(stocks[:10])
        ]

        new_entries.append({
            "name": theme_name,
            "year_month": year_month,
            "icon": icon,
            "stocks": stock_records,
        })

    if not new_entries:
        return

    try:
        history = _load_full_theme_history()
        existing = history.get("themes", [])

        # 既存エントリから同年月・同テーマ名を除去してから新エントリを追加
        new_keys = {(e["year_month"], e["name"]) for e in new_entries}
        kept = [t for t in existing if (t.get("year_month"), t.get("name")) not in new_keys]
        history["themes"] = kept + new_entries

        atomic_write_json(str(THEME_HISTORY_FILE), history)
        logger.info(
            f"[Phase C] Updated {THEME_HISTORY_FILE} with {len(new_entries)} theme entries "
            f"for {year_month}"
        )
    except Exception as e:
        logger.warning(f"[Phase C] Failed to update theme_history.json: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase A: ニュース収集 + テーマ抽出
# ─────────────────────────────────────────────────────────────────────────────

PHASE_A_PROMPT = """
あなたは日本・米国の株式市場の専門アナリストです。
以下の直近1ヶ月の株式・経済ニュース見出しを分析し、
来月の株式投資において注目すべきテーマを選定してください。

# ニュース一覧
{news_text}

# 過去に選定済みのテーマ（重複を避けること）
{past_themes}

# 指示
1. ニュースから投資テーマの候補を10個抽出する（日本株・米国株どちらも対象）
2. 各候補を以下の4軸でスコアリング（各10点満点）:
   - policy_impact: 政策・規制の追い風
   - market_size: 市場規模・成長性
   - novelty: 新規性・話題性
   - sustainability: 持続性（一過性でないか）
3. 過去テーマと被らない上位1〜3テーマを選定
   - 3つのテーマのうち、最低1つは日本株市場（market: "JP"）、最低1つは米国株市場（market: "US"）のテーマにしてください
   - 例: 「日本AI関連」「米国EV関連」「日本防衛」など
4. 各テーマの背景を3〜5行で要約

以下のJSON形式で出力してください:
{{
  "themes": [
    {{
      "name": "テーマ名（簡潔に）",
      "market": "JP",
      "summary": "テーマ背景の要約（3〜5行）",
      "keywords": ["キーワード1", "キーワード2", ...],
      "scores": {{
        "policy_impact": 8,
        "market_size": 7,
        "novelty": 9,
        "sustainability": 6
      }},
      "total_score": 30,
      "icon": "絵文字1文字",
      "source_articles": [1, 5, 12],
      "investment_angle": "このテーマからどのような投資戦略が考えられるかの概要（2〜3文）"
    }}
  ],
  "news_overview": "先月の株式・経済ニュース全体の概況まとめ（5〜8行）",
  "candidates_count": 10
}}

market フィールドは必ず "JP" または "US" のどちらかを指定してください。
"""


def phase_a_extract_themes(gemini: GeminiClient) -> List[Dict]:
    """ニュース収集とテーマ抽出を実行する"""
    logger.info("[Phase A] Fetching news from RSS feeds...")
    articles = fetch_news(days=30, max_per_feed=40)

    if not articles:
        raise RuntimeError("No news articles fetched. Check RSS feed URLs.")

    # ニューステキストを構築（トークン節約のため上位100件）
    # プロンプトインジェクション対策: 改行・制御文字を除去し長さを制限
    def _sanitize(text: str, max_len: int = 200) -> str:
        return " ".join(text.replace("\n", " ").replace("\r", " ").split())[:max_len]

    top_articles = articles[:100]
    news_lines = []
    for i, art in enumerate(top_articles):
        title = _sanitize(art["title"], 150)
        source = _sanitize(art["source"], 30)
        news_lines.append(f"{i+1}. 【{source}】{title}")
        if art["summary"]:
            news_lines.append(f"   {_sanitize(art['summary'], 150)}")
    news_text = "\n".join(news_lines)

    # ニュース記事をファイルに保存（step2でソースリンク表示に使用）
    DATA_DIR.mkdir(exist_ok=True)
    saved_articles = []
    for art in top_articles:
        saved_articles.append({
            "title": _sanitize(art["title"], 200),
            "link": art.get("link", ""),
            "source": _sanitize(art["source"], 50),
            "published": art.get("published", ""),
        })
    atomic_write_json(str(NEWS_ARTICLES_FILE), {"articles": saved_articles})
    logger.info(f"[Phase A] Saved {len(saved_articles)} articles to {NEWS_ARTICLES_FILE}")

    past_themes = _load_theme_history()
    past_text = "、".join(past_themes) if past_themes else "なし"

    prompt = PHASE_A_PROMPT.format(
        news_text=news_text,
        past_themes=past_text,
    )

    logger.info("[Phase A] Calling Gemini for theme extraction...")
    result = gemini.generate_json(prompt)

    themes = validate_themes(result.get("themes", []))
    if not themes:
        raise RuntimeError("Gemini returned no valid themes in Phase A")

    # テーマを最大3件に絞り込み（スコア順）
    themes = sorted(themes, key=lambda t: t.get("total_score", 0), reverse=True)[:3]
    logger.info(f"[Phase A] Extracted {len(themes)} themes: {[t['name'] for t in themes]}")

    # ニュース全体の概況もthemes.jsonに保存
    news_overview = result.get("news_overview", "")

    atomic_write_json(str(THEMES_FILE), {"themes": themes, "news_overview": news_overview})

    return themes


# ─────────────────────────────────────────────────────────────────────────────
# Phase B: 関連銘柄リストアップ
# ─────────────────────────────────────────────────────────────────────────────

PHASE_B_PROMPT = """
あなたは日本・米国の株式市場の専門アナリストです。
以下のテーマ（市場: {theme_market}）に関連する上場銘柄をリストアップしてください。

# テーマ
名前: {theme_name}
市場: {theme_market}
背景: {theme_summary}
キーワード: {keywords}

# 指示
1. このテーマに関連する上場銘柄を15〜20社挙げる（証券コードと銘柄名を必ず含める）
   - 市場が "JP" の場合: 東証上場銘柄（4桁証券コード）
   - 市場が "US" の場合: 米国上場銘柄（ティッカーシンボル、例: AAPL, TSLA, BRK.B）
2. 各銘柄のテーマとの関連度を分類する:
   - "direct": テーマのコア事業が主力
   - "indirect": テーマ関連事業が一部
   - "peripheral": 間接的に恩恵を受ける
3. 関連度・期待度が高い銘柄を5〜8社に絞り込む

以下のJSON形式で出力してください:
{{
  "theme_name": "{theme_name}",
  "candidates": [
    {{
      "code": "6758",
      "market": "{theme_market}",
      "name": "ソニーグループ",
      "relation": "indirect",
      "reason": "選定理由（1〜2文）"
    }}
  ]
}}

各銘柄に market フィールド（"{theme_market}"）を必ず含めてください。
"""


def phase_b_list_candidates(gemini: GeminiClient, themes: List[Dict]) -> List[Dict]:
    """各テーマの関連銘柄候補をリストアップする（テーマ間は並列実行）"""

    def _fetch_one(theme: Dict) -> Optional[Dict]:
        logger.info(f"[Phase B] Listing candidates for theme: {theme['name']}")
        theme_market = theme.get("market", "JP")
        prompt = PHASE_B_PROMPT.format(
            theme_name=theme["name"],
            theme_market=theme_market,
            theme_summary=theme["summary"],
            keywords="、".join(theme.get("keywords", [])),
        )
        result = gemini.generate_json(prompt)
        candidates = validate_candidates(result.get("candidates", []))

        if not candidates:
            logger.warning(f"No valid candidates found for theme: {theme['name']}")
            return None

        candidates = candidates[:8]
        logger.info(f"[Phase B] Theme '{theme['name']}': {len(candidates)} candidates selected")
        return {"theme_name": theme["name"], "candidates": candidates}

    all_candidates = []
    with ThreadPoolExecutor(max_workers=min(3, len(themes))) as executor:
        futures = {executor.submit(_fetch_one, theme): theme for theme in themes}
        for future in as_completed(futures):
            result = future.result()
            if result:
                all_candidates.append(result)

    if not all_candidates:
        raise RuntimeError("No stock candidates found for any theme")

    # テーマ順を維持
    theme_order = {t["name"]: i for i, t in enumerate(themes)}
    all_candidates.sort(key=lambda x: theme_order.get(x["theme_name"], 99))

    atomic_write_json(str(CANDIDATES_FILE), {"themes": all_candidates})

    return all_candidates


# ─────────────────────────────────────────────────────────────────────────────
# セクター分散チェック
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_OVERLAP_THRESHOLD = 2  # 3テーマ中2テーマ以上で共通するセクターがあればアラート


def check_sector_overlap(sectors_by_theme: Dict[str, List[str]]) -> Dict:
    """
    テーマ間のセクター重複度を検査し、themes.json に書き込むメタデータを返す。

    Args:
        sectors_by_theme: {theme_name: [sector, ...]} の辞書（銘柄上位3件分のセクター）

    Returns:
        {"sector_overlap_warning": bool, "dominant_sectors": list[str]}
    """
    if len(sectors_by_theme) < 2:
        return {"sector_overlap_warning": False, "dominant_sectors": []}

    theme_sector_sets = [
        set(s for s in sectors if s)
        for sectors in sectors_by_theme.values()
    ]

    # 複数テーマに共通して現れるセクターを集計
    sector_count: Counter = Counter()
    for s_set in theme_sector_sets:
        for sector in s_set:
            sector_count[sector] += 1

    dominant = [s for s, cnt in sector_count.items() if cnt >= SECTOR_OVERLAP_THRESHOLD]

    warning = len(dominant) > 0
    if warning:
        logger.warning(
            f"[Sector Check] Sector overlap detected across themes: {dominant}"
        )
    else:
        logger.info("[Sector Check] No significant sector overlap detected.")

    return {"sector_overlap_warning": warning, "dominant_sectors": dominant}


# ─────────────────────────────────────────────────────────────────────────────
# Phase C: 株価データ取得 + 構造化
# ─────────────────────────────────────────────────────────────────────────────

PHASE_C_STRUCTURE_PROMPT = """
あなたは日本株式市場および米国株式市場の専門アナリストです。
以下のyfinanceから取得した銘柄データと候補情報をもとに、
構造化された銘柄プロファイルを作成してください。

# テーマ
{theme_name}

# 銘柄生データ
{raw_data}

# 指示
各銘柄について以下の構造化JSONを作成してください。
数値データが存在しない場合はnullを使用してください。
financial_healthは以下の基準で1〜5を付けてください:
  5: 財務優良, 4: 良好, 3: 普通, 2: やや懸念, 1: 要注意

以下のJSON形式で出力してください:
{{
  "theme_name": "{theme_name}",
  "stocks": [
    {{
      "code": "6758",
      "name": "ソニーグループ",
      "theme_relation": "indirect",
      "relation_reason": "テーマとの関連説明（1〜2文）",
      "current_price": 12500,
      "change_pct": 1.5,
      "market_cap_billion": 155000,
      "per": 18.5,
      "pbr": 2.1,
      "dividend_yield_pct": 0.6,
      "price_52w_high": 14000,
      "price_52w_low": 9800,
      "latest_revenue_billion": 2800,
      "latest_op_income_billion": 120,
      "financial_health": 4,
      "sector": "電子機器",
      "notable_point": "テーマ観点での注目ポイント（2〜3文）",
      "risk_factor": "主なリスク要因（1〜2文）"
    }}
  ]
}}
"""


def phase_c_fetch_and_structure(
    gemini: GeminiClient, candidates_by_theme: List[Dict]
) -> List[Dict]:
    """yfinanceでデータ取得後、Geminiで構造化する"""
    structured_themes = []
    # セクター分散チェック用: {theme_name: [sector, ...]} (上位3銘柄分)
    sectors_by_theme: Dict[str, List[str]] = {}

    for theme_data in candidates_by_theme:
        theme_name = theme_data["theme_name"]
        candidates = theme_data["candidates"]

        # 証券コードと市場情報を収集
        code_entries = [
            {"code": c["code"], "market": c.get("market", "JP")}
            for c in candidates if c.get("code")
        ]
        if not code_entries:
            logger.warning(f"No codes for theme: {theme_name}")
            continue

        logger.info(f"[Phase C] Fetching yfinance data for {len(code_entries)} stocks...")
        raw_results = fetch_multiple(code_entries, min_success=3)

        # 生データとcandidates情報を結合
        combined = []
        for candidate in candidates:
            code = candidate.get("code", "")
            stock_data = raw_results.get(code)
            if stock_data:
                combined.append({**stock_data, **{
                    "theme_relation": candidate.get("relation", "indirect"),
                    "relation_reason": candidate.get("reason", ""),
                }})

        if not combined:
            logger.warning(f"No valid stock data for theme: {theme_name}")
            continue

        # 上位3銘柄のセクターをセクター分散チェック用に収集
        sectors_by_theme[theme_name] = [
            s.get("sector", "") for s in combined[:3] if s.get("sector")
        ]

        # Geminiで構造化
        logger.info(f"[Phase C] Structuring data with Gemini for theme: {theme_name}")
        raw_json = json.dumps(combined, ensure_ascii=False, default=str)
        prompt = PHASE_C_STRUCTURE_PROMPT.format(
            theme_name=theme_name,
            raw_data=raw_json[:8000],  # トークン節約のため切り詰め
        )

        result = gemini.generate_json(prompt)
        stocks = result.get("stocks", [])

        if not stocks:
            logger.warning(f"Gemini returned no structured stocks for: {theme_name}")
            continue

        failed_codes = [
            c["code"] for c in candidates
            if raw_results.get(c.get("code")) is None
        ]
        if failed_codes:
            logger.warning(f"[Phase C] Failed to fetch data for: {failed_codes}")

        structured_themes.append({
            "theme_name": theme_name,
            "stocks": stocks,
            "failed_codes": failed_codes,
        })
        logger.info(
            f"[Phase C] Structured {len(stocks)} stocks for theme: {theme_name}"
            + (f" ({len(failed_codes)} failed)" if failed_codes else "")
        )

    if not structured_themes:
        raise RuntimeError("No structured stock data generated")

    atomic_write_json(str(STOCK_DATA_FILE), {"themes": structured_themes})

    logger.info(f"[Phase C] Saved structured data to {STOCK_DATA_FILE}")

    # セクター分散チェックを実行し themes.json を更新
    overlap_meta = check_sector_overlap(sectors_by_theme)
    themes_meta: List[Dict] = []
    if THEMES_FILE.exists():
        with open(THEMES_FILE, encoding="utf-8") as f:
            themes_data = json.load(f)
        themes_meta = themes_data.get("themes", [])
        themes_data.update(overlap_meta)
        atomic_write_json(str(THEMES_FILE), themes_data)
        logger.info(
            f"[Phase C] Updated {THEMES_FILE} with sector overlap metadata: {overlap_meta}"
        )

    # 推奨銘柄を theme_history.json に記録
    _record_theme_history(structured_themes, themes_meta)

    return structured_themes


# ─────────────────────────────────────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────────────────────────────────────

def run():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set")

    gemini = GeminiClient(api_key=api_key)

    # Phase A: ニュース収集 + テーマ抽出
    themes = phase_a_extract_themes(gemini)

    # Phase B: 銘柄候補リストアップ
    candidates_by_theme = phase_b_list_candidates(gemini, themes)

    # Phase C: 株価データ取得 + 構造化
    phase_c_fetch_and_structure(gemini, candidates_by_theme)

    logger.info("Step 1 complete.")


if __name__ == "__main__":
    run()
