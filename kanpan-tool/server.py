#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import ctypes
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests


APP_ROOT = Path(os.getenv("ASTOCK_APP_ROOT", Path(__file__).resolve().parents[1])).resolve()
BUNDLE_ROOT = Path(os.getenv("ASTOCK_BUNDLE_ROOT", Path(__file__).resolve().parents[1])).resolve()
ROOT = BUNDLE_ROOT
DATA_ROOT = APP_ROOT / "data"
EXPORT_SCRIPT = BUNDLE_ROOT / "market-overview-exporter" / "scripts" / "fetch_market_overview.py"
INDEX_HTML = BUNDLE_ROOT / "kanpan-tool" / "index.html"
CONFIG_ROOT = APP_ROOT / ".local-config"
SETTINGS_FILE = CONFIG_ROOT / "terminal-settings.json"
DEFAULT_PORT = 8765
DEFAULT_MODEL = "kimi-k2.5"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
KIMI_BASE_URL = "https://api.moonshot.cn/v1/chat/completions"
KIMI_MODELS_URL = "https://api.moonshot.cn/v1/models"
KIMI_MAX_STREAM_SECONDS = 900
KIMI_MODEL_TEST_SECONDS = 60
PROMPT_REQUIREMENTS_FILE = ROOT / "解析提示词要求.md"
PROMPT_SYSTEM_FILES = {
    "open": ROOT / "开盘盘前解析-system-prompt-完整版.md",
    "intraday": ROOT / "盘中预期解析-system-prompt-完整版.md",
    "close": ROOT / "盘后全量复盘-system-prompt-完整版.md",
}
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

ANALYSIS_FRAMEWORK = """
分析框架要求：
1. 周期定位
   - 判断市场当前属于试错、主升、分歧、高位震荡、退潮中的哪个阶段。
   - 给出判断依据，优先引用指数环境、涨停家数、连板高度、封板率、量能、晋级率。
2. 情绪与结构
   - 判断情绪强弱、赚钱效应、亏钱效应、修复还是分歧。
   - 拆出高位、中位、低位的承接状态，不要只给笼统结论。
3. 主线与轮动
   - 区分核心主线、活跃支线、轮动题材、噪音题材。
   - 结合板块轮动前20天强度、日期复盘、涨停直播、异动播报交叉验证。
4. 核心个股与梯队
   - 找出龙头、最强跟风、补涨候选、中位风险标的。
   - 明确连板梯队是否完整，谁在强化，谁在掉队。
5. 竞价到盘中的验证链
   - 用竞价封单、竞价抢筹、指数、盘中异动、涨停直播串成果链。
   - 明确哪些信号是超预期，哪些是低于预期。
6. 操作预案
   - 输出“若走强 / 若分歧 / 若退潮”三套次日预案。
   - 给出适合关注的方向、适合回避的方向，不给买卖指令，不承诺收益。
7. 输出风格
   - 专业、克制、结构化，强调依据和推演。
   - 不模仿任何特定作者口吻，不提及来源作者。
""".strip()

TARGETS: dict[str, dict[str, str]] = {
    "jingjia": {"label": "竞价封单", "file": "jingjia.md"},
    "jjyd": {"label": "竞价异动（含5日竞价与竞价抢筹）", "file": "jjyd.md"},
    "global": {"label": "指数行情", "file": "global.md"},
    "ztlive": {"label": "涨停实时直播", "file": "ztlive.md"},
    "yidong": {"label": "异动播报", "file": "yidong.md"},
    "pool": {"label": "涨停股池", "file": "pool.md"},
    "amount": {"label": "成交额", "file": "amount.md"},
    "fupan": {"label": "日期复盘", "file": "fupan.md"},
    "platerotat": {"label": "板块轮动（前20天涨停强度）", "file": "platerotat.md"},
    "jinji": {"label": "涨停股票池晋级", "file": "jinji.md"},
}
TARGET_ORDER = list(TARGETS.keys())

JOBS: dict[str, dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
SETTINGS_LOCK = threading.RLock()

SOURCE_PATTERNS = [
    re.compile(r"^>\s*来源", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b[A-Z_]+_META\b"),
]

PUBLIC_FILE_LABELS = {
    "dashboard.md": "总览导航",
    "ai-analysis.md": "AI解析结果",
    "market_overview_summary.md": "市场总览汇总",
    "jingjia_summary.md": "竞价封单汇总",
    "jjyd_summary.md": "竞价异动汇总",
    "yidong_pool_summary.md": "异动与股池汇总",
}

PUBLIC_TEXT_REPLACEMENTS = {
    "[OK]": "已完成",
    "usage:": "命令参数：",
    "error:": "错误：",
    "Call log:": "浏览器调用记录：",
    "Page.goto:": "页面加载：",
    "Page.wait_for_function:": "页面等待：",
    "TimeoutError:": "超时：",
    "Timeout": "超时",
    "navigating to": "正在访问",
    "waiting until": "等待阶段",
    "domcontentloaded": "DOM已加载",
    "unrecognized arguments": "未识别参数",
    "Traceback (most recent call last):": "内部调用栈已隐藏",
    "market-overview-exporter": "内置模块",
    "duanxian-jingjia-exporter": "内置模块",
    "duanxian-workflow": "内置模块",
    "duanxian-yidong-pool": "内置模块",
    "duanxianxia": "数据源",
    "短线侠": "数据源",
}


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def sanitize_proxy_env(env: dict[str, str]) -> dict[str, str]:
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    return env


def subprocess_env() -> dict[str, str]:
    env = sanitize_proxy_env(os.environ.copy())
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def is_windows() -> bool:
    return os.name == "nt"


def hidden_subprocess_kwargs() -> dict[str, Any]:
    if not is_windows():
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def normalize_date(value: str | None) -> str:
    if not value:
        return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    if not isinstance(value, str):
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")
    cleaned = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        raise ValueError(f"非法日期：{value}")
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"非法日期：{value}") from exc
    return cleaned


def request_targets(payload: dict[str, Any]) -> list[str]:
    targets = payload.get("targets", TARGET_ORDER)
    if not isinstance(targets, list) or not targets:
        raise ValueError("请至少选择一个数据标签")
    if any(not isinstance(target, str) or target not in TARGETS for target in targets):
        raise ValueError("存在无法识别的数据标签")
    return list(dict.fromkeys(targets))


def validate_fetch_date(date_text: str, targets: list[str]) -> None:
    today = normalize_date(None)
    if date_text > today:
        raise ValueError(f"抓取日期不能晚于上海今日 {today}")
    live_targets = [target for target in targets if target != "jingjia"]
    if date_text != today and live_targets:
        raise ValueError(
            f"{format_target_labels(live_targets)}仅支持当天快照，请将交易日期切换至 {today}。"
            "历史归档仍可解析；历史竞价仅支持源站当前可见日期。"
        )


def latest_dates(limit: int = 30) -> list[str]:
    if not DATA_ROOT.exists():
        return []
    values = [
        child.name
        for child in DATA_ROOT.iterdir()
        if child.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name)
    ]
    return sorted(values, reverse=True)[:limit]


def available_files(date_text: str) -> list[dict[str, str]]:
    day_dir = DATA_ROOT / date_text
    if not day_dir.exists():
        return []
    results: list[dict[str, str]] = []
    for key in TARGET_ORDER:
        meta = TARGETS[key]
        if (day_dir / meta["file"]).exists():
            results.append({"target": key, "label": meta["label"], "file": meta["file"]})
    return results


