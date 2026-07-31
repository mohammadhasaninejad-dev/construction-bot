#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بات گزارش روزانه کارگاه‌های ساختمانی
"""

import logging
import shutil
from io import BytesIO
from datetime import datetime, time as dt_time
from pathlib import Path

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

import config
import database as db
from utils import (
    get_persian_day_name,
    today_gregorian,
    week_range_gregorian,
    format_report_text,
    format_report_summary_line,
    format_stats_text,
    calculate_hours,
    generate_pdf,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Conversation states ----------
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
    # edit
    EDIT_CHOOSE_FIELD,
    EDIT_VALUE,
    # delete confirm
    DELETE_CONFIRM,
    # filter / view
    FILTER_MENU,
    FILTER_PROJECT,
    FILTER_DATE,
    FILTER_SUPERVISOR,
    # user management
    USER_MENU,
    USER_ADD_USERNAME,
    USER_ADD_NAME,
    USER_ADD_ROLE,
    USER_ADD_PROJECTS,
    USER_EDIT_SELECT,
    USER_EDIT_FIELD,
    USER_EDIT_VALUE,
    USER_DELETE_CONFIRM,
    # export
    EXPORT_MENU,
    EXPORT_PDF_MODE,
    EXPORT_DATE_FROM,
    EXPORT_DATE_TO,
    EXPORT_SINGLE_ID,
) = range(33)


# ==================== Helpers ====================

def main_menu_keyboard(role: str, projects: list = None):
    if role == "manager":
        buttons = [
            ["گزارش امروز", "گزارش این هفته"],
            ["آمار و خلاصه", "فیلتر گزارش‌ها"],
            ["خروجی اکسل / PDF", "حذف گزارش"],
            ["ویرایش گزارش", "مدیریت کاربران"],
            ["لاگ فعالیت‌ها", "راهنما"],
        ]
    else:
        buttons = [
            ["ثبت گزارش جدید"],
            ["گزارش‌های من", "ویرایش آخرین گزارش"],
            ["راهنما"],
        ]
        # میانبر سریع برای پروژه‌ها
        if projects:
            for p in projects:
                buttons.append([f"گزارش سریع: {p}"])
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
    # چک کاربر pending از مدیریت داخل بات
    if username:
        promoted = db.promote_pending_user(user.id, username)
        if promoted:
            return promoted
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


async def download_media_file(bot, file_id: str, report_id: int, index: int, media_type: str) -> str:
    """دانلود فایل رسانه روی دیسک و برگرداندن مسیر محلی"""
    try:
        tg_file = await bot.get_file(file_id)
        ext = ".jpg" if media_type == "photo" else ".mp4"
        folder = config.MEDIA_DIR / str(report_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{index}{ext}"
        await tg_file.download_to_drive(str(path))
        return str(path)
    except Exception as e:
        logger.warning(f"media download failed: {e}")
        return None


# ==================== Start / Help / Cancel ====================

@require_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    role_fa = "مدیر" if u["role"] == "manager" else "سرپرست کارگاه"
    await update.message.reply_text(
        f"سلام {u['name']}\nنقش شما: {role_fa}\n\nاز منوی پایین گزینه مورد نظر را انتخاب کنید.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


@require_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    text = (
        "راهنمای بات گزارش کارگاه\n\n"
        "• ثبت گزارش جدید / گزارش سریع\n"
        "• مشاهده و ویرایش گزارش‌های خود\n"
        "• /cancel برای لغو هر عملیات\n"
    )
    if u["role"] == "manager":
        text += (
            "\nامکانات مدیر:\n"
            "• گزارش امروز / این هفته (خلاصه)\n"
            "• آمار، فیلتر، خروجی اکسل و PDF\n"
            "• ویرایش و حذف گزارش (با تأیید)\n"
            "• مدیریت کاربران\n"
            "• لاگ فعالیت‌ها\n"
            "• یادآوری خودکار ساعت ۱۸ برای سرپرست‌ها\n"
        )
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    u = get_or_register_user(update)
    role = u["role"] if u else "supervisor"
    projects = u.get("projects") if u else []
    await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_keyboard(role, projects))
    return ConversationHandler.END


# ==================== New report flow ====================

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
    context.user_data["is_edit"] = False
    buttons = [[p] for p in projects]
    buttons.append(["انصراف"])
    await update.message.reply_text(
        "پروژه را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return CHOOSING_PROJECT


@require_user
async def quick_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE, project: str = None):
    u = context.user_data["db_user"]
    if u["role"] != "supervisor":
        await update.message.reply_text("فقط سرپرست‌ها می‌توانند گزارش ثبت کنند.")
        return ConversationHandler.END
    if not project:
        text = update.message.text or ""
        if text.startswith("گزارش سریع:"):
            project = text.replace("گزارش سریع:", "").strip()
    projects = u.get("projects") or []
    if project not in projects:
        await update.message.reply_text("پروژه نامعتبر است.")
        return ConversationHandler.END
    context.user_data["report"] = {
        "supervisor_id": u["user_id"],
        "supervisor_name": u["name"],
        "project": project,
        "workers": [],
        "media": [],
    }
    context.user_data["is_edit"] = False
    today = today_gregorian()
    await update.message.reply_text(
        f"پروژه: {project}\nتاریخ گزارش را وارد کنید (مثال: {today})\nیا کلمه امروز را بفرستید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REPORT_DATE


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
    rid = report.get("id", "جدید")
    preview = {
        "id": rid,
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
    is_edit = context.user_data.get("is_edit", False)
    u = context.user_data["db_user"]

    if is_edit and report.get("id"):
        report_id = report["id"]
        # دانلود رسانه‌های جدید
        saved_media = []
        for i, m in enumerate(media_list):
            local = await download_media_file(context.bot, m["file_id"], report_id, i, m["type"])
            saved_media.append({**m, "local_path": local})
        db.update_report(report_id, report, saved_media if media_list else None)
        db.log_activity(u["user_id"], u["name"], "edit_report", "report", report_id,
                        f"پروژه {report['project']} تاریخ {report['report_date']}")
        msg = f"گزارش #{report_id} با موفقیت ویرایش شد."
    else:
        report_id = db.save_report(report, [])  # اول بدون رسانه برای گرفتن id
        saved_media = []
        for i, m in enumerate(media_list):
            local = await download_media_file(context.bot, m["file_id"], report_id, i, m["type"])
            saved_media.append({**m, "local_path": local})
        if saved_media:
            db.update_report(report_id, report, saved_media)
        db.log_activity(u["user_id"], u["name"], "create_report", "report", report_id,
                        f"پروژه {report['project']} تاریخ {report['report_date']}")
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
        msg = f"گزارش با شماره #{report_id} با موفقیت ثبت شد."

    await update.message.reply_text(msg, reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    context.user_data.clear()
    return ConversationHandler.END


# ==================== Edit report ====================

@require_user
async def edit_last_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سرپرست: ویرایش آخرین گزارش همان روز برای یکی از پروژه‌هایش"""
    u = context.user_data["db_user"]
    if u["role"] != "supervisor":
        await update.message.reply_text("این گزینه برای سرپرست است. مدیر از «ویرایش گزارش» استفاده کند.")
        return ConversationHandler.END
    today = today_gregorian()
    projects = u.get("projects") or []
    found = None
    for p in projects:
        r = db.get_last_report_for_day_project(u["user_id"], p, today)
        if r:
            found = r
            break
    if not found:
        # آخرین گزارش کلی سرپرست
        reports = db.get_reports(supervisor_id=u["user_id"], limit=1)
        if not reports:
            await update.message.reply_text("گزارشی برای ویرایش یافت نشد.")
            return ConversationHandler.END
        found = reports[0]
        await update.message.reply_text(
            f"گزارش امروز پیدا نشد. آخرین گزارش شما (#{found['id']} - {found['report_date']}) بارگذاری شد."
        )
    return await _start_edit_report(update, context, found)


