"""cost_logger / cost_report の動作確認テスト"""
import json
import os
import tempfile
from unittest import mock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# cost_logger
# ─────────────────────────────────────────────────────────────────────────────

def _make_logger_with_tmp(tmp_path):
    """テスト用の一時ログパスを持つ cost_logger モジュールを返す。"""
    import src.utils.cost_logger as cl
    log_path = str(tmp_path / "cost_log.json")
    with mock.patch.object(cl, "COST_LOG_PATH", log_path):
        yield cl, log_path


@pytest.fixture()
def tmp_log(tmp_path):
    import src.utils.cost_logger as cl
    log_path = str(tmp_path / "cost_log.json")
    with mock.patch.object(cl, "COST_LOG_PATH", log_path):
        yield cl, log_path


def test_log_api_call_creates_file(tmp_log):
    cl, log_path = tmp_log
    cl.log_api_call(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        operation="test_op",
        input_tokens=1000,
        output_tokens=500,
        duration_sec=1.2,
        success=True,
    )
    assert os.path.exists(log_path)


def test_log_api_call_appends_valid_json(tmp_log):
    cl, log_path = tmp_log
    cl.log_api_call(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        operation="test_op",
        input_tokens=1000,
        output_tokens=500,
        duration_sec=1.2,
        success=True,
    )
    with open(log_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["provider"] == "gemini"
    assert event["operation"] == "test_op"
    assert event["input_tokens"] == 1000
    assert event["output_tokens"] == 500
    assert event["success"] is True
    assert "timestamp" in event
    assert "estimated_cost_usd" in event
    assert "estimated_cost_jpy" in event


def test_log_api_call_multiple_appends(tmp_log):
    cl, log_path = tmp_log
    for i in range(3):
        cl.log_api_call(
            provider="claude",
            model="claude-sonnet-4-6",
            operation=f"op_{i}",
            input_tokens=100 * (i + 1),
            output_tokens=50 * (i + 1),
            duration_sec=0.5,
            success=True,
        )
    with open(log_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) == 3


def test_cost_calculation_gemini(tmp_log):
    cl, log_path = tmp_log
    # gemini: $0.10/1M input, $0.40/1M output
    # 1,000,000 input + 1,000,000 output = $0.10 + $0.40 = $0.50
    cl.log_api_call(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        operation="cost_test",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        success=True,
    )
    with open(log_path, encoding="utf-8") as f:
        event = json.loads(f.readline())
    assert abs(event["estimated_cost_usd"] - 0.50) < 1e-5
    assert abs(event["estimated_cost_jpy"] - 0.50 * 150) < 0.01


def test_cost_calculation_claude(tmp_log):
    cl, log_path = tmp_log
    # claude: $1.50/1M input, $7.50/1M output
    cl.log_api_call(
        provider="claude",
        model="claude-sonnet-4-6",
        operation="cost_test",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        success=True,
    )
    with open(log_path, encoding="utf-8") as f:
        event = json.loads(f.readline())
    assert abs(event["estimated_cost_usd"] - 9.00) < 1e-5


def test_yfinance_zero_cost(tmp_log):
    cl, log_path = tmp_log
    cl.log_api_call(
        provider="yfinance",
        model="",
        operation="stock_data",
        input_tokens=0,
        output_tokens=0,
        success=True,
    )
    with open(log_path, encoding="utf-8") as f:
        event = json.loads(f.readline())
    assert event["estimated_cost_usd"] == 0.0


def test_log_api_call_with_extra(tmp_log):
    cl, log_path = tmp_log
    cl.log_api_call(
        provider="claude",
        model="claude-sonnet-4-6",
        operation="batch_submit",
        success=True,
        extra={"batch_id": "abc123"},
    )
    with open(log_path, encoding="utf-8") as f:
        event = json.loads(f.readline())
    assert event["extra"]["batch_id"] == "abc123"


def test_log_api_call_does_not_raise_on_write_error(tmp_path):
    """書き込みエラーが起きてもメインコードに例外が伝播しないこと。"""
    import src.utils.cost_logger as cl
    # 存在しない読み取り専用パスを指定して書き込み失敗を起こす
    with mock.patch.object(cl, "COST_LOG_PATH", "/nonexistent_dir/cost_log.json"):
        with mock.patch("os.makedirs", side_effect=OSError("permission denied")):
            cl.log_api_call(
                provider="gemini",
                model="gemini-3.1-flash-lite-preview",
                operation="should_not_raise",
                success=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# cost_report
# ─────────────────────────────────────────────────────────────────────────────

def _write_events(log_path, events):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_cost_report_aggregates_correctly(tmp_path, capsys):
    import src.utils.cost_report as cr

    log_path = str(tmp_path / "cost_log.json")
    events = [
        {
            "timestamp": "2026-05-01T00:00:00+00:00",
            "provider": "gemini",
            "operation": "theme_extraction",
            "input_tokens": 10000,
            "output_tokens": 2000,
            "estimated_cost_usd": 0.001,
            "estimated_cost_jpy": 0.15,
            "success": True,
        },
        {
            "timestamp": "2026-05-02T00:00:00+00:00",
            "provider": "gemini",
            "operation": "theme_extraction",
            "input_tokens": 5000,
            "output_tokens": 1000,
            "estimated_cost_usd": 0.0005,
            "estimated_cost_jpy": 0.075,
            "success": True,
        },
        {
            "timestamp": "2026-04-30T00:00:00+00:00",  # 別月 → 除外
            "provider": "gemini",
            "operation": "theme_extraction",
            "input_tokens": 9999,
            "output_tokens": 9999,
            "estimated_cost_usd": 99.0,
            "estimated_cost_jpy": 9900.0,
            "success": True,
        },
    ]
    _write_events(log_path, events)

    with mock.patch.object(cr, "COST_LOG_PATH", log_path):
        cr.print_report("2026-05")

    captured = capsys.readouterr().out
    assert "2026-05" in captured
    assert "gemini" in captured
    # 別月イベントのコストが合計に混入していないこと
    assert "99" not in captured


def test_cost_report_no_file(tmp_path, capsys):
    import src.utils.cost_report as cr

    log_path = str(tmp_path / "missing.json")
    with mock.patch.object(cr, "COST_LOG_PATH", log_path):
        cr.print_report("2026-05")

    captured = capsys.readouterr().out
    assert "2026-05" in captured
    assert "Total" in captured