def read_file(date_text: str, file_name: str) -> str:
    file_path = (DATA_ROOT / date_text / file_name).resolve()
    base_dir = (DATA_ROOT / date_text).resolve()
    if base_dir not in file_path.parents:
        raise ValueError("非法文件路径")
    if not file_path.exists():
        raise FileNotFoundError(file_name)
    return file_path.read_text(encoding="utf-8-sig")


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if any(pattern.search(raw_line) for pattern in SOURCE_PATTERNS):
            continue
        if raw_line.strip().startswith("<!--") and "META" in raw_line.upper():
            continue
        lines.append(raw_line)
    sanitized = "\n".join(lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized


def target_label(target: str) -> str:
    return TARGETS.get(target, {}).get("label", target)


def format_target_labels(targets: list[str], separator: str = " / ") -> str:
    ordered = [target for target in TARGET_ORDER if target in targets]
    if len(ordered) != len(targets):
        ordered.extend(target for target in targets if target not in ordered)
    return separator.join(target_label(target) for target in ordered)


def build_job_title(job_type: str, payload: dict[str, Any]) -> str:
    date_text = str(payload.get("date", "") or "").strip()
    targets = [str(item) for item in payload.get("targets", []) if str(item).strip()]
    action = {
        "fetch": "数据抓取",
        "analyze": "Kimi 解析",
    }.get(job_type, "任务执行")
    target_text = format_target_labels(targets)
    return " ".join(part for part in [date_text, action, target_text] if part)


def display_name_for_file(file_name: str) -> str:
    for meta in TARGETS.values():
        if meta["file"] == file_name:
            return f"{meta['label']}数据"
    return PUBLIC_FILE_LABELS.get(file_name, "数据文件")


def sanitize_public_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""

    hidden_patterns = [
        r'^\s*File ".*", line \d+',
        r"^\s*raise SystemExit",
        r"^\s*return task\.result",
        r"^\s*\^+$",
        r"^\s*~+\^*$",
        r"^\s*\.\.\.<.*>\.\.\.$",
    ]
    if any(re.match(pattern, cleaned) for pattern in hidden_patterns):
        return ""

    ok_match = re.match(r"^\[OK\]\s+([a-z0-9_-]+)\s*->\s*([a-z0-9_.-]+)$", cleaned, flags=re.IGNORECASE)
    if ok_match:
        return f"已写入：{target_label(ok_match.group(1).lower())}"

    skip_match = re.match(r"^SKIP\s+(.+)$", cleaned, flags=re.IGNORECASE)
    if skip_match:
        file_name = Path(skip_match.group(1)).name
        return f"跳过写入：{display_name_for_file(file_name)}（内容无变化）"

    windows_path_only = re.match(r"^[A-Za-z]:[\\/].+$", cleaned)
    if windows_path_only:
        file_name = Path(cleaned).name
        return f"已更新：{display_name_for_file(file_name)}"

    generic_line_map = {
        r"^输出(?:根)?目录[:：]\s*.+$": "数据目录已更新",
        r"^导航文件[:：]\s*.+$": "总览导航已更新",
        r"^AI .*文件[:：]\s*.+$": "AI解析入口已更新",
        r"^汇总文件[:：]\s*.+$": "汇总索引已更新",
    }
    for pattern, replacement in generic_line_map.items():
        if re.match(pattern, cleaned):
            return replacement

    cleaned = re.sub(r"https?://[^\s]+", "[数据页]", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[A-Za-z]:[/\\][^\s]+", "[本机路径]", cleaned)
    cleaned = re.sub(r"_MEI\d+", "运行目录", cleaned)

    for source, replacement in PUBLIC_TEXT_REPLACEMENTS.items():
        cleaned = cleaned.replace(source, replacement)

    for file_name in PUBLIC_FILE_LABELS:
        cleaned = cleaned.replace(file_name, display_name_for_file(file_name))
    for key, meta in TARGETS.items():
        cleaned = re.sub(rf"\b{re.escape(key)}\b", meta["label"], cleaned)
        cleaned = cleaned.replace(meta["file"], f"{meta['label']}数据")

    cleaned = cleaned.replace("首页底部涨停股票池", "涨停股票池晋级")
    cleaned = cleaned.replace("MOONSHOT_API_KEY", "Kimi API Key")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
    if not blob.cbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    if not is_windows():
        return raw.hex()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise OSError("无法加密本地密钥")
    try:
        return _bytes_from_blob(out_blob).hex()
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    raw = bytes.fromhex(value)
    if not is_windows():
        return raw.decode("utf-8")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise OSError("无法解密本地密钥")
    try:
        return _bytes_from_blob(out_blob).decode("utf-8")
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)


def load_settings() -> dict[str, str]:
    with SETTINGS_LOCK:
        if not SETTINGS_FILE.exists():
            return {"kimi_key": "", "kimi_model": DEFAULT_MODEL}
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"kimi_key": "", "kimi_model": DEFAULT_MODEL}

        encrypted_key = str(raw.get("kimi_key", "") or "")
        return {
            "kimi_key": unprotect_secret(encrypted_key) if encrypted_key else "",
            "kimi_model": str(raw.get("kimi_model", DEFAULT_MODEL) or DEFAULT_MODEL),
        }


def save_settings(kimi_key: str, kimi_model: str, keep_existing_key: bool = False) -> dict[str, str]:
    with SETTINGS_LOCK:
        CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
        existing = load_settings()
        effective_key = existing["kimi_key"] if keep_existing_key and not kimi_key.strip() else kimi_key.strip()
        payload = {
            "kimi_key": protect_secret(effective_key) if effective_key else "",
            "kimi_model": kimi_model.strip() or DEFAULT_MODEL,
            "updated_at": now_text(),
        }
        temporary = SETTINGS_FILE.with_name(f"{SETTINGS_FILE.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
            os.replace(temporary, SETTINGS_FILE)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "kimi_key": effective_key,
            "kimi_model": payload["kimi_model"],
        }


