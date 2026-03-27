#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from playwright.sync_api import APIRequestContext, sync_playwright
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 playwright 依赖，请先执行 `pip install playwright`。") from exc

SOURCE_PAGE = "https://quote.eastmoney.com/ztb/detail"
API_BASE = "https://push2ex.eastmoney.com"
DEFAULT_SUMMARY_FILE = "eastmoney_shortline_summary.md"
DEFAULT_DAILY_FILE = "shortline.md"
META_PREFIX = "<!-- EASTMONEY_SHORTLINE_META "
META_SUFFIX = " -->"
PAGE_SIZE = 20
REQUEST_TIMEOUT_MS = 20000

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh,zh-CN;q=0.9,en;q=0.8",
    "Referer": SOURCE_PAGE,
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
    'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?1",
    'sec-ch-ua-platform': '"Android"',
}

RowBuilder = Callable[[dict[str, Any]], list[str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按东方财富网页接口导出短线股池 Markdown")
    parser.add_argument("--date", help="单日，支持 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--start-date", help="起始日期，支持 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束日期，支持 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--output-root", default=".", help="输出根目录，默认当前目录")
    parser.add_argument(
        "--summary-file",
        default=DEFAULT_SUMMARY_FILE,
        help=f"根目录汇总文件名，默认 {DEFAULT_SUMMARY_FILE}",
    )
    parser.add_argument(
        "--daily-file",
        default=DEFAULT_DAILY_FILE,
        help=f"日期目录日文件名，默认 {DEFAULT_DAILY_FILE}",
    )
    parser.add_argument("--cookie", default="", help="可选，请求头 Cookie")
    args = parser.parse_args()

    has_single_date = bool(args.date)
    has_range = bool(args.start_date or args.end_date)

    if has_single_date and has_range:
        parser.error("--date 与 --start-date/--end-date 不能同时使用")

    if has_single_date:
        return args

    if bool(args.start_date) ^ bool(args.end_date):
        parser.error("区间模式必须同时提供 --start-date 和 --end-date")

    if not args.start_date or not args.end_date:
        parser.error("必须提供 --date，或者同时提供 --start-date 和 --end-date")

    return args


def normalize_date_input(value: str) -> date:
    cleaned = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"无法识别日期格式：{value}")


def iter_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def folder_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fixed_decimal(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def trim_decimal(value: float, digits: int = 2) -> str:
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".")


def format_price(value: Any) -> str:
    if value in (None, ""):
        return "-"
    number = float(value)
    if number <= 0 or number >= 1000000000:
        return "-"
    return fixed_decimal(number / 1000, 2)


def format_pct(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return f"{fixed_decimal(float(value), 2)}%"


def format_number(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "-"
    return fixed_decimal(float(value), digits)


def format_amount(value: Any) -> str:
    if value in (None, ""):
        return "-"
    number = float(value)
    abs_number = abs(number)
    if abs_number >= 100000000:
        return f"{trim_decimal(number / 100000000, 2)}亿"
    if abs_number >= 10000:
        return f"{trim_decimal(number / 10000, 2)}万"
    if number.is_integer():
        return str(int(number))
    return trim_decimal(number, 2)


def format_time_value(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return "-"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if number <= 0:
        return "-"
    text = f"{number:06d}"
    return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"


def format_date_value(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return "-"
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        return text
    return f"{text[0:4]}/{text[4:6]}/{text[6:8]}"


def format_zttj(value: Any) -> str:
    if not isinstance(value, dict):
        return "-"
    ct = value.get("ct", 0)
    days = value.get("days", 0)
    return f"{ct}/{days}"


def format_lianban(value: Any) -> str:
    if value in (None, ""):
        return "-"
    number = int(value)
    if number <= 0:
        return "-"
    if number == 1:
        return "首板"
    return f"{number}连板"


def format_day_count(value: Any, unit: str) -> str:
    if value in (None, ""):
        return "-"
    number = int(value)
    if number < 0:
        return "-"
    return f"{number}{unit}"


def format_yes_no(value: Any) -> str:
    return "是" if int(value or 0) == 1 else "否"


def format_reason_qs(value: Any) -> str:
    mapping = {
        1: "60日新高",
        2: "近期多次涨停",
        3: "60日新高且近期多次涨停",
    }
    try:
        return mapping.get(int(value), "-")
    except (TypeError, ValueError):
        return "-"


def escape_md(value: Any) -> str:
    text = "-" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def build_empty_row(column_count: int) -> list[str]:
    return ["-"] * column_count


@dataclass(frozen=True)
class PoolConfig:
    name: str
    endpoint: str
    sort: str
    headers: list[str]
    row_builder: RowBuilder


POOL_CONFIGS: list[PoolConfig] = [
    PoolConfig(
        name="涨停股池",
        endpoint="getTopicZTPool",
        sort="fbt:asc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "成交额",
            "流通市值",
            "总市值",
            "换手率",
            "封板资金",
            "首次封板时间",
            "最后封板时间",
            "炸板次数",
            "涨停统计",
            "连板数",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_pct(row.get("hs")),
            format_amount(row.get("fund")),
            format_time_value(row.get("fbt")),
            format_time_value(row.get("lbt")),
            format_day_count(row.get("zbc"), "次"),
            format_zttj(row.get("zttj")),
            format_lianban(row.get("lbc")),
            row.get("hybk", "-"),
        ],
    ),
    PoolConfig(
        name="昨日涨停股池",
        endpoint="getYesterdayZTPool",
        sort="zs:desc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "涨停价",
            "成交额",
            "流通市值",
            "总市值",
            "换手率",
            "涨速",
            "振幅",
            "昨日封板时间",
            "涨停统计",
            "昨日连板数",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_price(row.get("ztp")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_pct(row.get("hs")),
            format_pct(row.get("zs")),
            format_pct(row.get("zf")),
            format_time_value(row.get("yfbt")),
            format_zttj(row.get("zttj")),
            format_lianban(row.get("ylbc")),
            row.get("hybk", "-"),
        ],
    ),
    PoolConfig(
        name="强势股池",
        endpoint="getTopicQSPool",
        sort="zdp:desc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "涨停价",
            "成交额",
            "流通市值",
            "总市值",
            "换手率",
            "涨速",
            "是否新高",
            "量比",
            "涨停统计",
            "入选理由",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_price(row.get("ztp")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_pct(row.get("hs")),
            format_pct(row.get("zs")),
            format_yes_no(row.get("nh")),
            format_number(row.get("lb")),
            format_zttj(row.get("zttj")),
            format_reason_qs(row.get("cc")),
            row.get("hybk", "-"),
        ],
    ),
    PoolConfig(
        name="次新股池",
        endpoint="getTopicCXPooll",
        sort="ods:asc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "涨停价",
            "成交额",
            "流通市值",
            "总市值",
            "转手率",
            "开板几日",
            "开板日期",
            "上市日期",
            "涨停统计",
            "是否新高",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_price(row.get("ztp")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_pct(row.get("hs")),
            format_day_count(row.get("ods"), "日"),
            format_date_value(row.get("od")),
            format_date_value(row.get("ipod")),
            format_zttj(row.get("zttj")),
            format_yes_no(row.get("nh")),
            row.get("hybk", "-"),
        ],
    ),
    PoolConfig(
        name="炸板股池",
        endpoint="getTopicZBPool",
        sort="fbt:asc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "涨停价",
            "成交额",
            "流通市值",
            "总市值",
            "换手率",
            "涨速",
            "首次封板时间",
            "炸板次数",
            "涨停统计",
            "振幅",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_price(row.get("ztp")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_pct(row.get("hs")),
            format_pct(row.get("zs")),
            format_time_value(row.get("fbt")),
            format_day_count(row.get("zbc"), "次"),
            format_zttj(row.get("zttj")),
            format_pct(row.get("zf")),
            row.get("hybk", "-"),
        ],
    ),
    PoolConfig(
        name="跌停股池",
        endpoint="getTopicDTPool",
        sort="fund:asc",
        headers=[
            "序号",
            "代码",
            "名称",
            "涨跌幅",
            "最新价",
            "成交额",
            "流通市值",
            "总市值",
            "动态市盈率",
            "换手率",
            "封单资金",
            "最后封板时间",
            "板上成交额",
            "连续跌停",
            "开板次数",
            "所属行业",
        ],
        row_builder=lambda row: [
            row["序号"],
            row.get("c", "-"),
            row.get("n", "-"),
            format_pct(row.get("zdp")),
            format_price(row.get("p")),
            format_amount(row.get("amount")),
            format_amount(row.get("ltsz")),
            format_amount(row.get("tshare")),
            format_number(row.get("pe")),
            format_pct(row.get("hs")),
            format_amount(row.get("fund")),
            format_time_value(row.get("lbt")),
            format_amount(row.get("fba")),
            format_day_count(row.get("days"), "天"),
            format_day_count(row.get("oc"), "次"),
            row.get("hybk", "-"),
        ],
    ),
]


def parse_jsonp(text: str) -> dict[str, Any]:
    match = re.match(r"^[^(]+\((.*)\);?\s*$", text, re.S)
    if not match:
        raise RuntimeError(f"接口返回不是合法 JSONP：{text[:200]}")
    return json.loads(match.group(1))


def build_request_headers(cookie: str) -> dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    return headers


def fetch_pool(context: APIRequestContext, config: PoolConfig, trade_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pageindex = 0
    total_count: int | None = None

    while True:
        params = {
            "cb": "callbackdata",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": str(pageindex),
            "pagesize": str(PAGE_SIZE),
            "sort": config.sort,
            "date": trade_date,
            "_": str(int(datetime.now().timestamp() * 1000)),
        }
        response = context.get(f"{API_BASE}/{config.endpoint}", params=params, timeout=REQUEST_TIMEOUT_MS)
        if not response.ok:
            raise RuntimeError(f"{config.name} 请求失败：HTTP {response.status}")

        payload = parse_jsonp(response.text())
        data = payload.get("data") or {}
        if total_count is None:
            total_count = int(data.get("tc") or 0)

        page_rows = data.get("pool") or []
        if not page_rows:
            break

        rows.extend(page_rows)

        if total_count and len(rows) >= total_count:
            break
        if len(page_rows) < PAGE_SIZE:
            break

        pageindex += 1

    if total_count is None:
        return rows
    return rows[:total_count]


def attach_sequence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        merged = dict(row)
        merged["序号"] = index
        enriched.append(merged)
    return enriched


def render_table(headers: list[str], rows: list[dict[str, Any]], builder: RowBuilder) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    if not rows:
        lines.append("| " + " | ".join(build_empty_row(len(headers))) + " |")
        return lines

    for row in rows:
        values = [escape_md(value) for value in builder(row)]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def build_daily_markdown(trade_day: date, pool_data: dict[str, list[dict[str, Any]]]) -> str:
    day_text = folder_date(trade_day)
    counts = {config.name: len(pool_data.get(config.name, [])) for config in POOL_CONFIGS}
    total = sum(counts.values())
    meta = {
        "date": day_text,
        "counts": counts,
        "total": total,
    }

    lines: list[str] = [
        f"# 东方财富短线数据 - {day_text}",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 生成时间：{now_text()}",
        f"> 数据日期：{day_text}",
        "",
        f"{META_PREFIX}{json.dumps(meta, ensure_ascii=False)}{META_SUFFIX}",
        "",
        "## 股池统计",
        "",
        "| 股池 | 条数 |",
        "| --- | ---: |",
    ]

    for config in POOL_CONFIGS:
        lines.append(f"| {config.name} | {counts[config.name]} |")

    lines.extend(
        [
            f"| 合计 | {total} |",
            "",
        ]
    )

    for config in POOL_CONFIGS:
        rows = pool_data.get(config.name, [])
        lines.append(f"## {config.name}（{len(rows)}）")
        lines.append("")
        if not rows:
            lines.append("> 空返回")
            lines.append("")
        lines.extend(render_table(config.headers, attach_sequence(rows), config.row_builder))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig", newline="\n")


def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"<!-- EASTMONEY_SHORTLINE_META (.*?) -->", text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


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

    lines: list[str] = [
        "# 东方财富短线数据汇总",
        "",
        f"> 来源页面：{SOURCE_PAGE}",
        f"> 重建时间：{now_text()}",
        "",
        "| 日期 | 涨停股池 | 昨日涨停股池 | 强势股池 | 次新股池 | 炸板股池 | 跌停股池 | 合计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    if not entries:
        lines.append("| - | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    else:
        names = [config.name for config in POOL_CONFIGS]
        for entry in entries:
            day = entry["date"]
            counts = entry.get("counts", {})
            lines.append(
                "| "
                + f"[{day}](./{day}/{daily_name}) | "
                + " | ".join(str(counts.get(name, 0)) for name in names)
                + f" | {entry.get('total', 0)} |"
            )

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 汇总文件由日期目录内的隐藏元数据自动重建。",
            "- 重跑任意日期后，再次执行脚本会自动刷新该日期及根目录汇总。",
            "",
        ]
    )

    summary_path = output_root / summary_name
    write_text(summary_path, "\n".join(lines))
    return summary_path


def export_for_dates(
    dates: list[date],
    output_root: Path,
    summary_name: str,
    daily_name: str,
    cookie: str,
) -> tuple[list[Path], Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []

    headers = build_request_headers(cookie)
    with sync_playwright() as playwright:
        context = playwright.request.new_context(
            extra_http_headers=headers,
            ignore_https_errors=True,
        )
        try:
            for trade_day in dates:
                day_compact = compact_date(trade_day)
                pool_data: dict[str, list[dict[str, Any]]] = {}
                for config in POOL_CONFIGS:
                    pool_data[config.name] = fetch_pool(context, config, day_compact)

                daily_content = build_daily_markdown(trade_day, pool_data)
                daily_dir = output_root / folder_date(trade_day)
                daily_path = daily_dir / daily_name
                write_text(daily_path, daily_content)
                written_files.append(daily_path)
        finally:
            context.dispose()

    summary_path = rebuild_summary(output_root, summary_name, daily_name)
    return written_files, summary_path


def main() -> int:
    args = parse_args()

    if args.date:
        target_dates = [normalize_date_input(args.date)]
    else:
        start = normalize_date_input(args.start_date)
        end = normalize_date_input(args.end_date)
        if start > end:
            raise SystemExit("开始日期不能大于结束日期")
        target_dates = list(iter_dates(start, end))

    output_root = Path(args.output_root).resolve()
    written_files, summary_path = export_for_dates(
        dates=target_dates,
        output_root=output_root,
        summary_name=args.summary_file,
        daily_name=args.daily_file,
        cookie=args.cookie,
    )

    print(f"输出根目录: {output_root}")
    print(f"生成日文件数量: {len(written_files)}")
    for path in written_files:
        print(path)
    print(f"汇总文件: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
