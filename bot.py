#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""بات گزارش روزانه کارگاه‌های ساختمانی"""

import logging
import shutil
from io import BytesIO
from datetime import datetime, time as dt_time
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters,
)
from openpyxl import Workbook

import config
import database as db
from utils import (
    get_persian_day_name, today_gregorian, today_jalali, week_range_gregorian,
    format_report_text, format_report_summary_line, format_stats_text,
    calculate_hours, generate_pdf, parse_jalali_date_text, gregorian_to_jalali_display,
)
from config import PERSIAN_MONTHS

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# states
(
    CHOOSING_PROJECT, REPORT_DATE, REPORT_DATE_YEAR, REPORT_DATE_MONTH, REPORT_DATE_DAY,
    WORKERS_SELECT, WORKERS_HOURS, WORK_REPORT, MATERIALS_IN, MATERIALS_OUT,
    FOOD_COUNT, PETTY_CASH, ISSUES, MISC, MEDIA, CONFIRM,
    EDIT_CHOOSE_FIELD, EDIT_VALUE,
    FILTER_MENU, FILTER_PROJECT, FILTER_DATE, FILTER_SUPERVISOR,
    USER_MENU, USER_ADD_USERNAME, USER_ADD_NAME, USER_ADD_ROLE, USER_ADD_PROJECTS,
    USER_EDIT_SELECT, USER_EDIT_FIELD, USER_EDIT_VALUE, USER_DELETE_CONFIRM,
    EXPORT_MENU, EXPORT_SINGLE_ID, EXPORT_DATE_FROM, EXPORT_DATE_TO,
    WORKER_MENU, WORKER_ADD, WORKER_DELETE,
    EDIT_PICK_REPORT,
    PROJECT_MENU, PROJECT_ADD, PROJECT_DELETE,
) = range(42)

BACK = "🔙 بازگشت"
CANCEL_BTN = "❌ انصراف"
DONE_BTN = "✅ تمام"
NONE_BTN = "➖ ندارد"
CONFIRM_SAVE = "✅ ثبت نهایی"
CONFIRM_EDIT = "✏️ ویرایش"
YES_DELETE = "🗑️ بله حذف شود"


