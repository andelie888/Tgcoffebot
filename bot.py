import json
import math
import sqlite3

from datetime import time

from telegram import Update, ReplyKeyboardMarkup

from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from config import ADMIN_IDS, BOT_TOKEN, DB_NAME
from database import create_tables, migrate_database


def ai_analysis_menu():
    return ReplyKeyboardMarkup(
        [
            ["📊 Детальный анализ"],
            ["⬅️ Назад в меню"],
        ],
        resize_keyboard=True,
    )


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["📊 Продажи", "💰 Финансы"],
            ["💸 Расходы"],
            ["💳 История расходов"],
            ["📋 История", "🏆 Лучшие продажи"],
            ["📈 Отчёт по товарам", "🧾 Отчёты"],
            ["📦 Склад", "🛒 Закупки"],
            ["📋 История закупок", "🧠 AI-анализ"],
            ["⚙️ Настройки"],
        ],
        resize_keyboard=True,
    )


def get_products():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, name, price, cost
        FROM products
        WHERE is_active = 1
        ORDER BY id
        """)

    products = cursor.fetchall()

    connection.close()

    return products


def get_ingredients():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, name, unit, stock, minimum_stock
        FROM ingredients
        ORDER BY id
        """)

    ingredients = cursor.fetchall()

    connection.close()

    return ingredients


def get_low_stock():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name, unit, stock, minimum_stock
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock ASC
        """)

    low_stock = cursor.fetchall()

    connection.close()

    return low_stock


def deduct_inventory(cursor, product_id, quantity):
    cursor.execute(
        """
        SELECT ingredient_id, quantity
        FROM recipes
        WHERE product_id = ?
        """,
        (product_id,),
    )

    recipes = cursor.fetchall()

    for ingredient_id, recipe_quantity in recipes:
        total_needed = recipe_quantity * quantity

        cursor.execute(
            """
            UPDATE ingredients
            SET stock = stock - ?
            WHERE id = ?
            """,
            (total_needed, ingredient_id),
        )


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user is None or user.id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("⛔ Нет доступа.")
        raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "☕ Добро пожаловать в Coffee Manager!\n\n"
        "Ваш помощник для управления кофейней.\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_menu(),
    )


async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ingredients = get_ingredients()

    if not ingredients:
        await update.message.reply_text("📦 Склад пока пуст.")
        return

    message = "📦 Текущий склад\n\n"

    for ingredient_id, name, unit, stock, minimum_stock in ingredients:
        if stock <= minimum_stock:
            status = "⚠️ Мало"
        else:
            status = "✅ Норма"

        message += (
            f"{status} {name}\n"
            f"   Остаток: {stock:g} {unit}\n"
            f"   Минимум: {minimum_stock:g} {unit}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def add_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    name = context.user_data["expense_name"]
    amount = context.user_data["expense_amount"]

    cursor.execute(
        """
        INSERT INTO expenses (
            name,
            amount,
            expense_date
        )
        VALUES (?, ?, datetime('now'))
        """,
        (
            name,
            amount,
        ),
    )

    connection.commit()
    connection.close()

    context.user_data.pop("expense_name", None)
    context.user_data.pop("expense_amount", None)

    await update.message.reply_text(
        "✅ Расход сохранён.\n" f"💸 Сумма: ${amount:.2f}",
        reply_markup=main_menu(),
    )


async def expense_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name, amount, expense_date
        FROM expenses
        ORDER BY id DESC
        """)

    expenses = cursor.fetchall()
    connection.close()

    if not expenses:
        await update.message.reply_text(
            "📋 История расходов пока пуста.",
            reply_markup=main_menu(),
        )
        return

    message = "📋 История расходов\n\n"

    for name, amount, expense_date in expenses:
        message += (
            f"💸 {name}\n" f"   Сумма: ${amount:.2f}\n" f"   Дата: {expense_date}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def purchase_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            ingredients.name,
            purchases.quantity,
            ingredients.unit,
            purchases.purchase_price,
            purchases.purchase_date
        FROM purchases
        JOIN ingredients
            ON purchases.ingredient_id = ingredients.id
        ORDER BY purchases.id DESC
        """)

    purchases = cursor.fetchall()
    connection.close()

    if not purchases:
        await update.message.reply_text(
            "📋 История закупок пока пуста.",
            reply_markup=main_menu(),
        )
        return

    message = "📋 История закупок\n\n"

    for name, quantity, unit, purchase_price, purchase_date in purchases:
        total = quantity * purchase_price

        message += (
            f"🛒 {name}\n"
            f"   Количество: {quantity:g} {unit}\n"
            f"   Цена: ${purchase_price:.2f}\n"
            f"   Сумма: ${total:.2f}\n"
            f"   Дата: {purchase_date}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def add_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    ingredient_id = context.user_data["purchase_ingredient_id"]
    quantity = context.user_data["purchase_quantity"]

    cursor.execute(
        "SELECT purchase_price FROM ingredients WHERE id = ?",
        (ingredient_id,),
    )

    result = cursor.fetchone()

    if result is None:
        connection.close()
        await update.message.reply_text(
            "❌ Ингредиент не найден.",
            reply_markup=main_menu(),
        )
        return

    purchase_price = result[0]

    cursor.execute(
        """
        INSERT INTO purchases (
            ingredient_id,
            quantity,
            purchase_price,
            purchase_date
        )
        VALUES (?, ?, ?, datetime('now'))
        """,
        (
            ingredient_id,
            quantity,
            purchase_price,
        ),
    )

    cursor.execute(
        """
        UPDATE ingredients
        SET stock = stock + ?
        WHERE id = ?
        """,
        (
            quantity,
            ingredient_id,
        ),
    )

    connection.commit()
    connection.close()

    context.user_data.pop("purchase_ingredient_id", None)
    context.user_data.pop("purchase_quantity", None)

    await update.message.reply_text(
        "✅ Закупка сохранена.\n" f"📦 Добавлено на склад: {quantity:g}",
        reply_markup=main_menu(),
    )


async def expense_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_expense_name"] = True

    await update.message.reply_text(
        "💸 Добавление расхода\n\n"
        "Введите название расхода:\n"
        "Например: аренда, зарплата, электричество",
        reply_markup=ReplyKeyboardMarkup(
            [["🔙 Главное меню"]],
            resize_keyboard=True,
        ),
    )


async def purchase_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ingredients = get_ingredients()

    if not ingredients:
        await update.message.reply_text(
            "📦 В базе нет ингредиентов.",
            reply_markup=main_menu(),
        )
        return

    buttons = []

    for ingredient_id, name, unit, stock, minimum_stock in ingredients:
        buttons.append([f"🛒 {name}"])

    buttons.append(["🔙 Главное меню"])

    await update.message.reply_text(
        "🛒 Выберите ингредиент для закупки:",
        reply_markup=ReplyKeyboardMarkup(
            buttons,
            resize_keyboard=True,
        ),
    )


async def show_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    low_stock = get_low_stock()

    if not low_stock:
        await update.message.reply_text(
            "🛒 Закупки\n\n" "✅ Пока ничего докупать не нужно.",
            reply_markup=main_menu(),
        )
        return

    message = "🛒 Нужно заказать\n\n"

    for name, unit, stock, minimum_stock in low_stock:
        needed = minimum_stock - stock

        message += (
            f"⚠️ {name}\n"
            f"   Остаток: {stock:g} {unit}\n"
            f"   Минимум: {minimum_stock:g} {unit}\n"
            f"   Нужно докупить минимум: {needed:g} {unit}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def show_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()

    if not products:
        await update.message.reply_text("📊 Пока в базе нет товаров.")
        return

    buttons = []

    for product_id, name, price, cost in products:
        buttons.append([f"☕ {name} — ${price:.2f}"])

    buttons.append(["🔙 Главное меню"])

    keyboard = ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "📊 Выберите товар для продажи:",
        reply_markup=keyboard,
    )


async def save_sale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "selected_product_id" not in context.user_data:
        return False

    try:
        quantity = int(text)
    except ValueError:
        await update.message.reply_text("❗ Введите количество числом.\nНапример: 2")
        return True

    if quantity <= 0:
        await update.message.reply_text("❗ Количество должно быть больше нуля.")
        return True

    product_id = context.user_data["selected_product_id"]
    product_name = context.user_data["selected_product_name"]

    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT price, cost FROM products WHERE id = ?",
        (product_id,),
    )

    result = cursor.fetchone()

    if result is None:
        connection.close()
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Товар не найден.",
            reply_markup=main_menu(),
        )
        return True

    unit_price, unit_cost = result

    cursor.execute(
        """
        INSERT INTO sales (product_id, quantity, sale_date, unit_price, unit_cost)
        VALUES (?, ?, datetime('now'), ?, ?)
        """,
        (product_id, quantity, unit_price, unit_cost),
    )

    deduct_inventory(cursor, product_id, quantity)

    connection.commit()
    connection.close()

    await update.message.reply_text(
        f"✅ Продажа записана!\n\n" f"☕ {product_name}\n" f"Количество: {quantity} шт."
    )

    context.user_data.clear()

    return True


async def sales_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            p.name,
            s.quantity,
            s.unit_price,
            s.quantity * s.unit_price,
            s.sale_date
        FROM sales s
        JOIN products p ON s.product_id = p.id
        ORDER BY s.id DESC
        LIMIT 10
        """)

    sales = cursor.fetchall()

    connection.close()

    if not sales:
        await update.message.reply_text(
            "📋 История продаж пока пуста.",
            reply_markup=main_menu(),
        )
        return

    message = "📋 Последние продажи\n\n"

    for name, quantity, price, total, sale_date in sales:
        message += (
            f"☕ {name}\n"
            f"   Количество: {quantity} шт.\n"
            f"   Сумма: ${total:.2f}\n"
            f"   Дата: {sale_date}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["📅 Сегодня", "📅 Вчера"],
        ["📅 7 дней", "📅 Этот месяц"],
        ["📅 Всё время"],
        ["🔙 Главное меню"],
    ]

    await update.message.reply_text(
        "💰 Выберите период:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
        ),
    )


