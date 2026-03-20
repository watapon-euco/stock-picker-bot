"""Gemini API ラッパー（google-generativeai SDK）"""
import json
import logging
import time
from typing import Any, Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
MAX_RETRIES = 3
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
                delay = RETRY_BASE_DELAY * (2 ** attempt)
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

        Returns:
            パース済みのPythonオブジェクト
        """
        raw = self.generate(prompt, json_mode=True)

        # コードフェンスが付いている場合を除去
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON response: {e}\nRaw: {raw[:500]}")
            raise
