"""Обёртка над Groq: единая точка повторов, фолбэка моделей и разбора ошибок."""
import asyncio
import logging
from typing import List, Optional

from .config import GROQ_FALLBACK_MODELS, GROQ_MODELS, config

logger = logging.getLogger(__name__)

MODEL_DEAD_MARKERS = ("decommissioned", "deprecated", "does not exist", "not found")


class LLMError(Exception):
    pass


class GroqLLM:
    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        self._api_key = api_key or config.groq_api_key
        self._models = models or (GROQ_MODELS + GROQ_FALLBACK_MODELS)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=self._api_key)
        return self._client

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        """Вернуть текст ответа. Бросает LLMError, если все модели отказали."""
        last_error = None
        for model in self._models:
            for attempt in range(config.groq_retries_per_model):
                try:
                    kwargs = dict(
                        model=model,
                        temperature=min(1.0, temperature + 0.1 * attempt),
                        max_tokens=max_tokens,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = await asyncio.to_thread(
                        self.client.chat.completions.create, **kwargs)
                    text = (resp.choices[0].message.content or "").strip()
                    if not text:
                        raise LLMError("пустой ответ")
                    return text
                except Exception as e:
                    last_error = e
                    msg = str(e).lower()
                    if any(m in msg for m in MODEL_DEAD_MARKERS):
                        logger.warning(f"  ⚠️ модель {model} недоступна, следующая")
                        break
                    if "json_validate_failed" in msg and json_mode:
                        logger.warning(f"  ⚠️ {model}: json_validate_failed, повтор без json-режима")
                        try:
                            resp = await asyncio.to_thread(
                                self.client.chat.completions.create,
                                model=model,
                                temperature=min(1.0, temperature + 0.1 * attempt),
                                max_tokens=max_tokens,
                                messages=[{"role": "user", "content": prompt}],
                            )
                            text = (resp.choices[0].message.content or "").strip()
                            if text:
                                return text
                        except Exception as inner:
                            last_error = inner
                    logger.error(f"  ❌ {model} попытка {attempt + 1}: {e}")
                    await asyncio.sleep(config.groq_base_delay * (2 ** attempt))

        raise LLMError(f"все модели отказали: {last_error}")