def get_today_finances():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0),
            COALESCE(SUM(s.quantity), 0)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date) = date('now')
        """)

    revenue, cost, quantity = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE date(expense_date) = date('now')
        """)

    expenses = cursor.fetchone()[0]

    connection.close()

    gross_profit = revenue - cost
    net_profit = gross_profit - expenses

    return quantity, revenue, cost, gross_profit, expenses, net_profit


async def show_finances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    period = context.user_data.get("finance_period", "today")

    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0),
            COALESCE(SUM(s.quantity), 0)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date) >= CASE
            WHEN ? = 'yesterday'
                THEN date('now', '-1 day')
            WHEN ? = '7days'
                THEN date('now', '-6 days')
            WHEN ? = 'month'
                THEN date('now', 'start of month')
            ELSE CASE
                WHEN ? = 'all' THEN '1900-01-01'
                ELSE date('now')
            END 
        END
        AND date(s.sale_date) <= CASE
            WHEN ? = 'yesterday'
                THEN date('now', '-1 day')
            ELSE date('now')
        END
        """,
        (period, period, period, period, period),
    )

    revenue, cost, quantity = cursor.fetchone()
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE date(expense_date) >= CASE
            WHEN ? = 'yesterday'
                THEN date('now', '-1 day')
            WHEN ? = '7days'
                THEN date('now', '-6 days')
            WHEN ? = 'month'
                THEN date('now', 'start of month')
            ELSE CASE
                WHEN ? = 'all' THEN '1900-01-01'
                ELSE date('now')
            END
        END
        AND date(expense_date) <= CASE
            WHEN ? = 'yesterday'
                THEN date('now', '-1 day')
            ELSE date('now')
        END
        """,
        (period, period, period, period, period),
    )

    expenses = cursor.fetchone()[0]

    connection.close()

    profit = revenue - cost
    net_profit = profit - expenses

    if revenue > 0:
        margin = (profit / revenue) * 100
    else:
        margin = 0

    await update.message.reply_text(
        "💰 Финансы\n\n"
        f"☕ Продано: {quantity} шт.\n"
        f"💵 Выручка: ${revenue:.2f}\n"
        f"📦 Себестоимость: ${cost:.2f}\n"
        f"💰 Валовая прибыль: ${profit:.2f}\n"
        f"💸 Расходы: ${expenses:.2f}\n"
        f"💵 Чистая прибыль: ${net_profit:.2f}\n"
        f"📊 Маржа: {margin:.1f}%",
        reply_markup=main_menu(),
    )


