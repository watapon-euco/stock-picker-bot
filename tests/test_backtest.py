"""
バックテスト計算ロジックのユニットテスト

yfinance は使用しない（モックで代替）。
"""
import pytest
from unittest.mock import patch, MagicMock

from src.utils.backtest import calculate_performance, _extract_monthly_entries, _to_yf_ticker
from src.step6_backtest import _build_monthly_table_rows, _format_pct


# ─────────────────────────────────────────────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────────────────────────────────────────────

def _make_history(*month_stocks):
    """
    月別の (year_month, [(code, name, price_at_pick), ...]) タプルから
    theme_history.json 相当の dict を生成するヘルパー。
    """
    themes = []
    for year_month, stocks in month_stocks:
        stock_entries = [
            {"code": c, "name": n, "rank": i + 1, "price_at_pick": p}
            for i, (c, n, p) in enumerate(stocks)
        ]
        themes.append({
            "name": f"テーマ_{year_month}",
            "year_month": year_month,
            "icon": "📊",
            "stocks": stock_entries,
        })
    return {"themes": themes}


# ─────────────────────────────────────────────────────────────────────────────
# テスト 1: 月別集計が正しく計算される
# ─────────────────────────────────────────────────────────────────────────────

def test_monthly_aggregation_correct():
    """推奨時価格と現在価格からリターン・勝率・平均が正しく計算される"""
    history = _make_history(
        ("2026-03", [("7203", "トヨタ", 1000), ("8035", "東京エレクトロン", 2000)]),
    )
    current_prices = {"7203": 1200.0, "8035": 1800.0}

    result = calculate_performance(history, current_prices)
    monthly = result["monthly"]

    assert len(monthly) == 1
    m = monthly[0]
    assert m["year_month"] == "2026-03"
    assert m["pick_count"] == 2

    # トヨタ: +20%, 東エレク: -10%
    assert m["avg_return_pct"] == pytest.approx(5.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# テスト 2: 勝率計算（上昇銘柄数 / 全銘柄数）
# ─────────────────────────────────────────────────────────────────────────────

def test_win_rate_calculation():
    """正のリターン銘柄の割合が勝率として正しく計算される"""
    history = _make_history(
        ("2026-03", [
            ("1001", "銘柄A", 100),  # +20% → 勝ち
            ("1002", "銘柄B", 100),  # -10% → 負け
            ("1003", "銘柄C", 100),  # +50% → 勝ち
            ("1004", "銘柄D", 100),  # -5%  → 負け
        ]),
    )
    current_prices = {"1001": 120.0, "1002": 90.0, "1003": 150.0, "1004": 95.0}

    result = calculate_performance(history, current_prices)
    m = result["monthly"][0]

    # 勝ち2件 / 全4件 = 0.5
    assert m["win_rate"] == pytest.approx(0.5, abs=0.001)
    assert m["pick_count"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# テスト 3: 複数月の累計集計
# ─────────────────────────────────────────────────────────────────────────────

def test_cumulative_aggregation_multiple_months():
    """複数月のデータが正しく累積集計される"""
    history = _make_history(
        ("2026-03", [("1001", "銘柄A", 100), ("1002", "銘柄B", 200)]),
        ("2026-04", [("1003", "銘柄C", 150)]),
    )
    current_prices = {
        "1001": 110.0,   # +10%
        "1002": 180.0,   # -10%
        "1003": 180.0,   # +20%
    }

    result = calculate_performance(history, current_prices)
    cum = result["cumulative"]

    assert cum["total_picks"] == 3
    assert cum["winning_picks"] == 2
    # 月が2つあることを確認
    assert len(result["monthly"]) == 2
    assert cum["overall_win_rate"] == pytest.approx(2 / 3, abs=0.001)
    # 平均 = (10 - 10 + 20) / 3 = 20/3 ≈ 6.67%
    assert cum["avg_return_pct"] == pytest.approx(20 / 3, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# テスト 4: current_prices に code がない銘柄は安全にスキップ
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_current_price_skipped_safely():
    """current_prices に存在しないコードはエラーなくスキップされる"""
    history = _make_history(
        ("2026-03", [
            ("7203", "トヨタ", 1000),
            ("9999", "存在しない銘柄", 500),  # current_prices にない
        ]),
    )
    current_prices = {"7203": 1100.0}  # 9999 は意図的に欠落

    result = calculate_performance(history, current_prices)
    m = result["monthly"][0]

    # 9999 はスキップ → picks は 1 件だけ
    assert m["pick_count"] == 1
    assert m["picks"][0]["code"] == "7203"


# ─────────────────────────────────────────────────────────────────────────────
# テスト 5: price_at_pick が記録されていない場合のフォールバック
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_price_at_pick_falls_back_to_yfinance():
    """price_at_pick が None の場合、yfinance で月末価格を取得しようとする"""
    history = {"themes": [
        {
            "name": "テーマX",
            "year_month": "2026-03",
            "icon": "📊",
            "stocks": [{"code": "7203", "name": "トヨタ", "rank": 1}],
            # price_at_pick フィールドなし（旧データ形式）
        }
    ]}
    current_prices = {"7203": 1200.0}

    with patch("src.utils.backtest.fetch_month_end_price", return_value=1000.0) as mock_fetch:
        result = calculate_performance(history, current_prices)
        mock_fetch.assert_called_once_with("7203", "2026-03")

    m = result["monthly"][0]
    assert m["pick_count"] == 1
    assert m["picks"][0]["price_at_pick"] == pytest.approx(1000.0)
    assert m["picks"][0]["return_pct"] == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# ボーナスケース: price_at_pick が None かつ yfinance も None → スキップ
# ─────────────────────────────────────────────────────────────────────────────

def test_price_at_pick_none_and_yfinance_none_skips():
    """price_at_pick も yfinance 取得も失敗した場合は計算をスキップする"""
    history = {"themes": [
        {
            "name": "テーマX",
            "year_month": "2026-03",
            "icon": "📊",
            "stocks": [{"code": "7203", "name": "トヨタ", "rank": 1}],
        }
    ]}
    current_prices = {"7203": 1200.0}

    with patch("src.utils.backtest.fetch_month_end_price", return_value=None):
        result = calculate_performance(history, current_prices)

    m = result["monthly"][0]
    assert m["pick_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# ユーティリティ: _to_yf_ticker
# ─────────────────────────────────────────────────────────────────────────────

def test_to_yf_ticker_japanese_stock():
    """日本株コードには .T サフィックスが付く"""
    assert _to_yf_ticker("7203") == "7203.T"
    assert _to_yf_ticker("8035") == "8035.T"


def test_to_yf_ticker_already_has_suffix():
    """すでにサフィックスがある場合は変更しない"""
    assert _to_yf_ticker("7203.T") == "7203.T"


# ─────────────────────────────────────────────────────────────────────────────
# _extract_monthly_entries: グループ化が正しい
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_monthly_entries_groups_by_month():
    history = {
        "themes": [
            {"name": "A", "year_month": "2026-03", "icon": "📊"},
            {"name": "B", "year_month": "2026-03", "icon": "📊"},
            {"name": "C", "year_month": "2026-04", "icon": "📊"},
        ]
    }
    entries = _extract_monthly_entries(history)
    assert len(entries) == 2
    assert entries[0]["year_month"] == "2026-03"
    assert len(entries[0]["themes"]) == 2
    assert entries[1]["year_month"] == "2026-04"
    assert len(entries[1]["themes"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# M4: 符号表示バグ — 負のリターンで "+-" が出ないこと
# ─────────────────────────────────────────────────────────────────────────────

def test_top_str_negative_return_no_plus_prefix():
    """top_performer のリターンが負の場合に '+' が付かないこと（M4）。"""
    monthly = [
        {
            "year_month": "2026-03",
            "pick_count": 2,
            "avg_return_pct": -5.0,
            "win_rate": 0.0,
            "top_performer": {"code": "7203", "name": "トヨタ", "return_pct": -5.5},
            "worst_performer": {"code": "8035", "name": "東エレク", "return_pct": -10.5},
            "picks": [],
        }
    ]
    html = _build_monthly_table_rows(monthly)
    assert "+-" not in html
    assert "-5.5%" in html
    assert "-10.5%" in html


def test_top_str_positive_return_has_plus_prefix():
    """top_performer のリターンが正の場合に '+' が付くこと（M4）。"""
    monthly = [
        {
            "year_month": "2026-03",
            "pick_count": 1,
            "avg_return_pct": 8.0,
            "win_rate": 1.0,
            "top_performer": {"code": "7203", "name": "トヨタ", "return_pct": 8.0},
            "worst_performer": {"code": "7203", "name": "トヨタ", "return_pct": 8.0},
            "picks": [],
        }
    ]
    html = _build_monthly_table_rows(monthly)
    assert "+8.0%" in html


# ─────────────────────────────────────────────────────────────────────────────
# M4: _format_pct 単体テスト（正・負・ゼロ）
# ─────────────────────────────────────────────────────────────────────────────

def test_format_pct_positive():
    """正のリターンには '+' 符号が付くこと。"""
    result = _format_pct(8.5, "トヨタ")
    assert result.startswith("+")
    assert "8.5%" in result
    assert "トヨタ" in result


def test_format_pct_negative():
    """負のリターンには '-' のみで '+' が付かないこと。"""
    result = _format_pct(-3.2, "東エレク")
    assert result.startswith("-")
    assert "+-" not in result
    assert "3.2%" in result
    assert "東エレク" in result


def test_format_pct_zero():
    """ゼロのリターンには '+' が付くこと（>= 0 の条件）。"""
    result = _format_pct(0.0, "銘柄A")
    assert result.startswith("+")
    assert "0.0%" in result
