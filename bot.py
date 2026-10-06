import logging
import math
import re
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

from config import (
    ADMIN_IDS,
    BACKUP_DIR,
    BACKUP_KEEP_DAYS,
    BOT_TOKEN,
    CURRENCY,
    DAILY_SUMMARY_TIME,
    DB_NAME,
    HISTORY_LIMIT,
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

BTN_SALES = "☕ Продажа"
BTN_FINANCE = "💰 Финансы"
BTN_EXPENSE = "💸 Расход"
BTN_PURCHASE = "🛒 Закупка"
BTN_INVENTORY = "📦 Склад"
BTN_REPORTS = "📊 Отчёты"
BTN_SETTINGS = "⚙️ Настройки"

BTN_MAIN = "🔙 Главное меню"
BTN_CANCEL = "❌ Отмена"

BTN_UNDO_SALE = "↩️ Отменить последнюю"
BTN_STOCKTAKE = "📝 Инвентаризация"
BTN_SAME_PRICE = "✅ Как в прошлый раз"

BTN_REPORT_PRODUCTS = "📈 По товарам"
BTN_REPORT_WEEK = "🧠 Итоги недели"
BTN_REPORT_DETAILED = "📊 Подробно"
BTN_HISTORY_SALES = "🧾 Продажи"
BTN_HISTORY_EXPENSES = "🧾 Расходы"
BTN_HISTORY_PURCHASES = "🧾 Закупки"
BTN_BACK_REPORTS = "⬅️ Отчёты"

BTN_MENU_MANAGEMENT = "☕ Меню и цены"
BTN_NOTIFICATIONS = "🔔 Уведомления"
BTN_NOTIFY_ON = "🔔 Включить"
BTN_NOTIFY_OFF = "🔕 Выключить"
BTN_BACK_SETTINGS = "⬅️ Настройки"

BTN_ADD_PRODUCT = "➕ Добавить товар"
BTN_BACK_PRODUCTS = "⬅️ К списку товаров"
BTN_EDIT_NAME = "📝 Название"
BTN_EDIT_PRICE = "💲 Цена"
BTN_EDIT_COST = "🧮 Себестоимость"
BTN_DELETE = "🗑 Удалить"
BTN_CONFIRM_DELETE = "✅ Да, удалить"

SALE_PREFIX = "☕ "
SALE_SEPARATOR = " · "
PURCHASE_PREFIX = "🛒 "
STOCKTAKE_PREFIX = "📝 "
EDIT_PRODUCT_PREFIX = "✏️ "

PERIOD_BUTTONS = {
    "📅 Сегодня": "today",
    "📅 Вчера": "yesterday",
    "📅 7 дней": "7days",
    "📅 Этот месяц": "month",
    "📅 Всё время": "all",
}

PRODUCT_EDIT_STEPS = {
    BTN_EDIT_NAME: "edit_name",
    BTN_EDIT_PRICE: "edit_price",
    BTN_EDIT_COST: "edit_cost",
    BTN_DELETE: "confirm_delete",
}

EXPENSE_PRESETS = ["Аренда", "Зарплата", "Коммуналка", "Хозтовары"]

# Объём в конце названия: «Латте 0.3», «Раф 0,4 л», «Чай 500 мл».
SIZE_PATTERN = re.compile(r"\s+\d+(?:[.,]\d+)?\s*(?:л|мл|ml|l)?$", re.IGNORECASE)

PRODUCT_NAME_MAX_LENGTH = 40
MAX_SALE_QUANTITY = 99
QUANTITY_BUTTONS = {f"×{n}": n for n in range(2, 6)}
MESSAGE_LIMIT = 4000


# ===== ОБЩИЕ ПОМОЩНИКИ =====


def now_local():
    return datetime.now(TIMEZONE)


def now_str():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return now_local().date()


def money(value):
    value = round(value or 0, 2)

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

    if not math.isfinite(value):
        return None

    return value


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
        await update.message.reply_text(
            chunk,
            reply_markup=markup if is_last else None,
        )


# ===== КЛАВИАТУРЫ =====


def keyboard(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def in_pairs(buttons):
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]


def main_menu():
    return keyboard(
        [
            [BTN_SALES, BTN_FINANCE],
            [BTN_EXPENSE, BTN_PURCHASE],
            [BTN_INVENTORY, BTN_REPORTS],
            [BTN_SETTINGS],
        ]
    )


def cancel_menu():
    return keyboard([[BTN_CANCEL]])


def sale_label(name):
    return f"{SALE_PREFIX}{name}"


def base_name(name):
    """«Латте 0.4» → «Латте»: объёмы одного напитка стоят в одном ряду."""
    return SIZE_PATTERN.sub("", name).strip() or name


def sales_rows(products):
    groups = {}

    for _, name, _, _ in products:
        groups.setdefault(base_name(name), []).append(sale_label(name))

    rows = []
    singles = []

    for labels in groups.values():
        if len(labels) == 1:
            singles.append(labels[0])
        else:
            rows += [labels[index : index + 3] for index in range(0, len(labels), 3)]

    return rows + in_pairs(singles)


def sales_menu(products):
    return keyboard(
        sales_rows(products) + [list(QUANTITY_BUTTONS)] + [[BTN_UNDO_SALE, BTN_MAIN]]
    )


def finance_menu():
    periods = list(PERIOD_BUTTONS)

    return keyboard(in_pairs(periods) + [[BTN_MAIN]])


def reports_menu():
    return keyboard(
        [
            [BTN_REPORT_PRODUCTS, BTN_REPORT_WEEK],
            [BTN_HISTORY_SALES, BTN_HISTORY_EXPENSES, BTN_HISTORY_PURCHASES],
            [BTN_MAIN],
        ]
    )


def week_menu():
    return keyboard([[BTN_REPORT_DETAILED], [BTN_BACK_REPORTS, BTN_MAIN]])


def inventory_menu():
    return keyboard([[BTN_STOCKTAKE, BTN_PURCHASE], [BTN_MAIN]])


def ingredients_menu(prefix, ingredients):
    buttons = [f"{prefix}{name}" for _, name, *_ in ingredients]

    return keyboard(in_pairs(buttons) + [[BTN_MAIN]])


def expense_name_menu():
    return keyboard(in_pairs(EXPENSE_PRESETS) + [[BTN_CANCEL]])


def purchase_total_menu():
    return keyboard([[BTN_SAME_PRICE], [BTN_CANCEL]])


def settings_menu():
    return keyboard([[BTN_MENU_MANAGEMENT], [BTN_NOTIFICATIONS], [BTN_MAIN]])


def notifications_menu():
    return keyboard([[BTN_NOTIFY_ON, BTN_NOTIFY_OFF], [BTN_BACK_SETTINGS]])


def menu_management_menu(products):
    buttons = [f"{EDIT_PRODUCT_PREFIX}{name}" for _, name, _, _ in products]

    return keyboard(
        [[BTN_ADD_PRODUCT]] + in_pairs(buttons) + [[BTN_BACK_SETTINGS]]
    )


def product_card_menu():
    return keyboard(
        [
            [BTN_EDIT_NAME, BTN_EDIT_PRICE],
            [BTN_EDIT_COST],
            [BTN_DELETE],
            [BTN_BACK_PRODUCTS],
        ]
    )


def confirm_delete_menu():
    return keyboard([[BTN_CONFIRM_DELETE, BTN_CANCEL]])


# ===== ДАННЫЕ: ТОВАРЫ И СКЛАД =====


def get_products():
    return fetch_all("""
        SELECT id, name, price, cost
        FROM products
        WHERE is_active = 1
        ORDER BY id
        """)


def get_product(product_id):
    return fetch_one(
        """
        SELECT id, name, price, cost
        FROM products
        WHERE id = ? AND is_active = 1
        """,
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


def get_low_stock():
    return fetch_all("""
        SELECT name, unit, stock, minimum_stock
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock ASC
        """)


def change_stock_by_recipe(cursor, product_id, quantity_delta):
    """Списывает (delta > 0) или возвращает (delta < 0) склад по рецепту."""
    cursor.execute(
        "SELECT ingredient_id, quantity FROM recipes WHERE product_id = ?",
        (product_id,),
    )

    for ingredient_id, recipe_quantity in cursor.fetchall():
        amount = recipe_quantity * abs(quantity_delta)

        if quantity_delta > 0:
            # Остаток не уходит в минус: расхождение исправит инвентаризация.
            cursor.execute(
                "UPDATE ingredients SET stock = MAX(stock - ?, 0) WHERE id = ?",
                (amount, ingredient_id),
            )
        else:
            cursor.execute(
                "UPDATE ingredients SET stock = stock + ? WHERE id = ?",
                (amount, ingredient_id),
            )


# ===== ДАННЫЕ: ПРОДАЖИ =====


def record_sale(product_id, quantity=1):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        cursor = connection.cursor()

        cursor.execute(
            "SELECT name, price, cost FROM products WHERE id = ?",
            (product_id,),
        )
        product = cursor.fetchone()

        if product is None:
            return None

        name, price, cost = product

        cursor.execute(
            """
            INSERT INTO sales (product_id, quantity, sale_date, unit_price, unit_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            (product_id, quantity, now_str(), price, cost),
        )
        sale_id = cursor.lastrowid

        change_stock_by_recipe(cursor, product_id, quantity)
        connection.commit()

    return sale_id, name, price


def change_sale_quantity(sale_id, new_quantity):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT s.product_id, s.quantity, s.unit_price, p.name
            FROM sales s
            JOIN products p ON p.id = s.product_id
            WHERE s.id = ?
            """,
            (sale_id,),
        )
        sale = cursor.fetchone()

        if sale is None:
            return None

        product_id, old_quantity, unit_price, name = sale

        cursor.execute(
            "UPDATE sales SET quantity = ? WHERE id = ?",
            (new_quantity, sale_id),
        )
        change_stock_by_recipe(cursor, product_id, new_quantity - old_quantity)
        connection.commit()

    return name, unit_price


def undo_last_sale_today():
    with closing(sqlite3.connect(DB_NAME)) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT s.id, s.product_id, s.quantity, s.unit_price, p.name
            FROM sales s
            JOIN products p ON p.id = s.product_id
            WHERE date(s.sale_date) = ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (today().isoformat(),),
        )
        sale = cursor.fetchone()

        if sale is None:
            return None

        sale_id, product_id, quantity, unit_price, name = sale

        cursor.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
        change_stock_by_recipe(cursor, product_id, -quantity)
        connection.commit()

    return sale_id, name, quantity, unit_price


# ===== ДАННЫЕ: ФИНАНСЫ И ОТЧЁТЫ =====


def period_range(period):
    current = today()

    if period == "yesterday":
        day = current - timedelta(days=1)
        return day, day, f"Вчера ({day:%d.%m})"

    if period == "7days":
        return current - timedelta(days=6), current, "Последние 7 дней"

    if period == "month":
        return current.replace(day=1), current, "Этот месяц"

    if period == "all":
        return date(1900, 1, 1), current, "Всё время"

    return current, current, f"Сегодня ({current:%d.%m})"


def get_finances(start, end):
    """Возвращает (кол-во, выручка, себестоимость, расходы) за период."""
    quantity, revenue, cost = fetch_one(
        """
        SELECT
            COALESCE(SUM(quantity), 0),
            COALESCE(SUM(quantity * unit_price), 0),
            COALESCE(SUM(quantity * unit_cost), 0)
        FROM sales
        WHERE date(sale_date) BETWEEN ? AND ?
        """,
        (start.isoformat(), end.isoformat()),
    )

    expenses = fetch_one(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE date(expense_date) BETWEEN ? AND ?
        """,
        (start.isoformat(), end.isoformat()),
    )[0]

    return quantity, revenue, cost, expenses


def get_product_sales(start, end):
    return fetch_all(
        """
        SELECT
            p.name,
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0)
        FROM products p
        LEFT JOIN sales s
            ON s.product_id = p.id
            AND date(s.sale_date) BETWEEN ? AND ?
        WHERE p.is_active = 1 OR s.id IS NOT NULL
        GROUP BY p.id, p.name
        ORDER BY COALESCE(SUM(s.quantity), 0) DESC
        """,
        (start.isoformat(), end.isoformat()),
    )


def get_expense_breakdown(start, end):
    return fetch_all(
        """
        SELECT name, SUM(amount)
        FROM expenses
        WHERE date(expense_date) BETWEEN ? AND ?
        GROUP BY name
        ORDER BY SUM(amount) DESC
        """,
        (start.isoformat(), end.isoformat()),
    )


def get_product_profitability():
    return fetch_all("""
        SELECT
            name,
            price,
            cost,
            price - cost,
            CASE WHEN price > 0 THEN ((price - cost) / price) * 100 ELSE 0 END
        FROM products
        WHERE is_active = 1
        ORDER BY (price - cost) DESC
        """)


def format_change(current, previous):
    if previous == 0:
        return "— нет данных за прошлую неделю"

    change = ((current - previous) / previous) * 100

    if change > 0:
        return f"📈 +{change:.1f}%"

    if change < 0:
        return f"📉 {change:.1f}%"

    return "➖ 0.0%"


def get_week_data():
    current = today()
    start = current - timedelta(days=6)
    previous_start = current - timedelta(days=13)
    previous_end = current - timedelta(days=7)

    quantity, revenue, cost, expenses = get_finances(start, current)
    prev_quantity, prev_revenue, _, prev_expenses = get_finances(
        previous_start, previous_end
    )

    product_sales = get_product_sales(start, current)

    return {
        "quantity": quantity,
        "revenue": revenue,
        "cost": cost,
        "gross_profit": revenue - cost,
        "expenses": expenses,
        "net_profit": revenue - cost - expenses,
        "sales_change": format_change(quantity, prev_quantity),
        "revenue_change": format_change(revenue, prev_revenue),
        "expenses_change": format_change(expenses, prev_expenses),
        "product_sales": product_sales,
        "top_products": [item for item in product_sales if item[1] > 0],
        "unsold_products": [name for name, sold, _ in product_sales if sold == 0],
        "expense_breakdown": get_expense_breakdown(start, current),
    }


def get_main_conclusion(week):
    if week["net_profit"] < 0 and week["expense_breakdown"]:
        name, amount = week["expense_breakdown"][0]
        return (
            f"Проверь расход «{name}» на {money(amount)} — "
            "он сильнее всего влияет на результат недели."
        )

    if week["net_profit"] < 0:
        return "Чистая прибыль за неделю отрицательная: расходы выше валовой прибыли."

    if week["top_products"] and week["unsold_products"]:
        return (
            f"Лидер продаж — {week['top_products'][0][0]}. "
            "Есть товары без продаж."
        )

    if week["net_profit"] > 0:
        return "За неделю кофейня в плюсе."

    return "Пока мало данных — продолжайте записывать продажи."


def get_week_action(week):
    if (
        week["net_profit"] < 0
        and week["expenses"] > week["gross_profit"]
        and week["expense_breakdown"]
    ):
        name, amount = week["expense_breakdown"][0]
        return (
            f"🔴 Расходы {money(week['expenses'])}, "
            f"а валовая прибыль {money(week['gross_profit'])}.\n\n"
            f"Основной расход — «{name}» на {money(amount)}.\n\n"
            "💡 Проверь, разовый это расход или регулярный."
        )

    if week["sales_change"].startswith("📉"):
        return (
            "📉 Продаж меньше, чем на прошлой неделе.\n\n"
            "💡 Посмотри, какие товары просели, в «📊 Подробно»."
        )

    if week["expenses_change"].startswith("📈"):
        return (
            "📈 Расходы выросли по сравнению с прошлой неделей.\n\n"
            "💡 Проверь основные статьи расходов."
        )

    if week["top_products"]:
        return (
            f"🏆 Лидер продаж — «{week['top_products'][0][0]}».\n\n"
            "💡 Следи, чтобы для него всегда хватало ингредиентов."
        )

    if week["unsold_products"]:
        return (
            "⚠️ Есть товары без продаж.\n\n"
            "💡 Реши, стоит ли оставлять их в меню."
        )

    return "Добавь больше продаж для более точного анализа."


# ===== ДАННЫЕ: РАСХОДЫ, ЗАКУПКИ, ИНВЕНТАРИЗАЦИЯ =====


def save_expense(name, amount):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        connection.execute(
            "INSERT INTO expenses (name, amount, expense_date) VALUES (?, ?, ?)",
            (name, amount, now_str()),
        )
        connection.commit()


def save_purchase(ingredient_id, quantity, total):
    unit_price = round(total / quantity, 2)

    with closing(sqlite3.connect(DB_NAME)) as connection:
        connection.execute(
            """
            INSERT INTO purchases (ingredient_id, quantity, purchase_price, purchase_date)
            VALUES (?, ?, ?, ?)
            """,
            (ingredient_id, quantity, unit_price, now_str()),
        )
        connection.execute(
            """
            UPDATE ingredients
            SET stock = stock + ?, purchase_price = ?
            WHERE id = ?
            """,
            (quantity, unit_price, ingredient_id),
        )
        connection.commit()

    return unit_price


def save_stocktake(ingredient_id, new_stock):
    with closing(sqlite3.connect(DB_NAME)) as connection:
        cursor = connection.cursor()

        cursor.execute(
            "SELECT stock FROM ingredients WHERE id = ?",
            (ingredient_id,),
        )
        old_stock = cursor.fetchone()[0]

        cursor.execute(
            "UPDATE ingredients SET stock = ? WHERE id = ?",
            (new_stock, ingredient_id),
        )
        cursor.execute(
            """
            INSERT INTO stock_adjustments (ingredient_id, old_stock, new_stock, adjusted_at)
            VALUES (?, ?, ?, ?)
            """,
            (ingredient_id, old_stock, new_stock, now_str()),
        )
        connection.commit()

    return old_stock


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
        "☕ Добро пожаловать в Coffee Manager!\n\n"
        f"{BTN_SALES} — записать продажу в один тап\n"
        f"{BTN_FINANCE} — выручка и прибыль за период\n"
        f"{BTN_EXPENSE} — аренда, зарплата и другие траты\n"
        f"{BTN_PURCHASE} — что купили для склада\n"
        f"{BTN_INVENTORY} — остатки и инвентаризация\n\n"
        f"Каждый вечер в {DAILY_SUMMARY_TIME} пришлю сводку за день.",
        main_menu(),
    )


# ===== ПРОДАЖИ =====


async def show_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()

    if not products:
        await send(
            update,
            "☕ В меню пока нет товаров.\n\n"
            f"Добавьте их: {BTN_SETTINGS} → {BTN_MENU_MANAGEMENT}.",
            main_menu(),
        )
        return

    context.user_data["mode"] = "sales"

    await send(
        update,
        "☕ Продажа\n\n"
        f"{price_list(products)}\n"
        "Нажмите на товар — запишется 1 шт.\n"
        "Взяли несколько — сразу после этого нажмите ×2…×5 "
        "или отправьте число.\n"
        f"Ошиблись — «{BTN_UNDO_SALE}».",
        sales_menu(products),
    )


def price_list(products):
    groups = {}

    for _, name, price, _ in products:
        groups.setdefault(base_name(name), []).append((name, price))

    lines = []

    for base, items in groups.items():
        if len(items) == 1:
            lines.append(f"• {items[0][0]} — {money(items[0][1])}")
        else:
            sizes = " / ".join(
                f"{name[len(base):].strip()} — {money(price)}" for name, price in items
            )
            lines.append(f"• {base}: {sizes}")

    return "\n".join(lines) + "\n"


def find_product_by_label(text):
    if not text.startswith(SALE_PREFIX):
        return None

    for product in get_products():
        product_id, name, price, cost = product

        # Старые кнопки были с ценой («☕ Латте · 250 ₽») — их тоже понимаем.
        if text == sale_label(name) or text.startswith(
            f"{SALE_PREFIX}{name}{SALE_SEPARATOR}"
        ):
            return product

    return None


def today_sales_line():
    quantity, revenue, _, _ = get_finances(today(), today())

    return f"📊 Всего за сегодня: {quantity} шт. · {money(revenue)}"


async def sell_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product):
    result = record_sale(product[0])

    if result is None:
        await send(update, "❌ Товар не найден.", main_menu())
        return

    sale_id, name, price = result

    context.user_data["mode"] = "sales"
    context.user_data["last_sale_id"] = sale_id

    await send(
        update,
        f"✅ Записано: {name} × 1 — {money(price)}\n"
        "Взяли больше? Нажмите ×2…×5 или отправьте число — "
        "количество заменится, а не добавится.\n\n"
        f"{today_sales_line()}",
        sales_menu(get_products()),
    )


async def set_last_sale_quantity(update: Update, context, quantity):
    result = change_sale_quantity(context.user_data["last_sale_id"], quantity)

    if result is None:
        context.user_data.pop("last_sale_id", None)
        await send(update, "Эта продажа уже отменена.", sales_menu(get_products()))
        return

    name, unit_price = result

    await send(
        update,
        f"✏️ Исправлено: {name} × {quantity} — {money(unit_price * quantity)}\n"
        "(количество заменено, а не добавлено)\n\n"
        f"{today_sales_line()}",
        sales_menu(get_products()),
    )


async def undo_sale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = undo_last_sale_today()
    products = get_products()
    markup = sales_menu(products) if products else main_menu()

    if result is None:
        await send(update, "Сегодня ещё нет продаж для отмены.", markup)
        return

    sale_id, name, quantity, unit_price = result

    if context.user_data.get("last_sale_id") == sale_id:
        context.user_data.pop("last_sale_id", None)

    context.user_data["mode"] = "sales"

    await send(
        update,
        f"↩️ Отменено: {name} × {quantity} ({money(unit_price * quantity)})\n"
        "Склад возвращён.\n"
        f"{today_sales_line()}",
        markup,
    )


async def sales_history(update: Update):
    sales = fetch_all(
        """
        SELECT p.name, s.quantity, s.quantity * s.unit_price, s.sale_date
        FROM sales s
        JOIN products p ON s.product_id = p.id
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (HISTORY_LIMIT,),
    )

    if not sales:
        await send(update, "🧾 Продаж пока нет.", reports_menu())
        return

    message = f"🧾 Последние продажи ({len(sales)})\n\n"

    for name, quantity, total, sale_date in sales:
        message += f"{format_dt(sale_date)} · {name} × {quantity} · {money(total)}\n"

    await send(update, message, reports_menu())


