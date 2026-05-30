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


def _collect_benchmark_pairs(theme_history: dict) -> list:
    """対指数α算出用に (year_month, market) ペアを重複なしで収集する。"""
    pairs = set()
    for theme in theme_history.get("themes", []):
        ym = theme.get("year_month", "")
        if not ym:
            continue
        for stock in theme.get("stocks", []):
            pairs.add((ym, stock.get("market", "JP")))
    return list(pairs)


def _build_hero_return(cumulative: dict) -> str:
    avg = cumulative.get("avg_return_pct", 0.0)
    sign = "+" if avg >= 0 else ""
    color = "var(--green)" if avg >= 0 else "var(--red)"
    return (
        f'<div class="perf-hero">'
        f'<span class="perf-hero__num" style="color:{color};">{sign}{avg}</span>'
        f'<span class="perf-hero__pct" style="color:{color};">%</span>'
        f'<span class="perf-hero__note">平均リターン</span>'
        f'</div>'
    )


def _build_best_worst(cumulative: dict) -> str:
    best = cumulative.get("best_pick_ever")
    worst = cumulative.get("worst_pick_ever")

    def cell(label, stock, color, arrow_path, is_up):
        if not stock:
            val_html = '<div class="tp-best-worst__value" style="color:var(--text-mute)">—</div>'
            name_html = '<div class="tp-best-worst__name">データなし</div>'
        else:
            sign = "+" if stock["return_pct"] >= 0 else ""
            up_cls = "tp-up" if is_up else "tp-down"
            val_html = f'<div class="tp-best-worst__value {up_cls}">{sign}{stock["return_pct"]}%</div>'
            name_html = f'<div class="tp-best-worst__name">{_html.escape(stock.get("name",""))}</div>'

        return (
            f'<div class="tp-best-worst__cell">'
            f'<div class="tp-best-worst__head">'
            f'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7">{arrow_path}</svg>'
            f'<span class="tp-mini">{label}</span>'
            f'</div>'
            f'{name_html}'
            f'{val_html}'
            f'</div>'
        )

    best_cell = cell(
        "BEST", best, "#7dc679",
        '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
        True
    )
    worst_cell = cell(
        "WORST", worst, "#e16158",
        '<path d="M3 7l6 6 4-4 8 8"/><path d="M14 17h7v-7"/>',
        False
    )
    return f'<div class="tp-best-worst">{best_cell}{worst_cell}</div>'


def _build_top_picks(monthly: list) -> str:
    # Collect all picks, sort by return_pct desc, take top 5
    all_picks = []
    for m in monthly:
        for pick in m.get("picks", []):
            if pick.get("return_pct") is not None:
                all_picks.append({
                    "name": pick.get("name", ""),
                    "code": pick.get("code", ""),
                    "theme": pick.get("theme", ""),
                    "month": m.get("year_month", ""),
                    "return": pick["return_pct"],
                })
    all_picks.sort(key=lambda x: x["return"], reverse=True)
    top5 = all_picks[:5]

    if not top5:
        return '<div style="color:var(--text-mute);font-size:13px;padding:12px 0;">データなし</div>'

    rows = []
    for i, s in enumerate(top5, start=1):
        ret = s["return"]
        ret_cls = "tp-up" if ret >= 0 else "tp-down"
        sign = "+" if ret >= 0 else ""
        top_cls = " tp-perf-row--top" if i == 1 else ""
        rows.append(
            f'<div class="tp-perf-row{top_cls}">'
            f'<div class="tp-perf-row__rank">{i}</div>'
            f'<div class="tp-perf-row__body">'
            f'<div class="tp-perf-row__name">{_html.escape(s["name"])}</div>'
            f'<div class="tp-perf-row__sub">{_html.escape(s["code"])} · {_html.escape(s["theme"])} · {_html.escape(s["month"])}</div>'
            f'</div>'
            f'<div class="tp-perf-row__return {ret_cls}">{sign}{ret}%</div>'
            f'</div>'
        )
    return "\n".join(rows)


def _build_summary_cards(cumulative: dict) -> str:
    total = cumulative.get("total_picks", 0)
    win_rate = round(cumulative.get("overall_win_rate", 0.0) * 100, 1)

    # 対指数α（超過リターン）。ベンチマーク未取得時は "—"。
    avg_alpha = cumulative.get("avg_alpha_pct")
    if avg_alpha is None:
        alpha_str = "—"
        alpha_cls = ""
    else:
        alpha_sign = "+" if avg_alpha >= 0 else ""
        alpha_str = f"{alpha_sign}{avg_alpha}%"
        alpha_cls = "tp-kpi__value--green" if avg_alpha >= 0 else "tp-kpi__value--red"

    # 指数に勝った銘柄の割合
    alpha_win = cumulative.get("alpha_win_rate")
    alpha_win_str = f"{round(alpha_win * 100, 1)}%" if alpha_win is not None else "—"

    return (
        '<div class="tp-kpi-grid">\n'
        f'  <div class="tp-kpi"><div class="tp-kpi__label">勝率</div>'
        f'<div class="tp-kpi__value">{win_rate}%</div></div>\n'
        f'  <div class="tp-kpi"><div class="tp-kpi__label">対指数α</div>'
        f'<div class="tp-kpi__value {alpha_cls}">{_html.escape(alpha_str)}</div></div>\n'
        f'  <div class="tp-kpi"><div class="tp-kpi__label">指数勝率</div>'
        f'<div class="tp-kpi__value">{_html.escape(alpha_win_str)}</div></div>\n'
        f'  <div class="tp-kpi"><div class="tp-kpi__label">推奨</div>'
        f'<div class="tp-kpi__value">{total}</div></div>\n'
        '</div>'
    )


