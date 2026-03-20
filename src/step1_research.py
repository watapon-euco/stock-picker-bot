"""
Step 1: ニュース収集 → テーマ抽出 → 銘柄候補リストアップ → 株価データ取得

Phase A: Google News RSS からニュース収集 + Gemini でテーマ抽出
Phase B: Gemini で各テーマの関連銘柄をリストアップ
Phase C: yfinance で株価データ取得 + Gemini で構造化
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

from src.utils.gemini_client import GeminiClient
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


def _load_theme_history() -> List[str]:
    if THEME_HISTORY_FILE.exists():
        with open(THEME_HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [t.get("name", "") for t in data.get("themes", [])]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Phase A: ニュース収集 + テーマ抽出
# ─────────────────────────────────────────────────────────────────────────────

PHASE_A_PROMPT = """
あなたは日本株式市場の専門アナリストです。
以下の直近1ヶ月の株式・経済ニュース見出しを分析し、
来月の株式投資において注目すべきテーマを選定してください。

# ニュース一覧
{news_text}

# 過去に選定済みのテーマ（重複を避けること）
{past_themes}

# 指示
1. ニュースから投資テーマの候補を10個抽出する
2. 各候補を以下の4軸でスコアリング（各10点満点）:
   - policy_impact: 政策・規制の追い風
   - market_size: 市場規模・成長性
   - novelty: 新規性・話題性
   - sustainability: 持続性（一過性でないか）
3. 過去テーマと被らない上位1〜3テーマを選定
4. 各テーマの背景を3〜5行で要約

以下のJSON形式で出力してください:
{{
  "themes": [
    {{
      "name": "テーマ名（簡潔に）",
      "summary": "テーマ背景の要約（3〜5行）",
      "keywords": ["キーワード1", "キーワード2", ...],
      "scores": {{
        "policy_impact": 8,
        "market_size": 7,
        "novelty": 9,
        "sustainability": 6
      }},
      "total_score": 30,
      "icon": "絵文字1文字"
    }}
  ],
  "candidates_count": 10
}}
"""


def phase_a_extract_themes(gemini: GeminiClient) -> List[Dict]:
    """ニュース収集とテーマ抽出を実行する"""
    logger.info("[Phase A] Fetching news from RSS feeds...")
    articles = fetch_news(days=30, max_per_feed=40)

    if not articles:
        raise RuntimeError("No news articles fetched. Check RSS feed URLs.")

    # ニューステキストを構築（トークン節約のため上位100件）
    news_lines = []
    for i, art in enumerate(articles[:100]):
        news_lines.append(f"{i+1}. 【{art['source']}】{art['title']}")
        if art["summary"]:
            news_lines.append(f"   {art['summary'][:150]}")
    news_text = "\n".join(news_lines)

    past_themes = _load_theme_history()
    past_text = "、".join(past_themes) if past_themes else "なし"

    prompt = PHASE_A_PROMPT.format(
        news_text=news_text,
        past_themes=past_text,
    )

    logger.info("[Phase A] Calling Gemini for theme extraction...")
    result = gemini.generate_json(prompt)

    themes = result.get("themes", [])
    if not themes:
        raise RuntimeError("Gemini returned no themes in Phase A")

    # テーマを最大3件に絞り込み（スコア順）
    themes = sorted(themes, key=lambda t: t.get("total_score", 0), reverse=True)[:3]
    logger.info(f"[Phase A] Extracted {len(themes)} themes: {[t['name'] for t in themes]}")

    DATA_DIR.mkdir(exist_ok=True)
    with open(THEMES_FILE, "w", encoding="utf-8") as f:
        json.dump({"themes": themes}, f, ensure_ascii=False, indent=2)

    return themes


# ─────────────────────────────────────────────────────────────────────────────
# Phase B: 関連銘柄リストアップ
# ─────────────────────────────────────────────────────────────────────────────

PHASE_B_PROMPT = """
あなたは日本株式市場の専門アナリストです。
以下のテーマに関連する東証上場銘柄をリストアップしてください。

# テーマ
名前: {theme_name}
背景: {theme_summary}
キーワード: {keywords}

# 指示
1. このテーマに関連する上場銘柄を15〜20社挙げる（証券コードと銘柄名を必ず含める）
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
      "name": "ソニーグループ",
      "relation": "indirect",
      "reason": "選定理由（1〜2文）"
    }}
  ]
}}
"""


def phase_b_list_candidates(gemini: GeminiClient, themes: List[Dict]) -> List[Dict]:
    """各テーマの関連銘柄候補をリストアップする"""
    all_candidates = []

    for theme in themes:
        logger.info(f"[Phase B] Listing candidates for theme: {theme['name']}")
        prompt = PHASE_B_PROMPT.format(
            theme_name=theme["name"],
            theme_summary=theme["summary"],
            keywords="、".join(theme.get("keywords", [])),
        )

        result = gemini.generate_json(prompt)
        candidates = result.get("candidates", [])

        if not candidates:
            logger.warning(f"No candidates found for theme: {theme['name']}")
            continue

        # 最大8件に絞り込み
        candidates = candidates[:8]
        all_candidates.append({
            "theme_name": theme["name"],
            "candidates": candidates,
        })
        logger.info(
            f"[Phase B] Theme '{theme['name']}': {len(candidates)} candidates selected"
        )

    if not all_candidates:
        raise RuntimeError("No stock candidates found for any theme")

    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump({"themes": all_candidates}, f, ensure_ascii=False, indent=2)

    return all_candidates


# ─────────────────────────────────────────────────────────────────────────────
# Phase C: 株価データ取得 + 構造化
# ─────────────────────────────────────────────────────────────────────────────

PHASE_C_STRUCTURE_PROMPT = """
あなたは日本株式市場の専門アナリストです。
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

    for theme_data in candidates_by_theme:
        theme_name = theme_data["theme_name"]
        candidates = theme_data["candidates"]

        # 証券コードを収集
        codes = [c["code"] for c in candidates if c.get("code")]
        if not codes:
            logger.warning(f"No codes for theme: {theme_name}")
            continue

        logger.info(f"[Phase C] Fetching yfinance data for {len(codes)} stocks...")
        raw_results = fetch_multiple(codes, min_success=3)

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

        structured_themes.append({
            "theme_name": theme_name,
            "stocks": stocks,
        })
        logger.info(
            f"[Phase C] Structured {len(stocks)} stocks for theme: {theme_name}"
        )

    if not structured_themes:
        raise RuntimeError("No structured stock data generated")

    with open(STOCK_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"themes": structured_themes}, f, ensure_ascii=False, indent=2)

    logger.info(f"[Phase C] Saved structured data to {STOCK_DATA_FILE}")
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