# ===== ФИНАНСЫ =====


async def show_finances(update: Update, period):
    start, end, label = period_range(period)
    quantity, revenue, cost, expenses = get_finances(start, end)

    profit = revenue - cost
    net_profit = profit - expenses
    margin = (profit / revenue) * 100 if revenue > 0 else 0

    await send(
        update,
        f"💰 Финансы · {label}\n\n"
        f"☕ Продано: {quantity} шт.\n"
        f"💵 Выручка: {money(revenue)}\n"
        f"📦 Себестоимость: {money(cost)}\n"
        f"💰 Валовая прибыль: {money(profit)}\n"
        f"💸 Расходы: {money(expenses)}\n"
        f"💵 Чистая прибыль: {money(net_profit)}\n"
        f"📊 Маржа: {margin:.1f}%",
        finance_menu(),
    )


# ===== РАСХОДЫ =====


async def start_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["step"] = "expense_name"

    await send(
        update,
        "💸 Новый расход\n\nВыберите или напишите, на что потратили:",
        expense_name_menu(),
    )


async def expense_history(update: Update):
    expenses = fetch_all(
        """
        SELECT name, amount, expense_date
        FROM expenses
        ORDER BY id DESC
        LIMIT ?
        """,
        (HISTORY_LIMIT,),
    )

    if not expenses:
        await send(update, "🧾 Расходов пока нет.", reports_menu())
        return

    message = f"🧾 Последние расходы ({len(expenses)})\n\n"

    for name, amount, expense_date in expenses:
        message += f"{format_dt(expense_date)} · {name} · {money(amount)}\n"

    await send(update, message, reports_menu())