def _format_pct(val: float, name: str) -> str:
    """リターン率と銘柄名を '±N.N% (名前)' 形式の文字列に変換する。"""
    sign = "+" if val >= 0 else ""
    return f'{sign}{val}% ({_html.escape(name)})'


def _build_monthly_table_rows(monthly: list) -> str:
    # Calculate maxAbs for bar normalization
    avgs = [m.get("avg_return_pct") for m in monthly if m.get("avg_return_pct") is not None]
    max_abs = max((abs(a) for a in avgs), default=1) or 1

    rows = []
    for m in monthly:
        ym = _html.escape(m["year_month"])
        avg_ret = m.get("avg_return_pct")
        win_pct = round((m.get("win_rate") or 0) * 100, 1)

        if avg_ret is None:
            rows.append(
                f'<div class="tp-monthly-row">'
                f'<span class="tp-monthly-row__month">{ym}</span>'
                f'<div class="tp-monthly-row__bar"><div class="tp-monthly-row__bar-axis"></div></div>'
                f'<span class="tp-monthly-row__win">—</span>'
                f'<span class="tp-monthly-row__return" style="color:var(--text-mute)">データなし</span>'
                f'</div>'
            )
            continue

        bar_pct = abs(avg_ret) / max_abs * 50  # 0-50%
        if avg_ret >= 0:
            bar_style = f'left:50%;width:{bar_pct:.1f}%;background:var(--green);'
        else:
            bar_style = f'left:{50 - bar_pct:.1f}%;width:{bar_pct:.1f}%;background:var(--red);'

        ret_cls = "tp-up" if avg_ret >= 0 else "tp-down"
        sign = "+" if avg_ret >= 0 else ""

        rows.append(
            f'<div class="tp-monthly-row">'
            f'<span class="tp-monthly-row__month">{ym}</span>'
            f'<div class="tp-monthly-row__bar">'
            f'<div class="tp-monthly-row__bar-fill" style="{bar_style}"></div>'
            f'<div class="tp-monthly-row__bar-axis"></div>'
            f'</div>'
            f'<span class="tp-monthly-row__win">{win_pct}%</span>'
            f'<span class="tp-monthly-row__return {ret_cls}">{sign}{avg_ret}%</span>'
            f'</div>'
        )
    return "\n".join(rows) if rows else '<div style="color:var(--text-mute);padding:12px 0">データなし</div>'


def _build_stocks_table_rows(monthly: list) -> str:
    from src.utils.ticker_utils import format_price, get_currency
    rows = []
    for m in monthly:
        for pick in m.get("picks", []):
            ret = pick["return_pct"]
            ret_cls = "tp-up" if ret > 0 else ("tp-down" if ret < 0 else "")
            sign = "+" if ret > 0 else ""
            currency = pick.get("currency", get_currency(pick.get("market", "JP")))
            price_pick = format_price(pick["price_at_pick"], currency) if pick.get("price_at_pick") else "—"
            price_now = format_price(pick["current_price"], currency) if pick.get("current_price") else "—"
            theme_esc = _html.escape(pick.get("theme", ""))
            name_esc = _html.escape(pick.get("name", ""))
            code_esc = _html.escape(pick.get("code", ""))
            ym_esc = _html.escape(pick.get("year_month", ""))

            rows.append(
                f'<tr data-theme="{theme_esc}" data-year_month="{ym_esc}" data-return_pct="{ret}">'
                f'<td class="col-month">{ym_esc}</td>'
                f'<td class="col-code">{code_esc}</td>'
                f'<td class="col-name">{name_esc}</td>'
                f'<td class="col-price">{_html.escape(price_pick)} → {_html.escape(price_now)}</td>'
                f'<td class="col-return {ret_cls}">{sign}{ret}%</td>'
                f'</tr>'
            )
    return "\n".join(rows) if rows else '<tr><td colspan="5" style="color:var(--text-mute)">データなし</td></tr>'


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

    # 対指数α算出のためベンチマーク（日経/S&P500）リターンを取得
    benchmark_returns = {}
    if all_codes:
        from src.utils.backtest import fetch_benchmark_returns
        pairs = _collect_benchmark_pairs(theme_history)
        try:
            benchmark_returns = fetch_benchmark_returns(pairs)
            logger.info(f"ベンチマーク取得: {len(benchmark_returns)}/{len(pairs)} (year_month, market) ペア")
        except Exception as e:
            logger.warning(f"ベンチマーク取得失敗（αはスキップ）: {e}")
            benchmark_returns = {}

    from src.utils.backtest import calculate_performance
    perf = calculate_performance(theme_history, current_prices, benchmark_returns or None)

    monthly = perf["monthly"]
    cumulative = perf["cumulative"]

    alpha = cumulative.get("avg_alpha_pct")
    logger.info(
        f"集計完了: 累計{cumulative['total_picks']}銘柄, "
        f"勝率{round(cumulative['overall_win_rate']*100,1)}%, "
        f"平均リターン{cumulative['avg_return_pct']}%, "
        f"対指数α{alpha if alpha is not None else '—'}"
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
    html = html.replace("{{HERO_RETURN_SECTION}}", _build_hero_return(cumulative))
    html = html.replace("{{SUMMARY_CARDS}}", _build_summary_cards(cumulative))
    html = html.replace("{{BEST_WORST_SECTION}}", _build_best_worst(cumulative))
    html = html.replace("{{TOP_PICKS_SECTION}}", _build_top_picks(monthly))
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
