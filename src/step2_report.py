"""
Step 2: Claude Sonnet Batch API で分析・ランキング・HTML生成

入力: data/themes.json + data/stock_data.json
出力: docs/index.html (最新号) + docs/archive/YYYY-MM.html + docs/archive/index.html
      data/theme_history.json (更新)
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

from src.utils.claude_batch import ClaudeBatchClient

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


# ─────────────────────────────────────────────────────────────────────────────
# Claude Batch プロンプト
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
あなたは日本株式市場の専門アナリストです。
指定されたHTMLフラグメントのみを生成してください。
<!DOCTYPE html>、<html>、<head>、<body>タグは含めないでください。
説明文や前置きは不要です。HTMLコードのみを出力してください。
"""

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
        themes = json.load(f).get("themes", [])
    with open(STOCK_DATA_FILE, encoding="utf-8") as f:
        stock_data = json.load(f).get("themes", [])
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template_html = f.read()

    if not themes:
        raise RuntimeError("themes.json is empty")
    if not stock_data:
        raise RuntimeError("stock_data.json is empty")

    # 年月の決定
    now = datetime.now(timezone.utc).astimezone()
    year_month_str = now.strftime("%Y-%m")

    # Claudeへのプロンプトを2件のバッチリクエストとして送信
    # （サマリーカードとランキングセクションを分割することでトークン上限問題を回避）
    summary_prompt = build_summary_cards_prompt(themes)
    ranking_prompt = build_ranking_sections_prompt(themes, stock_data)

    logger.info(f"Summary cards prompt: {len(summary_prompt)} chars")
    logger.info(f"Ranking sections prompt: {len(ranking_prompt)} chars")

    claude = ClaudeBatchClient(api_key=api_key)
    results = claude.run_batch(
        requests=[
            {"custom_id": "summary_cards", "user_message": summary_prompt},
            {"custom_id": "ranking_sections", "user_message": ranking_prompt},
        ],
        system_prompt=SYSTEM_PROMPT,
        max_tokens=8000,
    )

    summary_cards_html = strip_code_fence(results.get("summary_cards", "").strip())
    ranking_sections_html = strip_code_fence(results.get("ranking_sections", "").strip())

    logger.info(f"summary_cards response: {len(summary_cards_html)} chars")
    logger.info(f"ranking_sections response: {len(ranking_sections_html)} chars")

    if not summary_cards_html:
        logger.warning("summary_cards response is empty!")
    if not ranking_sections_html:
        logger.warning("ranking_sections response is empty!")

    # テンプレートの全プレースホルダーをPythonで置換
    year_month_label = f"{now.year}年{now.month}月"
    generated_date_label = now.strftime("%Y年%m月%d日")
    total_stocks = sum(len(t.get("stocks", [])) for t in stock_data)
    archive_link_html = '<a href="archive/index.html">アーカイブ一覧</a>'

    html = template_html
    html = html.replace("{{YEAR_MONTH}}", year_month_label)
    html = html.replace("{{GENERATED_DATE}}", generated_date_label)
    html = html.replace("{{THEME_COUNT}}", str(len(themes)))
    html = html.replace("{{TOTAL_STOCKS}}", str(total_stocks))
    html = html.replace("{{ARCHIVE_LINKS}}", archive_link_html)
    html = html.replace("{{THEME_SUMMARY_CARDS}}", summary_cards_html)
    html = html.replace("{{THEME_RANKING_SECTIONS}}", ranking_sections_html)

    # 保存
    save_report(html, year_month_str)

    # テーマ履歴更新
    update_theme_history(themes, year_month_str)

    logger.info("Step 2 complete.")


if __name__ == "__main__":
    run()