# state for manager entering report id to edit
EDIT_REPORT_ID = 32  # must match range if used; handled in conv

@require_user
async def edit_report_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    await update.message.reply_text(
        "شماره گزارش را برای ویرایش وارد کنید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return 32  # EDIT_REPORT_ID


async def edit_report_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("شماره معتبر وارد کنید.")
        return 32
    report = db.get_report(rid)
    if not report:
        u = context.user_data.get("db_user") or get_or_register_user(update)
        await update.message.reply_text(
            "گزارش پیدا نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects") if u else None),
        )
        return ConversationHandler.END
    return await _start_edit_report(update, context, report)


async def _start_edit_report(update: Update, context: ContextTypes.DEFAULT_TYPE, report: dict):
    media = db.get_report_media(report["id"])
    context.user_data["report"] = {
        "id": report["id"],
        "supervisor_id": report["supervisor_id"],
        "supervisor_name": report.get("supervisor_name"),
        "project": report["project"],
        "report_date": report["report_date"],
        "day_name": report.get("day_name"),
        "workers": report.get("workers") or [],
        "work_report": report.get("work_report"),
        "materials_in": report.get("materials_in"),
        "materials_out": report.get("materials_out"),
        "food_count": report.get("food_count", 0),
        "petty_cash": report.get("petty_cash", 0),
        "petty_cash_reason": report.get("petty_cash_reason"),
        "issues": report.get("issues"),
        "miscellaneous": report.get("miscellaneous"),
        "media": [{"file_id": m["file_id"], "type": m["media_type"], "local_path": m.get("local_path")} for m in media],
    }
    context.user_data["is_edit"] = True
    text = format_report_text(report, include_media_count=False)
    buttons = [
        ["کارگران", "گزارش کار"],
        ["لوازم ورودی", "لوازم خروجی"],
        ["غذا", "تنخواه"],
        ["ایرادات", "متفرقه"],
        ["رسانه", "تاریخ / پروژه"],
        ["ثبت نهایی تغییرات", "انصراف"],
    ]
    await update.message.reply_text(
        text + "\n\nکدام بخش را می‌خواهید ویرایش کنید؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    if text == "ثبت نهایی تغییرات":
        return await show_confirm(update, context)

    field_map = {
        "کارگران": ("workers", "لیست کارگران را وارد کنید (فرمت قبلی):"),
        "گزارش کار": ("work_report", "گزارش کار جدید:"),
        "لوازم ورودی": ("materials_in", "لوازم ورودی:"),
        "لوازم خروجی": ("materials_out", "لوازم خروجی:"),
        "غذا": ("food_count", "تعداد غذا (عدد):"),
        "تنخواه": ("petty_cash", "مبلغ | دلیل (یا ندارد):"),
        "ایرادات": ("issues", "ایرادات:"),
        "متفرقه": ("miscellaneous", "متفرقه:"),
        "رسانه": ("media", None),
        "تاریخ / پروژه": ("date_project", "تاریخ را وارد کنید (YYYY-MM-DD یا امروز):"),
    }
    if text not in field_map:
        await update.message.reply_text("گزینه نامعتبر.")
        return EDIT_CHOOSE_FIELD

    key, prompt = field_map[text]
    context.user_data["edit_field"] = key

    if key == "media":
        context.user_data["report"]["media"] = []
        await update.message.reply_text(
            f"رسانه‌های جدید را بفرستید (قبلی‌ها پاک می‌شوند).\nحداکثر {config.MAX_MEDIA}\nوقتی تمام شد: تمام",
            reply_markup=ReplyKeyboardRemove(),
        )
        return MEDIA

    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove())
    return EDIT_VALUE


