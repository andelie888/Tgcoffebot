import asyncio
import logging
import math
import sqlite3

from contextlib import closing
from datetime import date, datetime, time, timedelta

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import accounting as acc

from ai_analysis import analyze_day
from config import (
    ADMIN_IDS,
    ANTHROPIC_API_KEY,
    BACKUP_CHAT_ID,
    BACKUP_DIR,
    BACKUP_KEEP_DAYS,
    BOT_TOKEN,
    CURRENCY,
    DAILY_SUMMARY_TIME,
    DAY_ROLLOVER_HOUR,
    DB_NAME,
    HISTORY_LIMIT,
    MORNING_TIME,
    REMINDER_REPEAT_MINUTES,
    TIMEZONE,
)
from database import create_tables, migrate_database


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("coffee_manager")


# ===== КНОПКИ =====

# Главное меню — четыре кнопки.
BTN_CLOSE_DAY = "✅ Закрыть день"
BTN_REPORTS = "📊 Отчёты"
BTN_PURCHASE = "🛒 Закупка"
BTN_SETTINGS = "⚙️ Настройки"

BTN_MAIN = "🔙 Главное меню"
BTN_CANCEL = "❌ Отмена"

# Закрытие дня
BTN_TEMPLATE = "📋 Шаблон"
BTN_CLOSE_YESTERDAY = "📅 Закрыть вчерашний день"
BTN_CONFIRM_SALES = "✅ Верно"
BTN_RETYPE = "✏️ Ввести заново"
BTN_NO_FREE = "👍 Нет, закрыть день"
BTN_HAS_FREE = "☕ Были бесплатные"
BTN_MORE_FREE = "➕ Ещё бесплатные"
BTN_REASON_TREAT = "☕ Угощение"
BTN_REASON_WASTE = "🗑 Брак"

# Отчёты
BTN_REPORT_PRODUCTS = "📈 По товарам"
BTN_STOCK = "📦 Склад"
BTN_LOSSES = "🔍 Потери"
BTN_HISTORY = "🧾 История"
BTN_STOCKTAKE = "📝 Инвентаризация"
BTN_BACK_REPORTS = "⬅️ Отчёты"

PERIOD_BUTTONS = {
    "📅 Сегодня": "today",
    "📅 Вчера": "yesterday",
    "📅 7 дней": "7days",
    "📅 Этот месяц": "month",
    "📅 Прошлый месяц": "last_month",
    "📅 Всё время": "all",
}

# Закупка
BTN_OTHER_EXPENSE = "💸 Другой расход"
BTN_SAME_PRICE = "✅ Как в прошлый раз"

# Настройки
BTN_MENU_MANAGEMENT = "☕ Меню и цены"
BTN_FIXED_COSTS = "🏠 Постоянные расходы"
BTN_TAX = "🏛 Налог"
BTN_NOTIFICATIONS = "🔔 Уведомления"
BTN_NOTIFY_ON = "🔔 Включить"
BTN_NOTIFY_OFF = "🔕 Выключить"
BTN_BACK_SETTINGS = "⬅️ Настройки"

BTN_ADD_FIXED = "➕ Добавить постоянный расход"
BTN_BACK_FIXED = "⬅️ Постоянные расходы"
BTN_CHANGE_AMOUNT = "💲 Изменить сумму"

BTN_ADD_PRODUCT = "➕ Добавить товар"
BTN_BACK_PRODUCTS = "⬅️ К списку товаров"
BTN_EDIT_NAME = "📝 Название"
BTN_EDIT_PRICE = "💲 Цена"
BTN_EDIT_COST = "🧮 Себестоимость"
BTN_DELETE = "🗑 Удалить"
BTN_CONFIRM_DELETE = "✅ Да, удалить"
BTN_RECIPE = "🧾 Рецепт"
BTN_RECIPE_ADD = "➕ Ингредиент в рецепт"
BTN_RECIPE_CLEAR = "🗑 Очистить рецепт"
BTN_BACK_PRODUCT = "⬅️ К товару"
BTN_NEW_INGREDIENT = "➕ Новый ингредиент"
UNIT_BUTTONS = ["кг", "л", "шт"]

PURCHASE_PREFIX = "🛒 "
STOCKTAKE_PREFIX = "📝 "
RECIPE_PREFIX = "🥄 "
EDIT_PRODUCT_PREFIX = "✏️ "
FIXED_PREFIX = "🏠 "
TAX_PREFIX = "🏛 "

PRODUCT_EDIT_STEPS = {
    BTN_EDIT_NAME: "edit_name",
    BTN_EDIT_PRICE: "edit_price",
    BTN_EDIT_COST: "edit_cost",
    BTN_DELETE: "confirm_delete",
}

EXPENSE_PRESETS = ["Хозтовары", "Ремонт", "Реклама", "Другое"]
FIXED_PRESETS = ["Аренда", "Зарплата", "Коммуналка", "Интернет и связь"]

NAME_MAX_LENGTH = 40
MESSAGE_LIMIT = 4000


# ===== ОБЩИЕ ПОМОЩНИКИ =====


def now_local():
    return datetime.now(TIMEZONE)


def now_str():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return now_local().date()


def work_day():
    """День, который закрывают сейчас: после полуночи до 5 утра — ещё вчерашний."""
    current = now_local()

    if current.hour < DAY_ROLLOVER_HOUR:
        return current.date() - timedelta(days=1)

    return current.date()


def money(value):
    # Копейки показываем только в маленьких суммах: 12.50 ₽, но 6 442 ₽.
    value = round(value or 0, 2) if abs(value or 0) < 100 else round(value or 0)

    if value == int(value):
        text = f"{int(value):,}"
    else:
        text = f"{value:,.2f}"

    return f"{text.replace(',', ' ')} {CURRENCY}"


def qty(value):
    return f"{value:g}"


