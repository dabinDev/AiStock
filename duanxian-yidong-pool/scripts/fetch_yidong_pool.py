#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
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


SITE_BASE = "https://" + "".join(["duan", "xian", "xia"]) + ".com"
HOME_PAGE = f"{SITE_BASE}/"
YIDONG_PAGE = f"{SITE_BASE}/web/yidong"
POOL_PAGE = f"{SITE_BASE}/web/pool"
DEFAULT_YIDONG_FILE = "yidong.md"
DEFAULT_POOL_FILE = "pool.md"
DEFAULT_JINJI_FILE = "jinji.md"
DEFAULT_SUMMARY_FILE = "yidong_pool_summary.md"
YIDONG_META_PREFIX = "<!-- YIDONG_META "
POOL_META_PREFIX = "<!-- POOL_META "
JINJI_META_PREFIX = "<!-- JINJI_META "
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
POOL_TAB_MAP = {
    "zt": "涨停",
    "lb": "连板",
    "zb": "炸板",
    "cz": "冲涨",
    "fx": "热门",
    "dm": "大面",
    "dt": "跌停",
}
POOL_TAB_ALIASES = {
    "涨停": "zt",
    "连板": "lb",
    "炸板": "zb",
    "冲涨": "cz",
    "热门": "fx",
    "大面": "dm",
    "跌停": "dt",
}


@dataclass(frozen=True)
class YidongRow:
    seq: int
    name: str
    code: str
    status: str
    board: str
    time: str
    description: str
    concept: str


@dataclass(frozen=True)
class YidongSnapshot:
    snapshot_date: str
    total_row_count: int
    exported_row_count: int
    active_types: list[str]
    status_counts: dict[str, int]
    rows: list[YidongRow]


@dataclass(frozen=True)
class PoolSection:
    tab_code: str
    label: str
    button_text: str
    expected_count: int
    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class PoolSnapshot:
    snapshot_date: str
    stats: dict[str, dict[str, str]]
    sections: list[PoolSection]


@dataclass(frozen=True)
class JinjiEntry:
    stage: str
    rate: str
    market: str
    name: str
    code: str
    result: str
    result_class: str
    change_pct: str
    concept: str


@dataclass(frozen=True)
class JinjiStage:
    stage: str
    rate: str
    entries: list[JinjiEntry]


@dataclass(frozen=True)
class JinjiSnapshot:
    snapshot_date: str
    data_date: str
    source_url: str
    stage_count: int
    entry_count: int
    result_counts: dict[str, int]
    stages: list[JinjiStage]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出异动播报与涨停股池到 Markdown")
    parser.add_argument("--output-root", default=".", help="输出根目录，默认当前目录")
    parser.add_argument(
        "--yidong-file",
        default=DEFAULT_YIDONG_FILE,
        help=f"异动播报日文件名，默认 {DEFAULT_YIDONG_FILE}",
    )
    parser.add_argument(
        "--pool-file",
        default=DEFAULT_POOL_FILE,
        help=f"涨停股池日文件名，默认 {DEFAULT_POOL_FILE}",
    )
    parser.add_argument(
        "--jinji-file",
        default=DEFAULT_JINJI_FILE,
        help=f"首页底部涨停股票池日文件名，默认 {DEFAULT_JINJI_FILE}",
    )
    parser.add_argument(
        "--summary-file",
        default=DEFAULT_SUMMARY_FILE,
        help=f"根目录汇总文件名，默认 {DEFAULT_SUMMARY_FILE}",
    )
    parser.add_argument("--yidong-only", action="store_true", help="只导出异动播报")
    parser.add_argument("--pool-only", action="store_true", help="只导出涨停股池")
    parser.add_argument("--jinji-only", action="store_true", help="只导出首页底部涨停股票池")
    parser.add_argument("--no-jinji", action="store_true", help="不导出首页底部涨停股票池")
    parser.add_argument(
        "--pool-tabs",
        help="只导出指定股池标签页，逗号分隔，支持 zt,lb,zb,cz,fx,dm,dt 或中文名",
    )
    parser.add_argument(
        "--yidong-max-rows",
        type=int,
        default=0,
        help="限制导出的异动条数，0 表示导出当前可见全部行，默认 0",
    )
    parser.add_argument(
        "--yidong-wait-ms",
        type=int,
        default=8000,
        help="异动播报首屏可用后的额外等待毫秒数，默认 8000",
    )
    parser.add_argument(
        "--pool-wait-ms",
        type=int,
        default=6000,
        help="涨停股池首屏可用后的额外等待毫秒数，默认 6000",
    )
    args = parser.parse_args()

    only_flags = [args.yidong_only, args.pool_only, args.jinji_only]
    if sum(1 for enabled in only_flags if enabled) > 1:
        parser.error("--yidong-only、--pool-only 和 --jinji-only 只能选择一个")

    if args.no_jinji and args.jinji_only:
        parser.error("--no-jinji 和 --jinji-only 不能同时使用")

    if args.yidong_max_rows < 0:
        parser.error("--yidong-max-rows 不能小于 0")

    if args.yidong_wait_ms < 0 or args.pool_wait_ms < 0:
        parser.error("等待时间参数不能小于 0")

    args.pool_tab_codes = normalize_pool_tabs(args.pool_tabs, parser)
    return args