def masked_key(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return cleaned[:4] + "*" * (len(cleaned) - 8) + cleaned[-4:]


def resolve_kimi_key(api_key: str | None, stored_key: str | None = None) -> str:
    return (
        (api_key or "").strip()
        or (stored_key or "").strip()
        or os.getenv("MOONSHOT_API_KEY", "").strip()
    )


def data_quality(content: str, date_text: str, target: str) -> dict[str, Any]:
    """采集时间只描述本机快照，不冒充源站行情截止时间。"""
    def field(pattern: str) -> str:
        match = re.search(pattern, content, re.MULTILINE)
        return match.group(1).strip() if match else ""

    data_date = field(r"^>\s*数据日期[：:]\s*(\d{4}-\d{2}-\d{2})")
    if not data_date:
        data_date = field(r"^# .*?(\d{4}-\d{2}-\d{2})\s*$")
    capture_field = re.search(r"^>[ \t]*(?:生成|采集|抓取)时间[：:][ \t]*([^\r\n]*)", content, re.MULTILINE)
    captured_at = capture_field.group(1).strip() if capture_field else ""
    now = datetime.strptime(now_text(), "%Y-%m-%d %H:%M:%S")
    try:
        captured = datetime.strptime(captured_at, "%Y-%m-%d %H:%M:%S") if captured_at else None
    except ValueError:
        captured = None
    count_text = field(r"^>\s*(?:股票数量|个股条数|本次导出条数|指数数量|涨停直播条数|竞价异动条数)[：:]\s*(\d+)")
    lines = content.splitlines()
    meaningful = False
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith(("#", ">", "<!--")):
            continue
        if text.startswith("|"):
            if index + 1 < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[index + 1]):
                continue  # 表头不是一条行情。
            if not text.strip("| :-\t"):
                continue
        elif re.fullmatch(r"[-*_]+", text):
            continue
        meaningful = True
        break
    quality, message, usable = "ready", "数据可用", True
    if not meaningful:
        quality, message, usable = "empty", "空数据或无有效正文，请重新抓取", False
    elif data_date and data_date != date_text:
        quality, message, usable = "wrong_date", f"数据日期为 {data_date}，与当前交易日不符", False
    elif capture_field and (captured is None or (captured - now).total_seconds() > 60):
        quality, message, usable = "invalid_time", "采集时间无效或晚于本机时间，请核对资料", False
    elif not captured_at or not data_date:
        quality, message = "unknown", "旧版资料时间信息不完整，无法校验时效"
    else:
        if captured.date().isoformat() != date_text:
            # 补抓历史竞价允许晚于数据日期采集，但不能早于该交易日。
            if captured.date().isoformat() < date_text:
                quality, message, usable = "wrong_date", "采集日期早于数据日期", False
        if usable and date_text == now.date().isoformat() and target not in {"jingjia", "jjyd"} and now.weekday() < 5:
            session_start = now.replace(hour=9, minute=30, second=0)
            cutoff = min(now, now.replace(hour=15, minute=0, second=0))
            if now.replace(hour=11, minute=30, second=0) <= now < now.replace(hour=13, minute=0, second=0):
                cutoff = now.replace(hour=11, minute=30, second=0)
            if now >= session_start and (cutoff - captured).total_seconds() > 15 * 60:
                quality, message, usable = "stale", "动态数据超过 15 分钟，请更新后分析", False
    return {
        "data_date": data_date, "captured_at": captured_at,
        "row_count": int(count_text) if count_text else None,
        "quality": quality, "quality_message": message, "usable": usable,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def collect_target_contents(date_text: str, targets: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with JOB_LOCK:
        fetch_statuses = {}
        for job in JOBS.values():
            if job["type"] == "fetch" and job["payload"].get("date") == date_text:
                fetch_statuses.update(job.get("target_statuses", {}))
    for target in targets:
        meta = TARGETS[target]
        path = DATA_ROOT / date_text / meta["file"]
        if not path.exists():
            results.append(
                {
                    "target": target,
                    "label": meta["label"],
                    "file": meta["file"],
                    "content": "",
                    "exists": "false",
                    "quality": "missing", "quality_message": "尚未抓取成功",
                    "usable": False, "data_date": "", "captured_at": "", "row_count": None,
                }
            )
            continue
        try:
            content = sanitize_text(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError):
            results.append({
                "target": target, "label": meta["label"], "file": meta["file"],
                "content": "", "exists": "true", "usable": False,
                "quality": "read_error", "quality_message": "资料读取失败，请检查文件或重新抓取",
                "data_date": "", "captured_at": "", "row_count": None,
            })
            continue
        item = {
                "target": target,
                "label": meta["label"],
                "file": meta["file"],
                "content": content,
                "exists": "true",
                **data_quality(content, date_text, target),
            }
        if fetch_statuses.get(target) == "failed":
            item.update(quality="failed", quality_message="本次抓取失败，旧数据已保留，请重新抓取", usable=False, fetch_status="failed")
        elif fetch_statuses.get(target) in {"running", "pending"}:
            item.update(quality="updating", quality_message="正在更新，请等待抓取完成", usable=False)
        results.append(item)
    return results


def validate_target_data(items: list[dict[str, Any]]) -> None:
    problems = [f"{item['label']}：{item['quality_message']}" for item in items if not item["usable"]]
    if problems:
        raise RuntimeError("资料尚未抓取成功或不可用；" + "；".join(problems))


def analysis_context(date_text: str, preset: str, items: list[dict[str, Any]]) -> dict[str, str]:
    generated = now_text()
    captures = sorted(item["captured_at"] for item in items if item.get("captured_at"))
    month_day = date_text[5:]
    if preset == "open":
        title_time = f"{month_day} 09:25"
    elif preset == "close":
        title_time = f"{month_day} 15:00"
    elif captures and captures[-1][:10] == date_text:
        title_time = f"{month_day} {captures[-1][11:16]}"
    else:
        title_time = f"{month_day} 时间未知"
    return {
        "title_time": title_time, "generated_at": generated,
        "capture_start": captures[0] if captures else "",
        "capture_end": captures[-1] if captures else "",
        "time_note": "采集时间为本机获取资料的时间，源站行情截止时间未统一提供。",
    }


def export_command(date_text: str, target: str | None = None) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(EXPORT_SCRIPT))
    command.extend(["--output-root", str(DATA_ROOT), "--date", date_text])
    if target is None:
        command.append("--rebuild-only")
    else:
        command.extend(["--targets", target, "--skip-index"])
    return command


PROMPT_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "intro": "你是一名 A 股短线复盘分析助手，请基于 {date_text} 的以下数据做结构化分析。",
        "framework": ANALYSIS_FRAMEWORK,
        "sections": [
            "一、市场周期定位",
            "二、情绪与赚钱效应",
            "三、主线、支线与轮动结构",
            "四、核心个股、龙头梯队与补涨关系",
            "五、竞价到盘中的关键信号验证",
            "六、风险点与失败信号",
            "七、次日预案（走强 / 分歧 / 退潮）",
            "八、一句话结论",
        ],
        "notes": [
            "如果数据之间出现冲突，优先解释冲突而不是强行下结论。",
            "只基于已给出的标签数据判断，不要脑补未抓取模块。",
        ],
    },
    "open": {
        "intro": "你是一名 A 股短线盘前解析助手，请基于 {date_text} 开盘前已抓取的数据给出盘前预期。",
        "framework": "\n".join(
            [
                "分析要求：",
                "1. 只围绕竞价封单、竞价异动、竞价抢筹判断开盘预期，不扩展到未提供的数据。",
                "2. 先判断整体竞价情绪，再判断核心方向是否超预期或低于预期。",
                "3. 明确指出哪些信号适合开盘后继续验证，哪些信号需要谨慎。",
                "4. 不给买卖指令，不承诺收益，只输出盘前预期和验证点。",
            ]
        ),
        "sections": [
            "一、盘前情绪温度",
            "二、竞价最强方向与最强个股",
            "三、超预期 / 低于预期信号",
            "四、开盘后第一观察点",
            "五、盘前风险提醒",
            "六、一句话盘前结论",
        ],
        "notes": [
            "如果竞价数据不足以支持明确结论，要直接说明不确定性来源。",
        ],
    },
    "intraday": {
        "intro": "你是一名 A 股短线盘中预期分析助手，请基于 {date_text} 当前已抓取的数据判断盘中结构与后续预期。",
        "framework": "\n".join(
            [
                "分析要求：",
                "1. 结合竞价、抢筹、连板梯队和异动播报，判断当前主线强弱与市场承接。",
                "2. 要区分已经验证的信号、正在强化的信号、可能失败的信号。",
                "3. 输出午后或后续盘中观察重点，不做确定性结论包装。",
                "4. 结论必须贴合盘中节奏，避免写成盘后复盘。",
            ]
        ),
        "sections": [
            "一、当前情绪与承接状态",
            "二、连板梯队与核心个股强弱",
            "三、异动信号是否形成主线验证",
            "四、后续盘中预期与观察点",
            "五、风险点与失败信号",
            "六、一句话盘中结论",
        ],
        "notes": [
            "若标签之间信息冲突，请明确指出最值得相信的那一条证据链。",
        ],
    },
    "close": {
        "intro": "你是一名 A 股短线盘后复盘助手，请基于 {date_text} 的全量数据给出完整复盘和次日预案。",
        "framework": ANALYSIS_FRAMEWORK,
        "sections": [
            "一、市场周期定位",
            "二、情绪修复 / 分歧 / 退潮判断",
            "三、主线、支线与轮动结构",
            "四、龙头梯队、补涨与掉队关系",
            "五、竞价到收盘的关键验证链",
            "六、风险点与失败信号",
            "七、次日预案（走强 / 分歧 / 退潮）",
            "八、一句话盘后结论",
        ],
        "notes": [
            "盘后复盘需要把日内节奏和次日预案连起来，不要只做静态总结。",
        ],
    },
}


