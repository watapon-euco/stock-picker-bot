"""
週次速報レポート生成

毎週金曜に実行し、直近1週間の急騰銘柄TOP5・注目ニュース3本を
HTML (docs/weekly/YYYY-WW.html) と LINE Flex Message で配信する。

main.py・既存の step1-6 には一切干渉しない独立した呼び出し経路。
"""
import html as _html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yfinance as yf
from dotenv import load_dotenv

from src.utils.cost_logger import log_api_call
from src.utils.gemini_client import GeminiClient
from src.utils.helpers import safe_url
from src.utils.line_client import LineClient
from src.utils.rss_fetcher import fetch_news

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DOCS_WEEKLY_DIR = Path("docs/weekly")
TEMPLATE_PATH = Path("src/templates/weekly_flash_template.html")
THEME_HISTORY_FILE = DATA_DIR / "theme_history.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"

# 急騰判定の閾値
SURGE_PCT_THRESHOLD = 5.0   # 1週間で +5% 以上
SURGE_VOL_RATIO = 2.0       # 出来高が直近平均の 2倍以上


# ─────────────────────────────────────────────────────────────────────────────
# 銘柄コード収集
# ─────────────────────────────────────────────────────────────────────────────

def _collect_codes() -> List[str]:
    """theme_history.json の直近推奨 + watchlist.json から証券コードを収集する。"""
    stocks = _collect_stocks_with_names()
    return [s["code"] for s in stocks]


def _collect_stocks_with_names() -> List[Dict]:
    """stock_data.json + watchlist.json から {code, name} リストを重複なしで返す。"""
    stocks: List[Dict] = []
    seen: set = set()

    if THEME_HISTORY_FILE.exists():
        pass  # theme_history に銘柄コードは含まれないため stock_data.json を使用

    stock_data_file = DATA_DIR / "stock_data.json"
    if stock_data_file.exists():
        try:
            with open(stock_data_file, encoding="utf-8") as f:
                sd = json.load(f)
            for theme_block in sd.get("themes", []):
                for stock in theme_block.get("stocks", []):
                    code = str(stock.get("code", "")).strip()
                    if code and code not in seen:
                        stocks.append({"code": code, "name": stock.get("name", code)})
                        seen.add(code)
        except Exception as e:
            logger.warning(f"Failed to read stock_data.json: {e}")

    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, encoding="utf-8") as f:
                wl = json.load(f)
            for stock in wl.get("stocks", []):
                code = str(stock.get("code", "")).strip()
                if code and code not in seen:
                    stocks.append({"code": code, "name": stock.get("name", code)})
                    seen.add(code)
        except Exception as e:
            logger.warning(f"Failed to read watchlist.json: {e}")

    return stocks


# ─────────────────────────────────────────────────────────────────────────────
# 週次株価変化率計算
# ─────────────────────────────────────────────────────────────────────────────

_BATCH_SIZE = 20  # backtest.py と同じチャンクサイズ


def _to_yf_ticker(code: str) -> str:
    """日本株コードに .T サフィックスを付与する。"""
    if code.endswith(".T") or code.endswith(".OS"):
        return code
    return code + ".T"


