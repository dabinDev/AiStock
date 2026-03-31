#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, sync_playwright
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 playwright 依赖，请先执行 `pip install playwright`。") from exc


ROOT = Path(__file__).resolve().parents[2]
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT_ROOT = ROOT / "data"
DEFAULT_SUMMARY_FILE = "market_overview_summary.md"
DEFAULT_DASHBOARD_FILE = "dashboard.md"
DEFAULT_AI_FILE = "ai-analysis.md"
DASHBOARD_META_PREFIX = "<!-- DUANXIANXIA_COMPOSITE_META "
META_SUFFIX = " -->"

JINGJIA_SCRIPT = ROOT / "duanxian-jingjia-exporter" / "scripts" / "fetch_duanxianxia_jingjia.py"
JJYD_SCRIPT = ROOT / "duanxian-workflow" / "scripts" / "fetch_duanxianxia_jjyd.py"
YIDONG_POOL_SCRIPT = ROOT / "duanxian-yidong-pool" / "scripts" / "fetch_duanxianxia_yidong_pool.py"

TARGET_ALIASES = {
    "all": "all",
    "jingjia": "jingjia",
    "jjyd": "jjyd",
    "global": "global",
    "ztlive": "ztlive",
    "yidong": "yidong",
    "pool": "pool",
    "amount": "amount",
    "fupan": "fupan",
    "platerotat": "platerotat",
    "jinji": "jinji",
}

TARGET_ORDER = [
    "jingjia",
    "jjyd",
    "global",
    "ztlive",
    "yidong",
    "pool",
    "amount",
    "fupan",
    "platerotat",
    "jinji",
]

TARGET_METADATA = {
    "jingjia": {
        "label": "竞价封单",
        "page": "https://duanxianxia.com/web/jjlive",
        "file": "jingjia.md",
        "analysis_title": "竞价封单与近 5 日竞价分析",
    },
    "jjyd": {
        "label": "竞价异动/竞价抢筹",
        "page": "https://duanxianxia.com/mob/jjyd",
        "file": "jjyd.md",
        "analysis_title": "竞价异动与竞价抢筹分析",
    },
    "global": {
        "label": "指数行情",
        "page": "https://duanxianxia.com/web/global",
        "file": "global.md",
        "analysis_title": "指数环境与风险偏好分析",
    },
    "ztlive": {
        "label": "涨停实时直播",
        "page": "https://duanxianxia.com/web/ztlive",
        "file": "ztlive.md",
        "analysis_title": "涨停直播与主线发酵分析",
    },
    "yidong": {
        "label": "异动播报",
        "page": "https://duanxianxia.com/web/yidong",
        "file": "yidong.md",
        "analysis_title": "盘中异动强弱分析",
    },
    "pool": {
        "label": "涨停股池",
        "page": "https://duanxianxia.com/web/pool",
        "file": "pool.md",
        "analysis_title": "涨停股池结构分析",
    },
    "amount": {
        "label": "成交额",
        "page": "https://duanxianxia.com/web/amount",
        "file": "amount.md",
        "analysis_title": "量能与市场承接分析",
    },
    "fupan": {
        "label": "日期复盘",
        "page": "https://duanxianxia.com/web/fupan",
        "file": "fupan.md",
        "analysis_title": "板块强度与复盘分析",
    },
    "platerotat": {
        "label": "板块轮动",
        "page": "https://duanxianxia.com/web/platerotat",
        "file": "platerotat.md",
        "analysis_title": "板块轮动与持续性分析",
    },
    "jinji": {
        "label": "近期涨停快照",
        "page": "https://duanxianxia.com/",
        "file": "jinji.md",
        "analysis_title": "连板天梯与晋级率分析",
    },
}


@dataclass(frozen=True)
class ExportResult:
    target: str
    output_file: Path
    row_count: int | None = None
    note: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一抓取短线侠多页面数据并生成 AI 解析入口")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="输出根目录，默认仓库 data 目录")
    parser.add_argument("--date", help="目标日期，仅用于目录命名，默认使用上海今日")
    parser.add_argument(
        "--targets",
        default="all",
        help="逗号分隔的目标页面，可选 all/jingjia/jjyd/global/ztlive/yidong/pool/amount/fupan/platerotat/jinji",
    )
    parser.add_argument("--summary-file", default=DEFAULT_SUMMARY_FILE, help=f"根目录汇总文件名，默认 {DEFAULT_SUMMARY_FILE}")
    parser.add_argument("--dashboard-file", default=DEFAULT_DASHBOARD_FILE, help=f"日目录导航文件名，默认 {DEFAULT_DASHBOARD_FILE}")
    parser.add_argument("--ai-file", default=DEFAULT_AI_FILE, help=f"AI 解析入口文件名，默认 {DEFAULT_AI_FILE}")
    args = parser.parse_args()
    args.target_date = normalize_date(args.date) if args.date else today_text()
    args.target_list = normalize_targets(args.targets, parser)
    return args