def resolve_prompt_preset(preset: str | None) -> str:
    key = (preset or "default").strip().lower()
    return key if key in PROMPT_PRESETS else "default"


@lru_cache(maxsize=8)
def read_prompt_template(path: str) -> str:
    prompt_path = Path(path)
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8").strip()


def prompt_requirements_text() -> str:
    return read_prompt_template(str(PROMPT_REQUIREMENTS_FILE))


def prompt_system_text(preset_key: str) -> str:
    path = PROMPT_SYSTEM_FILES.get(preset_key)
    if not path:
        return ""
    return read_prompt_template(str(path))


def prompt_time_title(date_text: str, preset_key: str, context: dict[str, str] | None = None) -> str:
    if context:
        return context["title_time"]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date_text.strip())
    if match:
        month_day = f"{match.group(2)}-{match.group(3)}"
    else:
        month_day = date_text.strip()

    if preset_key == "open":
        return f"{month_day} 09:25"
    if preset_key == "intraday":
        return datetime.now(SHANGHAI_TZ).strftime(f"{month_day} %H:%M")
    if preset_key == "close":
        return f"{month_day} 15:00"
    return month_day


def prompt_field_alias(preset_key: str, target: str) -> str:
    if preset_key == "close":
        return {
            "global": "zhishu",
            "ztlive": "ztzhibo",
            "pool": "ztguchi",
            "amount": "chengjiao",
            "fupan": "riqi_fupan",
            "platerotat": "bankuaistrength",
        }.get(target, target)
    return target


def prompt_allowed_aliases(preset_key: str, targets: list[str]) -> list[str]:
    return [prompt_field_alias(preset_key, target) for target in targets]


def prompt_requirements_summary(preset_key: str, title_time: str, targets: list[str]) -> list[str]:
    if not prompt_requirements_text():
        return []

    summary = [
        f"首行必须只输出时间标题：{title_time}",
        "时间标题格式固定为 MM-DD HH:MM，不要加 #、不要加解释、不要加方括号。",
        f"本次只允许使用这些数据字段：{', '.join(prompt_allowed_aliases(preset_key, targets))}",
        "不要复述提示词规则，不要输出教学说明，不要输出占位符，不要重复同一结论区块。",
        "必须先写事实，再写推演，再写预案；推演不能写成既成事实。",
        "兔子、乌龟只允许定义为同题材或同分支内部角色；写作时必须带题材归属，不能把它们当成全市场总龙。",
        "周期龙、情绪龙只允许用于影响整个短线梯队或全市场风险偏好的核心；若只是单题材领涨，只能写题材龙头、题材核心或情绪先锋。",
        "周期龙强调破局、分离旧龙、带动梯队切换；情绪龙强调影响全市场风险偏好和同批高位反馈，二者不要混用。",
        "如果证据不足，不要强行指定兔子、乌龟、周期龙、情绪龙，宁可写成候选、观察对象或题材核心。",
    ]
    if preset_key == "intraday":
        summary.extend(
            [
                "盘中必须结合竞价强弱、晋级结构、异动/回封/炸板去判断板块轮动是在强化、回流、脉冲还是证伪，不能只因一只股上板就认定题材切换。",
                "盘中若没有近20日板块强度数据，只能写盘中轮动迹象与当前资金选择，不能替代盘后对板块轮动的最终定性。",
                "盘中周期节点必须落到当前可见证据：老高标是否掉队、1进2/2进3是否成批确认、异动是否向新方向集中；否则最多写试错节点或候选节点。",
                "盘中结论只能写到当前时点，不得把午后和收盘写成已经发生。",
            ]
        )
    elif preset_key == "open":
        summary.extend(
            [
                "盘前兔子、乌龟只能给候选，不要在盘前直接确认周期龙或情绪龙。",
                "盘前对板块轮动和周期节点只能给验证点与观察顺序，不得提前宣告新周期成立或大节点确认。",
                "盘前只能做开盘预期和验证点，不得写成收盘定论。",
            ]
        )
    elif preset_key == "close":
        summary.extend(
            [
                "盘后必须把板块轮动写清楚：区分主线、次主线、轮动题材、退潮题材，并说明它们与近20日板块强度、涨停直播进攻顺序、异动播报强弱、晋级梯队结果是否相互印证。",
                "近20日板块强度只能作为背景，不等于当天主线；当天主线必须同时看到核心带动、梯队完整、赚钱效应和时间顺序配合。",
                "盘后必须明确周期节点是大节点还是小节点；题材内部补涨切换、卡位切换只能算小节点，只有老周期负反馈/见顶与新方向确认或指数共振同时出现，才可以上升到大节点或新周期。",
                "若只是日内避险、消息脉冲或单题材发酵，不能直接写成新周期主线。",
                "盘后需要完成完整复盘，但不要机械重复相同数据和结论。",
            ]
        )
    return summary


def build_external_prompt(
    date_text: str,
    targets: list[str],
    *,
    preset_key: str,
    max_chars: int | None = 80000,
    per_target_limit: int | None = 15000,
    target_data: list[dict[str, Any]] | None = None,
    context: dict[str, str] | None = None,
) -> str:
    system_text = prompt_system_text(preset_key)
    if not system_text:
        return ""

    title_time = prompt_time_title(date_text, preset_key, context)
    requirement_lines = prompt_requirements_summary(preset_key, title_time, targets)
    parts = [
        "【场景系统提示词】",
        system_text,
        "",
    ]
    if requirement_lines:
        parts.extend(
            [
                "【补充执行约束】",
                *[f"- {line}" for line in requirement_lines],
                "",
            ]
        )

    parts.extend(
        [
            "【本次数据输入】",
            f"日期：{date_text}",
            f"输出时间标题：{title_time}",
        ]
    )
    if preset_key == "intraday":
        parts.append(f"当前时间：{title_time.split(' ', 1)[-1]}")
    if context:
        parts.extend([
            f"生成时间：{context['generated_at']}",
            f"资料采集区间：{context['capture_start'] or '未知'} 至 {context['capture_end'] or '未知'}",
            context["time_note"],
            "采集时间不代表行情截止时间；时间未知时不得补写当前时间。",
        ])
    parts.append("")

    total_chars = 0
    for target in targets:
        meta = TARGETS[target]
        content = next(item["content"] for item in target_data if item["target"] == target) if target_data is not None else sanitize_text(read_file(date_text, meta["file"]))
        clipped = content
        if max_chars is not None:
            budget = max_chars - total_chars
            if per_target_limit is not None:
                budget = min(per_target_limit, budget)
            if budget <= 0:
                break
            clipped = content[:budget]
        elif per_target_limit is not None:
            clipped = content[:per_target_limit]
        total_chars += len(clipped)
        alias = prompt_field_alias(preset_key, target)
        parts.append(f"### 数据模块：[{alias}] {meta['label']}")
        parts.append(clipped)
        if len(clipped) < len(content):
            parts.append(f"\n[已截断，原文本长度 {len(content)} 字符]\n")
    return "\n".join(parts)


