"""Обёртка над LLM: Groq с ротацией ключей + Gemini как последний фолбэк.

01-11-26: раньше использовался ровно один ключ Groq, и на 429 (tokens per minute)
пост просто падал. Теперь:
  1) модели идут каскадом (gpt-oss-120b -> 20b -> qwen),
  2) при лимите ключа — переход на следующий Groq-ключ (GROQ_API_KEY, _2, _3...),
  3) когда кончились все ключи — тот же промпт уходит в Gemini
     (GEMINI_API_KEY, _2, _3... через REST v1beta).
"""
import asyncio
import json
import logging
import os
import urllib.request
from typing import List, Optional

from .config import GROQ_FALLBACK_MODELS, GROQ_MODELS, config

logger = logging.getLogger(__name__)

MODEL_DEAD_MARKERS = ("decommissioned", "deprecated", "does not exist", "not found")
# Суточный лимит аккаунта Groq (TPD) — не лечится повтором, но лечится другим ключом.
DAILY_QUOTA_MARKERS = ("tokens per day", "requests per day", "tpd limit", "rpd limit")
# Поминутный лимит (TPM) — лечится сменой ключа или паузой.
RATE_LIMIT_MARKERS = ("rate limit", "tokens per minute", "tpm", "429", "too many requests")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "{model}:generateContent?key={key}")


def _collect_keys(prefix: str) -> List[str]:
    """Собрать ключи PREFIX, PREFIX_2, ... в порядке номеров (без дублей)."""
    found = []
    base = os.getenv(prefix)
    if base and base.strip():
        found.append(base.strip())
    for i in range(2, 9):
        v = os.getenv("%s_%d" % (prefix, i))
        if v and v.strip():
            found.append(v.strip())
    flat = []
    for k in found:
        for part in k.split(","):
            part = part.strip()
            if part and part not in flat:
                flat.append(part)
    return flat


class LLMError(Exception):
    pass


class DailyQuotaExhausted(LLMError):
    """Все ключи Groq упёрлись в дневной лимит — повторять бессмысленно."""


class GroqLLM:
    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        self._keys = ([api_key] if api_key else []) or _collect_keys("GROQ_API_KEY")
        self._gemini_keys = _collect_keys("GEMINI_API_KEY")
        self._models = models or (GROQ_MODELS + GROQ_FALLBACK_MODELS)
        self._clients = {}
        self._client = None          # для тестов/внешней подмены клиента
        self._key_idx = 0
        self._quota_error: Optional[str] = None

    # ------------------------------------------------------------------ служебное
    @property
    def quota_exhausted(self) -> bool:
        return self._quota_error is not None

    @property
    def available(self) -> bool:
        return bool(self._keys or self._gemini_keys)

    @property
    def api_key(self) -> Optional[str]:
        return self._keys[0] if self._keys else None

    @property
    def client(self):
        return self._client_for(self._key_idx)

    def _client_for(self, idx: int):
        if self._client is not None:
            return self._client
        if idx not in self._clients:
            from groq import Groq
            self._clients[idx] = Groq(api_key=self._keys[idx])
        return self._clients[idx]

    def _rotate_key(self) -> bool:
        if self._key_idx + 1 < len(self._keys):
            self._key_idx += 1
            logger.warning("  🔑 переключаюсь на ключ Groq #%d", self._key_idx + 1)
            return True
        return False

    # ------------------------------------------------------------------ Groq
    async def _groq_once(self, model: str, prompt: str, max_tokens: int,
                         temperature: float, json_mode: bool) -> str:
        kwargs = dict(
            model=model,
            temperature=min(1.0, temperature),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await asyncio.to_thread(
            self._client_for(self._key_idx).chat.completions.create, **kwargs)
        return (resp.choices[0].message.content or "").strip()

    # ------------------------------------------------------------------ Gemini
    async def _gemini_once(self, key: str, prompt: str, max_tokens: int,
                           temperature: float, json_mode: bool) -> str:
        gen = {"temperature": min(1.0, temperature), "maxOutputTokens": max_tokens}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
        }
        url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=key)
        data = json.dumps(body).encode()

        def _call():
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json",
                "User-Agent": "post-bot/1.0",
            })
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())

        payload = await asyncio.to_thread(_call)
        cands = payload.get("candidates") or []
        if not cands:
            raise LLMError("Gemini: пустой ответ (%s)" % str(payload)[:200])
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise LLMError("Gemini: пустой текст")
        return text

    # ------------------------------------------------------------------ основной вызов
    async def complete(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        last_error = None

        if not self.quota_exhausted:
            for model in self._models:
                if self.quota_exhausted:
                    break
                attempt = 0
                while attempt < max(config.groq_retries_per_model, 1):
                    try:
                        text = await self._groq_once(model, prompt, max_tokens,
                                                     temperature + 0.1 * attempt, json_mode)
                        if not text:
                            raise LLMError("пустой ответ")
                        return text
                    except Exception as e:  # noqa: BLE001
                        last_error = e
                        msg = str(e).lower()

                        if any(m in msg for m in DAILY_QUOTA_MARKERS):
                            if self._rotate_key():
                                continue
                            logger.error("  ⛔ дневные лимиты всех ключей Groq исчерпаны")
                            self._quota_error = str(e)
                            break
                        if any(m in msg for m in RATE_LIMIT_MARKERS):
                            if self._rotate_key():
                                continue  # тот же промпт, другой ключ — сразу
                            logger.warning("  ⏳ %s: лимит по минуте, пауза", model)
                            await asyncio.sleep(config.groq_base_delay * (2 ** attempt))
                            attempt += 1
                            continue
                        if any(m in msg for m in MODEL_DEAD_MARKERS):
                            logger.warning("  ⚠️ модель %s недоступна, следующая", model)
                            break
                        if "json_validate_failed" in msg and json_mode:
                            logger.warning("  ⚠️ %s: json_validate_failed, повтор без json", model)
                            try:
                                return await self._groq_once(model, prompt, max_tokens,
                                                             temperature, False)
                            except Exception as inner:  # noqa: BLE001
                                last_error = inner
                        logger.error("  ❌ %s попытка %d: %s", model, attempt + 1, e)
                        await asyncio.sleep(config.groq_base_delay * (2 ** attempt))
                        attempt += 1

        # --- Gemini как последний рубеж
        if self._gemini_keys:
            logger.warning("  🔄 Groq не отдал текст — пробую Gemini (%s)", GEMINI_MODEL)
            for i, key in enumerate(self._gemini_keys, 1):
                for attempt in range(2):
                    try:
                        return await self._gemini_once(key, prompt, max_tokens,
                                                       temperature, json_mode)
                    except Exception as e:  # noqa: BLE001
                        last_error = e
                        logger.error("  ❌ Gemini ключ #%d попытка %d: %s", i, attempt + 1, e)
                        await asyncio.sleep(2 * (attempt + 1))

        # Groq упёрся в дневные лимиты и Gemini не выручил — это уже не «попробуем позже»
        if self._quota_error:
            raise DailyQuotaExhausted(self._quota_error)
        raise LLMError("все модели и ключи отказали: %s" % last_error)
