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

CURRENCY = os.getenv("CURRENCY", "₽").strip()
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Samara").strip())

# Вечером бот напоминает закрыть день, через час — ещё раз, если день не закрыт.
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "22:00").strip()
REMINDER_REPEAT_MINUTES = int(os.getenv("REMINDER_REPEAT_MINUTES", "60"))

# Утром: точка безубыточности на день и что заканчивается на складе.
MORNING_TIME = os.getenv("MORNING_TIME", "09:00").strip()

# Закрытие дня после полуночи до этого часа относится к вчерашнему дню.
DAY_ROLLOVER_HOUR = 5

HISTORY_LIMIT = 20

ADMIN_IDS = {
    int(user_id)
    for user_id in os.getenv("ADMIN_IDS", "").split(",")
    if user_id.strip()
}

# ИИ-анализ дня. Без ключа бот пишет короткий анализ по правилам.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001").strip()
