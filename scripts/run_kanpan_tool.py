#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "kanpan-tool" / "server.py"


def main() -> int:
    if not SERVER.exists():
        raise SystemExit(f"未找到看盘工具入口：{SERVER}")
    command = [sys.executable, str(SERVER)]
    completed = subprocess.run(command, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
