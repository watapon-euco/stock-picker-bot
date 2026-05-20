"""
Step 2: Claude Sonnet Batch API で分析・ランキング・HTML生成

入力: data/themes.json + data/stock_data.json + data/news_articles.json
出力: docs/index.html (最新号) + docs/archive/YYYY-MM.html + docs/archive/index.html
      data/theme_history.json (更新) + data/report_summary.json (更新)
"""
import html
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from src.config import CLAUDE_MODEL, GEMINI_MODEL
from src.utils.claude_batch import ClaudeBatchClient
from src.utils.helpers import atomic_write_json, build_source_links_html
from src.utils.ticker_utils import format_price, get_currency
from src.utils.yfinance_fetcher import fetch_multiple

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
以下のニュース概況をもとに、「先月のニュース概況」セクションのHTMLを生成してください。

## 生成対象
{{NEWS_STRATEGY_SECTION}} 部分に挿入するHTMLフラグメントです。

## 構成
先月の株式・経済の主要トピックを段落形式で要約（5〜8行）。
市場全体のトレンドや注目イベントを含む。

## ニュース全体の概況
{news_overview}

## テーマデータ（参考情報）
{themes_json}

## 出力フォーマット例
<section class="tp-section">
  <div class="tp-lede">
    <span class="tp-lede__drop">先</span>月の日本株式市場は...（続き）
  </div>
  <div class="tp-byline">市場概況</div>
</section>

注意: tp-lede__drop には本文の最初の1文字を入れ、残りはその後に続けてください。
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

## 出力フォーマット例
<section class="tp-section">
  <div class="tp-section__head"><div class="tp-kicker">前月比較</div></div>
  <div class="tp-callout">
    <div class="tp-callout__body">
      <p>テーマの変化: ...</p>
      <p>注目銘柄の動き: ...</p>
    </div>
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
- .angle : 投資戦略・着眼点の説明文

## テーマデータ
{themes_json}

## 出力フォーマット例
<div class="theme-summary-card">
  <div class="icon">🚀</div>
  <h2>防衛・宇宙テック</h2>
  <p>テーマの要約文（3〜5行）</p>
  <div class="angle">このテーマに関連するニュースから読み取れる投資戦略・着眼点（2〜4行）</div>
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

# ── リスクシナリオ ──

RISK_SCENARIOS_PROMPT = """
以下のテーマについて、投資家が想定すべきネガティブシナリオを3つ、各2-3文で簡潔に挙げてください。

## 生成対象
{{RISK_SCENARIOS_SECTION}} 部分に挿入するHTMLフラグメントです。
各テーマのリスクシナリオセクションを生成してください。

## テーマデータ
{themes_json}

## 出力形式
各テーマについて、以下の構造のHTMLを生成してください：

<div class="risk-scenarios-section">
  <div class="risk-scenarios-title">⚠️ リスクシナリオ（テーマ名）</div>
  <ol class="risk-list">
    <li><strong>リスク1のタイトル</strong>: 説明文（2-3文）</li>
    <li><strong>リスク2のタイトル</strong>: 説明文（2-3文）</li>
    <li><strong>リスク3のタイトル</strong>: 説明文（2-3文）</li>
  </ol>
</div>

テーマごとにこのブロックを繰り返してください。HTMLフラグメントのみ出力してください。
"""

# ── サプライチェーン分析 ──

SUPPLY_CHAIN_PROMPT = """
以下のテーマのTop3銘柄について、各銘柄の上流（仕入先・部品提供企業）2社と下流（顧客・取引先）2社を洗い出してください。
ティッカーが明確に分かる場合のみ証券コードも添えてください。

## 生成対象
{{SUPPLY_CHAIN_SECTION}} 部分に挿入するHTMLフラグメントです。

## テーマデータ
{themes_json}

## 銘柄データ（Top3を対象）
{stock_data_json}

## 出力形式
各テーマについて以下の構造のHTMLを生成してください：

<section class="tp-section">
  <div class="tp-section__head"><div class="tp-kicker">サプライチェーン分析</div></div>
  <table class="tp-supply-table">
    <thead><tr><th>銘柄</th><th>上流</th><th>下流</th></tr></thead>
    <tbody>
      <tr>
        <td>銘柄名<div class="name-sub" style="font-family:var(--mono);font-size:9.5px;color:var(--text-mute);margin-top:1px;">証券コード</div></td>
        <td><span class="rel">direct</span>企業名<br></td>
        <td><span class="rel">indirect</span>企業名<br></td>
      </tr>
    </tbody>
  </table>
</section>

テーマごとにこのブロックを繰り返してください。HTMLフラグメントのみ出力してください。
"""

# ── 銘柄比較表（AI生成） ──

