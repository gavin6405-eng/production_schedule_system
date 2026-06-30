
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Iterable, Sequence
import pandas as pd


@dataclass
class WorkCalendar:
    workday_start: time = time(8, 0)
    workday_end: time = time(17, 0)
    lunch_start: time = time(12, 0)
    lunch_end: time = time(13, 0)
    holidays: set[date] | None = None
    work_weekdays: set[int] | None = None  # Mon=0 ... Sun=6

    def __post_init__(self) -> None:
        self.holidays = self.holidays or set()
        self.work_weekdays = self.work_weekdays or {0, 1, 2, 3, 4}

    @property
    def daily_work_hours(self) -> float:
        morning = (
            datetime.combine(date.today(), self.lunch_start)
            - datetime.combine(date.today(), self.workday_start)
        ).total_seconds() / 3600
        afternoon = (
            datetime.combine(date.today(), self.workday_end)
            - datetime.combine(date.today(), self.lunch_end)
        ).total_seconds() / 3600
        return morning + afternoon

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.work_weekdays and d not in self.holidays

    def previous_workday(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_workday(d):
            d -= timedelta(days=1)
        return d

    def normalize_backward(self, dt: datetime) -> datetime:
        """Move a datetime to the nearest valid working moment at or before dt."""
        d = dt.date()
        if not self.is_workday(d):
            d = self.previous_workday(d + timedelta(days=1))
            return datetime.combine(d, self.workday_end)

        t = dt.time()
        if t > self.workday_end:
            return datetime.combine(d, self.workday_end)
        if self.lunch_start < t < self.lunch_end:
            return datetime.combine(d, self.lunch_start)
        if t <= self.workday_start:
            prev = self.previous_workday(d)
            return datetime.combine(prev, self.workday_end)
        return dt

    def subtract_work_hours(self, end_dt: datetime, hours: float) -> datetime:
        """Subtract working hours while skipping weekends, holidays, and lunch."""
        if pd.isna(hours) or float(hours) <= 0:
            return self.normalize_backward(end_dt)

        remaining = float(hours)
        current = self.normalize_backward(end_dt)

        while remaining > 1e-9:
            d = current.date()
            t = current.time()

            # Work segment: afternoon
            if self.lunch_end <= t <= self.workday_end:
                seg_start = datetime.combine(d, self.lunch_end)
                available = (current - seg_start).total_seconds() / 3600
                if remaining <= available:
                    return current - timedelta(hours=remaining)
                remaining -= available
                current = datetime.combine(d, self.lunch_start)
                continue

            # Work segment: morning
            if self.workday_start < t <= self.lunch_start:
                seg_start = datetime.combine(d, self.workday_start)
                available = (current - seg_start).total_seconds() / 3600
                if remaining <= available:
                    return current - timedelta(hours=remaining)
                remaining -= available
                prev = self.previous_workday(d)
                current = datetime.combine(prev, self.workday_end)
                continue

            # Fallback
            prev = self.previous_workday(d)
            current = datetime.combine(prev, self.workday_end)

        return current


def parse_holidays(holiday_df: pd.DataFrame | None) -> set[date]:
    if holiday_df is None or holiday_df.empty:
        return set()

    date_col = None
    for col in holiday_df.columns:
        if str(col).strip() in {"日期", "假日日期", "Holiday", "Date"}:
            date_col = col
            break
    if date_col is None:
        date_col = holiday_df.columns[0]

    vals = pd.to_datetime(holiday_df[date_col], errors="coerce").dropna()
    return {x.date() for x in vals}


def build_schedule(
    orders: pd.DataFrame,
    process_cfg: pd.DataFrame,
    holidays: set[date],
    workday_start: str = "08:00",
    workday_end: str = "17:00",
    lunch_start: str = "12:00",
    lunch_end: str = "13:00",
    buffer_days: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reverse-schedule each order from customer inbound date.

    Required order columns:
      製令, 客戶入庫日
    Optional:
      客戶, P/N, Type, Category, 組立地點, 組立人員, 組立進度, 備註, 發料日, 入庫日

    Process config columns:
      工序順序, 工序名稱, 標準工時(小時)
    """
    def to_time(s: str) -> time:
        return datetime.strptime(s, "%H:%M").time()

    cal = WorkCalendar(
        workday_start=to_time(workday_start),
        workday_end=to_time(workday_end),
        lunch_start=to_time(lunch_start),
        lunch_end=to_time(lunch_end),
        holidays=holidays,
    )

    orders = orders.copy()
    orders.columns = [str(c).strip() for c in orders.columns]
    process_cfg = process_cfg.copy()
    process_cfg.columns = [str(c).strip() for c in process_cfg.columns]

    required_order = {"製令", "客戶入庫日"}
    missing = required_order - set(orders.columns)
    if missing:
        raise ValueError(f"訂單資料缺少必要欄位：{', '.join(sorted(missing))}")

    required_process = {"工序順序", "工序名稱", "標準工時(小時)"}
    missing_p = required_process - set(process_cfg.columns)
    if missing_p:
        raise ValueError(f"工時設定缺少必要欄位：{', '.join(sorted(missing_p))}")

    process_cfg["工序順序"] = pd.to_numeric(process_cfg["工序順序"], errors="coerce")
    process_cfg["標準工時(小時)"] = pd.to_numeric(process_cfg["標準工時(小時)"], errors="coerce").fillna(0)
    process_cfg = process_cfg.dropna(subset=["工序順序", "工序名稱"]).sort_values("工序順序")

    output_rows = []
    detail_rows = []

    for _, row in orders.iterrows():
        inbound = pd.to_datetime(row.get("客戶入庫日"), errors="coerce")
        if pd.isna(inbound):
            out = row.to_dict()
            out.update({
                "排程狀態": "客戶入庫日格式錯誤",
                "總標準工時(小時)": process_cfg["標準工時(小時)"].sum(),
                "預計發料日": pd.NaT,
                "預計開工時間": pd.NaT,
                "預計完工時間": pd.NaT,
            })
            output_rows.append(out)
            continue

        # Default inbound deadline is end of workday on customer inbound date.
        end_dt = datetime.combine(inbound.date(), cal.workday_end)

        # Additional buffer before customer inbound.
        if float(buffer_days or 0) > 0:
            end_dt = cal.subtract_work_hours(end_dt, float(buffer_days) * cal.daily_work_hours)

        process_end = end_dt
        reverse_detail = []

        for _, p in process_cfg.sort_values("工序順序", ascending=False).iterrows():
            hours = float(p["標準工時(小時)"])
            process_start = cal.subtract_work_hours(process_end, hours)
            reverse_detail.append({
                "製令": row["製令"],
                "客戶": row.get("客戶", ""),
                "P/N": row.get("P/N", ""),
                "Type": row.get("Type", ""),
                "工序順序": int(p["工序順序"]),
                "工序名稱": p["工序名稱"],
                "標準工時(小時)": hours,
                "工序開始": process_start,
                "工序結束": process_end,
                "負責單位": p.get("負責單位", ""),
                "備註": p.get("備註", ""),
            })
            process_end = process_start

        reverse_detail.reverse()
        detail_rows.extend(reverse_detail)

        total_hours = float(process_cfg["標準工時(小時)"].sum())
        start_dt = process_end
        finish_dt = end_dt

        out = row.to_dict()
        out.update({
            "排程狀態": "完成",
            "總標準工時(小時)": total_hours,
            "換算工作天": round(total_hours / cal.daily_work_hours, 2) if cal.daily_work_hours else 0,
            "預計發料日": start_dt.date(),
            "預計開工時間": start_dt,
            "預計完工時間": finish_dt,
            "客戶入庫日_確認": inbound.date(),
        })
        output_rows.append(out)

    summary = pd.DataFrame(output_rows)
    details = pd.DataFrame(detail_rows)

    # Stable column ordering
    preferred = [
        "製令", "客戶", "P/N", "Type", "Category", "組立地點", "組立人員",
        "組立進度", "備註", "發料日", "入庫日", "客戶入庫日",
        "排程狀態", "總標準工時(小時)", "換算工作天",
        "預計發料日", "預計開工時間", "預計完工時間", "客戶入庫日_確認"
    ]
    summary = summary[[c for c in preferred if c in summary.columns] +
                      [c for c in summary.columns if c not in preferred]]
    return summary, details


def build_daily_loading(detail_df: pd.DataFrame, holidays: set[date]) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame(columns=["日期", "工時負荷(小時)", "製令數"])

    rows = []
    for _, r in detail_df.iterrows():
        start = pd.to_datetime(r["工序開始"])
        end = pd.to_datetime(r["工序結束"])
        current = start.date()
        while current <= end.date():
            if current.weekday() < 5 and current not in holidays:
                day_start = max(start, datetime.combine(current, time(8, 0)))
                day_end = min(end, datetime.combine(current, time(17, 0)))
                lunch_s = datetime.combine(current, time(12, 0))
                lunch_e = datetime.combine(current, time(13, 0))
                hours = max(0, (day_end - day_start).total_seconds() / 3600)
                overlap = max(0, (min(day_end, lunch_e) - max(day_start, lunch_s)).total_seconds() / 3600)
                hours -= overlap
                if hours > 0:
                    rows.append({"日期": current, "工時負荷(小時)": hours, "製令": r["製令"]})
            current += timedelta(days=1)

    if not rows:
        return pd.DataFrame(columns=["日期", "工時負荷(小時)", "製令數"])

    df = pd.DataFrame(rows)
    return (
        df.groupby("日期", as_index=False)
          .agg(**{"工時負荷(小時)": ("工時負荷(小時)", "sum"), "製令數": ("製令", "nunique")})
          .sort_values("日期")
    )
