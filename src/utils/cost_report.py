"""
月別コストレポート CLI

使い方:
    python -m src.utils.cost_report
    python -m src.utils.cost_report --month 2026-05
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from src.utils.cost_logger import COST_LOG_PATH


def _load_events(month: str) -> List[Dict[str, Any]]:
    """JSONL コストログから指定月のイベントを読み込む。"""
    if not os.path.exists(COST_LOG_PATH):
        return []

    events = []
    with open(COST_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = event.get("timestamp", "")
            if ts.startswith(month):
                events.append(event)
    return events


def _aggregate(events: List[Dict[str, Any]]) -> List[Tuple]:
    """
    (provider, operation) をキーに集計する。

    Returns:
        [(provider, operation, calls, total_in, total_out, total_usd, total_jpy), ...]
    """
    rows: Dict[Tuple[str, str], Dict] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                 "cost_usd": 0.0, "cost_jpy": 0.0}
    )
    for ev in events:
        key = (ev.get("provider", ""), ev.get("operation", ""))
        r = rows[key]
        r["calls"] += 1
        r["input_tokens"] += ev.get("input_tokens", 0)
        r["output_tokens"] += ev.get("output_tokens", 0)
        r["cost_usd"] += ev.get("estimated_cost_usd", 0.0)
        r["cost_jpy"] += ev.get("estimated_cost_jpy", 0.0)

    result = []
    for (provider, operation), r in sorted(rows.items()):
        result.append((
            provider,
            operation,
            r["calls"],
            r["input_tokens"],
            r["output_tokens"],
            round(r["cost_usd"], 6),
            round(r["cost_jpy"], 2),
        ))
    return result


def _fmt_tokens(in_tok: int, out_tok: int, provider: str) -> str:
    if provider == "yfinance":
        return "-"
    return f"{in_tok:>10,} / {out_tok:<10,}"


def _fmt_cost_usd(usd: float, provider: str) -> str:
    if provider == "yfinance":
        return "-"
    return f"${usd:.3f}"


def _fmt_cost_jpy(jpy: float, provider: str) -> str:
    if provider == "yfinance":
        return "-"
    return f"¥{jpy:.1f}"


def print_report(month: str) -> None:
    events = _load_events(month)
    rows = _aggregate(events)

    print(f"\n=== Cost Report: {month} ===")
    header = f"{'Provider':<12} {'Operation':<22} {'Calls':>6}  {'Tokens(in/out)':>24}  {'Cost(USD)':>10}  {'Cost(JPY)':>10}"
    print(header)
    print("-" * len(header))

    total_usd = 0.0
    total_jpy = 0.0
    total_calls = 0

    for provider, operation, calls, in_tok, out_tok, usd, jpy in rows:
        tokens_str = _fmt_tokens(in_tok, out_tok, provider)
        usd_str = _fmt_cost_usd(usd, provider)
        jpy_str = _fmt_cost_jpy(jpy, provider)
        print(
            f"{provider:<12} {operation:<22} {calls:>6}  {tokens_str:>24}  {usd_str:>10}  {jpy_str:>10}"
        )
        total_usd += usd
        total_jpy += jpy
        total_calls += calls

    print("-" * len(header))
    print(
        f"{'Total':<36} {total_calls:>6}  {'':>24}  ${total_usd:.3f}    ¥{total_jpy:.1f}"
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="API コストレポートを表示する")
    parser.add_argument(
        "--month",
        default=datetime.now(timezone.utc).strftime("%Y-%m"),
        help="集計対象月 (YYYY-MM)。省略時は当月。",
    )
    args = parser.parse_args()
    print_report(args.month)


if __name__ == "__main__":
    main()