def format_dt(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return value


def parse_number(text):
    try:
        value = float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return None

    return value if math.isfinite(value) else None


def parse_money(text):
    value = parse_number(text)
    return None if value is None else round(value, 2)


def fetch_all(sql, params=()):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        return connection.execute(sql, params).fetchall()


def fetch_one(sql, params=()):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        return connection.execute(sql, params).fetchone()


def split_text(text):
    if len(text) <= MESSAGE_LIMIT:
        return [text]

    chunks = []
    current = ""

    for line in text.split("\n"):
        while len(line) > MESSAGE_LIMIT:
            chunks.append(line[:MESSAGE_LIMIT])
            line = line[MESSAGE_LIMIT:]

        if current and len(current) + len(line) + 1 > MESSAGE_LIMIT:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


async def send(update: Update, text, markup=None):
    """Отправляет текст, разбивая длинные сообщения под лимит Telegram."""
    chunks = split_text(text)

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        await update.message.reply_text(chunk, reply_markup=markup if is_last else None)


# ===== КЛАВИАТУРЫ =====


def keyboard(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def in_pairs(buttons):
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]


def main_menu():
    return keyboard([[BTN_CLOSE_DAY, BTN_REPORTS], [BTN_PURCHASE, BTN_SETTINGS]])


def cancel_menu():
    return keyboard([[BTN_CANCEL]])


def close_input_menu(offer_yesterday):
    rows = [[BTN_TEMPLATE]]

    if offer_yesterday:
        rows.append([BTN_CLOSE_YESTERDAY])

    return keyboard(rows + [[BTN_CANCEL]])


def confirm_sales_menu():
    return keyboard([[BTN_CONFIRM_SALES], [BTN_RETYPE, BTN_CANCEL]])


def free_question_menu(first_time):
    return keyboard([[BTN_NO_FREE], [BTN_HAS_FREE if first_time else BTN_MORE_FREE], [BTN_CANCEL]])


def free_reason_menu():
    return keyboard([[BTN_REASON_TREAT, BTN_REASON_WASTE], [BTN_CANCEL]])


def reports_menu():
    periods = list(PERIOD_BUTTONS)

    return keyboard(
        in_pairs(periods)
        + [[BTN_REPORT_PRODUCTS, BTN_STOCK], [BTN_LOSSES, BTN_HISTORY], [BTN_MAIN]]
    )


def stock_menu():
    return keyboard([[BTN_STOCKTAKE, BTN_NEW_INGREDIENT], [BTN_BACK_REPORTS, BTN_MAIN]])


def ingredients_menu(prefix, ingredients, extra=None, back=BTN_MAIN):
    buttons = [f"{prefix}{name}" for _, name, *_ in ingredients]
    rows = in_pairs(buttons)

    if extra:
        rows.append(extra)

    return keyboard(rows + [[back]])


def expense_name_menu():
    return keyboard(in_pairs(EXPENSE_PRESETS) + [[BTN_CANCEL]])


def purchase_total_menu():
    return keyboard([[BTN_SAME_PRICE], [BTN_CANCEL]])


def settings_menu():
    return keyboard(
        [[BTN_MENU_MANAGEMENT, BTN_FIXED_COSTS], [BTN_TAX, BTN_NOTIFICATIONS], [BTN_MAIN]]
    )


def notifications_menu():
    return keyboard([[BTN_NOTIFY_ON, BTN_NOTIFY_OFF], [BTN_BACK_SETTINGS]])


def fixed_costs_menu(costs):
    buttons = [f"{FIXED_PREFIX}{name}" for _, name, _ in costs]

    return keyboard([[BTN_ADD_FIXED]] + in_pairs(buttons) + [[BTN_BACK_SETTINGS]])


def fixed_card_menu():
    return keyboard([[BTN_CHANGE_AMOUNT, BTN_DELETE], [BTN_BACK_FIXED]])


def fixed_name_menu():
    return keyboard(in_pairs(FIXED_PRESETS) + [[BTN_CANCEL]])


def tax_menu():
    buttons = [f"{TAX_PREFIX}{label}" for label in acc.TAX_MODES.values()]

    return keyboard([[button] for button in buttons] + [[BTN_BACK_SETTINGS]])


def menu_management_menu(products):
    buttons = [f"{EDIT_PRODUCT_PREFIX}{name}" for _, name, *_ in products]

    return keyboard([[BTN_ADD_PRODUCT]] + in_pairs(buttons) + [[BTN_BACK_SETTINGS]])


def product_card_menu(has_recipe):
    rows = [[BTN_EDIT_NAME, BTN_EDIT_PRICE], [BTN_RECIPE]]

    if not has_recipe:
        rows[1].append(BTN_EDIT_COST)

    return keyboard(rows + [[BTN_DELETE], [BTN_BACK_PRODUCTS]])


def recipe_menu(has_recipe):
    rows = [[BTN_RECIPE_ADD]]

    if has_recipe:
        rows[0].append(BTN_RECIPE_CLEAR)

    return keyboard(rows + [[BTN_BACK_PRODUCT]])


def confirm_delete_menu():
    return keyboard([[BTN_CONFIRM_DELETE, BTN_CANCEL]])


# ===== ДАННЫЕ =====


def get_product(product_id):
    return fetch_one(
        "SELECT id, name, price, cost FROM products WHERE id = ? AND is_active = 1",
        (product_id,),
    )


def get_ingredients():
    return fetch_all("""
        SELECT id, name, unit, stock, minimum_stock, purchase_price
        FROM ingredients
        ORDER BY id
        """)


def get_ingredient(ingredient_id):
    return fetch_one(
        """
        SELECT id, name, unit, stock, minimum_stock, purchase_price
        FROM ingredients
        WHERE id = ?
        """,
        (ingredient_id,),
    )


def low_stock_text():
    low_stock = acc.get_low_stock()

    if not low_stock:
        return ""

    text = "⚠️ Заканчивается:\n"

    for name, unit, stock, minimum_stock in low_stock:
        if stock < 0:
            text += f"• {name}: {qty(round(stock, 3))} {unit} — по учёту меньше нуля, пересчитайте склад\n"
        else:
            text += f"• {name}: {qty(round(stock, 3))} {unit} (минимум {qty(minimum_stock)})\n"

    return text + "\n"


def products_by_id():
    return {product[0]: product for product in acc.get_products()}


def items_text(items, products):
    """«• Латте 0.4 × 23 = 6 210 ₽» по строкам."""
    lines = []

    for product_id, quantity in items.items():
        product = products.get(product_id)

        if product:
            lines.append(f"• {product[1]} × {quantity} = {money(product[2] * quantity)}")

    return "\n".join(lines)


def free_text(free, products):
    lines = []

    for product_id, quantity, kind in free:
        product = products.get(product_id)
        label = "угощение" if kind == acc.KIND_TREAT else "брак"

        if product:
            lines.append(f"• {product[1]} × {quantity} — {label}")

    return "\n".join(lines)


# ===== ДОСТУП И СТАРТ =====


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user is None or user.id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("⛔ Нет доступа.")
        raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await send(
        update,
        "☕ Coffee Manager\n\n"
        "Каждый вечер — одно действие: нажмите «✅ Закрыть день» и отправьте, "
        "сколько чего продали (цифры из отчёта кассы). Всё остальное бот посчитает сам: "
        "склад, себестоимость, прибыль, налог и анализ дня.\n\n"
        f"{BTN_REPORTS} — деньги за любой период, склад, потери\n"
        f"{BTN_PURCHASE} — купили продукты или потратили на другое\n"
        f"{BTN_SETTINGS} — меню, аренда и зарплаты, налог\n\n"
        f"В {DAILY_SUMMARY_TIME} напомню закрыть день, в {MORNING_TIME} пришлю план на день.",
        main_menu(),
    )


# ===== ЗАКРЫТИЕ ДНЯ =====


async def start_close_day(update: Update, context: ContextTypes.DEFAULT_TYPE, day=None):
    data = context.user_data
    products = acc.get_products()

    if not products:
        await send(
            update,
            f"☕ В меню пока нет товаров.\n\nДобавьте их: {BTN_SETTINGS} → {BTN_MENU_MANAGEMENT}.",
            main_menu(),
        )
        return

    day = day or work_day()
    yesterday = day - timedelta(days=1)
    example = "\n".join(f"{name} - {n}" for (_, name, *_), n in zip(products[:3], (23, 15, 8)))
    first = acc.first_activity_day()
    offer_yesterday = (
        day == work_day()
        and not acc.is_day_closed(yesterday)
        and first is not None
        and first <= yesterday
    )

    data.clear()
    data.update(flow="close", step="close_sales", day=day.isoformat(), items={}, free=[])

    warning = ""
    if acc.is_day_closed(day):
        warning = "\n⚠️ Этот день уже закрыт. Новые цифры заменят прежние.\n"

    await send(
        update,
        f"✅ Закрытие дня · {day:%d.%m}\n{warning}\n"
        "Отправьте, сколько чего продали, одним сообщением — по отчёту кассы:\n\n"
        f"{example}\n\n"
        f"Чтобы не набирать названия, нажмите «{BTN_TEMPLATE}», скопируйте "
        "и впишите числа. Что не продавалось — просто не пишите.",
        close_input_menu(offer_yesterday),
    )


async def send_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, "Скопируйте, впишите количество и отправьте:")
    await send(update, acc.day_template(acc.get_products()), close_input_menu(False))


