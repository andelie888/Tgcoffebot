"""Учёт кофейни: закрытие дня, себестоимость, финансы, налоги, потери.

Модуль не зависит от Telegram: всё здесь можно проверить тестами.
Даты дней — объекты date; время записей — строки "YYYY-MM-DD HH:MM:SS".
"""

import calendar
import math
import re
import sqlite3

from contextlib import closing
from datetime import date, datetime, timedelta

from config import DB_NAME


KIND_SALE = "sale"
KIND_TREAT = "treat"
KIND_WASTE = "waste"
FREE_KINDS = (KIND_TREAT, KIND_WASTE)

TAX_MODES = {
    "none": "Не считать",
    "usn6": "УСН 6% (доходы)",
    "usn15": "УСН 15% (доходы − расходы)",
    "patent": "Патент",
}

MAX_DAY_QUANTITY = 5000


# ===== БАЗА =====


def connect():
    return closing(sqlite3.connect(DB_NAME))


def fetch_all(sql, params=()):
    with connect() as connection:
        return connection.execute(sql, params).fetchall()


def fetch_one(sql, params=()):
    with connect() as connection:
        return connection.execute(sql, params).fetchone()


def day_bounds(start, end):
    return start.isoformat(), end.isoformat()


# ===== НАСТРОЙКИ КОФЕЙНИ =====


def get_setting(key, default=None):
    row = fetch_one("SELECT value FROM shop_settings WHERE key = ?", (key,))
    return default if row is None else row[0]


def set_setting(key, value):
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO shop_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        connection.commit()


def get_tax_mode():
    mode = get_setting("tax_mode", "none")
    return mode if mode in TAX_MODES else "none"


def get_patent_cost():
    try:
        return float(get_setting("patent_cost_year", "0"))
    except ValueError:
        return 0.0


# ===== ТОВАРЫ И СЕБЕСТОИМОСТЬ =====


def get_products():
    return fetch_all("""
        SELECT id, name, price, cost
        FROM products
        WHERE is_active = 1
        ORDER BY id
        """)


def recipe_cost(cursor, product_id):
    """Себестоимость по рецепту и последним закупочным ценам. None — рецепта нет."""
    rows = cursor.execute(
        """
        SELECT r.quantity, i.purchase_price
        FROM recipes r
        JOIN ingredients i ON i.id = r.ingredient_id
        WHERE r.product_id = ?
        """,
        (product_id,),
    ).fetchall()

    if not rows:
        return None

    return round(sum(quantity * price for quantity, price in rows), 2)


def product_unit_cost(cursor, product_id):
    """Себестоимость порции: по рецепту, а если рецепта нет — введённая вручную."""
    cost = recipe_cost(cursor, product_id)

    if cost is not None:
        return cost

    row = cursor.execute("SELECT cost FROM products WHERE id = ?", (product_id,)).fetchone()
    return row[0] if row else 0


def get_product_costs():
    """[(id, name, price, cost, by_recipe)] для активных товаров."""
    with connect() as connection:
        cursor = connection.cursor()
        result = []

        for product_id, name, price, manual_cost in get_products():
            cost = recipe_cost(cursor, product_id)
            by_recipe = cost is not None
            result.append((product_id, name, price, cost if by_recipe else manual_cost, by_recipe))

        return result


def change_stock_by_recipe(cursor, product_id, quantity_delta):
    """Списывает (delta > 0) или возвращает (delta < 0) склад по рецепту."""
    rows = cursor.execute(
        "SELECT ingredient_id, quantity FROM recipes WHERE product_id = ?",
        (product_id,),
    ).fetchall()

    # Остаток может уйти в минус: это честный сигнал «проверьте склад»,
    # а повторное закрытие дня возвращает ровно то, что списало.
    for ingredient_id, recipe_quantity in rows:
        cursor.execute(
            "UPDATE ingredients SET stock = stock - ? WHERE id = ?",
            (recipe_quantity * quantity_delta, ingredient_id),
        )


# ===== РАЗБОР ВВОДА «ЛАТТЕ 0.4 — 23» =====


SPLIT_PATTERN = re.compile(r"[\n;]+|,(?!\d)")
LINE_PATTERN = re.compile(
    r"^(?P<name>.*?)[\s\-—–:=×x*]*(?P<qty>\d+)\s*(?:шт\.?|штук|pcs)?$",
    re.IGNORECASE,
)