# ===== ЗАКУПКИ И СКЛАД =====


def low_stock_text():
    low_stock = get_low_stock()

    if not low_stock:
        return ""

    text = "⚠️ Заканчивается:\n"

    for name, unit, stock, minimum_stock in low_stock:
        text += f"• {name}: {qty(stock)} {unit} (минимум {qty(minimum_stock)})\n"

    return text + "\n"


async def show_purchase_menu(update: Update):
    ingredients = get_ingredients()

    if not ingredients:
        await send(
            update,
            "📦 Склад ещё не настроен — нет ингредиентов.",
            main_menu(),
        )
        return

    await send(
        update,
        f"🛒 Закупка\n\n{low_stock_text()}Выберите, что купили:",
        ingredients_menu(PURCHASE_PREFIX, ingredients),
    )


async def show_inventory(update: Update):
    ingredients = get_ingredients()

    if not ingredients:
        await send(update, "📦 Склад пока пуст.", main_menu())
        return

    message = "📦 Склад\n\n"

    for _, name, unit, stock, minimum_stock, _ in ingredients:
        status = "⚠️" if stock <= minimum_stock else "✅"
        message += f"{status} {name}: {qty(stock)} {unit} (мин. {qty(minimum_stock)})\n"

    message += f"\nЕсли остаток не совпадает с реальным — «{BTN_STOCKTAKE}»."

    await send(update, message, inventory_menu())


