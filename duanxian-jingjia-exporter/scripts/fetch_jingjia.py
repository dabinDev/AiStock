#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 playwright 依赖，请先执行 `pip install playwright`。") from exc


SITE_BASE = "https://" + "".join(["duan", "xian", "xia"]) + ".com"
SOURCE_PAGE = f"{SITE_BASE}/web/jjlive"
DEFAULT_DAILY_FILE = "jingjia.md"
DEFAULT_SUMMARY_FILE = "jingjia_summary.md"
META_PREFIX = "<!-- JINGJIA_META "
META_SUFFIX = " -->"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
BROWSER_LAUNCH_ARGS = ["--proxy-server=direct://", "--proxy-bypass-list=*"]
CUSTOM_BROWSER_PATH = os.getenv("PLAYWRIGHT_CHROME_EXECUTABLE", "").strip()
SYSTEM_BROWSER_CANDIDATES = [
    *([Path(CUSTOM_BROWSER_PATH)] if CUSTOM_BROWSER_PATH else []),
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
]
HEADER_SELECTORS = [
    ("live", "#thlive", "#tblive"),
    ("history", "#th1", "#tb1"),
    ("history", "#th2", "#tb2"),
    ("history", "#th3", "#tb3"),
    ("history", "#th4", "#tb4"),
]


@dataclass(frozen=True)
class StockRow:
    seq: int
    code: str
    name: str
    tags: list[str]
    amount_915: str
    amount_920: str
    amount_925: str
    change_pct: str


@dataclass(frozen=True)
class SnapshotSection:
    date: str
    section_type: str
    yizi_count: int
    total_fengdan: str
    rows: list[StockRow]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出竞价封单与历史竞价数据")
    parser.add_argument("--output-root", default=".", help="输出根目录，默认当前目录")
    parser.add_argument(
        "--daily-file",
        default=DEFAULT_DAILY_FILE,
        help=f"日期目录日文件名，默认 {DEFAULT_DAILY_FILE}",
    )
    parser.add_argument(
        "--summary-file",
        default=DEFAULT_SUMMARY_FILE,
        help=f"根目录汇总文件名，默认 {DEFAULT_SUMMARY_FILE}",
    )
    parser.add_argument("--date", help="仅导出页面当前可见的指定日期，格式 YYYY-MM-DD")
    parser.add_argument("--latest-only", action="store_true", help="只导出实时竞价列")
    parser.add_argument("--history-only", action="store_true", help="只导出历史竞价列")
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=3000,
        help="页面首屏加载后的额外等待毫秒数，默认 3000",
    )
    args = parser.parse_args()

    if args.latest_only and args.history_only:
        parser.error("--latest-only 与 --history-only 不能同时使用")

    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date.strip()):
        parser.error("--date 仅支持 YYYY-MM-DD")

    if args.wait_ms < 0:
        parser.error("--wait-ms 不能小于 0")

    return args


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def sanitize_output_text(content: str) -> str:
    content = content.replace("\u77ed\u7ebf\u4fa0", "市场")
    content = re.sub(r"^>\s*来源.*(?:\r?\n)?", "", content, flags=re.MULTILINE)
    content = re.sub(r"https?://[^\s)>\]]+", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() + "\n"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sanitize_output_text(content), encoding="utf-8-sig", newline="\n")


def escape_md(value: Any) -> str:
    text = "-" if value in (None, "", []) else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def section_label(section_type: str) -> str:
    return "实时竞价" if section_type == "live" else "历史竞价"


def parse_header_html(header_html: str) -> tuple[str, int, str]:
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", header_html)
    yizi_match = re.search(r"一字:\s*(\d+)\s*个", header_html)
    total_match = re.search(r"封单:\s*([^<]+)", header_html)

    if not date_match:
        raise RuntimeError(f"无法从表头解析日期：{header_html}")

    date_text = date_match.group(1)
    yizi_count = int(yizi_match.group(1)) if yizi_match else 0
    total_fengdan = total_match.group(1).strip() if total_match else "-"
    return date_text, yizi_count, total_fengdan


