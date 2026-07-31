#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بات گزارش روزانه کارگاه‌های ساختمانی
"""

import logging
from io import BytesIO
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

import config
import database as db
from utils import get_persian_day_name, today_gregorian, format_report_text, calculate_hours

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

(
    CHOOSING_PROJECT,
    REPORT_DATE,
    WORKERS,
    WORK_REPORT,
    MATERIALS_IN,
    MATERIALS_OUT,
    FOOD_COUNT,
    PETTY_CASH,
    ISSUES,
    MISC,
    MEDIA,
    CONFIRM,
) = range(12)


def main_menu_keyboard(role: str):
    if role == "manager":
        buttons = [
            ["مشاهده همه گزارش‌ها", "خروجی اکسل"],
            ["خروجی PDF", "حذف گزارش"],
            ["مدیریت کاربران", "راهنما"],
        ]
    else:
        buttons = [
            ["ثبت گزارش جدید"],
            ["گزارش‌های من"],
            ["راهنما"],
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_or_register_user(update: Update):
    user = update.effective_user
    if not user:
        return None
    existing = db.get_user(user.id)
    if existing:
        if user.username and existing.get("username") != user.username:
            db.upsert_user(user.id, user.username, existing["name"], existing["role"], existing["projects"])
            existing["username"] = user.username
        return existing
    username = (user.username or "").lstrip("@")
    if username in config.INITIAL_USERS:
        info = config.INITIAL_USERS[username]
        db.upsert_user(user.id, username, info["name"], info["role"], info["projects"])
        return db.get_user(user.id)
    return None


def require_user(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = get_or_register_user(update)
        if not u:
            await update.effective_message.reply_text(
                "شما مجاز به استفاده از این بات نیستید.\nبا مدیر سیستم تماس بگیرید."
            )
            return ConversationHandler.END
        context.user_data["db_user"] = u
        return await func(update, context)
    return wrapper


@require_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    role_fa = "مدیر" if u["role"] == "manager" else "سرپرست کارگاه"
    await update.message.reply_text(
        f"سلام {u['name']}\nنقش شما: {role_fa}\n\nاز منوی پایین گزینه مورد نظر را انتخاب کنید.",
        reply_markup=main_menu_keyboard(u["role"]),
    )


@require_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    text = (
        "راهنمای بات گزارش کارگاه\n\n"
        "ثبت گزارش جدید: گزینه ثبت گزارش جدید\n"
        "مشاهده گزارش‌های قبلی خودتان\n\n"
    )
    if u["role"] == "manager":
        text += (
            "امکانات مدیر:\n"
            "مشاهده همه گزارش‌ها\n"
            "خروجی اکسل و PDF\n"
            "حذف گزارش\n"
            "مدیریت کاربران\n"
        )
    text += "\nبرای لغو هر عملیات از دستور /cancel استفاده کنید."
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    u = get_or_register_user(update)
    role = u["role"] if u else "supervisor"
    await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_keyboard(role))
    return ConversationHandler.END


@require_user
async def new_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "supervisor":
        await update.message.reply_text("فقط سرپرست‌ها می‌توانند گزارش ثبت کنند.")
        return ConversationHandler.END
    projects = u.get("projects") or []
    if not projects:
        await update.message.reply_text("هیچ پروژه‌ای به شما اختصاص داده نشده.")
        return ConversationHandler.END
    context.user_data["report"] = {
        "supervisor_id": u["user_id"],
        "supervisor_name": u["name"],
        "workers": [],
        "media": [],
    }
    buttons = [[p] for p in projects]
    buttons.append(["انصراف"])
    await update.message.reply_text(
        "پروژه را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return CHOOSING_PROJECT


async def choose_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    u = context.user_data["db_user"]
    if text not in (u.get("projects") or []):
        await update.message.reply_text("پروژه نامعتبر است.")
        return CHOOSING_PROJECT
    context.user_data["report"]["project"] = text
    today = today_gregorian()
    await update.message.reply_text(
        f"تاریخ گزارش را وارد کنید (مثال: {today})\nیا کلمه امروز را بفرستید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REPORT_DATE


async def report_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in ("امروز", "today"):
        date_str = today_gregorian()
    else:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            date_str = text
        except ValueError:
            await update.message.reply_text("فرمت تاریخ اشتباه است. مثال: 2026-07-31 یا کلمه امروز")
            return REPORT_DATE
    context.user_data["report"]["report_date"] = date_str
    context.user_data["report"]["day_name"] = get_persian_day_name(date_str)
    await update.message.reply_text(
        "لیست کارگران را وارد کنید.\n"
        "هر خط یک کارگر به این فرمت:\n"
        "نام | ساعت ورود | ساعت خروج\n\n"
        "مثال:\n"
        "علی احمدی | 07:30 | 16:00\n"
        "رضا محمدی | 08:00 | 17:00\n\n"
        "اگر کارگری ندارید کلمه ندارد را بفرستید.\n"
        "اگر فقط نام بنویسید پیش‌فرض ۸ ساعت در نظر گرفته می‌شود."
    )
    return WORKERS


async def workers_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    workers = []
    if text not in ("ندارد", "هیچ", "-"):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("،", "|").split("|")]
            name = parts[0] if parts else "نامشخص"
            entry = parts[1] if len(parts) > 1 else "08:00"
            exit_ = parts[2] if len(parts) > 2 else "16:00"
            hours = calculate_hours(entry, exit_)
            workers.append({"name": name, "entry": entry, "exit": exit_, "hours": hours})
    context.user_data["report"]["workers"] = workers
    await update.message.reply_text("گزارش کار امروز را بنویسید (متن آزاد):")
    return WORK_REPORT


async def work_report_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report"]["work_report"] = update.message.text.strip()
    await update.message.reply_text("لوازم ورودی به کارگاه (یا ندارد):")
    return MATERIALS_IN


async def materials_in_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report"]["materials_in"] = update.message.text.strip()
    await update.message.reply_text("لوازم خروجی از کارگاه (یا ندارد):")
    return MATERIALS_OUT


async def materials_out_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report"]["materials_out"] = update.message.text.strip()
    await update.message.reply_text("تعداد غذا را عدد وارد کنید (مثال: 12):")
    return FOOD_COUNT


async def food_count_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text("فقط عدد وارد کنید.")
        return FOOD_COUNT
    context.user_data["report"]["food_count"] = count
    await update.message.reply_text(
        "مبلغ تنخواه مصرفی و دلیل آن را بنویسید.\n"
        "مثال: 1500000 | خرید سیمان و میخ\n"
        "یا ندارد"
    )
    return PETTY_CASH


async def petty_cash_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in ("ندارد", "0", "۰", "-"):
        context.user_data["report"]["petty_cash"] = 0
        context.user_data["report"]["petty_cash_reason"] = ""
    else:
        parts = [p.strip() for p in text.replace("،", "|").split("|")]
        try:
            amount = float(parts[0].replace(",", "").replace("٬", ""))
        except ValueError:
            amount = 0
        reason = parts[1] if len(parts) > 1 else text
        context.user_data["report"]["petty_cash"] = amount
        context.user_data["report"]["petty_cash_reason"] = reason
    await update.message.reply_text("ایرادات (کارفرما یا خودمان) را بنویسید (یا ندارد):")
    return ISSUES


async def issues_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report"]["issues"] = update.message.text.strip()
    await update.message.reply_text("متفرقه را بنویسید (یا ندارد):")
    return MISC


async def misc_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["report"]["miscellaneous"] = update.message.text.strip()
    context.user_data["report"]["media"] = []
    await update.message.reply_text(
        f"حالا عکس یا فیلم بفرستید (حداکثر {config.MAX_MEDIA} عدد).\n"
        "می‌توانید چندتا را با هم بفرستید.\n"
        "وقتی تمام شد کلمه تمام را بفرستید.\n"
        "اگر رسانه‌ای ندارید همین الان تمام بزنید."
    )
    return MEDIA


async def media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = context.user_data["report"]
    media_list = report.setdefault("media", [])
    if update.message.text and update.message.text.strip() in ("تمام", "تمام شد", "پایان", "done"):
        return await show_confirm(update, context)
    if len(media_list) >= config.MAX_MEDIA:
        await update.message.reply_text(f"حداکثر {config.MAX_MEDIA} رسانه مجاز است. کلمه تمام را بفرستید.")
        return MEDIA
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        media_list.append({"file_id": file_id, "type": "photo"})
        await update.message.reply_text(f"عکس ذخیره شد ({len(media_list)}/{config.MAX_MEDIA})\nعکس/فیلم بعدی یا تمام")
    elif update.message.video:
        file_id = update.message.video.file_id
        media_list.append({"file_id": file_id, "type": "video"})
        await update.message.reply_text(f"فیلم ذخیره شد ({len(media_list)}/{config.MAX_MEDIA})\nعکس/فیلم بعدی یا تمام")
    else:
        await update.message.reply_text("لطفا عکس، فیلم یا کلمه تمام بفرستید.")
    return MEDIA


async def show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = context.user_data["report"]
    preview = {
        "id": "جدید",
        "project": report["project"],
        "report_date": report["report_date"],
        "day_name": report["day_name"],
        "supervisor_name": report["supervisor_name"],
        "workers": report["workers"],
        "work_report": report.get("work_report"),
        "materials_in": report.get("materials_in"),
        "materials_out": report.get("materials_out"),
        "food_count": report.get("food_count", 0),
        "petty_cash": report.get("petty_cash", 0),
        "petty_cash_reason": report.get("petty_cash_reason"),
        "issues": report.get("issues"),
        "miscellaneous": report.get("miscellaneous"),
    }
    text = format_report_text(preview, include_media_count=False)
    text += f"\nتعداد رسانه: {len(report.get('media', []))}"
    buttons = [["ثبت نهایی", "انصراف"]]
    await update.message.reply_text(
        text + "\n\nآیا تأیید می‌کنید؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return CONFIRM


async def confirm_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text != "ثبت نهایی":
        return await cancel(update, context)
    report = context.user_data["report"]
    media_list = report.pop("media", [])
    report_id = db.save_report(report, media_list)
    managers = db.get_manager_ids()
    notify_text = (
        f"گزارش جدید ثبت شد\n"
        f"#{report_id} | {report['project']}\n"
        f"سرپرست: {report['supervisor_name']}\n"
        f"تاریخ: {report['report_date']} ({report['day_name']})"
    )
    for mid in managers:
        try:
            await context.bot.send_message(mid, notify_text)
        except Exception as e:
            logger.warning(f"Could not notify manager {mid}: {e}")
    u = context.user_data["db_user"]
    await update.message.reply_text(
        f"گزارش با شماره #{report_id} با موفقیت ثبت شد.",
        reply_markup=main_menu_keyboard(u["role"]),
    )
    context.user_data.clear()
    return ConversationHandler.END


@require_user
async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    reports = db.get_reports(supervisor_id=u["user_id"], limit=15)
    if not reports:
        await update.message.reply_text("هنوز گزارشی ثبت نکرده‌اید.")
        return
    for r in reports:
        text = format_report_text(r)
        await update.message.reply_text(text)
        media = db.get_report_media(r["id"])
        if media:
            await send_media_group(update, context, media)


@require_user
async def all_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر دسترسی دارد.")
        return
    reports = db.get_reports(limit=20)
    if not reports:
        await update.message.reply_text("هیچ گزارشی وجود ندارد.")
        return
    for r in reports:
        text = format_report_text(r)
        await update.message.reply_text(text)
        media = db.get_report_media(r["id"])
        if media:
            await send_media_group(update, context, media)


async def send_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, media_list: list):
    if not media_list:
        return
    media_objs = []
    for m in media_list[:10]:
        if m["media_type"] == "photo":
            media_objs.append(InputMediaPhoto(media=m["file_id"]))
        else:
            media_objs.append(InputMediaVideo(media=m["file_id"]))
    try:
        await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_objs)
    except Exception as e:
        logger.error(f"send_media_group error: {e}")
        for m in media_list:
            try:
                if m["media_type"] == "photo":
                    await context.bot.send_photo(update.effective_chat.id, m["file_id"])
                else:
                    await context.bot.send_video(update.effective_chat.id, m["file_id"])
            except Exception:
                pass


@require_user
async def delete_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر می‌تواند حذف کند.")
        return
    await update.message.reply_text("شماره گزارش را برای حذف وارد کنید (مثال: 12):", reply_markup=ReplyKeyboardRemove())
    context.user_data["awaiting_delete"] = True


@require_user
async def handle_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if not u:
        return
    text = update.message.text.strip()
    if context.user_data.get("awaiting_delete"):
        context.user_data.pop("awaiting_delete", None)
        try:
            rid = int(text)
            report = db.get_report(rid)
            if not report:
                await update.message.reply_text("گزارش پیدا نشد.")
            else:
                db.delete_report(rid)
                await update.message.reply_text(f"گزارش #{rid} حذف شد.")
        except ValueError:
            await update.message.reply_text("شماره معتبر وارد کنید.")
        await update.message.reply_text("منوی اصلی:", reply_markup=main_menu_keyboard(u["role"]))
        return
    if text == "ثبت گزارش جدید":
        return await new_report_start(update, context)
    if text == "گزارش‌های من":
        return await my_reports(update, context)
    if text == "مشاهده همه گزارش‌ها":
        return await all_reports(update, context)
    if text == "حذف گزارش":
        return await delete_report_start(update, context)
    if text == "راهنما":
        return await help_cmd(update, context)
    if text == "خروجی اکسل":
        return await export_excel(update, context)
    if text == "خروجی PDF":
        return await export_pdf(update, context)
    if text == "مدیریت کاربران":
        return await manage_users(update, context)
    await update.message.reply_text("گزینه نامعتبر. از منو استفاده کنید.", reply_markup=main_menu_keyboard(u["role"]))


@require_user
async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    reports = db.get_reports(limit=500)
    if not reports:
        await update.message.reply_text("گزارشی وجود ندارد.")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "گزارش‌ها"
    headers = [
        "شماره", "پروژه", "تاریخ", "روز", "سرپرست", "تعداد کارگر",
        "گزارش کار", "لوازم ورودی", "لوازم خروجی", "غذا", "تنخواه",
        "دلیل تنخواه", "ایرادات", "متفرقه",
    ]
    ws.append(headers)
    for r in reports:
        workers = r.get("workers") or []
        ws.append([
            r["id"], r["project"], r["report_date"], r.get("day_name", ""),
            r.get("supervisor_name", ""), len(workers),
            r.get("work_report", ""), r.get("materials_in", ""), r.get("materials_out", ""),
            r.get("food_count", 0), r.get("petty_cash", 0), r.get("petty_cash_reason", ""),
            r.get("issues", ""), r.get("miscellaneous", ""),
        ])
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    await update.message.reply_document(
        document=buffer,
        filename=f"reports_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        caption="خروجی اکسل گزارش‌ها",
    )


@require_user
async def export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    reports = db.get_reports(limit=50)
    if not reports:
        await update.message.reply_text("گزارشی وجود ندارد.")
        return
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2 * cm
    c.setFont("Helvetica", 14)
    c.drawString(2 * cm, y, "Construction Daily Reports")
    y -= 1 * cm
    c.setFont("Helvetica", 9)
    for r in reports:
        if y < 3 * cm:
            c.showPage()
            y = height - 2 * cm
            c.setFont("Helvetica", 9)
        line = f"#{r['id']} | {r['project']} | {r['report_date']} | {r.get('supervisor_name', '')}"
        c.drawString(2 * cm, y, line[:90])
        y -= 0.5 * cm
        work = (r.get("work_report") or "")[:80]
        c.drawString(2 * cm, y, f"  Work: {work}")
        y -= 0.7 * cm
    c.save()
    buffer.seek(0)
    await update.message.reply_document(
        document=buffer,
        filename=f"reports_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        caption="خروجی PDF (خلاصه)",
    )


@require_user
async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    users = db.get_all_users()
    if not users:
        await update.message.reply_text(
            "هنوز هیچ کاربری ثبت نشده.\nاز سرپرست‌ها بخواهید /start بزنند تا ثبت شوند."
        )
        return
    lines = []
    for usr in users:
        role = "مدیر" if usr["role"] == "manager" else "سرپرست"
        projs = "، ".join(usr["projects"]) if usr["projects"] else "—"
        lines.append(
            f"• {usr['name']} (@{usr.get('username') or '—'})\n"
            f"  نقش: {role} | پروژه‌ها: {projs}\n"
            f"  ID: {usr['user_id']}"
        )
    await update.message.reply_text(
        "لیست کاربران ثبت‌شده:\n\n" + "\n\n".join(lines) +
        "\n\nبرای تغییر پروژه، فایل config.py را ویرایش کنید یا به من بگویید."
    )


def main():
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^ثبت گزارش جدید$"), new_report_start),
            CommandHandler("newreport", new_report_start),
        ],
        states={
            CHOOSING_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_project)],
            REPORT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date)],
            WORKERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, workers_input)],
            WORK_REPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, work_report_input)],
            MATERIALS_IN: [MessageHandler(filters.TEXT & ~filters.COMMAND, materials_in_input)],
            MATERIALS_OUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, materials_out_input)],
            FOOD_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, food_count_input)],
            PETTY_CASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, petty_cash_input)],
            ISSUES: [MessageHandler(filters.TEXT & ~filters.COMMAND, issues_input)],
            MISC: [MessageHandler(filters.TEXT & ~filters.COMMAND, misc_input)],
            MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | (filters.TEXT & ~filters.COMMAND), media_input)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_report)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_fallback))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