async def show_stocktake_menu(update: Update):
    ingredients = get_ingredients()

    if not ingredients:
        await send(update, "📦 Склад пока пуст.", main_menu())
        return

    await send(
        update,
        "📝 Инвентаризация\n\n"
        "Выберите ингредиент и введите, сколько его есть на самом деле:",
        ingredients_menu(STOCKTAKE_PREFIX, ingredients),
    )


def find_ingredient_by_label(text, prefix):
    if not text.startswith(prefix):
        return None

    for ingredient in get_ingredients():
        if text == f"{prefix}{ingredient[1]}":
            return ingredient

    return None


async def purchase_history(update: Update):
    purchases = fetch_all(
        """
        SELECT i.name, p.quantity, i.unit, p.purchase_price, p.purchase_date
        FROM purchases p
        JOIN ingredients i ON p.ingredient_id = i.id
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (HISTORY_LIMIT,),
    )

    if not purchases:
        await send(update, "🧾 Закупок пока нет.", reports_menu())
        return

    message = f"🧾 Последние закупки ({len(purchases)})\n\n"

    for name, quantity, unit, unit_price, purchase_date in purchases:
        message += (
            f"{format_dt(purchase_date)} · {name} {qty(quantity)} {unit} · "
            f"{money(quantity * unit_price)}\n"
        )

    await send(update, message, reports_menu())


# ===== ОТЧЁТЫ =====


async def product_report(update: Update):
    rows = fetch_all("""
        SELECT
            p.name,
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0)
        FROM products p
        LEFT JOIN sales s ON s.product_id = p.id
        WHERE p.is_active = 1 OR s.id IS NOT NULL
        GROUP BY p.id
        ORDER BY COALESCE(SUM(s.quantity), 0) DESC
        """)

    if not rows:
        await send(update, "📈 Пока нет товаров.", reports_menu())
        return

    message = "📈 Отчёт по товарам · всё время\n\n"

    for position, (name, quantity, revenue, cost) in enumerate(rows, start=1):
        profit = revenue - cost
        margin = (profit / revenue) * 100 if revenue > 0 else 0

        message += (
            f"{position}. {name}\n"
            f"   {quantity} шт. · выручка {money(revenue)} · "
            f"прибыль {money(profit)} ({margin:.0f}%)\n"
        )

    await send(update, message, reports_menu())


async def week_summary(update: Update):
    week = get_week_data()

    await send(
        update,
        "🧠 Итоги недели\n\n"
        f"💵 Выручка: {money(week['revenue'])} ({week['revenue_change']})\n"
        f"💰 Чистая прибыль: {money(week['net_profit'])}\n\n"
        f"🎯 Что сделать:\n{get_week_action(week)}",
        week_menu(),
    )


async def detailed_week_report(update: Update):
    week = get_week_data()
    profitability = get_product_profitability()

    message = (
        "📊 Подробно · последние 7 дней\n\n"
        "Сравнение с прошлой неделей:\n"
        f"☕ Продажи: {week['sales_change']}\n"
        f"💰 Выручка: {week['revenue_change']}\n"
        f"💸 Расходы: {week['expenses_change']}\n\n"
        f"☕ Продано: {week['quantity']} шт.\n"
        f"💵 Выручка: {money(week['revenue'])}\n"
        f"📦 Себестоимость: {money(week['cost'])}\n"
        f"📈 Валовая прибыль: {money(week['gross_profit'])}\n"
        f"💸 Расходы: {money(week['expenses'])}\n"
        f"💵 Чистая прибыль: {money(week['net_profit'])}\n\n"
        "🏆 Топ продаж:\n"
    )

    if week["top_products"]:
        for index, (name, sold, revenue) in enumerate(week["top_products"][:5], start=1):
            message += f"{index}. {name} — {sold} шт. / {money(revenue)}\n"
    else:
        message += "Нет продаж за неделю.\n"

    message += "\n💰 Прибыль с одной продажи:\n"

    for name, _, _, profit, margin in profitability:
        message += f"• {name}: {money(profit)} ({margin:.0f}%)\n"

    if week["unsold_products"]:
        message += "\n⚠️ Без продаж за неделю:\n"
        for name in week["unsold_products"]:
            message += f"• {name}\n"

    message += "\n💸 Расходы:\n"

    if week["expense_breakdown"]:
        for name, amount in week["expense_breakdown"]:
            message += f"• {name}: {money(amount)}\n"
    else:
        message += "Нет расходов за неделю.\n"

    stock_text = low_stock_text()
    message += f"\n{stock_text}" if stock_text else "\n✅ Запасов хватает.\n"

    message += f"\n🧠 Главный вывод:\n{get_main_conclusion(week)}"

    await send(update, message, week_menu())


# ===== УПРАВЛЕНИЕ МЕНЮ =====


def get_product_stats(product_id):
    sold_quantity = fetch_one(
        "SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE product_id = ?",
        (product_id,),
    )[0]

    has_recipe = (
        fetch_one(
            "SELECT COUNT(*) FROM recipes WHERE product_id = ?",
            (product_id,),
        )[0]
        > 0
    )

    return sold_quantity, has_recipe


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

        cursor.execute(
            "SELECT COUNT(*) FROM sales WHERE product_id = ?",
            (product_id,),
        )
        has_sales = cursor.fetchone()[0] > 0

        if has_sales:
            cursor.execute(
                "UPDATE products SET is_active = 0 WHERE id = ?",
                (product_id,),
            )
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

    if SALE_SEPARATOR.strip() in name:
        return f"❌ Название не должно содержать «{SALE_SEPARATOR.strip()}»."

    if len(name) > PRODUCT_NAME_MAX_LENGTH:
        return (
            "❌ Название слишком длинное. "
            f"Максимум {PRODUCT_NAME_MAX_LENGTH} символов."
        )

    for product_id, product_name, _, _ in get_products():
        if product_id != exclude_product_id and (
            product_name.casefold() == name.casefold()
        ):
            return "❌ Товар с таким названием уже есть в меню."

    return None


async def show_menu_management(update: Update):
    products = get_products()

    message = "☕ Меню и цены\n\n"

    if products:
        for _, name, price, cost in products:
            message += f"• {name}: {money(price)} (себестоимость {money(cost)})\n"
        message += "\nВыберите товар или добавьте новый:"
    else:
        message += "В меню пока нет товаров.\nДобавьте первый товар:"

    await send(update, message, menu_management_menu(products))


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update)
        return

    product_id, name, price, cost = product
    sold_quantity, has_recipe = get_product_stats(product_id)

    profit = price - cost
    margin = (profit / price) * 100 if price > 0 else 0

    if has_recipe:
        recipe_status = "✅ Рецепт задан — склад списывается."
    else:
        recipe_status = "⚠️ Рецепт не задан — склад не списывается."

    await send(
        update,
        f"☕ {name}\n\n"
        f"💵 Цена: {money(price)}\n"
        f"📦 Себестоимость: {money(cost)}\n"
        f"💰 Прибыль с продажи: {money(profit)} ({margin:.1f}% маржа)\n"
        f"📊 Продано всего: {sold_quantity} шт.\n\n"
        f"{recipe_status}",
        product_card_menu(),
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

        await send(
            update,
            f"☕ {text}\n\nВведите цену продажи, например: 250",
            cancel_menu(),
        )
        return

    if step == "confirm_delete":
        await send(
            update,
            f"Нажмите «{BTN_CONFIRM_DELETE}» или «{BTN_CANCEL}».",
            confirm_delete_menu(),
        )
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

    # Остальные шаги — ввод цены или себестоимости.
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
            "📦 Введите себестоимость одной порции, например: 60\n"
            "Если не знаете — введите 0.",
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
            "⚠️ Рецепт для него не задан — склад при продаже не списывается.",
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


# ===== УВЕДОМЛЕНИЯ, СВОДКА, БЭКАПЫ =====


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

    # По умолчанию сводка включена: владелец не должен её искать.
    return True if result is None else bool(result[0])


def notifications_text(user_id):
    if get_notifications_status(user_id):
        status = f"🔔 Включены — сводка каждый день в {DAILY_SUMMARY_TIME}."
    else:
        status = "🔕 Выключены."

    return f"🔔 Уведомления\n\n{status}"


def build_day_summary(day):
    quantity, revenue, cost, expenses = get_finances(day, day)
    gross_profit = revenue - cost
    net_profit = gross_profit - expenses

    message = (
        f"📊 Итоги дня · {day:%d.%m}\n\n"
        f"☕ Продано: {quantity} шт.\n"
        f"💵 Выручка: {money(revenue)}\n"
        f"📈 Валовая прибыль: {money(gross_profit)}\n"
        f"💸 Расходы: {money(expenses)}\n"
        f"💰 Чистая прибыль: {money(net_profit)}\n"
    )

    top = [item for item in get_product_sales(day, day) if item[1] > 0][:3]

    if top:
        message += "\n🏆 Топ дня:\n"
        for name, sold, product_revenue in top:
            message += f"• {name} — {sold} шт. / {money(product_revenue)}\n"

    stock_text = low_stock_text()

    if stock_text:
        message += f"\n{stock_text}"

    return message.strip()


async def send_daily_notification(context: ContextTypes.DEFAULT_TYPE):
    text = build_day_summary(today())

    for user_id in ADMIN_IDS:
        if not get_notifications_status(user_id):
            continue

        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.exception("Не удалось отправить сводку пользователю %s", user_id)


def backup_database():
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"coffee_manager_{today():%Y-%m-%d}.db"

    with closing(sqlite3.connect(DB_NAME)) as source, closing(
        sqlite3.connect(target)
    ) as destination:
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


async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        backup_database()
    except Exception:
        logger.exception("Не удалось сделать бэкап базы")


# ===== МАРШРУТИЗАЦИЯ СООБЩЕНИЙ =====


async def handle_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    """Обрабатывает ввод внутри расхода, закупки или инвентаризации."""
    data = context.user_data
    step = data.get("step")

    if step == "expense_name":
        if len(text) > PRODUCT_NAME_MAX_LENGTH:
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

        save_expense(data["expense_name"], amount)
        name = data["expense_name"]
        data.clear()
        await send(update, f"✅ Расход сохранён: {name} — {money(amount)}", main_menu())
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

        unit_price = save_purchase(ingredient_id, quantity, total)
        data.clear()

        await send(
            update,
            f"✅ Закупка: {name} +{qty(quantity)} {unit} за {money(total)} "
            f"({money(unit_price)} за {unit})\n"
            f"📦 Теперь на складе: {qty(stock + quantity)} {unit}",
            main_menu(),
        )
        return

    if step == "stocktake_value":
        new_stock = parse_number(text)

        if new_stock is None or new_stock < 0:
            await send(update, "❌ Введите остаток числом, например: 3.5", cancel_menu())
            return

        old_stock = save_stocktake(ingredient_id, new_stock)
        data.clear()
        difference = new_stock - old_stock
        sign = "+" if difference > 0 else ""

        await send(
            update,
            f"✅ {name}: было {qty(old_stock)} {unit} → стало {qty(new_stock)} {unit} "
            f"({sign}{qty(round(difference, 3))})",
        )
        await show_stocktake_menu(update)
        return

    data.clear()
    await send(update, "Главное меню:", main_menu())


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data
    user_id = update.effective_user.id

    # ===== НАВИГАЦИЯ =====

    if text == BTN_MAIN:
        data.clear()
        await send(update, "Главное меню:", main_menu())
        return

    if text == BTN_SALES:
        data.clear()
        await show_sales(update, context)
        return

    if text == BTN_FINANCE:
        data.clear()
        await send(update, "💰 Выберите период:", finance_menu())
        return

    if text in PERIOD_BUTTONS:
        data.clear()
        await show_finances(update, PERIOD_BUTTONS[text])
        return

    if text == BTN_EXPENSE:
        data.clear()
        await start_expense(update, context)
        return

    if text == BTN_PURCHASE:
        data.clear()
        await show_purchase_menu(update)
        return

    if text == BTN_INVENTORY:
        data.clear()
        await show_inventory(update)
        return

    if text == BTN_STOCKTAKE:
        data.clear()
        await show_stocktake_menu(update)
        return

    if text in (BTN_REPORTS, BTN_BACK_REPORTS):
        data.clear()
        await send(update, "📊 Отчёты", reports_menu())
        return

    report_actions = {
        BTN_REPORT_PRODUCTS: product_report,
        BTN_REPORT_WEEK: week_summary,
        BTN_REPORT_DETAILED: detailed_week_report,
        BTN_HISTORY_SALES: sales_history,
        BTN_HISTORY_EXPENSES: expense_history,
        BTN_HISTORY_PURCHASES: purchase_history,
    }

    if text in report_actions:
        data.clear()
        await report_actions[text](update)
        return

    if text in (BTN_SETTINGS, BTN_BACK_SETTINGS):
        data.clear()
        await send(update, "⚙️ Настройки", settings_menu())
        return

    if text == BTN_NOTIFICATIONS:
        await send(update, notifications_text(user_id), notifications_menu())
        return

    if text in (BTN_NOTIFY_ON, BTN_NOTIFY_OFF):
        set_notifications(user_id, text == BTN_NOTIFY_ON)
        await send(update, notifications_text(user_id), notifications_menu())
        return

    if text == BTN_UNDO_SALE:
        await undo_sale(update, context)
        return

    # ===== УПРАВЛЕНИЕ МЕНЮ =====

    if text in (BTN_MENU_MANAGEMENT, BTN_BACK_PRODUCTS):
        data.clear()
        await show_menu_management(update)
        return

    if text == BTN_ADD_PRODUCT:
        data.clear()
        data["product_step"] = "add_name"
        await send(update, "➕ Новый товар\n\nВведите название, например: Раф", cancel_menu())
        return

    if text == BTN_CANCEL:
        if "product_id" in data:
            data.pop("product_step", None)
            await show_product_card(update, context)
        elif "product_step" in data:
            data.clear()
            await show_menu_management(update)
        else:
            data.clear()
            await send(update, "Отменено. Главное меню:", main_menu())
        return

    if text in PRODUCT_EDIT_STEPS and "product_id" in data:
        await start_product_edit(update, context, PRODUCT_EDIT_STEPS[text])
        return

    if text == BTN_CONFIRM_DELETE and data.get("product_step") == "confirm_delete":
        product = get_product(data.get("product_id"))
        data.clear()

        if product is not None:
            if delete_product(product[0]) == "archived":
                await send(
                    update,
                    f"🗑 «{product[1]}» скрыт из меню.\nИстория продаж и отчёты сохранены.",
                )
            else:
                await send(update, f"🗑 «{product[1]}» удалён.")

        await show_menu_management(update)
        return

    # ===== ВВОД ДАННЫХ =====

    if "product_step" in data:
        await handle_product_input(update, context)
        return

    if "step" in data:
        await handle_step(update, context, text)
        return

    # Сразу после продажи число = сколько штук продали (заменяет, не добавляет).
    quantity_button_pressed = text in QUANTITY_BUTTONS

    if quantity_button_pressed:
        text = str(QUANTITY_BUTTONS[text])

    if data.get("last_sale_id") and text.isdigit():
        quantity = int(text)

        if 1 <= quantity <= MAX_SALE_QUANTITY:
            await set_last_sale_quantity(update, context, quantity)
        else:
            await send(update, f"❌ Количество от 1 до {MAX_SALE_QUANTITY}.")
        return

    if text.isdigit() and (data.get("mode") == "sales" or quantity_button_pressed):
        await send(
            update,
            "Сначала нажмите товар, потом отправьте количество.",
            sales_menu(get_products()),
        )
        return

    # ===== КНОПКИ ТОВАРОВ И ИНГРЕДИЕНТОВ =====

    product = find_product_by_label(text)

    if product is not None:
        await sell_product(update, context, product)
        return

    if text.startswith(EDIT_PRODUCT_PREFIX):
        for product_id, name, _, _ in get_products():
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
        await send(
            update,
            f"🛒 {ingredient[1]}\n\nСколько купили ({ingredient[2]})?",
            cancel_menu(),
        )
        return

    ingredient = find_ingredient_by_label(text, STOCKTAKE_PREFIX)

    if ingredient is not None:
        data.clear()
        data["ingredient_id"] = ingredient[0]
        data["step"] = "stocktake_value"
        await send(
            update,
            f"📝 {ingredient[1]}\n\n"
            f"По учёту: {qty(ingredient[3])} {ingredient[2]}.\n"
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


def parse_summary_time():
    hours, minutes = DAILY_SUMMARY_TIME.split(":")

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

    application.job_queue.run_daily(send_daily_notification, time=parse_summary_time())
    application.job_queue.run_daily(
        backup_job,
        time=time(hour=4, minute=0, tzinfo=TIMEZONE),
    )

    logger.info(
        "Coffee Manager запущен. Часовой пояс: %s, сводка в %s",
        TIMEZONE,
        DAILY_SUMMARY_TIME,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