def normalize_name(text):
    text = text.casefold().replace("ё", "е").replace(",", ".")
    text = re.sub(r"(\d)\s*(?:л|l)\b", r"\1", text)
    text = re.sub(r"[^\w.]+", " ", text)
    return " ".join(text.split())


def match_product(name, products):
    """Находит товар по названию: точно, а если нет — по единственному совпадению."""
    wanted = normalize_name(name)

    if not wanted:
        return None

    by_name = {normalize_name(product[1]): product for product in products}

    if wanted in by_name:
        return by_name[wanted]

    candidates = [p for key, p in by_name.items() if key.startswith(wanted) or wanted in key]

    return candidates[0] if len(candidates) == 1 else None


def parse_items(text, products):
    """Разбирает «Латте 0.4 — 23, Капучино 0.3 — 15».

    Возвращает (items, unknown): items — {product_id: количество},
    unknown — строки, которые не удалось понять.
    Строки шаблона без числа («Латте 0.4 — ») пропускаются.
    """
    items = {}
    unknown = []

    for chunk in SPLIT_PATTERN.split(text):
        line = chunk.strip().strip("-—–:•·").strip()

        if not line:
            continue

        # Строка шаблона без количества: название товара целиком.
        if normalize_name(line) in {normalize_name(p[1]) for p in products}:
            continue

        match = LINE_PATTERN.match(line)
        product = match_product(match.group("name"), products) if match else None

        if product is None:
            # Строка шаблона без количества: «Латте 0.4 —» или «Латте 0.4».
            if match_product(line, products) is None:
                unknown.append(line)
            continue

        quantity = int(match.group("qty"))

        if quantity == 0:
            continue

        if quantity > MAX_DAY_QUANTITY:
            unknown.append(line)
            continue

        items[product[0]] = items.get(product[0], 0) + quantity

    return items, unknown


def day_template(products):
    """Шаблон для копирования: каждая позиция с новой строки."""
    return "\n".join(f"{name} - " for _, name, *_ in products)


# ===== ЗАКРЫТИЕ ДНЯ =====


def is_day_closed(day):
    return fetch_one("SELECT 1 FROM day_closures WHERE day = ?", (day.isoformat(),)) is not None


def get_day_records(day):
    """Записи дня по видам: {kind: [(product_id, name, quantity)]}."""
    rows = fetch_all(
        """
        SELECT s.kind, s.product_id, p.name, SUM(s.quantity)
        FROM sales s
        JOIN products p ON p.id = s.product_id
        WHERE date(s.sale_date) = ?
        GROUP BY s.kind, s.product_id
        ORDER BY p.id
        """,
        (day.isoformat(),),
    )
    result = {}

    for kind, product_id, name, quantity in rows:
        result.setdefault(kind, []).append((product_id, name, quantity))

    return result