def normalize_date(value: str) -> str:
    cleaned = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        raise SystemExit(f"无法识别日期格式：{value}")
    return cleaned


def normalize_targets(raw_value: str, parser: argparse.ArgumentParser) -> list[str]:
    values = [item.strip().lower() for item in raw_value.split(",") if item.strip()]
    if not values:
        parser.error("--targets 不能为空")
    normalized: list[str] = []
    for value in values:
        mapped = TARGET_ALIASES.get(value)
        if not mapped:
            parser.error(f"不支持的 target：{value}")
        if mapped == "all":
            return list(TARGET_ORDER)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig", newline="\n")


def escape_md(value: Any) -> str:
    text = "-" if value in (None, "", []) else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("|", "\\|").replace("\n", "<br>")


def render_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(escape_md(item) for item in headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    if not rows:
        lines.append("| " + " | ".join(["-"] * len(headers)) + " |")
        return lines
    for row in rows:
        normalized = [escape_md(row[index] if index < len(row) else "-") for index in range(len(headers))]
        lines.append("| " + " | ".join(normalized) + " |")
    return lines


def markdown_link(label: str, path: Path, base_dir: Path) -> str:
    relative = path.relative_to(base_dir).as_posix()
    return f"[{label}](./{relative})"


def read_dashboard_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    pattern = re.escape(DASHBOARD_META_PREFIX) + r"(.*?)" + re.escape(META_SUFFIX)
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def ensure_dependency_scripts() -> None:
    missing = [str(path) for path in (JINGJIA_SCRIPT, JJYD_SCRIPT, YIDONG_POOL_SCRIPT) if not path.exists()]
    if missing:
        raise SystemExit("缺少依赖脚本：\n" + "\n".join(missing))


def run_external_script(command: list[str], expected_file: Path, target: str) -> ExportResult:
    result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise SystemExit(f"{target} 抓取失败，退出码 {result.returncode}\n{detail}")
    if not expected_file.exists():
        raise SystemExit(f"{target} 抓取已执行，但未找到输出文件：{expected_file}")
    return ExportResult(target=target, output_file=expected_file, note="复用现有脚本")


def export_existing_targets(output_root: Path, target_date: str, targets: list[str]) -> list[ExportResult]:
    day_dir = output_root / target_date
    results: list[ExportResult] = []
    if "jingjia" in targets:
        command = [sys.executable, str(JINGJIA_SCRIPT), "--output-root", str(output_root)]
        if target_date != today_text():
            command.extend(["--date", target_date])
        results.append(run_external_script(command, day_dir / "jingjia.md", "jingjia"))
    if "jjyd" in targets:
        command = [sys.executable, str(JJYD_SCRIPT), "--output-root", str(output_root)]
        results.append(run_external_script(command, day_dir / "jjyd.md", "jjyd"))
    if "yidong" in targets:
        command = [sys.executable, str(YIDONG_POOL_SCRIPT), "--output-root", str(output_root), "--yidong-only"]
        results.append(run_external_script(command, day_dir / "yidong.md", "yidong"))
    if "pool" in targets:
        command = [sys.executable, str(YIDONG_POOL_SCRIPT), "--output-root", str(output_root), "--pool-only"]
        results.append(run_external_script(command, day_dir / "pool.md", "pool"))
    if "jinji" in targets:
        command = [sys.executable, str(YIDONG_POOL_SCRIPT), "--output-root", str(output_root), "--jinji-only"]
        results.append(run_external_script(command, day_dir / "jinji.md", "jinji"))
    return results


def launch_browser(playwright):
    attempts = [
        ("msedge", lambda: playwright.chromium.launch(channel="msedge", headless=True)),
        ("chrome", lambda: playwright.chromium.launch(channel="chrome", headless=True)),
        ("chromium", lambda: playwright.chromium.launch(headless=True)),
    ]
    errors: list[str] = []
    for name, launcher in attempts:
        try:
            return launcher()
        except PlaywrightError as exc:
            errors.append(f"{name}: {exc}")
    raise SystemExit("无法启动浏览器，请先安装 Edge/Chrome 或执行 `playwright install`。\n" + "\n".join(errors))


def wait_page_ready(page: Page, url: str, wait_ms: int) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if wait_ms:
        page.wait_for_timeout(wait_ms)


def collect_global(page: Page) -> tuple[str, int]:
    url = TARGET_METADATA["global"]["page"]
    wait_page_ready(page, url, 2000)
    cards = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('[id^="Z_"]')).map((node) => {
          const lines = (node.innerText || '').split(/\\n+/).map((item) => item.trim()).filter(Boolean);
          return { index: node.id, name: lines[0] || node.id, latest: lines[1] || '-', change: lines[2] || '-' };
        }).filter((item) => item.name && item.latest)
        """
    )
    rows = [[item["index"], item["name"], item["latest"], item["change"]] for item in cards]
    lines = [
        f"# 短线侠指数行情 - {today_text()}",
        "",
        f"> 来源页面：{url}",
        f"> 生成时间：{now_text()}",
        f"> 指数数量：{len(rows)}",
        "",
        "## 指数快照",
        "",
    ]
    lines.extend(render_table(["ID", "名称", "最新值", "涨跌/涨跌幅"], rows))
    lines.append("")
    return "\n".join(lines), len(rows)


def collect_ztlive(page: Page) -> tuple[str, int]:
    url = TARGET_METADATA["ztlive"]["page"]
    wait_page_ready(page, url, 4000)
    hot_buttons = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#plates button')).map((button) => ({
          text: button.innerText.trim(),
          name: button.getAttribute('name') || '',
        })).filter((item) => item.text)
        """
    )
    headers = page.locator("#zthead th").evaluate_all("(nodes) => nodes.map((node) => node.innerText.trim()).filter(Boolean)")
    rows = page.locator("#ztlist tr").evaluate_all("(nodes) => nodes.map((tr) => Array.from(tr.children).map((td) => td.innerText.trim()))")
    lines = [
        f"# 短线侠涨停实时直播 - {today_text()}",
        "",
        f"> 来源页面：{url}",
        f"> 生成时间：{now_text()}",
        f"> 热门题材数量：{len(hot_buttons)}",
        f"> 涨停直播条数：{len(rows)}",
        "",
        "## 热门题材",
        "",
    ]
    lines.extend(render_table(["序号", "题材", "按钮文本"], [[index, item["name"] or "-", item["text"]] for index, item in enumerate(hot_buttons, start=1)]))
    lines.extend(["", "## 直播明细", ""])
    lines.extend(render_table([str(item) for item in headers], rows))
    lines.append("")
    return "\n".join(lines), len(rows)


