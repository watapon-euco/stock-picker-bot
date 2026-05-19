"""
Step 6: バックテスト実行 + 成績ダッシュボード HTML 生成

使用方法:
  python -m src.step6_backtest          # 直接実行
  python -m src.main --step 6           # main.py 経由
"""
import html as _html
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
DOCS_DIR = REPO_ROOT / "docs"
TEMPLATE_PATH = REPO_ROOT / "src" / "templates" / "performance_template.html"
OUTPUT_PATH = DOCS_DIR / "performance.html"
THEME_HISTORY_PATH = DATA_DIR / "theme_history.json"


def _load_theme_history() -> dict:
    if not THEME_HISTORY_PATH.exists():
        logger.warning(f"theme_history.json が見つかりません: {THEME_HISTORY_PATH}")
        return {"themes": []}
    with open(THEME_HISTORY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _collect_all_codes(theme_history: dict) -> tuple:
    """theme_history から全銘柄コードと市場マップを重複なしで収集する。

    Returns:
        (codes: list, market_map: dict) のタプル
    """
    codes = set()
    market_map: dict = {}
    for theme in theme_history.get("themes", []):
        for stock in theme.get("stocks", []):
            code = str(stock.get("code", "")).strip()
            if code:
                codes.add(code)
                if code not in market_map:
                    market_map[code] = stock.get("market", "JP")
    return list(codes), market_map


def _build_summary_cards(cumulative: dict) -> str:
    total = cumulative.get("total_picks", 0)
    win_rate = cumulative.get("overall_win_rate", 0.0)
    avg_ret = cumulative.get("avg_return_pct", 0.0)
    best = cumulative.get("best_pick_ever")

    win_rate_pct = round(win_rate * 100, 1)
    avg_cls = "positive" if avg_ret >= 0 else "negative"
    avg_sign = "+" if avg_ret >= 0 else ""
    win_cls = "positive" if win_rate_pct >= 50 else "negative"

    best_html = ""
    if best:
        sign = "+" if best["return_pct"] >= 0 else ""
        best_html = (
            f'<div class="card-value" style="font-size:18px">'
            f'{_html.escape(best["name"])}</div>'
            f'<div class="card-sub">{sign}{best["return_pct"]}%</div>'
        )
    else:
        best_html = '<div class="card-value" style="font-size:16px;color:#555577">—</div>'

    return f"""<div class="summary-cards">
  <div class="summary-card">
    <div class="card-label">累計推奨数</div>
    <div class="card-value">{total}</div>
    <div class="card-sub">銘柄</div>
  </div>
  <div class="summary-card {win_cls}">
    <div class="card-label">全体勝率</div>
    <div class="card-value">{win_rate_pct}%</div>
    <div class="card-sub">上昇銘柄の割合</div>
  </div>
  <div class="summary-card {avg_cls}">
    <div class="card-label">平均リターン</div>
    <div class="card-value">{avg_sign}{avg_ret}%</div>
    <div class="card-sub">全推奨銘柄平均</div>
  </div>
  <div class="summary-card positive">
    <div class="card-label">最高パフォーマー</div>
    {best_html}
  </div>
</div>"""


def _format_pct(val: float, name: str) -> str:
    """リターン率と銘柄名を '±N.N% (名前)' 形式の文字列に変換する。"""
    sign = "+" if val >= 0 else ""
    return f'{sign}{val}% ({_html.escape(name)})'


def _build_monthly_table_rows(monthly: list) -> str:
    rows = []
    for m in monthly:
        ym = _html.escape(m["year_month"])
        count = m.get("pick_count", 0)

        if m.get("avg_return_pct") is None:
            rows.append(
                f'<tr><td>{ym}</td><td>{count}</td>'
                f'<td colspan="4" style="color:#555577">データなし</td></tr>'
            )
            continue

        win_pct = round((m["win_rate"] or 0) * 100, 1)
        avg_ret = m["avg_return_pct"]
        avg_cls = "change-up" if avg_ret >= 0 else "change-down"
        avg_sign = "+" if avg_ret >= 0 else ""

        top = m.get("top_performer") or {}
        worst = m.get("worst_performer") or {}
        top_str = _format_pct(top["return_pct"], top.get("name", "")) if top else "—"
        worst_str = _format_pct(worst["return_pct"], worst.get("name", "")) if worst else "—"

        rows.append(
            f'<tr>'
            f'<td>{ym}</td>'
            f'<td>{count}</td>'
            f'<td>{win_pct}%</td>'
            f'<td class="{avg_cls}">{avg_sign}{avg_ret}%</td>'
            f'<td class="change-up" style="font-size:12px">{top_str}</td>'
            f'<td class="change-down" style="font-size:12px">{worst_str}</td>'
            f'</tr>'
        )
    return "\n".join(rows) if rows else '<tr><td colspan="6" style="color:#555577">データなし</td></tr>'


def _build_stocks_table_rows(monthly: list) -> str:
    from src.utils.ticker_utils import format_price, get_currency
    rows = []
    for m in monthly:
        for pick in m.get("picks", []):
            ret = pick["return_pct"]
            ret_cls = "change-up" if ret > 0 else ("change-down" if ret < 0 else "change-flat")
            sign = "+" if ret > 0 else ""
            currency = pick.get("currency", get_currency(pick.get("market", "JP")))
            price_pick = (
                format_price(pick["price_at_pick"], currency) if pick.get("price_at_pick") else "—"
            )
            price_now = (
                format_price(pick["current_price"], currency) if pick.get("current_price") else "—"
            )
            market_badge = _html.escape(pick.get("market", "JP"))
            theme_esc = _html.escape(pick.get("theme", ""))
            name_esc = _html.escape(pick.get("name", ""))
            code_esc = _html.escape(pick.get("code", ""))
            ym_esc = _html.escape(pick.get("year_month", ""))

            rows.append(
                f'<tr data-year_month="{ym_esc}" data-theme="{theme_esc}" '
                f'data-code="{code_esc}" data-name="{name_esc}" '
                f'data-return_pct="{ret}" data-price_at_pick="{pick.get("price_at_pick","")}" '
                f'data-current_price="{pick.get("current_price","")}">'
                f'<td>{ym_esc}</td>'
                f'<td>{code_esc} <span style="font-size:10px;color:#5577cc">{market_badge}</span></td>'
                f'<td>{name_esc}</td>'
                f'<td>{price_pick}</td>'
                f'<td>{price_now}</td>'
                f'<td class="{ret_cls}">{sign}{ret}%</td>'
                f'<td style="font-size:12px">{theme_esc}</td>'
                f'</tr>'
            )
    return "\n".join(rows) if rows else '<tr><td colspan="7" style="color:#555577">データなし</td></tr>'


def _build_filter_options(monthly: list, key: str) -> str:
    seen = []
    for m in monthly:
        for pick in m.get("picks", []):
            val = pick.get(key, "")
            if val and val not in seen:
                seen.append(val)
    return "\n".join(
        f'<option value="{_html.escape(v)}">{_html.escape(v)}</option>'
        for v in seen
    )


def _build_chart_data(monthly: list) -> tuple:
    labels = []
    returns = []
    for m in monthly:
        labels.append(m["year_month"])
        returns.append(m.get("avg_return_pct"))  # None はチャートで null 扱い
    return labels, returns


def _period_range(monthly: list) -> str:
    months = [m["year_month"] for m in monthly if m.get("pick_count", 0) > 0]
    if not months:
        return "データなし"
    return f"{months[0]} 〜 {months[-1]}"


def run():
    logger.info("Step 6: バックテスト計算開始")

    theme_history = _load_theme_history()
    all_codes, market_map = _collect_all_codes(theme_history)

    if not all_codes:
        logger.warning("theme_history.json に銘柄データがありません（stocks フィールドなし）。空ダッシュボードを生成します。")
        current_prices = {}
    else:
        logger.info(f"{len(all_codes)} 銘柄の現在価格を取得中...")
        from src.utils.backtest import fetch_current_prices
        current_prices = fetch_current_prices(all_codes, market_map=market_map)
        fetched = sum(1 for v in current_prices.values() if v is not None)
        logger.info(f"価格取得完了: {fetched}/{len(all_codes)} 銘柄")

    from src.utils.backtest import calculate_performance
    perf = calculate_performance(theme_history, current_prices)

    monthly = perf["monthly"]
    cumulative = perf["cumulative"]

    logger.info(
        f"集計完了: 累計{cumulative['total_picks']}銘柄, "
        f"勝率{round(cumulative['overall_win_rate']*100,1)}%, "
        f"平均リターン{cumulative['avg_return_pct']}%"
    )

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    chart_labels, chart_returns = _build_chart_data(monthly)
    chart_labels_json = json.dumps(chart_labels, ensure_ascii=False)
    chart_returns_json = json.dumps(
        [r if r is not None else None for r in chart_returns]
    )

    updated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    html = template
    html = html.replace("{{UPDATED_AT}}", _html.escape(updated_at))
    html = html.replace("{{PERIOD_RANGE}}", _html.escape(_period_range(monthly)))
    html = html.replace("{{SUMMARY_CARDS}}", _build_summary_cards(cumulative))
    html = html.replace("{{MONTHLY_TABLE_ROWS}}", _build_monthly_table_rows(monthly))
    html = html.replace("{{STOCKS_TABLE_ROWS}}", _build_stocks_table_rows(monthly))
    html = html.replace("{{THEME_FILTER_OPTIONS}}", _build_filter_options(monthly, "theme"))
    html = html.replace("{{MONTH_FILTER_OPTIONS}}", _build_filter_options(monthly, "year_month"))
    html = html.replace("{{CHART_LABELS}}", chart_labels_json)
    html = html.replace("{{CHART_RETURNS}}", chart_returns_json)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    logger.info(f"ダッシュボード生成完了: {OUTPUT_PATH}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    run()
