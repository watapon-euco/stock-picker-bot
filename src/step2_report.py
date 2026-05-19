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
ニュース概況（.news-overview-box 内にまとめ）:
   - 先月の株式・経済の主要トピック5〜8個を段落形式で要約
   - 市場全体のトレンドや注目イベントを含む

## ニュース全体の概況
{news_overview}

## テーマデータ（参考情報）
{themes_json}

## 出力フォーマット例
<div class="news-overview-box">
  <p>先月の日本株式市場は...（5〜8行の概況）</p>
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

<div class="supply-chain-section">
  <div class="supply-chain-title">🔗 サプライチェーン（テーマ名）</div>
  <table class="supply-chain-table">
    <thead>
      <tr><th>銘柄</th><th>上流（仕入先・部品提供）</th><th>下流（顧客・取引先）</th></tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>銘柄名</strong><br><small>証券コード</small></td>
        <td>
          <ul>
            <li>企業名（証券コードがある場合: コード）</li>
            <li>企業名（証券コードがある場合: コード）</li>
          </ul>
        </td>
        <td>
          <ul>
            <li>企業名（証券コードがある場合: コード）</li>
            <li>企業名（証券コードがある場合: コード）</li>
          </ul>
        </td>
      </tr>
    </tbody>
  </table>
</div>

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
各テーマについて以下のJSON構造を含むHTMLを生成してください（スクリプトタグ不要、テーブル形式で出力）：

<div class="stock-comparison-section">
  <div class="stock-comparison-title">📊 銘柄比較表（テーマ名）</div>
  <div style="overflow-x:auto">
  <table class="stock-comparison-table">
    <thead>
      <tr>
        <th>銘柄</th>
        <th>成長性</th>
        <th>割安度</th>
        <th>安定性</th>
        <th>一言コメント</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>銘柄名</strong><br><small>証券コード</small></td>
        <td class="score-cell" data-score="4">★★★★☆</td>
        <td class="score-cell" data-score="3">★★★☆☆</td>
        <td class="score-cell" data-score="5">★★★★★</td>
        <td>一言コメント（20字以内）</td>
      </tr>
    </tbody>
  </table>
  </div>
</div>

★の数はスコアと一致させ、残りを☆で埋めてください（例：スコア3なら★★★☆☆）。
テーマごとにこのブロックを繰り返してください。HTMLフラグメントのみ出力してください。
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
    <div class="stock-card rank-1" data-stock-code="7013">
      <div class="card-header">
        <div class="rank-badge">1</div>
        <span class="tier-label tier-honmei">本命</span>
        <div class="card-info">
          <div class="stock-name">IHI <span class="market-badge market-badge-jp">JP</span></div>
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
        <div class="price-chart-wrapper">
          <div class="price-chart-label">過去6ヶ月の株価推移</div>
          <canvas class="price-chart-canvas" data-stock-code="7013"></canvas>
        </div>
      </div>
    </div>
    （銘柄数分繰り返す）
  </div>
</section>
（テーマ数分繰り返す）

## 重要な出力ルール
- 各銘柄の market フィールドに応じて市場バッジを表示してください:
  - JP 銘柄: `<span class="market-badge market-badge-jp">JP</span>`
  - US 銘柄: `<span class="market-badge market-badge-us">US</span>`
  バッジは `.stock-name` の直後に配置してください。
- 価格表示は市場に合わせて: JP は「¥8,430」、US は「$185.50」（小数2桁）の形式にしてください。
- 各 `.stock-card` 要素には必ず `data-stock-code="証券コード"` 属性を付与してください（例: `<div class="stock-card rank-1" data-stock-code="7013">`）。
- 各 `.card-detail` の末尾（閉じタグ直前）に必ず以下のHTMLを含めてください:
  ```
  <div class="price-chart-wrapper">
    <div class="price-chart-label">過去6ヶ月の株価推移</div>
    <canvas class="price-chart-canvas" data-stock-code="証券コード"></canvas>
  </div>
  ```
  `data-stock-code` は当該銘柄の証券コード（4桁数字または海外ティッカー）を入れてください。

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


