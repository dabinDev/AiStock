#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from shared import now_text, today_in_shanghai, write_text

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, sync_playwright
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 playwright 依赖，请先执行 `pip install playwright`。") from exc


SITE_BASE = "https://" + "".join(["duan", "xian", "xia"]) + ".com"
SOURCE_PAGE = f"{SITE_BASE}/mob/jjyd"
DEFAULT_DAILY_FILE = "jjyd.md"
DEFAULT_SUMMARY_FILE = "jjyd_summary.md"
META_PREFIX = "<!-- JJYD_META "
META_SUFFIX = " -->"
BROWSER_LAUNCH_ARGS = ["--proxy-server=direct://", "--proxy-bypass-list=*"]


@dataclass(frozen=True)
class SnapshotRow:
    seq: int
    name: str
    code: str
    values: list[str]


@dataclass(frozen=True)
class SnapshotTable:
    key: str
    title: str
    headers: list[str]
    rows: list[SnapshotRow]


@dataclass(frozen=True)
class JjydSnapshot:
    snapshot_date: str
    tables: list[SnapshotTable]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出竞价异动与竞价抢筹到 Markdown")
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
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=3000,
        help="页面首屏可用后的额外等待毫秒数，默认 3000",
    )
    args = parser.parse_args()
    if args.wait_ms < 0:
        parser.error("--wait-ms 不能小于 0")
    return args


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sanitize_output_text(content: str) -> str:
    content = content.replace("\u77ed\u7ebf\u4fa0", "市场")
    content = re.sub(r"^>\s*来源.*(?:\r?\n)?", "", content, flags=re.MULTILINE)
    content = re.sub(r"https?://[^\s)>\]]+", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() + "\n"


def escape_md(value: Any) -> str:
    text = "-" if value in (None, "", []) else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("|", "\\|").replace("\n", "<br>")


def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    pattern = re.escape(META_PREFIX) + r"(.*?)" + re.escape(META_SUFFIX)
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def launch_browser(playwright):
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

    detail = "\n".join(errors)
    raise SystemExit(f"无法启动浏览器，请先安装 Edge/Chrome 或执行 `playwright install`。\n{detail}")


def split_name_code(cell_text: str) -> tuple[str, str]:
    lines = [line.strip() for line in cell_text.splitlines() if line.strip()]
    if len(lines) >= 2 and re.fullmatch(r"\d{6}", lines[1]):
        return lines[0], lines[1]

    match = re.search(r"(\d{6})", cell_text)
    if match:
        code = match.group(1)
        name = cell_text.replace(code, "").replace("\n", " ").strip()
        return name or "-", code

    return (lines[0] if lines else "-"), "-"


def collect_headers(page: Page, selector: str) -> list[str]:
    headers = page.locator(selector).evaluate_all(
        """
        (nodes) => nodes
          .map((node) => node.innerText.trim())
          .filter(Boolean)
        """
    )
    return [str(item) for item in headers]


def collect_raw_rows(page: Page, selector: str) -> list[list[str]]:
    rows = page.locator(selector).evaluate_all(
        """
        (nodes) => nodes.map((tr) =>
          Array.from(tr.children).map((td) => td.innerText.trim())
        )
        """
    )
    return [[str(value) for value in row] for row in rows]


def to_snapshot_rows(raw_rows: list[list[str]]) -> list[SnapshotRow]:
    rows: list[SnapshotRow] = []
    for index, raw_row in enumerate(raw_rows, start=1):
        first_cell = raw_row[0] if raw_row else ""
        name, code = split_name_code(first_cell)
        rows.append(
            SnapshotRow(
                seq=index,
                name=name,
                code=code,
                values=list(raw_row[1:]),
            )
        )
    return rows


def collect_weimai_table(page: Page) -> SnapshotTable:
    headers = collect_headers(page, "#zthead th")
    raw_rows = collect_raw_rows(page, "#dblist tr")
    return SnapshotTable(
        key="weimai",
        title="竞价异动（涨停委买）",
        headers=headers,
        rows=to_snapshot_rows(raw_rows),
    )


def collect_qiangchou_tables(page: Page) -> list[SnapshotTable]:
    page.locator('.getstock[name="qiangchou"]').click()
    page.wait_for_function(
        "() => document.querySelectorAll('#qclist tr').length > 0 || document.querySelectorAll('#gblist tr').length > 0",
        timeout=30000,
    )
    page.wait_for_timeout(1000)

    headers = collect_headers(page, "#qthead th")
    qclist_rows = collect_raw_rows(page, "#qclist tr")
    gblist_rows = collect_raw_rows(page, "#gblist tr")
    return [
        SnapshotTable(
            key="qiangchou_920_925",
            title="竞价抢筹：9:20 - 9:25 的抢筹幅度",
            headers=headers,
            rows=to_snapshot_rows(qclist_rows),
        ),
        SnapshotTable(
            key="qiangchou_last_second",
            title="竞价抢筹：竞价最后 1 秒的抢筹幅度",
            headers=headers,
            rows=to_snapshot_rows(gblist_rows),
        ),
    ]


def collect_snapshot(wait_ms: int) -> JjydSnapshot:
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1400, "height": 2200})
        try:
            page.goto(SOURCE_PAGE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                "() => document.querySelectorAll('.getstock').length > 0 && document.querySelectorAll('#dblist tr').length > 0",
                timeout=60000,
            )
            if wait_ms:
                page.wait_for_timeout(wait_ms)

            tables = [collect_weimai_table(page)]
            tables.extend(collect_qiangchou_tables(page))
            return JjydSnapshot(
                snapshot_date=today_in_shanghai().isoformat(),
                tables=tables,
            )
        finally:
            browser.close()


