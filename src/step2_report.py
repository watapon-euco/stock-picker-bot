"""
Step 2: Claude Sonnet Batch API で分析・ランキング・HTML生成

入力: data/themes.json + data/stock_data.json + data/news_articles.json
出力: docs/index.html (最新号) + docs/archive/YYYY-MM.html + docs/archive/index.html
      data/theme_history.json (更新) + data/report_summary.json (更新)
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from src.config import CLAUDE_MODEL, GEMINI_MODEL
from src.utils.claude_batch import ClaudeBatchClient
from src.utils.helpers import build_source_links_html, safe_url as _safe_url
from src.utils.yfinance_fetcher import fetch_stock_data

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DOCS_DIR = Path("docs")
ARCHIVE_DIR = DOCS_DIR / "archive"
TEMPLATE_PATH = Path("src/templates/report_template.html")

THEMES_FILE = DATA_DIR / "themes.json"
STOCK_DATA_FILE = DATA_DIR / "stock_data.json"
THEME_HISTORY_FILE = DATA_DIR / "theme_history.json"
NEWS_ARTICLES_FILE = DATA_DIR / "news_articles.json"
REPORT_SUMMARY_FILE = DATA_DIR / "report_summary.json"


# ─────────────────────────────────────────────────────────────────────────────
# Claude Batch プロンプト
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
あなたは日本株式市場の専門アナリストです。
指定されたHTMLフラグメントのみを生成してください。
<!DOCTYPE html>、<html>、<head>、<body>タグは含めないでください。
説明文や前置きは不要です。HTMLコードのみを出力してください。
"""

# ── ニュース概況と投資戦略 ──

NEWS_STRATEGY_PROMPT = """
以下のニュース概況とテーマ情報をもとに、「先月のニュース概況と投資戦略」セクションのHTMLを生成してください。

## 生成対象
{{NEWS_STRATEGY_SECTION}} 部分に挿入するHTMLフラグメントです。

## 構成
1. ニュース概況（.news-overview-box 内にまとめ）:
   - 先月の株式・経済の主要トピック5〜8個を段落形式で要約
   - 市場全体のトレンドや注目イベントを含む
2. テーマ別の投資戦略（.strategy-cards 内に .strategy-card 要素群）:
   - 各テーマについて、ニュースからどのような投資戦略を導き出したかを説明

## ニュース全体の概況
{news_overview}

## テーマデータ（テーマ名・要約・投資視点を含む）
{themes_json}

## 出力フォーマット例
<div class="news-overview-box">
  <p>先月の日本株式市場は...（5〜8行の概況）</p>
</div>
<div class="strategy-cards">
  <div class="strategy-card">
    <div class="theme-name">🚀 防衛・宇宙テック</div>
    <div class="angle">このテーマに関連するニュースから読み取れる投資戦略の説明（3〜5行）</div>
  </div>
  （テーマ数分繰り返す）
</div>

HTMLフラグメントのみ出力してください。
"""

# ── 前月レポートからの変化 ──

CHANGES_PROMPT = """
以下の今月と前月のレポートデータをもとに、「前月レポートからの変化」セクションのHTMLを生成してください。

## 生成対象
{{CHANGES_SECTION}} 部分に挿入するHTMLフラグメントです。
前月レポートが存在しない場合は空のHTMLを返してください。

## 分析観点
- テーマの入れ替わり（新規テーマ・継続テーマ・終了テーマ）
- 注目銘柄の変化
- 市場環境の変化

## 今月のデータ
テーマ: {current_themes_json}
銘柄: {current_stocks_json}

## 前月のデータ
{previous_data_section}

## 使用するCSSクラス
- section.section : セクション全体
- .section-title : セクションタイトル
- .changes-box : 変化内容のボックス

## 出力フォーマット例
<section class="section">
  <div class="section-title">前月レポートからの変化</div>
  <div class="changes-box">
    <p><strong>テーマの変化:</strong> 先月の「〇〇」に代わり、今月は「△△」が新たに登場しました。...</p>
    <p><strong>注目銘柄の動き:</strong> ...</p>
    <p><strong>市場環境:</strong> ...</p>
  </div>
</section>

HTMLフラグメントのみ出力してください。前月データがない場合は空文字を返してください。
"""