def build_sector_warning_html(themes_data: Dict) -> str:
    """
    sector_overlap_warning フラグが true の場合に警告バナーHTMLを生成する。
    フィールドが存在しない場合は警告なし（フォールバック: false 扱い）として空文字を返す。
    """
    if not themes_data.get("sector_overlap_warning", False):
        return ""
    dominant = themes_data.get("dominant_sectors", [])
    if not dominant:
        return ""
    sectors_text = "、".join(html.escape(s) for s in dominant)
    return (
        f'<div class="sector-warning-banner">\n'
        f'  ⚠️ 今月は <strong>{sectors_text}</strong> セクターが偏重しています。'
        f'分散投資の観点では、他の業種と組み合わせた検討をお勧めします。\n'
        f'</div>\n'
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
            borderColor: '#5588ff',
            backgroundColor: 'rgba(85,136,255,0.08)',
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
              backgroundColor: 'rgba(20,20,40,0.9)',
              titleColor: '#aaaacc',
              bodyColor: '#e0e0f0',
              borderColor: '#2a2a5a',
              borderWidth: 1
            }}
          }},
          scales: {{
            x: {{
              ticks: {{ color: '#555577', maxTicksLimit: 6, maxRotation: 0 }},
              grid: {{ color: '#1a1a36' }}
            }},
            y: {{
              ticks: {{ color: '#555577', maxTicksLimit: 5 }},
              grid: {{ color: '#1a1a36' }}
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

    rows = []
    for s in perf_data:
        chg = s["change_pct"]
        direction = "up" if chg >= 0 else "down"
        sign = "+" if chg >= 0 else ""
        currency = get_currency(s.get("market", "JP"))
        price_at_report_str = format_price(s["price_at_report"], currency)
        current_price_str = format_price(s["current_price"], currency)
        rows.append(
            f'<tr>'
            f'<td>{html.escape(s["name"])}<br><small style="color:#555577">{html.escape(s["code"])}</small></td>'
            f'<td>{html.escape(s["theme"])}</td>'
            f'<td>{price_at_report_str}</td>'
            f'<td>{current_price_str}</td>'
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

    # Claudeへのプロンプトを7件のバッチリクエストとして送信（既存4件 + 新規3件）
    news_strategy_prompt = build_news_strategy_prompt(news_overview, themes)
    changes_prompt = build_changes_prompt(themes, stock_data, previous_summary)
    summary_prompt = build_summary_cards_prompt(themes)
    ranking_prompt = build_ranking_sections_prompt(themes, stock_data)
    risk_scenarios_prompt = build_risk_scenarios_prompt(themes)
    supply_chain_prompt = build_supply_chain_prompt(themes, stock_data)
    stock_comparison_prompt = build_stock_comparison_prompt(themes, stock_data)

    batch_requests = [
        {"custom_id": "news_strategy", "user_message": news_strategy_prompt},
        {"custom_id": "changes", "user_message": changes_prompt},
        {"custom_id": "summary_cards", "user_message": summary_prompt},
        {"custom_id": "ranking_sections", "user_message": ranking_prompt},
        {"custom_id": "risk_scenarios", "user_message": risk_scenarios_prompt},
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
    summary_cards_html = strip_code_fence(results.get("summary_cards", "").strip())
    ranking_sections_html = strip_code_fence(results.get("ranking_sections", "").strip())
    ranking_sections_html = ensure_chart_canvas_in_ranking_html(ranking_sections_html, stock_data)
    risk_scenarios_html = strip_code_fence(results.get("risk_scenarios", "").strip())
    supply_chain_html = strip_code_fence(results.get("supply_chain", "").strip())
    stock_comparison_html = strip_code_fence(results.get("stock_comparison", "").strip())

    for name, content in [
        ("news_strategy", news_strategy_html),
        ("changes", changes_html),
        ("summary_cards", summary_cards_html),
        ("ranking_sections", ranking_sections_html),
        ("risk_scenarios", risk_scenarios_html),
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

    replacements = {
        "{{YEAR_MONTH}}": year_month_label,
        "{{GENERATED_DATE}}": generated_date_label,
        "{{THEME_COUNT}}": str(len(themes)),
        "{{TOTAL_STOCKS}}": str(total_stocks),
        "{{ARCHIVE_LINKS}}": archive_link_html,
        "{{AI_MODELS_TEXT}}": ai_models_text,
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
        "{{CHAT_PROXY_URL}}": os.environ.get("CHAT_PROXY_URL", ""),
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
