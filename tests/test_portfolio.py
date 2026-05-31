"""ポートフォリオ構築支援（portfolio）のユニットテスト"""
import pytest

from src.utils.portfolio import (
    assess_data_quality,
    compute_volatility,
    find_duplicate_stocks,
    suggest_position_weights,
    theme_relative_valuation,
)


# ─────────────────────────────────────────────────────────────────────────────
# compute_volatility
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeVolatility:
    def test_none_input(self):
        assert compute_volatility(None) is None

    def test_too_few_points(self):
        assert compute_volatility({"closes": [100, 101, 102]}) is None

    def test_flat_series_zero_vol(self):
        vol = compute_volatility({"closes": [100.0] * 20})
        assert vol == 0.0

    def test_volatile_series_positive(self):
        closes = [100, 110, 95, 120, 90, 130, 85, 125, 100, 115, 92, 121]
        vol = compute_volatility({"closes": closes})
        assert vol is not None
        assert vol > 0

    def test_more_volatile_higher(self):
        calm = compute_volatility({"closes": [100 + (i % 2) for i in range(20)]})
        wild = compute_volatility({"closes": [100 + (i % 2) * 30 for i in range(20)]})
        assert wild > calm


# ─────────────────────────────────────────────────────────────────────────────
# suggest_position_weights
# ─────────────────────────────────────────────────────────────────────────────

class TestSuggestPositionWeights:
    def test_empty(self):
        assert suggest_position_weights([]) == []

    def test_sums_to_100(self):
        items = [
            {"rank": 1, "volatility": 2.0},
            {"rank": 2, "volatility": 3.0},
            {"rank": 3, "volatility": 1.5},
        ]
        weights = suggest_position_weights(items)
        assert sum(weights) == 100

    def test_higher_rank_gets_more(self):
        # キャップが binding しない現実的な銘柄数（4件）で順位効果を検証
        items = [
            {"rank": 1, "volatility": 2.0},
            {"rank": 2, "volatility": 2.0},
            {"rank": 3, "volatility": 2.0},
            {"rank": 4, "volatility": 2.0},
        ]
        weights = suggest_position_weights(items)
        assert weights[0] >= weights[1] >= weights[2] >= weights[3]
        assert weights[0] > weights[3]

    def test_lower_vol_gets_more_at_same_rank(self):
        items = [
            {"rank": 1, "volatility": 1.0},
            {"rank": 1, "volatility": 2.0},
            {"rank": 1, "volatility": 4.0},
            {"rank": 1, "volatility": 8.0},
        ]
        weights = suggest_position_weights(items)
        assert weights[0] > weights[3]

    def test_max_weight_cap_respected(self):
        # 1銘柄が極端に高確信・低ボラでも上限30%以内
        items = [
            {"rank": 1, "volatility": 0.1},
            {"rank": 2, "volatility": 5.0},
            {"rank": 3, "volatility": 5.0},
            {"rank": 4, "volatility": 5.0},
            {"rank": 5, "volatility": 5.0},
        ]
        weights = suggest_position_weights(items, max_weight=0.30)
        assert max(weights) <= 31  # 丸め誤差を許容
        assert sum(weights) == 100

    def test_missing_volatility_uses_fallback(self):
        items = [
            {"rank": 1, "volatility": 2.0},
            {"rank": 2, "volatility": None},
        ]
        weights = suggest_position_weights(items)
        assert sum(weights) == 100
        assert all(w >= 0 for w in weights)


# ─────────────────────────────────────────────────────────────────────────────
# theme_relative_valuation
# ─────────────────────────────────────────────────────────────────────────────

class TestThemeRelativeValuation:
    def test_insufficient_data_all_none(self):
        stocks = [{"code": "1", "per": 15.0}, {"code": "2", "per": None}]
        result = theme_relative_valuation(stocks)
        assert result == {"1": None, "2": None}

    def test_cheap_and_expensive_flagged(self):
        stocks = [
            {"code": "A", "per": 8.0},    # 中央値20より大幅安 → 割安
            {"code": "B", "per": 20.0},   # 中央値 → 中立
            {"code": "C", "per": 40.0},   # 中央値より大幅高 → 割高
        ]
        result = theme_relative_valuation(stocks)
        assert result["A"] == "割安"
        assert result["B"] == "中立"
        assert result["C"] == "割高"

    def test_missing_per_is_none_even_when_others_present(self):
        stocks = [
            {"code": "A", "per": 10.0},
            {"code": "B", "per": 20.0},
            {"code": "C", "per": None},
        ]
        result = theme_relative_valuation(stocks)
        assert result["C"] is None


# ─────────────────────────────────────────────────────────────────────────────
# assess_data_quality
# ─────────────────────────────────────────────────────────────────────────────

class TestAssessDataQuality:
    def test_high_quality(self):
        stock = {"per": 15.0, "pbr": 1.2, "sector": "電機", "current_price": 1000.0,
                 "data_source": "yfinance"}
        result = assess_data_quality(stock)
        assert result["level"] == "高"
        assert result["reasons"] == []

    def test_stooq_is_low(self):
        stock = {"per": None, "pbr": None, "sector": "", "current_price": 1000.0,
                 "data_source": "stooq"}
        result = assess_data_quality(stock)
        assert result["level"] == "低"
        assert any("Stooq" in r for r in result["reasons"])

    def test_missing_valuation_is_low(self):
        stock = {"per": None, "pbr": None, "sector": "電機", "current_price": 1000.0}
        result = assess_data_quality(stock)
        assert result["level"] == "低"


# ─────────────────────────────────────────────────────────────────────────────
# find_duplicate_stocks
# ─────────────────────────────────────────────────────────────────────────────

class TestFindDuplicateStocks:
    def test_no_duplicates(self):
        data = [
            {"theme_name": "AI", "stocks": [{"code": "1", "name": "A"}]},
            {"theme_name": "EV", "stocks": [{"code": "2", "name": "B"}]},
        ]
        assert find_duplicate_stocks(data) == []

    def test_detects_cross_theme_duplicate(self):
        data = [
            {"theme_name": "AI", "stocks": [{"code": "6758", "name": "ソニー"}]},
            {"theme_name": "半導体", "stocks": [{"code": "6758", "name": "ソニー"}]},
        ]
        result = find_duplicate_stocks(data)
        assert len(result) == 1
        assert result[0]["code"] == "6758"
        assert set(result[0]["themes"]) == {"AI", "半導体"}

    def test_same_theme_not_duplicate(self):
        data = [
            {"theme_name": "AI", "stocks": [
                {"code": "6758", "name": "ソニー"},
                {"code": "6758", "name": "ソニー"},
            ]},
        ]
        assert find_duplicate_stocks(data) == []