async def best_selling_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            p.name,
            SUM(s.quantity) AS total_quantity,
            SUM(s.quantity * s.unit_price) AS revenue
        FROM sales s
        JOIN products p ON s.product_id = p.id
        GROUP BY p.id
        ORDER BY total_quantity DESC
        """)

    products = cursor.fetchall()

    connection.close()

    if not products:
        await update.message.reply_text(
            "📊 Пока нет данных для анализа.",
            reply_markup=main_menu(),
        )
        return

    message = "🏆 Лучшие продажи\n\n"

    for position, (name, quantity, revenue) in enumerate(products, start=1):
        message += (
            f"{position}. ☕ {name}\n"
            f"   Продано: {quantity} шт.\n"
            f"   Выручка: ${revenue:.2f}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def product_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            p.name,
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0)
        FROM products p
        LEFT JOIN sales s ON s.product_id = p.id
        WHERE p.is_active = 1 OR s.id IS NOT NULL
        GROUP BY p.id
        ORDER BY SUM(s.quantity) DESC
        """)

    products = cursor.fetchall()

    connection.close()

    message = "📈 Отчёт по товарам\n\n"

    for name, quantity, revenue, cost in products:
        profit = revenue - cost

        if revenue > 0:
            margin = (profit / revenue) * 100
        else:
            margin = 0

        message += (
            f"☕ {name}\n"
            f"   Продано: {quantity} шт.\n"
            f"   Выручка: ${revenue:.2f}\n"
            f"   Себестоимость: ${cost:.2f}\n"
            f"   Прибыль: ${profit:.2f}\n"
            f"   Маржа: {margin:.1f}%\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0),
            COALESCE(SUM(s.quantity), 0)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date) = date('now')
        """)

    revenue, cost, quantity = cursor.fetchone()

    connection.close()

    profit = revenue - cost

    await update.message.reply_text(
        "🧾 Отчёт за сегодня\n\n"
        f"☕ Продано: {quantity} шт.\n"
        f"💵 Выручка: ${revenue:.2f}\n"
        f"📦 Себестоимость: ${cost:.2f}\n"
        f"💰 Валовая прибыль: ${profit:.2f}"
    )


def get_ai_analysis_data():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0),
            COALESCE(SUM(s.quantity * s.unit_cost), 0)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date) >= date('now', '-6 days')
    """)

    sales_quantity, revenue, product_cost = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE date(expense_date) >= date('now', '-6 days')
    """)

    expenses = cursor.fetchone()[0]

    connection.close()

    gross_profit = revenue - product_cost
    net_profit = gross_profit - expenses

    return (
        sales_quantity,
        revenue,
        product_cost,
        gross_profit,
        expenses,
        net_profit,
    )


def get_previous_period_data():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date)
              BETWEEN date('now', '-13 days')
              AND date('now', '-7 days')
    """)

    sales_quantity, revenue = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE date(expense_date)
              BETWEEN date('now', '-13 days')
              AND date('now', '-7 days')
    """)

    expenses = cursor.fetchone()[0]

    connection.close()

    return sales_quantity, revenue, expenses


def format_change(current, previous):
    if previous == 0:
        return "— Нет данных за предыдущие 7 дней"

    change = ((current - previous) / previous) * 100

    if change > 0:
        return f"📈 +{change:.1f}%"
    elif change < 0:
        return f"📉 {change:.1f}%"
    else:
        return "➖ 0.0%"


def get_product_profitability():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            name,
            price,
            cost,
            price - cost,
            CASE
                WHEN price > 0
                THEN ((price - cost) / price) * 100
                ELSE 0
            END
        FROM products
        WHERE is_active = 1
        ORDER BY (price - cost) DESC
    """)

    products = cursor.fetchall()

    connection.close()

    return products


def get_profitability_summary():
    products = get_product_profitability()

    if not products:
        return None

    most_profitable = max(products, key=lambda item: item[3])
    best_margin = max(products, key=lambda item: item[4])

    return most_profitable, best_margin


def get_product_sales_analysis():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            p.name,
            COALESCE(SUM(s.quantity), 0),
            COALESCE(SUM(s.quantity * s.unit_price), 0)
        FROM products p
        LEFT JOIN sales s
            ON s.product_id = p.id
            AND date(s.sale_date) >= date('now', '-6 days')
        WHERE p.is_active = 1 OR s.id IS NOT NULL
        GROUP BY p.id, p.name
        ORDER BY SUM(s.quantity) DESC
    """)

    products = cursor.fetchall()

    connection.close()

    return products


def get_unsold_products(product_sales_analysis):
    return [
        name
        for name, quantity, product_revenue in product_sales_analysis
        if quantity == 0
    ]


def get_expense_analysis():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            name,
            SUM(amount)
        FROM expenses
        WHERE date(expense_date) >= date('now', '-6 days')
        GROUP BY name
        ORDER BY SUM(amount) DESC
    """)

    expenses = cursor.fetchall()

    connection.close()

    return expenses


def get_main_ai_conclusion(
    net_profit,
    gross_profit,
    expenses,
    top_products,
    expense_analysis,
    unsold_products,
):
    if net_profit < 0 and expense_analysis:
        biggest_expense_name, biggest_expense_amount = expense_analysis[0]

        return (
            f"Проверь расход «{biggest_expense_name}» "
            f"на ${biggest_expense_amount:.2f} — он сильнее всего влияет "
            "на результат за период."
        )

    if net_profit < 0:
        return (
            "За последние 7 дней чистая прибыль отрицательная. "
            "Расходы превышают валовую прибыль."
        )

    if top_products and unsold_products:
        best_name = top_products[0][0]

        return (
            f"За последние 7 дней лидер продаж — {best_name}. "
            "Есть товары без продаж."
        )

    if net_profit > 0:
        return "За последние 7 дней бизнес показывает положительную " "чистую прибыль."

    return "За последние 7 дней требуется дополнительный анализ данных."


