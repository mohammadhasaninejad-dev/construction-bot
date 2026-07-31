import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

PROJECTS = ["گلستان", "نارنجستان", "شمشک", "سروستان", "کامران"]

MAX_MEDIA = 6

REMINDER_HOUR = 18
REMINDER_MINUTE = 0

PERSIAN_WEEKDAYS = [
    "شنبه",
    "یک‌شنبه",
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
]

PERSIAN_MONTHS = [
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
]

INITIAL_USERS = {
    "RAA1362": {
        "name": "مهندس ریاض اشعری",
        "role": "manager",
        "projects": [],
    },
    "Mojganhk": {
        "name": "مهندس مژگان",
        "role": "manager",
        "projects": [],
    },
    "mHasaninejad": {
        "name": "مهندس حسنی‌نژاد",
        "role": "supervisor",
        "projects": ["گلستان"],
    },
    "aminkahali": {
        "name": "مهندس کحالی",
        "role": "supervisor",
        "projects": ["شمشک"],
    },
    "raouf1367": {
        "name": "مهندس رئوف اشعری",
        "role": "supervisor",
        "projects": ["کامران", "سروستان"],
    },
}

_data_dir = Path("/data") if Path("/data").exists() else Path(__file__).parent
MEDIA_DIR = _data_dir / "media_files"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

FONTS_DIR = Path(__file__).parent / "fonts"
PERSIAN_FONT_PATH = FONTS_DIR / "Vazirmatn-Regular.ttf"
PERSIAN_FONT_BOLD_PATH = FONTS_DIR / "Vazirmatn-Bold.ttf"
