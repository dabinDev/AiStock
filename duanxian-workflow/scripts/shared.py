#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUCTION_END = time(9, 30)
CLOSE_TIME = time(15, 0)


def normalize_date(value: str) -> date:
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"无法识别日期格式：{value}")


def now_in_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def today_in_shanghai() -> date:
    return now_in_shanghai().date()


def now_text() -> str:
    return now_in_shanghai().strftime("%Y-%m-%d %H:%M:%S")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig", newline="\n")


def is_sse_trading_day(target_date: date) -> bool:
    try:
        import pandas_market_calendars as mcal

        calendar = mcal.get_calendar("SSE")
        schedule = calendar.schedule(
            start_date=target_date.isoformat(),
            end_date=target_date.isoformat(),
        )
        return not schedule.empty
    except Exception as exc:  # pragma: no cover
        fallback = target_date.weekday() < 5
        print(
            f"警告：未能使用 SSE 交易日历判断（{exc}），回退为工作日判断：{target_date} -> {fallback}",
            file=sys.stderr,
        )
        return fallback


def detect_auto_phase(current_dt: datetime) -> str:
    current_time = current_dt.timetz().replace(tzinfo=None)
    if current_time < AUCTION_END:
        return "auction"
    if current_time < CLOSE_TIME:
        return "intraday"
    return "after-close"