def get_ai_action(
    net_profit,
    revenue,
    gross_profit,
    expenses,
    expense_analysis,
    top_products,
    unsold_products,
    sales_comparison,
    revenue_comparison,
    expenses_comparison,
):
    if net_profit < 0 and expenses > gross_profit and expense_analysis:
        biggest_expense_name, biggest_expense_amount = expense_analysis[0]

        return (
            f"🔴 Главная проблема: расходы составили ${expenses:.2f}, "
            f"а валовая прибыль — ${gross_profit:.2f}.\n\n"
            f"Основной расход — «{biggest_expense_name}» "
            f"на ${biggest_expense_amount:.2f}.\n\n"
            f"💡 Что сделать: проверь, является ли этот расход "
            "разовым или регулярным."
        )

    if sales_comparison.startswith("📉"):
        return (
            "📉 Продажи снизились по сравнению с предыдущими 7 днями.\n\n"
            "💡 Что сделать: проверь количество продаж "
            "и товары, которые продаются хуже обычного."
        )

    if expenses_comparison.startswith("📈"):
        return (
            "📈 Расходы выросли по сравнению с предыдущими 7 днями.\n\n"
            "💡 Что сделать: проверь основные статьи расходов "
            "и найди причину роста."
        )

    if top_products:
        best_name = top_products[0][0]

        return (
            f"🏆 Лидер продаж — «{best_name}».\n\n"
            "💡 Что сделать: следи за его продажами "
            "и используй его как один из основных товаров."
        )

    if unsold_products:
        return (
            "⚠️ Есть товары без продаж.\n\n"
            "💡 Что сделать: проверь, почему они не продаются, "
            "и реши, стоит ли оставлять их в ассортименте."
        )

    if net_profit > 0:
        return (
            "🟢 Бизнес показывает положительную чистую прибыль.\n\n"
            "💡 Что сделать: продолжай контролировать прибыль "
            "и основные расходы."
        )

    return "Добавь больше данных о продажах для более точного анализа."


def build_ai_context(
    sales_quantity,
    revenue,
    product_cost,
    gross_profit,
    expenses,
    net_profit,
    sales_comparison,
    revenue_comparison,
    expenses_comparison,
    top_products,
    expense_analysis,
    unsold_products,
    profitability_summary,
    low_stock,
):
    context = {
        "period": "Последние 7 дней",
        "sales_quantity": sales_quantity,
        "revenue": revenue,
        "product_cost": product_cost,
        "gross_profit": gross_profit,
        "expenses": expenses,
        "net_profit": net_profit,
        "sales_comparison": sales_comparison,
        "revenue_comparison": revenue_comparison,
        "expenses_comparison": expenses_comparison,
        "top_products": top_products,
        "expense_analysis": expense_analysis,
        "unsold_products": unsold_products,
        "profitability_summary": profitability_summary,
        "low_stock": low_stock,
    }

    return context


def format_ai_context(ai_context):
    return json.dumps(
        ai_context,
        ensure_ascii=False,
        indent=2,
    )


def get_ai_system_prompt():
    return (
        "Ты AI-аналитик кофейни Coffee Manager.\n"
        "Анализируй только предоставленные данные.\n"
        "Никогда не придумывай и не изменяй цифры.\n"
        "Не повторяй одну и ту же информацию несколько раз.\n"
        "Не давай длинных объяснений.\n\n"
        "Формат ответа:\n"
        "🎯 Главная проблема: одна конкретная проблема.\n"
        "🔎 Причина: краткое объяснение на основе данных.\n"
        "💡 Что сделать: одно конкретное действие.\n\n"
        "Если серьёзной проблемы нет, укажи это кратко "
        "и предложи одно полезное действие."
    )


def build_ai_request(ai_context):
    return {
        "system": get_ai_system_prompt(),
        "data": format_ai_context(ai_context),
    }


