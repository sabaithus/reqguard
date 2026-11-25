"""Конфигурация окружения, выбор LLM-провайдера и цепочек резервных моделей."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


PLACEHOLDER_PREFIXES = ("your_", "sk-your", "paste_", "insert_", "<", "TODO")


def _is_real_secret(value: str | None) -> bool:
    if not value:
        return False
    cleaned = value.strip()
    if not cleaned:
        return False
    return not any(cleaned.lower().startswith(p.lower()) for p in PLACEHOLDER_PREFIXES)


def detect_agent_version(project_root: Path | None = None) -> str:
    """Возвращает короткий хэш git-коммита или тег версии агента для паспорта прогона."""
    cwd = str(project_root or Path(__file__).resolve().parents[2])
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return f"v1.0.0 ({proc.stdout.strip()})"
    except Exception:
        pass

    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            return f"v1.0.0 ({rev.stdout.strip()})"
        git_dir = Path(cwd) / ".git"
        if git_dir.exists():
            return "v1.0.0 (uncommitted)"
    except Exception:
        pass
    return "v1.0.0"


@dataclass(slots=True)
class LLMSettings:
    provider: str  # "gemini" | "openai" | "anthropic" | "ollama" | "none"
    api_key: str
    primary_model: str
    fallback_models: list[str] = field(default_factory=list)
    base_url: str = ""
    timeout_sec: float = 90.0
    max_retries: int = 3
    temperature: float = 0.0
    thinking_budget: int = 1536

    @property
    def all_models(self) -> list[str]:
        seen: list[str] = []
        for m in [self.primary_model, *self.fallback_models]:
            m_clean = m.strip()
            if m_clean and m_clean not in seen:
                seen.append(m_clean)
        return seen


def load_settings(env_path: Path | None = None) -> LLMSettings:
    """Загружает настройки из .env и переменных окружения с автоопределением провайдера."""
    if env_path and env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)

    requested = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    timeout_sec = float(os.getenv("LLM_TIMEOUT_SEC", "90"))
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "3"))
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.0"))
    thinking_budget = int(os.getenv("LLM_THINKING_BUDGET", "1536"))

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ollama_url = os.getenv("OLLAMA_BASE_URL", "").strip()

    # Надёжная цепочка моделей Gemini (актуальная на конец 2026 года)
    raw_gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
    raw_gemini_fallbacks = os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.5-flash-lite,gemini-flash-latest,gemini-3.8-flash,gemma-4-31b-it",
    )
    gemini_fallbacks = [m.strip() for m in raw_gemini_fallbacks.split(",") if m.strip()]

    if requested in ("gemini", "auto") and _is_real_secret(gemini_key):
        return LLMSettings(
            provider="gemini",
            api_key=gemini_key,
            primary_model=raw_gemini_model,
            fallback_models=gemini_fallbacks,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            temperature=temperature,
            thinking_budget=thinking_budget,
        )

    if requested in ("openai", "auto") and _is_real_secret(openai_key):
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        primary = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        fb_raw = os.getenv("OPENAI_FALLBACK_MODELS", "")
        fbs = [m.strip() for m in fb_raw.split(",") if m.strip()]
        return LLMSettings(
            provider="openai",
            api_key=openai_key,
            primary_model=primary,
            fallback_models=fbs,
            base_url=base_url,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            temperature=temperature,
        )

    if requested in ("anthropic", "auto") and _is_real_secret(anthropic_key):
        primary = (
            os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest").strip()
            or "claude-3-5-sonnet-latest"
        )
        return LLMSettings(
            provider="anthropic",
            api_key=anthropic_key,
            primary_model=primary,
            fallback_models=["claude-3-5-haiku-latest"],
            base_url="https://api.anthropic.com/v1",
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            temperature=temperature,
        )

    if requested in ("ollama", "auto") and ollama_url:
        primary = os.getenv("OLLAMA_MODEL", "qwen2.5:14b").strip() or "qwen2.5:14b"
        return LLMSettings(
            provider="ollama",
            api_key="",
            primary_model=primary,
            fallback_models=[],
            base_url=ollama_url.rstrip("/"),
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            temperature=temperature,
        )

    return LLMSettings(
        provider="none",
        api_key="",
        primary_model="unconfigured",
        fallback_models=[],
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        temperature=temperature,
    )
