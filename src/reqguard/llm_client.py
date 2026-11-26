"""Отказоустойчивый мульти-провайдерный LLM-шлюз (Gemini, OpenAI/совместимые, Anthropic, Ollama).

Обеспечивает:
- автоматическое переключение по цепочке резервных моделей при 404/429/5xx;
- экспоненциальные повторы (backoff) при сетевых сбоях и лимитах бесплатных тарифов;
- надёжное извлечение и валидацию JSON по схеме Pydantic (с авто-ремонтом при ошибке схемы).
"""

from __future__ import annotations

import json
import re
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from reqguard.config import LLMSettings
from reqguard.models import TokenUsage

T = TypeVar("T", bound=BaseModel)


class LLMGatewayError(RuntimeError):
    """Критическая ошибка обращения ко всем доступным моделям LLM."""


def extract_json_payload(raw_text: str) -> dict:
    """Извлекает корневой JSON-объект из ответа модели, очищая markdown-блоки и мусор."""
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    except json.JSONDecodeError:
        pass

    # Поиск сбалансированного объекта {...}
    start = text.find("{")
    if start == -1:
        raise ValueError(f"В ответе модели не найден JSON-объект: {raw_text[:250]!r}")

    depth = 0
    in_str = False
    escape = False
    end = -1
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break

    candidate = text[start : end + 1] if end != -1 else text[start:]
    # Удаление висячих запятых перед } или ]
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Ожидался корневой JSON-объект (dict).")
    return parsed


