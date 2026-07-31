import jdatetime
from config import PERSIAN_WEEKDAYS
from typing import List, Dict


def get_persian_day_name(date_str: str = None) -> str:
    """تاریخ به صورت YYYY-MM-DD یا None برای امروز"""
    if date_str:
        y, m, d = map(int, date_str.split("-"))
        jd = jdatetime.date.fromgregorian(year=y, month=m, day=d)
    else:
        jd = jdatetime.date.today()
    return PERSIAN_WEEKDAYS[jd.weekday()]


def today_gregorian() -> str:
    return jdatetime.date.today().togregorian().strftime("%Y-%m-%d")


def format_report_text(report: Dict, include_media_count: bool = True) -> str:
    workers = report.get("workers") or []
    workers_text = ""
    if workers:
        lines = []
        for i, w in enumerate(workers, 1):
            name = w.get("name", "—")
            entry = w.get("entry", "—")
            exit_ = w.get("exit", "—")
            hours = w.get("hours", "—")
            lines.append(f"{i}. {name} | ورود: {entry} | خروج: {exit_} | ساعت: {hours}")
        workers_text = "\n".join(lines)
    else:
        workers_text = "—"

    text = (
        f"📋 گزارش #{report['id']}\n"
        f"🏗 پروژه: {report['project']}\n"
        f"📅 تاریخ: {report['report_date']} ({report.get('day_name', '')})\n"
        f"👷 سرپرست: {report.get('supervisor_name', '—')}\n"
        f"\n👥 کارگران:\n{workers_text}\n"
        f"\n📝 گزارش کار:\n{report.get('work_report') or '—'}\n"
        f"\n📦 لوازم ورودی:\n{report.get('materials_in') or '—'}\n"
        f"📤 لوازم خروجی:\n{report.get('materials_out') or '—'}\n"
        f"\n🍽 تعداد غذا: {report.get('food_count', 0)}\n"
        f"💰 تنخواه: {report.get('petty_cash', 0)} — {report.get('petty_cash_reason') or '—'}\n"
        f"⚠️ ایرادات:\n{report.get('issues') or '—'}\n"
        f"📌 متفرقه:\n{report.get('miscellaneous') or '—'}\n"
    )
    if include_media_count:
        text += f"\n🖼 تعداد رسانه: (در ادامه نمایش داده می‌شود)"
    return text


def calculate_hours(entry: str, exit_: str) -> float:
    """ورود و خروج به صورت HH:MM → ساعت کاری"""
    try:
        eh, em = map(int, entry.split(":"))
        xh, xm = map(int, exit_.split(":"))
        total = (xh * 60 + xm) - (eh * 60 + em)
        if total < 0:
            total += 24 * 60  # شیفت شب
        return round(total / 60, 1)
    except Exception:
        return 8.0