# ── テーマ概要カード ──

SUMMARY_CARDS_PROMPT = """
以下のテーマ情報をもとに、各テーマの概要カードHTMLを生成してください。

## 生成対象
テーマ一覧セクションの {{THEME_SUMMARY_CARDS}} 部分に挿入するHTMLフラグメントです。
<div class="theme-summary-grid"> の中に入る .theme-summary-card 要素群のみを出力してください。

## 使用するCSSクラス（既存スタイルシートで定義済み）
- .theme-summary-card : カード全体
- .icon : テーマアイコン（絵文字）
- .theme-score-row + .score-chip : スコアチップ行

## テーマデータ
{themes_json}

## 出力フォーマット例
<div class="theme-summary-card">
  <div class="icon">🚀</div>
  <h2>防衛・宇宙テック</h2>
  <p>テーマの要約文（3〜5行）</p>
  <div class="theme-score-row">
    <span class="score-chip">政策: 8</span>
    <span class="score-chip">市場: 7</span>
    <span class="score-chip">新規: 9</span>
    <span class="score-chip">持続: 6</span>
  </div>
</div>
（テーマ数分繰り返す）

HTMLフラグメントのみ出力してください。<!DOCTYPE html>や<html>タグは不要です。
"""

# ── 銘柄ランキング ──

RANKING_SECTIONS_PROMPT = """
以下のデータをもとに、各テーマの銘柄ランキングセクションHTMLを生成してください。

## 生成対象
{{THEME_RANKING_SECTIONS}} 部分に挿入するHTMLフラグメントです。
section.theme-section 要素群のみを出力してください。

## 5軸スコアリングの重み付け（各銘柄に適用してランキングを決定）
- テーマ直結度: 30%（テーマのコア事業か間接的か）
- 業績モメンタム: 25%（直近決算の売上・利益成長）
- バリュエーション: 20%（PER・PBRが業界水準比）
- 財務健全性: 15%（financial_health スコア）
- カタリスト期待: 10%（今後のイベント・ニュースによる上昇余地）

各スコアは 0〜100 の数値で表現し、data-width 属性に設定してください。

## ティア分類（対応CSSクラス）
- tier-honmei（本命）: 総合スコア最上位
- tier-ogata（大型安定）: 大型株でリスク低め
- tier-hirisuku（ハイリスク）: 成長性高いが高リスク
- tier-omowaku（思惑）: 話題性先行
- tier-okure（出遅れ）: 割安な出遅れ株

## テーマデータ
{themes_json}

## 銘柄データ
stock_dataの各テーマには "failed_codes" フィールドがあり、yfinanceでデータ取得できなかった銘柄コードが含まれます。
これらの銘柄はランキングに含めず、テーマセクションの末尾に以下のような注釈を追加してください:
<p style="font-size:12px;color:#555577;margin-top:8px">※ データ未取得: 6789, 1234（yfinance取得エラー）</p>
failed_codesが空の場合は注釈不要です。

{stock_data_json}

## 出力フォーマット例
<section class="theme-section">
  <div class="theme-header">
    <div class="theme-icon">🚀</div>
    <div class="theme-title-block">
      <h3>防衛・宇宙テック</h3>
      <div class="stock-count">5銘柄</div>
    </div>
  </div>
  <div class="stock-list">
    <div class="stock-card rank-1">
      <div class="card-header">
        <div class="rank-badge">1</div>
        <span class="tier-label tier-honmei">本命</span>
        <div class="card-info">
          <div class="stock-name">IHI</div>
          <div class="stock-code">7013 / 航空・宇宙</div>
        </div>
        <div class="card-metrics">
          <div class="metric"><div class="value">¥8,430</div><div class="label">現在値</div></div>
          <div class="metric"><div class="value change up">+2.3%</div><div class="label">前日比</div></div>
        </div>
        <div class="card-expand-icon">▼</div>
      </div>
      <div class="card-detail">
        <div class="detail-grid">
          <div class="detail-item"><div class="d-label">PER</div><div class="d-value">15.2x</div></div>
          <div class="detail-item"><div class="d-label">PBR</div><div class="d-value">2.1x</div></div>
          <div class="detail-item"><div class="d-label">配当利回り</div><div class="d-value">1.5%</div></div>
          <div class="detail-item"><div class="d-label">財務健全性</div><div class="d-value health-stars">★★★★☆</div></div>
        </div>
        <div class="detail-text">
          <div class="dt-label">注目ポイント</div>
          <p>テーマとの関連ポイント（2〜3文）</p>
          <div class="dt-label" style="margin-top:10px">リスク要因</div>
          <p>主なリスク（1〜2文）</p>
        </div>
        <div class="score-bar-section">
          <div class="score-bar-label">5軸スコア評価</div>
          <div class="score-bars">
            <div class="score-bar-row">
              <div class="score-bar-name">テーマ直結度</div>
              <div class="score-bar-track"><div class="score-bar-fill" data-width="85" style="width:0%"></div></div>
              <div class="score-bar-val">85</div>
            </div>
            <div class="score-bar-row">
              <div class="score-bar-name">業績モメンタム</div>
              <div class="score-bar-track"><div class="score-bar-fill" data-width="70" style="width:0%"></div></div>
              <div class="score-bar-val">70</div>
            </div>
            <div class="score-bar-row">
              <div class="score-bar-name">バリュエーション</div>
              <div class="score-bar-track"><div class="score-bar-fill" data-width="60" style="width:0%"></div></div>
              <div class="score-bar-val">60</div>
            </div>
            <div class="score-bar-row">
              <div class="score-bar-name">財務健全性</div>
              <div class="score-bar-track"><div class="score-bar-fill" data-width="80" style="width:0%"></div></div>
              <div class="score-bar-val">80</div>
            </div>
            <div class="score-bar-row">
              <div class="score-bar-name">カタリスト期待</div>
              <div class="score-bar-track"><div class="score-bar-fill" data-width="75" style="width:0%"></div></div>
              <div class="score-bar-val">75</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    （銘柄数分繰り返す）
  </div>
</section>
（テーマ数分繰り返す）

HTMLフラグメントのみ出力してください。上位3銘柄にはrank-1/rank-2/rank-3クラスを付与してください。
"""