def main_menu_keyboard(role: str, projects: list = None):
    if role == "manager":
        buttons = [
            ["📅 گزارش امروز", "📆 گزارش این هفته"],
            ["📊 آمار و خلاصه", "🔍 فیلتر گزارش‌ها"],
            ["📁 خروجی اکسل / PDF", "🗑️ حذف گزارش"],
            ["✏️ ویرایش گزارش", "👷 مدیریت کارگران"],
            ["🏗 مدیریت پروژه‌ها", "👥 مدیریت کاربران"],
            ["📜 لاگ فعالیت‌ها"],
            ["❓ راهنما"],
        ]
    else:
        buttons = [
            ["📝 ثبت گزارش جدید"],
            ["📋 گزارش‌های من", "✏️ ویرایش گزارش"],
            ["❓ راهنما"],
        ]
        if projects:
            for p in projects:
                buttons.append([f"⚡ گزارش سریع: {p}"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def nav_keyboard(extra_rows=None, with_back=True, with_none=False):
    rows = list(extra_rows or [])
    nav = []
    if with_none:
        nav.append(NONE_BTN)
    if with_back:
        nav.append(BACK)
    nav.append(CANCEL_BTN)
    rows.append(nav)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_projects_list():
    """لیست پروژه‌های فعال از دیتابیس (با fallback به config)"""
    try:
        names = db.get_active_projects()
        if names:
            return names
    except Exception:
        pass
    return list(config.PROJECTS)


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


def _is_back(text: str) -> bool:
    return text.strip() in (BACK, "بازگشت", "برگشت")


def _is_cancel(text: str) -> bool:
    return text.strip() in (CANCEL_BTN, "انصراف", "لغو")


# ---------- Start / Help / Cancel ----------

@require_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    role_fa = "مدیر" if u["role"] == "manager" else "سرپرست کارگاه"
    await update.message.reply_text(
        f"سلام {u['name']} 👋\nنقش شما: {role_fa}\n\nاز منوی پایین گزینه مورد نظر را انتخاب کنید.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


@require_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    text = (
        "❓ راهنمای بات گزارش کارگاه\n\n"
        "📝 ثبت گزارش جدید / ⚡ گزارش سریع\n"
        "در هر مرحله می‌توانید 🔙 بازگشت بزنید.\n"
        "تاریخ را شمسی وارد کنید؛ مثلاً: ۷ مرداد ۱۴۰۵ یا «امروز»\n"
        "✏️ ویرایش گزارش‌های قبلی\n"
        "/cancel برای لغو\n"
    )
    if u["role"] == "manager":
        text += (
            "\nامکانات مدیر:\n"
            "📅 گزارش امروز / 📆 این هفته\n"
            "📊 آمار، 🔍 فیلتر، 📁 خروجی\n"
            "👷 مدیریت کارگران\n"
            "🏗 مدیریت پروژه‌ها\n"
            "👥 مدیریت کاربران، 📜 لاگ\n"
            "⏰ یادآوری خودکار ساعت ۱۸\n"
        )
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    u = get_or_register_user(update)
    role = u["role"] if u else "supervisor"
    projects = u.get("projects") if u else []
    await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_keyboard(role, projects))
    return ConversationHandler.END


# ---------- New report flow ----------

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
    await update.message.reply_text(
        "🏗 پروژه را انتخاب کنید:",
        reply_markup=nav_keyboard(buttons, with_back=False),
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
        project = text.replace("⚡ گزارش سریع:", "").replace("گزارش سریع:", "").strip()
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
    return await _ask_report_date(update, context)


async def choose_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data["db_user"]
    if text not in (u.get("projects") or []):
        await update.message.reply_text("پروژه نامعتبر است.")
        return CHOOSING_PROJECT
    context.user_data["report"]["project"] = text
    return await _ask_report_date(update, context)


async def _ask_report_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tj = today_jalali()
    example = f"{tj.day} {PERSIAN_MONTHS[tj.month - 1]} {tj.year}"
    await update.message.reply_text(
        f"📅 تاریخ گزارش را شمسی وارد کنید.\n\n"
        f"مثال‌ها:\n"
        f"• امروز\n"
        f"• {example}\n"
        f"• {tj.year}/{tj.month}/{tj.day}\n\n"
        f"یا مرحله‌ای: اول سال را بفرستید (مثلاً {tj.year})",
        reply_markup=nav_keyboard(),
    )
    return REPORT_DATE


async def report_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        # برگشت به انتخاب پروژه
        u = context.user_data["db_user"]
        projects = u.get("projects") or []
        buttons = [[p] for p in projects]
        await update.message.reply_text("🏗 پروژه را انتخاب کنید:", reply_markup=nav_keyboard(buttons, with_back=False))
        return CHOOSING_PROJECT

    # فقط سال؟
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    t2 = text.translate(trans)
    if t2.isdigit() and len(t2) == 4:
        context.user_data["jalali_year"] = int(t2)
        months = [[m] for m in PERSIAN_MONTHS]
        await update.message.reply_text(
            "📆 ماه را انتخاب کنید:",
            reply_markup=nav_keyboard(months),
        )
        return REPORT_DATE_MONTH

    parsed = parse_jalali_date_text(text)
    if not parsed:
        await update.message.reply_text("فرمت تاریخ اشتباه است. دوباره تلاش کنید یا «امروز» بفرستید.")
        return REPORT_DATE

    context.user_data["report"]["report_date"] = parsed
    context.user_data["report"]["day_name"] = get_persian_day_name(parsed)
    return await _ask_workers(update, context)


async def report_date_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        return await _ask_report_date(update, context)
    if text not in PERSIAN_MONTHS:
        await update.message.reply_text("ماه را از لیست انتخاب کنید.")
        return REPORT_DATE_MONTH
    context.user_data["jalali_month"] = PERSIAN_MONTHS.index(text) + 1
    await update.message.reply_text(
        "📅 روز ماه را عدد بفرستید (مثلاً ۷):",
        reply_markup=nav_keyboard(),
    )
    return REPORT_DATE_DAY


async def report_date_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        months = [[m] for m in PERSIAN_MONTHS]
        await update.message.reply_text("📆 ماه را انتخاب کنید:", reply_markup=nav_keyboard(months))
        return REPORT_DATE_MONTH
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    t2 = text.translate(trans)
    try:
        day = int(t2)
        jy = context.user_data["jalali_year"]
        jm = context.user_data["jalali_month"]
        from utils import jalali_to_gregorian_str
        parsed = jalali_to_gregorian_str(jy, jm, day)
    except Exception:
        await update.message.reply_text("روز نامعتبر است.")
        return REPORT_DATE_DAY
    context.user_data["report"]["report_date"] = parsed
    context.user_data["report"]["day_name"] = get_persian_day_name(parsed)
    return await _ask_workers(update, context)


async def _ask_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    workers = db.get_active_workers()
    context.user_data["worker_catalog"] = workers
    if not workers:
        await update.message.reply_text(
            "👷 هنوز کارگری در سیستم تعریف نشده.\n"
            "می‌توانید نام‌ها را دستی وارد کنید (هر خط: نام | ورود | خروج)\n"
            "یا «ندارد» بفرستید.\n"
            "مدیر باید از «👷 مدیریت کارگران» لیست را بسازد.",
            reply_markup=nav_keyboard(with_none=True),
        )
        context.user_data["workers_manual"] = True
        return WORKERS_SELECT

    lines = [f"{i}. {w['name']}" for i, w in enumerate(workers, 1)]
    await update.message.reply_text(
        "👷 کارگران را انتخاب کنید.\n"
        "شماره‌ها را با فاصله یا ویرگول بفرستید.\n"
        f"مثال: 1 3 5\n\n" + "\n".join(lines) +
        "\n\nاگر کسی نیست: ندارد\n"
        "برای ورود دستی نام‌ها: دستی",
        reply_markup=nav_keyboard(with_none=True),
    )
    context.user_data["workers_manual"] = False
    return WORKERS_SELECT


async def workers_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        return await _ask_report_date(update, context)

    if text in ("ندارد", "هیچ", "-"):
        context.user_data["report"]["workers"] = []
        await update.message.reply_text("📝 گزارش کار امروز را بنویسید:", reply_markup=nav_keyboard())
        return WORK_REPORT

    if text == NONE_BTN or text in ("ندارد", "هیچ", "-"):
        context.user_data["report"]["workers"] = []
        await update.message.reply_text("📝 گزارش کار امروز را بنویسید:", reply_markup=nav_keyboard())
        return WORK_REPORT

    if text == "دستی" or context.user_data.get("workers_manual"):
        if text == "دستی":
            await update.message.reply_text(
                "هر خط یک کارگر:\nنام | ورود | خروج\nمثال:\nعلی | 08:00 | 17:00",
                reply_markup=nav_keyboard(),
            )
            context.user_data["workers_manual"] = True
            return WORKERS_SELECT
        # parse manual
        workers = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("،", "|").split("|")]
            name = parts[0] if parts else "نامشخص"
            entry = parts[1] if len(parts) > 1 else "08:00"
            exit_ = parts[2] if len(parts) > 2 else "17:00"
            hours = calculate_hours(entry, exit_)
            workers.append({"name": name, "entry": entry, "exit": exit_, "hours": hours})
        context.user_data["report"]["workers"] = workers
        await update.message.reply_text("📝 گزارش کار امروز را بنویسید:", reply_markup=nav_keyboard())
        return WORK_REPORT

    # selection by numbers
    catalog = context.user_data.get("worker_catalog") or db.get_active_workers()
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    raw = text.translate(trans).replace("،", " ").replace(",", " ")
    try:
        indices = [int(x) for x in raw.split() if x.isdigit()]
    except ValueError:
        await update.message.reply_text("شماره‌ها را درست وارد کنید.")
        return WORKERS_SELECT
    selected = []
    for idx in indices:
        if 1 <= idx <= len(catalog):
            selected.append(catalog[idx - 1]["name"])
    if not selected:
        await update.message.reply_text("هیچ کارگری انتخاب نشد.")
        return WORKERS_SELECT

    context.user_data["selected_worker_names"] = selected
    await update.message.reply_text(
        f"انتخاب‌شده: {', '.join(selected)}\n\n"
        "ساعات را بفرستید:\n"
        "• کلمه «پیش‌فرض» = ورود ۰۸:۰۰ خروج ۱۶:۰۰ برای همه\n"
        "• یا هر خط: نام | ورود | خروج\n"
        "• یا یک خط برای همه: 07:30 | 16:00",
        reply_markup=nav_keyboard(),
    )
    return WORKERS_HOURS


async def workers_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        return await _ask_workers(update, context)

    names = context.user_data.get("selected_worker_names") or []
    workers = []
    if text in ("پیش‌فرض", "default", "پیشفرض"):
        for name in names:
            workers.append({"name": name, "entry": "08:00", "exit": "17:00", "hours": 8.0})
    elif "|" in text or "|" in text.replace("،", "|"):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) == 1 and lines[0].count("|") == 1:
            # یک ساعت برای همه
            parts = [p.strip() for p in lines[0].replace("،", "|").split("|")]
            entry, exit_ = parts[0], parts[1]
            hours = calculate_hours(entry, exit_)
            for name in names:
                workers.append({"name": name, "entry": entry, "exit": exit_, "hours": hours})
        else:
            # هر خط جدا
            by_name = {}
            for line in lines:
                parts = [p.strip() for p in line.replace("،", "|").split("|")]
                if len(parts) >= 3:
                    by_name[parts[0]] = (parts[1], parts[2])
                elif len(parts) == 2:
                    # بدون نام — اعمال به ترتیب
                    pass
            for name in names:
                if name in by_name:
                    entry, exit_ = by_name[name]
                else:
                    entry, exit_ = "08:00", "17:00"
                workers.append({"name": name, "entry": entry, "exit": exit_, "hours": calculate_hours(entry, exit_)})
    else:
        await update.message.reply_text("فرمت نامعتبر. «پیش‌فرض» یا 08:00 | 17:00 بفرستید.")
        return WORKERS_HOURS

    context.user_data["report"]["workers"] = workers
    await update.message.reply_text("📝 گزارش کار امروز را بنویسید:", reply_markup=nav_keyboard())
    return WORK_REPORT


async def work_report_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        return await _ask_workers(update, context)
    context.user_data["report"]["work_report"] = text
    await update.message.reply_text("📦 لوازم ورودی به کارگاه:", reply_markup=nav_keyboard(with_none=True))
    return MATERIALS_IN


async def materials_in_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text("📝 گزارش کار امروز را بنویسید:", reply_markup=nav_keyboard())
        return WORK_REPORT
    if text == NONE_BTN:
        text = "ندارد"
    context.user_data["report"]["materials_in"] = text
    await update.message.reply_text("📤 لوازم خروجی از کارگاه:", reply_markup=nav_keyboard(with_none=True))
    return MATERIALS_OUT


async def materials_out_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text("📦 لوازم ورودی:", reply_markup=nav_keyboard(with_none=True))
        return MATERIALS_IN
    if text == NONE_BTN:
        text = "ندارد"
    context.user_data["report"]["materials_out"] = text
    await update.message.reply_text("🍽 تعداد غذا را عدد وارد کنید:", reply_markup=nav_keyboard())
    return FOOD_COUNT


async def food_count_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text("📤 لوازم خروجی:", reply_markup=nav_keyboard(with_none=True))
        return MATERIALS_OUT
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    try:
        count = int(text.translate(trans))
    except ValueError:
        await update.message.reply_text("فقط عدد وارد کنید.")
        return FOOD_COUNT
    context.user_data["report"]["food_count"] = count
    await update.message.reply_text(
        "💰 مبلغ تنخواه و دلیل:\nمثال: 1500000 | خرید سیمان\n"
        "می‌توانید عکس فاکتور/رسید هم بفرستید، بعد مبلغ را بنویسید.",
        reply_markup=nav_keyboard(with_none=True),
    )
    return PETTY_CASH


async def petty_cash_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # امکان ارسال عکس فاکتور قبل از/همراه مبلغ
    if update.message.photo:
        report = context.user_data["report"]
        media_list = report.setdefault("media", [])
        if len(media_list) < config.MAX_MEDIA:
            media_list.append({"file_id": update.message.photo[-1].file_id, "type": "photo"})
            await update.message.reply_text(
                f"🖼 عکس تنخواه/فاکتور ذخیره شد ({len(media_list)}/{config.MAX_MEDIA}).\n"
                "مبلغ و دلیل را بنویسید یا دکمه ندارد را بزنید.",
                reply_markup=nav_keyboard(with_none=True),
            )
        else:
            await update.message.reply_text("ظرفیت رسانه پر است. مبلغ را بنویسید یا ندارد.")
        return PETTY_CASH

    text = (update.message.text or "").strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text("🍽 تعداد غذا:", reply_markup=nav_keyboard())
        return FOOD_COUNT
    if text == NONE_BTN or text in ("ندارد", "0", "۰", "-"):
        context.user_data["report"]["petty_cash"] = 0
        context.user_data["report"]["petty_cash_reason"] = ""
    else:
        parts = [p.strip() for p in text.replace("،", "|").split("|")]
        try:
            amount = float(parts[0].replace(",", "").replace("٬", ""))
        except ValueError:
            amount = 0
        context.user_data["report"]["petty_cash"] = amount
        context.user_data["report"]["petty_cash_reason"] = parts[1] if len(parts) > 1 else text
    await update.message.reply_text("⚠️ ایرادات:", reply_markup=nav_keyboard(with_none=True))
    return ISSUES


async def issues_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text(
            "💰 مبلغ تنخواه و دلیل:",
            reply_markup=nav_keyboard(with_none=True),
        )
        return PETTY_CASH
    if text == NONE_BTN:
        text = "ندارد"
    context.user_data["report"]["issues"] = text
    await update.message.reply_text("📌 متفرقه:", reply_markup=nav_keyboard(with_none=True))
    return MISC


async def misc_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        await update.message.reply_text("⚠️ ایرادات:", reply_markup=nav_keyboard(with_none=True))
        return ISSUES
    if text == NONE_BTN:
        text = "ندارد"
    context.user_data["report"]["miscellaneous"] = text
    context.user_data["report"]["media"] = []
    await update.message.reply_text(
        f"🖼 عکس یا فیلم بفرستید (حداکثر {config.MAX_MEDIA}).\n"
        f"وقتی تمام شد «{DONE_BTN}» یا کلمه تمام را بفرستید.",
        reply_markup=nav_keyboard([[DONE_BTN]]),
    )
    return MEDIA


async def media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = context.user_data["report"]
    media_list = report.setdefault("media", [])
    if update.message.text:
        text = update.message.text.strip()
        if _is_cancel(text):
            return await cancel(update, context)
        if _is_back(text):
            await update.message.reply_text("📌 متفرقه:", reply_markup=nav_keyboard(with_none=True))
            return MISC
        if text in (DONE_BTN, "تمام", "تمام شد", "پایان", "done"):
            return await show_confirm(update, context)
    if len(media_list) >= config.MAX_MEDIA:
        await update.message.reply_text(f"حداکثر {config.MAX_MEDIA} رسانه. «تمام» را بفرستید.")
        return MEDIA
    if update.message.photo:
        media_list.append({"file_id": update.message.photo[-1].file_id, "type": "photo"})
        await update.message.reply_text(f"عکس ذخیره شد ({len(media_list)}/{config.MAX_MEDIA})")
    elif update.message.video:
        media_list.append({"file_id": update.message.video.file_id, "type": "video"})
        await update.message.reply_text(f"فیلم ذخیره شد ({len(media_list)}/{config.MAX_MEDIA})")
    else:
        await update.message.reply_text("عکس، فیلم یا تمام بفرستید.")
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
    text += f"\n🖼 تعداد رسانه: {len(report.get('media', []))}"
    buttons = [[CONFIRM_SAVE], [CONFIRM_EDIT], [CANCEL_BTN]]
    await update.message.reply_text(
        text + "\n\nآیا تأیید می‌کنید؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return CONFIRM


async def confirm_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text) or text == "انصراف":
        return await cancel(update, context)
    if text in (CONFIRM_EDIT, "ویرایش"):
        return await _show_edit_fields_menu(update, context)
    if text not in (CONFIRM_SAVE, "ثبت نهایی"):
        await update.message.reply_text("یکی از گزینه‌ها را انتخاب کنید.")
        return CONFIRM

    report = context.user_data["report"]
    media_list = report.pop("media", [])
    is_edit = context.user_data.get("is_edit", False)
    u = context.user_data["db_user"]

    if is_edit and report.get("id"):
        report_id = report["id"]
        saved_media = []
        for i, m in enumerate(media_list):
            local = await download_media_file(context.bot, m["file_id"], report_id, i, m["type"])
            saved_media.append({**m, "local_path": local})
        db.update_report(report_id, report, saved_media if media_list else None)
        db.log_activity(u["user_id"], u["name"], "edit_report", "report", report_id,
                        f"{report['project']} {report['report_date']}")
        msg = f"✅ گزارش #{report_id} ویرایش شد."
    else:
        report_id = db.save_report(report, [])
        saved_media = []
        for i, m in enumerate(media_list):
            local = await download_media_file(context.bot, m["file_id"], report_id, i, m["type"])
            saved_media.append({**m, "local_path": local})
        if saved_media:
            db.update_report(report_id, report, saved_media)
        db.log_activity(u["user_id"], u["name"], "create_report", "report", report_id,
                        f"{report['project']} {report['report_date']}")
        for mid in db.get_manager_ids():
            try:
                await context.bot.send_message(
                    mid,
                    f"🆕 گزارش جدید #{report_id} | {report['project']}\n"
                    f"سرپرست: {report['supervisor_name']}\n"
                    f"تاریخ: {gregorian_to_jalali_display(report['report_date'])}",
                )
            except Exception as e:
                logger.warning(f"notify manager failed: {e}")
        msg = f"✅ گزارش #{report_id} ثبت شد."

    await update.message.reply_text(msg, reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    context.user_data.clear()
    return ConversationHandler.END


async def _show_edit_fields_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        ["👷 کارگران", "📝 گزارش کار"],
        ["📦 لوازم ورودی", "📤 لوازم خروجی"],
        ["🍽 غذا", "💰 تنخواه"],
        ["⚠️ ایرادات", "📌 متفرقه"],
        ["🖼 رسانه", "📅 تاریخ / پروژه"],
        [CONFIRM_SAVE, CANCEL_BTN],
    ]
    await update.message.reply_text(
        "کدام بخش را ویرایش می‌کنید؟",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return EDIT_CHOOSE_FIELD


# ---------- Edit existing reports ----------

@require_user
async def edit_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سرپرست: همه گزارش‌های خودش | مدیر: با شماره"""
    u = context.user_data["db_user"]
    if u["role"] == "manager":
        await update.message.reply_text(
            "شماره گزارش را برای ویرایش وارد کنید:",
            reply_markup=nav_keyboard(with_back=False),
        )
        return EDIT_PICK_REPORT

    reports = db.get_reports(supervisor_id=u["user_id"], limit=25)
    if not reports:
        await update.message.reply_text("گزارشی برای ویرایش ندارید.")
        return ConversationHandler.END
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        "گزارش‌های شما — شماره را برای ویرایش بفرستید:\n\n" + "\n".join(lines),
        reply_markup=nav_keyboard(with_back=False),
    )
    return EDIT_PICK_REPORT


async def edit_pick_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    try:
        rid = int(text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    except ValueError:
        await update.message.reply_text("شماره معتبر وارد کنید.")
        return EDIT_PICK_REPORT
    report = db.get_report(rid)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if not report:
        await update.message.reply_text("گزارش پیدا نشد.")
        return EDIT_PICK_REPORT
    if u["role"] != "manager" and report["supervisor_id"] != u["user_id"]:
        await update.message.reply_text("دسترسی ندارید.")
        return EDIT_PICK_REPORT
    return await _load_report_for_edit(update, context, report)


async def _load_report_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, report: dict):
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
        "media": [
            {"file_id": m["file_id"], "type": m["media_type"], "local_path": m.get("local_path")}
            for m in media
        ],
    }
    context.user_data["is_edit"] = True
    await update.message.reply_text(format_report_text(report, include_media_count=False))
    return await _show_edit_fields_menu(update, context)


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if text in (CONFIRM_SAVE, "ثبت نهایی"):
        return await show_confirm(update, context)

    field_map = {
        "👷 کارگران": "workers",
        "کارگران": "workers",
        "📝 گزارش کار": "work_report",
        "گزارش کار": "work_report",
        "📦 لوازم ورودی": "materials_in",
        "لوازم ورودی": "materials_in",
        "📤 لوازم خروجی": "materials_out",
        "لوازم خروجی": "materials_out",
        "🍽 غذا": "food_count",
        "غذا": "food_count",
        "💰 تنخواه": "petty_cash",
        "تنخواه": "petty_cash",
        "⚠️ ایرادات": "issues",
        "ایرادات": "issues",
        "📌 متفرقه": "miscellaneous",
        "متفرقه": "miscellaneous",
        "🖼 رسانه": "media",
        "رسانه": "media",
        "📅 تاریخ / پروژه": "date_project",
        "تاریخ / پروژه": "date_project",
    }
    if text not in field_map:
        await update.message.reply_text("گزینه نامعتبر.")
        return EDIT_CHOOSE_FIELD
    key = field_map[text]
    context.user_data["edit_field"] = key
    if key == "media":
        context.user_data["report"]["media"] = []
        await update.message.reply_text(
            f"رسانه‌های جدید (قبلی پاک می‌شود). حداکثر {config.MAX_MEDIA}. سپس تمام",
            reply_markup=nav_keyboard([[DONE_BTN]]),
        )
        return MEDIA
    if key == "workers":
        return await _ask_workers(update, context)
    prompts = {
        "work_report": "گزارش کار جدید:",
        "materials_in": "لوازم ورودی:",
        "materials_out": "لوازم خروجی:",
        "food_count": "تعداد غذا:",
        "petty_cash": "مبلغ | دلیل یا ندارد:",
        "issues": "ایرادات:",
        "miscellaneous": "متفرقه:",
        "date_project": "تاریخ شمسی جدید (مثلاً ۷ مرداد ۱۴۰۵ یا امروز):",
    }
    await update.message.reply_text(prompts.get(key, "مقدار جدید:"), reply_markup=nav_keyboard())
    return EDIT_VALUE


async def edit_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if _is_back(text):
        return await _show_edit_fields_menu(update, context)
    field = context.user_data.get("edit_field")
    report = context.user_data["report"]

    if field == "food_count":
        try:
            report["food_count"] = int(text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
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
        parsed = parse_jalali_date_text(text)
        if not parsed:
            await update.message.reply_text("تاریخ نامعتبر.")
            return EDIT_VALUE
        report["report_date"] = parsed
        report["day_name"] = get_persian_day_name(parsed)
        await update.message.reply_text("نام پروژه جدید (یا همان قبلی):", reply_markup=nav_keyboard())
        context.user_data["edit_field"] = "project_only"
        return EDIT_VALUE
    elif field == "project_only":
        report["project"] = text
    else:
        report[field] = text

    await update.message.reply_text("ذخیره موقت شد.")
    return await _show_edit_fields_menu(update, context)


# ---------- Delete ----------

@require_user
async def delete_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    await update.message.reply_text("شماره گزارش برای حذف:", reply_markup=ReplyKeyboardRemove())
    context.user_data["awaiting_delete_id"] = True
    return ConversationHandler.END


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in (YES_DELETE, "بله حذف شود"):
        return await cancel(update, context)
    rid = context.user_data.get("delete_report_id")
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if rid:
        for m in db.get_report_media(rid):
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


# ---------- Summaries ----------

@require_user
async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    reports = db.get_reports(supervisor_id=u["user_id"], limit=15)
    if not reports:
        await update.message.reply_text("هنوز گزارشی ثبت نکرده‌اید.")
        return
    lines = [format_report_summary_line(r) for r in reports]
    await update.message.reply_text(
        "📋 گزارش‌های اخیر شما:\n\n" + "\n".join(lines) +
        "\n\nبرای جزئیات، شماره گزارش را بفرستید."
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
    missing = [p for p in get_projects_list() if p not in reported]
    text = format_stats_text(stats, f"خلاصه امروز ({gregorian_to_jalali_display(today)})", missing)
    if stats["reports"]:
        text += "\n\n" + "\n".join(format_report_summary_line(r) for r in stats["reports"][:30])
    await update.message.reply_text(text)


@require_user
async def week_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    d_from, d_to = week_range_gregorian()
    stats = db.get_stats(d_from, d_to)
    text = format_stats_text(
        stats,
        f"خلاصه این هفته ({gregorian_to_jalali_display(d_from)} تا {gregorian_to_jalali_display(d_to)})",
    )
    if stats["reports"]:
        text += "\n\n" + "\n".join(format_report_summary_line(r) for r in stats["reports"][:20])
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
    missing = [p for p in get_projects_list() if p not in reported]
    text = format_stats_text(st_today, f"امروز ({gregorian_to_jalali_display(today)})", missing)
    text += "\n\n" + format_stats_text(
        st_week,
        f"این هفته ({gregorian_to_jalali_display(d_from)} تا {gregorian_to_jalali_display(d_to)})",
    )
    await update.message.reply_text(text)


# ---------- Filter ----------

@require_user
async def filter_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    buttons = [["🏗 فیلتر پروژه", "📅 فیلتر تاریخ"], ["👷 فیلتر سرپرست", CANCEL_BTN]]
    await update.message.reply_text("نوع فیلتر:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return FILTER_MENU


async def filter_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if "پروژه" in text:
        buttons = [[p] for p in get_projects_list()] + [[CANCEL_BTN]]
        await update.message.reply_text("پروژه:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
        return FILTER_PROJECT
    if "تاریخ" in text:
        await update.message.reply_text(
            "بازه شمسی یا میلادی:\nمثال: ۷ مرداد ۱۴۰۵\nیا: 1405/5/1 1405/5/31",
            reply_markup=nav_keyboard(with_back=False),
        )
        return FILTER_DATE
    if "سرپرست" in text:
        supers = db.get_supervisors()
        if not supers:
            await update.message.reply_text("سرپرستی نیست.")
            return await cancel(update, context)
        buttons = [[f"{s['name']}|{s['user_id']}"] for s in supers] + [[CANCEL_BTN]]
        await update.message.reply_text("سرپرست:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
        return FILTER_SUPERVISOR
    return FILTER_MENU


async def filter_by_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    reports = db.get_reports(project=text, limit=30)
    if not reports:
        await update.message.reply_text("یافت نشد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return ConversationHandler.END
    await update.message.reply_text(
        "\n".join(format_report_summary_line(r) for r in reports),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def filter_by_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    parts = text.split()
    try:
        if len(parts) == 1:
            d = parse_jalali_date_text(parts[0])
            if not d:
                raise ValueError
            reports = db.get_reports(date_from=d, date_to=d, limit=50)
        else:
            d1 = parse_jalali_date_text(parts[0])
            d2 = parse_jalali_date_text(parts[1])
            if not d1 or not d2:
                # maybe "1405/5/1 1405/5/31"
                d1 = parse_jalali_date_text(parts[0])
                d2 = parse_jalali_date_text(parts[1])
            if not d1 or not d2:
                raise ValueError
            reports = db.get_reports(date_from=d1, date_to=d2, limit=50)
    except Exception:
        await update.message.reply_text("فرمت تاریخ اشتباه.")
        return FILTER_DATE
    if not reports:
        await update.message.reply_text("یافت نشد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return ConversationHandler.END
    await update.message.reply_text(
        "\n".join(format_report_summary_line(r) for r in reports),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def filter_by_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        sid = int(text.split("|")[-1])
    except ValueError:
        await update.message.reply_text("نامعتبر.")
        return FILTER_SUPERVISOR
    reports = db.get_reports(supervisor_id=sid, limit=30)
    if not reports:
        await update.message.reply_text("یافت نشد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return ConversationHandler.END
    await update.message.reply_text(
        "\n".join(format_report_summary_line(r) for r in reports),
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


# ---------- Export ----------

@require_user
async def export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    buttons = [
        ["📊 اکسل همه", "📄 PDF همه"],
        ["📄 PDF یک گزارش", "📄 PDF بازه تاریخ"],
        [CANCEL_BTN],
    ]
    await update.message.reply_text("نوع خروجی:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return EXPORT_MENU


async def export_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if "اکسل" in text:
        await export_excel(update, context)
        return ConversationHandler.END
    if "PDF همه" in text or text == "📄 PDF همه":
        await export_pdf_all(update, context)
        return ConversationHandler.END
    if "یک گزارش" in text:
        await update.message.reply_text("شماره گزارش:", reply_markup=ReplyKeyboardRemove())
        return EXPORT_SINGLE_ID
    if "بازه" in text:
        await update.message.reply_text("از تاریخ شمسی:", reply_markup=ReplyKeyboardRemove())
        return EXPORT_DATE_FROM
    return EXPORT_MENU


async def export_single_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        rid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("شماره معتبر.")
        return EXPORT_SINGLE_ID
    report = db.get_report(rid)
    if not report:
        await update.message.reply_text("پیدا نشد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return ConversationHandler.END
    buf = generate_pdf([report], title=f"گزارش #{rid}")
    await update.message.reply_document(
        document=buf, filename=f"report_{rid}.pdf", caption=f"PDF #{rid}",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


async def export_date_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_jalali_date_text(update.message.text.strip())
    if not d:
        await update.message.reply_text("تاریخ نامعتبر.")
        return EXPORT_DATE_FROM
    context.user_data["export_from"] = d
    await update.message.reply_text("تا تاریخ شمسی:")
    return EXPORT_DATE_TO


async def export_date_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    d = parse_jalali_date_text(update.message.text.strip())
    if not d:
        await update.message.reply_text("تاریخ نامعتبر.")
        return EXPORT_DATE_TO
    d_from = context.user_data.get("export_from")
    reports = db.get_reports(date_from=d_from, date_to=d, limit=200)
    if not reports:
        await update.message.reply_text("خالی.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return ConversationHandler.END
    buf = generate_pdf(reports, title=f"گزارش‌ها {gregorian_to_jalali_display(d_from)} تا {gregorian_to_jalali_display(d)}")
    await update.message.reply_document(
        document=buf,
        filename=f"reports_{d_from}_{d}.pdf",
        caption=f"{len(reports)} گزارش",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


@require_user
async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data["db_user"]
    if u["role"] != "manager":
        return
    reports = db.get_reports(limit=500)
    if not reports:
        await update.message.reply_text("گزارشی نیست.")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "گزارش‌ها"
    ws.append([
        "شماره", "پروژه", "تاریخ میلادی", "تاریخ شمسی", "روز", "سرپرست",
        "تعداد کارگر", "کارکرد (ساعت)", "گزارش کار", "لوازم ورودی", "لوازم خروجی",
        "غذا", "تنخواه", "دلیل تنخواه", "ایرادات", "متفرقه",
    ])
    for r in reports:
        workers = r.get("workers") or []
        hours = sum(float(w.get("hours") or 0) for w in workers)
        ws.append([
            r["id"], r["project"], r["report_date"], gregorian_to_jalali_display(r["report_date"]),
            r.get("day_name", ""), r.get("supervisor_name", ""), len(workers), hours,
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
        caption="خروجی اکسل",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


async def export_pdf_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    reports = db.get_reports(limit=50)
    if not reports:
        await update.message.reply_text("خالی.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
        return
    buf = generate_pdf(reports, title="گزارش‌های اخیر کارگاه")
    await update.message.reply_document(
        document=buf,
        filename=f"reports_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        caption="PDF تا ۵۰ گزارش",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )


# ---------- Worker management ----------

@require_user
async def worker_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    workers = db.get_all_workers()
    if workers:
        lines = [
            f"{'✅' if w['active'] else '⏸️'} #{w['id']} {w['name']}"
            for w in workers
        ]
        body = "\n".join(lines)
    else:
        body = "هنوز کارگری تعریف نشده."
    buttons = [["➕ افزودن کارگر", "🗑️ حذف کارگر"], [CANCEL_BTN]]
    await update.message.reply_text(
        f"👷 لیست کارگران:\n\n{body}\n\nعملیات:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return WORKER_MENU


async def worker_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if "افزودن" in text:
        await update.message.reply_text(
            "نام کارگر را بفرستید (می‌توانید چند نام در خطوط جدا بفرستید):",
            reply_markup=nav_keyboard(with_back=False),
        )
        return WORKER_ADD
    if "حذف" in text:
        workers = db.get_active_workers()
        if not workers:
            await update.message.reply_text("کارگر فعالی نیست.")
            return await cancel(update, context)
        buttons = [[f"#{w['id']} {w['name']}"] for w in workers] + [[CANCEL_BTN]]
        await update.message.reply_text(
            "کدام کارگر حذف/غیرفعال شود؟",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return WORKER_DELETE
    return WORKER_MENU


async def worker_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    added = []
    for line in text.splitlines():
        name = line.strip()
        if name:
            wid = db.add_worker(name)
            if wid:
                added.append(name)
    if added:
        db.log_activity(u["user_id"], u["name"], "add_workers", "worker", None, ", ".join(added))
        await update.message.reply_text(
            f"اضافه شد: {', '.join(added)}",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    else:
        await update.message.reply_text("چیزی اضافه نشد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    return ConversationHandler.END


async def worker_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    import re
    m = re.search(r"#(\d+)", text)
    if not m:
        await update.message.reply_text("از لیست انتخاب کنید.")
        return WORKER_DELETE
    wid = int(m.group(1))
    w = db.get_worker(wid)
    db.deactivate_worker(wid)
    db.log_activity(u["user_id"], u["name"], "deactivate_worker", "worker", wid, w["name"] if w else "")
    await update.message.reply_text(
        f"کارگر #{wid} غیرفعال شد.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


# ---------- User management (compact) ----------

@require_user
async def manage_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    users = db.get_all_users()
    lines = []
    for usr in users:
        role = "مدیر" if usr["role"] == "manager" else "سرپرست"
        projs = "، ".join(usr["projects"]) if usr["projects"] else "—"
        st = "" if usr["user_id"] > 0 else " (منتظر /start)"
        lines.append(f"• {usr['name']} (@{usr.get('username') or '—'}){st}\n  {role} | {projs}\n  ID: {usr['user_id']}")
    body = "\n\n".join(lines) if lines else "خالی"
    buttons = [["➕ افزودن کاربر", "✏️ ویرایش کاربر"], ["🗑️ حذف کاربر", CANCEL_BTN]]
    await update.message.reply_text(
        f"👥 کاربران:\n\n{body}\n\nعملیات:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return USER_MENU


async def user_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if "افزودن" in text:
        await update.message.reply_text("یوزرنیم بدون @:", reply_markup=ReplyKeyboardRemove())
        return USER_ADD_USERNAME
    if "ویرایش" in text:
        users = db.get_all_users()
        buttons = [[f"{u['name']}|{u['user_id']}"] for u in users] + [[CANCEL_BTN]]
        await update.message.reply_text("کاربر:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
        return USER_EDIT_SELECT
    if "حذف" in text:
        me = context.user_data.get("db_user", {})
        users = [x for x in db.get_all_users() if x["user_id"] != me.get("user_id")]
        buttons = [[f"{u['name']}|{u['user_id']}"] for u in users] + [[CANCEL_BTN]]
        await update.message.reply_text("حذف:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
        return USER_DELETE_CONFIRM
    return USER_MENU


async def user_add_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_user"] = {"username": update.message.text.strip().lstrip("@")}
    await update.message.reply_text("نام کامل:")
    return USER_ADD_NAME


async def user_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_user"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "نقش:",
        reply_markup=ReplyKeyboardMarkup([["مدیر", "سرپرست"], [CANCEL_BTN]], resize_keyboard=True),
    )
    return USER_ADD_ROLE


async def user_add_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    role = "manager" if text == "مدیر" else "supervisor"
    context.user_data["new_user"]["role"] = role
    if role == "manager":
        context.user_data["new_user"]["projects"] = []
        return await _finish_add_user(update, context)
    context.user_data["new_user"]["projects"] = []
    buttons = [[p] for p in get_projects_list()] + [[DONE_BTN, CANCEL_BTN]]
    await update.message.reply_text("پروژه‌ها را انتخاب کنید، بعد تمام:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return USER_ADD_PROJECTS


async def user_add_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if text in (DONE_BTN, "تمام"):
        return await _finish_add_user(update, context)
    if text in get_projects_list():
        projs = context.user_data["new_user"].setdefault("projects", [])
        if text not in projs:
            projs.append(text)
        await update.message.reply_text(f"فعلی: {', '.join(projs)}")
        return USER_ADD_PROJECTS
    return USER_ADD_PROJECTS


async def _finish_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nu = context.user_data["new_user"]
    u = context.user_data.get("db_user") or get_or_register_user(update)
    ok = db.add_pending_user(nu["username"], nu["name"], nu["role"], nu.get("projects") or [])
    if ok:
        db.log_activity(u["user_id"], u["name"], "add_user", "user", None, f"@{nu['username']}")
        msg = f"کاربر @{nu['username']} اضافه شد. باید /start بزند."
    else:
        msg = "یوزرنیم تکراری است."
    await update.message.reply_text(msg, reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    return ConversationHandler.END


async def user_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    try:
        uid = int(text.split("|")[-1])
    except ValueError:
        return USER_EDIT_SELECT
    context.user_data["edit_user_id"] = uid
    await update.message.reply_text(
        "چه چیزی؟",
        reply_markup=ReplyKeyboardMarkup([["نام", "نقش"], ["پروژه‌ها", CANCEL_BTN]], resize_keyboard=True),
    )
    return USER_EDIT_FIELD


async def user_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    context.user_data["user_edit_field"] = text
    if text == "نام":
        await update.message.reply_text("نام جدید:", reply_markup=ReplyKeyboardRemove())
        return USER_EDIT_VALUE
    if text == "نقش":
        await update.message.reply_text(
            "نقش:",
            reply_markup=ReplyKeyboardMarkup([["مدیر", "سرپرست"]], resize_keyboard=True),
        )
        return USER_EDIT_VALUE
    if text == "پروژه‌ها":
        context.user_data["temp_projects"] = []
        buttons = [[p] for p in get_projects_list()] + [[DONE_BTN, "پاک کردن همه"]]
        await update.message.reply_text("پروژه‌ها:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
        return USER_EDIT_VALUE
    return USER_EDIT_FIELD


async def user_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data["edit_user_id"]
    field = context.user_data.get("user_edit_field")
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if field == "نام":
        db.update_user(uid, name=text)
    elif field == "نقش":
        db.update_user(uid, role="manager" if text == "مدیر" else "supervisor")
    elif field == "پروژه‌ها":
        if text == "پاک کردن همه":
            db.update_user(uid, projects=[])
        elif text in (DONE_BTN, "تمام"):
            db.update_user(uid, projects=context.user_data.get("temp_projects") or [])
        elif text in get_projects_list():
            projs = context.user_data.setdefault("temp_projects", [])
            if text not in projs:
                projs.append(text)
            await update.message.reply_text(f"فعلی: {', '.join(projs)}")
            return USER_EDIT_VALUE
        else:
            return USER_EDIT_VALUE
    db.log_activity(u["user_id"], u["name"], "edit_user", "user", uid, field)
    await update.message.reply_text("به‌روز شد.", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    return ConversationHandler.END


async def user_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    try:
        uid = int(text.split("|")[-1])
    except ValueError:
        return USER_DELETE_CONFIRM
    target = db.get_user(uid)
    if target:
        db.delete_user(uid)
        db.log_activity(u["user_id"], u["name"], "delete_user", "user", uid, target.get("name"))
        await update.message.reply_text(f"حذف شد: {target['name']}", reply_markup=main_menu_keyboard(u["role"], u.get("projects")))
    return ConversationHandler.END



# ---------- Project management ----------

@require_user
async def project_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return ConversationHandler.END
    projects = db.get_all_projects()
    if projects:
        lines = [
            f"{'✅' if pr['active'] else '⏸️'} #{pr['id']} {pr['name']}"
            for pr in projects
        ]
        body = "\n".join(lines)
    else:
        body = "هنوز پروژه‌ای تعریف نشده."
    buttons = [["➕ افزودن پروژه", "🗑️ حذف پروژه"], [CANCEL_BTN]]
    await update.message.reply_text(
        "🏗 لیست پروژه‌ها:\n\n" + body + "\n\nعملیات:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return PROJECT_MENU


async def project_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    if "افزودن" in text:
        await update.message.reply_text(
            "نام پروژه(ها) را بفرستید (هر خط یک نام):",
            reply_markup=nav_keyboard(with_back=False),
        )
        return PROJECT_ADD
    if "حذف" in text:
        projects = db.get_all_projects()
        active = [pr for pr in projects if pr["active"]]
        if not active:
            await update.message.reply_text("پروژه فعالی نیست.")
            return await cancel(update, context)
        buttons = [[f"#{pr['id']} {pr['name']}"] for pr in active] + [[CANCEL_BTN]]
        await update.message.reply_text(
            "کدام پروژه غیرفعال شود؟",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return PROJECT_DELETE
    return PROJECT_MENU


async def project_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    added = []
    for line in text.splitlines():
        name = line.strip()
        if name:
            pid = db.add_project(name)
            if pid:
                added.append(name)
    if added:
        db.log_activity(u["user_id"], u["name"], "add_projects", "project", None, ", ".join(added))
        await update.message.reply_text(
            f"اضافه شد: {', '.join(added)}",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    else:
        await update.message.reply_text(
            "چیزی اضافه نشد.",
            reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
        )
    return ConversationHandler.END


async def project_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if _is_cancel(text):
        return await cancel(update, context)
    u = context.user_data.get("db_user") or get_or_register_user(update)
    import re
    m = re.search(r"#(\d+)", text)
    if not m:
        await update.message.reply_text("از لیست انتخاب کنید.")
        return PROJECT_DELETE
    pid = int(m.group(1))
    name = text
    for pr in db.get_all_projects():
        if pr["id"] == pid:
            name = pr["name"]
            break
    db.deactivate_project(pid)
    db.log_activity(u["user_id"], u["name"], "deactivate_project", "project", pid, name)
    await update.message.reply_text(
        f"پروژه «{name}» غیرفعال شد.",
        reply_markup=main_menu_keyboard(u["role"], u.get("projects")),
    )
    return ConversationHandler.END


# ---------- Activity log ----------

@require_user
async def activity_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["db_user"]["role"] != "manager":
        await update.message.reply_text("فقط مدیر.")
        return
    logs = db.get_activity_log(40)
    if not logs:
        await update.message.reply_text("لاگی نیست.")
        return
    lines = [
        f"• {lg.get('created_at', '')} | {lg.get('actor_name', '—')}\n"
        f"  {lg.get('action')} {lg.get('target_type') or ''} #{lg.get('target_id') or ''} {lg.get('details') or ''}"
        for lg in logs
    ]
    text = "📜 لاگ:\n\n" + "\n\n".join(lines)
    await update.message.reply_text(text[:4000])


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
    except Exception:
        for m in media_list:
            try:
                if m["media_type"] == "photo":
                    await context.bot.send_photo(update.effective_chat.id, m["file_id"])
                else:
                    await context.bot.send_video(update.effective_chat.id, m["file_id"])
            except Exception:
                pass


# ---------- Fallback ----------

@require_user
async def handle_text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data.get("db_user") or get_or_register_user(update)
    if not u:
        return
    text = (update.message.text or "").strip()

    if context.user_data.get("awaiting_delete_id"):
        context.user_data.pop("awaiting_delete_id", None)
        try:
            rid = int(text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
            report = db.get_report(rid)
            if not report:
                await update.message.reply_text("پیدا نشد.")
                return
            context.user_data["delete_report_id"] = rid
            context.user_data["in_delete_confirm"] = True
            await update.message.reply_text(
                format_report_text(report, include_media_count=False) + "\n\nمطمئن هستید؟",
                reply_markup=ReplyKeyboardMarkup([[YES_DELETE], [CANCEL_BTN]], resize_keyboard=True),
            )
            return
        except ValueError:
            await update.message.reply_text("شماره معتبر.")
            return

    if context.user_data.get("in_delete_confirm"):
        context.user_data.pop("in_delete_confirm", None)
        if text in (YES_DELETE, "بله حذف شود"):
            return await delete_confirm(update, context)
        return await cancel(update, context)

    routes = {
        "📝 ثبت گزارش جدید": new_report_start,
        "ثبت گزارش جدید": new_report_start,
        "📋 گزارش‌های من": my_reports,
        "گزارش‌های من": my_reports,
        "✏️ ویرایش گزارش": edit_report_start,
        "ویرایش گزارش": edit_report_start,
        "📅 گزارش امروز": today_summary,
        "گزارش امروز": today_summary,
        "📆 گزارش این هفته": week_summary,
        "گزارش این هفته": week_summary,
        "📊 آمار و خلاصه": stats_cmd,
        "آمار و خلاصه": stats_cmd,
        "🔍 فیلتر گزارش‌ها": filter_menu,
        "فیلتر گزارش‌ها": filter_menu,
        "🗑️ حذف گزارش": delete_report_start,
        "حذف گزارش": delete_report_start,
        "📁 خروجی اکسل / PDF": export_menu,
        "خروجی اکسل / PDF": export_menu,
        "👷 مدیریت کارگران": worker_menu,
        "مدیریت کارگران": worker_menu,
        "🏗 مدیریت پروژه‌ها": project_menu,
        "مدیریت پروژه‌ها": project_menu,
        "👥 مدیریت کاربران": manage_users_menu,
        "مدیریت کاربران": manage_users_menu,
        "📜 لاگ فعالیت‌ها": activity_log_cmd,
        "لاگ فعالیت‌ها": activity_log_cmd,
        "❓ راهنما": help_cmd,
        "راهنما": help_cmd,
    }
    if text in routes:
        return await routes[text](update, context)
    if text.startswith("⚡ گزارش سریع:") or text.startswith("گزارش سریع:"):
        return await quick_report_start(update, context)

    if text.isdigit() or text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).isdigit():
        rid = int(text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        report = db.get_report(rid)
        if report:
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


async def daily_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = today_gregorian()
    for s in db.get_supervisors():
        own = db.get_reports(supervisor_id=s["user_id"], date_from=today, date_to=today, limit=50)
        own_projects = {r["project"] for r in own}
        missing = [p for p in (s.get("projects") or []) if p not in own_projects]
        if missing and s["user_id"] > 0:
            try:
                await context.bot.send_message(
                    s["user_id"],
                    f"⏰ یادآوری: برای امروز ({gregorian_to_jalali_display(today)}) "
                    f"گزارش ثبت نشده.\nپروژه‌ها: {', '.join(missing)}",
                )
            except Exception as e:
                logger.warning(f"reminder failed: {e}")


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN در .env تنظیم نشده است.")
    db.init_db()
    db.seed_projects_if_empty(config.PROJECTS)
    app = Application.builder().token(config.BOT_TOKEN).build()

    conv_report = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(📝 )?ثبت گزارش جدید$"), new_report_start),
            MessageHandler(filters.Regex("^(⚡ )?گزارش سریع:"), quick_report_start),
            MessageHandler(filters.Regex("^(✏️ )?ویرایش گزارش$"), edit_report_start),
            CommandHandler("newreport", new_report_start),
            CommandHandler("edit", edit_report_start),
        ],
        states={
            CHOOSING_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_project)],
            REPORT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date)],
            REPORT_DATE_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date_month)],
            REPORT_DATE_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date_day)],
            WORKERS_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, workers_select)],
            WORKERS_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, workers_hours)],
            WORK_REPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, work_report_input)],
            MATERIALS_IN: [MessageHandler(filters.TEXT & ~filters.COMMAND, materials_in_input)],
            MATERIALS_OUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, materials_out_input)],
            FOOD_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, food_count_input)],
            PETTY_CASH: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), petty_cash_input)],
            ISSUES: [MessageHandler(filters.TEXT & ~filters.COMMAND, issues_input)],
            MISC: [MessageHandler(filters.TEXT & ~filters.COMMAND, misc_input)],
            MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | (filters.TEXT & ~filters.COMMAND), media_input)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_report)],
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_input)],
            EDIT_PICK_REPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_pick_report)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    conv_filter = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🔍 )?فیلتر گزارش‌ها$"), filter_menu)],
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
        entry_points=[MessageHandler(filters.Regex("^(📁 )?خروجی اکسل / PDF$"), export_menu)],
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
        entry_points=[MessageHandler(filters.Regex("^(👥 )?مدیریت کاربران$"), manage_users_menu)],
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

    conv_workers = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(👷 )?مدیریت کارگران$"), worker_menu)],
        states={
            WORKER_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, worker_menu_choice)],
            WORKER_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, worker_add)],
            WORKER_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, worker_delete)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv_report)
    app.add_handler(conv_filter)
    app.add_handler(conv_export)
    app.add_handler(conv_users)
    conv_projects = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🏗 )?مدیریت پروژه‌ها$"), project_menu)],
        states={
            PROJECT_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, project_menu_choice)],
            PROJECT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, project_add)],
            PROJECT_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, project_delete)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_workers)
    app.add_handler(conv_projects)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_fallback))

    if app.job_queue:
        app.job_queue.run_daily(
            daily_reminder_job,
            time=dt_time(hour=config.REMINDER_HOUR, minute=config.REMINDER_MINUTE, second=0),
            name="daily_report_reminder",
        )
        logger.info("Reminder scheduled")
    else:
        logger.warning("JobQueue unavailable")

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