def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"<!-- JINGJIA_META (.*?) -->", text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def render_table(rows: list[StockRow]) -> list[str]:
    headers = ["序号", "代码", "名称", "标签", "9:15", "9:20", "9:25", "涨幅"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
        return lines

    for row in rows:
        values = [
            row.seq,
            row.code or "-",
            row.name or "-",
            "、".join(row.tags) if row.tags else "-",
            row.amount_915,
            row.amount_920,
            row.amount_925,
            row.change_pct,
        ]
        lines.append("| " + " | ".join(escape_md(value) for value in values) + " |")
    return lines


def build_daily_markdown(section: SnapshotSection) -> str:
    meta = {
        "date": section.date,
        "section_type": section.section_type,
        "yizi_count": section.yizi_count,
        "total_fengdan": section.total_fengdan,
        "row_count": len(section.rows),
    }

    lines = [
        f"# 竞价数据 - {section.date}",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 生成时间：{now_text()}",
        f"> 数据日期：{section.date}",
        f"> 数据类型：{section_label(section.section_type)}",
        f"> 一字数量：{section.yizi_count}",
        f"> 封单合计：{section.total_fengdan}",
        f"> 股票数量：{len(section.rows)}",
        "",
        f"{META_PREFIX}{json.dumps(meta, ensure_ascii=False)}{META_SUFFIX}",
        "",
        "## 竞价列表",
        "",
    ]
    lines.extend(render_table(section.rows))
    return "\n".join(lines).rstrip() + "\n"


def rebuild_summary(output_root: Path, summary_name: str, daily_name: str) -> Path:
    entries: list[dict[str, Any]] = []

    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue
        meta = read_meta(child / daily_name)
        if not meta:
            continue
        entries.append(meta)

    entries.sort(key=lambda item: item["date"], reverse=True)

    lines = [
        "# 竞价数据汇总",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 重建时间：{now_text()}",
        "",
        "| 日期 | 类型 | 一字数量 | 封单合计 | 股票数量 |",
        "| --- | --- | ---: | --- | ---: |",
    ]

    if not entries:
        lines.append("| - | - | 0 | - | 0 |")
    else:
        for entry in entries:
            day = entry["date"]
            day_link = f"[{day}](./{day}/{daily_name})"
            lines.append(
                f"| {day_link} | {section_label(entry.get('section_type', 'history'))} | "
                f"{entry.get('yizi_count', 0)} | {entry.get('total_fengdan', '-')} | "
                f"{entry.get('row_count', 0)} |"
            )

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 汇总文件由日期目录内的隐藏元数据自动重建。",
            "- 同一日期优先保留实时竞价版本，避免被缺少涨幅的历史快照覆盖。",
            "",
        ]
    )

    summary_path = output_root / summary_name
    write_text(summary_path, "\n".join(lines))
    return summary_path


def launch_browser(playwright):
    def launch_executable(path: Path):
        return playwright.chromium.launch(
            executable_path=str(path),
            headless=True,
            proxy={"server": "direct://"},
            args=BROWSER_LAUNCH_ARGS,
        )

    def launch_channel(channel: str | None = None):
        kwargs: dict[str, Any] = {
            "headless": True,
            "proxy": {"server": "direct://"},
            "args": BROWSER_LAUNCH_ARGS,
        }
        if channel:
            kwargs["channel"] = channel
        return playwright.chromium.launch(**kwargs)

    attempts = [
        *[
            (str(path), lambda path=path: launch_executable(path))
            for path in SYSTEM_BROWSER_CANDIDATES
            if str(path) and path.exists()
        ],
        ("msedge", lambda: launch_channel("msedge")),
        ("chrome", lambda: launch_channel("chrome")),
        ("chromium", launch_channel),
    ]
    errors: list[str] = []

    for name, launcher in attempts:
        try:
            return launcher()
        except PlaywrightError as exc:
            errors.append(f"{name}: {exc}")

    raise SystemExit(
        "未找到可用浏览器。优先使用系统 Edge，其次尝试 Chrome 和 Playwright Chromium。"
        "如本机未安装 Edge/Chrome，请先安装浏览器或执行 `playwright install chromium`。\n"
        + "\n".join(errors)
    )


def collect_row_objects(page, body_selector: str) -> list[dict[str, Any]]:
    selector = f"{body_selector} .fd"
    count = page.locator(selector).count()
    if count == 0:
        return []

    script = """
    (elements) => elements.map((td, index) => {
      const bold = td.querySelector('b');
      let name = '';
      if (bold) {
        const textNode = Array.from(bold.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim()
        );
        name = textNode ? textNode.textContent.trim() : bold.textContent.trim();
      }

      const tags = Array.from(td.querySelectorAll('i p'))
        .map((p) => p.textContent.trim())
        .filter(Boolean);

      const values = Array.from(td.children)
        .filter((child) => child.tagName === 'SPAN')
        .map((span) => span.textContent.trim())
        .filter(Boolean);

      return {
        seq: index + 1,
        code: td.getAttribute('code') || '',
        name,
        tags,
        values,
      };
    })
    """
    return page.locator(selector).evaluate_all(script)


