"""Диагностический запуск ReqGuard с записью сырых ответов модели.

Используется только для оценки (E6). Логику агента не меняет: оборачивает
ResilientLLMClient._call_provider, сохраняет текст ответа в raw_llm.jsonl
и вызывает обычную точку входа reqguard.cli.main.

python eval/e6/capture_run.py <вход> <каталог_результата>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from reqguard import cli  # noqa: E402
from reqguard.llm_client import ResilientLLMClient  # noqa: E402


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    log = out / "raw_llm.jsonl"
    log.write_text("", encoding="utf-8")
    original = ResilientLLMClient._call_provider

    def recording(self, model_name, system_prompt, user_prompt):
        entry = {"model": model_name, "prompt_sha256": hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()}
        try:
            text = original(self, model_name=model_name, system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception as exc:  # фиксируем и пробрасываем без изменений
            entry["error"] = repr(exc)[:500]
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            raise
        entry["raw_text"] = text
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return text

    ResilientLLMClient._call_provider = recording
    return cli.main([str(src), str(out / "report.md"), "--with-html", "--json", str(out / "report.json")])


if __name__ == "__main__":
    raise SystemExit(main())