STOCK_COMPARISON_PROMPT = """
以下のテーマの上位5銘柄を「成長性 / 割安度 / 安定性」の3軸で5段階評価した比較表を生成してください。

## 生成対象
{{STOCK_COMPARISON_SECTION}} 部分に挿入するHTMLフラグメントです。

## 評価軸の定義
- 成長性（1-5）: 売上・利益の成長ポテンシャル（5=高成長期待）
- 割安度（1-5）: PER・PBR等の観点からの割安感（5=非常に割安）
- 安定性（1-5）: 財務健全性・配当安定性・ビジネスモデルの堅牢さ（5=非常に安定）

## テーマデータ
{themes_json}

## 銘柄データ（上位5銘柄を対象）
{stock_data_json}

## 出力形式
各テーマについて以下の構造のHTMLを生成してください：

<section class="tp-section">
  <div class="tp-section__head"><div class="tp-kicker">銘柄比較 · Top 5（テーマ名）</div></div>
  <table class="tp-stars-table">
    <thead><tr><th>銘柄</th><th>成長</th><th>安定</th><th>割安</th></tr></thead>
    <tbody>
      <tr>
        <td>銘柄名<div class="name-sub">証券コード.JP</div></td>
        <td class="stars">★★★★☆</td>
        <td class="stars">★★★☆☆</td>
        <td class="stars">★★★★☆</td>
      </tr>
    </tbody>
  </table>
</section>

★の数はスコアと一致させ、残りを☆で埋めてください（例：スコア3なら★★★☆☆）。
テーマごとにこのブロックを繰り返してください。HTMLフラグメントのみ出力してください。
"""

# ── 銘柄ランキング ──