async def edit_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("edit_field")
    text = update.message.text.strip()
    report = context.user_data["report"]

    if field == "workers":
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
        report["workers"] = workers
    elif field == "food_count":
        try:
            report["food_count"] = int(text)
        except ValueError:
            await update.message.reply_text("فقط عدد.")
            return EDIT_VALUE
    elif field == "petty_cash":
        if text in ("ندارد", "0", "۰", "-"):
            report["petty_cash"] = 0
            report["petty_cash_reason"] = ""
        else:
            parts = [p.strip() for p in text.replace("،", "|").split("|")]
            try:
                amount = float(parts[0].replace(",", "").replace("٬", ""))
            except ValueError:
                amount = 0
            report["petty_cash"] = amount
            report["petty_cash_reason"] = parts[1] if len(parts) > 1 else text
    elif field == "date_project":
        if text in ("امروز", "today"):
            date_str = today_gregorian()
        else:
            try:
                datetime.strptime(text, "%Y-%m-%d")
                date_str = text
            except ValueError:
                await update.message.reply_text("فرمت تاریخ اشتباه.")
                return EDIT_VALUE
        report["report_date"] = date_str
        report["day_name"] = get_persian_day_name(date_str)
        await update.message.reply_text("نام پروژه جدید را وارد کنید (یا همان پروژه قبلی):")
        context.user_data["edit_field"] = "project_only"
        return EDIT_VALUE
    elif field == "project_only":
        report["project"] = text
    else:
        report[field] = text

    buttons = [
        ["کارگران", "گزارش کار"],
        ["لوازم ورودی", "لوازم خروجی"],
        ["غذا", "تنخواه"],
        ["ایرادات", "متفرقه"],
        ["رسانه", "تاریخ / پروژه"],
        ["ثبت نهایی تغییرات", "انصراف"],
    ]
    await update.message.reply_text(
        "ذخیره شد. بخش دیگری ویرایش شود یا ثبت نهایی؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return EDIT_CHOOSE_FIELD


# ==================== Delete with confirm ====================

@require_user
async def delete_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر می‌تواند حذف کند.")
        return ConversationHandler.END
    await update.message.reply_text(
        "شماره گزارش را برای حذف وارد کنید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["awaiting_delete_id"] = True
    return ConversationHandler.END


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text != "بله حذف شود":
        return await cancel(update, context)
    rid = context.user_data.get("delete_report_id")
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if rid:
        # حذف فایل‌های محلی
        media = db.get_report_media(rid)
        for m in media:
            if m.get("local_path"):
                try:
                    Path(m["local_path"]).unlink(missing_ok=True)
                except Exception:
                    pass
        folder = config.MEDIA_DIR / str(rid)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        db.delete_report(rid)
        db.log_activity(u["user_id"], u["name"], "delete_report", "report", rid, None)
        await update.message.reply_text(
            f"گزارش #{rid} حذف شد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    context.user_data.clear()
    return ConversationHandler.END


# ==================== My / All reports ====================

@require_user
async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    reports = db.get_reports(supervisor_id=u["user_id"], limit=10)
    if not reports:
        await update.message.reply_text("هنوز گزارشی ثبت نکرده‌اید.")
        return
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        "گزارش‌های اخیر شما:\n\n" + "\n".join(lines) +
        "\n\nبرای جزئیات کامل شماره را بفرستید یا از ویرایش استفاده کنید."
    )


@require_user
async def today_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    today = today_gregorian()
    stats = db.get_stats(today, today)
    reported = db.get_reported_projects_on_date(today)
    missing = [p for p in config.PROJECTS if p not in reported]
    text = format_stats_text(stats, f"خلاصه امروز ({today})", missing)
    if stats["reports"]:
        text += "\n\nگزارش‌ها:\n" + "\n".join(format_report_summary_line(r) for r in stats["reports"][:30])
    await update.message.reply_text(text)


@require_user
async def week_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    d_from, d_to = week_range_gregorian()
    stats = db.get_stats(d_from, d_to)
    text = format_stats_text(stats, f"خلاصه این هفته ({d_from} تا {d_to})")
    if stats["reports"]:
        text += "\n\nآخرین گزارش‌ها:\n" + "\n".join(
            format_report_summary_line(r) for r in stats["reports"][:20]
        )
    await update.message.reply_text(text)


@require_user
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    today = today_gregorian()
    d_from, d_to = week_range_gregorian()
    st_today = db.get_stats(today, today)
    st_week = db.get_stats(d_from, d_to)
    reported = db.get_reported_projects_on_date(today)
    missing = [p for p in config.PROJECTS if p not in reported]
    text = format_stats_text(st_today, f"امروز ({today})", missing)
    text += "\n\n" + format_stats_text(st_week, f"این هفته ({d_from} تا {d_to})")
    await update.message.reply_text(text)


# ==================== Filter ====================

@require_user
async def filter_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    buttons = [
        ["فیلتر پروژه", "فیلتر تاریخ"],
        ["فیلتر سرپرست", "انصراف"],
    ]
    await update.message.reply_text(
        "نوع فیلتر را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return FILTER_MENU


async def filter_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    if text == "فیلتر پروژه":
        buttons = [[p] for p in config.PROJECTS] + [["انصراف"]]
        await update.message.reply_text(
            "پروژه را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
        )
        return FILTER_PROJECT
    if text == "فیلتر تاریخ":
        await update.message.reply_text(
            "بازه تاریخ را وارد کنید:\nاز تاریخ تا تاریخ\nمثال: 2026-07-01 2026-07-31\nیا فقط یک تاریخ",
            reply_markup=ReplyKeyboardRemove(),
        )
        return FILTER_DATE
    if text == "فیلتر سرپرست":
        supers = db.get_supervisors()
        if not supers:
            await update.message.reply_text("سرپرستی ثبت نشده.")
            return await cancel(update, context)
        buttons = [[f"{s['name']}|{s['user_id']}"] for s in supers] + [["انصراف"]]
        await update.message.reply_text(
            "سرپرست را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
        )
        return FILTER_SUPERVISOR
    await update.message.reply_text("گزینه نامعتبر.")
    return FILTER_MENU


async def filter_by_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    reports = db.get_reports(project=text, limit=30)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if not reports:
        await update.message.reply_text(
            "گزارشی یافت نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        f"گزارش‌های پروژه {text}:\n\n" + "\n".join(lines),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def filter_by_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        if len(parts) == 1:
            if parts[0] in ("امروز", "today"):
                d = today_gregorian()
            else:
                datetime.strptime(parts[0], "%Y-%m-%d")
                d = parts[0]
            reports = db.get_reports(date_from=d, date_to=d, limit=50)
        else:
            datetime.strptime(parts[0], "%Y-%m-%d")
            datetime.strptime(parts[1], "%Y-%m-%d")
            reports = db.get_reports(date_from=parts[0], date_to=parts[1], limit=50)
    except ValueError:
        await update.message.reply_text("فرمت تاریخ اشتباه.")
        return FILTER_DATE
    if not reports:
        await update.message.reply_text(
            "گزارشی یافت نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        "نتیجه فیلتر تاریخ:\n\n" + "\n".join(lines),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def filter_by_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        sid = int(text.split("|")[-1])
    except ValueError:
        await update.message.reply_text("انتخاب نامعتبر.")
        return FILTER_SUPERVISOR
    reports = db.get_reports(supervisor_id=sid, limit=30)
    if not reports:
        await update.message.reply_text(
            "گزارشی یافت نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        "گزارش‌های سرپرست:\n\n" + "\n".join(lines),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


# ==================== Export ====================

@require_user
async def export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    buttons = [
        ["اکسل همه", "PDF همه (۵۰ تای اخیر)"],
        ["PDF یک گزارش", "PDF بازه تاریخ"],
        ["انصراف"],
    ]
    await update.message.reply_text(
        "نوع خروجی را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return EXPORT_MENU


async def export_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    if text == "اکسل همه":
        await export_excel(update, context)
        return ConversationHandler.END
    if text == "PDF همه (۵۰ تای اخیر)":
        await export_pdf_all(update, context)
        return ConversationHandler.END
    if text == "PDF یک گزارش":
        await update.message.reply_text("شماره گزارش را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return EXPORT_SINGLE_ID
    if text == "PDF بازه تاریخ":
        await update.message.reply_text(
            "از تاریخ را وارد کنید (YYYY-MM-DD یا امروز):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPORT_DATE_FROM
    await update.message.reply_text("گزینه نامعتبر.")
    return EXPORT_MENU


async def export_single_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        rid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("شماره معتبر وارد کنید.")
        return EXPORT_SINGLE_ID
    report = db.get_report(rid)
    if not report:
        await update.message.reply_text(
            "گزارش پیدا نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    buf = generate_pdf([report], title=f"گزارش #{rid}")
    await update.message.reply_document(
        document=buf,
        filename=f"report_{rid}.pdf",
        caption=f"PDF گزارش #{rid}",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def export_date_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in ("امروز", "today"):
        d = today_gregorian()
    else:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            d = text
        except ValueError:
            await update.message.reply_text("فرمت اشتباه.")
            return EXPORT_DATE_FROM
    context.user_data["export_from"] = d
    await update.message.reply_text("تا تاریخ را وارد کنید (YYYY-MM-DD یا امروز):")
    return EXPORT_DATE_TO


async def export_date_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if text in ("امروز", "today"):
        d = today_gregorian()
    else:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            d = text
        except ValueError:
            await update.message.reply_text("فرمت اشتباه.")
            return EXPORT_DATE_TO
    d_from = context.user_data.get("export_from")
    reports = db.get_reports(date_from=d_from, date_to=d, limit=200)
    if not reports:
        await update.message.reply_text(
            "گزارشی در این بازه نیست.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    buf = generate_pdf(reports, title=f"گزارش‌ها {d_from} تا {d}")
    await update.message.reply_document(
        document=buf,
        filename=f"reports_{d_from}_{d}.pdf",
        caption=f"PDF بازه {d_from} تا {d} — {len(reports)} گزارش",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


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
        "شماره", "پروژه", "تاریخ", "روز", "سرپرست", "تعداد کارگر", "ساعات کار",
        "گزارش کار", "لوازم ورودی", "لوازم خروجی", "غذا", "تنخواه",
        "دلیل تنخواه", "ایرادات", "متفرقه",
    ]
    ws.append(headers)
    for r in reports:
        workers = r.get("workers") or []
        hours = sum(float(w.get("hours") or 0) for w in workers)
        ws.append([
            r["id"], r["project"], r["report_date"], r.get("day_name", ""),
            r.get("supervisor_name", ""), len(workers), hours,
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
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


async def export_pdf_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    reports = db.get_reports(limit=50)
    if not reports:
        await update.message.reply_text(
            "گزارشی وجود ندارد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return
    buf = generate_pdf(reports, title="گزارش‌های اخیر کارگاه")
    await update.message.reply_document(
        document=buf,
        filename=f"reports_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        caption="خروجی PDF (تا ۵۰ گزارش اخیر)",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


# ==================== User management ====================

@require_user
async def manage_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    users = db.get_all_users()
    lines = []
    for usr in users:
        role = "مدیر" if usr["role"] == "manager" else "سرپرست"
        projs = "، ".join(usr["projects"]) if usr["projects"] else "—"
        status = "" if usr["user_id"] > 0 else " (در انتظار /start)"
        lines.append(
            f"• {usr['name']} (@{usr.get('username') or '—'}){status}\n"
            f"  نقش: {role} | پروژه‌ها: {projs}\n"
            f"  ID: {usr['user_id']}"
        )
    body = "\n\n".join(lines) if lines else "هنوز کاربری نیست."
    buttons = [
        ["افزودن کاربر", "ویرایش کاربر"],
        ["حذف کاربر", "انصراف"],
    ]
    await update.message.reply_text(
        "لیست کاربران:\n\n" + body + "\n\nعملیات:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return USER_MENU


async def user_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    if text == "افزودن کاربر":
        await update.message.reply_text(
            "یوزرنیم کاربر را بدون @ وارد کنید:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return USER_ADD_USERNAME
    if text == "ویرایش کاربر":
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("کاربری نیست.")
            return await cancel(update, context)
        buttons = [[f"{usr['name']}|{usr['user_id']}"] for usr in users] + [["انصراف"]]
        await update.message.reply_text(
            "کاربر را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
        )
        return USER_EDIT_SELECT
    if text == "حذف کاربر":
        users = [x for x in db.get_all_users() if x["user_id"] != (context.user_data.get("db_user") or {}).get("user_id")]
        if not users:
            await update.message.reply_text("کاربر دیگری برای حذف نیست.")
            return await cancel(update, context)
        buttons = [[f"{usr['name']}|{usr['user_id']}"] for usr in users] + [["انصراف"]]
        await update.message.reply_text(
            "کاربر برای حذف:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
        )
        return USER_DELETE_CONFIRM
    await update.message.reply_text("گزینه نامعتبر.")
    return USER_MENU


async def user_add_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    if not username:
        await update.message.reply_text("یوزرنیم نامعتبر.")
        return USER_ADD_USERNAME
    context.user_data["new_user"] = {"username": username}
    await update.message.reply_text("نام کامل کاربر را وارد کنید:")
    return USER_ADD_NAME


async def user_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_user"]["name"] = update.message.text.strip()
    buttons = [["مدیر", "سرپرست"], ["انصراف"]]
    await update.message.reply_text(
        "نقش را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return USER_ADD_ROLE


async def user_add_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    role = "manager" if text == "مدیر" else "supervisor" if text == "سرپرست" else None
    if not role:
        await update.message.reply_text("نقش نامعتبر.")
        return USER_ADD_ROLE
    context.user_data["new_user"]["role"] = role
    if role == "manager":
        context.user_data["new_user"]["projects"] = []
        return await _finish_add_user(update, context)
    buttons = [[p] for p in config.PROJECTS] + [["تمام", "انصراف"]]
    context.user_data["new_user"]["projects"] = []
    await update.message.reply_text(
        "پروژه‌ها را یکی‌یکی انتخاب کنید. وقتی تمام شد «تمام» بزنید:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return USER_ADD_PROJECTS


async def user_add_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    if text == "تمام":
        return await _finish_add_user(update, context)
    if text in config.PROJECTS:
        projs = context.user_data["new_user"].setdefault("projects", [])
        if text not in projs:
            projs.append(text)
        await update.message.reply_text(f"اضافه شد: {', '.join(projs)}\nپروژه بعدی یا تمام")
        return USER_ADD_PROJECTS
    await update.message.reply_text("پروژه نامعتبر.")
    return USER_ADD_PROJECTS


async def _finish_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nu = context.user_data["new_user"]
    u = context.user_data.get("db_user") or get_or_register_user(update)
    ok = db.add_pending_user(nu["username"], nu["name"], nu["role"], nu.get("projects") or [])
    if not ok:
        await update.message.reply_text(
            "این یوزرنیم از قبل وجود دارد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    else:
        db.log_activity(u["user_id"], u["name"], "add_user", "user", None,
                        f"@{nu['username']} — {nu['name']} — {nu['role']}")
        await update.message.reply_text(
            f"کاربر @{nu['username']} اضافه شد.\n"
            "باید یک‌بار /start بزند تا فعال شود.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    context.user_data.pop("new_user", None)
    return ConversationHandler.END


async def user_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    try:
        uid = int(text.split("|")[-1])
    except ValueError:
        await update.message.reply_text("نامعتبر.")
        return USER_EDIT_SELECT
    target = db.get_user(uid)
    if not target:
        await update.message.reply_text("کاربر پیدا نشد.")
        return await cancel(update, context)
    context.user_data["edit_user_id"] = uid
    buttons = [["نام", "نقش"], ["پروژه‌ها", "انصراف"]]
    await update.message.reply_text(
        f"ویرایش {target['name']}:\nچه چیزی عوض شود؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return USER_EDIT_FIELD


async def user_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    context.user_data["user_edit_field"] = text
    if text == "نام":
        await update.message.reply_text("نام جدید:", reply_markup=ReplyKeyboardRemove())
        return USER_EDIT_VALUE
    if text == "نقش":
        buttons = [["مدیر", "سرپرست"]]
        await update.message.reply_text(
            "نقش جدید:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
        )
        return USER_EDIT_VALUE
    if text == "پروژه‌ها":
        buttons = [[p] for p in config.PROJECTS] + [["تمام", "پاک کردن همه"]]
        context.user_data["temp_projects"] = []
        await update.message.reply_text(
            "پروژه‌ها را انتخاب کنید، سپس تمام:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return USER_EDIT_VALUE
    await update.message.reply_text("نامعتبر.")
    return USER_EDIT_FIELD


async def user_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data["edit_user_id"]
    field = context.user_data.get("user_edit_field")
    u = context.user_data.get("db_user") or get_or_register_user(update)

    if field == "نام":
        db.update_user(uid, name=text)
        db.log_activity(u["user_id"], u["name"], "edit_user", "user", uid, f"name={text}")
        await update.message.reply_text(
            "نام به‌روز شد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    if field == "نقش":
        role = "manager" if text == "مدیر" else "supervisor"
        db.update_user(uid, role=role)
        db.log_activity(u["user_id"], u["name"], "edit_user", "user", uid, f"role={role}")
        await update.message.reply_text(
            "نقش به‌روز شد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
        return ConversationHandler.END
    if field == "پروژه‌ها":
        if text == "پاک کردن همه":
            db.update_user(uid, projects=[])
            db.log_activity(u["user_id"], u["name"], "edit_user", "user", uid, "projects=[]")
            await update.message.reply_text(
                "پروژه‌ها پاک شد.",
                reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
            )
            return ConversationHandler.END
        if text == "تمام":
            projs = context.user_data.get("temp_projects") or []
            db.update_user(uid, projects=projs)
            db.log_activity(u["user_id"], u["name"], "edit_user", "user", uid, f"projects={projs}")
            await update.message.reply_text(
                f"پروژه‌ها: {', '.join(projs) or '—'}",
                reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
            )
            return ConversationHandler.END
        if text in config.PROJECTS:
            projs = context.user_data.setdefault("temp_projects", [])
            if text not in projs:
                projs.append(text)
            await update.message.reply_text(f"فعلی: {', '.join(projs)}\nادامه یا تمام")
            return USER_EDIT_VALUE
        await update.message.reply_text("نامعتبر.")
        return USER_EDIT_VALUE
    return ConversationHandler.END


async def user_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "انصراف":
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        uid = int(text.split("|")[-1])
    except ValueError:
        await update.message.reply_text("نامعتبر.")
        return USER_DELETE_CONFIRM
    if uid == u["user_id"]:
        await update.message.reply_text("نمی‌توانید خودتان را حذف کنید.")
        return await cancel(update, context)
    target = db.get_user(uid)
    if not target:
        await update.message.reply_text("پیدا نشد.")
        return await cancel(update, context)
    db.delete_user(uid)
    db.log_activity(u["user_id"], u["name"], "delete_user", "user", uid, target.get("name"))
    await update.message.reply_text(
        f"کاربر {target['name']} حذف شد.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


# ==================== Activity log ====================

@require_user
async def activity_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    logs = db.get_activity_log(40)
    if not logs:
        await update.message.reply_text("لاگی وجود ندارد.")
        return
    lines = []
    for lg in logs:
        lines.append(
            f"• {lg.get('created_at', '')} | {lg.get('actor_name', '—')}\n"
            f"  {lg.get('action')} {lg.get('target_type') or ''} "
            f"#{lg.get('target_id') or ''} {lg.get('details') or ''}"
        )
    # تلگرام محدودیت طول دارد
    text = "لاگ فعالیت‌ها:\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text)


# ==================== Media group helper ====================

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


# ==================== Fallback text router ====================

@require_user
async def handle_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if not u:
        return
    text = (update.message.text or "").strip()

    # حذف با تأیید
    if context.user_data.get("awaiting_delete_id"):
        context.user_data.pop("awaiting_delete_id", None)
        try:
            rid = int(text)
            report = db.get_report(rid)
            if not report:
                await update.message.reply_text("گزارش پیدا نشد.")
                await update.message.reply_text(
                    "منوی اصلی:", reply_markup=main_menu_keyboard(u["role"], u.get("projects"))
                )
                return
            context.user_data["delete_report_id"] = rid
            preview = format_report_text(report, include_media_count=False)
            buttons = [["بله حذف شود", "انصراف"]]
            await update.message.reply_text(
                preview + "\n\nآیا مطمئن هستید این گزارش حذف شود؟",
                reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
            )
            # وارد state حذف از طریق conversation جدا — با fallback ساده:
            context.user_data["in_delete_confirm"] = True
            return
        except ValueError:
            await update.message.reply_text("شماره معتبر وارد کنید.")
            return

    if context.user_data.get("in_delete_confirm"):
        context.user_data.pop("in_delete_confirm", None)
        if text == "بله حذف شود":
            return await delete_confirm(update, context)
        return await cancel(update, context)

    # منوی اصلی
    if text == "ثبت گزارش جدید":
        return await new_report_start(update, context)
    if text.startswith("گزارش سریع:"):
        return await quick_report_start(update, context)
    if text == "گزارش‌های من":
        return await my_reports(update, context)
    if text == "ویرایش آخرین گزارش":
        return await edit_last_report_start(update, context)
    if text == "ویرایش گزارش":
        return await edit_report_manager_start(update, context)
    if text == "گزارش امروز":
        return await today_summary(update, context)
    if text == "گزارش این هفته":
        return await week_summary(update, context)
    if text == "آمار و خلاصه":
        return await stats_cmd(update, context)
    if text == "فیلتر گزارش‌ها":
        return await filter_menu(update, context)
    if text == "حذف گزارش":
        return await delete_report_start(update, context)
    if text == "خروجی اکسل / PDF":
        return await export_menu(update, context)
    if text == "مدیریت کاربران":
        return await manage_users_menu(update, context)
    if text == "لاگ فعالیت‌ها":
        return await activity_log_cmd(update, context)
    if text == "راهنما":
        return await help_cmd(update, context)

    # اگر فقط عدد بود = نمایش جزئیات گزارش
    if text.isdigit():
        report = db.get_report(int(text))
        if report:
            # دسترسی: مدیر همه، سرپرست فقط خودش
            if u["role"] != "manager" and report["supervisor_id"] != u["user_id"]:
                await update.message.reply_text("دسترسی ندارید.")
                return
            await update.message.reply_text(format_report_text(report))
            media = db.get_report_media(report["id"])
            if media:
                await send_media_group(update, context, media)
            return

    await update.message.reply_text(
        "گزینه نامعتبر. از منو استفاده کنید.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


# ==================== Reminder job ====================

async def daily_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = today_gregorian()
    reported_projects = set(db.get_reported_projects_on_date(today))
    supers = db.get_supervisors()
    for s in supers:
        missing = [p for p in (s.get("projects") or []) if p not in reported_projects]
        # همچنین چک کند آیا خود این سرپرست برای پروژه‌اش گزارش داده
        own_reports = db.get_reports(supervisor_id=s["user_id"], date_from=today, date_to=today, limit=50)
        own_projects = {r["project"] for r in own_reports}
        missing = [p for p in (s.get("projects") or []) if p not in own_projects]
        if missing and s["user_id"] > 0:
            try:
                await context.bot.send_message(
                    s["user_id"],
                    f"⏰ یادآوری: هنوز برای امروز ({today}) گزارش ثبت نکرده‌اید.\n"
                    f"پروژه‌های بدون گزارش: {', '.join(missing)}\n"
                    "از منو «ثبت گزارش جدید» یا گزارش سریع استفاده کنید.",
                )
            except Exception as e:
                logger.warning(f"reminder to {s['user_id']} failed: {e}")


# ==================== Main ====================

def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN در .env تنظیم نشده است.")
    db.init_db()
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .build()
    )

    # Conversation: ثبت / ویرایش گزارش
    conv_report = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^ثبت گزارش جدید$"), new_report_start),
            MessageHandler(filters.Regex("^گزارش سریع:"), quick_report_start),
            MessageHandler(filters.Regex("^ویرایش آخرین گزارش$"), edit_last_report_start),
            CommandHandler("newreport", new_report_start),
            CommandHandler("edit", edit_last_report_start),
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
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    conv_filter = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^فیلتر گزارش‌ها$"), filter_menu)],
        states={
            FILTER_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_menu_choice)],
            FILTER_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_by_project)],
            FILTER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_by_date)],
            FILTER_SUPERVISOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_by_supervisor)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    conv_export = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^خروجی اکسل / PDF$"), export_menu)],
        states={
            EXPORT_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, export_menu_choice)],
            EXPORT_SINGLE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, export_single_id)],
            EXPORT_DATE_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, export_date_from)],
            EXPORT_DATE_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, export_date_to)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    conv_users = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^مدیریت کاربران$"), manage_users_menu)],
        states={
            USER_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_menu_choice)],
            USER_ADD_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_add_username)],
            USER_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_add_name)],
            USER_ADD_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_add_role)],
            USER_ADD_PROJECTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_add_projects)],
            USER_EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_edit_select)],
            USER_EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_edit_field)],
            USER_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_edit_value)],
            USER_DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_delete_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ویرایش گزارش مدیر با شماره — از fallback هم پشتیبانی می‌شود
    conv_edit_mgr = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^ویرایش گزارش$"), edit_report_manager_start),
        ],
        states={
            32: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_report_id_input)],
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_input)],
            MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | (filters.TEXT & ~filters.COMMAND), media_input)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_report)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv_report)
    app.add_handler(conv_edit_mgr)
    app.add_handler(conv_filter)
    app.add_handler(conv_export)
    app.add_handler(conv_users)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_fallback))

    # JobQueue برای یادآوری
    if app.job_queue:
        app.job_queue.run_daily(
            daily_reminder_job,
            time=dt_time(hour=config.REMINDER_HOUR, minute=config.REMINDER_MINUTE, second=0),
            name="daily_report_reminder",
        )
        logger.info(f"Reminder scheduled at {config.REMINDER_HOUR:02d}:{config.REMINDER_MINUTE:02d}")
    else:
        logger.warning("JobQueue در دسترس نیست. برای یادآوری: pip install 'python-telegram-bot[job-queue]'")

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