def _fetch_weekly_changes_batch(codes: List[str], name_map: Dict[str, str]) -> List[Dict]:
    """
    銘柄コードリストの1ヶ月分日足データをバッチ取得し、
    各銘柄の週次変化率・出来高比率を計算して返す。

    バッチサイズは20件（backtest.py と同様）。tk.info は呼ばない。

    Returns:
        計算成功した銘柄の Dict リスト。失敗銘柄はスキップ。
    """
    if not codes:
        return []

    ticker_to_code = {_to_yf_ticker(c): c for c in codes}
    all_tickers = list(ticker_to_code.keys())
    results: List[Dict] = []

    for chunk_start in range(0, len(all_tickers), _BATCH_SIZE):
        chunk = all_tickers[chunk_start: chunk_start + _BATCH_SIZE]
        t0 = time.monotonic()
        try:
            data = yf.download(chunk, period="1mo", group_by="ticker",
                               auto_adjust=True, progress=False)
            duration = time.monotonic() - t0
            log_api_call(
                provider="yfinance", model="", operation="weekly_change_batch",
                duration_sec=duration, success=not data.empty,
                extra={"tickers": chunk},
            )
            if data.empty:
                continue
        except Exception as e:
            log_api_call(
                provider="yfinance", model="", operation="weekly_change_batch",
                duration_sec=time.monotonic() - t0, success=False,
                extra={"tickers": chunk, "error": str(e)},
            )
            logger.warning(f"Batch fetch failed for chunk {chunk}: {e}")
            continue

        for ticker in chunk:
            code = ticker_to_code.get(ticker)
            if code is None:
                continue
            try:
                # multi-ticker: data has MultiIndex columns (field, ticker)
                if hasattr(data.columns, "levels"):
                    close = data["Close"][ticker].dropna() if ticker in data["Close"].columns else None
                    volume = data["Volume"][ticker].dropna() if ticker in data["Volume"].columns else None
                else:
                    # single-ticker fallback
                    close = data["Close"].dropna() if "Close" in data.columns else None
                    volume = data["Volume"].dropna() if "Volume" in data.columns else None

                if close is None or len(close) < 2:
                    continue

                current_price = float(close.iloc[-1])
                week_ago_idx = max(0, len(close) - 6)
                week_ago_price = float(close.iloc[week_ago_idx])
                week_change_pct = (
                    (current_price - week_ago_price) / week_ago_price * 100
                    if week_ago_price else 0.0
                )

                vol_ratio = None
                avg_volume_30d = None
                if volume is not None and len(volume) >= 10:
                    vol_5 = float(volume.iloc[-5:].mean())
                    vol_30 = float(volume.mean())
                    avg_volume_30d = round(vol_30, 0)
                    if vol_30 > 0:
                        vol_ratio = round(vol_5 / vol_30, 2)

                name = name_map.get(code, code)
                results.append({
                    "code": code,
                    "ticker": ticker,
                    "name": name,
                    "current_price": round(current_price, 2),
                    "week_change_pct": round(week_change_pct, 2),
                    "vol_ratio": vol_ratio,
                    "avg_volume_30d": avg_volume_30d,
                })
            except Exception as e:
                logger.warning(f"Failed to parse weekly data for {code}: {e}")

    return results


def is_surging(price_change_pct, vol_ratio, avg_volume_30d) -> bool:
    """急騰銘柄判定。

    +5% 単体、または 出来高2倍以上かつ平均出来高10,000株以上かつ+1%以上。
    薄商い銘柄での誤検知を防ぐため avg_volume_30d の最低基準を設ける。
    """
    if price_change_pct is not None and price_change_pct >= SURGE_PCT_THRESHOLD:
        return True
    if (vol_ratio is not None and vol_ratio >= SURGE_VOL_RATIO
            and avg_volume_30d is not None and avg_volume_30d >= 10_000
            and price_change_pct is not None and price_change_pct >= 1.0):
        return True
    return False


