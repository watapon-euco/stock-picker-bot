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
from src.utils.ticker_utils import normalize_ticker

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
    """stock_data.json + watchlist.json から {code, name, market} リストを重複なしで返す。

    market フィールドは JP/US 銘柄を正しいティッカーへ正規化するために使う。
    """
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
                        stocks.append({
                            "code": code,
                            "name": stock.get("name", code),
                            "market": stock.get("market", "JP"),
                        })
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
                    stocks.append({
                        "code": code,
                        "name": stock.get("name", code),
                        "market": stock.get("market", "JP"),
                    })
                    seen.add(code)
        except Exception as e:
            logger.warning(f"Failed to read watchlist.json: {e}")

    return stocks


# ─────────────────────────────────────────────────────────────────────────────
# 週次株価変化率計算
# ─────────────────────────────────────────────────────────────────────────────

_BATCH_SIZE = 20  # backtest.py と同じチャンクサイズ


def _to_yf_ticker(code: str, market: str = None) -> str:
    """銘柄コードを yfinance ティッカーに正規化する。

    market 未指定時はコード形式から自動判定する（4桁数字→JP, 英字→US）。
    日本株は ``.T``、米国株はティッカーそのまま（BRK.B→BRK-B）。
    """
    return normalize_ticker(str(code).strip(), market)


def _fetch_weekly_changes_batch(
    codes: List[str], name_map: Dict[str, str], market_map: Optional[Dict[str, str]] = None
) -> List[Dict]:
    """
    銘柄コードリストの1ヶ月分日足データをバッチ取得し、
    各銘柄の週次変化率・出来高比率を計算して返す。

    バッチサイズは20件（backtest.py と同様）。tk.info は呼ばない。

    Returns:
        計算成功した銘柄の Dict リスト。失敗銘柄はスキップ。
    """
    if not codes:
        return []

    if market_map is None:
        market_map = {}

    ticker_to_code = {_to_yf_ticker(c, market_map.get(c)): c for c in codes}
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
                    "market": market_map.get(code, "JP"),
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