RANKING_SECTIONS_PROMPT = """
以下のデータをもとに、各テーマの銘柄ランキングセクションHTMLを生成してください。

## 生成対象
{{THEME_RANKING_SECTIONS}} 部分に挿入するHTMLフラグメントです。
article.tp-theme 要素群のみを出力してください。

## 5軸スコアリングの重み付け（各銘柄に適用してランキングを決定）
- テーマ直結度: 30%（テーマのコア事業か間接的か）
- 業績モメンタム: 25%（直近決算の売上・利益成長）
- バリュエーション: 20%（PER・PBRが業界水準比）
- 財務健全性: 15%（financial_health スコア）
- カタリスト期待: 10%（今後のイベント・ニュースによる上昇余地）

各スコアは 0〜100 の数値で表現し、data-width 属性に設定してください。
tp-score-bar__fill のクラス: 値>=70 は tp-score-bar__fill--hi, >=50 は tp-score-bar__fill--mid, それ以外はクラスなし。

## ティア分類（.tp-stock-row__sub の <span class="tier"> 内に入れるテキスト）
- 本命: 総合スコア最上位
- 大型安定: 大型株でリスク低め
- ハイリスク: 成長性高いが高リスク
- 思惑: 話題性先行
- 出遅れ: 割安な出遅れ株

## テーマデータ
{themes_json}

## 銘柄データ
stock_dataの各テーマには "failed_codes" フィールドがあり、yfinanceでデータ取得できなかった銘柄コードが含まれます。
これらの銘柄はランキングに含めず、テーマ末尾に注釈を追加してください（failed_codesが空の場合は不要）。

{stock_data_json}

## 出力フォーマット例（1テーマ分）
<article class="tp-theme">
  <div class="tp-theme__head">
    <div class="tp-theme__num">01</div>
    <div class="tp-theme__title-block">
      <h2 class="tp-theme__title">防衛・宇宙テック</h2>
      <div class="tp-theme__angle">投資戦略の着眼点（1-2行）</div>
    </div>
  </div>
  <div class="tp-theme__chips">
    <span class="tp-chip">政策: 8</span>
    <span class="tp-chip">市場: 7</span>
    <span class="tp-chip">新規: 9</span>
    <span class="tp-chip">持続: 6</span>
  </div>
  <div class="tp-theme__stocks">
    <div class="tp-stocks-head"><span>#</span><span>銘柄</span><span>CHART</span><span>VALUE</span><span>1D</span></div>
    <div class="tp-stock-row tp-stock-row--top">
      <div class="tp-stock-row__rank">1</div>
      <div>
        <div class="tp-stock-row__name">IHI</div>
        <div class="tp-stock-row__sub">7013.JP<span class="tier">本命</span></div>
      </div>
      <svg width="42" height="18" viewBox="0 0 42 18"><path d="M0,9 L42,9" fill="none" stroke="#7dc679" stroke-width="1.2"/></svg>
      <div class="tp-stock-row__price">¥8,430</div>
      <div class="tp-stock-row__change tp-up">+2.3%</div>
    </div>
    <div class="tp-score-bars">
      <div class="tp-score-bar">
        <div class="tp-score-bar__label">テーマ直結度</div>
        <div class="tp-score-bar__track"><div class="tp-score-bar__fill tp-score-bar__fill--hi" data-width="85" style="width:0%;"></div></div>
        <div class="tp-score-bar__value tp-score-bar__value--hi">85</div>
      </div>
      <div class="tp-score-bar">
        <div class="tp-score-bar__label">業績モメンタム</div>
        <div class="tp-score-bar__track"><div class="tp-score-bar__fill tp-score-bar__fill--hi" data-width="70" style="width:0%;"></div></div>
        <div class="tp-score-bar__value tp-score-bar__value--hi">70</div>
      </div>
      <div class="tp-score-bar">
        <div class="tp-score-bar__label">バリュエーション</div>
        <div class="tp-score-bar__track"><div class="tp-score-bar__fill tp-score-bar__fill--mid" data-width="60" style="width:0%;"></div></div>
        <div class="tp-score-bar__value">60</div>
      </div>
      <div class="tp-score-bar">
        <div class="tp-score-bar__label">財務健全性</div>
        <div class="tp-score-bar__track"><div class="tp-score-bar__fill tp-score-bar__fill--hi" data-width="80" style="width:0%;"></div></div>
        <div class="tp-score-bar__value tp-score-bar__value--hi">80</div>
      </div>
      <div class="tp-score-bar">
        <div class="tp-score-bar__label">カタリスト期待</div>
        <div class="tp-score-bar__track"><div class="tp-score-bar__fill tp-score-bar__fill--hi" data-width="75" style="width:0%;"></div></div>
        <div class="tp-score-bar__value tp-score-bar__value--hi">75</div>
      </div>
    </div>
    （銘柄数分繰り返す: 2位以降は tp-stock-row--top なし）
  </div>
  <div class="tp-theme__risk">
    <div class="tp-callout tp-callout--risk">
      <div>
        <div class="tp-callout__title">失速シナリオ</div>
        <div class="tp-callout__body">3つの失速条件を箇条書きで記述</div>
      </div>
    </div>
  </div>
</article>
（テーマ数分繰り返す）

## 重要な出力ルール
- 価格表示: JP は「¥8,430」、US は「$185.50」形式
- 変化率: 上昇は tp-up クラス、下降は tp-down クラス
- 最初の銘柄行のみ tp-stock-row--top を付与
- テーマ番号は2桁ゼロパディング（例: 01, 02）
- failed_codesは最後に <p style="font-size:12px;color:var(--text-mute);margin-top:8px">※ データ未取得: コード（yfinance取得エラー）</p>

HTMLフラグメントのみ出力してください。
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


def build_risk_scenarios_prompt(themes: List[Dict]) -> str:
    """リスクシナリオセクション用プロンプトを構築する"""
    return RISK_SCENARIOS_PROMPT.replace(
        "{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2)
    )


def build_supply_chain_prompt(themes: List[Dict], stock_data: List[Dict]) -> str:
    """サプライチェーン分析セクション用プロンプトを構築する"""
    # Top3銘柄のみ渡してトークン節約
    trimmed = []
    for td in stock_data:
        trimmed.append({
            "theme_name": td.get("theme_name", ""),
            "stocks": td.get("stocks", [])[:3],
        })
    return (SUPPLY_CHAIN_PROMPT
            .replace("{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2))
            .replace("{stock_data_json}", json.dumps(trimmed, ensure_ascii=False, indent=2)))


def build_stock_comparison_prompt(themes: List[Dict], stock_data: List[Dict]) -> str:
    """銘柄比較表セクション用プロンプトを構築する"""
    # Top5銘柄のみ渡してトークン節約
    trimmed = []
    for td in stock_data:
        trimmed.append({
            "theme_name": td.get("theme_name", ""),
            "stocks": td.get("stocks", [])[:5],
        })
    return (STOCK_COMPARISON_PROMPT
            .replace("{themes_json}", json.dumps(themes, ensure_ascii=False, indent=2))
            .replace("{stock_data_json}", json.dumps(trimmed, ensure_ascii=False, indent=2)))


def make_spark_svg(values, w=42, h=18, color="#7dc679"):
    """終値リストからインラインSVGスパークラインを生成する"""
    if not values or len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = w / (len(values) - 1)
    pts = [(i * step, h - (v - mn) / rng * h) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><path d="{path}" fill="none" stroke="{color}" stroke-width="1.2"/></svg>'


def build_cover_section(api_key: str, themes: List[Dict], stock_count: int) -> str:
    """Claude Sonnet 4.6 でカバーヘッドラインを生成して <section class="tp-cover"> を返す"""
    theme_count = len(themes)
    theme_names = "、".join(t.get("name", "") for t in themes[:3])

    try:
        import anthropic
        import time
        from src.utils.cost_logger import log_api_call
        client = anthropic.Anthropic(api_key=api_key)
        t0 = time.monotonic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system="あなたは金融メディアのコピーライターです。短く印象的な日本語コピーを生成してください。説明なしでJSONのみ出力してください。",
            messages=[{
                "role": "user",
                "content": f"""今月の株式テーマ「{theme_names}」などを踏まえ、