async def ai_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_ai_analysis_data()
    previous_sales, previous_revenue, previous_expenses = get_previous_period_data()
    product_sales_analysis = get_product_sales_analysis()
    expense_analysis = get_expense_analysis()
    unsold_products = get_unsold_products(product_sales_analysis)

    (
        sales_quantity,
        revenue,
        product_cost,
        gross_profit,
        expenses,
        net_profit,
    ) = data

    top_products = [item for item in product_sales_analysis if item[1] > 0]

    main_ai_conclusion = get_main_ai_conclusion(
        net_profit,
        gross_profit,
        expenses,
        top_products,
        expense_analysis,
        unsold_products,
    )

    sales_comparison = format_change(sales_quantity, previous_sales)
    revenue_comparison = format_change(revenue, previous_revenue)
    expenses_comparison = format_change(expenses, previous_expenses)
    profitability_summary = get_profitability_summary()

    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name, unit, stock, minimum_stock
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock
    """)

    low_stock = cursor.fetchall()

    connection.close()

    ai_context = build_ai_context(
        sales_quantity,
        revenue,
        product_cost,
        gross_profit,
        expenses,
        net_profit,
        sales_comparison,
        revenue_comparison,
        expenses_comparison,
        top_products,
        expense_analysis,
        unsold_products,
        profitability_summary,
        low_stock,
    )
    ai_request = build_ai_request(ai_context)

    ai_action = get_ai_action(
        net_profit,
        revenue,
        gross_profit,
        expenses,
        expense_analysis,
        top_products,
        unsold_products,
        sales_comparison,
        revenue_comparison,
        expenses_comparison,
    )
    message = (
        "🧠 AI-анализ Coffee Manager\n\n" "🎯 Что сделать сейчас:\n" f"👉 {ai_action}"
    )

    await update.message.reply_text(
        message,
        reply_markup=ai_analysis_menu(),
    )


async def detailed_ai_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_ai_analysis_data()
    previous_sales, previous_revenue, previous_expenses = get_previous_period_data()
    product_profitability = get_product_profitability()
    profitability_summary = get_profitability_summary()
    product_sales_analysis = get_product_sales_analysis()
    unsold_products = get_unsold_products(product_sales_analysis)
    expense_analysis = get_expense_analysis()
    (
        sales_quantity,
        revenue,
        product_cost,
        gross_profit,
        expenses,
        net_profit,
    ) = data

    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            p.name,
            SUM(s.quantity),
            SUM(s.quantity * s.unit_price)
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE date(s.sale_date) >= date('now', '-6 days')
        GROUP BY p.id, p.name
        ORDER BY SUM(s.quantity) DESC
        LIMIT 5
    """)

    top_products = cursor.fetchall()

    cursor.execute("""
        SELECT name, unit, stock, minimum_stock
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock
    """)

    low_stock = cursor.fetchall()

    connection.close()
    revenue_comparison = format_change(revenue, previous_revenue)
    sales_comparison = format_change(sales_quantity, previous_sales)
    expenses_comparison = format_change(expenses, previous_expenses)

    message = (
        "🧠 AI-анализ Coffee Manager\n\n"
        "📅 Последние 7 дней\n\n"
        f"📊 Сравнение с предыдущими 7 днями:\n"
        f"☕ Продажи: {sales_comparison}\n"
        f"💰 Выручка: {revenue_comparison}\n"
        f"💸 Расходы: {expenses_comparison}\n\n"
        f"☕ Продано: {sales_quantity:.0f} шт.\n"
        f"💰 Выручка: ${revenue:.2f}\n"
        f"📦 Себестоимость: ${product_cost:.2f}\n"
        f"📈 Валовая прибыль: ${gross_profit:.2f}\n"
        f"💸 Расходы: ${expenses:.2f}\n"
        f"💵 Чистая прибыль: ${net_profit:.2f}\n\n"
        "🏆 Топ продаж:\n"
    )

    if top_products:
        for index, (name, quantity, product_revenue) in enumerate(
            top_products, start=1
        ):
            message += (
                f"{index}. {name} — " f"{quantity:.0f} шт. / ${product_revenue:.2f}\n"
            )
    else:
        message += "Нет продаж за этот период.\n"
    message += "\n💰 Прибыль с продажи:\n"

    if product_profitability:
        for name, price, cost, profit, margin in product_profitability:
            message += (
                f"☕ {name}: "
                f"${profit:.2f} прибыли / продажа "
                f"({margin:.1f}% маржа)\n"
            )
    else:
        message += "Нет товаров для анализа.\n"
    message += "\n📊 Продажи по товарам:\n"

    for name, quantity, product_revenue in product_sales_analysis:
        message += f"☕ {name}: {quantity:.0f} шт. " f"/ ${product_revenue:.2f}\n"
    message += "\n⚠️ Товары без продаж:\n"

    if unsold_products:
        for name in unsold_products:
            message += f"☕ {name} — продаж не было\n"
    else:
        message += "✅ Все товары продавались.\n"
    message += "\n💸 Анализ расходов:\n"

    if expense_analysis:
        for name, amount in expense_analysis:
            message += f"💸 {name}: ${amount:.2f}\n"
    else:
        message += "Нет расходов за последние 7 дней.\n"
    message += "\n🏆 Экономика товаров:\n"

    if profitability_summary:
        most_profitable, best_margin = profitability_summary

        profitable_name = most_profitable[0]
        profitable_profit = most_profitable[3]

        margin_name = best_margin[0]
        margin_value = best_margin[4]

        message += (
            f"💰 Самая высокая прибыль с продажи: "
            f"{profitable_name} — ${profitable_profit:.2f}\n"
        )

        message += f"📊 Самая высокая маржа: " f"{margin_name} — {margin_value:.1f}%\n"
    else:
        message += "Нет данных для анализа.\n"
    message += "\n📦 Склад:\n"

    if low_stock:
        for name, unit, stock, minimum_stock in low_stock:
            message += f"⚠️ {name}: {stock:g} {unit} " f"(минимум {minimum_stock:g})\n"
    else:
        message += "✅ Критически низких запасов нет.\n"
    main_ai_conclusion = get_main_ai_conclusion(
        net_profit,
        gross_profit,
        expenses,
        top_products,
        expense_analysis,
        unsold_products,
    )

    message += "\n🧠 Главный вывод:\n" f"💡 {main_ai_conclusion}\n"
    message += "\n💡 Что требует внимания:\n"

    if net_profit < 0:
        message += (
            "🔴 Чистая прибыль отрицательная. " "Расходы превышают валовую прибыль.\n"
        )
    else:
        message += "🟢 Чистая прибыль положительная.\n"

    if expenses > gross_profit:
        message += (
            "💸 Расходы выше валовой прибыли. "
            "Стоит проверить крупные расходы и их влияние "
            "на результат бизнеса.\n"
        )

    if top_products:
        best_name, best_quantity, best_revenue = top_products[0]
        message += f"🏆 Лидер продаж: {best_name} — " f"{best_quantity:.0f} шт.\n"

    if low_stock:
        message += (
            "📦 На складе есть позиции ниже минимального "
            "уровня — стоит проверить необходимость закупки.\n"
        )
    else:
        message += "📦 Критически низких запасов не обнаружено.\n"
    await update.message.reply_text(
        message,
        reply_markup=ai_analysis_menu(),
    )


def settings_menu():
    return ReplyKeyboardMarkup(
        [
            ["☕ Управление меню"],
            ["🔔 Уведомления"],
            ["⬅️ Назад в меню"],
        ],
        resize_keyboard=True,
    )


# ===== УПРАВЛЕНИЕ МЕНЮ =====

PRODUCT_NAME_MAX_LENGTH = 40


def cancel_menu():
    return ReplyKeyboardMarkup(
        [["❌ Отмена"]],
        resize_keyboard=True,
    )


def menu_management_menu(products):
    buttons = [["➕ Добавить товар"]]

    for product_id, name, price, cost in products:
        buttons.append([f"✏️ {name}"])

    buttons.append(["⬅️ Назад в настройки"])

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
    )