def _build_top5(codes: List[str], name_map: Optional[Dict[str, str]] = None) -> List[Dict]:
    """銘柄コードから急騰銘柄を抽出してスコア順に上位5件を返す。"""
    if name_map is None:
        name_map = {}
    all_data = _fetch_weekly_changes_batch(codes, name_map)
    results = [
        d for d in all_data
        if is_surging(d.get("week_change_pct"), d.get("vol_ratio"), d.get("avg_volume_30d"))
    ]

    # ソート: 変化率優先、出来高比率はタイブレーカー
    results.sort(
        key=lambda x: (
            x.get("week_change_pct", 0.0),
            x.get("vol_ratio") or 0.0,
        ),
        reverse=True,
    )
    return results[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Gemini によるニュース選出
# ─────────────────────────────────────────────────────────────────────────────

def _select_top_news(articles: List[Dict], gemini_client: GeminiClient) -> List[Dict]:
    """
    Gemini Flash-Lite（通常 API）に直近1週間のニュースから3本選ばせる。

    Returns:
        [{"title", "link", "source", "comment"}, ...] （最大3件）
    """
    if not articles:
        return []

    # プロンプトに渡す記事サマリーを作成（多すぎると遅いので先頭50件に絞る）
    article_list = []
    for i, art in enumerate(articles[:50], start=1):
        title = art.get("title", "").replace("{", "（").replace("}", "）").replace("`", "'")
        source = art.get("source", "")
        summary = art.get("summary", "")[:120].replace("{", "（").replace("}", "）").replace("`", "'")
        article_list.append(f"{i}. 【{source}】{title}\n   {summary}")

    articles_text = "\n".join(article_list)

    prompt = f"""
以下は直近1週間の日本株・経済ニュース一覧です。
投資家が最も注目すべきニュースを3本選び、JSON配列で出力してください。

## ニュース一覧
{articles_text}

## 出力形式（JSONのみ、説明不要）
[
  {{
    "index": 1,
    "comment": "このニュースが重要な理由を1〜2文で説明"
  }},
  ...
]

indexは上記リストの番号（1始まり）です。3件選んでください。
"""

    try:
        result = gemini_client.generate_json(prompt)
        selected = []
        if isinstance(result, list):
            for item in result[:3]:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                comment = str(item.get("comment", "")).strip()
                if not isinstance(idx, int) or idx < 1 or idx > len(articles):
                    continue
                art = articles[idx - 1]
                selected.append({
                    "title": art.get("title", ""),
                    "link": art.get("link", ""),
                    "source": art.get("source", ""),
                    "comment": comment,
                })
        return selected
    except Exception as e:
        logger.warning(f"Gemini news selection failed: {e}. Falling back to first 3 articles.")
        # フォールバック: 先頭3件をそのまま返す
        return [
            {
                "title": art.get("title", ""),
                "link": art.get("link", ""),
                "source": art.get("source", ""),
                "comment": "",
            }
            for art in articles[:3]
        ]


# ─────────────────────────────────────────────────────────────────────────────
# HTML レポート生成
# ─────────────────────────────────────────────────────────────────────────────

def _build_surging_stocks_html(stocks: List[Dict]) -> str:
    if not stocks:
        return '<div class="empty-state">今週の急騰銘柄はありませんでした。</div>'

    rows = []
    for i, s in enumerate(stocks, start=1):
        code = _html.escape(str(s.get("code", "")))
        name = _html.escape(str(s.get("name", code)))
        price = s.get("current_price")
        price_str = f"¥{price:,.0f}" if price is not None else "---"
        pct = s.get("week_change_pct", 0.0) or 0.0
        pct_class = "change-up" if pct >= 0 else "change-down"
        pct_str = _html.escape(f"{pct:+.2f}%")
        vol = s.get("vol_ratio")
        vol_str = _html.escape(f"{vol:.1f}x") if vol is not None else "---"
        vol_class = "vol-ratio-high" if vol is not None and vol >= SURGE_VOL_RATIO else ""
        rank_class = f"rank-{i}" if i <= 3 else ""
        rows.append(
            f'<tr>'
            f'<td><span class="rank-badge {rank_class}">{i}</span></td>'
            f'<td><div class="stock-name">{name}</div>'
            f'<div class="stock-code">{code}</div></td>'
            f'<td>{_html.escape(price_str)}</td>'
            f'<td class="{pct_class}">{pct_str}</td>'
            f'<td class="{vol_class}">{vol_str}</td>'
            f'</tr>'
        )

    rows_html = "\n".join(rows)
    return (
        '<div class="stock-table-wrapper">'
        '<table class="stock-table">'
        '<thead><tr>'
        '<th>#</th><th>銘柄</th><th>現在価格</th><th>1週間変化</th><th>出来高比率</th>'
        '</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>'
    )


def _build_top_news_html(news_items: List[Dict]) -> str:
    if not news_items:
        return '<div class="empty-state">今週の注目ニュースはありませんでした。</div>'

    cards = []
    for item in news_items:
        title = _html.escape(str(item.get("title", "")))
        source = _html.escape(str(item.get("source", "")))
        comment = _html.escape(str(item.get("comment", "")))
        link = safe_url(item.get("link", ""))
        source_tag = f'<span class="news-source-tag">{source}</span>' if source else ""
        title_html = f'<a href="{link}" target="_blank" rel="noopener">{title}</a>' if link != "#" else title
        comment_html = f'<div class="news-comment">{comment}</div>' if comment else ""
        cards.append(
            f'<div class="news-card">'
            f'<div class="news-meta">{source_tag}</div>'
            f'<div class="news-title">{title_html}</div>'
            f'{comment_html}'
            f'</div>'
        )

    return '<div class="news-list">' + "\n".join(cards) + "</div>"


def _render_html(
    week_label: str,
    date_range: str,
    stocks_html: str,
    news_html: str,
    monthly_url: str,
) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M JST")
    return (
        template
        .replace("{{WEEK_LABEL}}", _html.escape(week_label))
        .replace("{{GENERATED_DATE}}", _html.escape(now_str))
        .replace("{{DATE_RANGE}}", _html.escape(date_range))
        .replace("{{SURGING_STOCKS_SECTION}}", stocks_html)
        .replace("{{TOP_NEWS_SECTION}}", news_html)
        .replace("{{MONTHLY_REPORT_URL}}", safe_url(monthly_url))
    )


def _save_html(html_content: str, week_label: str) -> Path:
    DOCS_WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_WEEKLY_DIR / f"{week_label}.html"
    out_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Weekly HTML saved: {out_path}")
    return out_path


def _update_weekly_index(week_label: str) -> None:
    """docs/weekly/index.html を再生成して最新の週次レポートへのリンクを並べる。"""
    DOCS_WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    html_files = sorted(DOCS_WEEKLY_DIR.glob("[0-9]*.html"), reverse=True)
    items = []
    for p in html_files:
        label = _html.escape(p.stem)
        items.append(f'<li><a href="{label}.html">{label} 週次速報</a></li>')

    items_html = "\n".join(items) if items else "<li>（レポートなし）</li>"
    index_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>週次速報 アーカイブ</title>
<style>
  body {{ background: #0d0d1a; color: #e0e0f0;
         font-family: 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif;
         max-width: 600px; margin: 48px auto; padding: 0 16px; }}
  h1 {{ font-size: 20px; margin-bottom: 24px; color: #fff; }}
  ul {{ list-style: none; }}
  li {{ margin-bottom: 10px; }}
  a {{ color: #ff9944; font-size: 15px; }}
  a:hover {{ text-decoration: underline; }}
  .back {{ margin-top: 32px; font-size: 13px; }}
  .back a {{ color: #7eb8ff; }}
</style>
</head>
<body>
<h1>⚡ 週次速報 アーカイブ</h1>
<ul>
{items_html}
</ul>
<p class="back"><a href="../index.html">← 月次レポートへ</a></p>
</body>
</html>
"""
    (DOCS_WEEKLY_DIR / "index.html").write_text(index_content, encoding="utf-8")
    logger.info("Weekly index updated.")


# ─────────────────────────────────────────────────────────────────────────────
# LINE Flex Message
# ─────────────────────────────────────────────────────────────────────────────

def _build_flex_contents(
    week_label: str,
    stocks: List[Dict],
    news_items: List[Dict],
    report_url: str,
) -> Dict:
    """週次速報の LINE Flex bubble を構築する。"""
    stock_rows = []
    for i, s in enumerate(stocks[:5], start=1):
        code = str(s.get("code", ""))
        name = str(s.get("name", code))[:18]
        pct = s.get("week_change_pct", 0.0) or 0.0
        pct_color = "#44ee88" if pct >= 0 else "#ff5555"
        pct_str = f"{pct:+.1f}%"
        stock_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": f"{i}. {code} {name}",
                    "size": "sm",
                    "color": "#ddccbb",
                    "flex": 3,
                    "wrap": False,
                },
                {
                    "type": "text",
                    "text": pct_str,
                    "size": "sm",
                    "color": pct_color,
                    "flex": 1,
                    "align": "end",
                    "weight": "bold",
                },
            ],
            "spacing": "sm",
        })

    news_rows = []
    for item in news_items[:3]:
        title = str(item.get("title", ""))[:50]
        news_rows.append({
            "type": "text",
            "text": f"・{title}",
            "size": "xs",
            "color": "#bbaa99",
            "wrap": True,
            "margin": "sm",
        })

    body_contents = []
    if stock_rows:
        body_contents.append({
            "type": "text",
            "text": "🚀 急騰銘柄TOP5",
            "size": "sm",
            "weight": "bold",
            "color": "#ffbb33",
        })
        body_contents.extend(stock_rows)
    if news_rows:
        body_contents.append({"type": "separator", "margin": "md"})
        body_contents.append({
            "type": "text",
            "text": "📰 注目ニュース",
            "size": "sm",
            "weight": "bold",
            "color": "#ffbb33",
            "margin": "md",
        })
        body_contents.extend(news_rows)

    if not body_contents:
        body_contents.append({
            "type": "text",
            "text": "今週の急騰銘柄・注目ニュースはありませんでした。",
            "size": "sm",
            "color": "#886644",
            "wrap": True,
        })

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2d1500",
            "contents": [
                {
                    "type": "text",
                    "text": f"⚡ 週次速報 {week_label}",
                    "color": "#ffffff",
                    "size": "md",
                    "weight": "bold",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": _build_flex_footer(report_url),
        },
    }


def _build_flex_footer(report_url: str) -> List[dict]:
    """Flex footer コンテンツを構築する。safe_url 未適用の URL も内部で防御する。"""
    safe_report_url = safe_url(report_url) if report_url else "#"
    if safe_report_url and safe_report_url != "#":
        return [
            {
                "type": "button",
                "style": "primary",
                "color": "#cc5500",
                "action": {
                    "type": "uri",
                    "label": "詳細レポートを見る",
                    "uri": safe_report_url,
                },
            }
        ]
    return [
        {
            "type": "text",
            "text": "詳細レポートを見る",
            "size": "xs",
            "color": "#886644",
            "align": "center",
        }
    ]


# ─────────────────────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    now = datetime.now(timezone.utc).astimezone()
    # ISO week number: 例 "2026-W21"
    week_label = now.strftime("%G-W%V")
    week_start = now - timedelta(days=7)
    date_range = f"{week_start.strftime('%Y/%m/%d')} 〜 {now.strftime('%Y/%m/%d')}"

    logger.info(f"Starting weekly flash report: {week_label}")

    # 1. ニュース取得
    articles = fetch_news(days=7)
    logger.info(f"Fetched {len(articles)} articles for the week")

    # 2. Gemini でニュース選出
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    top_news: List[Dict] = []
    if gemini_api_key and articles:
        try:
            gemini = GeminiClient(gemini_api_key)
            top_news = _select_top_news(articles, gemini)
            logger.info(f"Selected {len(top_news)} top news items")
        except Exception as e:
            logger.warning(f"Gemini news selection failed entirely: {e}")
            top_news = [
                {"title": a.get("title", ""), "link": a.get("link", ""),
                 "source": a.get("source", ""), "comment": ""}
                for a in articles[:3]
            ]
    elif articles:
        top_news = [
            {"title": a.get("title", ""), "link": a.get("link", ""),
             "source": a.get("source", ""), "comment": ""}
            for a in articles[:3]
        ]

    # 3. 銘柄コード収集 → 急騰銘柄TOP5
    stocks_with_names = _collect_stocks_with_names()
    codes = [s["code"] for s in stocks_with_names]
    name_map = {s["code"]: s["name"] for s in stocks_with_names}
    logger.info(f"Collected {len(codes)} ticker codes to scan")
    top5: List[Dict] = []
    if codes:
        top5 = _build_top5(codes, name_map)
        logger.info(f"Found {len(top5)} surging stocks")

    # 4. HTMLレポート生成・保存
    monthly_url = os.environ.get("REPORT_URL", "https://github.com")
    stocks_html = _build_surging_stocks_html(top5)
    news_html = _build_top_news_html(top_news)
    html_content = _render_html(week_label, date_range, stocks_html, news_html, monthly_url)
    out_path = _save_html(html_content, week_label)

    # 5. weekly/index.html 更新
    _update_weekly_index(week_label)

    # 6. LINE Flex 通知
    channel_token = os.environ.get("LINE_CHANNEL_TOKEN")
    group_id = os.environ.get("LINE_GROUP_ID")
    if channel_token and group_id:
        # WEEKLY_REPORT_BASE_URL がなければ REPORT_URL から派生。
        # 末尾が .htm/.html のファイル名で終わる場合はそれを除去してからパスを追加する。
        base_url = os.environ.get("WEEKLY_REPORT_BASE_URL", "") or monthly_url
        base_url = re.sub(r"/[^/]+\.html?$", "", base_url).rstrip("/")
        weekly_report_url = safe_url(base_url + f"/weekly/{week_label}.html")
        flex_contents = _build_flex_contents(week_label, top5, top_news, weekly_report_url)
        line_client = LineClient(channel_token)
        alt_text = f"⚡ 週次速報 {week_label} — 急騰銘柄{len(top5)}件"
        success = line_client.send_flex(to=group_id, alt_text=alt_text, flex_contents=flex_contents)
        if success:
            logger.info("LINE Flex notification sent.")
        else:
            logger.warning("LINE Flex notification failed (non-blocking).")
    else:
        logger.info("LINE credentials not set. Skipping notification.")

    logger.info(f"Weekly flash report complete: {out_path}")


if __name__ == "__main__":
    run()