def collect_amount(page: Page) -> tuple[str, int]:
    url = TARGET_METADATA["amount"]["page"]
    wait_page_ready(page, url, 1500)
    metrics = page.evaluate(
        """
        () => ({
          realAmount: (document.querySelector('#realAmount')?.innerText || '').trim(),
          yuceAmount: (document.querySelector('#yuceAmount')?.innerText || '').trim(),
          lastAmount: (document.querySelector('#lastAmount')?.innerText || '').trim(),
        })
        """
    )
    rows = [["今日量能", metrics.get("realAmount", "-")], ["预测量能", metrics.get("yuceAmount", "-")], ["昨日量能", metrics.get("lastAmount", "-")]]
    lines = [
        f"# 短线侠沪深量能 - {today_text()}",
        "",
        f"> 来源页面：{url}",
        f"> 生成时间：{now_text()}",
        "",
        "## 量能快照",
        "",
    ]
    lines.extend(render_table(["指标", "数值"], rows))
    lines.append("")
    return "\n".join(lines), len(rows)


def collect_fupan(page: Page) -> tuple[str, int]:
    url = TARGET_METADATA["fupan"]["page"]
    wait_page_ready(page, url, 5000)
    metrics = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.chart')).map((node) => ({
          name: node.getAttribute('name') || '',
          text: node.innerText.trim(),
        })).filter((item) => item.text)
        """
    )
    plates = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#plates tr')).map((tr) =>
          Array.from(tr.children).map((td) => td.innerText.trim()).filter(Boolean)
        ).filter((row) => row.length > 0)
        """
    )
    tables = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('table.ztlist')).map((table) => ({
          tableId: table.id || '',
          headers: Array.from(table.querySelectorAll('tr:first-child th')).map((th) => th.innerText.trim()),
          rows: Array.from(table.querySelectorAll('tr')).slice(1).map((tr) =>
            Array.from(tr.children).map((td) => td.innerText.trim())
          ),
        }))
        """
    )
    lines = [
        f"# 短线侠日期复盘 - {today_text()}",
        "",
        f"> 来源页面：{url}",
        f"> 生成时间：{now_text()}",
        f"> 指标数量：{len(metrics)}",
        f"> 板块数量：{len(plates)}",
        f"> 涨停分组数量：{len(tables)}",
        "",
        "## 顶部指标",
        "",
    ]
    lines.extend(render_table(["序号", "代码", "指标"], [[index, item["name"] or "-", item["text"]] for index, item in enumerate(metrics, start=1)]))
    lines.extend(["", "## 板块强度", ""])
    lines.extend(render_table(["序号", "板块", "补充1", "补充2"], [[index, row[0] if row else "-", row[1] if len(row) > 1 else "-", row[2] if len(row) > 2 else "-"] for index, row in enumerate(plates, start=1)]))
    for table in tables:
        lines.extend(["", f"## 涨停分组 {table['tableId'] or '-'}", ""])
        lines.extend(render_table(table["headers"], table["rows"]))
    lines.append("")
    return "\n".join(lines), sum(len(table["rows"]) for table in tables)


def collect_platerotat(page: Page) -> tuple[str, int]:
    url = TARGET_METADATA["platerotat"]["page"]
    wait_page_ready(page, url, 4000)
    page.evaluate(
        """
        () => {
          const clickByText = (matcher) => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const target = buttons.find((button) => matcher((button.innerText || '').replace(/\\s+/g, '')));
            if (target) target.click();
          };
          clickByText((text) => text === '板块强度');
          clickByText((text) => text === '近20日');
        }
        """
    )
    page.wait_for_timeout(1200)
    data_type = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.datatype')).map((node) => ({
          text: node.innerText.trim(),
          active: node.classList.contains('btn-danger'),
          name: node.getAttribute('name') || '',
        }))
        """
    )
    date_ranges = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.chdate')).map((node) => ({
          text: node.innerText.trim(),
          active: node.classList.contains('btn-warning'),
        }))
        """
    )
    table_rows = page.locator("#plate tr").evaluate_all("(nodes) => nodes.map((tr) => Array.from(tr.children).map((td) => td.innerText.trim()))")
    headers = table_rows[0] if table_rows else []
    rows = table_rows[1:] if len(table_rows) > 1 else []
    lines = [
        f"# 短线侠板块轮动 - {today_text()}",
        "",
        f"> 来源页面：{url}",
        f"> 生成时间：{now_text()}",
        f"> 当前数据源：{next((item['text'] for item in data_type if item['active']), '-')}",
        f"> 当前区间：{next((item['text'] for item in date_ranges if item['active']), '-')}",
        "> 抓取模式：前20天板块强度数据",
        "",
        "## 数据源选项",
        "",
    ]
    lines.extend(render_table(["序号", "代码", "文本", "激活"], [[index, item["name"] or "-", item["text"], "是" if item["active"] else "否"] for index, item in enumerate(data_type, start=1)]))
    lines.extend(["", "## 区间选项", ""])
    lines.extend(render_table(["序号", "区间", "激活"], [[index, item["text"], "是" if item["active"] else "否"] for index, item in enumerate(date_ranges, start=1)]))
    lines.extend(["", "## 前 20 天涨停强度数据", ""])
    lines.extend(render_table(headers, rows))
    lines.append("")
    return "\n".join(lines), len(rows)


def collect_with_playwright(output_root: Path, target_date: str, targets: list[str]) -> list[ExportResult]:
    day_dir = output_root / target_date
    playwright_targets = [target for target in targets if target in {"global", "ztlive", "amount", "fupan", "platerotat"}]
    if not playwright_targets:
        return []
    collectors = {
        "global": collect_global,
        "ztlive": collect_ztlive,
        "amount": collect_amount,
        "fupan": collect_fupan,
        "platerotat": collect_platerotat,
    }
    results: list[ExportResult] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1600, "height": 2400})
        try:
            for target in playwright_targets:
                markdown, row_count = collectors[target](page)
                output_file = day_dir / TARGET_METADATA[target]["file"]
                write_text(output_file, markdown)
                results.append(ExportResult(target=target, output_file=output_file, row_count=row_count, note="页面 DOM 快照"))
        finally:
            browser.close()
    return results


def build_ai_analysis(day_dir: Path, results: list[ExportResult], ai_file_name: str) -> Path:
    ai_path = day_dir / ai_file_name
    lines = [
        f"# AI 解析入口 - {day_dir.name}",
        "",
        f"> 生成时间：{now_text()}",
        "> 使用方式：点击下方对应条目，复制提示词给 AI，或直接让 Codex 基于对应 Markdown 文件做分析。",
        "",
        "## 综合分析",
        "",
        "```text",
        f"请结合 {day_dir.name} 目录下的全部短线侠导出文件，按“指数环境、竞价、盘中异动、涨停结构、板块轮动、量能、风险点、次日观察”输出一份结构化复盘。",
        "```",
        "",
    ]
    for result in results:
        meta = TARGET_METADATA[result.target]
        file_name = result.output_file.name
        lines.extend(
            [
                f"## {meta['analysis_title']}",
                "",
                f"- 数据文件：[{file_name}](./{file_name})",
                "",
                "```text",
                f"请阅读 {file_name}，重点分析 {meta['label']} 的核心信号，输出“关键信号、最强方向、分歧点、风险点、可跟踪标的、结论”六部分。",
                "```",
                "",
            ]
        )
    write_text(ai_path, "\n".join(lines))
    return ai_path


def build_dashboard(day_dir: Path, results: list[ExportResult], dashboard_file_name: str, ai_path: Path) -> Path:
    dashboard_path = day_dir / dashboard_file_name
    meta = {"date": day_dir.name, "generated_at": now_text(), "targets": [result.target for result in results]}
    lines = [
        f"# 短线侠综合导航 - {day_dir.name}",
        "",
        f"{DASHBOARD_META_PREFIX}{compact_json(meta)}{META_SUFFIX}",
        "",
        f"> 生成时间：{now_text()}",
        "> 使用方式：手动点击数据文件查看原始导出，再点击 AI 解析入口执行后续分析。",
        "",
        "| 页面 | 数据文件 | 来源页面 | 说明 | AI 解析 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        meta_item = TARGET_METADATA[result.target]
        data_link = markdown_link(result.output_file.name, result.output_file, day_dir)
        ai_link = markdown_link("AI解析", ai_path, day_dir)
        note = result.note
        if result.row_count is not None:
            note = f"{note}，记录数 {result.row_count}" if note else f"记录数 {result.row_count}"
        lines.append(f"| {meta_item['label']} | {data_link} | {meta_item['page']} | {escape_md(note)} | {ai_link} |")
    lines.extend(["", "## 快速入口", "", f"- {markdown_link('AI 解析总入口', ai_path, day_dir)}", ""])
    write_text(dashboard_path, "\n".join(lines))
    return dashboard_path


def rebuild_summary(output_root: Path, summary_file_name: str, dashboard_file_name: str) -> Path:
    entries: list[dict[str, Any]] = []
    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue
        meta = read_dashboard_meta(child / dashboard_file_name)
        if meta:
            entries.append(meta)
    entries.sort(key=lambda item: item["date"], reverse=True)
    lines = [
        "# 短线侠综合导出汇总",
        "",
        f"> 重建时间：{now_text()}",
        "",
        "| 日期 | 导航 | 页面数 | 目标列表 |",
        "| --- | --- | ---: | --- |",
    ]
    if not entries:
        lines.append("| - | - | 0 | - |")
    else:
        for entry in entries:
            day = entry["date"]
            dashboard_link = f"[dashboard](./{day}/{dashboard_file_name})"
            lines.append(f"| {day} | {dashboard_link} | {len(entry.get('targets', []))} | {escape_md('、'.join(entry.get('targets', [])))} |")
    lines.append("")
    summary_path = output_root / summary_file_name
    write_text(summary_path, "\n".join(lines))
    return summary_path


def main() -> int:
    args = parse_args()
    ensure_dependency_scripts()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    results = export_existing_targets(output_root, args.target_date, args.target_list)
    results.extend(collect_with_playwright(output_root, args.target_date, args.target_list))
    results.sort(key=lambda item: TARGET_ORDER.index(item.target))
    if not results:
        raise SystemExit("没有可执行的抓取目标。")
    day_dir = output_root / args.target_date
    ai_path = build_ai_analysis(day_dir, results, args.ai_file)
    dashboard_path = build_dashboard(day_dir, results, args.dashboard_file, ai_path)
    summary_path = rebuild_summary(output_root, args.summary_file, args.dashboard_file)
    print(f"输出目录：{day_dir}")
    for result in results:
        print(f"[OK] {result.target} -> {result.output_file.name}")
    print(f"导航文件：{dashboard_path}")
    print(f"AI 解析：{ai_path}")
    print(f"汇总文件：{summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
