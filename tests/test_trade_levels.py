"""売買プラン算出ロジック（trade_levels）のユニットテスト"""
import pytest

from src.utils.trade_levels import (
    GOOD_RR_THRESHOLD,
    compute_trade_levels,
)


# ─────────────────────────────────────────────────────────────────────────────
# 算出不能ケース
# ─────────────────────────────────────────────────────────────────────────────

class TestUncomputable:
    def test_none_price_returns_none(self):
        assert compute_trade_levels(None) is None

    def test_zero_price_returns_none(self):
        assert compute_trade_levels(0) is None

    def test_negative_price_returns_none(self):
        assert compute_trade_levels(-100) is None


# ─────────────────────────────────────────────────────────────────────────────
# 基本構造
# ─────────────────────────────────────────────────────────────────────────────

class TestBasicShape:
    def test_returns_all_keys(self):
        result = compute_trade_levels(1000.0)
        assert result is not None
        for key in (
            "entry_low", "entry_high", "stop_loss", "target",
            "risk_reward", "risk_reward_good", "rsi", "rsi_signal",
        ):
            assert key in result

    def test_no_technicals_uses_defaults(self):
        """テクニカル無しでも押し目・目標がデフォルトで算出される。"""
        result = compute_trade_levels(1000.0)
        assert result["entry_high"] == 1000
        # サポートは現値の -3%（=970）
        assert result["entry_low"] == 970
        # 目標は現値 +15%（=1150）
        assert result["target"] == 1150


# ─────────────────────────────────────────────────────────────────────────────
# 順序関係: stop < entry_low <= entry_high < target
# ─────────────────────────────────────────────────────────────────────────────

class TestOrdering:
    def test_levels_are_ordered(self):
        result = compute_trade_levels(
            1000.0,
            technicals={"ma25": 950.0, "ma75": 900.0, "rsi14": 55.0},
            price_52w_high=1300.0,
            price_52w_low=800.0,
        )
        assert result["stop_loss"] < result["entry_low"]
        assert result["entry_low"] <= result["entry_high"]
        assert result["entry_high"] < result["target"]

    def test_ma25_below_price_used_as_entry_low(self):
        result = compute_trade_levels(
            1000.0,
            technicals={"ma25": 950.0},
        )
        assert result["entry_low"] == 950

    def test_ma25_above_price_falls_back_to_pullback(self):
        """MA25 が現値より上なら押し目（-3%）を使う。"""
        result = compute_trade_levels(
            1000.0,
            technicals={"ma25": 1100.0},
        )
        assert result["entry_low"] == 970

    def test_ma75_below_stop_takes_precedence(self):
        """MA75 がサポート由来の損切りより下なら、より保守的な MA75 を採用。"""
        result = compute_trade_levels(
            1000.0,
            technicals={"ma25": 950.0, "ma75": 700.0},
        )
        # ma25*0.92 = 874 だが ma75=700 の方が下なので 700
        assert result["stop_loss"] == 700


# ─────────────────────────────────────────────────────────────────────────────
# 目標株価: 52週高値
# ─────────────────────────────────────────────────────────────────────────────

class TestTarget:
    def test_uses_52w_high_when_meaningfully_above(self):
        result = compute_trade_levels(1000.0, price_52w_high=1400.0)
        assert result["target"] == 1400

    def test_ignores_52w_high_when_too_close(self):
        """52週高値が現値の +3% 以内なら使わずデフォルト上昇率にする。"""
        result = compute_trade_levels(1000.0, price_52w_high=1010.0)
        assert result["target"] == 1150  # default +15%


# ─────────────────────────────────────────────────────────────────────────────
# リスクリワード比
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskReward:
    def test_risk_reward_computed(self):
        # entry=1000, stop=920(=1000*0.97*0.92≒892? )。明示的に検証
        result = compute_trade_levels(
            1000.0,
            technicals={"ma25": 1000.0},  # support=current*0.97=970, stop=970*0.92=892.4
            price_52w_high=1300.0,
        )
        # reward = 1300-1000 = 300, risk = 1000-892.4 = 107.6 → rr≒2.8
        assert result["risk_reward"] is not None
        assert result["risk_reward"] > 2.0

    def test_good_rr_flag_true(self):
        result = compute_trade_levels(1000.0, price_52w_high=1400.0)
        assert result["risk_reward"] >= GOOD_RR_THRESHOLD
        assert result["risk_reward_good"] is True

    def test_good_rr_flag_false_for_low_upside(self):
        """目標が近い（上昇余地小）と R/R は低くフラグは False。"""
        result = compute_trade_levels(1000.0, price_52w_high=1050.0)
        # target=デフォルト1150。reward=150, risk=1000-892.4=107.6 → rr≒1.4
        assert result["risk_reward_good"] is False


# ─────────────────────────────────────────────────────────────────────────────
# RSI シグナル
# ─────────────────────────────────────────────────────────────────────────────

class TestRsiSignal:
    def test_overbought(self):
        result = compute_trade_levels(1000.0, technicals={"rsi14": 75.0})
        assert result["rsi_signal"] == "過熱"

    def test_oversold(self):
        result = compute_trade_levels(1000.0, technicals={"rsi14": 25.0})
        assert result["rsi_signal"] == "売られすぎ"

    def test_neutral(self):
        result = compute_trade_levels(1000.0, technicals={"rsi14": 50.0})
        assert result["rsi_signal"] == "中立"

    def test_unknown_when_missing(self):
        result = compute_trade_levels(1000.0)
        assert result["rsi_signal"] == "不明"
        assert result["rsi"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 米国株（小数価格）の丸め
# ─────────────────────────────────────────────────────────────────────────────

class TestUsStockRounding:
    def test_small_price_two_decimals(self):
        result = compute_trade_levels(
            185.50,
            technicals={"ma25": 180.0, "rsi14": 60.0},
            price_52w_high=210.0,
        )
        assert result["entry_high"] == 185.5
        assert result["entry_low"] == 180.0
        assert result["target"] == 210
