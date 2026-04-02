#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
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
RUNTIME_FILES = [
    ROOT / "解析提示词要求.md",
    ROOT / "开盘盘前解析-system-prompt-完整版.md",
    ROOT / "盘中预期解析-system-prompt-完整版.md",
    ROOT / "盘后全量复盘-system-prompt-完整版.md",
]
BASE_HIDDEN_IMPORTS = [
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
]
OPTIONAL_HIDDEN_IMPORTS = [
    "tkinter",
    "tkinter.messagebox",
]
COLLECT_PACKAGES = [
    "requests",
    "playwright",
]


def host_target() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise SystemExit(f"Unsupported host platform for packaging: {sys.platform}")


def add_data_arg(path: Path) -> str:
    separator = ";" if os.name == "nt" else ":"
    return f"{path}{separator}{path.name}"


def module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except Exception:
        return False
    return True


def ensure_pyinstaller_available() -> None:
    try:
        import PyInstaller  # noqa: F401
    except Exception as exc:
        raise SystemExit("PyInstaller is not installed. Run: python -m pip install pyinstaller") from exc


def artifact_name(target: str, app_name: str = APP_NAME) -> str:
    return app_name + (".exe" if target == "windows" else "")


def build_bundle(
    target: str,
    *,
    app_name: str = APP_NAME,
    dist_dir: Path | None = None,
    work_dir: Path | None = None,
    spec_dir: Path | None = None,
    console: bool | None = None,
) -> int:
    current_host = host_target()
    if target != current_host:
        raise SystemExit(f"Cannot build {target} on host {current_host}")
    if not ENTRY.exists():
        raise SystemExit(f"Entry script not found: {ENTRY}")

    ensure_pyinstaller_available()

    if dist_dir is None:
        dist_dir = DIST_DIR / target
    if work_dir is None:
        work_dir = BUILD_DIR / "pyinstaller" / target
    if spec_dir is None:
        spec_dir = BUILD_DIR / "spec" / target
    if console is None:
        console = target != "windows"

    hidden_imports = list(BASE_HIDDEN_IMPORTS)
    for module_name in OPTIONAL_HIDDEN_IMPORTS:
        if module_available(module_name):
            hidden_imports.append(module_name)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        app_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
    ]
    if not console:
        command.append("--noconsole")
    if target == "windows" and ICON_FILE.exists():
        command.extend(["--icon", str(ICON_FILE)])
    for runtime_dir in RUNTIME_DIRS:
        if runtime_dir.exists():
            command.extend(["--add-data", add_data_arg(runtime_dir)])
    for runtime_file in RUNTIME_FILES:
        if runtime_file.exists():
            command.extend(["--add-data", add_data_arg(runtime_file)])
    for hidden_import in hidden_imports:
        command.extend(["--hidden-import", hidden_import])
    for package_name in COLLECT_PACKAGES:
        command.extend(["--collect-all", package_name])
    command.append(str(ENTRY))

    completed = subprocess.run(command, check=False)
    if completed.returncode == 0:
        print(f"Build complete: {dist_dir / artifact_name(target, app_name)}")
    return completed.returncode