async def handle_close_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    data = context.user_data
    products = acc.get_products()
    by_id = products_by_id()
    parsed, unknown = acc.parse_items(text, products)

    if data["step"] == "close_sales":
        items = {int(k): v for k, v in data.get("items", {}).items()}
        items.update(parsed)
        data["items"] = items

        if unknown:
            names = "\n".join(f"• {line}" for line in unknown[:10])
            understood = f"\n\nУже записал:\n{items_text(items, by_id)}" if items else ""
            await send(
                update,
                f"🤔 Не понял строки:\n{names}\n\n"
                "Проверьте названия и отправьте эти строки ещё раз "
                f"(или «{BTN_TEMPLATE}» — список названий).{understood}",
                close_input_menu(False),
            )
            return

        if not items:
            await send(
                update,
                "Не нашёл количество. Пример: Латте 0.4 - 23",
                close_input_menu(False),
            )
            return

        data["step"] = "close_confirm"
        total_qty = sum(items.values())
        revenue = sum(by_id[pid][2] * q for pid, q in items.items() if pid in by_id)
        day = date.fromisoformat(data["day"])

        await send(
            update,
            f"📋 Продажи за {day:%d.%m}:\n\n{items_text(items, by_id)}\n\n"
            f"Итого: {total_qty} шт. · {money(revenue)}\n\n"
            "Сверьте с кассой. Всё верно?",
            confirm_sales_menu(),
        )
        return

    if data["step"] == "close_free_input":
        if unknown:
            names = "\n".join(f"• {line}" for line in unknown[:10])
            await send(
                update,
                f"🤔 Не понял строки:\n{names}\n\nОтправьте бесплатные позиции ещё раз.",
                cancel_menu(),
            )
            return

        if not parsed:
            await send(update, "Не нашёл количество. Пример: Латте 0.4 - 2", cancel_menu())
            return

        data["pending_free"] = parsed
        data["step"] = "close_free_reason"

        listed = "\n".join(
            f"• {by_id[pid][1]} × {quantity}" for pid, quantity in parsed.items() if pid in by_id
        )
        await send(
            update,
            f"Бесплатно:\n{listed}\n\n"
            "Это угощение (себе, гостям, бариста) или брак (пролили, переделали)?",
            free_reason_menu(),
        )


async def ask_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    data["step"] = "close_free_ask"
    first_time = not data.get("free")
    by_id = products_by_id()

    if first_time:
        text = (
            "☕ Были сегодня бесплатные напитки?\n\n"
            "Угощения себе, друзьям, бариста или брак. "
            "Их ингредиенты тоже ушли со склада — запишем, чтобы склад и прибыль были точными."
        )
    else:
        text = f"Записал бесплатные:\n{free_text(data['free'], by_id)}\n\nЕщё были?"

    await send(update, text, free_question_menu(first_time))


async def finish_close_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    day = date.fromisoformat(data["day"])
    items = {int(k): v for k, v in data.get("items", {}).items()}
    free = [tuple(entry) for entry in data.get("free", [])]

    acc.close_day(day, items, free, now=now_local().replace(tzinfo=None))
    data.clear()

    await send(update, f"✅ День {day:%d.%m} закрыт. Склад списан по рецептам.")
    await send(update, build_day_report(day))

    if ANTHROPIC_API_KEY:
        await send(update, "🧠 Анализирую день…")

    await send(update, await day_analysis_text(day), main_menu())


def build_day_report(day):
    f = acc.get_finances(day, day)
    message = (
        f"📊 Итоги дня · {day:%d.%m}\n\n"
        f"☕ Продано: {f['sold']} шт.\n"
        f"💵 Выручка: {money(f['revenue'])}\n"
        f"📦 Себестоимость: {money(f['cost'])}\n"
        f"📈 Валовая прибыль: {money(f['gross'])} ({f['margin']:.0f}%)\n"
    )
    message += costs_lines(f)
    message += f"\n💰 Чистая прибыль: {money(f['net'])}\n"

    be = acc.breakeven(day)

    if be:
        cups = be[0]
        diff = f["sold"] - cups
        if diff >= 0:
            message += f"🎯 Точка безубыточности {cups} шт. — вы выше на {diff} шт.\n"
        else:
            message += f"🎯 Точка безубыточности {cups} шт. — не хватило {-diff} шт.\n"

    top = acc.get_product_sales(day, day)[:3]

    if top:
        message += "\n🏆 Топ дня:\n"
        for name, sold, revenue, _ in top:
            message += f"• {name} — {sold} шт. / {money(revenue)}\n"

    stock_text = low_stock_text()

    if stock_text:
        message += f"\n{stock_text}"

    return message.strip()


def costs_lines(f):
    text = ""

    if f["treat_qty"]:
        text += f"☕ Угощения: {f['treat_qty']} шт. · {money(f['treat_cost'])}\n"

    if f["waste_qty"]:
        text += f"🗑 Брак: {f['waste_qty']} шт. · {money(f['waste_cost'])}\n"

    if f["fixed"]:
        text += f"🏠 Постоянные расходы (доля): {money(f['fixed'])}\n"

    if f["expenses"]:
        text += f"💸 Другие расходы: {money(f['expenses'])}\n"

    if f["tax_mode"] != "none":
        text += f"🏛 Налог (примерно): {money(f['tax'])}\n"

    return text


async def day_analysis_text(day):
    context = acc.day_context(day)
    ai_text = await asyncio.to_thread(analyze_day, context)

    if ai_text:
        return f"🧠 Анализ дня\n\n{ai_text}"

    lines = acc.rule_insights(context)

    if not lines:
        return "🧠 Анализ дня\n\nПока мало данных для выводов — закрывайте каждый день."

    return "🧠 Анализ дня\n\n" + "\n".join(f"• {line}" for line in lines)


async def handle_close_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    """Кнопки и ввод внутри закрытия дня. Возвращает True, если сообщение обработано."""
    data = context.user_data
    step = data.get("step")

    if text == BTN_TEMPLATE:
        await send_template(update, context)
        return True

    if text == BTN_CLOSE_YESTERDAY:
        await start_close_day(update, context, work_day() - timedelta(days=1))
        return True

    if step == "close_confirm":
        if text == BTN_CONFIRM_SALES:
            await ask_free(update, context)
        elif text == BTN_RETYPE:
            await start_close_day(update, context, date.fromisoformat(data["day"]))
        else:
            await send(update, "Нажмите «Верно» или «Ввести заново».", confirm_sales_menu())
        return True

    if step == "close_free_ask":
        if text == BTN_NO_FREE:
            await finish_close_day(update, context)
        elif text in (BTN_HAS_FREE, BTN_MORE_FREE):
            data["step"] = "close_free_input"
            await send(
                update,
                "Отправьте бесплатные позиции так же: Латте 0.4 - 2",
                cancel_menu(),
            )
        else:
            await send(update, "Выберите кнопку ниже.", free_question_menu(not data.get("free")))
        return True

    if step == "close_free_reason":
        if text in (BTN_REASON_TREAT, BTN_REASON_WASTE):
            kind = acc.KIND_TREAT if text == BTN_REASON_TREAT else acc.KIND_WASTE
            pending = {int(k): v for k, v in data.pop("pending_free", {}).items()}
            data["free"] = data.get("free", []) + [[pid, q, kind] for pid, q in pending.items()]
            await ask_free(update, context)
        else:
            await send(update, "Выберите: угощение или брак.", free_reason_menu())
        return True

    if step in ("close_sales", "close_free_input"):
        await handle_close_input(update, context, text)
        return True

    return False


# ===== ОТЧЁТЫ =====


def period_range(period):
    current = today()

    if period == "yesterday":
        day = current - timedelta(days=1)
        return day, day, f"Вчера ({day:%d.%m})"

    if period == "7days":
        return current - timedelta(days=6), current, "Последние 7 дней"

    if period == "month":
        return current.replace(day=1), current, "Этот месяц"

    if period == "last_month":
        end = current.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, f"{end:%m.%Y}"

    if period == "all":
        first = acc.first_activity_day() or current
        return first, current, "Всё время"

    return current, current, f"Сегодня ({current:%d.%m})"


