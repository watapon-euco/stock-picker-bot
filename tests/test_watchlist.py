"""ウォッチリスト機能のテスト"""
import json
import os
import tempfile
from unittest import mock

import pytest

from src.utils.watchlist_checker import evaluate_alerts


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: price_change_pct
# ─────────────────────────────────────────────────────────────────────────────

def test_price_change_pct_triggers_when_exceeded():
    """price_change_pct のしきい値を超えた場合にアラートメッセージが返ること。"""
    cfg = {"alerts": {"price_change_pct": 5.0}}
    # 2,850 → 3,027: +6.2%
    result = evaluate_alerts(cfg, current_price=3027, prev_price=2850, current_volume=0, avg_volume_5d=1.0)
    assert len(result) == 1
    assert "+6.2%" in result[0]
    assert "¥2,850" in result[0]
    assert "¥3,027" in result[0]


def test_price_change_pct_no_alert_when_within_threshold():
    """price_change_pct の範囲内の変動ではアラートが返らないこと。"""
    cfg = {"alerts": {"price_change_pct": 5.0}}
    # 3,000 → 3,100: +3.3%（しきい値以下）
    result = evaluate_alerts(cfg, current_price=3100, prev_price=3000, current_volume=0, avg_volume_5d=1.0)
    assert result == []


def test_price_change_pct_triggers_on_negative_change():
    """価格が下落方向でしきい値を超えた場合もアラートが返ること。"""
    cfg = {"alerts": {"price_change_pct": 5.0}}
    # 3,000 → 2,700: -10%
    result = evaluate_alerts(cfg, current_price=2700, prev_price=3000, current_volume=0, avg_volume_5d=1.0)
    assert len(result) == 1
    assert "-10.0%" in result[0]


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: price_above / price_below
# ─────────────────────────────────────────────────────────────────────────────

