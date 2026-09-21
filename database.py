import sqlite3

DATABASE = "database.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            business TEXT,
            notes TEXT,
            follow_up TEXT,
            sales REAL DEFAULT 0,
            paid REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            location TEXT,
            status TEXT DEFAULT 'Active',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT NOT NULL UNIQUE,
            project_id INTEGER,
            notes TEXT,
            follow_up_date TEXT,
            status TEXT DEFAULT 'New',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_date TEXT,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            expense_date TEXT,
            category TEXT,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_settings (
            id INTEGER PRIMARY KEY,
            header_name TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # Add user management columns if they do not exist
    user_columns = conn.execute(
        "PRAGMA table_info(users)"
    ).fetchall()

    column_names = [column["name"] for column in user_columns]

    if "name" not in column_names:
        conn.execute(
            "ALTER TABLE users ADD COLUMN name TEXT"
        )

    if "role" not in column_names:
        conn.execute(
            "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'Sales'"
        )

    if "active" not in column_names:
        conn.execute(
            "ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1"
        )

    existing_user = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not existing_user:
        conn.execute("""
            INSERT INTO users (username, password)
            VALUES (?, ?)
        """, ("admin", "1234"))

    existing_setting = conn.execute(
        "SELECT id FROM receipt_settings WHERE id = 1"
    ).fetchone()

    if not existing_setting:
        conn.execute("""
            INSERT INTO receipt_settings (id, header_name)
            VALUES (1, ?)
        """, ("ARKAM MC PARK",))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database created successfully!")