def finance_report(start, end, label):
    f = acc.get_finances(start, end)
    message = (
        f"💰 Финансы · {label}\n\n"
        f"☕ Продано: {f['sold']} шт.\n"
        f"💵 Выручка: {money(f['revenue'])}\n"
        f"📦 Себестоимость: {money(f['cost'])}\n"
        f"📈 Валовая прибыль: {money(f['gross'])} ({f['margin']:.0f}%)\n"
    )
    message += costs_lines(f)
    message += f"\n💰 Чистая прибыль: {money(f['net'])}"

    unclosed = [
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if not acc.is_day_closed(start + timedelta(days=i))
    ]
    first = acc.first_activity_day()
    unclosed = [d for d in unclosed if first and d >= first and d < work_day()]

    if unclosed and len(unclosed) <= 7:
        days = ", ".join(f"{d:%d.%m}" for d in unclosed)
        message += f"\n\n⚠️ Не закрыты дни: {days} — продажи за них не учтены."
    elif unclosed:
        message += f"\n\n⚠️ Не закрыто дней: {len(unclosed)} — продажи за них не учтены."

    return message


async def product_report(update: Update):
    start, end, label = period_range("month")
    rows = acc.get_product_sales(start, end)
    costs = acc.get_product_costs()

    message = f"📈 По товарам · {label}\n\n"

    if rows:
        for position, (name, quantity, revenue, cost) in enumerate(rows, start=1):
            profit = revenue - cost
            margin = profit / revenue * 100 if revenue > 0 else 0
            message += (
                f"{position}. {name} — {quantity} шт.\n"
                f"   выручка {money(revenue)} · прибыль {money(profit)} ({margin:.0f}%)\n"
            )
    else:
        message += "Продаж за месяц пока нет.\n"

    if costs:
        message += "\n💰 Прибыль с одной порции сейчас:\n"
        for _, name, price, cost, by_recipe in sorted(costs, key=lambda p: p[2] - p[3]):
            margin = (price - cost) / price * 100 if price > 0 else 0
            mark = "" if by_recipe else " (себестоимость вручную)"
            message += f"• {name}: {money(price - cost)} ({margin:.0f}%){mark}\n"

    await send(update, message, reports_menu())


async def show_stock(update: Update):
    ingredients = get_ingredients()

    if not ingredients:
        await send(update, "📦 Склад пока пуст.", reports_menu())
        return

    message = "📦 Склад\n\n"

    for _, name, unit, stock, minimum_stock, _ in ingredients:
        status = "⚠️" if stock <= minimum_stock else "✅"
        message += f"{status} {name}: {qty(round(stock, 3))} {unit} (мин. {qty(minimum_stock)})\n"

    message += (
        f"\nРаз в неделю пересчитывайте склад — «{BTN_STOCKTAKE}». "
        "Так бот найдёт неучтённые потери."
    )

    await send(update, message, stock_menu())


async def show_losses(update: Update):
    end = today()
    start = end - timedelta(days=29)
    f = acc.get_finances(start, end)
    losses = acc.get_losses(start, end)

    message = "🔍 Потери · последние 30 дней\n\n"
    message += f"☕ Угощения: {f['treat_qty']} шт. · {money(f['treat_cost'])}\n"
    message += f"🗑 Брак: {f['waste_qty']} шт. · {money(f['waste_cost'])}\n\n"

    if losses:
        total = sum(row[3] for row in losses)
        message += f"📝 Неучтённые потери по инвентаризации: {money(total)}\n"
        for name, unit, amount, value in losses:
            message += f"• {name}: −{qty(round(amount, 3))} {unit} ≈ {money(value)}\n"
        message += (
            "\nЭто то, что ушло со склада, но не попало ни в продажи, ни в угощения: "
            "пролили, забыли записать, неточный рецепт или пропажа."
        )
    elif fetch_one("SELECT 1 FROM stock_adjustments LIMIT 1"):
        message += "📝 Неучтённых потерь по инвентаризации нет."
    else:
        message += (
            "📝 Инвентаризацию ещё не делали — неучтённые потери пока не видны.\n"
            f"{BTN_STOCK} → {BTN_STOCKTAKE}, раз в неделю."
        )

    await send(update, message, reports_menu())


async def show_history(update: Update):
    rows = fetch_all(
        """
        SELECT purchase_date, '🛒 ' || i.name || ' ' || p.quantity || ' ' || i.unit,
               p.quantity * p.purchase_price
        FROM purchases p JOIN ingredients i ON i.id = p.ingredient_id
        UNION ALL
        SELECT expense_date, '💸 ' || name, amount FROM expenses
        ORDER BY 1 DESC
        LIMIT ?
        """,
        (HISTORY_LIMIT,),
    )

    if not rows:
        await send(update, "🧾 Закупок и расходов пока нет.", reports_menu())
        return

    message = "🧾 Последние закупки и расходы\n\n"

    for when, label, amount in rows:
        message += f"{format_dt(when)} · {label} · {money(amount)}\n"

    await send(update, message, reports_menu())


async def show_stocktake_menu(update: Update):
    ingredients = get_ingredients()

    if not ingredients:
        await send(update, "📦 Склад пока пуст.", main_menu())
        return

    await send(
        update,
        "📝 Инвентаризация\n\nВыберите ингредиент и введите, сколько его есть на самом деле:",
        ingredients_menu(STOCKTAKE_PREFIX, ingredients, back=BTN_BACK_REPORTS),
    )


# ===== ЗАКУПКА И РАСХОДЫ =====


async def show_purchase_menu(update: Update):
    ingredients = get_ingredients()

    await send(
        update,
        f"🛒 Закупка\n\n{low_stock_text()}"
        + ("Выберите, что купили:\n\n" if ingredients else "Склад ещё не настроен.\n\n")
        + f"Другие траты (хозтовары, ремонт) — «{BTN_OTHER_EXPENSE}».\n"
        f"Аренда и зарплаты — один раз в месяц: {BTN_SETTINGS} → {BTN_FIXED_COSTS}.",
        ingredients_menu(PURCHASE_PREFIX, ingredients, extra=[BTN_OTHER_EXPENSE]),
    )


def find_ingredient_by_label(text, prefix):
    if not text.startswith(prefix):
        return None

    for ingredient in get_ingredients():
        if text == f"{prefix}{ingredient[1]}":
            return ingredient

    return None