def product_card_menu():
    return ReplyKeyboardMarkup(
        [
            ["📝 Название", "💲 Цена"],
            ["🧮 Себестоимость"],
            ["🗑 Удалить"],
            ["⬅️ К списку товаров"],
        ],
        resize_keyboard=True,
    )


def confirm_delete_menu():
    return ReplyKeyboardMarkup(
        [["✅ Да, удалить", "❌ Отмена"]],
        resize_keyboard=True,
    )


def get_product(product_id):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, name, price, cost
        FROM products
        WHERE id = ? AND is_active = 1
        """,
        (product_id,),
    )

    product = cursor.fetchone()

    connection.close()

    return product


def get_product_stats(product_id):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM sales WHERE product_id = ?",
        (product_id,),
    )

    sold_quantity = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM recipes WHERE product_id = ?",
        (product_id,),
    )

    has_recipe = cursor.fetchone()[0] > 0

    connection.close()

    return sold_quantity, has_recipe


def add_product(name, price, cost):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        "INSERT INTO products (name, price, cost) VALUES (?, ?, ?)",
        (name, price, cost),
    )

    connection.commit()
    connection.close()


def update_product(product_id, name, price, cost):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE products
        SET name = ?, price = ?, cost = ?
        WHERE id = ?
        """,
        (name, price, cost, product_id),
    )

    connection.commit()
    connection.close()


def delete_product(product_id):
    """Удаляет товар без продаж. Проданный товар только скрывает из меню."""
    connection = sqlite3.connect(DB_NAME)
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
        cursor.execute(
            "DELETE FROM recipes WHERE product_id = ?",
            (product_id,),
        )
        cursor.execute(
            "DELETE FROM products WHERE id = ?",
            (product_id,),
        )

    connection.commit()
    connection.close()

    return "archived" if has_sales else "deleted"


def validate_product_name(name, exclude_product_id=None):
    if not name:
        return "❌ Название не может быть пустым."

    if "\n" in name:
        return "❌ Название должно быть в одну строку."

    if len(name) > PRODUCT_NAME_MAX_LENGTH:
        return (
            "❌ Название слишком длинное. "
            f"Максимум {PRODUCT_NAME_MAX_LENGTH} символов."
        )

    for product_id, product_name, price, cost in get_products():
        if product_id != exclude_product_id and (
            product_name.casefold() == name.casefold()
        ):
            return "❌ Товар с таким названием уже есть в меню."

    return None


def parse_money(text):
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None

    if not math.isfinite(value):
        return None

    return round(value, 2)


async def show_menu_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()

    message = "☕ Управление меню\n\n"

    if products:
        for product_id, name, price, cost in products:
            message += (
                f"☕ {name}\n"
                f"   Цена: ${price:.2f}\n"
                f"   Себестоимость: ${cost:.2f}\n\n"
            )
        message += "Выберите товар или добавьте новый:"
    else:
        message += "В меню пока нет товаров.\nДобавьте первый товар:"

    await update.message.reply_text(
        message,
        reply_markup=menu_management_menu(products),
    )


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update, context)
        return

    product_id, name, price, cost = product
    sold_quantity, has_recipe = get_product_stats(product_id)

    profit = price - cost
    margin = (profit / price) * 100 if price > 0 else 0

    if has_recipe:
        recipe_status = "✅ Рецепт задан — склад списывается."
    else:
        recipe_status = "⚠️ Рецепт не задан — склад не списывается."

    await update.message.reply_text(
        f"☕ {name}\n\n"
        f"💵 Цена: ${price:.2f}\n"
        f"📦 Себестоимость: ${cost:.2f}\n"
        f"💰 Прибыль с продажи: ${profit:.2f} ({margin:.1f}% маржа)\n"
        f"📊 Продано всего: {sold_quantity} шт.\n\n"
        f"{recipe_status}",
        reply_markup=product_card_menu(),
    )


async def handle_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data["product_step"]
    text = update.message.text.strip()

    if step == "add_name":
        error = validate_product_name(text)

        if error:
            await update.message.reply_text(error, reply_markup=cancel_menu())
            return

        context.user_data["new_product_name"] = text
        context.user_data["product_step"] = "add_price"

        await update.message.reply_text(
            f"☕ {text}\n\nВведите цену продажи, например: 4.50",
            reply_markup=cancel_menu(),
        )
        return

    if step == "confirm_delete":
        await update.message.reply_text(
            "Нажмите «✅ Да, удалить» или «❌ Отмена».",
            reply_markup=confirm_delete_menu(),
        )
        return

    if step == "edit_name":
        product = get_product(context.user_data.get("product_id"))

        if product is None:
            context.user_data.clear()
            await show_menu_management(update, context)
            return

        product_id, name, price, cost = product
        error = validate_product_name(text, exclude_product_id=product_id)

        if error:
            await update.message.reply_text(error, reply_markup=cancel_menu())
            return

        update_product(product_id, text, price, cost)
        context.user_data.pop("product_step", None)

        await update.message.reply_text("✅ Название изменено.")
        await show_product_card(update, context)
        return

    # Остальные шаги — ввод цены или себестоимости.
    value = parse_money(text)

    if value is None:
        await update.message.reply_text(
            "❌ Введите число, например: 4.50",
            reply_markup=cancel_menu(),
        )
        return

    if step in ("add_price", "edit_price") and value <= 0:
        await update.message.reply_text(
            "❌ Цена должна быть больше нуля.",
            reply_markup=cancel_menu(),
        )
        return

    if step in ("add_cost", "edit_cost") and value < 0:
        await update.message.reply_text(
            "❌ Себестоимость не может быть отрицательной.",
            reply_markup=cancel_menu(),
        )
        return

    if step == "add_price":
        context.user_data["new_product_price"] = value
        context.user_data["product_step"] = "add_cost"

        await update.message.reply_text(
            "📦 Введите себестоимость одной порции, например: 1.20\n"
            "Если не знаете — введите 0.",
            reply_markup=cancel_menu(),
        )
        return

    if step == "add_cost":
        name = context.user_data["new_product_name"]
        price = context.user_data["new_product_price"]

        add_product(name, price, value)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ Товар «{name}» добавлен.\n\n"
            "⚠️ Рецепт для него не задан — склад при продаже "
            "не списывается."
        )
        await show_menu_management(update, context)
        return

    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update, context)
        return

    product_id, name, price, cost = product

    if step == "edit_price":
        update_product(product_id, name, value, cost)
        await update.message.reply_text("✅ Цена изменена.")
    else:
        update_product(product_id, name, price, value)
        await update.message.reply_text("✅ Себестоимость изменена.")

    context.user_data.pop("product_step", None)
    await show_product_card(update, context)