def close_day(day, sales, free=(), now=None):
    """Записывает итоги дня. Повторное закрытие того же дня заменяет его записи.

    sales — {product_id: количество} проданного;
    free — [(product_id, количество, kind)] бесплатного (угощения, брак).
    """
    stamp = f"{day.isoformat()} 23:59:00"
    closed_at = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

    with connect() as connection:
        cursor = connection.cursor()

        # Убираем прежние записи дня и возвращаем их ингредиенты на склад.
        old = cursor.execute(
            "SELECT product_id, quantity FROM sales WHERE date(sale_date) = ?",
            (day.isoformat(),),
        ).fetchall()

        for product_id, quantity in old:
            change_stock_by_recipe(cursor, product_id, -quantity)

        cursor.execute("DELETE FROM sales WHERE date(sale_date) = ?", (day.isoformat(),))

        entries = [(pid, qty, KIND_SALE) for pid, qty in sales.items()] + list(free)

        for product_id, quantity, kind in entries:
            if quantity <= 0:
                continue

            price_row = cursor.execute(
                "SELECT price FROM products WHERE id = ?", (product_id,)
            ).fetchone()

            if price_row is None:
                continue

            unit_price = price_row[0] if kind == KIND_SALE else 0
            unit_cost = product_unit_cost(cursor, product_id)

            cursor.execute(
                """
                INSERT INTO sales (product_id, quantity, sale_date, unit_price, unit_cost, kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, quantity, stamp, unit_price, unit_cost, kind),
            )
            change_stock_by_recipe(cursor, product_id, quantity)

        cursor.execute(
            """
            INSERT INTO day_closures (day, closed_at) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET closed_at = excluded.closed_at
            """,
            (day.isoformat(), closed_at),
        )
        connection.commit()


def last_closed_day():
    row = fetch_one("SELECT MAX(day) FROM day_closures")
    return date.fromisoformat(row[0]) if row and row[0] else None


# ===== ПОСТОЯННЫЕ РАСХОДЫ =====


def get_fixed_costs():
    return fetch_all("SELECT id, name, monthly_amount FROM fixed_costs ORDER BY id")


def add_fixed_cost(name, amount):
    with connect() as connection:
        connection.execute(
            "INSERT INTO fixed_costs (name, monthly_amount) VALUES (?, ?)",
            (name, amount),
        )
        connection.commit()


def update_fixed_cost(cost_id, amount):
    with connect() as connection:
        connection.execute(
            "UPDATE fixed_costs SET monthly_amount = ? WHERE id = ?",
            (amount, cost_id),
        )
        connection.commit()


def delete_fixed_cost(cost_id):
    with connect() as connection:
        connection.execute("DELETE FROM fixed_costs WHERE id = ?", (cost_id,))
        connection.commit()


def monthly_fixed_total():
    return fetch_one("SELECT COALESCE(SUM(monthly_amount), 0) FROM fixed_costs")[0]


def daily_fixed(day):
    """Доля постоянных расходов на один день месяца этого дня."""
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    return monthly_fixed_total() / days_in_month


def fixed_for_period(start, end):
    total_month = monthly_fixed_total()

    if total_month == 0 or end < start:
        return 0.0

    total = 0.0
    day = start

    while day <= end:
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        month_end = date(day.year, day.month, days_in_month)
        chunk_end = min(month_end, end)
        total += total_month / days_in_month * ((chunk_end - day).days + 1)
        day = chunk_end + timedelta(days=1)

    return total


def first_activity_day():
    row = fetch_one("""
        SELECT MIN(d) FROM (
            SELECT MIN(date(sale_date)) AS d FROM sales
            UNION ALL SELECT MIN(date(expense_date)) FROM expenses
            UNION ALL SELECT MIN(day) FROM day_closures
        )
        """)
    return date.fromisoformat(row[0]) if row and row[0] else None


# ===== РАЗОВЫЕ РАСХОДЫ, ЗАКУПКИ, ИНВЕНТАРИЗАЦИЯ =====


def save_expense(name, amount, now):
    with connect() as connection:
        connection.execute(
            "INSERT INTO expenses (name, amount, expense_date) VALUES (?, ?, ?)",
            (name, amount, now),
        )
        connection.commit()


def save_purchase(ingredient_id, quantity, total, now):
    unit_price = round(total / quantity, 2)

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO purchases (ingredient_id, quantity, purchase_price, purchase_date)
            VALUES (?, ?, ?, ?)
            """,
            (ingredient_id, quantity, unit_price, now),
        )
        connection.execute(
            "UPDATE ingredients SET stock = stock + ?, purchase_price = ? WHERE id = ?",
            (quantity, unit_price, ingredient_id),
        )
        connection.commit()

    return unit_price


def save_stocktake(ingredient_id, new_stock, now):
    with connect() as connection:
        cursor = connection.cursor()
        old_stock = cursor.execute(
            "SELECT stock FROM ingredients WHERE id = ?", (ingredient_id,)
        ).fetchone()[0]

        cursor.execute(
            "UPDATE ingredients SET stock = ? WHERE id = ?",
            (new_stock, ingredient_id),
        )
        cursor.execute(
            """
            INSERT INTO stock_adjustments (ingredient_id, old_stock, new_stock, adjusted_at)
            VALUES (?, ?, ?, ?)
            """,
            (ingredient_id, old_stock, new_stock, now),
        )
        connection.commit()

    return old_stock


def get_losses(start, end):
    """Неучтённые потери по инвентаризации: [(название, ед., количество, сумма)]."""
    return fetch_all(
        """
        SELECT i.name, i.unit,
               SUM(a.old_stock - a.new_stock),
               SUM((a.old_stock - a.new_stock) * i.purchase_price)
        FROM stock_adjustments a
        JOIN ingredients i ON i.id = a.ingredient_id
        WHERE a.new_stock < a.old_stock AND date(a.adjusted_at) BETWEEN ? AND ?
        GROUP BY i.id
        ORDER BY 4 DESC
        """,
        day_bounds(start, end),
    )


