import re
import jdatetime
from config import PERSIAN_WEEKDAYS, PERSIAN_MONTHS, PERSIAN_FONT_PATH, PERSIAN_FONT_BOLD_PATH
from typing import List, Dict, Optional, Tuple
from io import BytesIO
from pathlib import Path


def get_persian_day_name(date_str: str = None) -> str:
    if date_str:
        y, m, d = map(int, date_str.split("-"))
        jd = jdatetime.date.fromgregorian(year=y, month=m, day=d)
    else:
        jd = jdatetime.date.today()
    return PERSIAN_WEEKDAYS[jd.weekday()]


def today_gregorian() -> str:
    return jdatetime.date.today().togregorian().strftime("%Y-%m-%d")


def today_jalali() -> jdatetime.date:
    return jdatetime.date.today()


def week_range_gregorian() -> tuple:
    jd = jdatetime.date.today()
    start = jd - jdatetime.timedelta(days=jd.weekday())
    end = start + jdatetime.timedelta(days=6)
    return (
        start.togregorian().strftime("%Y-%m-%d"),
        end.togregorian().strftime("%Y-%m-%d"),
    )


def jalali_to_gregorian_str(jy: int, jm: int, jd: int) -> str:
    g = jdatetime.date(jy, jm, jd).togregorian()
    return g.strftime("%Y-%m-%d")


def gregorian_to_jalali_display(date_str: str) -> str:
    """2026-07-31 → ۹ مرداد ۱۴۰۵ (نمایش ساده با اعداد لاتین)"""
    try:
        y, m, d = map(int, date_str.split("-"))
        jd = jdatetime.date.fromgregorian(year=y, month=m, day=d)
        month_name = PERSIAN_MONTHS[jd.month - 1]
        return f"{jd.day} {month_name} {jd.year}"
    except Exception:
        return date_str


def parse_jalali_date_text(text: str) -> Optional[str]:
    """
    پارس تاریخ شمسی از متن‌هایی مثل:
    - امروز
    - 7 مرداد 1405
    - ۷ مرداد ۱۴۰۵
    - 1405/5/7
    - 1405-05-07
    برمی‌گرداند: رشته میلادی YYYY-MM-DD یا None
    """
    text = (text or "").strip()
    if text in ("امروز", "today", "اليوم"):
        return today_gregorian()

    # اعداد فارسی → لاتین
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(trans)

    # 1405/5/7 یا 1405-05-07
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", text)
    if m:
        jy, jm, jd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return jalali_to_gregorian_str(jy, jm, jd)
        except Exception:
            return None

    # 7 مرداد 1405
    for i, month_name in enumerate(PERSIAN_MONTHS, start=1):
        if month_name in text:
            parts = text.replace(month_name, " ").split()
            nums = [p for p in parts if p.isdigit()]
            if len(nums) >= 2:
                # معمولاً روز و سال
                day = int(nums[0])
                year = int(nums[1]) if len(nums[1]) == 4 else int(nums[0])
                if len(nums[0]) == 4:
                    year = int(nums[0])
                    day = int(nums[1])
                try:
                    return jalali_to_gregorian_str(year, i, day)
                except Exception:
                    return None
            break

    # میلادی قدیمی هم قبول (سازگاری)
    try:
        from datetime import datetime
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        pass

    return None


