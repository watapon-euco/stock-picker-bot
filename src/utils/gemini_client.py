"""Gemini API ラッパー（google-generativeai SDK）"""
import json
import logging
import re
import time
from typing import Any, Optional

import google.generativeai as genai

from src.config import GEMINI_MODEL

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
RETRY_BASE_DELAY = 2  # seconds


class GeminiClient:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self._model_text = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )
        self._model_json = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )

    def generate(self, prompt: str, json_mode: bool = False) -> str:
        """
        テキスト生成。失敗時はexponential backoffでリトライ。
        レート制限エラーは通常エラーより長いウェイトを適用する。

        Args:
            prompt: プロンプト文字列
            json_mode: True のとき JSON レスポンスを要求

        Returns:
            生成されたテキスト（json_mode=True の場合はJSON文字列）
        """
        model = self._model_json if json_mode else self._model_text
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                response = model.generate_content(prompt)
                text = response.text.strip()
                logger.debug(f"Gemini response ({len(text)} chars)")
                return text
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # レート制限（429）は通常より長いウェイト
                is_rate_limit = "429" in err_str or "quota" in err_str or "rate" in err_str
                multiplier = 4 if is_rate_limit else 1
                delay = RETRY_BASE_DELAY * (2 ** attempt) * multiplier
                logger.warning(
                    f"Gemini API error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)

        raise RuntimeError(
            f"Gemini API failed after {MAX_RETRIES} retries: {last_error}"
        )

    def generate_json(self, prompt: str) -> Any:
        """
        JSON を生成してパースして返す。
        パース失敗時は明示的なJSON指示を付けて1回リトライする。

        Returns:
            パース済みのPythonオブジェクト
        """
        raw = self.generate(prompt, json_mode=True)
        parsed = _try_parse_json(raw)
        if parsed is not None:
            return parsed

        # 1回目パース失敗 → 明示的JSON指示でリトライ
        logger.warning("JSON parse failed on first attempt, retrying with explicit JSON instruction")
        retry_prompt = prompt + "\n\n重要: 有効なJSONのみを出力してください。説明文・コードフェンス不要。"
        raw2 = self.generate(retry_prompt, json_mode=True)
        parsed2 = _try_parse_json(raw2)
        if parsed2 is not None:
            return parsed2

        logger.error(f"Failed to parse Gemini JSON after retry. Raw: {raw[:500]}")
        raise json.JSONDecodeError("Gemini returned unparseable JSON", raw, 0)


def _try_parse_json(raw: str) -> Optional[Any]:
    """コードフェンスを取り除いてJSONパースを試みる。失敗時はNoneを返す。"""
    # コードフェンス除去（```json ... ``` や ``` ... ```）
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # JSONブロックを本文から抽出する（前後にテキストが混入している場合）
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None