def get_low_stock():
    return fetch_all("""
        SELECT name, unit, stock, minimum_stock
        FROM ingredients
        WHERE stock <= minimum_stock
        ORDER BY stock ASC
        """)


# ===== ФИНАНСЫ =====


def estimate_tax(mode, revenue, profit_before_tax, days):
    if mode == "usn6":
        return revenue * 0.06

    if mode == "usn15":
        return max(profit_before_tax * 0.15, revenue * 0.01) if revenue > 0 else 0

    if mode == "patent":
        return get_patent_cost() / 365 * days

    return 0.0


def get_finances(start, end):
    """Все цифры за период: продажи, бесплатное, расходы, налог, чистая прибыль."""
    rows = fetch_all(
        """
        SELECT kind,
               COALESCE(SUM(quantity), 0),
               COALESCE(SUM(quantity * unit_price), 0),
               COALESCE(SUM(quantity * unit_cost), 0)
        FROM sales
        WHERE date(sale_date) BETWEEN ? AND ?
        GROUP BY kind
        """,
        day_bounds(start, end),
    )
    by_kind = {kind: (quantity, revenue, cost) for kind, quantity, revenue, cost in rows}

    sold, revenue, cost = by_kind.get(KIND_SALE, (0, 0, 0))
    treat_qty, _, treat_cost = by_kind.get(KIND_TREAT, (0, 0, 0))
    waste_qty, _, waste_cost = by_kind.get(KIND_WASTE, (0, 0, 0))

    expenses = fetch_one(
        """
        SELECT COALESCE(SUM(amount), 0) FROM expenses
        WHERE date(expense_date) BETWEEN ? AND ?
        """,
        day_bounds(start, end),
    )[0]

    first = first_activity_day()
    fixed_start = max(start, first) if first else end + timedelta(days=1)
    fixed = fixed_for_period(fixed_start, end)
    days = max((end - fixed_start).days + 1, 0)

    gross = revenue - cost
    before_tax = gross - treat_cost - waste_cost - expenses - fixed
    mode = get_tax_mode()
    tax = estimate_tax(mode, revenue, before_tax, days)

    return {
        "sold": sold,
        "revenue": revenue,
        "cost": cost,
        "gross": gross,
        "margin": gross / revenue * 100 if revenue > 0 else 0,
        "treat_qty": treat_qty,
        "treat_cost": treat_cost,
        "waste_qty": waste_qty,
        "waste_cost": waste_cost,
        "expenses": expenses,
        "fixed": fixed,
        "tax": tax,
        "tax_mode": mode,
        "net": before_tax - tax,
    }


def get_product_sales(start, end, kind=KIND_SALE):
    """[(name, quantity, revenue, cost)] по товарам за период."""
    return fetch_all(
        """
        SELECT p.name,
               COALESCE(SUM(s.quantity), 0),
               COALESCE(SUM(s.quantity * s.unit_price), 0),
               COALESCE(SUM(s.quantity * s.unit_cost), 0)
        FROM sales s
        JOIN products p ON p.id = s.product_id
        WHERE s.kind = ? AND date(s.sale_date) BETWEEN ? AND ?
        GROUP BY p.id
        ORDER BY 2 DESC
        """,
        (kind, *day_bounds(start, end)),
    )


def get_expense_breakdown(start, end):
    return fetch_all(
        """
        SELECT name, SUM(amount) FROM expenses
        WHERE date(expense_date) BETWEEN ? AND ?
        GROUP BY name
        ORDER BY 2 DESC
        """,
        day_bounds(start, end),
    )


# ===== ТОЧКА БЕЗУБЫТОЧНОСТИ =====