def normalize_pool_tabs(raw_value: str | None, parser: argparse.ArgumentParser) -> list[str] | None:
    if not raw_value:
        return None

    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not values:
        parser.error("--pool-tabs 不能为空")

    result: list[str] = []
    for item in values:
        tab_code = item.lower()
        if tab_code in POOL_TAB_MAP:
            normalized = tab_code
        elif item in POOL_TAB_ALIASES:
            normalized = POOL_TAB_ALIASES[item]
        else:
            parser.error(f"不支持的 --pool-tabs 值：{item}")

        if normalized not in result:
            result.append(normalized)

    return result


def current_date_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")


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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("|", "\\|").replace("\n", "<br>")


def parse_count_from_button(text: str) -> int:
    match = re.search(r"\((\d+)\)", text)
    return int(match.group(1)) if match else 0


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_meta(path: Path, prefix: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    pattern = re.escape(prefix) + r"(.*?)" + re.escape(META_SUFFIX)
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


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
        "未找到可用浏览器。优先使用系统 Edge，其次尝试 Chrome 和 Playwright Chromium。\n"
        "如本机未安装 Edge/Chrome，请先安装浏览器或执行 `playwright install chromium`。\n"
        + "\n".join(errors)
    )


def wait_for_yidong_ready(page: Page, wait_ms: int) -> None:
    page.goto(YIDONG_PAGE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function(
        "() => document.querySelectorAll('tr.yd').length > 0",
        timeout=60000,
    )
    if wait_ms:
        page.wait_for_timeout(wait_ms)


def wait_for_pool_ready(page: Page, wait_ms: int) -> None:
    page.goto(POOL_PAGE, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function(
        "() => document.querySelectorAll('.getstock').length > 0 && document.querySelectorAll('#ztlist tr').length > 0",
        timeout=60000,
    )
    if wait_ms:
        page.wait_for_timeout(wait_ms)


def collect_yidong_snapshot(page: Page, wait_ms: int, max_rows: int) -> YidongSnapshot:
    wait_for_yidong_ready(page, wait_ms)

    snapshot = page.evaluate(
        """
        (maxRows) => {
          const allRows = Array.from(document.querySelectorAll("tr.yd"));
          const limit = maxRows && maxRows > 0 ? maxRows : allRows.length;
          const rows = allRows.slice(0, limit).map((tr, index) => ({
            seq: index + 1,
            name: tr.querySelector(".kline")?.innerText.trim() || "",
            code: tr.querySelector(".code")?.innerText.trim() || "",
            status: tr.children[2]?.innerText.trim() || "",
            board: tr.querySelector(".ban")?.innerText.trim() || "",
            time: tr.querySelector(".addtime")?.innerText.trim() || "",
            description: tr.querySelector(".desc")?.innerText.trim() || "",
            concept: tr.querySelector(".rs")?.innerText.trim() || ""
          }));

          const activeTypes = Array.from(document.querySelectorAll("input.ydtype:checked"))
            .map((node) => node.getAttribute("name") || node.id || "")
            .filter(Boolean);

          return {
            totalRowCount: allRows.length,
            activeTypes,
            rows
          };
        }
        """,
        max_rows,
    )

    rows = [
        YidongRow(
            seq=int(item["seq"]),
            name=str(item["name"]),
            code=str(item["code"]),
            status=str(item["status"]),
            board=str(item["board"]),
            time=str(item["time"]),
            description=str(item["description"]),
            concept=str(item["concept"]),
        )
        for item in snapshot["rows"]
    ]
    status_counts = dict(Counter(row.status for row in rows))

    return YidongSnapshot(
        snapshot_date=current_date_text(),
        total_row_count=int(snapshot["totalRowCount"]),
        exported_row_count=len(rows),
        active_types=[str(item) for item in snapshot["activeTypes"]],
        status_counts=status_counts,
        rows=rows,
    )


def get_pool_stats(page: Page) -> dict[str, dict[str, str]]:
    raw_stats = page.evaluate(
        """
        () => ({
          "涨停": {
            today: document.querySelector(".ztnum_1")?.innerText.trim() || "-",
            yesterday: document.querySelector(".ztnum_2")?.innerText.trim() || "-"
          },
          "连板": {
            today: document.querySelector(".lbnum_1")?.innerText.trim() || "-",
            yesterday: document.querySelector(".lbnum_2")?.innerText.trim() || "-"
          },
          "封板率": {
            today: document.querySelector(".fbnum_1")?.innerText.trim() || "-",
            yesterday: document.querySelector(".fbnum_2")?.innerText.trim() || "-"
          },
          "炸板": {
            today: document.querySelector(".zbnum_1")?.innerText.trim() || "-",
            yesterday: document.querySelector(".zbnum_2")?.innerText.trim() || "-"
          },
          "跌停": {
            today: document.querySelector(".dtnum_1")?.innerText.trim() || "-",
            yesterday: document.querySelector(".dtnum_2")?.innerText.trim() || "-"
          }
        })
        """
    )
    return {
        str(metric): {
            "today": str(values.get("today", "-")),
            "yesterday": str(values.get("yesterday", "-")),
        }
        for metric, values in raw_stats.items()
    }


def list_pool_buttons(page: Page) -> list[dict[str, str]]:
    raw_buttons = page.evaluate(
        """
        () => Array.from(document.querySelectorAll(".getstock")).map((button) => ({
          text: button.innerText.trim(),
          name: button.getAttribute("name") || "",
          head: button.getAttribute("head") || ""
        }))
        """
    )
    return [
        {
            "text": str(item["text"]),
            "name": str(item["name"]),
            "head": str(item["head"]),
        }
        for item in raw_buttons
        if item.get("name")
    ]


def collect_pool_section(page: Page, button: dict[str, str]) -> PoolSection:
    button_selector = f'.getstock[name="{button["name"]}"]'
    page.locator(button_selector).click()

    expected_count = parse_count_from_button(button["text"])
    if expected_count > 0:
        page.wait_for_function(
            """
            (tabName) => document.querySelectorAll(`#${tabName}list tr`).length > 0
            """,
            arg=button["name"],
            timeout=30000,
        )
    else:
        page.wait_for_timeout(500)

    page.wait_for_timeout(700)

    headers = page.evaluate(
        """
        (headId) => Array.from(document.querySelectorAll(`#${headId} th`))
          .map((node) => node.innerText.trim())
          .filter(Boolean)
        """,
        button["head"],
    )

    rows = page.evaluate(
        """
        (tabName) => Array.from(document.querySelectorAll(`#${tabName}list tr`)).map((tr) =>
          Array.from(tr.children).map((td) => td.innerText.trim())
        )
        """,
        button["name"],
    )

    return PoolSection(
        tab_code=button["name"],
        label=POOL_TAB_MAP.get(button["name"], button["name"]),
        button_text=button["text"],
        expected_count=expected_count,
        headers=[str(header) for header in headers],
        rows=[[str(value) for value in row] for row in rows],
    )


def collect_pool_snapshot(page: Page, wait_ms: int, selected_tabs: list[str] | None) -> PoolSnapshot:
    wait_for_pool_ready(page, wait_ms)
    stats = get_pool_stats(page)
    buttons = list_pool_buttons(page)

    if selected_tabs:
        buttons = [button for button in buttons if button["name"] in selected_tabs]
        if not buttons:
            raise SystemExit("没有匹配到可导出的涨停股池标签页。")

    sections = [collect_pool_section(page, button) for button in buttons]
    return PoolSnapshot(
        snapshot_date=current_date_text(),
        stats=stats,
        sections=sections,
    )


def parse_jinji_entry(stage: str, rate: str, raw_entry: str) -> JinjiEntry:
    code_match = re.search(r"<#'(\d+)'>", raw_entry)
    name_match = re.search(r">([^<]+)</span>（", raw_entry)
    result_match = re.search(r"class='([^']+)'>([^<]+)</[bi]>", raw_entry)
    change_match = re.search(r"\[<Aa><[bi]>([^<]+)</[bi]></span>\]", raw_entry)
    concept_match = re.search(r"<u>(.*?)</u>", raw_entry)

    if not (code_match and name_match and result_match and change_match and concept_match):
        raise RuntimeError(f"无法解析涨停股票池条目：{raw_entry}")

    market = raw_entry.split("<#'", 1)[0].strip()
    result_class, result_text = result_match.groups()

    return JinjiEntry(
        stage=stage,
        rate=rate,
        market=market,
        name=name_match.group(1).strip(),
        code=code_match.group(1).strip(),
        result=result_text.strip(),
        result_class=result_class.strip(),
        change_pct=change_match.group(1).strip(),
        concept=concept_match.group(1).strip(),
    )


def collect_jinji_snapshot(page: Page) -> JinjiSnapshot:
    page.goto(HOME_PAGE, wait_until="domcontentloaded", timeout=60000)
    result = page.evaluate(
        """
        async () => {
          const candidates = [];
          try {
            const infoResponse = await fetch("/vendor/stockdata/datasource.json", { credentials: "include" });
            if (infoResponse.ok) {
              const info = await infoResponse.json();
              if (info?.istrade === 1 && info?.data_url) {
                candidates.push(`${String(info.data_url).replace(/\\/$/, "")}/vendor/stockdata/jinjidata.json?_=${Date.now()}`);
              }
            }
          } catch (error) {
            // Ignore datasource lookup errors and fall back to same-origin JSON.
          }

          candidates.push("/vendor/stockdata/jinjidata.json");

          let lastError = "";
          for (const source of [...new Set(candidates)]) {
            try {
              const response = await fetch(source, { credentials: "include" });
              if (!response.ok) {
                lastError = `${source} -> ${response.status}`;
                continue;
              }
              return {
                source_url: new URL(source, location.href).href,
                payload: await response.json()
              };
            } catch (error) {
              lastError = `${source} -> ${String(error)}`;
            }
          }

          throw new Error(lastError || "fetch jinjidata failed");
        }
        """
    )

    payload = result["payload"]
    source_url = str(result["source_url"])

    html = str(payload.get("html") or "")
    data_date = str(payload.get("date") or current_date_text())

    stages: list[JinjiStage] = []
    result_counts: Counter[str] = Counter()

    for stage_text, rate_text, body_html in re.findall(
        r"<tr><td>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td></tr>",
        html,
    ):
        entries: list[JinjiEntry] = []
        for raw_entry in re.findall(r"<@>(.*?)</div>", body_html):
            entry = parse_jinji_entry(stage_text.strip(), rate_text.strip(), raw_entry)
            entries.append(entry)
            result_counts[entry.result] += 1

        stages.append(
            JinjiStage(
                stage=stage_text.strip(),
                rate=rate_text.strip(),
                entries=entries,
            )
        )

    return JinjiSnapshot(
        snapshot_date=current_date_text(),
        data_date=data_date,
        source_url=source_url,
        stage_count=len(stages),
        entry_count=sum(len(stage.entries) for stage in stages),
        result_counts=dict(result_counts),
        stages=stages,
    )


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not headers:
        return ["- 无表头数据"]

    lines = [
        "| " + " | ".join(escape_md(header) for header in headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    if not rows:
        lines.append("| " + " | ".join(["-"] * len(headers)) + " |")
        return lines

    for row in rows:
        values = list(row[: len(headers)])
        if len(values) < len(headers):
            values.extend(["-"] * (len(headers) - len(values)))
        lines.append("| " + " | ".join(escape_md(value) for value in values) + " |")
    return lines


def build_yidong_markdown(snapshot: YidongSnapshot) -> str:
    meta = {
        "snapshot_date": snapshot.snapshot_date,
        "total_row_count": snapshot.total_row_count,
        "exported_row_count": snapshot.exported_row_count,
        "active_types": snapshot.active_types,
        "status_counts": snapshot.status_counts,
    }

    lines = [
        f"# 异动播报 - {snapshot.snapshot_date}",
        "",
        f"> 来源主页：{HOME_PAGE}",
        f"> 来源页面：{YIDONG_PAGE}",
        f"> 生成时间：{now_text()}",
        f"> 页面可见总条数：{snapshot.total_row_count}",
        f"> 本次导出条数：{snapshot.exported_row_count}",
        f"> 当前勾选类型：{'、'.join(snapshot.active_types) if snapshot.active_types else '-'}",
        "",
        f"{YIDONG_META_PREFIX}{compact_json(meta)}{META_SUFFIX}",
        "",
        "## 状态统计",
        "",
        "| 状态 | 条数 |",
        "| --- | ---: |",
    ]

    if snapshot.status_counts:
        for status, count in sorted(snapshot.status_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| {escape_md(status)} | {count} |")
    else:
        lines.append("| - | 0 |")

    lines.extend(
        [
            "",
            "## 异动明细",
            "",
        ]
    )
    headers = ["序号", "名称", "代码", "状态", "板数", "时间", "描述", "题材"]
    rows = [
        [
            str(row.seq),
            row.name,
            row.code,
            row.status,
            row.board,
            row.time,
            row.description,
            row.concept,
        ]
        for row in snapshot.rows
    ]
    lines.extend(render_table(headers, rows))
    return "\n".join(lines).rstrip() + "\n"


def build_pool_markdown(snapshot: PoolSnapshot) -> str:
    meta = {
        "snapshot_date": snapshot.snapshot_date,
        "stats": snapshot.stats,
        "sections": [
            {
                "tab_code": section.tab_code,
                "label": section.label,
                "button_text": section.button_text,
                "row_count": len(section.rows),
                "expected_count": section.expected_count,
            }
            for section in snapshot.sections
        ],
    }

    lines = [
        f"# 涨停股池 - {snapshot.snapshot_date}",
        "",
        f"> 来源主页：{HOME_PAGE}",
        f"> 来源页面：{POOL_PAGE}",
        f"> 生成时间：{now_text()}",
        f"> 导出标签页：{'、'.join(section.label for section in snapshot.sections) if snapshot.sections else '-'}",
        "",
        f"{POOL_META_PREFIX}{compact_json(meta)}{META_SUFFIX}",
        "",
        "## 今日概览",
        "",
        "| 指标 | 今日 | 昨日 |",
        "| --- | ---: | ---: |",
    ]

    for metric in ("涨停", "连板", "封板率", "炸板", "跌停"):
        values = snapshot.stats.get(metric, {"today": "-", "yesterday": "-"})
        lines.append(f"| {metric} | {escape_md(values['today'])} | {escape_md(values['yesterday'])} |")

    for section in snapshot.sections:
        lines.extend(
            [
                "",
                f"## {section.button_text}",
                "",
                f"> 标签代码：`{section.tab_code}`",
                f"> 实际行数：{len(section.rows)}",
                "",
            ]
        )
        lines.extend(render_table(section.headers, section.rows))

    return "\n".join(lines).rstrip() + "\n"


def build_jinji_markdown(snapshot: JinjiSnapshot) -> str:
    meta = {
        "snapshot_date": snapshot.snapshot_date,
        "data_date": snapshot.data_date,
        "source_url": snapshot.source_url,
        "stage_count": snapshot.stage_count,
        "entry_count": snapshot.entry_count,
        "result_counts": snapshot.result_counts,
    }

    lines = [
        f"# 涨停股票池 - {snapshot.snapshot_date}",
        "",
        f"> 来源主页：{HOME_PAGE}",
        f"> 来源接口：{snapshot.source_url}",
        f"> 数据日期：{snapshot.data_date}",
        f"> 生成时间：{now_text()}",
        f"> 晋级阶段数：{snapshot.stage_count}",
        f"> 个股条数：{snapshot.entry_count}",
        "",
        f"{JINJI_META_PREFIX}{compact_json(meta)}{META_SUFFIX}",
        "",
        "## 阶段汇总",
        "",
        "| 阶段 | 晋级率 | 个股数 |",
        "| --- | --- | ---: |",
    ]

    if snapshot.stages:
        for stage in snapshot.stages:
            lines.append(f"| {escape_md(stage.stage)} | {escape_md(stage.rate)} | {len(stage.entries)} |")
    else:
        lines.append("| - | - | 0 |")

    lines.extend(
        [
            "",
            "## 结果统计",
            "",
            "| 结果 | 条数 |",
            "| --- | ---: |",
        ]
    )

    if snapshot.result_counts:
        for result, count in sorted(snapshot.result_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| {escape_md(result)} | {count} |")
    else:
        lines.append("| - | 0 |")

    lines.extend(
        [
            "",
            "## 个股明细",
            "",
        ]
    )

    headers = ["阶段", "晋级率", "市场", "名称", "代码", "结果", "涨幅", "概念"]
    rows = [
        [
            entry.stage,
            entry.rate,
            entry.market,
            entry.name,
            entry.code,
            entry.result,
            entry.change_pct,
            entry.concept,
        ]
        for stage in snapshot.stages
        for entry in stage.entries
    ]
    lines.extend(render_table(headers, rows))
    return "\n".join(lines).rstrip() + "\n"


def rebuild_summary(output_root: Path, summary_name: str, yidong_name: str, pool_name: str, jinji_name: str) -> Path:
    entries: list[dict[str, Any]] = []

    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue

        yidong_meta = read_meta(child / yidong_name, YIDONG_META_PREFIX)
        pool_meta = read_meta(child / pool_name, POOL_META_PREFIX)
        jinji_meta = read_meta(child / jinji_name, JINJI_META_PREFIX)
        if not yidong_meta and not pool_meta and not jinji_meta:
            continue

        entries.append(
            {
                "date": child.name,
                "yidong_meta": yidong_meta,
                "pool_meta": pool_meta,
                "jinji_meta": jinji_meta,
            }
        )

    entries.sort(key=lambda item: item["date"], reverse=True)

    lines = [
        "# 异动播报与涨停股池汇总",
        "",
        f"> 来源主页：{HOME_PAGE}",
        f"> 汇总生成时间：{now_text()}",
        "",
        "| 日期 | 异动条数 | 异动文件 | 涨停 | 连板 | 炸板 | 跌停 | 股池文件 | 涨停股票池条数 | 涨停股票池文件 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]

    if not entries:
        lines.append("| - | 0 | - | - | - | - | - | - | 0 | - |")
    else:
        for entry in entries:
            day = entry["date"]
            yidong_meta = entry["yidong_meta"] or {}
            pool_meta = entry["pool_meta"] or {}
            jinji_meta = entry["jinji_meta"] or {}
            stats = pool_meta.get("stats", {}) if pool_meta else {}

            yidong_rows = yidong_meta.get("exported_row_count", "-") if yidong_meta else "-"
            yidong_link = f"[{yidong_name}](./{day}/{yidong_name})" if yidong_meta else "-"
            pool_link = f"[{pool_name}](./{day}/{pool_name})" if pool_meta else "-"
            jinji_rows = jinji_meta.get("entry_count", "-") if jinji_meta else "-"
            jinji_link = f"[{jinji_name}](./{day}/{jinji_name})" if jinji_meta else "-"

            limit_up = stats.get("涨停", {}).get("today", "-")
            boards = stats.get("连板", {}).get("today", "-")
            zaban = stats.get("炸板", {}).get("today", "-")
            limit_down = stats.get("跌停", {}).get("today", "-")

            lines.append(
                f"| {day} | {yidong_rows} | {yidong_link} | {limit_up} | {boards} | {zaban} | {limit_down} | {pool_link} | {jinji_rows} | {jinji_link} |"
            )

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 汇总文件根据日期目录中的隐藏元数据自动重建。",
            "- `异动条数` 是导出时页面当前可见的列表快照，不代表站点完整历史。",
            "- `涨停/连板/炸板/跌停` 来自 `pool.md` 顶部概览中的今日值。",
            "- `涨停股票池条数` 来自首页底部 `jinji.md` 的个股明细总数。",
            "",
        ]
    )

    summary_path = output_root / summary_name
    write_text(summary_path, "\n".join(lines))
    return summary_path


def export_outputs(
    output_root: Path,
    summary_name: str,
    yidong_name: str,
    pool_name: str,
    jinji_name: str,
    yidong_snapshot: YidongSnapshot | None,
    pool_snapshot: PoolSnapshot | None,
    jinji_snapshot: JinjiSnapshot | None,
) -> tuple[list[Path], Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    day_folder = output_root / current_date_text()
    written_files: list[Path] = []

    if yidong_snapshot is not None:
        yidong_path = day_folder / yidong_name
        write_text(yidong_path, build_yidong_markdown(yidong_snapshot))
        written_files.append(yidong_path)

    if pool_snapshot is not None:
        pool_path = day_folder / pool_name
        write_text(pool_path, build_pool_markdown(pool_snapshot))
        written_files.append(pool_path)

    if jinji_snapshot is not None:
        jinji_path = day_folder / jinji_name
        write_text(jinji_path, build_jinji_markdown(jinji_snapshot))
        written_files.append(jinji_path)

    summary_path = rebuild_summary(output_root, summary_name, yidong_name, pool_name, jinji_name)
    return written_files, summary_path


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    if args.yidong_only:
        export_yidong = True
        export_pool = False
        export_jinji = False
    elif args.pool_only:
        export_yidong = False
        export_pool = True
        export_jinji = False
    elif args.jinji_only:
        export_yidong = False
        export_pool = False
        export_jinji = True
    else:
        export_yidong = True
        export_pool = True
        export_jinji = not args.no_jinji

    yidong_snapshot: YidongSnapshot | None = None
    pool_snapshot: PoolSnapshot | None = None
    jinji_snapshot: JinjiSnapshot | None = None

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            if export_yidong:
                yidong_page = browser.new_page(viewport={"width": 1600, "height": 2200})
                try:
                    yidong_snapshot = collect_yidong_snapshot(
                        yidong_page,
                        wait_ms=args.yidong_wait_ms,
                        max_rows=args.yidong_max_rows,
                    )
                finally:
                    yidong_page.close()

            if export_pool:
                pool_page = browser.new_page(viewport={"width": 1600, "height": 2200})
                try:
                    pool_snapshot = collect_pool_snapshot(
                        pool_page,
                        wait_ms=args.pool_wait_ms,
                        selected_tabs=args.pool_tab_codes,
                    )
                finally:
                    pool_page.close()

            if export_jinji:
                jinji_page = browser.new_page(viewport={"width": 1600, "height": 1200})
                try:
                    jinji_snapshot = collect_jinji_snapshot(jinji_page)
                finally:
                    jinji_page.close()
        finally:
            browser.close()

    written_files, summary_path = export_outputs(
        output_root=output_root,
        summary_name=args.summary_file,
        yidong_name=args.yidong_file,
        pool_name=args.pool_file,
        jinji_name=args.jinji_file,
        yidong_snapshot=yidong_snapshot,
        pool_snapshot=pool_snapshot,
        jinji_snapshot=jinji_snapshot,
    )

    print(f"输出根目录: {output_root}")
    print(f"目标日期: {current_date_text()}")
    print(f"写入文件数: {len(written_files)}")
    for path in written_files:
        print(path)
    if yidong_snapshot is not None:
        print(f"异动播报: 页面可见 {yidong_snapshot.total_row_count} 条，本次导出 {yidong_snapshot.exported_row_count} 条")
    if pool_snapshot is not None:
        section_text = ", ".join(f"{section.label}:{len(section.rows)}" for section in pool_snapshot.sections)
        print(f"涨停股池: {section_text}")
    if jinji_snapshot is not None:
        print(f"首页底部涨停股票池: 阶段 {jinji_snapshot.stage_count} 个，个股 {jinji_snapshot.entry_count} 条")
    print(f"汇总文件: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
