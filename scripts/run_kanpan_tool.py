#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import tkinter as tk
from tkinter import messagebox


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "kanpan-tool" / "server.py"
EMBEDDED_SCRIPTS = {
    "market_overview": ROOT / "market-overview-exporter" / "scripts" / "fetch_market_overview.py",
    "jingjia": ROOT / "duanxian-jingjia-exporter" / "scripts" / "fetch_jingjia.py",
    "jjyd": ROOT / "duanxian-workflow" / "scripts" / "fetch_jjyd.py",
    "yidong_pool": ROOT / "duanxian-yidong-pool" / "scripts" / "fetch_yidong_pool.py",
}
DEFAULT_PORT = 8765
APP_STORAGE_NAME = "AstockKanpan"


def runtime_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", "")
    if bundled_root:
        return Path(bundled_root)
    return ROOT


def user_storage_root() -> Path:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / APP_STORAGE_NAME
    return Path.home() / f".{APP_STORAGE_NAME.lower()}"


def legacy_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def migrate_legacy_workspace(target_root: Path) -> None:
    source_root = legacy_app_root()
    if source_root == target_root:
        return

    target_root.mkdir(parents=True, exist_ok=True)
    for folder_name in ("data", ".local-config"):
        source = source_root / folder_name
        target = target_root / folder_name
        if source.exists() and not target.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        root = user_storage_root()
        migrate_legacy_workspace(root)
        return root
    return ROOT


def write_runtime_log(message: str) -> None:
    try:
        log_path = app_root() / "kanpan-runtime.log"
        log_path.write_text(message, encoding="utf-8", newline="\n")
    except Exception:
        pass


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ASTOCK_APP_ROOT"] = str(app_root())
    env["ASTOCK_BUNDLE_ROOT"] = str(runtime_root())
    return env


def dispatch_embedded_script() -> bool:
    script_key = os.environ.get("ASTOCK_EMBEDDED_SCRIPT", "").strip()
    if not script_key:
        return False

    script_path = EMBEDDED_SCRIPTS.get(script_key)
    if not script_path:
        raise SystemExit(f"未找到内置脚本标识: {script_key}")

    runtime_script = runtime_root() / script_path.relative_to(ROOT)
    if not runtime_script.exists():
        raise SystemExit(f"未找到内置脚本文件: {runtime_script}")

    os.environ.update(runtime_env())
    script_dir = str(runtime_script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    sys.argv = [str(runtime_script), *sys.argv[1:]]
    runpy.run_path(str(runtime_script), run_name="__main__")
    return True


def run_server_mode() -> int:
    server_path = runtime_root() / SERVER.relative_to(ROOT)
    if not server_path.exists():
        raise SystemExit(f"未找到看盘工具入口: {server_path}")

    os.environ.update(runtime_env())
    server_dir = str(server_path.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    sys.argv = [str(server_path), *sys.argv[1:]]
    runpy.run_path(str(server_path), run_name="__main__")
    return 0


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def pick_port(start: int = DEFAULT_PORT, attempts: int = 20) -> int:
    preferred = os.environ.get("KANPAN_PORT", "").strip()
    if preferred:
        try:
            preferred_port = int(preferred)
        except ValueError:
            preferred_port = start
        else:
            if is_port_free(preferred_port):
                return preferred_port
    for port in range(start, start + attempts):
        if is_port_free(port):
            return port
    raise RuntimeError("未找到可用本地端口")


def wait_for_server(url: str, timeout_seconds: int = 20) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("currentDate"):
                    return
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"本地服务启动超时: {last_error}")


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def spawn_server(port: int) -> subprocess.Popen[str]:
    env = runtime_env()
    env["ASTOCK_SERVER_MODE"] = "1"
    env["KANPAN_PORT"] = str(port)
    return subprocess.Popen(
        [sys.executable],
        env=env,
        cwd=str(app_root()),
        creationflags=creation_flags(),
    )


def terminate_process(process: subprocess.Popen[str] | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def copy_to_clipboard(root: tk.Tk, text: str) -> None:
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()


def open_launcher_window(port: int, process: subprocess.Popen[str]) -> int:
    address = f"http://127.0.0.1:{port}/"
    root = tk.Tk()
    root.title("A股交易台")
    root.geometry("480x220")
    root.resizable(False, False)
    root.configure(bg="#0f1722")

    title = tk.Label(
        root,
        text="启动成功",
        fg="#e7eef7",
        bg="#0f1722",
        font=("Microsoft YaHei UI", 22, "bold"),
    )
    title.pack(pady=(24, 8))

    desc = tk.Label(
        root,
        text="本地看盘服务已启动，访问以下地址即可使用。",
        fg="#8fa2ba",
        bg="#0f1722",
        font=("Microsoft YaHei UI", 11),
    )
    desc.pack()

    address_var = tk.StringVar(value=address)
    entry = tk.Entry(
        root,
        textvariable=address_var,
        justify="center",
        font=("Consolas", 12),
        bd=0,
        relief="flat",
        bg="#152233",
        fg="#e7eef7",
        readonlybackground="#152233",
        width=34,
    )
    entry.configure(state="readonly")
    entry.pack(pady=16, ipady=8)

    status_var = tk.StringVar(value="关闭此窗口将自动结束后台服务")
    status = tk.Label(
        root,
        textvariable=status_var,
        fg="#8fa2ba",
        bg="#0f1722",
        font=("Microsoft YaHei UI", 10),
    )
    status.pack()

    button_bar = tk.Frame(root, bg="#0f1722")
    button_bar.pack(pady=18)

    def do_open() -> None:
        webbrowser.open(address)

    def do_copy() -> None:
        copy_to_clipboard(root, address)
        status_var.set("地址已复制")

    def on_close() -> None:
        terminate_process(process)
        root.destroy()

    open_button = tk.Button(
        button_bar,
        text="打开页面",
        command=do_open,
        width=12,
        bg="#cf3f2b",
        fg="#ffffff",
        activebackground="#f2543d",
        activeforeground="#ffffff",
        relief="flat",
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    open_button.pack(side="left", padx=6)

    copy_button = tk.Button(
        button_bar,
        text="复制地址",
        command=do_copy,
        width=12,
        bg="#177e9f",
        fg="#ffffff",
        activebackground="#23a0c8",
        activeforeground="#ffffff",
        relief="flat",
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    copy_button.pack(side="left", padx=6)

    close_button = tk.Button(
        button_bar,
        text="关闭程序",
        command=on_close,
        width=12,
        bg="#223246",
        fg="#e7eef7",
        activebackground="#30445c",
        activeforeground="#ffffff",
        relief="flat",
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    close_button.pack(side="left", padx=6)

    def watch_process() -> None:
        if process.poll() is not None:
            status_var.set("后台服务已退出，窗口即将关闭")
            root.after(1200, root.destroy)
            return
        root.after(1000, watch_process)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(1000, watch_process)
    root.mainloop()
    return 0


def run_launcher_mode() -> int:
    port = pick_port()
    process = spawn_server(port)
    try:
        wait_for_server(f"http://127.0.0.1:{port}/api/state")
    except Exception as exc:
        terminate_process(process)
        messagebox.showerror("启动失败", f"本地服务启动失败。\n\n{exc}")
        raise
    return open_launcher_window(port, process)


def main() -> int:
    try:
        if dispatch_embedded_script():
            return 0
        if os.environ.get("ASTOCK_SERVER_MODE", "").strip() == "1":
            return run_server_mode()
        return run_launcher_mode()
    except Exception:
        write_runtime_log(traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