def build_prompt(
    date_text: str,
    targets: list[str],
    prompt_override: str | None = None,
    preset: str | None = None,
    max_chars: int | None = 80000,
    per_target_limit: int | None = 15000,
    target_data: list[dict[str, Any]] | None = None,
    context: dict[str, str] | None = None,
) -> str:
    if prompt_override and prompt_override.strip():
        return prompt_override.strip()

    preset_key = resolve_prompt_preset(preset)
    if preset_key in PROMPT_SYSTEM_FILES:
        external_prompt = build_external_prompt(
            date_text,
            targets,
            preset_key=preset_key,
            max_chars=max_chars,
            per_target_limit=per_target_limit,
            target_data=target_data,
            context=context,
        )
        if external_prompt:
            return external_prompt

    spec = PROMPT_PRESETS[preset_key]
    parts = [
        str(spec["intro"]).format(date_text=date_text),
        str(spec["framework"]),
        "",
        "固定输出章节：",
        *[str(section) for section in spec["sections"]],
        "",
    ]
    for note in spec.get("notes", []):
        parts.append(f"- {note}")
    if spec.get("notes"):
        parts.append("")
    total_chars = 0
    for target in targets:
        meta = TARGETS[target]
        content = next(item["content"] for item in target_data if item["target"] == target) if target_data is not None else sanitize_text(read_file(date_text, meta["file"]))
        clipped = content
        if max_chars is not None:
            budget = max_chars - total_chars
            if per_target_limit is not None:
                budget = min(per_target_limit, budget)
            if budget <= 0:
                break
            clipped = content[:budget]
        elif per_target_limit is not None:
            clipped = content[:per_target_limit]
        total_chars += len(clipped)
        parts.append(f"### 数据模块：{meta['label']}")
        parts.append(clipped)
        if len(clipped) < len(content):
            parts.append(f"\n[已截断，原文本长度 {len(content)} 字符]\n")
    return "\n".join(parts)


def compact_prompt_for_kimi(prompt: str, max_chars: int = 24000, section_limit: int = 5200) -> str:
    text = (prompt or "").strip()
    if len(text) <= max_chars:
        return text

    marker = "### 数据模块："
    notice_template = "\n\n[已为 Kimi 压缩过长提示词，原始长度 {original} 字符，发送长度 {current} 字符]"

    if marker not in text:
        reserved = len(notice_template.format(original=len(text), current=max_chars))
        clipped = text[: max(0, max_chars - reserved)]
        notice = notice_template.format(original=len(text), current=len(clipped))
        return clipped.rstrip() + notice

    intro, *sections = text.split(marker)
    intro = intro.strip()
    notice_budget = len(notice_template.format(original=len(text), current=max_chars))
    content_budget = max(0, max_chars - notice_budget)
    intro = intro[:max(0, content_budget - len(sections) * 120)].rstrip()
    remaining_budget = content_budget - len(intro) - 2
    kept_sections: list[str] = []

    for index, raw_section in enumerate(sections):
        if remaining_budget <= 0:
            break
        section_text = f"{marker}{raw_section.strip()}"
        remaining_sections = len(sections) - index
        allowed = min(section_limit, max(0, (remaining_budget - 2 * (remaining_sections - 1)) // remaining_sections))
        if allowed <= 0:
            break
        clipped = section_text[:allowed].rstrip()
        kept_sections.append(clipped)
        remaining_budget -= len(clipped) + 2

    compacted = intro
    if kept_sections:
        compacted = intro + "\n\n" + "\n\n".join(kept_sections)
    notice = notice_template.format(original=len(text), current=len(compacted))
    allowed_notice_budget = max_chars - len(notice)
    compacted = compacted[:allowed_notice_budget].rstrip()
    notice = notice_template.format(original=len(text), current=len(compacted))
    return compacted + notice


def kimi_configuration_error(error: Exception, api_key: str) -> RuntimeError:
    if isinstance(error, requests.HTTPError) and error.response is not None:
        detail = f"Kimi HTTP {error.response.status_code}: {error.response.text.strip()}"
    elif isinstance(error, requests.Timeout):
        detail = "Kimi 请求超时，请稍后重试。"
    elif isinstance(error, requests.RequestException):
        detail = f"Kimi 连接失败：{error}"
    else:
        detail = str(error)
    return RuntimeError(detail.replace(api_key, "[已隐藏]")[:2000])


def list_kimi_models(api_key: str) -> list[str]:
    response = None
    try:
        response = requests.get(KIMI_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=(10, 30))
        response.raise_for_status()
        data = response.json()
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Kimi 模型列表响应格式不正确。")
        models = sorted({item["id"].strip() for item in rows if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()})
        if not models:
            raise RuntimeError("Kimi 未返回可用模型，请检查当前密钥的模型权限。")
        return models
    except Exception as exc:
        raise kimi_configuration_error(exc, api_key) from exc
    finally:
        if response is not None:
            response.close()


def read_kimi_response(
    response: requests.Response,
    on_progress: Callable[[str], None] | None = None,
    *,
    max_seconds: int = KIMI_MAX_STREAM_SECONDS,
) -> str:
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        data = response.json()
        content = data["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Kimi 未返回分析正文。")
        if data["choices"][0].get("finish_reason") not in {None, "stop"}:
            raise RuntimeError("Kimi 分析未完整生成，请检查模型输出限制。")
        return content

    response.encoding = "utf-8"
    parts: list[str] = []
    completed = False
    phase = ""
    started_at = last_progress_at = time.monotonic()
    for line in response.iter_lines(decode_unicode=True):
        now = time.monotonic()
        if now - started_at > max_seconds:
            raise RuntimeError(f"Kimi 响应超过 {max_seconds} 秒，已停止本次请求，请稍后手动重试。")
        if not line or not line.startswith("data:"):
            continue
        event_text = line[5:].strip()
        if event_text == "[DONE]":
            completed = True
            break
        event = json.loads(event_text)
        if event.get("error"):
            raise RuntimeError(f"Kimi 返回错误：{event['error']}")
        choices = event.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
            current_phase = "正在生成分析正文"
        elif delta.get("reasoning_content"):
            current_phase = "模型正在思考"
        else:
            current_phase = phase
        if current_phase and (current_phase != phase or now - last_progress_at >= 15):
            if on_progress:
                on_progress(current_phase)
            phase, last_progress_at = current_phase, now
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            if finish_reason != "stop":
                raise RuntimeError(f"Kimi 分析未完整生成（finish_reason={finish_reason}），请检查模型输出限制。")
            completed = True
    if not completed:
        raise RuntimeError("Kimi 连接提前结束，分析未完整接收，请手动重试。")
    content = "".join(parts)
    if not content.strip():
        raise RuntimeError("Kimi 未返回分析正文。")
    return content


def test_kimi_model(api_key: str, model: str) -> dict[str, Any]:
    response = None
    started_at = time.monotonic()
    try:
        response = requests.post(
            KIMI_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "请只回复 OK。"}],
                "temperature": 1,
                "max_tokens": 4096,
                "stream": True,
            },
            stream=True,
            timeout=(10, 30),
        )
        response.raise_for_status()
        reply = read_kimi_response(response, max_seconds=KIMI_MODEL_TEST_SECONDS)
        return {"ok": True, "model": model, "reply": reply.replace(api_key, "[已隐藏]")[:4000], "elapsed_ms": round((time.monotonic() - started_at) * 1000)}
    except Exception as exc:
        raise kimi_configuration_error(exc, api_key) from exc
    finally:
        if response is not None:
            response.close()