月次株式レポートのカバーヘッドラインを3パーツで生成してください。
形式は以下のJSONのみ出力:
{{"pre": "前置き（5-8文字）", "focus": "強調語（2-5文字の投資テーマキーワード）", "post": "後置き（5-10文字）"}}
pre + focus + post で1つの文になるように。絵文字禁止。"""
            }]
        )
        duration = time.monotonic() - t0
        log_api_call(
            provider="anthropic", model="claude-sonnet-4-6",
            operation="cover_headline",
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            duration_sec=duration, success=True,
        )
        import json as _json
        data = _json.loads(msg.content[0].text.strip())
        pre = html.escape(data.get("pre", "今月の注目"))
        focus = html.escape(data.get("focus", "テーマ"))
        post = html.escape(data.get("post", "銘柄を解説"))
    except Exception as e:
        logger.warning(f"Cover headline generation failed: {e}. Using fallback.")
        pre = "今月の注目"
        focus = "テーマ株"
        post = "を徹底解説"

    return (
        f'<section class="tp-cover">\n'
        f'  <div class="tp-cover__kicker">注目テーマ {theme_count}件 · 銘柄 {stock_count}件</div>\n'
        f'  <h1 class="tp-cover__title">{pre}と<br>'
        f'<span class="tp-italic-gold">{focus}</span>の<br>{post}</h1>\n'
        f'</section>\n'
    )


def build_kpi_strip_section(themes: List[Dict], stock_data: List[Dict], perf_data: List[Dict]) -> str:
    """5項目KPIストリップHTMLを生成する"""
    theme_count = len(themes)
    stock_count = sum(len(t.get("stocks", [])) for t in stock_data)

    if perf_data:
        changes = [s["change_pct"] for s in perf_data if s.get("change_pct") is not None]
        avg_ret = round(sum(changes) / len(changes), 1) if changes else 0.0
        win_rate = round(sum(1 for c in changes if c >= 0) / len(changes) * 100) if changes else 0
        prev_avg = avg_ret  # use same as prev month comparison placeholder
    else:
        avg_ret = 0.0
        win_rate = 0
        prev_avg = 0.0

    avg_cls = "tp-stat-cell__value--green" if avg_ret >= 0 else "tp-stat-cell__value--red"
    avg_sign = "+" if avg_ret >= 0 else ""
    prev_cls = "tp-stat-cell__value--green" if prev_avg >= 0 else "tp-stat-cell__value--red"
    prev_sign = "+" if prev_avg >= 0 else ""

    return (
        f'<section class="tp-section--tight">\n'
        f'  <div class="tp-stat-strip tp-stat-strip--5">\n'
        f'    <div class="tp-stat-cell"><div class="tp-stat-cell__label">テーマ</div>'
        f'<div class="tp-stat-cell__value">{theme_count}</div></div>\n'
        f'    <div class="tp-stat-cell"><div class="tp-stat-cell__label">銘柄</div>'
        f'<div class="tp-stat-cell__value">{stock_count}</div></div>\n'
        f'    <div class="tp-stat-cell"><div class="tp-stat-cell__label">1M Avg</div>'
        f'<div class="tp-stat-cell__value {avg_cls}">{avg_sign}{avg_ret}%</div></div>\n'
        f'    <div class="tp-stat-cell"><div class="tp-stat-cell__label">勝率</div>'
        f'<div class="tp-stat-cell__value">{win_rate}%</div></div>\n'
        f'    <div class="tp-stat-cell"><div class="tp-stat-cell__label">前月比</div>'
        f'<div class="tp-stat-cell__value {prev_cls}">{prev_sign}{prev_avg}%</div></div>\n'
        f'  </div>\n'
        f'</section>\n'
    )


def build_chat_widget_section(proxy_url: str) -> str:
    """チャットウィジェット全体HTML（CHAT_PROXY_URL が未設定なら空文字）"""
    if not proxy_url:
        return ""
    safe_proxy = html.escape(proxy_url)
    return f"""<style>
