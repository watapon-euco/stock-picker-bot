"""step2_report.build_trade_plan_section / step1 の生データマージのテスト"""
from src.step1_research import _merge_raw_into_structured
from src.step2_report import build_trade_plan_section


# ─────────────────────────────────────────────────────────────────────────────
# build_trade_plan_section
# ─────────────────────────────────────────────────────────────────────────────

def _theme(stocks):
    return [{"theme_name": "テストテーマ", "stocks": stocks}]


class TestBuildTradePlanSection:
    def test_renders_levels_for_valid_stock(self):
        data = _theme([{
            "code": "7203", "name": "トヨタ", "market": "JP",
            "current_price": 1000.0,
            "technicals": {"ma25": 950.0, "ma75": 900.0, "rsi14": 55.0},
            "52w_high": 1300.0, "52w_low": 800.0,
        }])
        out = build_trade_plan_section(data)
        assert "売買プラン" in out
        assert "トヨタ" in out
        assert "7203.JP" in out
        assert "エントリー" in out
        assert "損切り" in out
        assert "目標" in out

    def test_skips_stock_without_price(self):
        data = _theme([{"code": "9999", "name": "X", "current_price": None}])
        out = build_trade_plan_section(data)
        # 有効銘柄が無いテーマはブロックごと出力しない
        assert out == ""

    def test_us_stock_suffix_and_dollar(self):
        data = _theme([{
            "code": "AAPL", "name": "Apple", "market": "US",
            "current_price": 185.50,
            "technicals": {"ma25": 180.0, "rsi14": 60.0},
            "52w_high": 210.0,
        }])
        out = build_trade_plan_section(data)
        assert "AAPL.US" in out
        assert "$" in out

    def test_xss_in_name_escaped(self):
        data = _theme([{
            "code": "1234", "name": "<script>alert(1)</script>", "market": "JP",
            "current_price": 1000.0,
        }])
        out = build_trade_plan_section(data)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_overbought_rsi_flagged(self):
        data = _theme([{
            "code": "7203", "name": "トヨタ", "market": "JP",
            "current_price": 1000.0,
            "technicals": {"rsi14": 78.0},
        }])
        out = build_trade_plan_section(data)
        assert "過熱" in out

    def test_empty_stock_data_returns_empty(self):
        assert build_trade_plan_section([]) == ""


# ─────────────────────────────────────────────────────────────────────────────
# _merge_raw_into_structured（チャート・売買プランの前提となる生データ復元）
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeRawIntoStructured:
    def test_restores_missing_technicals_and_history(self):
        stocks = [{"code": "7203", "name": "トヨタ", "current_price": 1000.0}]
        raw = {"7203": {
            "technicals": {"ma25": 950.0},
            "price_history_6m": {"dates": ["2026-01-01"], "closes": [990.0]},
            "52w_high": 1300.0,
        }}
        _merge_raw_into_structured(stocks, raw)
        assert stocks[0]["technicals"] == {"ma25": 950.0}
        assert stocks[0]["price_history_6m"]["closes"] == [990.0]
        assert stocks[0]["52w_high"] == 1300.0

    def test_does_not_overwrite_existing(self):
        stocks = [{"code": "7203", "technicals": {"ma25": 1.0}}]
        raw = {"7203": {"technicals": {"ma25": 999.0}}}
        _merge_raw_into_structured(stocks, raw)
        assert stocks[0]["technicals"] == {"ma25": 1.0}

    def test_no_raw_for_code_is_safe(self):
        stocks = [{"code": "0000", "current_price": 1.0}]
        _merge_raw_into_structured(stocks, {})
        assert stocks[0]["current_price"] == 1.0
