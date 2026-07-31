import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8885731079:AAEOiPfe56eq_oZQEm9ksXucDTKMINfKOi4")

# پروژه‌های فعال
PROJECTS = ["گلستان", "نارنجستان", "شمشک", "سروستان", "کامران"]

# حداکثر رسانه در هر گزارش
MAX_MEDIA = 6

# نام‌های روز هفته به فارسی (شنبه = 0 در jdatetime)
PERSIAN_WEEKDAYS = [
    "شنبه",
    "یک‌شنبه",
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
]

# اطلاعات اولیه کاربران بر اساس یوزرنیم
# بعد از اولین /start، user_id عددی ذخیره می‌شود
INITIAL_USERS = {
    "RAA1362": {
        "name": "مهندس ریاض اشعری",
        "role": "manager",
        "projects": [],  # مدیر همه پروژه‌ها را می‌بیند
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
