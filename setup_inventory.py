import sqlite3

DB_NAME = "coffee_manager.db"

ingredients = [
    ("Кофе в зёрнах", "кг", 5.0, 18.0, 1.0),
    ("Молоко", "л", 20.0, 2.0, 5.0),
    ("Матча", "кг", 1.0, 30.0, 0.2),
    ("Сахар", "кг", 5.0, 1.5, 1.0),
]

connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()

for name, unit, stock, purchase_price, minimum_stock in ingredients:
    cursor.execute(
        """
        INSERT INTO ingredients
        (name, unit, stock, purchase_price, minimum_stock)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, unit, stock, purchase_price, minimum_stock),
    )

connection.commit()
connection.close()

print("Ингредиенты добавлены.")