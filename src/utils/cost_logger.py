"""API 呼び出しコスト・実行ログ記録ユーティリティ"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config import CLAUDE_MODEL, GEMINI_MODEL

logger = logging.getLogger(__name__)

# data/cost_log.json への絶対パス
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COST_LOG_PATH = os.path.join(_PROJECT_ROOT, "data", "cost_log.json")

# 価格表 USD / 1M トークン
# 将来の価格変更はここだけ更新すればよい
PRICING: Dict[str, Dict[str, float]] = {
    "gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40},
    "claude-sonnet-4-6": {"input": 1.50, "output": 7.50},
}

USD_TO_JPY = 150.0

def _validate_pricing_config() -> None:
    """config.py のモデル名が PRICING テーブルに存在するか起動時に確認する。"""
    for provider_label, model in (("gemini", GEMINI_MODEL), ("claude", CLAUDE_MODEL)):
        if model not in PRICING:
            logger.warning(
                f"cost_logger: {provider_label} model '{model}' is not in PRICING table. "
                "Cost calculations for this model will return 0. "
                "Update PRICING in cost_logger.py to fix this."
            )

_validate_pricing_config()


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """モデル名とトークン数から USD コストを計算する。"""
    price = PRICING.get(model)
    if price is None:
        return 0.0
    cost = (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]
    return round(cost, 6)


def log_api_call(
    provider: str,
    model: str,
    operation: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_sec: float = 0.0,
    success: bool = True,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    API 呼び出し 1 回分をコストログに追記する。

    ログ書き込みが失敗してもメインパイプラインには影響しない（例外を飲み込む）。

    Args:
        provider: "gemini" | "claude" | "yfinance"
        model: モデル識別子（PRICING のキーと一致させること）
        operation: 任意の操作名（例 "theme_extraction"）
        input_tokens: 入力トークン数
        output_tokens: 出力トークン数
        duration_sec: 実行時間（秒）
        success: 呼び出し成否
        extra: 追加情報（任意の dict）
    """
    try:
        estimated_cost_usd = _calc_cost(model, input_tokens, output_tokens)
        event: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "operation": operation,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_sec": round(duration_sec, 3),
            "success": success,
            "estimated_cost_usd": estimated_cost_usd,
            "estimated_cost_jpy": round(estimated_cost_usd * USD_TO_JPY, 4),
        }
        if extra:
            event["extra"] = extra

        _append_event(event)
    except Exception:
        logger.warning("cost_logger: failed to write log entry", exc_info=True)


def _append_event(event: Dict[str, Any]) -> None:
    """
    JSONL 形式（1行1イベント）でファイルへ追記する。

    シングルプロセス内での逐次呼び出しは安全。
    複数プロセスが同時に append する場合、OS の append モードは個々の write(2) システムコールを
    アトミックに扱うが、Python の f.write() が単一 write(2) に対応する保証はないため、
    複数プロセスの並列書き込みでは行が混入する可能性がある。
    ファイルが存在しない場合は自動生成する。
    """
    os.makedirs(os.path.dirname(COST_LOG_PATH), exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(COST_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