def export_headers(headers: list[str]) -> list[str]:
    if not headers:
        return ["名称", "代码"]
    return ["名称", "代码", *headers[1:]]


def render_table(table: SnapshotTable) -> list[str]:
    headers = ["序号", *export_headers(table.headers)]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    if not table.rows:
        lines.append("| - | - | - |")
        return lines

    for row in table.rows:
        values = [row.seq, row.name, row.code, *row.values]
        lines.append("| " + " | ".join(escape_md(value) for value in values) + " |")
    return lines


def build_daily_markdown(snapshot: JjydSnapshot) -> str:
    counts = {table.key: len(table.rows) for table in snapshot.tables}
    meta = {
        "date": snapshot.snapshot_date,
        "counts": counts,
    }

    lines = [
        f"# 竞价异动 - {snapshot.snapshot_date}",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 生成时间：{now_text()}",
        f"> 数据日期：{snapshot.snapshot_date}",
        f"> 竞价异动条数：{counts.get('weimai', 0)}",
        f"> 抢筹（9:20 - 9:25）条数：{counts.get('qiangchou_920_925', 0)}",
        f"> 抢筹（最后 1 秒）条数：{counts.get('qiangchou_last_second', 0)}",
        "",
        f"{META_PREFIX}{compact_json(meta)}{META_SUFFIX}",
        "",
    ]

    for table in snapshot.tables:
        lines.append(f"## {table.title}")
        lines.append("")
        lines.extend(render_table(table))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def rebuild_summary(output_root: Path, summary_name: str, daily_name: str) -> Path:
    rows: list[dict[str, Any]] = []

    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        daily_path = child / daily_name
        meta = read_meta(daily_path)
        if not meta:
            continue
        rows.append(meta)

    rows.sort(key=lambda item: item["date"], reverse=True)

    lines = [
        "# 竞价异动汇总",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 重建时间：{now_text()}",
        "",
        "| 日期 | 竞价异动 | 9:20 - 9:25 抢筹 | 最后 1 秒抢筹 | 文件 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]

    if not rows:
        lines.append("| - | 0 | 0 | 0 | - |")
    else:
        for row in rows:
            date_text = row["date"]
            counts = row.get("counts", {})
            daily_link = f"[{daily_name}](./{date_text}/{daily_name})"
            lines.append(
                f"| {date_text} | {counts.get('weimai', 0)} | {counts.get('qiangchou_920_925', 0)} | "
                f"{counts.get('qiangchou_last_second', 0)} | {daily_link} |"
            )

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- `jjyd.md` 只导出当前页面可见快照，不回放历史竞价异动。",
            "- `竞价异动` 对应 `涨停委买` 标签页。",
            "- `竞价抢筹` 同时保留 `9:20 - 9:25` 和 `最后 1 秒` 两张表，便于 AI 分段检索。",
            "",
        ]
    )

    summary_path = output_root / summary_name
    write_text(summary_path, sanitize_output_text("\n".join(lines)))
    return summary_path


def export_snapshot(
    snapshot: JjydSnapshot,
    output_root: Path,
    summary_name: str,
    daily_name: str,
) -> tuple[Path, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    daily_path = output_root / snapshot.snapshot_date / daily_name
    write_text(daily_path, sanitize_output_text(build_daily_markdown(snapshot)))
    summary_path = rebuild_summary(output_root, summary_name, daily_name)
    return daily_path, summary_path


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()

    snapshot = collect_snapshot(args.wait_ms)
    daily_path, summary_path = export_snapshot(
        snapshot=snapshot,
        output_root=output_root,
        summary_name=args.summary_file,
        daily_name=args.daily_file,
    )

    print(f"输出根目录：{output_root}")
    print(f"目标日期：{snapshot.snapshot_date}")
    for table in snapshot.tables:
        print(f"{table.title}：{len(table.rows)} 条")
    print(daily_path)
    print(f"汇总文件：{summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
