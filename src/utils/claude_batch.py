"""Claude Batch API ラッパー"""
import logging
import time
from typing import Dict, List, Optional

import anthropic

from src.config import CLAUDE_MODEL

logger = logging.getLogger(__name__)
POLL_INTERVAL = 30       # ポーリング間隔（秒）
MAX_POLL_MINUTES = 55    # 最大待機時間（分）


class ClaudeBatchClient:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        # SDK バージョンに応じてbetaか通常かを選択
        try:
            self._batches = self.client.messages.batches
        except AttributeError:
            self._batches = self.client.beta.messages.batches

    def submit_batch(
        self,
        requests: List[Dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 16000,
    ) -> str:
        """
        バッチリクエストを送信してバッチIDを返す。

        Args:
            requests: [{"custom_id": str, "user_message": str}, ...]
            system_prompt: システムプロンプト（全リクエスト共通）
            max_tokens: 最大出力トークン数

        Returns:
            batch_id
        """
        batch_requests = []
        for req in requests:
            messages = [{"role": "user", "content": req["user_message"]}]
            params: Dict = {
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system_prompt:
                params["system"] = system_prompt

            batch_requests.append({
                "custom_id": req["custom_id"],
                "params": params,
            })

        batch = self._batches.create(requests=batch_requests)
        logger.info(f"Batch submitted: id={batch.id}, requests={len(batch_requests)}")
        return batch.id

    def wait_for_completion(self, batch_id: str) -> Dict[str, str]:
        """
        バッチの完了を待ってから結果を返す。

        Args:
            batch_id: バッチID

        Returns:
            {custom_id: response_text} のdict

        Raises:
            TimeoutError: MAX_POLL_MINUTES を超えた場合
            RuntimeError: バッチがエラー終了した場合
        """
        max_polls = (MAX_POLL_MINUTES * 60) // POLL_INTERVAL
        elapsed_minutes = 0.0

        for poll_count in range(int(max_polls) + 1):
            batch = self._batches.retrieve(batch_id)
            status = batch.processing_status

            logger.info(
                f"Batch {batch_id}: status={status}, elapsed={elapsed_minutes:.1f}min"
            )

            if status == "ended":
                break

            if poll_count >= max_polls:
                raise TimeoutError(
                    f"Batch {batch_id} did not complete within {MAX_POLL_MINUTES} minutes"
                )

            time.sleep(POLL_INTERVAL)
            elapsed_minutes += POLL_INTERVAL / 60

        # 結果収集
        results: Dict[str, str] = {}
        error_count = 0

        for result in self._batches.results(batch_id):
            custom_id = result.custom_id
            if result.result.type == "succeeded":
                content = result.result.message.content
                text = content[0].text if content else ""
                results[custom_id] = text
                logger.info(f"Batch result {custom_id}: {len(text)} chars")
            elif result.result.type == "errored":
                error = result.result.error
                logger.error(f"Batch request {custom_id} errored: {error}")
                error_count += 1
            else:
                logger.warning(f"Batch request {custom_id}: unexpected type {result.result.type}")
                error_count += 1

        if not results:
            raise RuntimeError(
                f"All batch requests failed (errors: {error_count})"
            )

        logger.info(
            f"Batch complete: {len(results)} succeeded, {error_count} failed"
        )
        return results

    def run_batch(
        self,
        requests: List[Dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 16000,
    ) -> Dict[str, str]:
        """
        submit + wait_for_completion のショートカット。

        Returns:
            {custom_id: response_text}
        """
        batch_id = self.submit_batch(requests, system_prompt, max_tokens)
        return self.wait_for_completion(batch_id)
