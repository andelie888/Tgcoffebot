"""Заполняет базу Coffee Manager данными конкретной кофейни.

Запуск:
    python3 setup_shop.py shop_data.json          — заполнить пустую базу
    python3 setup_shop.py shop_data.json --reset  — начать с чистой базы
                                                    (старая база сохранится копией)

Формат shop_data.json — см. shop_data.example.json.
Себестоимость считается по рецепту и ценам ингредиентов,
если в товаре не указано "cost" вручную.
"""

import json
import shutil
import sqlite3
import sys

from datetime import datetime
from pathlib import Path

from config import DB_NAME
from database import create_tables, migrate_database


def load_data(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def expand_products(products):
    """Разворачивает товары с объёмами в отдельные позиции: «Латте 0.3», «Латте 0.4»."""
    items = []

    for product in products:
        if "sizes" in product:
            for size, details in product["sizes"].items():
                items.append({"name": f"{product['name']} {size}", **details})
        else:
            items.append(product)

    return items


def validate(data):
    errors = []
    ingredient_names = set()

    for ingredient in data.get("ingredients", []):
        for field in ("name", "unit"):
            if not ingredient.get(field):
                errors.append(f"Ингредиент без поля «{field}»: {ingredient}")
        ingredient_names.add(ingredient.get("name"))

    product_names = set()

    for product in expand_products(data.get("products", [])):
        name = product.get("name")

        if not name:
            errors.append(f"Товар без названия: {product}")
            continue

        if name.casefold() in product_names:
            errors.append(f"Товар повторяется: {name}")
        product_names.add(name.casefold())

        words = name.split()
        if len(words) > 1 and words[-1].isdigit():
            errors.append(f"Название не должно кончаться целым числом (не отличить от количества): {name}")

        if not product.get("price") or product["price"] <= 0:
            errors.append(f"Нет цены у товара: {name}")

        for ingredient_name in product.get("recipe", {}):
            if ingredient_name not in ingredient_names:
                errors.append(f"«{name}»: ингредиент «{ingredient_name}» не найден в списке")

    return errors


def recipe_cost(recipe, ingredient_prices):
    return round(
        sum(quantity * ingredient_prices[name] for name, quantity in recipe.items()),
        2,
    )


def reset_database():
    db_path = Path(DB_NAME)

    if db_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        copy = db_path.with_name(f"{db_path.stem}_before_reset_{stamp}.db")
        shutil.copy2(db_path, copy)
        db_path.unlink()
        print(f"Старая база сохранена: {copy.name}")


def fill(data):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    ingredient_ids = {}
    ingredient_prices = {}

    for ingredient in data.get("ingredients", []):
        cursor.execute(
            """
            INSERT INTO ingredients (name, unit, stock, purchase_price, minimum_stock)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ingredient["name"],
                ingredient["unit"],
                ingredient.get("stock", 0),
                ingredient.get("price", 0),
                ingredient.get("minimum", 0),
            ),
        )
        ingredient_ids[ingredient["name"]] = cursor.lastrowid
        ingredient_prices[ingredient["name"]] = ingredient.get("price", 0)

    report = []

    for product in expand_products(data.get("products", [])):
        recipe = product.get("recipe", {})
        cost = product.get("cost")

        if cost is None:
            cost = recipe_cost(recipe, ingredient_prices)

        cursor.execute(
            "INSERT INTO products (name, price, cost) VALUES (?, ?, ?)",
            (product["name"], product["price"], cost),
        )
        product_id = cursor.lastrowid

        for ingredient_name, quantity in recipe.items():
            cursor.execute(
                """
                INSERT INTO recipes (product_id, ingredient_id, quantity)
                VALUES (?, ?, ?)
                """,
                (product_id, ingredient_ids[ingredient_name], quantity),
            )

        margin = (product["price"] - cost) / product["price"] * 100
        report.append((product["name"], product["price"], cost, margin, bool(recipe)))

    # Постоянные расходы в месяц и налог — по желанию.
    for name, amount in data.get("fixed_costs", {}).items():
        cursor.execute(
            "INSERT INTO fixed_costs (name, monthly_amount) VALUES (?, ?)",
            (name, amount),
        )

    if data.get("tax_mode"):
        cursor.execute(
            "INSERT OR REPLACE INTO shop_settings (key, value) VALUES ('tax_mode', ?)",
            (data["tax_mode"],),
        )

    if data.get("patent_cost_year"):
        cursor.execute(
            "INSERT OR REPLACE INTO shop_settings (key, value) VALUES ('patent_cost_year', ?)",
            (str(data["patent_cost_year"]),),
        )

    connection.commit()
    connection.close()

    return report


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    data = load_data(sys.argv[1])
    errors = validate(data)

    if errors:
        print("❌ В данных есть ошибки, база не изменена:\n")
        for error in errors:
            print(f"  • {error}")
        sys.exit(1)

    if "--reset" in sys.argv:
        reset_database()

    create_tables()
    migrate_database()

    with sqlite3.connect(DB_NAME) as connection:
        has_products = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    if has_products:
        print(
            "❌ В базе уже есть товары. Чтобы начать с чистой базы, "
            "запустите с флагом --reset (старая база сохранится копией)."
        )
        sys.exit(1)

    report = fill(data)

    print(f"✅ Кофейня «{data.get('shop_name', 'без названия')}» загружена.\n")
    print(f"Ингредиентов: {len(data.get('ingredients', []))}, позиций в меню: {len(report)}\n")

    for name, price, cost, margin, has_recipe in report:
        recipe_mark = "" if has_recipe else "  ⚠️ без рецепта — склад не списывается"
        print(f"  {name:<24} цена {price:>6g} ₽  себест. {cost:>7g} ₽  маржа {margin:4.0f}%{recipe_mark}")


if __name__ == "__main__":
    main()