async def handle_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    """Ввод внутри расхода, закупки, инвентаризации и постоянных расходов."""
    data = context.user_data
    step = data.get("step")

    if step == "expense_name":
        if len(text) > NAME_MAX_LENGTH:
            await send(update, "❌ Слишком длинное название.", expense_name_menu())
            return

        data["expense_name"] = text
        data["step"] = "expense_amount"
        await send(update, f"💸 {text}\n\nВведите сумму:", cancel_menu())
        return

    if step == "expense_amount":
        amount = parse_money(text)

        if amount is None or amount <= 0:
            await send(update, "❌ Введите сумму числом, например: 1500", cancel_menu())
            return

        acc.save_expense(data["expense_name"], amount, now_str())
        name = data["expense_name"]
        data.clear()
        await send(update, f"✅ Расход сохранён: {name} — {money(amount)}", main_menu())
        return

    if step == "fixed_name":
        if len(text) > NAME_MAX_LENGTH:
            await send(update, "❌ Слишком длинное название.", fixed_name_menu())
            return

        data["fixed_name"] = text
        data["step"] = "fixed_amount"
        await send(update, f"🏠 {text}\n\nСколько в месяц? Например: 45000", cancel_menu())
        return

    if step in ("fixed_amount", "fixed_change"):
        amount = parse_money(text)

        if amount is None or amount < 0:
            await send(update, "❌ Введите сумму числом, например: 45000", cancel_menu())
            return

        if step == "fixed_amount":
            acc.add_fixed_cost(data["fixed_name"], amount)
        else:
            acc.update_fixed_cost(data["fixed_id"], amount)

        data.clear()
        await send(update, "✅ Сохранено.")
        await show_fixed_costs(update)
        return

    if step == "ing_name":
        if not text or len(text) > NAME_MAX_LENGTH:
            await send(update, "❌ Название от 1 до 40 символов.", cancel_menu())
            return

        if any(row[1].casefold() == text.casefold() for row in get_ingredients()):
            await send(update, "❌ Такой ингредиент уже есть на складе.", cancel_menu())
            return

        data["ing_name"] = text
        data["step"] = "ing_unit"
        await send(
            update,
            f"📦 {text}\n\nВ чём считаем? Кофе и сахар — кг, молоко и сиропы — л, стаканы — шт.",
            keyboard([UNIT_BUTTONS, [BTN_CANCEL]]),
        )
        return

    if step == "ing_unit":
        if text not in UNIT_BUTTONS:
            await send(update, "Выберите кнопкой: кг, л или шт.", keyboard([UNIT_BUTTONS, [BTN_CANCEL]]))
            return

        data["ing_unit"] = text
        data["step"] = "ing_price"
        await send(update, f"Сколько стоит 1 {text} по закупке? Например: 1800", cancel_menu())
        return

    if step in ("ing_price", "ing_stock", "ing_min"):
        value = parse_number(text)

        if value is None or value < 0:
            await send(update, "❌ Введите число, например: 5", cancel_menu())
            return

        unit = data["ing_unit"]

        if step == "ing_price":
            data["ing_price"] = value
            data["step"] = "ing_stock"
            await send(update, f"Сколько сейчас есть на складе ({unit})?", cancel_menu())
            return

        if step == "ing_stock":
            data["ing_stock"] = value
            data["step"] = "ing_min"
            await send(
                update,
                f"При каком остатке напоминать о закупке ({unit})? Например: 2",
                cancel_menu(),
            )
            return

        acc.add_ingredient(data["ing_name"], unit, data["ing_price"], data["ing_stock"], value)
        name = data["ing_name"]
        data.clear()
        await send(
            update,
            f"✅ Ингредиент «{name}» добавлен. Теперь его можно ставить в рецепты: "
            f"{BTN_SETTINGS} → {BTN_MENU_MANAGEMENT} → товар → {BTN_RECIPE}.",
        )
        await show_stock(update)
        return

    if step == "patent_cost":
        amount = parse_money(text)

        if amount is None or amount <= 0:
            await send(update, "❌ Введите стоимость патента за год, например: 60000", cancel_menu())
            return

        acc.set_setting("patent_cost_year", amount)
        acc.set_setting("tax_mode", "patent")
        data.clear()
        await send(update, f"✅ Патент: {money(amount)} в год — {money(amount / 365)} в день.")
        await show_tax(update)
        return

    ingredient = get_ingredient(data.get("ingredient_id"))

    if ingredient is None:
        data.clear()
        await send(update, "❌ Ингредиент не найден.", main_menu())
        return

    ingredient_id, name, unit, stock, _, last_price = ingredient

    if step == "purchase_quantity":
        quantity = parse_number(text)

        if quantity is None or quantity <= 0:
            await send(update, "❌ Введите количество числом, например: 2", cancel_menu())
            return

        data["purchase_quantity"] = quantity
        data["step"] = "purchase_total"

        hint = ""
        if last_price > 0:
            hint = (
                f"\n\nВ прошлый раз: {money(last_price)} за {unit} — "
                f"было бы {money(last_price * quantity)}."
            )

        await send(
            update,
            f"🛒 {name}: {qty(quantity)} {unit}\n\nСколько заплатили всего?{hint}",
            purchase_total_menu() if last_price > 0 else cancel_menu(),
        )
        return

    if step == "purchase_total":
        quantity = data["purchase_quantity"]

        if text == BTN_SAME_PRICE and last_price > 0:
            total = round(last_price * quantity, 2)
        else:
            total = parse_money(text)

        if total is None or total <= 0:
            await send(update, "❌ Введите сумму числом, например: 900", cancel_menu())
            return

        unit_price = acc.save_purchase(ingredient_id, quantity, total, now_str())
        data.clear()

        change = ""
        if last_price > 0 and abs(unit_price - last_price) / last_price >= 0.05:
            percent = (unit_price - last_price) / last_price * 100
            word = "подорожал" if percent > 0 else "подешевел"
            change = (
                f"\n\n{'📈' if percent > 0 else '📉'} {name} {word} на {abs(percent):.0f}%. "
                "Себестоимость напитков пересчитана автоматически."
            )

        await send(
            update,
            f"✅ Закупка: {name} +{qty(quantity)} {unit} за {money(total)} "
            f"({money(unit_price)} за {unit})\n"
            f"📦 Теперь на складе: {qty(round(stock + quantity, 3))} {unit}{change}",
            main_menu(),
        )
        return

    if step == "stocktake_value":
        new_stock = parse_number(text)

        if new_stock is None or new_stock < 0:
            await send(update, "❌ Введите остаток числом, например: 3.5", cancel_menu())
            return

        old_stock = acc.save_stocktake(ingredient_id, new_stock, now_str())
        data.clear()
        difference = new_stock - old_stock
        sign = "+" if difference > 0 else ""
        loss = ""

        if difference < 0 and last_price > 0:
            loss = f"\nНеучтённая потеря ≈ {money(-difference * last_price)}"

        await send(
            update,
            f"✅ {name}: было {qty(round(old_stock, 3))} {unit} → стало {qty(new_stock)} {unit} "
            f"({sign}{qty(round(difference, 3))}){loss}",
        )
        await show_stocktake_menu(update)
        return

    data.clear()
    await send(update, "Главное меню:", main_menu())


# ===== НАСТРОЙКИ: ПОСТОЯННЫЕ РАСХОДЫ И НАЛОГ =====


async def show_fixed_costs(update: Update):
    costs = acc.get_fixed_costs()
    message = "🏠 Постоянные расходы в месяц\n\n"

    if costs:
        for _, name, amount in costs:
            message += f"• {name}: {money(amount)}\n"
        total = acc.monthly_fixed_total()
        message += (
            f"\nИтого: {money(total)} в месяц ≈ {money(acc.daily_fixed(today()))} в день.\n"
            "Бот сам распределяет их по дням, поэтому прибыль каждого дня честная."
        )
    else:
        message += (
            "Пока пусто. Добавьте аренду, зарплаты, коммуналку — один раз, "
            "и бот будет учитывать их каждый день."
        )

    await send(update, message, fixed_costs_menu(costs))


async def show_tax(update: Update):
    mode = acc.get_tax_mode()
    text = f"🏛 Налог\n\nСейчас: {acc.TAX_MODES[mode]}"

    if mode == "patent":
        text += f" — {money(acc.get_patent_cost())} в год"

    text += (
        "\n\nБот считает налог примерно, чтобы прибыль была ближе к реальной. "
        "Точную сумму считает бухгалтер.\n\nВыберите режим:"
    )

    await send(update, text, tax_menu())


# ===== МЕНЮ И ЦЕНЫ =====


def get_product_stats(product_id):
    sold_quantity = fetch_one(
        "SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE product_id = ? AND kind = 'sale'",
        (product_id,),
    )[0]

    with closing(sqlite3.connect(DB_NAME)) as connection:
        recipe = acc.recipe_cost(connection.cursor(), product_id)

    return sold_quantity, recipe


def add_product(name, price, cost):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        connection.execute(
            "INSERT INTO products (name, price, cost) VALUES (?, ?, ?)",
            (name, price, cost),
        )
        connection.commit()


def update_product(product_id, name, price, cost):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        connection.execute(
            "UPDATE products SET name = ?, price = ?, cost = ? WHERE id = ?",
            (name, price, cost, product_id),
        )
        connection.commit()


