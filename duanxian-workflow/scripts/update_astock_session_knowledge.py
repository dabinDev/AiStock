#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from shared import ROOT, detect_auto_phase, is_sse_trading_day, normalize_date, now_in_shanghai, today_in_shanghai


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "data"
MAX_VISIBLE_JINGJIA_HISTORY_DAYS = 4

JINGJIA_SCRIPT = ROOT / "duanxian-jingjia-exporter" / "scripts" / "fetch_jingjia.py"
YIDONG_POOL_SCRIPT = ROOT / "duanxian-yidong-pool" / "scripts" / "fetch_yidong_pool.py"
SHORTLINE_SCRIPT = ROOT / "eastmoney-shortline" / "scripts" / "fetch_eastmoney_shortline.py"
JJYD_SCRIPT = SKILL_ROOT / "scripts" / "fetch_jjyd.py"


@dataclass(frozen=True)
class TaskSpec:
    name: str
    label: str
    live_only: bool


TASKS = {
    "jingjia": TaskSpec(name="jingjia", label="5日竞价", live_only=False),
    "jjyd": TaskSpec(name="jjyd", label="竞价异动/竞价抢筹", live_only=True),
    "yidong": TaskSpec(name="yidong", label="盘中异动播报", live_only=True),
    "pool": TaskSpec(name="pool", label="连板天梯/涨停股池", live_only=True),
    "jinji": TaskSpec(name="jinji", label="近期涨停快照", live_only=True),
    "shortline": TaskSpec(name="shortline", label="盘后东方财富短线股池", live_only=False),
}

