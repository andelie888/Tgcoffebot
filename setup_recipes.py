import sqlite3

DB_NAME = "coffee_manager.db"

connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()

# Получаем ID товаров
cursor.execute("SELECT id, name FROM products")
products = {name: product_id for product_id, name in cursor.fetchall()}

# Получаем ID ингредиентов
cursor.execute("SELECT id, name FROM ingredients")
ingredients = {name: ingredient_id for ingredient_id, name in cursor.fetchall()}

recipes = [
    # Латте: 18 г кофе + 0.25 л молока
    (products["Латте"], ingredients["Кофе в зёрнах"], 0.018),
    (products["Латте"], ingredients["Молоко"], 0.25),

    # Матча: 8 г матча + 0.25 л молока
    (products["Матча"], ingredients["Матча"], 0.008),
    (products["Матча"], ingredients["Молоко"], 0.25),

    # Капучино: 18 г кофе + 0.18 л молока
    (products["Капучино"], ingredients["Кофе в зёрнах"], 0.018),
    (products["Капучино"], ingredients["Молоко"], 0.18),
]

for product_id, ingredient_id, quantity in recipes:
    cursor.execute(
        """
        INSERT INTO recipes
        (product_id, ingredient_id, quantity)
        VALUES (?, ?, ?)
        """,
        (product_id, ingredient_id, quantity),
    )

connection.commit()
connection.close()

print("Рецепты добавлены.")