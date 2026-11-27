"""Повторная сборка отчёта из записанных ответов модели (raw_llm.jsonl).

Используется только для оценки: позволяет проверить детерминированные этапы
(верификация, дедупликация, отчёт) нового кода на тех же ответах модели без
сетевых вызовов. Вызывает обычную точку входа reqguard.cli.main.

python eval/e6/replay_raw.py <raw_llm.jsonl> <вход> <каталог_результата>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from reqguard import cli  # noqa: E402
from reqguard.config import LLMSettings  # noqa: E402
from reqguard.llm_client import ResilientLLMClient  # noqa: E402


def main() -> int:
    raw, src, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    replies = [e["raw_text"] for e in map(json.loads, raw.read_text(encoding="utf-8").splitlines()) if "raw_text" in e]
    settings = LLMSettings(provider="gemini", api_key="replay", primary_model="replay", max_retries=1)
    with patch("reqguard.cli.load_settings", return_value=settings), patch.object(
        ResilientLLMClient, "_call_provider", side_effect=replies
    ):
        return cli.main([str(src), str(out / "report.md"), "--json", str(out / "report.json")])


if __name__ == "__main__":
    raise SystemExit(main())
