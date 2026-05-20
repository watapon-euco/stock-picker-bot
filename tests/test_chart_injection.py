"""ensure_chart_canvas_in_ranking_html フォールバック関数、および build_chart_init_script XSS 対策のテスト"""
import re

import pytest

from src.step2_report import build_chart_init_script, ensure_chart_canvas_in_ranking_html


# ─────────────────────────────────────────────────────────────────────────────
# XSS 回帰テスト: build_chart_init_script が </script> をエスケープすること
# ─────────────────────────────────────────────────────────────────────────────

def test_script_tag_in_closes_is_escaped():
    """price_history_6m の closes 値に </script> が含まれてもそのまま出力されないこと。"""
    stock_data = [
        {
            "theme_name": "テスト",
            "stocks": [
                {
                    "code": "9999",
                    "price_history_6m": {
                        "dates": ["2024-01-01"],
                        "closes": ["</script><script>alert(1)</script>"],
                    },
                }
            ],
        }
    ]

    result = build_chart_init_script(stock_data)

    # Data is escaped to </script> — only the block's own closing tag remains
    assert result.count("</script>") == 1
    assert "\\u003c/script>" in result


def test_script_tag_in_dates_is_escaped():
    """price_history_6m の dates 値に </script> が含まれてもそのまま出力されないこと。"""
    stock_data = [
        {
            "theme_name": "テスト",
            "stocks": [
                {
                    "code": "8888",
                    "price_history_6m": {
                        "dates": ["</script>"],
                        "closes": [100],
                    },
                }
            ],
        }
    ]

    result = build_chart_init_script(stock_data)

    # Data is escaped to </script> — only the block's own closing tag remains
    assert result.count("</script>") == 1
    assert "\\u003c/script>" in result


def test_normal_data_produces_no_spurious_escapes():
    """通常のデータでは意図しないエスケープが発生しないこと。"""
    stock_data = [
        {
            "theme_name": "テスト",
            "stocks": [
                {
                    "code": "7203",
                    "price_history_6m": {
                        "dates": ["2024-01-01", "2024-02-01"],
                        "closes": [1000, 1100],
                    },
                }
            ],
        }
    ]

    result = build_chart_init_script(stock_data)

    assert "7203" in result
    assert "1000" in result
    assert "1100" in result


def _make_card_detail(code: str, include_canvas: bool) -> str:
    """テスト用の stock-card HTML を生成するヘルパー"""
    canvas_block = (
        f'\n        <div class="price-chart-wrapper">'
        f'\n          <div class="price-chart-label">過去6ヶ月の株価推移</div>'
        f'\n          <canvas class="price-chart-canvas" data-stock-code="{code}"></canvas>'
        f'\n        </div>'
        if include_canvas
        else ""
    )
    return (
        f'<div class="stock-card rank-1" data-stock-code="{code}">'
        f'<div class="card-header"><div class="rank-badge">1</div></div>'
        f'<div class="card-detail">'
        f'<div class="detail-grid"><div class="detail-item">dummy</div></div>'
        f'{canvas_block}'
        f'</div>'
        f'</div>'
    )


def _make_stock_data(codes: list) -> list:
    return [{"theme_name": "テスト", "stocks": [{"code": c} for c in codes]}]


# ─────────────────────────────────────────────────────────────────────────────
# Case 1: canvas が既に全銘柄に存在する → 変更なし
# ─────────────────────────────────────────────────────────────────────────────

def test_canvas_already_present_no_change():
    code = "7013"
    html = _make_card_detail(code, include_canvas=True)
    stock_data = _make_stock_data([code])

    result = ensure_chart_canvas_in_ranking_html(html, stock_data)

    canvas_count = result.count(f'data-stock-code="{code}"')
    # canvas ブロック内の data-stock-code + stock-card の data-stock-code = 2
    # canvas が重複挿入されていないことを確認
    assert canvas_count == 2  # stock-card attribute + canvas attribute


# ─────────────────────────────────────────────────────────────────────────────
# Case 2: canvas が全く存在しない → 全銘柄に挿入される
# ─────────────────────────────────────────────────────────────────────────────

def test_canvas_absent_gets_injected():
    code = "4204"
    html = _make_card_detail(code, include_canvas=False)
    stock_data = _make_stock_data([code])

    result = ensure_chart_canvas_in_ranking_html(html, stock_data)

    assert f'<canvas class="price-chart-canvas" data-stock-code="{code}"></canvas>' in result
    assert 'price-chart-wrapper' in result


# ─────────────────────────────────────────────────────────────────────────────
# Case 3: 複数銘柄のうち一部だけ canvas が欠落している（部分欠落）
# ─────────────────────────────────────────────────────────────────────────────

def test_partial_canvas_missing_only_missing_gets_injected():
    code_with = "7203"
    code_without = "6758"

    html = (
        _make_card_detail(code_with, include_canvas=True)
        + "\n"
        + _make_card_detail(code_without, include_canvas=False)
    )
    stock_data = _make_stock_data([code_with, code_without])

    result = ensure_chart_canvas_in_ranking_html(html, stock_data)

    # canvas が2つになっているはず（既存1 + 追加1）
    canvas_tags = re.findall(r'<canvas[^>]+class="price-chart-canvas"[^>]*>', result)
    assert len(canvas_tags) == 2

    # code_without に canvas が追加されている
    assert f'data-stock-code="{code_without}"' in "".join(canvas_tags)

    # code_with の canvas は重複していない
    with_count = sum(1 for t in canvas_tags if f'data-stock-code="{code_with}"' in t)
    assert with_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Case 4: stock_data が空 → HTML は変更されない
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_stock_data_returns_html_unchanged():
    html = "<div>some html</div>"
    result = ensure_chart_canvas_in_ranking_html(html, [])
    assert result == html


# ─────────────────────────────────────────────────────────────────────────────
# Case 5: card-detail 数と銘柄数が一致しない（警告してHTMLをそのまま返す）
# ─────────────────────────────────────────────────────────────────────────────

def test_mismatch_count_returns_html_unchanged():
    code = "1234"
    # card-detail は1つだが stock_data は2銘柄
    html = _make_card_detail(code, include_canvas=False)
    stock_data = _make_stock_data([code, "5678"])

    result = ensure_chart_canvas_in_ranking_html(html, stock_data)

    # 件数ミスマッチ時はそのまま返す（挿入しない）
    assert result == html