async def start_product_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, step):
    product = get_product(context.user_data.get("product_id"))

    if product is None:
        context.user_data.clear()
        await show_menu_management(update, context)
        return

    product_id, name, price, cost = product
    context.user_data["product_step"] = step

    if step == "edit_name":
        prompt = f"Текущее название: {name}\n\nВведите новое название:"
    elif step == "edit_price":
        prompt = f"Текущая цена: ${price:.2f}\n\nВведите новую цену:"
    elif step == "edit_cost":
        prompt = (
            f"Текущая себестоимость: ${cost:.2f}\n\n"
            "Введите новую себестоимость:"
        )
    else:
        await update.message.reply_text(
            f"🗑 Удалить «{name}»?\n\n"
            "Если товар уже продавался, он будет скрыт из меню, "
            "а история продаж и отчёты сохранятся.",
            reply_markup=confirm_delete_menu(),
        )
        return

    await update.message.reply_text(prompt, reply_markup=cancel_menu())


def notifications_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔔 Включить уведомления"],
            ["🔕 Выключить уведомления"],
            ["⬅️ Назад в настройки"],
        ],
        resize_keyboard=True,
    )


def notifications_status():
    return "🔔 Уведомления включены."


def set_notifications(user_id, enabled):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO settings (user_id, notifications_enabled)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET notifications_enabled = excluded.notifications_enabled
    """,
        (user_id, int(enabled)),
    )

    connection.commit()
    connection.close()


def get_notifications_status(user_id):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT notifications_enabled
        FROM settings
        WHERE user_id = ?
        """,
        (user_id,),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return False

    return bool(result[0])
    connection.commit()
    connection.close()