def to_stock_rows(raw_rows: list[dict[str, Any]]) -> list[StockRow]:
    rows: list[StockRow] = []
    for raw in raw_rows:
        values = raw.get("values") or []
        rows.append(
            StockRow(
                seq=int(raw.get("seq") or 0),
                code=str(raw.get("code") or ""),
                name=str(raw.get("name") or ""),
                tags=[str(tag) for tag in (raw.get("tags") or []) if str(tag).strip()],
                amount_915=str(values[0]) if len(values) > 0 else "-",
                amount_920=str(values[1]) if len(values) > 1 else "-",
                amount_925=str(values[2]) if len(values) > 2 else "-",
                change_pct=str(values[3]) if len(values) > 3 else "-",
            )
        )
    return rows


def collect_sections(wait_ms: int) -> list[SnapshotSection]:
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        try:
            page.goto(SOURCE_PAGE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                """
                () => {
                  const header = document.querySelector('#thlive');
                  const rows = document.querySelectorAll('#tblive .fd, #tb1 .fd, #tb2 .fd, #tb3 .fd, #tb4 .fd');
                  return !!header && /\\d{4}-\\d{2}-\\d{2}/.test(header.innerText) && rows.length > 0;
                }
                """,
                timeout=60000,
            )
            if wait_ms:
                page.wait_for_timeout(wait_ms)

            sections: list[SnapshotSection] = []
            for section_type, header_selector, body_selector in HEADER_SELECTORS:
                header_html = page.locator(header_selector).inner_html().strip()
                if not header_html or not re.search(r"\d{4}-\d{2}-\d{2}", header_html):
                    continue
                date_text, yizi_count, total_fengdan = parse_header_html(header_html)
                raw_rows = collect_row_objects(page, body_selector)
                sections.append(
                    SnapshotSection(
                        date=date_text,
                        section_type=section_type,
                        yizi_count=yizi_count,
                        total_fengdan=total_fengdan,
                        rows=to_stock_rows(raw_rows),
                    )
                )

            return sections
        finally:
            browser.close()


def filter_sections(sections: list[SnapshotSection], args: argparse.Namespace) -> list[SnapshotSection]:
    filtered = sections

    if args.latest_only:
        filtered = [section for section in filtered if section.section_type == "live"]
    if args.history_only:
        filtered = [section for section in filtered if section.section_type == "history"]
    if args.date:
        filtered = [section for section in filtered if section.date == args.date.strip()]

    if not filtered:
        available = ", ".join(section.date for section in sections) or "无"
        raise SystemExit(f"没有匹配到可导出的竞价数据。当前页面可用日期：{available}")

    return filtered


def should_skip_write(target_path: Path, new_section: SnapshotSection) -> bool:
    existing_meta = read_meta(target_path)
    if not existing_meta:
        return False

    existing_type = existing_meta.get("section_type")
    if existing_type == "live" and new_section.section_type == "history":
        return True
    return False


def export_sections(
    sections: list[SnapshotSection],
    output_root: Path,
    summary_name: str,
    daily_name: str,
) -> tuple[list[Path], list[Path], Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []
    skipped_files: list[Path] = []

    for section in sections:
        daily_path = output_root / section.date / daily_name
        if should_skip_write(daily_path, section):
            skipped_files.append(daily_path)
            continue
        write_text(daily_path, build_daily_markdown(section))
        written_files.append(daily_path)

    summary_path = rebuild_summary(output_root, summary_name, daily_name)
    return written_files, skipped_files, summary_path


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()

    sections = collect_sections(args.wait_ms)
    filtered_sections = filter_sections(sections, args)
    written_files, skipped_files, summary_path = export_sections(
        sections=filtered_sections,
        output_root=output_root,
        summary_name=args.summary_file,
        daily_name=args.daily_file,
    )

    print(f"输出根目录: {output_root}")
    print(f"页面可见日期数: {len(sections)}")
    print(f"本次处理日期数: {len(filtered_sections)}")
    print(f"写入文件数: {len(written_files)}")
    for path in written_files:
        print(path)
    if skipped_files:
        print(f"跳过文件数: {len(skipped_files)}")
        for path in skipped_files:
            print(f"SKIP {path}")
    print(f"汇总文件: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