def delete_product(product_id):
    """Удаляет товар без продаж. Проданный товар только скрывает из меню."""
    with closing(sqlite3.connect(DB_NAME)) as connection:
        cursor = connection.cursor()
        has_sales = (
            cursor.execute(
                "SELECT COUNT(*) FROM sales WHERE product_id = ?", (product_id,)
            ).fetchone()[0]
            > 0
        )

        if has_sales:
            cursor.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))
        else:
            cursor.execute("DELETE FROM recipes WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))

        connection.commit()

    return "archived" if has_sales else "deleted"


def validate_product_name(name, exclude_product_id=None):
    if not name:
        return "❌ Название не может быть пустым."

    if "\n" in name:
        return "❌ Название должно быть в одну строку."

    if len(name) > NAME_MAX_LENGTH:
        return f"❌ Название слишком длинное. Максимум {NAME_MAX_LENGTH} символов."

    # «Латте 0.4» — можно; «Чай 2» — нельзя: при закрытии дня не отличить от «Чай» × 2.
    words = acc.normalize_name(name).split(" ")

    if len(words) > 1 and words[-1].isdigit():
        return "❌ Название не должно заканчиваться целым числом, например «Чай 2». Напишите «Чай №2»."

    for product_id, product_name, *_ in acc.get_products():
        if product_id != exclude_product_id and acc.normalize_name(product_name) == acc.normalize_name(name):
            return "❌ Товар с таким названием уже есть в меню."

    return None


async def show_menu_management(update: Update):
    products = acc.get_product_costs()
    message = "☕ Меню и цены\n\n"

    if products:
        for _, name, price, cost, by_recipe in products:
            source = "по рецепту" if by_recipe else "вручную"
            message += f"• {name}: {money(price)} (себестоимость {money(cost)} {source})\n"
        message += "\nВыберите товар или добавьте новый:"
    else:
        message += "В меню пока нет товаров.\nДобавьте первый товар:"

    await send(update, message, menu_management_menu(products))


async def show_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    product = get_product(data.get("product_id"))

    if product is None:
        data.clear()
        await show_menu_management(update)
        return

    data.pop("recipe_step", None)
    recipe = acc.get_recipe(product[0])
    message = f"🧾 Рецепт · {product[1]}\n\n"

    if recipe:
        total = 0
        for _, name, unit, amount, price in recipe:
            cost = amount * price
            total += cost
            message += f"• {name}: {qty(amount)} {unit} — {money(cost)}\n"
        message += (
            f"\nСебестоимость порции: {money(total)}\n"
            "При закрытии дня эти ингредиенты списываются со склада."
        )
    else:
        message += (
            "Рецепта пока нет — склад не списывается.\n"
            f"Нажмите «{BTN_RECIPE_ADD}» и добавьте всё, что уходит на одну порцию: "
            "кофе, молоко, сироп, стакан, крышку."
        )

    await send(update, message, recipe_menu(bool(recipe)))


async def start_recipe_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ingredients = get_ingredients()

    if not ingredients:
        await send(
            update,
            f"На складе нет ингредиентов. Сначала добавьте их: {BTN_REPORTS} → {BTN_STOCK} → {BTN_NEW_INGREDIENT}.",
            recipe_menu(False),
        )
        return

    context.user_data["recipe_step"] = "pick"
    await send(
        update,
        "Выберите ингредиент. Если нужного нет — добавьте его в «📦 Склад» → «➕ Новый ингредиент».",
        ingredients_menu(RECIPE_PREFIX, ingredients, back=BTN_BACK_PRODUCT),
    )


async def handle_recipe_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    data = context.user_data

    if data["recipe_step"] == "pick":
        ingredient = find_ingredient_by_label(text, RECIPE_PREFIX)

        if ingredient is None:
            await send(update, "Выберите ингредиент кнопкой.")
            return

        data["ingredient_id"] = ingredient[0]
        data["recipe_step"] = "amount"
        unit = ingredient[2]
        examples = {"кг": "например: 18 г или 0.018", "л": "например: 200 мл или 0.2"}
        await send(
            update,
            f"🥄 {ingredient[1]}\n\nСколько уходит на одну порцию ({unit})? "
            f"{examples.get(unit, 'например: 1')}",
            cancel_menu(),
        )
        return

    ingredient = get_ingredient(data.get("ingredient_id"))

    if ingredient is None:
        await show_recipe(update, context)
        return

    amount = acc.parse_amount(text, ingredient[2])

    if amount is None or amount <= 0:
        await send(update, f"❌ Введите количество в {ingredient[2]}, например: 0.2", cancel_menu())
        return

    acc.set_recipe_item(data["product_id"], ingredient[0], amount)
    data.pop("ingredient_id", None)
    await send(update, f"✅ {ingredient[1]}: {qty(amount)} {ingredient[2]} на порцию.")
    await show_recipe(update, context)


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update)
        return

    product_id, name, price, manual_cost = product
    sold_quantity, recipe = get_product_stats(product_id)
    cost = recipe if recipe is not None else manual_cost
    profit = price - cost
    margin = profit / price * 100 if price > 0 else 0

    if recipe is not None:
        recipe_status = (
            "✅ Рецепт задан: склад списывается, себестоимость считается сама "
            "по последним закупочным ценам."
        )
    else:
        recipe_status = (
            "⚠️ Рецепта нет: склад не списывается, себестоимость введена вручную. "
            "Попросите того, кто настраивал бота, добавить рецепт."
        )

    await send(
        update,
        f"☕ {name}\n\n"
        f"💵 Цена: {money(price)}\n"
        f"📦 Себестоимость: {money(cost)}\n"
        f"💰 Прибыль с порции: {money(profit)} ({margin:.0f}% маржа)\n"
        f"📊 Продано всего: {sold_quantity} шт.\n\n"
        f"{recipe_status}",
        product_card_menu(recipe is not None),
    )


async def handle_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data["product_step"]
    text = update.message.text.strip()

    if step == "add_name":
        error = validate_product_name(text)

        if error:
            await send(update, error, cancel_menu())
            return

        context.user_data["new_product_name"] = text
        context.user_data["product_step"] = "add_price"
        await send(update, f"☕ {text}\n\nВведите цену продажи, например: 250", cancel_menu())
        return

    if step == "confirm_delete":
        await send(update, f"Нажмите «{BTN_CONFIRM_DELETE}» или «{BTN_CANCEL}».", confirm_delete_menu())
        return

    if step == "edit_name":
        product = get_product(context.user_data.get("product_id"))

        if product is None:
            context.user_data.clear()
            await show_menu_management(update)
            return

        product_id, name, price, cost = product
        error = validate_product_name(text, exclude_product_id=product_id)

        if error:
            await send(update, error, cancel_menu())
            return

        update_product(product_id, text, price, cost)
        context.user_data.pop("product_step", None)
        await send(update, "✅ Название изменено.")
        await show_product_card(update, context)
        return

    value = parse_money(text)

    if value is None:
        await send(update, "❌ Введите число, например: 250", cancel_menu())
        return

    if step in ("add_price", "edit_price") and value <= 0:
        await send(update, "❌ Цена должна быть больше нуля.", cancel_menu())
        return

    if step in ("add_cost", "edit_cost") and value < 0:
        await send(update, "❌ Себестоимость не может быть отрицательной.", cancel_menu())
        return

    if step == "add_price":
        context.user_data["new_product_price"] = value
        context.user_data["product_step"] = "add_cost"
        await send(
            update,
            "📦 Введите себестоимость одной порции, например: 60\nЕсли не знаете — введите 0.",
            cancel_menu(),
        )
        return

    if step == "add_cost":
        name = context.user_data["new_product_name"]
        price = context.user_data["new_product_price"]
        add_product(name, price, value)
        context.user_data.clear()
        await send(
            update,
            f"✅ Товар «{name}» добавлен.\n\n"
            "⚠️ Рецепта для него нет — склад не списывается. "
            "Попросите того, кто настраивал бота, добавить рецепт.",
        )
        await show_menu_management(update)
        return

    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update)
        return

    product_id, name, price, cost = product

    if step == "edit_price":
        update_product(product_id, name, value, cost)
        await send(update, "✅ Цена изменена.")
    else:
        update_product(product_id, name, price, value)
        await send(update, "✅ Себестоимость изменена.")

    context.user_data.pop("product_step", None)
    await show_product_card(update, context)