PHASE_TASKS = {
    "auction": ["jingjia", "jjyd"],
    "intraday": ["jingjia", "jjyd", "yidong"],
    "after-close": ["pool", "jinji", "shortline"],
    "full": ["jingjia", "jjyd", "yidong", "pool", "jinji", "shortline"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按盘前、盘中、盘后自动更新 A 股时段知识库")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="输出根目录，默认仓库 data 目录")
    parser.add_argument("--date", help="目标日期，支持 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--start-date", help="历史区间开始日期，支持 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--end-date", help="历史区间结束日期，支持 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument(
        "--phase",
        choices=["auto", "auction", "intraday", "after-close", "full"],
        default="auto",
        help="执行阶段，默认 auto",
    )
    parser.add_argument("--cookie", default="", help="东方财富可选 Cookie，透传给 shortline 导出")
    parser.add_argument(
        "--skip-if-non-trading-day",
        action="store_true",
        help="单日时若不是 A 股交易日则跳过；区间时会过滤非交易日",
    )
    args = parser.parse_args()

    has_single_date = bool(args.date)
    has_range = bool(args.start_date or args.end_date)

    if has_single_date and has_range:
        parser.error("--date 与 --start-date/--end-date 不能同时使用")

    if bool(args.start_date) ^ bool(args.end_date):
        parser.error("区间模式必须同时提供 --start-date 和 --end-date")

    if has_range and args.phase in {"auction", "intraday"}:
        parser.error("历史区间模式不支持 auction 或 intraday，只支持 auto、after-close、full")

    return args


def resolve_target_dates(args: argparse.Namespace, today: date) -> list[date]:
    if args.date:
        target = normalize_date(args.date)
        return [target]

    if args.start_date and args.end_date:
        start = normalize_date(args.start_date)
        end = normalize_date(args.end_date)
        if start > end:
            raise SystemExit(f"开始日期不能晚于结束日期：{start} > {end}")
        dates: list[date] = []
        current = start
        while current <= end:
            dates.append(current)
            current = current.fromordinal(current.toordinal() + 1)
        return dates

    return [today]


def filter_trading_dates(target_dates: list[date], skip_if_non_trading_day: bool) -> tuple[list[date], list[date]]:
    if len(target_dates) == 1:
        target = target_dates[0]
        if skip_if_non_trading_day and not is_sse_trading_day(target):
            return [], [target]
        return target_dates, []

    kept: list[date] = []
    skipped: list[date] = []
    for target in target_dates:
        if is_sse_trading_day(target):
            kept.append(target)
        else:
            skipped.append(target)
    return kept, skipped


def resolve_phase(requested_phase: str, primary_date: date, today: date, range_mode: bool) -> str:
    if requested_phase != "auto":
        return requested_phase
    if range_mode:
        return "after-close"
    if primary_date != today:
        return "full"
    return detect_auto_phase(now_in_shanghai())


def ensure_required_scripts() -> None:
    required = [JINGJIA_SCRIPT, YIDONG_POOL_SCRIPT, SHORTLINE_SCRIPT, JJYD_SCRIPT]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("缺少依赖脚本：\n" + "\n".join(missing))


def skip_reason(task: TaskSpec, primary_date: date, today: date, range_mode: bool) -> str | None:
    if task.live_only and primary_date != today:
        return "仅支持当天实时快照"

    if task.name == "jingjia" and primary_date != today:
        delta_days = (today - primary_date).days
        if delta_days > MAX_VISIBLE_JINGJIA_HISTORY_DAYS:
            return f"jjlive 页面通常只保留最近 {MAX_VISIBLE_JINGJIA_HISTORY_DAYS + 1} 个交易日可见列"

    if range_mode and task.name != "shortline" and primary_date != today:
        return "历史区间模式仅批量补采 shortline"

    return None


def build_command(
    task: TaskSpec,
    output_root: Path,
    primary_date: date,
    today: date,
    cookie: str,
    target_dates: list[date],
) -> list[str]:
    command = [sys.executable]

    if task.name == "jingjia":
        command.extend([str(JINGJIA_SCRIPT), "--output-root", str(output_root)])
        if primary_date != today:
            command.extend(["--date", primary_date.isoformat()])
        return command

    if task.name == "jjyd":
        command.extend([str(JJYD_SCRIPT), "--output-root", str(output_root)])
        return command

    if task.name == "yidong":
        command.extend([str(YIDONG_POOL_SCRIPT), "--output-root", str(output_root), "--yidong-only"])
        return command

    if task.name == "pool":
        command.extend(
            [
                str(YIDONG_POOL_SCRIPT),
                "--output-root",
                str(output_root),
                "--pool-only",
                "--pool-tabs",
                "涨停,连板",
            ]
        )
        return command

    if task.name == "jinji":
        command.extend([str(YIDONG_POOL_SCRIPT), "--output-root", str(output_root), "--jinji-only"])
        return command

    if task.name == "shortline":
        command.extend([str(SHORTLINE_SCRIPT), "--output-root", str(output_root)])
        if len(target_dates) == 1:
            command.extend(["--date", target_dates[0].isoformat()])
        else:
            command.extend(["--start-date", target_dates[0].isoformat(), "--end-date", target_dates[-1].isoformat()])
        if cookie:
            command.extend(["--cookie", cookie])
        return command

    raise SystemExit(f"未实现的任务：{task.name}")


def expected_outputs(task: TaskSpec, output_root: Path, primary_date: date, target_dates: list[date]) -> list[Path]:
    if task.name == "shortline":
        return [output_root / trade_day.isoformat() / "shortline.md" for trade_day in target_dates]

    day_dir = output_root / primary_date.isoformat()
    file_map = {
        "jingjia": day_dir / "jingjia.md",
        "jjyd": day_dir / "jjyd.md",
        "yidong": day_dir / "yidong.md",
        "pool": day_dir / "pool.md",
        "jinji": day_dir / "jinji.md",
    }
    return [file_map[task.name]]


def run_command(task: TaskSpec, command: list[str]) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        detail_parts = [part for part in [stdout, stderr] if part]
        detail = "\n".join(detail_parts)
        safe_detail = console_safe(detail, sys.stderr.encoding)
        raise SystemExit(f"{task.label} 执行失败，退出码 {result.returncode}\n{safe_detail}")

    print(f"[OK] {task.label}")


def console_safe(text: str, encoding: str | None) -> str:
    target_encoding = encoding or "utf-8"
    return text.encode(target_encoding, errors="replace").decode(target_encoding, errors="replace")


def main() -> int:
    args = parse_args()
    ensure_required_scripts()

    today = today_in_shanghai()
    requested_dates = resolve_target_dates(args, today)
    if requested_dates[-1] > today:
        raise SystemExit(f"目标日期不能晚于上海今日：{requested_dates[-1]} > {today}")

    target_dates, skipped_non_trading = filter_trading_dates(requested_dates, args.skip_if_non_trading_day)
    if not target_dates:
        print("目标日期均不是 A 股交易日，已跳过。")
        if skipped_non_trading:
            print("跳过日期：" + "、".join(day.isoformat() for day in skipped_non_trading))
        return 0

    primary_date = target_dates[-1]
    range_mode = len(target_dates) > 1
    resolved_phase = resolve_phase(args.phase, primary_date, today, range_mode)
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    print(f"输出根目录：{output_root}")
    if range_mode:
        print(f"目标区间：{target_dates[0]} ~ {target_dates[-1]}")
        print(f"目标交易日数：{len(target_dates)}")
    else:
        print(f"目标日期：{primary_date}")
    if skipped_non_trading:
        print("已过滤非交易日：" + "、".join(day.isoformat() for day in skipped_non_trading))
    print(f"请求阶段：{args.phase}")
    print(f"实际阶段：{resolved_phase}")

    executed: list[str] = []
    skipped: list[str] = []

    for task_name in PHASE_TASKS[resolved_phase]:
        task = TASKS[task_name]
        reason = skip_reason(task, primary_date, today, range_mode)
        if reason:
            message = f"{task.label}：{reason}"
            skipped.append(message)
            print(f"[SKIP] {message}")
            continue

        command = build_command(task, output_root, primary_date, today, args.cookie, target_dates)
        run_command(task, command)
        for path in expected_outputs(task, output_root, primary_date, target_dates):
            print(path)
        if task.name == "jingjia" and primary_date == today:
            print("jingjia 会额外补写 jjlive 当前页可见的最近历史竞价列。")
        executed.append(task.label)

    print("执行完成。")
    if executed:
        print("已执行：" + "、".join(executed))
    if skipped:
        print("已跳过：" + "；".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
