import sqlite3


DB_NAME = "coffee_manager.db"


products = [
    ("Латте", 5.00, 1.00),
    ("Матча", 6.00, 1.50),
    ("Капучино", 4.50, 1.30),
]


connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()


for name, price, cost in products:
    cursor.execute(
        """
        INSERT INTO products (name, price, cost)
        VALUES (?, ?, ?)
        """,
        (name, price, cost),
    )


connection.commit()
connection.close()

print("Товары добавлены.")