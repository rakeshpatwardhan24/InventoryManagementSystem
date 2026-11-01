import sqlite3
from pathlib import Path

# Store the database in the project folder.
DATABASE_PATH = Path(__file__).resolve().parent / "inventory.db"


def get_connection():
    """Create and return a database connection."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    """Create the tables if they do not already exist."""
    connection = get_connection()

    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                address TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL CHECK (price >= 0),
                quantity INTEGER NOT NULL DEFAULT 0
                    CHECK (quantity >= 0),
                reorder_level INTEGER NOT NULL DEFAULT 5
                    CHECK (reorder_level >= 0),
                supplier_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supplier_id)
                    REFERENCES suppliers(supplier_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS stock_movements (
                movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                movement_type TEXT NOT NULL
                    CHECK (movement_type IN ('IN', 'OUT')),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id)
                    REFERENCES products(product_id)
            );

                        CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_products_name
                ON products(name);

            CREATE INDEX IF NOT EXISTS idx_products_category
                ON products(category);

            CREATE INDEX IF NOT EXISTS idx_stock_product
                ON stock_movements(product_id);
            
            CREATE INDEX IF NOT EXISTS idx_stock_created
                ON stock_movements(created_at);
        """)

        connection.commit()
        print("Database initialized successfully!")
        print(f"Database location: {DATABASE_PATH}")

    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database()