def average_cup(day, window_days=30):
    """Средняя цена и себестоимость одного проданного напитка."""
    start = day - timedelta(days=window_days)
    row = fetch_one(
        """
        SELECT COALESCE(SUM(quantity), 0),
               COALESCE(SUM(quantity * unit_price), 0),
               COALESCE(SUM(quantity * unit_cost), 0)
        FROM sales
        WHERE kind = 'sale' AND date(sale_date) BETWEEN ? AND ?
        """,
        (start.isoformat(), (day - timedelta(days=1)).isoformat()),
    )
    quantity, revenue, cost = row

    if quantity > 0:
        return revenue / quantity, cost / quantity

    products = get_product_costs()

    if not products:
        return 0, 0

    return (
        sum(p[2] for p in products) / len(products),
        sum(p[3] for p in products) / len(products),
    )


def breakeven(day):
    """Сколько напитков нужно продать за день, чтобы выйти в ноль.

    Возвращает (чашек, расходы дня, прибыль с чашки) или None, если считать не из чего.
    """
    price, cost = average_cup(day)
    mode = get_tax_mode()
    profit_per_cup = price - cost

    if mode == "usn6":
        profit_per_cup -= price * 0.06
    elif mode == "usn15":
        profit_per_cup -= (price - cost) * 0.15

    day_costs = daily_fixed(day)

    if mode == "patent":
        day_costs += get_patent_cost() / 365

    # Разовые расходы за 30 дней тоже ложатся на каждый день.
    start = day - timedelta(days=30)
    recent_expenses = fetch_one(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date(expense_date) BETWEEN ? AND ?",
        (start.isoformat(), (day - timedelta(days=1)).isoformat()),
    )[0]
    day_costs += recent_expenses / 30

    if day_costs <= 0 or profit_per_cup <= 0:
        return None

    return math.ceil(day_costs / profit_per_cup), day_costs, profit_per_cup


# ===== АНАЛИЗ ДНЯ =====


def previous_closed_days(day, limit=7):
    rows = fetch_all(
        "SELECT day FROM day_closures WHERE day < ? ORDER BY day DESC LIMIT ?",
        (day.isoformat(), limit),
    )
    return [date.fromisoformat(row[0]) for row in rows]


def day_context(day):
    """Всё, что нужно для анализа дня: цифры, сравнение, товары, склад."""
    finances = get_finances(day, day)
    previous = previous_closed_days(day)
    previous_revenue = [get_finances(d, d)["revenue"] for d in previous]
    previous_sold = [get_finances(d, d)["sold"] for d in previous]

    return {
        "day": day,
        "finances": finances,
        "avg_revenue": sum(previous_revenue) / len(previous_revenue) if previous_revenue else None,
        "avg_sold": sum(previous_sold) / len(previous_sold) if previous_sold else None,
        "products": get_product_sales(day, day),
        "product_costs": get_product_costs(),
        "breakeven": breakeven(day),
        "low_stock": get_low_stock(),
        "weekday": day.weekday(),
    }


def rule_insights(context):
    """Короткий анализ без ИИ: 2–4 конкретных наблюдения."""
    f = context["finances"]
    lines = []

    if context["avg_revenue"]:
        change = (f["revenue"] - context["avg_revenue"]) / context["avg_revenue"] * 100
        if abs(change) >= 10:
            word = "выше" if change > 0 else "ниже"
            lines.append(f"Выручка на {abs(change):.0f}% {word} средней за прошлые дни.")

    be = context["breakeven"]
    if be:
        cups = be[0]
        diff = f["sold"] - cups
        if diff >= 0:
            lines.append(f"Продано на {diff} напитков больше точки безубыточности ({cups}).")
        else:
            lines.append(f"До точки безубыточности не хватило {-diff} напитков (нужно {cups}).")

    priced = [p for p in context["product_costs"] if p[2] > 0]
    if priced:
        worst = min(priced, key=lambda p: (p[2] - p[3]) / p[2])
        margin = (worst[2] - worst[3]) / worst[2] * 100
        if margin < 60:
            lines.append(
                f"У «{worst[1]}» самая низкая маржа — {margin:.0f}%. "
                "Стоит поднять цену или пересмотреть рецепт."
            )

    if f["treat_cost"] + f["waste_cost"] > 0 and f["revenue"] > 0:
        share = (f["treat_cost"] + f["waste_cost"]) / f["revenue"] * 100
        if share >= 5:
            lines.append(f"Угощения и брак съели {share:.0f}% выручки.")

    if context["low_stock"]:
        names = ", ".join(row[0] for row in context["low_stock"][:3])
        lines.append(f"Пора закупить: {names}.")

    return lines[:4]
