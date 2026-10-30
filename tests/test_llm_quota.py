"""Дневной лимит Groq (TPD) не должен превращаться в долбёжку по всем моделям."""
import asyncio
import types

import pytest

from bot.llm import DailyQuotaExhausted, GroqLLM, LLMError

TPD_ERROR = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_x` on tokens per day (TPD): "
    "Limit 200000, Used 199700, Requested 1593.'}}"
)


class _StubClient:
    def __init__(self, exc=Exception(TPD_ERROR)):
        self.calls = 0
        self._exc = exc
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        raise self._exc


def _llm_with(stub):
    llm = GroqLLM(api_key="gsk_test")
    llm._client = stub
    return llm


def test_daily_quota_aborts_without_retries():
    stub = _StubClient()
    llm = _llm_with(stub)
    with pytest.raises(DailyQuotaExhausted):
        asyncio.run(llm.complete("hi"))
    # ни повторов, ни перебора моделей
    assert stub.calls == 1
    assert llm.quota_exhausted is True


def test_daily_quota_sticks_for_later_calls():
    stub = _StubClient()
    llm = _llm_with(stub)
    with pytest.raises(DailyQuotaExhausted):
        asyncio.run(llm.complete("hi"))
    with pytest.raises(DailyQuotaExhausted):
        asyncio.run(llm.complete("hi"))
    assert stub.calls == 1  # второй вызов даже не пошёл в сеть


def test_per_minute_limit_still_retries():
    """429 по RPM (без TPD) — обычная временная ошибка, её ретраить надо."""
    stub = _StubClient(Exception("Error code: 429 - rate limit exceeded on requests per minute"))
    llm = _llm_with(stub)
    llm._models = ["m1"]
    asyncio.run(_slow_guard(llm))
    assert stub.calls > 1
    assert llm.quota_exhausted is False


async def _slow_guard(llm):
    from bot.config import config
    delay = config.groq_base_delay
    config.groq_base_delay = 0.0  # не спать в тестах
    try:
        with pytest.raises(LLMError):
            await llm.complete("hi")
    finally:
        config.groq_base_delay = delay