def get_low_stock_items():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name, stock, minimum_stock, unit
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock ASC
        """)

    items = cursor.fetchall()

    connection.close()

    return items


async def send_daily_notification(context):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT user_id
        FROM settings
        WHERE notifications_enabled = 1
        """)

    users = cursor.fetchall()

    connection.close()

    for (user_id,) in users:
        if user_id not in ADMIN_IDS:
            continue

        quantity, revenue, cost, gross_profit, expenses, net_profit = (
            get_today_finances()
        )
        low_stock_items = get_low_stock_items()

        low_stock_text = ""

        if low_stock_items:
            low_stock_text = "\n\n⚠️ Заканчивается:\n"

            for name, stock, minimum_stock, unit in low_stock_items:
                low_stock_text += (
                    f"• {name}: {stock:g} {unit} "
                    f"(минимум {minimum_stock:g} {unit})\n"
                )
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "📊 Ежедневная сводка Coffee Manager\n\n"
                f"☕ Продано: {quantity} шт.\n"
                f"💵 Выручка: ${revenue:.2f}\n"
                f"📦 Себестоимость: ${cost:.2f}\n"
                f"📈 Валовая прибыль: ${gross_profit:.2f}\n"
                f"💸 Расходы: ${expenses:.2f}\n"
                f"💰 Чистая прибыль: ${net_profit:.2f}"
                f"{low_stock_text}"
            ),
        )


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    main_menu_buttons = {
        "📊 Продажи",
        "💰 Финансы",
        "💸 Расходы",
        "💳 История расходов",
        "📋 История",
        "🏆 Лучшие продажи",
        "📈 Отчёт по товарам",
        "🧾 Отчёты",
        "📦 Склад",
        "🛒 Закупки",
        "📋 История закупок",
        "🧠 AI-анализ",
        "⚙️ Настройки",
    }

    if text in main_menu_buttons:
        context.user_data.clear()
    # ===== ФИНАНСЫ =====
    if text == "📅 Сегодня":
        context.user_data["finance_period"] = "today"
        await show_finances(update, context)
        return

    if text == "📅 Вчера":
        context.user_data["finance_period"] = "yesterday"
        await show_finances(update, context)
        return

    if text == "📅 7 дней":
        context.user_data["finance_period"] = "7days"
        await show_finances(update, context)
        return

    if text == "📅 Этот месяц":
        context.user_data["finance_period"] = "month"
        await show_finances(update, context)
        return

    if text == "📅 Всё время":
        context.user_data["finance_period"] = "all"
        await show_finances(update, context)
        return

    if text == "🔙 Главное меню":
        context.user_data.clear()

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu(),
        )
        return

    # ===== ГЛАВНОЕ МЕНЮ =====

    if text == "📊 Продажи":
        context.user_data.clear()
        await show_sales(update, context)
        return

    if text == "💰 Финансы":
        context.user_data.clear()
        await finance_menu(update, context)
        return

    if text == "💸 Расходы":
        context.user_data.clear()
        await expense_menu(update, context)
        return

    if text == "💳 История расходов":
        context.user_data.clear()
        await expense_history(update, context)
        return

    if text == "📋 История":
        context.user_data.clear()
        await sales_history(update, context)
        return

    if text == "🏆 Лучшие продажи":
        context.user_data.clear()
        await best_selling_products(update, context)
        return

    if text == "📈 Отчёт по товарам":
        context.user_data.clear()
        await product_report(update, context)
        return

    if text == "🧾 Отчёты":
        context.user_data.clear()
        await daily_report(update, context)
        return

    if text == "📦 Склад":
        context.user_data.clear()
        await show_inventory(update, context)
        return

    if text == "🛒 Закупки":
        context.user_data.clear()
        await purchase_menu(update, context)
        return

    if text == "📋 История закупок":
        context.user_data.clear()
        await purchase_history(update, context)
        return

    if text == "🧠 AI-анализ":
        context.user_data.clear()
        await ai_analysis(update, context)
        return

    if text == "📊 Детальный анализ":
        context.user_data.clear()
        await detailed_ai_analysis(update, context)
        return

    if text == "⚙️ Настройки":
        context.user_data.clear()

        await update.message.reply_text(
            "⚙️ Настройки",
            reply_markup=settings_menu(),
        )
        return

    if text == "🔔 Уведомления":
        await update.message.reply_text(
            "🔔 Уведомления\n\nВыбери действие:",
            reply_markup=notifications_menu(),
        )
        return

    if text == "🔔 Включить уведомления":
        user_id = update.effective_user.id

        set_notifications(user_id, True)

        await update.message.reply_text(
            notifications_status(),
            reply_markup=notifications_menu(),
        )
        return
    if text == "🔕 Выключить уведомления":
        user_id = update.effective_user.id

        set_notifications(user_id, False)

        await update.message.reply_text(
            "🔕 Уведомления выключены.",
            reply_markup=notifications_menu(),
        )
        return

    if text == "⬅️ Назад в настройки":
        context.user_data.clear()

        await update.message.reply_text(
            "⚙️ Настройки",
            reply_markup=settings_menu(),
        )
        return

    if text == "⬅️ Назад в меню":
        context.user_data.clear()

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu(),
        )
        return

    # ===== УПРАВЛЕНИЕ МЕНЮ =====

    if text in ("☕ Управление меню", "⬅️ К списку товаров"):
        context.user_data.clear()
        await show_menu_management(update, context)
        return

    if text == "➕ Добавить товар":
        context.user_data.clear()
        context.user_data["product_step"] = "add_name"

        await update.message.reply_text(
            "➕ Новый товар\n\nВведите название, например: Раф",
            reply_markup=cancel_menu(),
        )
        return

    if text == "❌ Отмена":
        if "product_id" in context.user_data:
            context.user_data.pop("product_step", None)
            await show_product_card(update, context)
        else:
            context.user_data.clear()
            await show_menu_management(update, context)
        return

    product_edit_steps = {
        "📝 Название": "edit_name",
        "💲 Цена": "edit_price",
        "🧮 Себестоимость": "edit_cost",
        "🗑 Удалить": "confirm_delete",
    }

    if text in product_edit_steps and "product_id" in context.user_data:
        await start_product_edit(update, context, product_edit_steps[text])
        return

    if (
        text == "✅ Да, удалить"
        and context.user_data.get("product_step") == "confirm_delete"
    ):
        product = get_product(context.user_data.get("product_id"))
        context.user_data.clear()

        if product is not None:
            result = delete_product(product[0])

            if result == "archived":
                await update.message.reply_text(
                    f"🗑 «{product[1]}» скрыт из меню.\n"
                    "История продаж и отчёты сохранены."
                )
            else:
                await update.message.reply_text(f"🗑 «{product[1]}» удалён.")

        await show_menu_management(update, context)
        return

    # ===== ВВОД ТОВАРА =====

    if "product_step" in context.user_data:
        await handle_product_input(update, context)
        return

    # ===== ВВОД РАСХОДА =====

    if context.user_data.get("waiting_for_expense_name"):
        context.user_data["expense_name"] = text
        context.user_data.pop("waiting_for_expense_name", None)

        await update.message.reply_text(
            f"💸 Расход: {text}\n\n" "Введите сумму расхода:"
        )
        return

    if "expense_name" in context.user_data:
        try:
            amount = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введите сумму числом, например: 50")
            return

        if amount <= 0:
            await update.message.reply_text("❌ Сумма должна быть больше нуля.")
            return

        context.user_data["expense_amount"] = amount
        await add_expense(update, context)
        return

    # ===== ВВОД ЗАКУПКИ =====

    if "purchase_ingredient_id" in context.user_data:
        try:
            quantity = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введите количество числом, например: 2")
            return

        if quantity <= 0:
            await update.message.reply_text("❌ Количество должно быть больше нуля.")
            return

        context.user_data["purchase_quantity"] = quantity
        await add_purchase(update, context)
        return

    # ===== ВВОД ПРОДАЖИ =====

    if "selected_product_id" in context.user_data:
        await save_sale(update, context)
        return

    # ===== ВЫБОР ТОВАРА ДЛЯ РЕДАКТИРОВАНИЯ =====

    for product_id, name, price, cost in get_products():
        if text == f"✏️ {name}":
            context.user_data.clear()
            context.user_data["product_id"] = product_id
            await show_product_card(update, context)
            return

    # ===== ВЫБОР ИНГРЕДИЕНТА =====

    ingredients = get_ingredients()

    for ingredient_id, name, unit, stock, minimum_stock in ingredients:
        if text == f"🛒 {name}":
            context.user_data["purchase_ingredient_id"] = ingredient_id
            context.user_data["purchase_ingredient_name"] = name
            context.user_data["purchase_unit"] = unit

            await update.message.reply_text(
                f"🛒 Вы выбрали: {name}\n\n" f"Введите количество в {unit}:"
            )
            return

    # ===== ВЫБОР ТОВАРА =====

    products = get_products()

    for product_id, name, price, cost in products:
        if text == f"☕ {name} — ${price:.2f}":
            context.user_data["selected_product_id"] = product_id
            context.user_data["selected_product_name"] = name

            await update.message.reply_text(
                f"☕ Вы выбрали: {name}\n\n" "Введите количество:"
            )
            return

    await update.message.reply_text(
        f"Вы выбрали: {text}\n\n" "Этот раздел скоро будет подключён.",
        reply_markup=main_menu(),
    )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден в файле .env")

    if not ADMIN_IDS:
        raise ValueError("ADMIN_IDS не найден в файле .env")

    create_tables()
    migrate_database()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(TypeHandler(Update, check_access), group=-1)
    application.job_queue.run_daily(
        send_daily_notification,
        time=time(hour=10, minute=0),
    )
    application.add_handler(CommandHandler("start", start))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            menu_button,
        )
    )

    print("Coffee Manager запущен...")
    application.run_polling()


if __name__ == "__main__":
    main()
