#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
EXPORT_SCRIPT = ROOT / "market-overview-exporter" / "scripts" / "fetch_market_overview.py"
INDEX_HTML = ROOT / "kanpan-tool" / "index.html"
DEFAULT_PORT = 8765
DEFAULT_MODEL = "kimi-k2-thinking"
KIMI_BASE_URL = "https://api.moonshot.cn/v1/chat/completions"

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
    "jjyd": {"label": "竞价异动/竞价抢筹", "file": "jjyd.md"},
    "global": {"label": "指数行情", "file": "global.md"},
    "ztlive": {"label": "涨停实时直播", "file": "ztlive.md"},
    "yidong": {"label": "异动播报", "file": "yidong.md"},
    "pool": {"label": "涨停股池", "file": "pool.md"},
    "amount": {"label": "成交额", "file": "amount.md"},
    "fupan": {"label": "日期复盘", "file": "fupan.md"},
    "platerotat": {"label": "板块轮动前20天强度", "file": "platerotat.md"},
    "jinji": {"label": "近期涨停快照", "file": "jinji.md"},
}
TARGET_ORDER = list(TARGETS.keys())

JOBS: dict[str, dict[str, Any]] = {}
JOB_LOCK = threading.Lock()

SOURCE_PATTERNS = [
    re.compile(r"^>\s*来源", re.IGNORECASE),
    re.compile(r"duanxianxia", re.IGNORECASE),
    re.compile(r"DUANXIANXIA", re.IGNORECASE),
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(value: str | None) -> str:
    if not value:
        return datetime.now().strftime("%Y-%m-%d")
    cleaned = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        raise ValueError(f"非法日期：{value}")
    return cleaned


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


def collect_target_contents(date_text: str, targets: list[str]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
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
                }
            )
            continue
        content = sanitize_text(path.read_text(encoding="utf-8-sig"))
        results.append(
            {
                "target": target,
                "label": meta["label"],
                "file": meta["file"],
                "content": content,
                "exists": "true",
            }
        )
    return results


def build_prompt(date_text: str, targets: list[str], prompt_override: str | None = None) -> str:
    if prompt_override and prompt_override.strip():
        return prompt_override.strip()

    parts = [
        f"你是一名 A 股短线复盘分析助手，请基于 {date_text} 的以下数据做结构化分析。",
        ANALYSIS_FRAMEWORK,
        "",
        "固定输出章节：",
        "一、市场周期定位",
        "二、情绪与赚钱效应",
        "三、主线、支线与轮动结构",
        "四、核心个股、龙头梯队与补涨关系",
        "五、竞价到盘中的关键信号验证",
        "六、风险点与失败信号",
        "七、次日预案（走强 / 分歧 / 退潮）",
        "八、一句话结论",
        "",
    ]
    total_chars = 0
    max_chars = 180000
    for target in targets:
        meta = TARGETS[target]
        content = sanitize_text(read_file(date_text, meta["file"]))
        budget = min(30000, max_chars - total_chars)
        if budget <= 0:
            break
        clipped = content[:budget]
        total_chars += len(clipped)
        parts.append(f"### 数据模块：{meta['label']}")
        parts.append(clipped)
        if len(clipped) < len(content):
            parts.append(f"\n[已截断，原文本长度 {len(content)} 字符]\n")
    return "\n".join(parts)


def call_kimi(api_key: str, model: str, prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是专业的 A 股短线看盘和复盘分析助手。"
                    "你的任务是把多来源盘面数据整理成可执行的复盘框架。"
                    "你必须强调市场周期、主线强度、龙头地位、补涨关系和风险预案。"
                    "语言要求克制、结构化、基于证据，不模仿任何网络作者风格，也不提及任何来源作者。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = requests.post(KIMI_BASE_URL, headers=headers, json=payload, timeout=300)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # pragma: no cover
            last_error = exc
            if attempt == 2:
                break
    assert last_error is not None
    raise last_error


def save_analysis(date_text: str, slug: str, content: str) -> str:
    safe_slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "analysis"
    path = DATA_ROOT / date_text / f"kimi-analysis-{safe_slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig", newline="\n")
    return path.name


def create_job(job_type: str, title: str, payload: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex
    target_statuses = {target: "pending" for target in payload.get("targets", [])}
    with JOB_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "type": job_type,
            "title": title,
            "status": "running",
            "created_at": now_text(),
            "updated_at": now_text(),
            "logs": [f"[{now_text()}] 开始任务：{title}"],
            "payload": payload,
            "target_statuses": target_statuses,
            "result": None,
            "error": None,
        }
    return job_id