def call_kimi(api_key: str, model: str, prompt: str, *, on_progress: Callable[[str], None] | None = None,
              on_prompt: Callable[[str], None] | None = None) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是专业的 A 股短线看盘和复盘分析助手。"
                    "你必须优先遵循用户消息中的角色设定、术语口径、时间标题格式、输出结构和数据边界。"
                    "你只能基于用户提供的数据做判断，不得脑补未提供模块，不得把推演写成事实。"
                    "语言要求克制、结构化、基于证据，不模仿任何网络作者风格，也不提及来源作者。"
                ),
            },
        ],
        "temperature": 1,
        "stream": True,
    }
    prompt_candidates = [prompt]
    compact_prompt = compact_prompt_for_kimi(prompt)
    if compact_prompt != prompt:
        prompt_candidates.append(compact_prompt)
    for index, candidate_prompt in enumerate(prompt_candidates):
        payload = dict(base_payload)
        payload["messages"] = [*base_payload["messages"], {"role": "user", "content": candidate_prompt}]
        response = None
        try:
            if on_prompt:
                on_prompt(candidate_prompt)
            if on_progress:
                on_progress(f"Kimi 发送长度：{len(candidate_prompt)} 字符，等待流式响应")
            response = requests.post(KIMI_BASE_URL, headers=headers, json=payload, stream=True, timeout=(10, 90))
            response.raise_for_status()
            return read_kimi_response(response, on_progress)
        except requests.HTTPError as exc:
            failed_response = exc.response if exc.response is not None else response
            response_text = failed_response.text.strip() if failed_response is not None else ""
            status = failed_response.status_code if failed_response is not None else "未知"
            prompt_too_long = status in {400, 413} and any(
                keyword in response_text.lower()
                for keyword in ("context_length_exceeded", "maximum context length", "context window", "too many tokens", "token limit", "prompt is too long", "input is too long", "上下文长度")
            )
            if prompt_too_long and index < len(prompt_candidates) - 1:
                if on_progress:
                    on_progress("输入超过模型上下文限制，将压缩所选数据模块后重新发送")
                continue
            detail = (response_text or str(exc)).replace(api_key, "[已隐藏]")[:2000]
            raise RuntimeError(f"Kimi HTTP {status}: {detail} (api_prompt_length={len(candidate_prompt)})") from exc
        except requests.Timeout as exc:
            raise RuntimeError("等待 Kimi 响应超时（连接 10 秒，连续无数据 90 秒），已停止本次请求；请手动重试。") from exc
        except requests.RequestException as exc:
            detail = str(exc).replace(api_key, "[已隐藏]")[:1000]
            raise RuntimeError(f"Kimi 连接中断，已停止本次请求，请手动重试：{detail}") from exc
        except Exception as exc:
            raise RuntimeError(str(exc).replace(api_key, "[已隐藏]")) from exc
        finally:
            if response is not None:
                response.close()
    raise RuntimeError("Kimi 未返回分析结果。")