def format_report_text(report: Dict, include_media_count: bool = True) -> str:
    workers = report.get("workers") or []
    if workers:
        lines = []
        for i, w in enumerate(workers, 1):
            name = w.get("name", "—")
            entry = w.get("entry", "—")
            exit_ = w.get("exit", "—")
            hours = w.get("hours", "—")
            lines.append(f"{i}. {name} | ورود: {entry} | خروج: {exit_} | کارکرد {hours} ساعت")
        workers_text = "\n".join(lines)
    else:
        workers_text = "—"

    date_disp = report.get("report_date", "")
    jalali = gregorian_to_jalali_display(date_disp) if date_disp else ""

    text = (
        f"📋 گزارش #{report['id']}\n"
        f"🏗 پروژه: {report['project']}\n"
        f"📅 تاریخ: {jalali} ({report.get('day_name', '')})\n"
        f"   ({date_disp})\n"
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


def format_report_summary_line(report: Dict) -> str:
    workers = report.get("workers") or []
    hours = sum(float(w.get("hours") or 0) for w in workers)
    jalali = gregorian_to_jalali_display(report.get("report_date", ""))
    return (
        f"#{report['id']} | {report['project']} | {jalali} | "
        f"{report.get('supervisor_name', '—')} | "
        f"کارگر:{len(workers)} | کارکرد {hours:.0f} ساعت | "
        f"غذا:{report.get('food_count', 0)} | تنخواه:{report.get('petty_cash', 0)}"
    )


def format_stats_text(stats: Dict, title: str, missing_projects: Optional[List[str]] = None) -> str:
    lines = [
        f"📊 {title}",
        f"تعداد گزارش: {stats['count']}",
        f"مجموع کارکرد: {stats['total_hours']} ساعت بوده",
        f"مجموع غذا: {stats['total_food']}",
        f"مجموع تنخواه: {stats['total_petty_cash']:,.0f}",
        f"تعداد سرپرست فعال: {stats['supervisors_count']}",
    ]
    if stats.get("by_project"):
        lines.append("\nبر اساس پروژه:")
        for p, n in sorted(stats["by_project"].items()):
            lines.append(f"  • {p}: {n} گزارش")
    if missing_projects is not None:
        if missing_projects:
            lines.append("\n⚠️ پروژه‌های بدون گزارش امروز:")
            for p in missing_projects:
                lines.append(f"  • {p}")
        else:
            lines.append("\n✅ همه پروژه‌ها امروز گزارش دارند.")
    return "\n".join(lines)


def calculate_hours(entry: str, exit_: str) -> float:
    """ساعت کاری؛ اگر بازه بیش از ۶ ساعت باشد ۱ ساعت ناهار کم می‌شود."""
    try:
        eh, em = map(int, entry.split(":"))
        xh, xm = map(int, exit_.split(":"))
        total = (xh * 60 + xm) - (eh * 60 + em)
        if total < 0:
            total += 24 * 60
        # ناهار ۱ ساعته برای روز کاری معمولی (مثلاً ۸ تا ۱۷ = ۹ ساعت خام → ۸ کارکرد)
        if total > 6 * 60:
            total -= 60
        if total < 0:
            total = 0
        return round(total / 60, 1)
    except Exception:
        return 8.0


def reshape_persian(text: str) -> str:
    """آماده‌سازی متن فارسی برای ReportLab (اتصال حروف + راست‌چین منطقی)"""
    if not text:
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    except Exception:
        return str(text)


def _register_persian_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular = "Helvetica"
    bold = "Helvetica-Bold"
    if PERSIAN_FONT_PATH.exists():
        pdfmetrics.registerFont(TTFont("Vazir", str(PERSIAN_FONT_PATH)))
        regular = "Vazir"
    if PERSIAN_FONT_BOLD_PATH.exists():
        pdfmetrics.registerFont(TTFont("Vazir-Bold", str(PERSIAN_FONT_BOLD_PATH)))
        bold = "Vazir-Bold"
    elif regular == "Vazir":
        bold = "Vazir"
    return regular, bold


def generate_pdf(reports: List[Dict], title: str = "گزارش‌های کارگاه") -> BytesIO:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    font_r, font_b = _register_persian_fonts()
    has_persian = font_r == "Vazir"

    def draw_line(text, y, font=font_r, size=10, bold=False):
        """رسم یک خط فارسی از راست"""
        f = font_b if bold else font
        c.setFont(f, size)
        t = reshape_persian(text) if has_persian else str(text)[:110]
        if has_persian:
            c.drawRightString(width - 1.5 * cm, y, t)
        else:
            c.drawString(1.5 * cm, y, t[:100])

    y = height - 2 * cm
    draw_line(title, y, bold=True, size=14)
    y -= 1.0 * cm

    if not has_persian:
        c.setFont("Helvetica", 8)
        c.drawString(1.5 * cm, y, "Put Vazirmatn-Regular.ttf in fonts/ for Persian PDF")
        y -= 0.6 * cm

    for r in reports:
        if y < 4 * cm:
            c.showPage()
            y = height - 2 * cm

        jalali = gregorian_to_jalali_display(r.get("report_date", ""))
        header = f"گزارش #{r['id']} | {r['project']} | {jalali} ({r.get('day_name', '')})"
        draw_line(header, y, bold=True, size=11)
        y -= 0.5 * cm
        draw_line(f"سرپرست: {r.get('supervisor_name', '—')}", y, size=9)
        y -= 0.45 * cm

        workers = r.get("workers") or []
        if workers:
            draw_line("کارگران:", y, bold=True, size=9)
            y -= 0.38 * cm
            for w in workers:
                line = (
                    f"• {w.get('name', '—')} | ورود {w.get('entry', '—')} | "
                    f"خروج {w.get('exit', '—')} | کارکرد {w.get('hours', '—')} ساعت"
                )
                draw_line(line, y, size=8)
                y -= 0.34 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm

        def field(label, value):
            nonlocal y
            if y < 3.5 * cm:
                c.showPage()
                y = height - 2 * cm
            draw_line(f"{label}:", y, bold=True, size=9)
            y -= 0.34 * cm
            val = (value or "—").replace("\n", " | ")
            # شکستن متن طولانی
            chunk_size = 70
            while val:
                chunk, val = val[:chunk_size], val[chunk_size:]
                draw_line(chunk, y, size=8)
                y -= 0.32 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm

        field("گزارش کار", r.get("work_report"))
        field("لوازم ورودی", r.get("materials_in"))
        field("لوازم خروجی", r.get("materials_out"))
        field("تعداد غذا", str(r.get("food_count", 0)))
        field("تنخواه", f"{r.get('petty_cash', 0)} — {r.get('petty_cash_reason') or ''}")
        field("ایرادات", r.get("issues"))
        field("متفرقه", r.get("miscellaneous"))

        y -= 0.25 * cm
        c.setStrokeColorRGB(0.75, 0.75, 0.75)
        c.line(1.5 * cm, y, width - 1.5 * cm, y)
        y -= 0.55 * cm

    c.save()
    buffer.seek(0)
    return buffer