def append_job_log(job_id: str, message: str) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        job["logs"].append(f"[{now_text()}] {message}")
        job["updated_at"] = now_text()


def finish_job(job_id: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        job["status"] = "failed" if error else "completed"
        job["result"] = result
        job["error"] = error
        job["updated_at"] = now_text()
        tail = f"任务失败：{error}" if error else "任务完成"
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
    job_id = create_job("fetch", f"{date_text} 抓取 {'/'.join(targets)}", {"date": date_text, "targets": targets})

    def worker() -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            for index, target in enumerate(targets):
                meta = TARGETS[target]
                set_job_target_status(job_id, target, "running")
                append_job_log(job_id, f"[{meta['label']}] 启动抓取")
                args = [
                    sys.executable,
                    str(EXPORT_SCRIPT),
                    "--output-root",
                    str(DATA_ROOT),
                    "--date",
                    date_text,
                    "--targets",
                    target,
                ]
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    text = line.strip()
                    if text:
                        append_job_log(job_id, f"[{meta['label']}] {text}")
                return_code = process.wait()
                if return_code != 0:
                    set_job_target_status(job_id, target, "failed")
                    for pending in targets[index + 1:]:
                        set_job_target_status(job_id, pending, "skipped")
                    raise RuntimeError(f"{meta['label']} 抓取失败，退出码 {return_code}")
                set_job_target_status(job_id, target, "completed")

            target_data = collect_target_contents(date_text, targets)
            finish_job(
                job_id,
                result={
                    "date": date_text,
                    "targets": targets,
                    "target_data": target_data,
                    "fetched_count": sum(1 for item in target_data if item["exists"] == "true"),
                },
            )
        except Exception as exc:
            finish_job(job_id, error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def start_analyze_job(
    date_text: str,
    targets: list[str],
    api_key: str | None,
    model: str | None,
    prompt_override: str | None = None,
) -> str:
    job_id = create_job("analyze", f"{date_text} Kimi 解析 {'/'.join(targets)}", {"date": date_text, "targets": targets})

    def worker() -> None:
        target_data: list[dict[str, str]] = []
        prompt = ""
        resolved_model = (model or "").strip() or DEFAULT_MODEL
        try:
            resolved_key = (api_key or "").strip() or os.getenv("MOONSHOT_API_KEY", "").strip()
            if not resolved_key:
                raise RuntimeError("未配置 MOONSHOT_API_KEY，也未在页面中填写 Kimi API Key。")

            append_job_log(job_id, f"使用模型：{resolved_model}")
            append_job_log(job_id, "读取所选标签数据")
            for target in targets:
                set_job_target_status(job_id, target, "reading")

            target_data = collect_target_contents(date_text, targets)
            missing = [item["label"] for item in target_data if item["exists"] != "true"]
            if missing:
                raise RuntimeError(f"以下标签尚未抓取成功：{'、'.join(missing)}")

            prompt = build_prompt(date_text, targets, prompt_override=prompt_override)
            append_job_log(job_id, f"提示词长度：{len(prompt)} 字符")
            append_job_log(job_id, "调用 Kimi API")
            for target in targets:
                set_job_target_status(job_id, target, "analyzing")

            analysis = call_kimi(resolved_key, resolved_model, prompt)
            slug = "all" if targets == TARGET_ORDER else "-".join(targets)
            file_name = save_analysis(date_text, slug, analysis)
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
        if parsed.path == "/api/state":
            return self._handle_state(parsed.query)
        if parsed.path == "/api/job":
            return self._handle_job(parsed.query)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/fetch":
            return self._handle_fetch()
        if parsed.path == "/api/analyze":
            return self._handle_analyze()
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
        current = normalize_date(requested or (dates[0] if dates else None))
        self._send_json(
            {
                "currentDate": current,
                "dates": dates or [current],
                "availableTargets": [item["target"] for item in available_files(current) if item["target"] in TARGETS],
                "generatedAt": now_text(),
            }
        )

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
            targets = payload.get("targets") or TARGET_ORDER
            for target in targets:
                if target not in TARGETS:
                    raise ValueError(f"非法 target：{target}")
            job_id = start_fetch_job(date_text, targets)
            self._send_json({"job_id": job_id})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_analyze(self) -> None:
        try:
            payload = self._read_json()
            date_text = normalize_date(payload.get("date"))
            targets = payload.get("targets") or TARGET_ORDER
            for target in targets:
                if target not in TARGETS:
                    raise ValueError(f"非法 target：{target}")
            job_id = start_analyze_job(
                date_text,
                targets,
                payload.get("api_key"),
                payload.get("model"),
                payload.get("prompt_override"),
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