def _build_top5(
    codes: List[str],
    name_map: Optional[Dict[str, str]] = None,
    market_map: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """銘柄コードから急騰銘柄を抽出してスコア順に上位5件を返す。"""
    if name_map is None:
        name_map = {}
    all_data = _fetch_weekly_changes_batch(codes, name_map, market_map)
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

def make_spark_svg(values, w=44, h=20, color="#7dc679"):
    """終値リストからインラインSVGスパークラインを生成する"""
    if not values or len(values) < 2:
        return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"></svg>'
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = w / (len(values) - 1)
    pts = [(i * step, h - (v - mn) / rng * h) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.3" stroke-linecap="round"/>'
            f'</svg>')


def _fetch_market_indices() -> str:
    """4市場指標（日経/S&P500/USDJPY/VIX）を取得してtp-stat-strip--4を生成する"""
    indices = [
        ("^N225", "日経平均"),
        ("^GSPC", "S&P 500"),
        ("JPY=X", "USD/JPY"),
        ("^VIX", "VIX"),
    ]

    cells = []
    any_success = False

    for ticker_sym, label in indices:
        try:
            tk = yf.Ticker(ticker_sym)
            hist = tk.history(period="2d", auto_adjust=True)
            if hist.empty or len(hist) < 1:
                raise ValueError("No data")

            current = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                chg_pct = (current - prev) / prev * 100 if prev else 0.0
            else:
                chg_pct = 0.0

            if ticker_sym == "JPY=X":
                val_str = f"{current:.2f}"
            elif ticker_sym == "^VIX":
                val_str = f"{current:.2f}"
            elif ticker_sym == "^N225":
                val_str = f"{current:,.0f}"
            else:
                val_str = f"{current:,.2f}"

            chg_str = f"{chg_pct:+.1f}%"
            chg_cls = "tp-stat-cell__value--green" if chg_pct >= 0 else "tp-stat-cell__value--red"
            any_success = True

            cells.append(
                f'<div class="tp-stat-cell">'
                f'<div class="tp-stat-cell__label">{_html.escape(label)}</div>'
                f'<div class="tp-stat-cell__value {chg_cls} tp-stat-cell__value--mono">'
                f'{_html.escape(val_str)} <span style="font-size:9.5px">{_html.escape(chg_str)}</span>'
                f'</div>'
                f'</div>'
            )
        except Exception as e:
            logger.warning(f"Failed to fetch {ticker_sym}: {e}")
            cells.append(
                f'<div class="tp-stat-cell">'
                f'<div class="tp-stat-cell__label">{_html.escape(label)}</div>'
                f'<div class="tp-stat-cell__value" style="color:var(--text-mute)">—</div>'
                f'</div>'
            )

    if not any_success:
        return ""

    cells_html = "\n    ".join(cells)
    return (
        f'<section class="tp-section--tight">\n'
        f'  <div class="tp-stat-strip tp-stat-strip--4">\n'
        f'    {cells_html}\n'
        f'  </div>\n'
        f'</section>\n'
    )


def _build_earnings_calendar_section() -> str:
    """7日以内の決算予定銘柄を .tp-earnings-row で列挙する"""
    try:
        from src.utils.earnings_fetcher import fetch_upcoming_earnings
    except ImportError:
        return ""

    stocks_with_names = _collect_stocks_with_names()
    if not stocks_with_names:
        return ""

    icon_svg = (
        '<svg class="tp-earnings-row__icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
        '<rect x="3" y="5" width="18" height="16" rx="1"/>'
        '<line x1="3" y1="10" x2="21" y2="10"/>'
        '<line x1="8" y1="3" x2="8" y2="7"/>'
        '<line x1="16" y1="3" x2="16" y2="7"/>'
        '</svg>'
    )

    rows_html = []
    for stock in stocks_with_names[:30]:
        code = stock["code"]
        name = stock.get("name", code)
        stock_market = stock.get("market", "JP")
        try:
            result = fetch_upcoming_earnings(code, lookahead_days=7, market=stock_market)
            if result and result.get("earnings_date"):
                earnings_date = result["earnings_date"]
                market = "US" if stock_market == "US" else "T"
                rows_html.append(
                    f'<div class="tp-earnings-row">'
                    f'{icon_svg}'
                    f'<div class="tp-earnings-row__body">'
                    f'<div class="tp-earnings-row__name">{_html.escape(name)}</div>'
                    f'<div class="tp-earnings-row__sub">{_html.escape(code)}.{_html.escape(market)}</div>'
                    f'</div>'
                    f'<div class="tp-earnings-row__date">{_html.escape(earnings_date)}</div>'
                    f'</div>'
                )
        except Exception:
            pass

    if not rows_html:
        return ""

    return (
        '<section class="tp-section">\n'
        '  <div class="tp-section__head"><div class="tp-kicker">来週の決算予定</div></div>\n'
        + "\n".join(rows_html) + "\n"
        '</section>\n'
    )


def _build_dividend_calendar_section() -> str:
    """14日以内に権利落ち（ex-dividend）を迎える銘柄を列挙する。

    決算予定と対になる投資イベント。権利付き最終日に向けた仕込み判断に使う。
    """
    try:
        from src.utils.dividend_fetcher import fetch_upcoming_dividend
    except ImportError:
        return ""

    stocks_with_names = _collect_stocks_with_names()
    if not stocks_with_names:
        return ""

    icon_svg = (
        '<svg class="tp-earnings-row__icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M12 7v10M9.5 9.5h4a1.5 1.5 0 010 3h-3a1.5 1.5 0 000 3h4"/>'
        '</svg>'
    )

    rows_html = []
    for stock in stocks_with_names[:30]:
        code = stock["code"]
        name = stock.get("name", code)
        stock_market = stock.get("market", "JP")
        try:
            result = fetch_upcoming_dividend(code, lookahead_days=14, market=stock_market)
            if result and result.get("ex_date"):
                market = "US" if stock_market == "US" else "T"
                yld = result.get("dividend_yield")
                yld_str = f"利回り{yld:.1f}%" if isinstance(yld, (int, float)) and yld < 1 else (
                    f"利回り{yld:.1f}%" if isinstance(yld, (int, float)) else ""
                )
                sub = f'{_html.escape(code)}.{_html.escape(market)}'
                if yld_str:
                    sub += f' · {_html.escape(yld_str)}'
                rows_html.append(
                    f'<div class="tp-earnings-row">'
                    f'{icon_svg}'
                    f'<div class="tp-earnings-row__body">'
                    f'<div class="tp-earnings-row__name">{_html.escape(name)}</div>'
                    f'<div class="tp-earnings-row__sub">{sub}</div>'
                    f'</div>'
                    f'<div class="tp-earnings-row__date">{_html.escape(result["ex_date"])}</div>'
                    f'</div>'
                )
        except Exception:
            pass

    if not rows_html:
        return ""

    return (
        '<section class="tp-section">\n'
        '  <div class="tp-section__head"><div class="tp-kicker">権利落ち予定（2週間以内）</div></div>\n'
        + "\n".join(rows_html) + "\n"
        '</section>\n'
    )


def _build_surging_stocks_html(stocks: List[Dict]) -> str:
    if not stocks:
        return '<div style="color:var(--text-mute);padding:16px 0;font-size:13px">今週の急騰銘柄はありませんでした。</div>'

    rows = []
    for i, s in enumerate(stocks, start=1):
        code = _html.escape(str(s.get("code", "")))
        name = _html.escape(str(s.get("name", code)))
        market = "US" if s.get("market") == "US" else "T"
        pct = s.get("week_change_pct", 0.0) or 0.0
        pct_color = "#7dc679" if pct >= 0 else "#e16158"
        pct_str = f"{pct:+.1f}%"
        vol = s.get("vol_ratio")
        vol_str = f"x{vol:.1f}" if vol is not None else "—"
        top_cls = " tp-surge-row--top" if i == 1 else ""

        closes = s.get("price_history", [])
        spark = make_spark_svg(closes, color=pct_color) if closes else make_spark_svg([], color=pct_color)

        rows.append(
            f'<div class="tp-surge-row{top_cls}">'
            f'<div class="tp-surge-row__rank">{i}</div>'
            f'<div>'
            f'<div class="tp-surge-row__name">{name}</div>'
            f'<div class="tp-surge-row__sub">{code}.{market} · <span class="sector"></span></div>'
            f'</div>'
            f'{spark}'
            f'<div class="tp-surge-row__vol">{_html.escape(vol_str)}</div>'
            f'<div class="tp-surge-row__change" style="color:{pct_color}">{_html.escape(pct_str)}</div>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_top_news_html(news_items: List[Dict]) -> str:
    if not news_items:
        return '<div style="color:var(--text-mute);padding:16px 0;font-size:13px">今週の注目ニュースはありませんでした。</div>'

    articles = []
    for item in news_items:
        title = _html.escape(str(item.get("title", "")))
        source = _html.escape(str(item.get("source", "")).upper())
        comment = _html.escape(str(item.get("comment", "")))
        link = safe_url(item.get("link", ""))

        date_str = item.get("published", "") or ""
        date_display = ""
        if date_str:
            try:
                from dateutil import parser as dateparser
                dt = dateparser.parse(date_str)
                date_display = dt.strftime("%m/%d") if dt else ""
            except Exception:
                date_display = ""

        title_html = f'<a href="{link}" target="_blank" rel="noopener">{title}</a>' if link != "#" else title
        comment_html = f'<div class="tp-news__comment">{comment}</div>' if comment else ""

        articles.append(
            f'<article class="tp-news">'
            f'<div class="tp-news__meta">'
            f'<span class="tp-news__source">{source}</span>'
            f'<span class="tp-news__time">{_html.escape(date_display)}</span>'
            f'</div>'
            f'<h3 class="tp-news__title">{title_html}</h3>'
            f'{comment_html}'
            f'</article>'
        )

    return "\n".join(articles)


def _render_html(
    week_label: str,
    date_range: str,
    stocks_html: str,
    news_html: str,
    monthly_url: str,
    market_indices_html: str = "",
    earnings_calendar_html: str = "",
    dividend_calendar_html: str = "",
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
        .replace("{{MARKET_INDICES_SECTION}}", market_indices_html)
        .replace("{{EARNINGS_CALENDAR_SECTION}}", earnings_calendar_html)
        .replace("{{DIVIDEND_CALENDAR_SECTION}}", dividend_calendar_html)
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
    market_map = {s["code"]: s.get("market", "JP") for s in stocks_with_names}
    logger.info(f"Collected {len(codes)} ticker codes to scan")
    top5: List[Dict] = []
    if codes:
        top5 = _build_top5(codes, name_map, market_map)
        logger.info(f"Found {len(top5)} surging stocks")

    # 4. HTMLレポート生成・保存
    monthly_url = os.environ.get("REPORT_URL", "https://github.com")
    stocks_html = _build_surging_stocks_html(top5)
    news_html = _build_top_news_html(top_news)
    market_indices_html = _fetch_market_indices()
    earnings_calendar_html = _build_earnings_calendar_section()
    dividend_calendar_html = _build_dividend_calendar_section()
    html_content = _render_html(
        week_label, date_range, stocks_html, news_html, monthly_url,
        market_indices_html=market_indices_html,
        earnings_calendar_html=earnings_calendar_html,
        dividend_calendar_html=dividend_calendar_html,
    )
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