async def start_product_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, step):
    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update)
        return

    _, name, price, cost = product
    context.user_data["product_step"] = step

    if step == "edit_name":
        prompt = f"Текущее название: {name}\n\nВведите новое название:"
    elif step == "edit_price":
        prompt = f"Текущая цена: {money(price)}\n\nВведите новую цену:"
    elif step == "edit_cost":
        prompt = f"Текущая себестоимость: {money(cost)}\n\nВведите новую себестоимость:"
    else:
        await send(
            update,
            f"🗑 Удалить «{name}»?\n\n"
            "Если товар уже продавался, он будет скрыт из меню, "
            "а история продаж и отчёты сохранятся.",
            confirm_delete_menu(),
        )
        return

    await send(update, prompt, cancel_menu())


# ===== УВЕДОМЛЕНИЯ, НАПОМИНАНИЯ, БЭКАПЫ =====


def set_notifications(user_id, enabled):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        connection.execute(
            """
            INSERT INTO settings (user_id, notifications_enabled)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET notifications_enabled = excluded.notifications_enabled
            """,
            (user_id, int(enabled)),
        )
        connection.commit()


def get_notifications_status(user_id):
    result = fetch_one(
        "SELECT notifications_enabled FROM settings WHERE user_id = ?",
        (user_id,),
    )

    # По умолчанию включены: владелец не должен их искать.
    return True if result is None else bool(result[0])


def notifications_text(user_id):
    if get_notifications_status(user_id):
        status = (
            f"🔔 Включены:\n"
            f"• {MORNING_TIME} — план на день: точка безубыточности, что закупить\n"
            f"• {DAILY_SUMMARY_TIME} — напоминание закрыть день (и ещё раз через час)\n"
            "• по понедельникам — напоминание об инвентаризации\n"
            "• 1-го числа — итоги прошлого месяца"
        )
    else:
        status = "🔕 Выключены."

    return f"🔔 Уведомления\n\n{status}"


async def notify_all(context: ContextTypes.DEFAULT_TYPE, text, markup=None):
    for user_id in ADMIN_IDS:
        if not get_notifications_status(user_id):
            continue

        try:
            await context.bot.send_message(chat_id=user_id, text=text, reply_markup=markup)
        except Exception:
            logger.exception("Не удалось отправить уведомление пользователю %s", user_id)


def has_activity():
    return acc.first_activity_day() is not None or acc.get_products()


async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    day = today()

    if acc.is_day_closed(day) or not has_activity():
        return

    await notify_all(
        context,
        f"⏰ Пора закрыть день {day:%d.%m}.\n\n"
        f"Нажмите «{BTN_CLOSE_DAY}» и отправьте продажи из отчёта кассы — это 1 минута.",
        main_menu(),
    )
    context.job_queue.run_once(repeat_reminder, when=timedelta(minutes=REMINDER_REPEAT_MINUTES))


async def repeat_reminder(context: ContextTypes.DEFAULT_TYPE):
    day = work_day()

    if acc.is_day_closed(day):
        return

    await notify_all(
        context,
        f"⏰ День {day:%d.%m} ещё не закрыт. Без него завтрашний отчёт будет неполным.",
        main_menu(),
    )


def build_morning_text(day):
    lines = [f"☀️ Доброе утро! План на {day:%d.%m}"]
    be = acc.breakeven(day)

    if be:
        cups, day_costs, per_cup = be
        lines.append(
            f"🎯 Чтобы выйти в ноль, продайте {cups} шт. "
            f"(расходы дня ≈ {money(day_costs)}, прибыль с чашки ≈ {money(per_cup)})."
        )
    elif acc.monthly_fixed_total() == 0:
        lines.append(
            f"🎯 Добавьте аренду и зарплаты ({BTN_SETTINGS} → {BTN_FIXED_COSTS}) — "
            "и я буду считать, сколько нужно продать, чтобы выйти в ноль."
        )

    yesterday = day - timedelta(days=1)
    first = acc.first_activity_day()

    if first and first <= yesterday and not acc.is_day_closed(yesterday):
        lines.append(f"⚠️ Вчерашний день ({yesterday:%d.%m}) не закрыт — закройте его сейчас.")

    stock = low_stock_text()

    if stock:
        lines.append(stock.strip())
    else:
        lines.append("✅ Запасов хватает.")

    if day.weekday() == 0:
        lines.append(
            f"📝 Понедельник — пересчитайте склад: {BTN_REPORTS} → {BTN_STOCK} → {BTN_STOCKTAKE}."
        )

    return "\n\n".join(lines)


def build_month_text(day):
    end = day.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    return "📅 Итоги месяца\n\n" + finance_report(start, end, f"{end:%m.%Y}").split("\n\n", 1)[1]


async def morning_message(context: ContextTypes.DEFAULT_TYPE):
    if not has_activity():
        return

    day = today()
    await notify_all(context, build_morning_text(day))

    if day.day == 1 and acc.first_activity_day():
        await notify_all(context, build_month_text(day))


def backup_database():
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"coffee_manager_{today():%Y-%m-%d}.db"

    with closing(sqlite3.connect(DB_NAME)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)

    cutoff = today() - timedelta(days=BACKUP_KEEP_DAYS)

    for backup_file in BACKUP_DIR.glob("coffee_manager_*.db"):
        try:
            backup_day = date.fromisoformat(backup_file.stem.split("_")[-1])
        except ValueError:
            continue

        if backup_day < cutoff:
            backup_file.unlink()

    logger.info("Бэкап базы сохранён: %s", target)

    return target


async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        target = backup_database()
    except Exception:
        logger.exception("Не удалось сделать бэкап базы")
        return

    # Копия вне сервера: файл базы приходит в Telegram тому, кто обслуживает бота.
    if not BACKUP_CHAT_ID:
        return

    try:
        with open(target, "rb") as file:
            await context.bot.send_document(
                chat_id=BACKUP_CHAT_ID,
                document=file,
                filename=target.name,
                caption=f"💾 Бэкап Coffee Manager · {today():%d.%m.%Y}",
                disable_notification=True,
            )
    except Exception:
        logger.exception("Не удалось отправить бэкап в Telegram")