def test_price_above_triggers_when_exceeded():
    """price_above のしきい値を超えた価格でアラートが返ること。"""
    cfg = {"alerts": {"price_above": 3500}}
    result = evaluate_alerts(cfg, current_price=3600, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    assert len(result) == 1
    assert "¥3,500" in result[0]
    assert "¥3,600" in result[0]


def test_price_above_no_alert_when_at_threshold():
    """price_above のしきい値ちょうどではアラートが返らないこと（超過のみ）。"""
    cfg = {"alerts": {"price_above": 3500}}
    result = evaluate_alerts(cfg, current_price=3500, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    assert result == []


def test_price_below_triggers_when_fallen():
    """price_below のしきい値を下回った場合にアラートが返ること。"""
    cfg = {"alerts": {"price_below": 2500}}
    result = evaluate_alerts(cfg, current_price=2400, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    assert len(result) == 1
    assert "¥2,500" in result[0]
    assert "¥2,400" in result[0]


def test_price_below_no_alert_when_at_threshold():
    """price_below のしきい値ちょうどではアラートが返らないこと。"""
    cfg = {"alerts": {"price_below": 2500}}
    result = evaluate_alerts(cfg, current_price=2500, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: volume_spike_ratio
# ─────────────────────────────────────────────────────────────────────────────

def test_volume_spike_triggers_when_exceeded():
    """出来高が直近5日平均の volume_spike_ratio 倍を超えた場合にアラートが返ること。"""
    cfg = {"alerts": {"volume_spike_ratio": 2.0}}
    # current_volume=2.5, avg_volume_5d=1.0 → ratio=2.5 > 2.0
    result = evaluate_alerts(cfg, current_price=3000, prev_price=None, current_volume=2.5, avg_volume_5d=1.0)
    assert len(result) == 1
    assert "2.5 倍" in result[0]


def test_volume_spike_no_alert_when_within_ratio():
    """出来高が volume_spike_ratio 以下の場合はアラートが返らないこと。"""
    cfg = {"alerts": {"volume_spike_ratio": 2.0}}
    result = evaluate_alerts(cfg, current_price=3000, prev_price=None, current_volume=1.8, avg_volume_5d=1.0)
    assert result == []


def test_volume_spike_uses_actual_volume_values():
    """当日出来高 / 10日平均出来高の実数値で判定できること（C3 選択肢A）。"""
    cfg = {"alerts": {"volume_spike_ratio": 2.0}}
    # 当日: 3,000,000株、10日平均: 1,000,000株 → ratio=3.0 > 2.0
    result = evaluate_alerts(
        cfg, current_price=3000, prev_price=None,
        current_volume=3_000_000, avg_volume_5d=1_000_000,
    )
    assert len(result) == 1
    assert "3.0 倍" in result[0]


def test_volume_spike_skipped_when_avg_volume_zero():
    """avg_volume が 0（取得失敗）の場合にアラートが発火しないこと（C3 フォールバック）。"""
    cfg = {"alerts": {"volume_spike_ratio": 2.0}}
    result = evaluate_alerts(
        cfg, current_price=3000, prev_price=None,
        current_volume=5_000_000, avg_volume_5d=0,
    )
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: 複数アラート同時発火
# ─────────────────────────────────────────────────────────────────────────────

def test_multiple_alerts_triggered_simultaneously():
    """複数のアラート条件が同時に該当する場合、すべてのメッセージが返ること。"""
    cfg = {
        "alerts": {
            "price_change_pct": 5.0,
            "price_above": 3500,
            "volume_spike_ratio": 2.0,
        }
    }
    # price: 2,850 → 3,600 (+26.3%)、price_above=3500 超、volume ratio=3.0
    result = evaluate_alerts(
        cfg,
        current_price=3600,
        prev_price=2850,
        current_volume=3.0,
        avg_volume_5d=1.0,
    )
    assert len(result) == 3


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: 該当なし
# ─────────────────────────────────────────────────────────────────────────────

def test_no_alerts_returns_empty_list():
    """アラート条件が1つも設定されていない場合に空リストが返ること。"""
    cfg = {"alerts": {}}
    result = evaluate_alerts(cfg, current_price=3000, prev_price=2950, current_volume=1.0, avg_volume_5d=1.0)
    assert result == []


def test_no_alerts_key_returns_empty_list():
    """alerts キー自体が存在しない場合も空リストが返ること。"""
    cfg = {}
    result = evaluate_alerts(cfg, current_price=3000, prev_price=2950, current_volume=1.0, avg_volume_5d=1.0)
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_alerts: 前回価格が None（初回チェック）
# ─────────────────────────────────────────────────────────────────────────────

def test_first_check_skips_price_change_pct():
    """prev_price が None（初回チェック）の場合、price_change_pct はスキップされること。"""
    cfg = {"alerts": {"price_change_pct": 5.0}}
    result = evaluate_alerts(cfg, current_price=3600, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    assert result == []


def test_first_check_still_evaluates_absolute_thresholds():
    """初回チェックでも price_above / price_below は評価されること。"""
    cfg = {"alerts": {"price_above": 3500, "price_below": 2000}}
    result = evaluate_alerts(cfg, current_price=3600, prev_price=None, current_volume=0, avg_volume_5d=1.0)
    # price_above=3500 のみ該当（3,600 > 3,500）
    assert len(result) == 1
    assert "¥3,500" in result[0]


# ─────────────────────────────────────────────────────────────────────────────
# watchlist.json の読み書きラウンドトリップ（アトミック書き込み確認）
# ─────────────────────────────────────────────────────────────────────────────

def test_watchlist_roundtrip_with_atomic_write():
    """atomic_write_json で書いたデータを読み戻すとラウンドトリップできること。"""
    from src.utils.helpers import atomic_write_json

    sample = {
        "stocks": [
            {
                "code": "6758",
                "name": "ソニー",
                "added_date": "2026-05-17",
                "note": "テスト用",
                "alerts": {"price_change_pct": 3.0},
            }
        ],
        "last_check": "2026-05-17T09:00:00+00:00",
        "last_prices": {"6758": 12345.0},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "watchlist.json")
        atomic_write_json(path, sample)

        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)

    assert loaded["stocks"][0]["code"] == "6758"
    assert loaded["last_prices"]["6758"] == 12345.0
    assert loaded["last_check"] == "2026-05-17T09:00:00+00:00"


def test_atomic_write_no_partial_file_on_error():
    """atomic_write_json が失敗しても元ファイルが壊れないこと。"""
    from src.utils.helpers import atomic_write_json

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "watchlist.json")
        original = {"stocks": [], "last_check": None, "last_prices": {}}
        atomic_write_json(path, original)

        # json.dump が失敗するオブジェクトを渡すと例外が起きるが、元ファイルは残る
        class _Unserializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(path, {"bad": _Unserializable()})

        # 元ファイルが intact であること
        with open(path, encoding="utf-8") as f:
            recovered = json.load(f)
        assert recovered == original
