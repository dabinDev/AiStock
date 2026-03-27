#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SKILL_SCRIPT = ROOT / "eastmoney-shortline-exporter" / "scripts" / "fetch_eastmoney_shortline.py"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 data 目录下的最新东方财富短线数据")
    parser.add_argument("--date", help="指定日期，格式支持 YYYY-MM-DD 或 YYYYMMDD；默认使用北京时间今天")
    return parser.parse_args()


def normalize_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"无法识别日期格式：{value}")


def today_in_shanghai() -> date:
    return datetime.now(SHANGHAI_TZ).date()


def load_skill_module():
    if not SKILL_SCRIPT.exists():
        raise SystemExit(f"未找到 skill 脚本：{SKILL_SCRIPT}")

    module_name = "eastmoney_shortline_exporter_runtime"
    spec = importlib.util.spec_from_file_location(module_name, SKILL_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 skill 脚本：{SKILL_SCRIPT}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def read_meta(path: Path) -> dict | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"<!-- EASTMONEY_SHORTLINE_META (.*?) -->", text)
    if not match:
        return None
    return json.loads(match.group(1))


def rebuild_index(data_dir: Path, summary_name: str, daily_name: str) -> Path:
    trade_dir_rows = []
    for child in sorted(data_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not child.is_dir():
            continue
        daily = child / daily_name
        meta = read_meta(daily)
        total = meta.get("total", 0) if meta else "-"
        daily_rel = daily.relative_to(data_dir).as_posix() if daily.exists() else None
        trade_dir_rows.append((child.name, total, daily_rel))

    index_path = data_dir / "INDEX.md"

    all_paths = []
    for item in sorted(data_dir.rglob("*")):
        if item == index_path:
            continue
        rel = item.relative_to(data_dir).as_posix()
        all_paths.append((item.is_dir(), rel))

    lines = [
        "# data 目录索引",
        "",
        f"> 生成时间：{datetime.now(SHANGHAI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
        "> 本文件覆盖 data 目录当前可见内容，并保留已有历史文件入口。",
        f"> 日期目录数量：{len(trade_dir_rows)}",
        "> 编码：UTF-8 with BOM",
        "",
        "## 主要文件",
        "",
        f"- [东方财富短线汇总](./{summary_name})",
        "- [当前索引](./INDEX.md)",
        "- [涨停最高板明细](./highest_limit_up_board.csv)",
        "- [涨停最高板说明](./highest_limit_up_board.md)",
        "- [涨停最高板折线图](./highest_limit_up_board.png)",
        "",
        "## 日期目录索引",
        "",
        "| 日期目录 | 合计条数 | 日文件 |",
        "| --- | ---: | --- |",
    ]

    for folder_name, total, daily_rel in trade_dir_rows:
        folder_link = f"[./{folder_name}/](./{folder_name}/)"
        daily_link = f"[shortline.md](./{daily_rel})" if daily_rel else "-"
        lines.append(f"| {folder_link} | {total} | {daily_link} |")

    lines.extend(
        [
            "",
            "## 目录内全部现有路径",
            "",
            "| 类型 | 相对路径 |",
            "| --- | --- |",
        ]
    )

    for is_dir, rel in all_paths:
        kind = "目录" if is_dir else "文件"
        suffix = "/" if is_dir else ""
        lines.append(f"| {kind} | [./{rel}{suffix}](./{rel}{suffix}) |")

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig", newline="\n")
    return index_path


def generate_highest_board_artifacts(data_dir: Path) -> tuple[Path, Path, Path]:
    files = sorted(data_dir.glob("20??-??-??/shortline.md"))

    section_title = "## 涨停股池"
    header_board = "连板数"
    header_name = "名称"
    header_code = "代码"
    first_board = "首板"

    rows: list[dict[str, str | int]] = []

    for path in files:
        date_str = path.parent.name
        text = path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()

        start_idx = None
        end_idx = None
        for i, line in enumerate(lines):
            if line.startswith(section_title):
                start_idx = i + 1
                continue
            if start_idx is not None and i > start_idx and line.startswith("## "):
                end_idx = i
                break

        if start_idx is None:
            rows.append({"date": date_str, "highest_board": 0, "leaders": ""})
            continue

        section_lines = lines[start_idx:end_idx] if end_idx is not None else lines[start_idx:]
        table_lines = [line.strip() for line in section_lines if line.strip().startswith("|")]

        if len(table_lines) < 3:
            rows.append({"date": date_str, "highest_board": 0, "leaders": ""})
            continue

        header = [c.strip() for c in table_lines[0].strip("|").split("|")]
        data_lines = [line for line in table_lines[2:] if "---" not in line]

        highest = 0
        leaders: list[str] = []

        for line in data_lines:
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) != len(header):
                continue
            rec = dict(zip(header, cols))
            board_text = rec.get(header_board, "").strip()
            name = rec.get(header_name, "").strip()
            code = rec.get(header_code, "").strip()

            if board_text == first_board:
                board = 1
            else:
                match = re.search(r"(\d+)连板", board_text)
                board = int(match.group(1)) if match else 0

            marker = f"{name}({code})" if (name or code) else ""
            if board > highest:
                highest = board
                leaders = [marker] if marker else []
            elif board == highest and board > 0 and marker and marker not in leaders:
                leaders.append(marker)

        rows.append({"date": date_str, "highest_board": highest, "leaders": "、".join(leaders)})

    csv_path = data_dir / "highest_limit_up_board.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "highest_board", "leaders"])
        writer.writeheader()
        writer.writerows(rows)

    font_candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Zen Hei",
    ]
    available_fonts = {f.name for f in font_manager.fontManager.ttflist}
    selected_font = next((f for f in font_candidates if f in available_fonts), "DejaVu Sans")
    plt.rcParams["font.sans-serif"] = [selected_font, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    x = [datetime.strptime(str(r["date"]), "%Y-%m-%d") for r in rows]
    y = [int(r["highest_board"]) for r in rows]
    leaders = [str(r["leaders"]) for r in rows]

    fig, ax = plt.subplots(figsize=(16, 7), dpi=150)
    ax.plot(x, y, color="#d62728", linewidth=2.2, marker="o", markersize=4, label="最高板")
    ax.fill_between(x, y, 0, color="#ff9896", alpha=0.18)

    ax.set_title("每日涨停最高板折线图（data 目录全部日文件）", fontsize=16, pad=14)
    ax.set_xlabel("交易日")
    ax.set_ylabel("最高板（连板数）")
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.legend(loc="upper left")

    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    max_y = max(y) if y else 0
    for dt, val, leader in zip(x, y, leaders):
        if val == max_y and val > 0:
            label = f"{dt.strftime('%m-%d')} {val}板"
            if leader:
                label += f"\n{leader[:30]}"
            ax.annotate(label, (dt, val), textcoords="offset points", xytext=(0, 10), ha="center", fontsize=9, color="#8c1d18")

    if x:
        last_dt, last_val = x[-1], y[-1]
        ax.annotate(
            f"最新: {last_dt.strftime('%m-%d')} {last_val}板",
            (last_dt, last_val),
            textcoords="offset points",
            xytext=(10, -18),
            ha="left",
            fontsize=9,
        )

    ax.set_ylim(bottom=0, top=max(max_y + 1, 2))
    fig.tight_layout()

    png_path = data_dir / "highest_limit_up_board.png"
    fig.savefig(png_path, bbox_inches="tight")
    plt.close(fig)

    nonzero = [r for r in rows if int(r["highest_board"]) > 0]
    peak = max(rows, key=lambda r: int(r["highest_board"])) if rows else None
    last = rows[-1] if rows else None

    report_lines = [
        "# 涨停最高板分析",
        "",
        f"> 生成时间：{datetime.now(SHANGHAI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 统计范围：{rows[0]['date'] if rows else '-'} 至 {rows[-1]['date'] if rows else '-'}",
        f"> 样本交易日：{len(rows)}",
        "",
        "## 结论摘要",
        "",
        f"- 非零最高板交易日数量：{len(nonzero)}",
    ]

    if peak:
        report_lines.append(f"- 区间最高板：{peak['highest_board']}板（{peak['date']}）")
        if peak.get("leaders"):
            report_lines.append(f"- 区间最高板龙头：{peak['leaders']}")

    if last:
        report_lines.append(f"- 最新交易日最高板：{last['highest_board']}板（{last['date']}）")
        if last.get("leaders"):
            report_lines.append(f"- 最新最高板股：{last['leaders']}")

    report_lines.extend(
        [
            "",
            "## 文件",
            "",
            f"- 折线图：`{png_path.name}`",
            f"- 明细数据：`{csv_path.name}`",
            "",
            "## 明细预览",
            "",
            "| 日期 | 最高板 | 龙头 |",
            "| --- | ---: | --- |",
        ]
    )

    for row in rows[-15:]:
        report_lines.append(f"| {row['date']} | {row['highest_board']} | {row['leaders'] or '-'} |")

    md_path = data_dir / "highest_limit_up_board.md"
    md_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8-sig", newline="\n")

    return csv_path, png_path, md_path


def main() -> int:
    args = parse_args()
    target_date = normalize_date(args.date) if args.date else today_in_shanghai()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    module = load_skill_module()
    written_files, summary_path = module.export_for_dates(
        dates=[target_date],
        output_root=DATA_DIR,
        summary_name=module.DEFAULT_SUMMARY_FILE,
        daily_name=module.DEFAULT_DAILY_FILE,
        cookie="",
    )

    index_path = rebuild_index(DATA_DIR, module.DEFAULT_SUMMARY_FILE, module.DEFAULT_DAILY_FILE)
    csv_path, png_path, md_path = generate_highest_board_artifacts(DATA_DIR)

    print(f"目标日期: {target_date}")
    print(f"日文件数量: {len(written_files)}")
    for path in written_files:
        print(path)
    print(f"汇总文件: {summary_path}")
    print(f"索引文件: {index_path}")
    print(f"最高板 CSV: {csv_path}")
    print(f"最高板 PNG: {png_path}")
    print(f"最高板说明: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
