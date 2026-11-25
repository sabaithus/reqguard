#!/usr/bin/env python3
"""Единая точка входа одной командой (согласно п. 7 ТЗ хакатона):
python main.py <путь_к_файлу_требований> <путь_к_файлу_отчёта>
"""

from __future__ import annotations

import sys
from pathlib import Path

# Подключаем каталог src/ без необходимости предварительного pip install -e .
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from reqguard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
