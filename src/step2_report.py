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
from typing import Dict, List, Optional

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
# Claude Batch プロンプト構築
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
あなたは日本株式市場の専門アナリストです。
提供されたテーマ情報と銘柄データをもとに、
投資家向けの高品質なHTMLレポートを生成してください。
出力は完全なHTMLコードのみです。説明文や前置きは不要です。
"""

ANALYSIS_PROMPT = """
# タスク
以下のデータをもとに、完成した株式テーマレポートのHTMLを生成してください。

## 実行日時
{generated_date}

## テーマ一覧
{themes_json}

## 銘柄データ（テーマ別）
{stock_data_json}

## HTMLテンプレート
以下のテンプレートに従って、完全なHTMLを生成してください。
プレースホルダー（{{YEAR_MONTH}} など）を実際の値に置き換えてください。

{template_html}

## 5軸評価の重み付け（各銘柄をスコアリングしてランキングを決定）
- テーマ直結度: 30%（テーマのコア事業か間接的か）
- 業績モメンタム: 25%（直近決算の売上・利益成長）
- バリュエーション: 20%（PER・PBRが業界水準と比較して割安か）
- 財務健全性: 15%（financial_health スコア）
- カタリスト期待: 10%（今後のイベント・ニュースによる株価上昇余地）

## ティア分類
各銘柄を以下のティアに分類し、対応するCSSクラスを適用してください:
- 本命 (tier-honmei): 総合スコア最上位、テーマ直結かつ業績好調
- 大型安定 (tier-ogata): 大型株でリスク低め、安定した収益基盤
- ハイリスク (tier-hirisuku): 成長性高いが赤字・高バリュエーション
- 思惑 (tier-omowaku): テーマ関連の話題性先行、業績はまだ小さい
- 出遅れ (tier-okure): テーマ関連性はあるが株価が出遅れている割安株

## HTMLプレースホルダーの置換ルール
- {{YEAR_MONTH}}: 年月（例: "2026年4月"）
- {{GENERATED_DATE}}: 生成日（例: "2026年4月1日"）
- {{THEME_COUNT}}: テーマ数
- {{TOTAL_STOCKS}}: 合計銘柄数
- {{ARCHIVE_LINKS}}: アーカイブリンク（archive/index.html へのリンクを含む）
- {{THEME_SUMMARY_CARDS}}: 各テーマのサマリーカードHTML
- {{THEME_RANKING_SECTIONS}}: 各テーマのランキングセクションHTML

## HTMLの要件
1. 自己完結型（CSSとJSをすべてインライン）
2. 全銘柄をスコアリングして順位をつける（各テーマ内でランキング）
3. 上位3銘柄はカードに rank-1/rank-2/rank-3 クラスを付与
4. 各カードにスコアバー（5軸）を含める（score-bar-fill の data-width に数値を設定）
5. テーマごとに section.theme-section を作成
6. 各テーマに適切なアイコン（絵文字）を選ぶ
7. 日本語で記述
8. 完全なHTMLを出力（<!DOCTYPE html>から</html>まで）

必ず完全なHTMLファイルを出力してください。途中で切れないようにしてください。
"""


def build_prompt(themes: List[Dict], stock_data: List[Dict], template_html: str) -> str:
    """Claudeへのプロンプトを構築する"""
    now = datetime.now(timezone.utc).astimezone()
    generated_date = now.strftime("%Y年%m月%d日")

    return ANALYSIS_PROMPT.format(
        generated_date=generated_date,
        themes_json=json.dumps(themes, ensure_ascii=False, indent=2),
        stock_data_json=json.dumps(stock_data, ensure_ascii=False, indent=2),
        template_html=template_html,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 出力ファイル処理
# ─────────────────────────────────────────────────────────────────────────────

def validate_html(html: str) -> bool:
    """生成されたHTMLの基本バリデーション"""
    checks = [
        "<!DOCTYPE html>" in html or "<!doctype html>" in html.lower(),
        "<html" in html,
        "</html>" in html,
        "<body" in html,
        "</body>" in html,
        "免責事項" in html,
    ]
    passed = sum(1 for c in checks if c)
    if passed < len(checks):
        logger.warning(f"HTML validation: {passed}/{len(checks)} checks passed")
    return passed >= 4  # 最低4項目はパス


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

    # 直近12ヶ月分のみ保持
    history["themes"] = history["themes"][-36:]

    with open(THEME_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    logger.info(f"Theme history updated: {len(history['themes'])} entries total")


def save_report(
    html: str,
    year_month_str: str,
    fallback_template: Optional[str] = None,
) -> None:
    """
    HTMLレポートを docs/ に保存する。

    Args:
        html: 生成されたHTMLコード
        year_month_str: 年月文字列 (例: "2026-04")
        fallback_template: バリデーション失敗時のフォールバックHTML
    """
    DOCS_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # バリデーション
    if not validate_html(html):
        logger.error("HTML validation failed. Using fallback template.")
        html = fallback_template or html

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

    # Claudeへのプロンプトを1件のバッチリクエストとして送信
    prompt = build_prompt(themes, stock_data, template_html)

    logger.info(f"Prompt length: {len(prompt)} chars (~{len(prompt)//4} tokens estimated)")

    claude = ClaudeBatchClient(api_key=api_key)
    results = claude.run_batch(
        requests=[{"custom_id": "report", "user_message": prompt}],
        system_prompt=SYSTEM_PROMPT,
        max_tokens=16000,
    )

    html = results.get("report", "")
    if not html:
        raise RuntimeError("Claude batch returned empty response for 'report'")

    # HTMLの先頭/末尾のコードフェンスを除去（念のため）
    html = html.strip()
    if html.startswith("```"):
        lines = html.split("\n")
        # ```html ... ``` の形式に対応
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1] == "```" else len(lines)
        html = "\n".join(lines[start:end])

    # 保存
    save_report(html, year_month_str, fallback_template=template_html)

    # テーマ履歴更新
    update_theme_history(themes, year_month_str)

    logger.info("Step 2 complete.")


if __name__ == "__main__":
    run()