# ─────────────────────────────────────────────────────────────────────────────
# プロンプトビルダー
# ─────────────────────────────────────────────────────────────────────────────

def build_news_strategy_prompt(
    news_overview: str, themes: List[Dict]
) -> str:
    """ニュース概況＋投資戦略セクション用プロンプトを構築する"""
    return (NEWS_STRATEGY_PROMPT
            .replace("{news_overview}", news_overview or "（概況データなし）")
            .replace("{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2)))


def build_changes_prompt(
    current_themes: List[Dict],
    current_stocks: List[Dict],
    previous_summary: Optional[Dict],
) -> str:
    """前月比較セクション用プロンプトを構築する"""
    if previous_summary:
        prev_section = (
            f"前月テーマ: {json.dumps(previous_summary.get('themes', []), ensure_ascii=False, indent=2)}\n"
            f"前月銘柄: {json.dumps(previous_summary.get('top_stocks', []), ensure_ascii=False, indent=2)}\n"
            f"前月年月: {previous_summary.get('year_month', '不明')}"
        )
    else:
        prev_section = "前月レポートのデータはありません（初回実行）。"

    return (CHANGES_PROMPT
            .replace("{current_themes_json}", json.dumps(current_themes, ensure_ascii=False, indent=2))
            .replace("{current_stocks_json}", json.dumps(current_stocks, ensure_ascii=False, indent=2))
            .replace("{previous_data_section}", prev_section))


def build_summary_cards_prompt(themes: List[Dict]) -> str:
    """テーマ概要カード用プロンプトを構築する"""
    return SUMMARY_CARDS_PROMPT.replace(
        "{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2)
    )


def build_ranking_sections_prompt(themes: List[Dict], stock_data: List[Dict]) -> str:
    """銘柄ランキングセクション用プロンプトを構築する"""
    return (RANKING_SECTIONS_PROMPT
            .replace("{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2))
            .replace("{stock_data_json}", json.dumps(stock_data, ensure_ascii=False, indent=2)))


def strip_code_fence(text: str) -> str:
    """Claudeが出力したコードフェンス（```html ... ```）を除去する"""
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[start:end])
    return text


# build_source_links_html, safe_url は src.utils.helpers からインポート済み

# ─────────────────────────────────────────────────────────────────────────────
# 前月推奨銘柄のパフォーマンス追跡
# ─────────────────────────────────────────────────────────────────────────────

def fetch_performance(previous_summary: Dict) -> List[Dict]:
    """前月推奨銘柄の現在株価を取得してパフォーマンスを計算する"""
    top_stocks = previous_summary.get("top_stocks", [])
    if not top_stocks:
        return []

    results = []
    for stock in top_stocks:
        code = stock.get("code")
        price_at_report = stock.get("price_at_report")
        if not code or price_at_report is None:
            continue
        data = fetch_stock_data(code)
        if data and data.get("current_price"):
            current_price = data["current_price"]
            change_pct = (current_price - price_at_report) / price_at_report * 100
            results.append({
                "code": code,
                "name": stock.get("name", code),
                "theme": stock.get("theme", ""),
                "price_at_report": price_at_report,
                "current_price": current_price,
                "change_pct": round(change_pct, 2),
            })
        else:
            logger.warning(f"Could not fetch current price for {code} (performance tracking)")
    return results


def build_performance_html(perf_data: List[Dict], prev_year_month: str) -> str:
    """前月推奨銘柄のパフォーマンス表HTML（Python生成）"""
    if not perf_data:
        return ""

    rows = []
    for s in perf_data:
        chg = s["change_pct"]
        direction = "up" if chg >= 0 else "down"
        sign = "+" if chg >= 0 else ""
        rows.append(
            f'<tr>'
            f'<td>{s["name"]}<br><small style="color:#555577">{s["code"]}</small></td>'
            f'<td>{s["theme"]}</td>'
            f'<td>¥{s["price_at_report"]:,.0f}</td>'
            f'<td>¥{s["current_price"]:,.0f}</td>'
            f'<td class="change {direction}">{sign}{chg:.1f}%</td>'
            f'</tr>'
        )

    return (
        f'<section class="section">\n'
        f'  <div class="section-title">前月（{prev_year_month}）推奨銘柄のパフォーマンス</div>\n'
        f'  <div style="overflow-x:auto">\n'
        f'  <table class="perf-table">\n'
        f'    <thead><tr>'
        f'<th>銘柄</th><th>テーマ</th><th>推奨時株価</th><th>現在株価</th><th>騰落率</th>'
        f'</tr></thead>\n'
        f'    <tbody>{"".join(rows)}</tbody>\n'
        f'  </table>\n'
        f'  </div>\n'
        f'</section>\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 前月レポートサマリーの読み込み・保存
# ─────────────────────────────────────────────────────────────────────────────

def load_previous_summary() -> Optional[Dict]:
    """前月のレポートサマリーを読み込む"""
    if REPORT_SUMMARY_FILE.exists():
        with open(REPORT_SUMMARY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_report_summary(
    themes: List[Dict], stock_data: List[Dict], year_month_str: str
) -> None:
    """今月のレポートサマリーを保存する（来月の比較・パフォーマンス追跡用）"""
    top_stocks = []
    for td in stock_data:
        stocks = td.get("stocks", [])
        for s in stocks[:3]:  # 各テーマ上位3銘柄
            top_stocks.append({
                "theme": td.get("theme_name", ""),
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "price_at_report": s.get("current_price"),  # パフォーマンス追跡用
            })

    summary = {
        "year_month": year_month_str,
        "themes": [
            {"name": t.get("name", ""), "icon": t.get("icon", "💹")}
            for t in themes
        ],
        "top_stocks": top_stocks,
    }

    with open(REPORT_SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"Report summary saved to {REPORT_SUMMARY_FILE}")


# ─────────────────────────────────────────────────────────────────────────────
# 出力ファイル処理
# ─────────────────────────────────────────────────────────────────────────────

def update_archive_index(archive_dir: Path, year_month_str: str) -> None:
    """
    アーカイブ一覧ページを更新する。

    Args:
        archive_dir: アーカイブディレクトリ
        year_month_str: 追加するアーカイブの年月文字列 (例: "2026-04")
    """
    archive_index = archive_dir / "index.html"
    existing_archives: List[Dict] = []

    # 既存エントリを読み込み
    if archive_index.exists():
        content = archive_index.read_text(encoding="utf-8")
        for m in re.finditer(r'href="(\d{4}-\d{2}\.html)"[^>]*>([^<]+)</a>', content):
            existing_archives.append({"file": m.group(1), "label": m.group(2)})

    # 重複チェックして追加
    new_file = f"{year_month_str}.html"
    if not any(a["file"] == new_file for a in existing_archives):
        dt = datetime.strptime(year_month_str, "%Y-%m")
        label = f"{dt.year}年{dt.month}月号"
        existing_archives.insert(0, {"file": new_file, "label": label})

    # HTML生成
    items_html = "\n".join(
        f'<li><a href="{a["file"]}">{a["label"]}</a></li>'
        for a in existing_archives
    )

    archive_index.write_text(
        f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>テーマ株レポート アーカイブ</title>
<style>
  body {{ background: #0d0d1a; color: #e0e0f0;
         font-family: 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif;
         max-width: 600px; margin: 48px auto; padding: 0 16px; }}
  h1 {{ font-size: 20px; margin-bottom: 24px; color: #fff; }}
  ul {{ list-style: none; }}
  li {{ margin-bottom: 10px; }}
  a {{ color: #7eb8ff; font-size: 16px; }}
  a:hover {{ text-decoration: underline; }}
  .back {{ margin-top: 32px; font-size: 13px; }}
</style>
</head>
<body>
<h1>📚 テーマ株レポート アーカイブ</h1>
<ul>
{items_html}
</ul>
<p class="back"><a href="../index.html">← 最新号に戻る</a></p>
</body>
</html>
""",
        encoding="utf-8",
    )
    logger.info(f"Archive index updated: {len(existing_archives)} entries")


def update_theme_history(themes: List[Dict], year_month_str: str) -> None:
    """テーマ履歴に今月のテーマを追記する"""
    history: Dict = {"themes": []}
    if THEME_HISTORY_FILE.exists():
        with open(THEME_HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)

    for theme in themes:
        history["themes"].append({
            "name": theme.get("name", ""),
            "year_month": year_month_str,
            "icon": theme.get("icon", "💹"),
        })

    # 直近36エントリ（約3年分）のみ保持
    history["themes"] = history["themes"][-36:]

    with open(THEME_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    logger.info(f"Theme history updated: {len(history['themes'])} entries total")


def save_report(html: str, year_month_str: str) -> None:
    """
    HTMLレポートを docs/ に保存する。

    Args:
        html: 完成したHTMLコード
        year_month_str: 年月文字列 (例: "2026-04")
    """
    DOCS_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # 残存プレースホルダーの警告
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if remaining:
        logger.warning(f"Unreplaced placeholders found: {set(remaining)}")

    # 最新号
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")

    # アーカイブ
    archive_path = ARCHIVE_DIR / f"{year_month_str}.html"
    shutil.copy(DOCS_DIR / "index.html", archive_path)

    # アーカイブ一覧を更新
    update_archive_index(ARCHIVE_DIR, year_month_str)

    logger.info(f"Report saved: docs/index.html + archive/{year_month_str}.html")


# ─────────────────────────────────────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────────────────────────────────────

def run():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")

    # データ読み込み
    if not THEMES_FILE.exists():
        raise FileNotFoundError(f"{THEMES_FILE} not found. Run step1_research first.")
    if not STOCK_DATA_FILE.exists():
        raise FileNotFoundError(f"{STOCK_DATA_FILE} not found. Run step1_research first.")

    with open(THEMES_FILE, encoding="utf-8") as f:
        themes_data = json.load(f)
    themes = themes_data.get("themes", [])
    news_overview = themes_data.get("news_overview", "")

    with open(STOCK_DATA_FILE, encoding="utf-8") as f:
        stock_data = json.load(f).get("themes", [])
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template_html = f.read()

    # ニュース記事を読み込み（ソースリンク表示用）
    articles: List[Dict] = []
    if NEWS_ARTICLES_FILE.exists():
        with open(NEWS_ARTICLES_FILE, encoding="utf-8") as f:
            articles = json.load(f).get("articles", [])
        logger.info(f"Loaded {len(articles)} news articles for source links")

    # 前月レポートサマリーを読み込み
    previous_summary = load_previous_summary()
    if previous_summary:
        logger.info(f"Loaded previous report summary: {previous_summary.get('year_month', '?')}")

    if not themes:
        raise RuntimeError("themes.json is empty")
    if not stock_data:
        raise RuntimeError("stock_data.json is empty")

    # 年月の決定
    now = datetime.now(timezone.utc).astimezone()
    year_month_str = now.strftime("%Y-%m")

    # Claudeへのプロンプトを4件のバッチリクエストとして送信
    news_strategy_prompt = build_news_strategy_prompt(news_overview, themes)
    changes_prompt = build_changes_prompt(themes, stock_data, previous_summary)
    summary_prompt = build_summary_cards_prompt(themes)
    ranking_prompt = build_ranking_sections_prompt(themes, stock_data)

    batch_requests = [
        {"custom_id": "news_strategy", "user_message": news_strategy_prompt},
        {"custom_id": "changes", "user_message": changes_prompt},
        {"custom_id": "summary_cards", "user_message": summary_prompt},
        {"custom_id": "ranking_sections", "user_message": ranking_prompt},
    ]

    for req in batch_requests:
        logger.info(f"{req['custom_id']} prompt: {len(req['user_message'])} chars")

    claude = ClaudeBatchClient(api_key=api_key)
    results = claude.run_batch(
        requests=batch_requests,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=8000,
    )

    # Claude結果を取得・コードフェンス除去
    news_strategy_html = strip_code_fence(results.get("news_strategy", "").strip())
    changes_html = strip_code_fence(results.get("changes", "").strip())
    summary_cards_html = strip_code_fence(results.get("summary_cards", "").strip())
    ranking_sections_html = strip_code_fence(results.get("ranking_sections", "").strip())

    for name, content in [
        ("news_strategy", news_strategy_html),
        ("changes", changes_html),
        ("summary_cards", summary_cards_html),
        ("ranking_sections", ranking_sections_html),
    ]:
        logger.info(f"{name} response: {len(content)} chars")
        if not content:
            logger.warning(f"{name} response is empty!")

    # ソースリンクHTML（Python生成）
    source_links_html = build_source_links_html(themes, articles)

    # 前月推奨銘柄のパフォーマンス（Python生成）
    performance_html = ""
    if previous_summary:
        logger.info("Fetching performance data for previous top stocks...")
        perf_data = fetch_performance(previous_summary)
        if perf_data:
            performance_html = build_performance_html(
                perf_data, previous_summary.get("year_month", "前月")
            )
            logger.info(f"Performance data: {len(perf_data)} stocks")

    # テンプレートの全プレースホルダーをPythonで置換
    year_month_label = f"{now.year}年{now.month}月"
    generated_date_label = now.strftime("%Y年%m月%d日")
    total_stocks = sum(len(t.get("stocks", [])) for t in stock_data)
    archive_link_html = '<a href="archive/index.html">アーカイブ一覧</a>'
    ai_models_text = f"{GEMINI_MODEL} / {CLAUDE_MODEL}"

    html = template_html
    html = html.replace("{{YEAR_MONTH}}", year_month_label)
    html = html.replace("{{GENERATED_DATE}}", generated_date_label)
    html = html.replace("{{THEME_COUNT}}", str(len(themes)))
    html = html.replace("{{TOTAL_STOCKS}}", str(total_stocks))
    html = html.replace("{{ARCHIVE_LINKS}}", archive_link_html)
    html = html.replace("{{AI_MODELS_TEXT}}", ai_models_text)
    html = html.replace("{{NEWS_STRATEGY_SECTION}}", news_strategy_html)
    html = html.replace("{{THEME_SUMMARY_CARDS}}", summary_cards_html)
    html = html.replace("{{CHANGES_SECTION}}", changes_html)
    html = html.replace("{{PERFORMANCE_SECTION}}", performance_html)
    html = html.replace("{{THEME_RANKING_SECTIONS}}", ranking_sections_html)
    html = html.replace("{{SOURCE_LINKS_SECTION}}", source_links_html)

    # 保存
    save_report(html, year_month_str)

    # テーマ履歴更新
    update_theme_history(themes, year_month_str)

    # 来月の比較用にレポートサマリーを保存
    save_report_summary(themes, stock_data, year_month_str)

    logger.info("Step 2 complete.")


if __name__ == "__main__":
    run()
