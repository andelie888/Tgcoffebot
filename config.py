import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


# Папка проекта: база, бэкапы и .env всегда ищутся рядом с кодом,
# из какой бы папки ни запустили бота.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")

DB_NAME = str(BASE_DIR / os.getenv("DB_FILE", "coffee_manager.db"))
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_KEEP_DAYS = 14

CURRENCY = os.getenv("CURRENCY", "₽")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Samara"))
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "22:00")

HISTORY_LIMIT = 20

ADMIN_IDS = {
    int(user_id)
    for user_id in os.getenv("ADMIN_IDS", "").split(",")
    if user_id.strip()
}
