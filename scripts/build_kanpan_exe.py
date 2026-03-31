#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "run_kanpan_tool.py"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
APP_NAME = "AstockKanpan"
ICON_FILE = ROOT / "scripts" / "assets" / "astock-kanpan.ico"
RUNTIME_DIRS = [
    ROOT / "kanpan-tool",
    ROOT / "market-overview-exporter",
    ROOT / "duanxian-jingjia-exporter",
    ROOT / "duanxian-workflow",
    ROOT / "duanxian-yidong-pool",
]
HIDDEN_IMPORTS = [
    "uuid",
    "ctypes",
    "ctypes.wintypes",
    "json",
    "threading",
    "subprocess",
    "http.server",
    "urllib.parse",
    "urllib.request",
    "requests",
    "playwright",
    "playwright.sync_api",
    "zoneinfo",
    "tkinter",
    "tkinter.messagebox",
]
COLLECT_PACKAGES = [
    "requests",
    "playwright",
]


def add_data_arg(path: Path) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{path}{separator}{path.name}"


def main() -> int:
    if not ENTRY.exists():
        raise SystemExit(f"未找到入口脚本: {ENTRY}")

    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        raise SystemExit("未安装 PyInstaller，请先执行: pip install pyinstaller")

    command = [
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        APP_NAME,
        "--icon",
        str(ICON_FILE),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
        "--specpath",
        str(BUILD_DIR / "spec"),
    ]
    for runtime_dir in RUNTIME_DIRS:
        if runtime_dir.exists():
            command.extend(["--add-data", add_data_arg(runtime_dir)])
    for hidden_import in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", hidden_import])
    for package_name in COLLECT_PACKAGES:
        command.extend(["--collect-all", package_name])
    command.append(str(ENTRY))

    completed = subprocess.run(command, check=False)
    if completed.returncode == 0:
        print(f"构建完成: {DIST_DIR / (APP_NAME + '.exe')}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
