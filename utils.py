import jdatetime
from config import PERSIAN_WEEKDAYS, PERSIAN_FONT_PATH, PERSIAN_FONT_BOLD_PATH
from typing import List, Dict, Optional
from io import BytesIO
from pathlib import Path


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


def week_range_gregorian() -> tuple:
    """از شنبه تا جمعه هفته جاری (میلادی)"""
    jd = jdatetime.date.today()
    # شنبه = 0
    start = jd - jdatetime.timedelta(days=jd.weekday())
    end = start + jdatetime.timedelta(days=6)
    return (
        start.togregorian().strftime("%Y-%m-%d"),
        end.togregorian().strftime("%Y-%m-%d"),
    )


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


def format_report_summary_line(report: Dict) -> str:
    workers = report.get("workers") or []
    hours = sum(float(w.get("hours") or 0) for w in workers)
    return (
        f"#{report['id']} | {report['project']} | {report['report_date']} | "
        f"{report.get('supervisor_name', '—')} | "
        f"کارگر:{len(workers)} | ساعت:{hours:.0f} | "
        f"غذا:{report.get('food_count', 0)} | تنخواه:{report.get('petty_cash', 0)}"
    )


def format_stats_text(stats: Dict, title: str, missing_projects: Optional[List[str]] = None) -> str:
    lines = [
        f"📊 {title}",
        f"تعداد گزارش: {stats['count']}",
        f"مجموع ساعات کار: {stats['total_hours']}",
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
    """ورود و خروج به صورت HH:MM → ساعت کاری"""
    try:
        eh, em = map(int, entry.split(":"))
        xh, xm = map(int, exit_.split(":"))
        total = (xh * 60 + xm) - (eh * 60 + em)
        if total < 0:
            total += 24 * 60
        return round(total / 60, 1)
    except Exception:
        return 8.0


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
    """تولید PDF مرتب با پشتیبانی فونت فارسی در صورت وجود فایل فونت"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    font_r, font_b = _register_persian_fonts()
    has_persian = font_r == "Vazir"

    def draw_rtl(text, x_right, y, font=font_r, size=10):
        """رسم متن؛ اگر فونت فارسی نباشد از چپ می‌نویسد"""
        c.setFont(font, size)
        if has_persian:
            # reportlab با فونت TTF فارسی بهتر است از راست رسم شود
            text_w = c.stringWidth(str(text), font, size)
            c.drawString(x_right - text_w, y, str(text))
        else:
            c.drawString(2 * cm, y, str(text)[:100])

    margin_r = width - 2 * cm
    y = height - 2 * cm

    # عنوان
    c.setFont(font_b, 14)
    if has_persian:
        tw = c.stringWidth(title, font_b, 14)
        c.drawString((width - tw) / 2, y, title)
    else:
        c.drawCentredString(width / 2, y, title)
    y -= 1.2 * cm

    for r in reports:
        if y < 5 * cm:
            c.showPage()
            y = height - 2 * cm

        # هدر گزارش
        header = f"گزارش #{r['id']} | {r['project']} | {r['report_date']} ({r.get('day_name', '')})"
        draw_rtl(header, margin_r, y, font_b, 11)
        y -= 0.55 * cm
        draw_rtl(f"سرپرست: {r.get('supervisor_name', '—')}", margin_r, y, font_r, 9)
        y -= 0.5 * cm

        workers = r.get("workers") or []
        if workers:
            draw_rtl("کارگران:", margin_r, y, font_b, 9)
            y -= 0.4 * cm
            for w in workers:
                line = f"• {w.get('name', '—')} | ورود {w.get('entry', '—')} | خروج {w.get('exit', '—')} | {w.get('hours', '—')} ساعت"
                draw_rtl(line, margin_r, y, font_r, 8)
                y -= 0.35 * cm
                if y < 3 * cm:
                    c.showPage()
                    y = height - 2 * cm

        def field(label, value):
            nonlocal y
            if y < 3.5 * cm:
                c.showPage()
                y = height - 2 * cm
            draw_rtl(f"{label}:", margin_r, y, font_b, 9)
            y -= 0.35 * cm
            val = (value or "—").replace("\n", " | ")
            # شکستن متن طولانی
            max_chars = 80
            while val:
                chunk = val[:max_chars]
                val = val[max_chars:]
                draw_rtl(chunk, margin_r, y, font_r, 8)
                y -= 0.35 * cm
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

        y -= 0.4 * cm
        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.line(2 * cm, y, width - 2 * cm, y)
        y -= 0.6 * cm

    if not has_persian:
        c.showPage()
        c.setFont("Helvetica", 9)
        c.drawString(2 * cm, height - 2 * cm,
                     "Note: Place Vazirmatn-Regular.ttf in fonts/ folder for Persian text.")

    c.save()
    buffer.seek(0)
    return buffer