def build_prompt_payload(
    date_text: str,
    targets: list[str],
    prompt_override: str | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    target_data = collect_target_contents(date_text, targets)
    validate_target_data(target_data)
    preset_key = resolve_prompt_preset(preset)
    context = analysis_context(date_text, preset_key, target_data)
    prompt = build_prompt(
        date_text,
        targets,
        prompt_override=prompt_override,
        preset=preset_key,
        max_chars=None,
        per_target_limit=None,
        target_data=target_data,
        context=context,
    )
    return {
        "date": date_text,
        "targets": targets,
        "prompt": prompt,
        "preset": preset_key,
        "available_count": len(target_data),
        "target_data": target_data,
        "context": context,
    }


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding=encoding, newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_analysis(date_text: str, slug: str, content: str, metadata: dict[str, Any] | None = None) -> str:
    safe_slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "analysis"
    path = DATA_ROOT / date_text / f"kimi-analysis-{safe_slug}-{uuid.uuid4().hex}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, content, "utf-8-sig")
    record = {
        **(metadata or {}), "id": path.name, "date": date_text,
        "analysis": content, "analysis_file": path.name, "legacy": False,
    }
    atomic_write(path.with_suffix(".json"), json.dumps(record, ensure_ascii=False, indent=2))
    return path.name


def read_analysis(date_text: str, record_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"kimi-analysis-[a-z0-9-]+\.md", record_id):
        raise ValueError("非法分析记录标识")
    path = (DATA_ROOT / date_text / record_id).resolve()
    if path.parent != (DATA_ROOT / date_text).resolve() or not path.is_file():
        raise FileNotFoundError("分析记录不存在")
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("id") != record_id or record.get("date") != date_text:
            raise ValueError("分析记录索引损坏")
        if not isinstance(record.get("analysis"), str):
            raise ValueError("分析记录正文损坏")
        for key in ("model", "preset", "generated_at", "prompt"):
            if key in record and not isinstance(record[key], str):
                raise ValueError("分析记录索引损坏")
        targets = record.get("targets", [])
        context = record.get("context", {})
        if (not isinstance(targets, list)
                or any(not isinstance(target, str) or target not in TARGETS for target in targets)
                or not isinstance(context, dict)
                or any(not isinstance(value, str) for value in context.values())):
            raise ValueError("分析记录索引损坏")
        return record
    if re.search(r"-[0-9a-f]{32}\.md$", record_id):
        raise FileNotFoundError("分析记录未完整保存")
    content = path.read_text(encoding="utf-8-sig")
    slug = path.stem.removeprefix("kimi-analysis-")
    targets = TARGET_ORDER if slug == "all" else [part for part in slug.split("-") if part in TARGETS]
    title = content.splitlines()[0].strip() if content.strip() else ""
    title_time = title if re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", title) else f"{date_text[5:]} 时间未知"
    preset = "open" if title_time.endswith("09:25") else "close" if title_time.endswith("15:00") else "intraday" if len(targets) == 4 else "default"
    return {
        "id": record_id, "date": date_text, "analysis": content, "analysis_file": record_id,
        "legacy": True, "targets": targets, "preset": preset, "model": "旧版未记录",
        "prompt": "", "generated_at": "", "context": {
            "title_time": title_time, "capture_start": "", "capture_end": "", "generated_at": "",
            "time_note": "旧版记录未保存输入快照和模型信息。",
        },
    }


def analysis_history(date_text: str) -> list[dict[str, Any]]:
    records = []
    for path in (DATA_ROOT / date_text).glob("kimi-analysis-*.md"):
        try:
            record = read_analysis(date_text, path.name)
        except (ValueError, OSError):
            continue
        records.append({key: record.get(key) for key in ("id", "date", "preset", "model", "generated_at", "context", "targets", "legacy")})
    return sorted(records, key=lambda item: (item["generated_at"] or "", item["id"]), reverse=True)


def create_job(job_type: str, title: str, payload: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex
    target_statuses = {target: "pending" for target in payload.get("targets", [])}
    public_title = build_job_title(job_type, payload) or sanitize_public_text(title) or title
    with JOB_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "type": job_type,
            "title": public_title,
            "status": "running",
            "created_at": now_text(),
            "updated_at": now_text(),
            "logs": [f"[{now_text()}] 开始任务：{public_title}"],
            "payload": payload,
            "target_statuses": target_statuses,
            "result": None,
            "error": None,
        }
    return job_id


def append_job_log(job_id: str, message: str) -> None:
    public_message = sanitize_public_text(message)
    if not public_message:
        return
    with JOB_LOCK:
        job = JOBS[job_id]
        job["logs"].append(f"[{now_text()}] {public_message}")
        job["updated_at"] = now_text()


def finish_job(job_id: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    public_error = sanitize_public_text(error) if error else None
    with JOB_LOCK:
        job = JOBS[job_id]
        job["status"] = "failed" if public_error else "completed"
        job["result"] = result
        job["error"] = public_error
        job["updated_at"] = now_text()
        tail = f"任务失败：{public_error}" if public_error else "任务完成"
        job["logs"].append(f"[{now_text()}] {tail}")


def snapshot_job(job_id: str) -> dict[str, Any]:
    with JOB_LOCK:
        if job_id not in JOBS:
            raise KeyError(job_id)
        job = JOBS[job_id]
        return {
            "id": job["id"],
            "type": job["type"],
            "title": job["title"],
            "status": job["status"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "logs": list(job["logs"]),
            "result": job["result"],
            "error": job["error"],
            "payload": job["payload"],
            "target_statuses": dict(job.get("target_statuses", {})),
        }


def set_job_target_status(job_id: str, target: str, status: str) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        job.setdefault("target_statuses", {})
        job["target_statuses"][target] = status
        job["updated_at"] = now_text()


def start_fetch_job(date_text: str, targets: list[str]) -> str:
    job_id = create_job("fetch", f"{date_text} 抓取 {format_target_labels(targets)}", {"date": date_text, "targets": targets})

    def worker() -> None:
        failures: list[str] = []

        def run_export(target: str | None = None) -> None:
            label = target_label(target) if target else "汇总索引"
            env = subprocess_env()
            if target:
                set_job_target_status(job_id, target, "running")
                append_job_log(job_id, f"[{label}] 启动抓取")
            if getattr(sys, "frozen", False):
                env["ASTOCK_EMBEDDED_SCRIPT"] = "market_overview"
            else:
                env.pop("ASTOCK_EMBEDDED_SCRIPT", None)
            process = subprocess.Popen(
                export_command(date_text, target),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                **hidden_subprocess_kwargs(),
            )
            assert process.stdout is not None
            last_line = ""
            for line in process.stdout:
                text = line.strip()
                if text:
                    last_line = text
                    append_job_log(job_id, f"[{label}] {text}")
            return_code = process.wait()
            if return_code != 0:
                detail = sanitize_public_text(last_line)
                raise RuntimeError(f"{label}执行失败，退出码 {return_code}" + (f"：{detail}" if detail else ""))
            if target:
                output = DATA_ROOT / date_text / TARGETS[target]["file"]
                if not output.is_file() or output.stat().st_size == 0:
                    raise RuntimeError(f"{label}未生成有效数据文件")
                try:
                    content = sanitize_text(output.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError) as exc:
                    raise RuntimeError(f"{label}资料读取失败，请重新抓取") from exc
                quality = data_quality(content, date_text, target)
                if not quality["usable"]:
                    raise RuntimeError(f"{label}：{quality['quality_message']}")

        try:
            with ThreadPoolExecutor(max_workers=max(1, min(4, len(targets)))) as executor:
                futures = {executor.submit(run_export, target): target for target in targets}
                for future in as_completed(futures):
                    target = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        set_job_target_status(job_id, target, "failed")
                        failures.append(str(exc))
                    else:
                        set_job_target_status(job_id, target, "completed")
            if any(status == "completed" for status in snapshot_job(job_id)["target_statuses"].values()):
                run_export()
        except Exception as exc:
            failures.append(str(exc))

        statuses = snapshot_job(job_id)["target_statuses"]
        target_data = collect_target_contents(date_text, targets)
        for item in target_data:
            item["fetch_status"] = statuses.get(item["target"], "failed")
        finish_job(
            job_id,
            result={
                "date": date_text,
                "targets": targets,
                "target_data": target_data,
                "fetched_count": sum(status == "completed" for status in statuses.values()),
            },
            error="；".join(failures) or None,
        )

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def start_analyze_job(
    date_text: str,
    targets: list[str],
    api_key: str | None,
    model: str | None,
    prompt_override: str | None = None,
    preset: str | None = None,
) -> str:
    job_id = create_job("analyze", f"{date_text} Kimi解析 {format_target_labels(targets)}", {"date": date_text, "targets": targets, "preset": resolve_prompt_preset(preset)})

    def worker() -> None:
        target_data: list[dict[str, str]] = []
        prompt = ""
        started_at = time.monotonic()
        resolved_model = DEFAULT_MODEL
        try:
            resolved_model = (model or "").strip() or DEFAULT_MODEL
            resolved_key = resolve_kimi_key(api_key)
            if not resolved_key:
                raise RuntimeError("未配置 Kimi API Key。请在左侧填写后保存，或设置环境变量 MOONSHOT_API_KEY。")

            append_job_log(job_id, f"使用模型：{resolved_model}")
            append_job_log(job_id, "读取所选标签数据")
            for target in targets:
                set_job_target_status(job_id, target, "reading")

            prepared = build_prompt_payload(date_text, targets, prompt_override, preset)
            target_data = prepared["target_data"]
            prompt = prepared["prompt"]
            append_job_log(job_id, f"提示词长度：{len(prompt)} 字符")
            append_job_log(job_id, "调用 Kimi API（流式接收）")
            for target in targets:
                set_job_target_status(job_id, target, "analyzing")

            def remember_prompt(candidate: str) -> None:
                nonlocal prompt
                prompt = candidate

            analysis = call_kimi(resolved_key, resolved_model, prompt,
                                 on_progress=lambda message: append_job_log(job_id, message),
                                 on_prompt=remember_prompt)
            generated_at = now_text()
            prepared["context"]["generated_at"] = generated_at
            slug = "all" if targets == TARGET_ORDER else "-".join(targets)
            metadata = {
                "schema_version": 1, "preset": prepared["preset"], "model": resolved_model,
                "targets": targets, "generated_at": generated_at, "context": prepared["context"],
                "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_version": hashlib.sha256((prompt_system_text(prepared["preset"]) + "\n" + "\n".join(prompt_requirements_summary(prepared["preset"], "", targets)) + "\n" + str(PROMPT_PRESETS[prepared["preset"]])).encode("utf-8")).hexdigest(),
                "inputs": [{key: item.get(key) for key in ("target", "file", "data_date", "captured_at", "row_count", "quality", "sha256")} for item in target_data],
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
            }
            file_name = save_analysis(date_text, slug, analysis, metadata)
            append_job_log(job_id, f"解析结果已保存：{file_name}")
            for target in targets:
                set_job_target_status(job_id, target, "completed")

            finish_job(
                job_id,
                result={
                    "date": date_text,
                    "targets": targets,
                    "target_data": target_data,
                    "prompt": prompt,
                    "analysis": analysis,
                    "model": resolved_model,
                    "analysis_file": file_name,
                    "record_id": file_name,
                    "context": prepared["context"],
                    "preset": resolve_prompt_preset(preset),
                },
            )
        except Exception as exc:
            for target in targets:
                current_status = snapshot_job(job_id)["target_statuses"].get(target, "pending")
                if current_status not in {"completed", "failed", "skipped"}:
                    set_job_target_status(job_id, target, "failed")
            finish_job(
                job_id,
                result={
                    "date": date_text,
                    "targets": targets,
                    "target_data": target_data,
                    "prompt": prompt,
                    "analysis": "",
                    "model": resolved_model,
                    "analysis_file": "",
                    "preset": resolve_prompt_preset(preset),
                },
                error=str(exc),
            )

    threading.Thread(target=worker, daemon=True).start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send_html()
        assets = {
            "/assets/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
            "/assets/app-icon.ico": ("assets/app-icon.ico", "image/x-icon"),
            "/assets/app-icon.png": ("assets/app-icon.png", "image/png"),
        }
        if parsed.path in assets:
            relative_path, content_type = assets[parsed.path]
            body = (INDEX_HTML.parent / relative_path).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/state":
            return self._handle_state(parsed.query)
        if parsed.path == "/api/settings":
            return self._handle_settings()
        if parsed.path == "/api/job":
            return self._handle_job(parsed.query)
        if parsed.path in {"/api/history", "/api/analysis"}:
            return self._handle_history(parsed.query, detail=parsed.path == "/api/analysis")
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/fetch":
            return self._handle_fetch()
        if parsed.path == "/api/prompt":
            return self._handle_prompt()
        if parsed.path == "/api/analyze":
            return self._handle_analyze()
        if parsed.path == "/api/settings":
            return self._handle_save_settings()
        if parsed.path == "/api/models":
            return self._handle_model_configuration()
        if parsed.path == "/api/model-test":
            return self._handle_model_configuration(test_model=True)
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        html = html.replace("%TARGETS_JSON%", json.dumps(TARGETS, ensure_ascii=False))
        html = html.replace("%DEFAULT_MODEL%", DEFAULT_MODEL)
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_state(self, query: str) -> None:
        params = parse_qs(query)
        dates = latest_dates()
        requested = params.get("date", [""])[0]
        try:
            current = normalize_date(requested)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, status=400)
        settings = load_settings()
        target_data = collect_target_contents(current, TARGET_ORDER)
        self._send_json(
            {
                "currentDate": current,
                "dates": sorted(set([current, *dates]), reverse=True),
                "availableTargets": [item["target"] for item in available_files(current) if item["target"] in TARGETS],
                "generatedAt": now_text(),
                "today": normalize_date(None),
                "targetData": target_data,
                "history": analysis_history(current),
                "settings": {
                    "kimiModel": settings["kimi_model"],
                    "hasKimiKey": bool(settings["kimi_key"]),
                    "hasEffectiveKimiKey": bool(resolve_kimi_key(None, settings["kimi_key"])),
                    "kimiKeyMask": masked_key(settings["kimi_key"]) if settings["kimi_key"] else "",
                },
            }
        )

    def _handle_history(self, query: str, *, detail: bool = False) -> None:
        try:
            params = parse_qs(query)
            date_text = normalize_date(params.get("date", [""])[0])
            result = read_analysis(date_text, params.get("id", [""])[0]) if detail else {"date": date_text, "records": analysis_history(date_text)}
            self._send_json(result)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
        except (ValueError, OSError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_settings(self) -> None:
        settings = load_settings()
        self._send_json(
            {
                "kimiModel": settings["kimi_model"],
                "hasKimiKey": bool(settings["kimi_key"]),
                "hasEffectiveKimiKey": bool(resolve_kimi_key(None, settings["kimi_key"])),
                "kimiKeyMask": masked_key(settings["kimi_key"]) if settings["kimi_key"] else "",
            }
        )

    def _handle_save_settings(self) -> None:
        try:
            payload = self._read_json()
            settings = save_settings(
                str(payload.get("kimi_key", "") or ""),
                str(payload.get("kimi_model", "") or DEFAULT_MODEL),
                bool(payload.get("keep_existing_key")),
            )
            self._send_json(
                {
                    "ok": True,
                    "kimiModel": settings["kimi_model"],
                    "hasKimiKey": bool(settings["kimi_key"]),
                    "hasEffectiveKimiKey": bool(resolve_kimi_key(None, settings["kimi_key"])),
                    "kimiKeyMask": masked_key(settings["kimi_key"]) if settings["kimi_key"] else "",
                }
            )
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_model_configuration(self, *, test_model: bool = False) -> None:
        try:
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ValueError("模型配置请求必须使用 JSON 对象。")
            api_key = payload.get("api_key", "")
            if api_key is None:
                api_key = ""
            if not isinstance(api_key, str):
                raise ValueError("Kimi API Key 必须为文本。")
            settings = load_settings()
            api_key = resolve_kimi_key(api_key, settings["kimi_key"])
            if not api_key:
                raise ValueError("请先输入或保存 Kimi API Key。")
            model = payload.get("model", settings["kimi_model"])
            if test_model and (not isinstance(model, str) or not model.strip()):
                raise ValueError("请选择要测试的模型。")
        except Exception as exc:
            return self._send_json({"error": str(exc)}, status=400)
        try:
            result = test_kimi_model(api_key, model.strip()) if test_model else {"models": list_kimi_models(api_key)}
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=502)

    def _handle_prompt(self) -> None:
        try:
            payload = self._read_json()
            date_text = normalize_date(payload.get("date"))
            targets = request_targets(payload)
            data = build_prompt_payload(
                date_text,
                targets,
                prompt_override=str(payload.get("prompt_override", "") or "") or None,
                preset=str(payload.get("preset", "") or "") or None,
            )
            self._send_json(data)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_job(self, query: str) -> None:
        try:
            params = parse_qs(query)
            job_id = params.get("id", [""])[0]
            if not job_id:
                raise ValueError("缺少 job id")
            self._send_json(snapshot_job(job_id))
        except KeyError:
            self._send_json({"error": "任务不存在"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_fetch(self) -> None:
        try:
            payload = self._read_json()
            date_text = normalize_date(payload.get("date"))
            targets = request_targets(payload)
            validate_fetch_date(date_text, targets)
            job_id = start_fetch_job(date_text, targets)
            self._send_json({"job_id": job_id})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_analyze(self) -> None:
        try:
            payload = self._read_json()
            for key in ("api_key", "model", "preset", "prompt_override"):
                if payload.get(key) is not None and not isinstance(payload[key], str):
                    raise ValueError(f"{key} 必须为字符串")
            date_text = normalize_date(payload.get("date"))
            targets = request_targets(payload)
            settings = load_settings()
            resolved_key = resolve_kimi_key(payload.get("api_key"), settings["kimi_key"])
            if not resolved_key:
                raise RuntimeError("未配置 Kimi API Key。请在左侧填写后保存，或设置环境变量 MOONSHOT_API_KEY。")
            job_id = start_analyze_job(
                date_text,
                targets,
                resolved_key,
                payload.get("model") or settings["kimi_model"],
                payload.get("prompt_override"),
                payload.get("preset"),
            )
            self._send_json({"job_id": job_id})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)


def main() -> int:
    port = int(os.getenv("KANPAN_PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"看盘工具已启动：http://127.0.0.1:{port}")
    print("Kimi API Key 优先级：页面输入 > MOONSHOT_API_KEY 环境变量")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
