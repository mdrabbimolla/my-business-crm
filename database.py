import os
import sqlite3


def _database_path():
    """Return a writable app-private database path on Android, with a desktop fallback."""
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        files_dir = activity.getFilesDir().getAbsolutePath()
        os.makedirs(files_dir, exist_ok=True)
        return os.path.join(files_dir, "database.db")
    except Exception:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, "database.db")


DATABASE = _database_path()


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
            project_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
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
            assigned_to TEXT,
            visit_date TEXT,
            visit_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            follow_up_date TEXT,
            note TEXT,
            status TEXT DEFAULT 'New',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            note_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS canceled_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            name TEXT,
            phone TEXT,
            project_id INTEGER,
            project_name TEXT,
            assigned_to TEXT,
            follow_up_date TEXT,
            note TEXT,
            cancelled_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT 'file',
            text_content TEXT,
            file_name TEXT,
            file_path TEXT,
            mime_type TEXT,
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

    # Add columns needed by newer CRM features to existing databases.
    def add_column_if_missing(table, column, definition):
        columns = conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
        names = [column["name"] for column in columns]

        if column not in names:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    add_column_if_missing("customers", "project_id", "INTEGER")
    add_column_if_missing("leads", "assigned_to", "TEXT")
    add_column_if_missing("leads", "visit_date", "TEXT")
    add_column_if_missing("leads", "visit_time", "TEXT")
    add_column_if_missing("leads", "visit_status", "TEXT DEFAULT 'Planned'")
    add_column_if_missing("leads", "visit_completed_date", "TEXT")
    add_column_if_missing("followups", "lead_id", "INTEGER")
    add_column_if_missing("followups", "status", "TEXT DEFAULT 'New'")

    # Add user management columns if they do not exist.
    add_column_if_missing("users", "name", "TEXT")
    add_column_if_missing("users", "role", "TEXT DEFAULT 'Sales'")
    add_column_if_missing("users", "active", "INTEGER DEFAULT 1")

    existing_user = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not existing_user:
        conn.execute("""
            INSERT INTO users (username, password, name, role, active)
            VALUES (?, ?, ?, ?, ?)
        """, ("admin", "1234", "Administrator", "Admin", 1))

    existing_setting = conn.execute(
        "SELECT id FROM receipt_settings WHERE id = 1"
    ).fetchone()

    if not existing_setting:
        conn.execute("""
            INSERT INTO receipt_settings (id, header_name)
            VALUES (1, ?)
        """, ("ARKAM MC PARK",))

    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_files_project ON project_files(project_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_notes_lead_id ON lead_notes(lead_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_notes_date ON lead_notes(note_date, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_project ON leads(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_visit_status ON leads(visit_status, visit_completed_date)")
    conn.execute("""
        INSERT INTO lead_notes (lead_id, note, note_date)
        SELECT id, notes, substr(created_at, 1, 10)
        FROM leads
        WHERE notes IS NOT NULL
        AND TRIM(notes) != ''
        AND NOT EXISTS (
            SELECT 1 FROM lead_notes ln WHERE ln.lead_id = leads.id
        )
    """)

    # Move any older Cancel follow-ups into the dedicated Cancelled Leads area.
    conn.execute("""
        INSERT INTO canceled_leads
        (lead_id, name, phone, project_id, project_name, assigned_to,
         follow_up_date, note, cancelled_date)
        SELECT
            f.lead_id,
            f.name,
            f.phone,
            l.project_id,
            p.name,
            l.assigned_to,
            f.follow_up_date,
            f.note,
            substr(COALESCE(f.created_at, CURRENT_TIMESTAMP), 1, 10)
        FROM followups f
        LEFT JOIN leads l ON f.lead_id = l.id
        LEFT JOIN projects p ON l.project_id = p.id
        WHERE f.status = 'Cancel'
        AND NOT EXISTS (
            SELECT 1 FROM canceled_leads c
            WHERE c.lead_id = f.lead_id
            AND f.lead_id IS NOT NULL
        )
    """)

    conn.execute("""
        UPDATE leads
        SET follow_up_date = NULL
        WHERE id IN (
            SELECT lead_id
            FROM followups
            WHERE status = 'Cancel'
            AND lead_id IS NOT NULL
        )
    """)

    conn.execute("DELETE FROM followups WHERE status = 'Cancel'")

    conn.execute("""
        INSERT INTO followups
        (lead_id, name, phone, follow_up_date, note, status)
        SELECT leads.id, leads.name, leads.phone, leads.follow_up_date, COALESCE(leads.notes, ''), 'New'
        FROM leads
        WHERE leads.follow_up_date IS NOT NULL
        AND TRIM(leads.follow_up_date) != ''
        AND NOT EXISTS (
            SELECT 1 FROM followups f WHERE f.lead_id = leads.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM canceled_leads c WHERE c.lead_id = leads.id
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_followups_lead_id ON followups(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canceled_leads_date ON canceled_leads(cancelled_date, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canceled_leads_lead_id ON canceled_leads(lead_id)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database created successfully!")
