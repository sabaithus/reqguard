"""Собирает отдельный комплект сдачи после полного модельного прогона."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from main import main as run_agent


def prepare(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Каталог результата уже существует. Укажите новый путь.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        bundle = Path(temp) / "bundle"
        inputs = bundle / "input"
        inputs.mkdir(parents=True)
        snapshot = inputs / source.name
        shutil.copyfile(source, snapshot)
        data = bundle / "report.json"
        result = run_agent([str(snapshot), str(bundle / "report.md"), "--json", str(data), "--with-html"])
        if result not in (0, 1, 3):
            raise ValueError("Прогон агента не завершён. Комплект не сформирован.")
        report = json.loads(data.read_text(encoding="utf-8"))
        passport = report["passport"]
        if result == 3 or passport["analysis_status"] != "complete" or not passport["llm_calls_count"]:
            raise ValueError(f"Проверка неполная ({passport['analysis_status']}). Комплект не сформирован.")
        if passport["source_sha256"] != hashlib.sha256(snapshot.read_bytes()).hexdigest():
            raise ValueError("Отпечаток входа отличается от паспорта.")
        bundle.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        prepare(args.source, args.destination)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        sys.exit(2)
    print(f"Комплект сохранён: {args.destination}")