#tp-chat-fab{{position:fixed;bottom:24px;right:24px;width:48px;height:48px;border-radius:50%;background:var(--gold);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;z-index:1000;box-shadow:0 4px 16px rgba(0,0,0,.4)}}
#tp-chat-panel{{display:none;position:fixed;bottom:84px;right:24px;width:320px;max-height:480px;background:var(--surface);border:1px solid var(--border-gold);z-index:999;flex-direction:column}}
#tp-chat-panel.is-open{{display:flex}}
#tp-chat-msgs{{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}}
#tp-chat-input-row{{display:flex;border-top:1px solid var(--border);padding:8px}}
#tp-chat-input{{flex:1;background:transparent;border:none;color:var(--text);font-family:var(--sans);font-size:13px;outline:none}}
#tp-chat-send{{background:var(--gold);border:none;color:#000;padding:4px 12px;cursor:pointer;font-family:var(--mono);font-size:11px}}
.tp-chat-msg{{font-size:13px;line-height:1.5;padding:8px 10px;border-radius:2px}}
.tp-chat-msg--user{{background:rgba(212,163,65,.12);align-self:flex-end;max-width:80%}}
.tp-chat-msg--ai{{background:rgba(255,255,255,.04);align-self:flex-start;max-width:90%}}
</style>
<button id="tp-chat-fab" aria-label="チャット">
<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0d1626" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
</button>
<div id="tp-chat-panel">
<div style="padding:10px 12px;border-bottom:1px solid var(--border);font-family:var(--serif);font-size:13px;color:var(--gold)">The Picker · AI</div>
<div id="tp-chat-msgs"></div>
<div id="tp-chat-input-row">
<input id="tp-chat-input" type="text" placeholder="レポートについて質問...">
<button id="tp-chat-send">送信</button>
</div>
</div>
<script>
(function(){{
var fab=document.getElementById('tp-chat-fab');
var panel=document.getElementById('tp-chat-panel');
var msgs=document.getElementById('tp-chat-msgs');
var input=document.getElementById('tp-chat-input');
var send=document.getElementById('tp-chat-send');
fab.addEventListener('click',function(){{panel.classList.toggle('is-open');}});
function addMsg(text,role){{var d=document.createElement('div');d.className='tp-chat-msg tp-chat-msg--'+role;d.textContent=text;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;}}
async function doSend(){{
var q=input.value.trim();if(!q)return;
input.value='';addMsg(q,'user');
try{{
var r=await fetch('{safe_proxy}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reportContext:document.title,message:q}})}});
var j=await r.json();
addMsg(j.reply||'エラーが発生しました','ai');
}}catch(e){{addMsg('接続エラー','ai');}}
}}
send.addEventListener('click',doSend);
input.addEventListener('keydown',function(e){{if(e.key==='Enter')doSend();}});
}})();
</script>"""


def build_sector_warning_html(themes_data: Dict) -> str:
    if not themes_data.get("sector_overlap_warning", False):
        return ""
    dominant = themes_data.get("dominant_sectors", [])
    if not dominant:
        return ""
    sectors_text = "、".join(html.escape(s) for s in dominant)
    return (
        '<section class="tp-section--tight">\n'
        '  <div class="tp-callout tp-callout--warn">\n'
        '    <svg class="tp-callout__icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
        '<path d="M10.3 3.8L2.8 18a2 2 0 001.7 3h15a2 2 0 001.7-3L13.7 3.8a2 2 0 00-3.4 0z"/></svg>\n'
        '    <div class="tp-callout__body">\n'
        f'      <strong style="color:var(--gold)">セクター分散注意:</strong> {sectors_text} セクターが偏重しています。分散投資の観点から他の業種との組み合わせをお勧めします。\n'
        '    </div>\n'
        '  </div>\n'
        '</section>\n'
    )


def build_chart_init_script(stock_data: List[Dict]) -> str:
    """
    各銘柄の price_history_6m を使って Chart.js 初期化スクリプトを生成する。
    IntersectionObserver で canvas が表示領域に入った時だけ描画する（遅延読み込み）。
    JSON データ内の </script> 対策として < を < にエスケープする。
    """
    chart_configs = []
    for theme_data in stock_data:
        for stock in theme_data.get("stocks", []):
            history = stock.get("price_history_6m")
            if not history:
                continue
            code = stock.get("code", "")
            if not code:
                continue
            entry = {
                "dates": history.get("dates", []),
                "closes": history.get("closes", []),
                "currency": stock.get("currency", "JPY"),
            }
            safe_json = json.dumps(entry, ensure_ascii=True).replace("<", "\\u003c")
            chart_configs.append(f'  chartData[{json.dumps(code)}] = {safe_json};')

    if not chart_configs:
        return ""

    configs_js = "\n".join(chart_configs)

    return f"""<script>
(function() {{
  var chartData = {{}};
{configs_js}

  var rendered = {{}};

  var chartObserver = new IntersectionObserver(function(entries) {{
    entries.forEach(function(entry) {{
      if (!entry.isIntersecting) return;
      var canvas = entry.target;
      var code = canvas.getAttribute('data-stock-code');
      if (rendered[code] || !chartData[code]) return;
      rendered[code] = true;
      chartObserver.unobserve(canvas);

      var data = chartData[code];
      new Chart(canvas, {{
        type: 'line',
        data: {{
          labels: data.dates,
          datasets: [{{
            data: data.closes,
            borderColor: '#7dc679',
            backgroundColor: 'rgba(125,198,121,0.12)',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.3,
            fill: true
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              callbacks: {{
                title: function(items) {{ return items[0].label; }},
                label: function(item) {{ var d = chartData[code]; var sym = d && d.currency === 'USD' ? '$' : '¥'; return sym + item.parsed.y.toLocaleString(); }}
              }},
              backgroundColor: '#0d1626',
              titleColor: '#9aa3b8',
              bodyColor: '#e0e0f0',
              borderColor: '#d4a341',
              borderWidth: 1
            }}
          }},
          scales: {{
            x: {{
              ticks: {{ color: '#9aa3b8', maxTicksLimit: 6, maxRotation: 0 }},
              grid: {{ color: 'rgba(255,255,255,0.05)' }}
            }},
            y: {{
              ticks: {{ color: '#9aa3b8', maxTicksLimit: 5 }},
              grid: {{ color: 'rgba(255,255,255,0.05)' }}
            }}
          }}
        }}
      }});
    }});
  }}, {{ threshold: 0.1 }});

  document.querySelectorAll('canvas.price-chart-canvas').forEach(function(canvas) {{
    chartObserver.observe(canvas);
  }});
}})();
</script>"""


def strip_code_fence(text: str) -> str:
    """Claudeが出力したコードフェンス（```html ... ```）を除去する"""
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[start:end])
    return text


def ensure_chart_canvas_in_ranking_html(ranking_html: str, stock_data: List[Dict]) -> str:
    """Claudeがcanvasを出力し忘れた銘柄について、Python側で補完する。

    各銘柄について `data-stock-code="{code}"` を持つ canvas が既に存在すれば何もしない。
    存在しなければ、当該銘柄の `.card-detail` 閉じタグ直前に canvas ブロックを挿入する。
    `.card-detail` の対応は stock_data の銘柄順序と `.card-detail` の出現順で行う。

    Strategy:
      1. stock_data から全銘柄コードのリストを順番に構築する。
      2. HTMLの `.card-detail` 出現位置と銘柄コードをインデックスで対応付ける。
      3. canvas が既にある銘柄はスキップ、ない銘柄は `.card-detail` 閉じタグ直前に挿入する。
    """
    all_codes: List[str] = []
    for theme_data in stock_data:
        for stock in theme_data.get("stocks", []):
            code = stock.get("code", "")
            if code:
                all_codes.append(code)

    if not all_codes:
        return ranking_html

    canvas_block_template = (
        '\n        <div class="price-chart-wrapper">\n'
        '          <div class="price-chart-label">過去6ヶ月の株価推移</div>\n'
        '          <canvas class="price-chart-canvas" data-stock-code="{code}"></canvas>\n'
        '        </div>\n      '
    )

    # .card-detail の閉じタグを順番に見つけ、コードと対応付けて処理する
    # card-detail の閉じタグパターン: </div> の直前に挿入（card-detail ブロックの末尾）
    # card-detail ブロックの末尾は score-bar-section か detail-text の後にある </div>
    # 実際には card-detail の </div> を card_detail_idx 番目として扱う

    # canvas タグが既にあるコードのセットを把握
    existing_codes = set(re.findall(
        r'<canvas[^>]+class="price-chart-canvas"[^>]+data-stock-code="([^"]+)"',
        ranking_html
    ))
    existing_codes |= set(re.findall(
        r'<canvas[^>]+data-stock-code="([^"]+)"[^>]+class="price-chart-canvas"',
        ranking_html
    ))

    codes_needing_canvas = [c for c in all_codes if c not in existing_codes]
    if not codes_needing_canvas:
        return ranking_html

    # card-detail の出現順に対応するコードを決定する
    # card-detail の出現回数を数えて、その中で canvas がないものをコードリストと対応付ける
    card_detail_open_pattern = re.compile(r'<div\s+class="card-detail"')
    card_detail_opens = list(card_detail_open_pattern.finditer(ranking_html))

    # 各 card-detail ブロックが対応する銘柄コードを、stock_data の順序で割り当てる
    # card_detail_opens の数と all_codes の数は一致するはず
    if len(card_detail_opens) != len(all_codes):
        logger.warning(
            f"ensure_chart_canvas: card-detail count ({len(card_detail_opens)}) "
            f"!= stock count ({len(all_codes)}). Skipping fallback injection."
        )
        return ranking_html

    # 各 card-detail に対応するコードを特定し、canvas がないものに挿入する
    # 後ろから処理することでオフセットのずれを防ぐ
    result = ranking_html
    offset = 0  # 前の挿入による文字数増加の累積

    for idx, match in enumerate(card_detail_opens):
        code = all_codes[idx]
        if code in existing_codes:
            continue

        # この card-detail ブロックの閉じ </div> を探す（ネストを考慮してカウント）
        search_start = match.start() + offset
        depth = 0
        pos = search_start
        adjusted_html = result
        i = pos
        close_pos = -1
        while i < len(adjusted_html):
            if adjusted_html[i:i+4] == '<div':
                depth += 1
                i += 4
            elif adjusted_html[i:i+6] == '</div>':
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
                i += 6
            else:
                i += 1

        if close_pos == -1:
            logger.warning(f"ensure_chart_canvas: Could not find closing </div> for code {code}")
            continue

        canvas_html = canvas_block_template.format(code=code)
        result = result[:close_pos] + canvas_html + result[close_pos:]
        offset += len(canvas_html)
        existing_codes.add(code)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 前月推奨銘柄のパフォーマンス追跡
# ─────────────────────────────────────────────────────────────────────────────

def fetch_performance(previous_summary: Dict) -> List[Dict]:
    """前月推奨銘柄の現在株価をまとめて取得してパフォーマンスを計算する"""
    top_stocks = previous_summary.get("top_stocks", [])
    eligible = [s for s in top_stocks if s.get("code") and s.get("price_at_report") is not None]
    if not eligible:
        return []

    code_entries = [
        {"code": s["code"], "market": s.get("market", "JP")}
        for s in eligible
    ]
    stock_map = fetch_multiple(code_entries, min_success=0)

    results = []
    for stock in eligible:
        code = stock["code"]
        data = stock_map.get(code)
        if data and data.get("current_price"):
            current_price = data["current_price"]
            price_at_report = stock["price_at_report"]
            change_pct = (current_price - price_at_report) / price_at_report * 100
            results.append({
                "code": code,
                "market": stock.get("market", "JP"),
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

    changes = [s["change_pct"] for s in perf_data if s.get("change_pct") is not None]
    if not changes:
        return ""
    avg = round(sum(changes) / len(changes), 1)
    win_rate = round(sum(1 for c in changes if c >= 0) / len(changes) * 100)
    best = round(max(changes), 1)
    worst = round(min(changes), 1)
    avg_cls = "var(--green)" if avg >= 0 else "var(--red)"
    avg_sign = "+" if avg >= 0 else ""
    best_sign = "+" if best >= 0 else ""

    return (
        f'<section class="tp-section">\n'
        f'  <div class="tp-section__head"><div class="tp-kicker">前月の成績</div></div>\n'
        f'  <div style="padding:12px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);">\n'
        f'    <div style="display:flex;justify-content:space-between;align-items:baseline;">\n'
        f'      <span style="font-size:11.5px;color:var(--text-dim);">{html.escape(prev_year_month)}号 推奨銘柄 平均</span>\n'
        f'      <span style="font-family:var(--mono);font-size:20px;font-weight:700;color:{avg_cls};">{avg_sign}{avg}%</span>\n'
        f'    </div>\n'
        f'    <div style="display:flex;justify-content:space-between;margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--text-dim);">\n'
        f'      <span>勝率 <span style="color:var(--text);font-weight:600;">{win_rate}%</span></span>\n'
        f'      <span>最高 <span style="color:var(--green);font-weight:600;">{best_sign}{best}%</span></span>\n'
        f'      <span>最低 <span style="color:var(--red);font-weight:600;">{worst}%</span></span>\n'
        f'      <span>{len(perf_data)}銘柄</span>\n'
        f'    </div>\n'
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
                "market": s.get("market", "JP"),
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

    atomic_write_json(str(REPORT_SUMMARY_FILE), summary)
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

    atomic_write_json(str(THEME_HISTORY_FILE), history)
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

    # Phase 1A 連携: sector_overlap_warning / dominant_sectors（フィールドがなければ false 扱い）

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

    # Claudeへのプロンプトを5件のバッチリクエストとして送信
    # summary_cards と risk_scenarios は新テンプレートでは不要（空文字で代替）
    news_strategy_prompt = build_news_strategy_prompt(news_overview, themes)
    changes_prompt = build_changes_prompt(themes, stock_data, previous_summary)
    ranking_prompt = build_ranking_sections_prompt(themes, stock_data)
    supply_chain_prompt = build_supply_chain_prompt(themes, stock_data)
    stock_comparison_prompt = build_stock_comparison_prompt(themes, stock_data)

    batch_requests = [
        {"custom_id": "news_strategy", "user_message": news_strategy_prompt},
        {"custom_id": "changes", "user_message": changes_prompt},
        {"custom_id": "ranking_sections", "user_message": ranking_prompt},
        {"custom_id": "supply_chain", "user_message": supply_chain_prompt},
        {"custom_id": "stock_comparison", "user_message": stock_comparison_prompt},
    ]

    for req in batch_requests:
        logger.info(f"{req['custom_id']} prompt: {len(req['user_message'])} chars")

    claude = ClaudeBatchClient(api_key=api_key)

    # バッチ送信と並行してパフォーマンスデータを取得（Claudeの待機時間を有効活用）
    with ThreadPoolExecutor(max_workers=1) as perf_exec:
        perf_future = (
            perf_exec.submit(fetch_performance, previous_summary)
            if previous_summary else None
        )
        results = claude.run_batch(
            requests=batch_requests,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=8000,
        )
        perf_data = perf_future.result() if perf_future else []

    # Claude結果を取得・コードフェンス除去
    news_strategy_html = strip_code_fence(results.get("news_strategy", "").strip())
    changes_html = strip_code_fence(results.get("changes", "").strip())
    # summary_cards と risk_scenarios は新テンプレートでは空文字（テーマ記事内に埋め込み済み）
    summary_cards_html = ""
    risk_scenarios_html = ""
    ranking_sections_html = strip_code_fence(results.get("ranking_sections", "").strip())
    supply_chain_html = strip_code_fence(results.get("supply_chain", "").strip())
    stock_comparison_html = strip_code_fence(results.get("stock_comparison", "").strip())

    for name, content in [
        ("news_strategy", news_strategy_html),
        ("changes", changes_html),
        ("ranking_sections", ranking_sections_html),
        ("supply_chain", supply_chain_html),
        ("stock_comparison", stock_comparison_html),
    ]:
        logger.info(f"{name} response: {len(content)} chars")
        if not content:
            logger.warning(f"{name} response is empty!")

    # ソースリンクHTML（Python生成）
    source_links_html = build_source_links_html(themes, articles)

    # パフォーマンスHTML生成（バッチ並行取得済み）
    performance_html = ""
    if perf_data:
        performance_html = build_performance_html(
            perf_data, previous_summary.get("year_month", "前月")
        )
        logger.info(f"Performance data: {len(perf_data)} stocks")

    # セクター分散警告HTML（Python生成）
    sector_warning_html = build_sector_warning_html(themes_data)
    if sector_warning_html:
        logger.info("Sector overlap warning banner will be shown")

    # Chart.js 初期化スクリプト（Python生成）
    chart_init_script = build_chart_init_script(stock_data)
    logger.info(f"Chart init script: {len(chart_init_script)} chars for stock charts")

    # テンプレートの全プレースホルダーをPythonで置換
    year_month_label = f"{now.year}年{now.month}月"
    generated_date_label = now.strftime("%Y年%m月%d日")
    total_stocks = sum(len(t.get("stocks", [])) for t in stock_data)
    archive_link_html = '<a href="archive/index.html">アーカイブ一覧</a>'
    ai_models_text = f"{GEMINI_MODEL} / {CLAUDE_MODEL}"

    # 新テンプレート用セクションをPythonで生成
    cover_section = build_cover_section(api_key, themes, total_stocks)
    kpi_strip_section = build_kpi_strip_section(themes, stock_data, perf_data)
    chat_widget_section = build_chat_widget_section(os.environ.get("CHAT_PROXY_URL", ""))

    replacements = {
        "{{YEAR_MONTH}}": year_month_label,
        "{{GENERATED_DATE}}": generated_date_label,
        "{{ARCHIVE_LINKS}}": archive_link_html,
        "{{AI_MODELS_TEXT}}": ai_models_text,
        "{{COVER_SECTION}}": cover_section,
        "{{KPI_STRIP_SECTION}}": kpi_strip_section,
        "{{NEWS_STRATEGY_SECTION}}": news_strategy_html,
        "{{THEME_SUMMARY_CARDS}}": summary_cards_html,
        "{{CHANGES_SECTION}}": changes_html,
        "{{PERFORMANCE_SECTION}}": performance_html,
        "{{THEME_RANKING_SECTIONS}}": ranking_sections_html,
        "{{SOURCE_LINKS_SECTION}}": source_links_html,
        "{{SECTOR_WARNING_BANNER}}": sector_warning_html,
        "{{RISK_SCENARIOS_SECTION}}": risk_scenarios_html,
        "{{SUPPLY_CHAIN_SECTION}}": supply_chain_html,
        "{{STOCK_COMPARISON_SECTION}}": stock_comparison_html,
        "{{CHART_INIT_SCRIPT}}": chart_init_script,
        "{{CHAT_WIDGET_SECTION}}": chat_widget_section,
    }
    html = template_html
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    # 保存
    save_report(html, year_month_str)

    # テーマ履歴更新
    update_theme_history(themes, year_month_str)

    # 来月の比較用にレポートサマリーを保存
    save_report_summary(themes, stock_data, year_month_str)

    logger.info("Step 2 complete.")


if __name__ == "__main__":
    run()
