import os

from dotenv import load_dotenv


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "coffee_manager.db"

ADMIN_IDS = {
    int(user_id)
    for user_id in os.getenv("ADMIN_IDS", "").split(",")
    if user_id.strip()
}