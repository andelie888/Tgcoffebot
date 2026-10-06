import sqlite3

from config import DB_NAME


def get_connection():
    return sqlite3.connect(DB_NAME)


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cost REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            stock REAL NOT NULL DEFAULT 0,
            purchase_price REAL NOT NULL DEFAULT 0,
            minimum_stock REAL NOT NULL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            sale_date TEXT NOT NULL,
            unit_price REAL,
            unit_cost REAL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            purchase_price REAL NOT NULL,
            purchase_date TEXT NOT NULL,
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            expense_date TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            notifications_enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Журнал инвентаризаций: было → стало, чтобы видеть расхождения.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingredient_id INTEGER NOT NULL,
            old_stock REAL NOT NULL,
            new_stock REAL NOT NULL,
            adjusted_at TEXT NOT NULL,
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
        )
    """)

    connection.commit()
    connection.close()


def get_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")

    return {row[1] for row in cursor.fetchall()}


def migrate_database():
    connection = get_connection()
    cursor = connection.cursor()

    if "is_active" not in get_columns(cursor, "products"):
        cursor.execute("""
            ALTER TABLE products
            ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
        """)

    sales_columns = get_columns(cursor, "sales")

    if "unit_price" not in sales_columns:
        cursor.execute("ALTER TABLE sales ADD COLUMN unit_price REAL")

    if "unit_cost" not in sales_columns:
        cursor.execute("ALTER TABLE sales ADD COLUMN unit_cost REAL")

    # Старые продажи получают цену и себестоимость товара на момент миграции.
    cursor.execute("""
        UPDATE sales
        SET unit_price = (
            SELECT price FROM products WHERE products.id = sales.product_id
        )
        WHERE unit_price IS NULL
    """)

    cursor.execute("""
        UPDATE sales
        SET unit_cost = (
            SELECT cost FROM products WHERE products.id = sales.product_id
        )
        WHERE unit_cost IS NULL
    """)

    connection.commit()
    connection.close()


if __name__ == "__main__":
    create_tables()
    migrate_database()
    print("База данных создана.")
