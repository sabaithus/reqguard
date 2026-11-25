"""Поддержка запуска пакета через `python -m reqguard <input> <output>`."""

import sys
from reqguard.cli import main

if __name__ == "__main__":
    sys.exit(main())