# ===== МАРШРУТИЗАЦИЯ СООБЩЕНИЙ =====


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data
    user_id = update.effective_user.id

    # ===== НАВИГАЦИЯ (работает из любого места) =====

    if text == BTN_MAIN:
        data.clear()
        await send(update, "Главное меню:", main_menu())
        return

    if text == BTN_CLOSE_DAY:
        await start_close_day(update, context)
        return

    if text in (BTN_REPORTS, BTN_BACK_REPORTS):
        data.clear()
        await send(update, "📊 Отчёты — выберите период или раздел:", reports_menu())
        return

    if text == BTN_PURCHASE:
        data.clear()
        await show_purchase_menu(update)
        return

    if text in (BTN_SETTINGS, BTN_BACK_SETTINGS):
        data.clear()
        await send(update, "⚙️ Настройки", settings_menu())
        return

    if text == BTN_CANCEL:
        if "recipe_step" in data and "product_id" in data:
            data.pop("ingredient_id", None)
            await show_recipe(update, context)
        elif "product_id" in data:
            data.pop("product_step", None)
            await show_product_card(update, context)
        elif "product_step" in data:
            data.clear()
            await show_menu_management(update)
        elif data.get("step", "").startswith("fixed") or "fixed_id" in data:
            data.clear()
            await show_fixed_costs(update)
        else:
            data.clear()
            await send(update, "Отменено. Главное меню:", main_menu())
        return

    # ===== ЗАКРЫТИЕ ДНЯ =====

    if data.get("flow") == "close" and await handle_close_flow(update, context, text):
        return

    # ===== ОТЧЁТЫ =====

    if text in PERIOD_BUTTONS:
        data.clear()
        start, end, label = period_range(PERIOD_BUTTONS[text])
        await send(update, finance_report(start, end, label), reports_menu())
        return

    report_actions = {
        BTN_REPORT_PRODUCTS: product_report,
        BTN_STOCK: show_stock,
        BTN_LOSSES: show_losses,
        BTN_HISTORY: show_history,
        BTN_STOCKTAKE: show_stocktake_menu,
    }

    if text in report_actions:
        data.clear()
        await report_actions[text](update)
        return

    # ===== НАСТРОЙКИ =====

    if text == BTN_NOTIFICATIONS:
        await send(update, notifications_text(user_id), notifications_menu())
        return

    if text in (BTN_NOTIFY_ON, BTN_NOTIFY_OFF):
        set_notifications(user_id, text == BTN_NOTIFY_ON)
        await send(update, notifications_text(user_id), notifications_menu())
        return

    if text in (BTN_FIXED_COSTS, BTN_BACK_FIXED):
        data.clear()
        await show_fixed_costs(update)
        return

    if text == BTN_ADD_FIXED:
        data.clear()
        data["step"] = "fixed_name"
        await send(update, "🏠 Выберите или напишите название расхода:", fixed_name_menu())
        return

    if text.startswith(FIXED_PREFIX) and "step" not in data:
        for cost_id, name, amount in acc.get_fixed_costs():
            if text == f"{FIXED_PREFIX}{name}":
                data.clear()
                data["fixed_id"] = cost_id
                await send(update, f"🏠 {name}: {money(amount)} в месяц", fixed_card_menu())
                return

    if text == BTN_CHANGE_AMOUNT and "fixed_id" in data:
        data["step"] = "fixed_change"
        await send(update, "Введите новую сумму в месяц:", cancel_menu())
        return

    if text == BTN_DELETE and "fixed_id" in data:
        acc.delete_fixed_cost(data["fixed_id"])
        data.clear()
        await send(update, "🗑 Удалено.")
        await show_fixed_costs(update)
        return

    if text == BTN_TAX:
        data.clear()
        await show_tax(update)
        return

    if text.startswith(TAX_PREFIX):
        for mode, label in acc.TAX_MODES.items():
            if text == f"{TAX_PREFIX}{label}":
                if mode == "patent":
                    data.clear()
                    data["step"] = "patent_cost"
                    await send(update, "Сколько стоит патент за год? Например: 60000", cancel_menu())
                else:
                    acc.set_setting("tax_mode", mode)
                    await send(update, f"✅ Налог: {label}.")
                    await show_tax(update)
                return

    # ===== МЕНЮ И ЦЕНЫ =====

    if text in (BTN_MENU_MANAGEMENT, BTN_BACK_PRODUCTS):
        data.clear()
        await show_menu_management(update)
        return

    if text == BTN_ADD_PRODUCT:
        data.clear()
        data["product_step"] = "add_name"
        await send(update, "➕ Новый товар\n\nВведите название, например: Раф 0.4", cancel_menu())
        return

    if text in PRODUCT_EDIT_STEPS and "product_id" in data:
        await start_product_edit(update, context, PRODUCT_EDIT_STEPS[text])
        return

    if text == BTN_CONFIRM_DELETE and data.get("product_step") == "confirm_delete":
        product = get_product(data.get("product_id"))
        data.clear()

        if product is not None:
            if delete_product(product[0]) == "archived":
                await send(update, f"🗑 «{product[1]}» скрыт из меню.\nИстория продаж и отчёты сохранены.")
            else:
                await send(update, f"🗑 «{product[1]}» удалён.")

        await show_menu_management(update)
        return

    if "product_id" in data and text == BTN_RECIPE:
        await show_recipe(update, context)
        return

    if "product_id" in data and text == BTN_BACK_PRODUCT:
        data.pop("recipe_step", None)
        data.pop("ingredient_id", None)
        await show_product_card(update, context)
        return

    if "product_id" in data and text == BTN_RECIPE_ADD:
        await start_recipe_add(update, context)
        return

    if "product_id" in data and text == BTN_RECIPE_CLEAR:
        acc.clear_recipe(data["product_id"])
        await send(update, "🗑 Рецепт очищен.")
        await show_recipe(update, context)
        return

    if "recipe_step" in data and "product_id" in data:
        await handle_recipe_input(update, context, text)
        return

    if text == BTN_NEW_INGREDIENT:
        data.clear()
        data["step"] = "ing_name"
        await send(update, "📦 Новый ингредиент\n\nНазвание, например: Сироп карамель", cancel_menu())
        return

    # ===== ВВОД ДАННЫХ =====

    if "product_step" in data:
        await handle_product_input(update, context)
        return

    if "step" in data:
        await handle_step(update, context, text)
        return

    # ===== ЗАКУПКА И РАСХОДЫ =====

    if text == BTN_OTHER_EXPENSE:
        data.clear()
        data["step"] = "expense_name"
        await send(update, "💸 На что потратили? Выберите или напишите:", expense_name_menu())
        return

    if text.startswith(EDIT_PRODUCT_PREFIX):
        for product_id, name, *_ in acc.get_products():
            if text == f"{EDIT_PRODUCT_PREFIX}{name}":
                data.clear()
                data["product_id"] = product_id
                await show_product_card(update, context)
                return

    ingredient = find_ingredient_by_label(text, PURCHASE_PREFIX)

    if ingredient is not None:
        data.clear()
        data["ingredient_id"] = ingredient[0]
        data["step"] = "purchase_quantity"
        await send(update, f"🛒 {ingredient[1]}\n\nСколько купили ({ingredient[2]})?", cancel_menu())
        return

    ingredient = find_ingredient_by_label(text, STOCKTAKE_PREFIX)

    if ingredient is not None:
        data.clear()
        data["ingredient_id"] = ingredient[0]
        data["step"] = "stocktake_value"
        await send(
            update,
            f"📝 {ingredient[1]}\n\n"
            f"По учёту: {qty(round(ingredient[3], 3))} {ingredient[2]}.\n"
            f"Сколько есть на самом деле ({ingredient[2]})?",
            cancel_menu(),
        )
        return

    await send(update, "🤔 Не понял. Выберите раздел в меню.", main_menu())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка при обработке сообщения", exc_info=context.error)

    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз.",
                reply_markup=main_menu(),
            )
        except Exception:
            pass


def parse_time(value):
    hours, minutes = value.split(":")
    return time(hour=int(hours), minute=int(minutes), tzinfo=TIMEZONE)


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в файле .env")

    if not ADMIN_IDS:
        raise ValueError("ADMIN_IDS не найден в файле .env")

    create_tables()
    migrate_database()
    backup_database()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(TypeHandler(Update, check_access), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_button))
    application.add_error_handler(error_handler)

    jobs = application.job_queue
    jobs.run_daily(evening_reminder, time=parse_time(DAILY_SUMMARY_TIME))
    jobs.run_daily(morning_message, time=parse_time(MORNING_TIME))
    jobs.run_daily(backup_job, time=time(hour=4, minute=0, tzinfo=TIMEZONE))

    logger.info(
        "Coffee Manager 2.0 запущен. Часовой пояс: %s, напоминание в %s, утро в %s",
        TIMEZONE,
        DAILY_SUMMARY_TIME,
        MORNING_TIME,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
