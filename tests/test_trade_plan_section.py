"""step2_report.build_trade_plan_section / build_portfolio_guide_section /
step1 の生データマージのテスト"""
from src.step1_research import _merge_raw_into_structured
from src.step2_report import (
    build_portfolio_guide_section,
    build_trade_plan_section,
)


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
# build_portfolio_guide_section
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPortfolioGuideSection:
    def _theme(self, name, stocks):
        return {"theme_name": name, "stocks": stocks}

    def _stock(self, code, name, per, price=1000.0, source="yfinance", sector="電機"):
        return {
            "code": code, "name": name, "market": "JP", "per": per, "pbr": 1.2,
            "current_price": price, "sector": sector, "data_source": source,
            "price_history_6m": {"closes": [1000 + (i % 3) * 10 for i in range(30)]},
        }

    def test_empty_returns_empty(self):
        assert build_portfolio_guide_section([]) == ""

    def test_no_valid_price_returns_empty(self):
        data = [self._theme("AI", [{"code": "1", "name": "X", "current_price": None}])]
        assert build_portfolio_guide_section(data) == ""

    def test_renders_weights_summing_to_100(self):
        data = [self._theme("AI", [
            self._stock("1", "A", 10.0),
            self._stock("2", "B", 20.0),
            self._stock("3", "C", 30.0),
        ])]
        out = build_portfolio_guide_section(data)
        assert "ポートフォリオ・ガイド" in out
        assert "推奨配分" in out
        # 配分%の合計が100
        import re
        pcts = [int(m) for m in re.findall(r">(\d+)%</td>", out)]
        assert sum(pcts) == 100

    def test_relative_valuation_flag(self):
        data = [self._theme("AI", [
            self._stock("1", "Cheap", 8.0),
            self._stock("2", "Mid", 20.0),
            self._stock("3", "Pricey", 40.0),
        ])]
        out = build_portfolio_guide_section(data)
        assert "割安" in out
        assert "割高" in out

    def test_low_quality_badge_for_stooq(self):
        data = [self._theme("AI", [
            self._stock("1", "Good", 10.0),
            {"code": "2", "name": "Stooq銘柄", "market": "JP", "per": None,
             "pbr": None, "current_price": 500.0, "sector": "", "data_source": "stooq"},
        ])]
        out = build_portfolio_guide_section(data)
        assert "低" in out

    def test_duplicate_stock_warning(self):
        data = [
            self._theme("AI", [self._stock("6758", "ソニー", 15.0)]),
            self._theme("半導体", [self._stock("6758", "ソニー", 15.0)]),
        ]
        out = build_portfolio_guide_section(data)
        assert "銘柄重複" in out
        assert "ソニー" in out

    def test_xss_escaped(self):
        data = [self._theme("AI", [
            self._stock("1", "<script>x</script>", 10.0),
        ])]
        out = build_portfolio_guide_section(data)
        assert "<script>" not in out


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