class ResilientLLMClient:
    """Клиент вызова LLM со строгой типизацией ответов и защитой от сбоев API."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.http_models: list[str] = []
        self.token_usage: list[TokenUsage] = []
        self.calls_count: int = 0
        self.last_used_model: str = settings.primary_model

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        status_cb=None,
    ) -> T:
        if self.settings.provider == "none":
            raise LLMGatewayError(
                "API-ключ LLM не настроен. Скопируйте .env.example в .env и укажите GEMINI_API_KEY "
                "(бесплатно на https://aistudio.google.com/apikey) или ключ другого провайдера."
            )

        errors: list[str] = []
        models_to_try = self.settings.all_models
        current_user_prompt = user_prompt

        for m_idx, model_name in enumerate(models_to_try):
            for attempt in range(1, self.settings.max_retries + 1):
                try:
                    raw_text = self._call_provider(
                        model_name=model_name,
                        system_prompt=system_prompt,
                        user_prompt=current_user_prompt,
                    )
                    self.calls_count += 1
                    self.last_used_model = model_name
                    payload = extract_json_payload(raw_text)
                    return response_model.model_validate(payload)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    body_preview = exc.response.text[:300]
                    err_msg = f"[{model_name} attempt {attempt}] HTTP {status}: {body_preview}"
                    errors.append(err_msg)
                    # Если модель устарела / недоступна (404 / 400 invalid model) — мгновенно переключаемся на следующую модель
                    if status == 404 or (status == 400 and "model" in body_preview.lower()):
                        if status_cb:
                            status_cb(f"  ↳ Модель {model_name} недоступна ({status}), переключение...")
                        break
                    # При 401/403 неверный ключ — повторять бессмысленно
                    if status in (401, 403):
                        raise LLMGatewayError(
                            f"Ошибка авторизации API ({status}) для провайдера {self.settings.provider}. "
                            f"Проверьте корректность ключа в .env. Детали: {body_preview}"
                        ) from exc
                    # При 503 (перегрузка) или 429, если есть резервные модели в списке, делаем не более 1 быстрой паузы и идём дальше
                    has_next_model = m_idx < len(models_to_try) - 1
                    if status in (429, 503) and has_next_model and attempt >= 1:
                        if status_cb:
                            status_cb(f"  ↳ Модель {model_name} перегружена ({status}), мгновенный переход на резервную модель...")
                        time.sleep(0.5)
                        break
                    if attempt < self.settings.max_retries:
                        wait_s = min(10.0, 2.0**attempt)
                        time.sleep(wait_s)
                    else:
                        break
                except (ValidationError, ValueError, KeyError, IndexError, AttributeError, TypeError) as exc:
                    err_msg = f"[{model_name} attempt {attempt}] Ошибка схемы JSON: {exc}"
                    errors.append(err_msg)
                    if attempt < self.settings.max_retries:
                        current_user_prompt = (
                            user_prompt
                            + "\n\nВНИМАНИЕ: предыдущий ответ содержал синтаксическую ошибку или не соответствовал "
                            f"JSON-схеме ({str(exc)[:350]}). Верни СТРОГО валидный JSON-объект без комментариев."
                        )
                        time.sleep(1.0)
                    else:
                        current_user_prompt = user_prompt
                        break
                except httpx.RequestError as exc:
                    errors.append(f"[{model_name} attempt {attempt}] Сетевая ошибка: {exc}")
                    has_next_model = m_idx < len(models_to_try) - 1
                    if isinstance(exc, httpx.TimeoutException) and has_next_model:
                        if status_cb:
                            status_cb(f"  ↳ Таймаут модели {model_name}, мгновенный переход на резервную модель...")
                        break
                    if attempt < self.settings.max_retries:
                        time.sleep(2.0 * attempt)
                    else:
                        break

        joined = "\n  - ".join(errors[-6:])
        raise LLMGatewayError(
            f"Не удалось получить валидный ответ ни от одной модели ({models_to_try}):\n  - {joined}"
        )

    def _response_data(self, response: httpx.Response, model: str) -> dict:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("API вернуло ответ без объекта JSON.")
        raw = data.get("usageMetadata") or data.get("usage") or data
        keys = ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount",
                "cachedContentTokenCount", "prompt_tokens", "completion_tokens", "total_tokens",
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                "prompt_eval_count", "eval_count")
        if isinstance(raw, dict):
            counts = {k: raw[k] for k in keys if type(raw.get(k)) is int and raw[k] >= 0}
            if counts:
                self.token_usage.append(TokenUsage(model=model, counters=counts))
        return data

    def _call_provider(self, model_name: str, system_prompt: str, user_prompt: str) -> str:
        p = self.settings.provider
        timeout = httpx.Timeout(self.settings.timeout_sec, connect=15.0)

        def count_attempt(request: httpx.Request) -> None:
            self.http_models.append(model_name)

        with httpx.Client(timeout=timeout, event_hooks={"request": [count_attempt]}) as client:
            if p == "gemini":
                url = f"{self.settings.base_url}/models/{model_name}:generateContent"
                gen_cfg: dict = {
                    "temperature": self.settings.temperature,
                    "responseMimeType": "application/json",
                }
                if self.settings.thinking_budget >= 0 and "gemini" in model_name.lower():
                    gen_cfg["thinkingConfig"] = {"thinkingBudget": self.settings.thinking_budget}
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": gen_cfg,
                }
                resp = client.post(url, headers={"x-goog-api-key": self.settings.api_key}, json=payload)
                if resp.status_code == 400 and "thinking" in resp.text.lower() and "thinkingConfig" in gen_cfg:
                    gen_cfg.pop("thinkingConfig", None)
                    resp = client.post(url, headers={"x-goog-api-key": self.settings.api_key}, json=payload)
                resp.raise_for_status()
                data = self._response_data(resp, model_name)
                candidates = data.get("candidates") or []
                if not candidates:
                    raise ValueError(f"Пустой список candidates от Gemini: {data}")
                parts = candidates[0].get("content", {}).get("parts") or []
                return "".join(part.get("text", "") for part in parts)

            if p == "openai":
                url = f"{self.settings.base_url}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model_name,
                    "temperature": self.settings.temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 400 and "response_format" in resp.text:
                    payload.pop("response_format", None)
                    resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = self._response_data(resp, model_name)
                choices = data.get("choices")
                if not choices or not isinstance(choices[0].get("message", {}).get("content"), str):
                    raise ValueError("OpenAI-совместимый API вернул ответ без текста.")
                return choices[0]["message"]["content"]

            if p == "anthropic":
                url = f"{self.settings.base_url}/messages"
                headers = {
                    "x-api-key": self.settings.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": model_name,
                    "max_tokens": 8192,
                    "temperature": self.settings.temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = self._response_data(resp, model_name)
                return "".join(
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                )

            if p == "ollama":
                url = f"{self.settings.base_url}/api/chat"
                payload = {
                    "model": model_name,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": self.settings.temperature},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = self._response_data(resp, model_name)
                return data.get("message", {}).get("content", "")

        raise LLMGatewayError(f"Неизвестный провайдер LLM: {p}")
