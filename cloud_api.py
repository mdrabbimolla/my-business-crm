import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, jsonify, request, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
API_SECRET = os.environ.get("CRM_API_SECRET", "").strip()

def db():
    path = os.environ.get("CRM_CLOUD_DB", os.path.join(os.path.dirname(__file__), "cloud.db"))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_cloud_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, name TEXT, role TEXT NOT NULL DEFAULT 'Sales',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        location TEXT, status TEXT NOT NULL DEFAULT 'Active', notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT NOT NULL UNIQUE,
        project_id INTEGER, notes TEXT, follow_up_date TEXT, status TEXT NOT NULL DEFAULT 'New',
        created_by TEXT, assigned_to TEXT, visit_date TEXT, visit_time TEXT,
        visit_status TEXT DEFAULT 'Planned', visit_completed_date TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT,
        address TEXT, business TEXT, notes TEXT, follow_up TEXT,
        sales REAL NOT NULL DEFAULT 0, paid REAL NOT NULL DEFAULT 0,
        project_id INTEGER, created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, name TEXT, phone TEXT,
        follow_up_date TEXT, note TEXT, status TEXT DEFAULT 'New', created_at TEXT NOT NULL,
        FOREIGN KEY(lead_id) REFERENCES leads(id)
    );
    CREATE INDEX IF NOT EXISTS idx_cloud_leads_project ON leads(project_id);
    CREATE INDEX IF NOT EXISTS idx_cloud_customers_project ON customers(project_id);
    CREATE TABLE IF NOT EXISTS lead_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, note TEXT NOT NULL,
        note_date TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS canceled_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, name TEXT, phone TEXT,
        project_id INTEGER, project_name TEXT, assigned_to TEXT, follow_up_date TEXT,
        note TEXT, cancelled_date TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(lead_id) REFERENCES leads(id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, amount REAL NOT NULL,
        payment_date TEXT, note TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL NOT NULL, expense_date TEXT,
        category TEXT, note TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS receipt_settings (
        id INTEGER PRIMARY KEY,
        header_name TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        title TEXT NOT NULL, item_type TEXT NOT NULL, file_name TEXT,
        mime_type TEXT, text_content TEXT, file_data BLOB, created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_cloud_followups_date ON followups(follow_up_date);
    CREATE INDEX IF NOT EXISTS idx_cloud_lead_notes_lead ON lead_notes(lead_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_cloud_canceled_date ON canceled_leads(cancelled_date, id DESC);
    CREATE INDEX IF NOT EXISTS idx_cloud_payments_customer ON payments(customer_id, id DESC);
    """)
    conn.commit()
    conn.close()

def require_api_secret(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not API_SECRET:
            return jsonify(error="CRM_API_SECRET is not configured"), 503
        if not secrets.compare_digest(request.headers.get("X-CRM-API-SECRET", ""), API_SECRET):
            return jsonify(error="Unauthorized"), 401
        return fn(*args, **kwargs)
    return wrapper

def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify(error="Authentication required"), 401
        conn = db()
        user = conn.execute("""SELECT users.* FROM auth_tokens
            JOIN users ON users.id = auth_tokens.user_id
            WHERE auth_tokens.token = ? AND users.active = 1""",
            (header[7:].strip(),)).fetchone()
        conn.close()
        if not user:
            return jsonify(error="Invalid or expired session"), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

@app.get("/api/health")
def health():
    init_cloud_db()
    return jsonify(ok=True, service="my-business-crm-central-api")

@app.get("/api/receipt-settings")
@require_token
def get_receipt_settings():
    init_cloud_db()
    conn = db()
    row = conn.execute("SELECT * FROM receipt_settings WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO receipt_settings(id,header_name,updated_at) VALUES(1,?,?)",
                     ("ARKAM MC PARK", datetime.now(timezone.utc).isoformat()))
        conn.commit()
        row = conn.execute("SELECT * FROM receipt_settings WHERE id=1").fetchone()
    conn.close()
    return jsonify(settings=dict(row))


@app.put("/api/receipt-settings")
@require_token
def update_receipt_settings():
    data = request.get_json(silent=True) or {}
    header_name = (data.get("header_name") or "").strip()
    if not header_name:
        return jsonify(error="header_name is required"), 400
    if len(header_name) > 120:
        return jsonify(error="header_name is too long"), 400
    if g.user["role"] not in {"Admin", "Manager"}:
        return jsonify(error="Admin or Manager access required"), 403
    init_cloud_db()
    conn = db()
    conn.execute("INSERT INTO receipt_settings(id,header_name,updated_at) VALUES(1,?,?) "
                 "ON CONFLICT(id) DO UPDATE SET header_name=excluded.header_name, updated_at=excluded.updated_at",
                 (header_name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM receipt_settings WHERE id=1").fetchone()
    conn.close()
    return jsonify(settings=dict(row))


@app.post("/api/bootstrap-user")
@require_api_secret
def bootstrap_user():
    data = request.get_json(silent=True) or {}
    username, password = (data.get("username") or "").strip(), data.get("password") or ""
    name, role = (data.get("name") or "").strip(), (data.get("role") or "Sales").strip()
    if not username or not password:
        return jsonify(error="username and password are required"), 400
    if role not in {"Admin", "Manager", "Sales"}:
        return jsonify(error="invalid role"), 400
    conn = db()
    if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        conn.close()
        return jsonify(error="username already exists"), 409
    cur = conn.execute("""INSERT INTO users(username,password_hash,name,role,active,created_at)
        VALUES (?,?,?,?,1,?)""",
        (username, generate_password_hash(password), name, role, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return jsonify(ok=True, user_id=user_id), 201

@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username, password = (data.get("username") or "").strip(), data.get("password") or ""
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        conn.close()
        return jsonify(error="Wrong username or password"), 401
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO auth_tokens(token,user_id,created_at) VALUES (?,?,?)",
                 (token, user["id"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return jsonify(ok=True, token=token, user={
        "id":user["id"],"username":user["username"],"name":user["name"],"role":user["role"]})

@app.get("/api/me")
@require_token
def me():
    return jsonify(ok=True, user={"id":g.user["id"],"username":g.user["username"],
                                  "name":g.user["name"],"role":g.user["role"]})

@app.get("/api/users")
@require_token
def list_users():
    if g.user["role"] != "Admin":
        return jsonify(error="Admin access required"), 403
    conn = db()
    rows = conn.execute("SELECT id, username, name, role, active, created_at FROM users ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify(users=[dict(row) for row in rows])

@app.post("/api/users")
@require_token
def create_user():
    if g.user["role"] != "Admin":
        return jsonify(error="Admin access required"), 403
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "Sales").strip()
    try:
        active = int(data.get("active", 1))
    except (TypeError, ValueError):
        return jsonify(error="invalid active value"), 400
    if not username or not password:
        return jsonify(error="username and password are required"), 400
    if role not in {"Admin", "Manager", "Sales"}:
        return jsonify(error="invalid role"), 400
    conn = db()
    try:
        cur = conn.execute("""INSERT INTO users(username,password_hash,name,role,active,created_at)
            VALUES (?,?,?,?,?,?)""",
            (username, generate_password_hash(password), name, role, 1 if active else 0,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        row = conn.execute("SELECT id, username, name, role, active, created_at FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(ok=True, user=dict(row), user_id=cur.lastrowid), 201
    except sqlite3.IntegrityError:
        return jsonify(error="username already exists"), 409
    finally:
        conn.close()

@app.put("/api/users/<int:user_id>/password")
@require_token
def reset_user_password(user_id):
    if g.user["role"] != "Admin":
        return jsonify(error="Admin access required"), 403
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if not password:
        return jsonify(error="password is required"), 400
    conn = db()
    row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="user not found"), 404
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
    conn.commit()
    conn.close()
    return jsonify(ok=True)

@app.put("/api/me/password")
@require_token
def change_my_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    if not current_password or not new_password:
        return jsonify(error="current and new passwords are required"), 400
    if not check_password_hash(g.user["password_hash"], current_password):
        return jsonify(error="Current password is incorrect"), 400
    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), g.user["id"]))
    conn.commit()
    conn.close()
    return jsonify(ok=True)

@app.get("/api/projects")
@require_token
def list_projects():
    conn = db()
    rows = conn.execute("SELECT * FROM projects WHERE status='Active' ORDER BY name").fetchall()
    conn.close()
    return jsonify(projects=[dict(r) for r in rows])

@app.post("/api/projects")
@require_token
def create_project():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(error="project name is required"), 400
    conn = db()
    try:
        cur = conn.execute("""INSERT INTO projects(name,location,status,notes,created_at)
            VALUES (?,?,?,?,?)""",
            (name,data.get("location"),data.get("status") or "Active",data.get("notes"),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(project=dict(row)), 201
    except sqlite3.IntegrityError:
        return jsonify(error="project already exists"), 409
    finally:
        conn.close()

@app.get("/api/leads")
@require_token
def list_leads():
    conn = db()
    conditions = []
    params = []

    if g.user["role"] == "Sales":
        conditions.append("leads.assigned_to = ?")
        params.append(g.user["username"])

    search = (request.args.get("search") or "").strip()
    if search:
        conditions.append("(leads.name LIKE ? OR leads.phone LIKE ?)")
        like = "%" + search + "%"
        params.extend([like, like])

    project_id = (request.args.get("project_id") or "").strip()
    if project_id:
        try:
            project_id_value = int(project_id)
        except ValueError:
            conn.close()
            return jsonify(error="invalid project_id"), 400
        conditions.append("leads.project_id = ?")
        params.append(project_id_value)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    query = """
        SELECT leads.*,
               projects.name AS project_name,
               COALESCE(assigned_user.name, leads.assigned_to) AS assigned_user_name,
               latest_note.note AS latest_note,
               latest_note.note_date AS latest_note_date
        FROM leads
        LEFT JOIN projects ON projects.id = leads.project_id
        LEFT JOIN users AS assigned_user ON assigned_user.username = leads.assigned_to
        LEFT JOIN (
            SELECT ln.lead_id, ln.note, ln.note_date
            FROM lead_notes ln
            INNER JOIN (
                SELECT lead_id, MAX(id) AS max_id
                FROM lead_notes
                GROUP BY lead_id
            ) newest ON newest.max_id = ln.id
        ) latest_note ON latest_note.lead_id = leads.id
    """ + where + " ORDER BY leads.id DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify(leads=[dict(r) for r in rows])

@app.post("/api/leads")
@require_token
def create_lead():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify(error="phone number is required"), 400
    conn = db()
    duplicate = conn.execute("SELECT id,name FROM leads WHERE phone=?", (phone,)).fetchone()
    customer_duplicate = conn.execute("SELECT id,name FROM customers WHERE phone=?", (phone,)).fetchone()
    if duplicate or customer_duplicate:
        conn.close()
        return jsonify(error="phone number already exists",
                       lead_id=duplicate["id"] if duplicate else None,
                       customer_id=customer_duplicate["id"] if customer_duplicate else None), 409
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("""INSERT INTO leads
        (name,phone,project_id,notes,follow_up_date,status,created_by,assigned_to,visit_date,visit_time,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("name"),phone,data.get("project_id"),data.get("notes"),data.get("follow_up_date"),
         data.get("status") or "New",g.user["username"],data.get("assigned_to") or g.user["username"],
         data.get("visit_date"),data.get("visit_time"),now))
    conn.commit()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(lead=dict(row)), 201

@app.get("/api/customers")
@require_token
def list_customers():
    conn = db()
    conditions = []
    params = []
    search = (request.args.get("search") or "").strip()
    customer_filter = (request.args.get("filter") or "").strip()
    project_id = (request.args.get("project_id") or "").strip()

    if search:
        like = "%" + search + "%"
        conditions.append("(customers.name LIKE ? OR customers.phone LIKE ? OR customers.business LIKE ? OR customers.address LIKE ?)")
        params.extend([like, like, like, like])
    if customer_filter == "due":
        conditions.append("(customers.sales - customers.paid) > 0")
    elif customer_filter == "paid":
        conditions.append("(customers.sales - customers.paid) <= 0")
    if project_id:
        try:
            project_id = int(project_id)
        except ValueError:
            conn.close()
            return jsonify(error="invalid project_id"), 400
        conditions.append("customers.project_id = ?")
        params.append(project_id)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute("""
        SELECT customers.*, (customers.sales - customers.paid) AS due,
               projects.name AS project_name
        FROM customers
        LEFT JOIN projects ON projects.id = customers.project_id
    """ + where + " ORDER BY customers.id DESC", params).fetchall()
    conn.close()
    return jsonify(customers=[dict(r) for r in rows])


@app.get("/api/dashboard")
@require_token
def dashboard_metrics():
    today = datetime.now(timezone.utc).date().isoformat()
    conn = db()

    total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    total_sales = conn.execute("SELECT COALESCE(SUM(sales),0) FROM customers").fetchone()[0]

    if g.user["role"] == "Sales":
        visibility = " AND leads.assigned_to = ?"
        visibility_params = [g.user["username"]]
        followup_visibility = """ AND EXISTS (
            SELECT 1 FROM leads
            WHERE leads.id = followups.lead_id
              AND leads.assigned_to = ?
        )"""
        canceled_visibility = " AND assigned_to = ?"
    else:
        visibility = ""
        visibility_params = []
        followup_visibility = ""
        canceled_visibility = ""

    today_followups = conn.execute(
        "SELECT followups.* FROM followups WHERE follow_up_date = ?" + followup_visibility +
        " ORDER BY id DESC",
        [today] + ([g.user["username"]] if g.user["role"] == "Sales" else [])
    ).fetchall()

    missed = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE follow_up_date < ?" + followup_visibility,
        [today] + ([g.user["username"]] if g.user["role"] == "Sales" else [])
    ).fetchone()[0]
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE follow_up_date > ?" + followup_visibility,
        [today] + ([g.user["username"]] if g.user["role"] == "Sales" else [])
    ).fetchone()[0]

    visit_count = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE visit_date IS NOT NULL AND TRIM(visit_date) != ''" + visibility,
        visibility_params
    ).fetchone()[0]

    cancel_count = conn.execute(
        "SELECT COUNT(*) FROM canceled_leads WHERE 1=1" + canceled_visibility,
        ([g.user["username"]] if g.user["role"] == "Sales" else [])
    ).fetchone()[0]

    conn.close()
    return jsonify(
        total_customers=total_customers,
        total_sales=total_sales,
        today_followup_count=len(today_followups),
        missed_followup_count=missed,
        upcoming_followup_count=upcoming,
        visit_count=visit_count,
        cancel_count=cancel_count,
        today_followups=[dict(row) for row in today_followups]
    )

@app.get("/api/reports")
@require_token
def report_metrics():
    payment_date = (request.args.get("payment_date") or "").strip()
    expense_date = (request.args.get("expense_date") or "").strip()
    conn = db()

    total_sales = conn.execute("SELECT COALESCE(SUM(sales),0) FROM customers").fetchone()[0]
    total_paid = conn.execute("SELECT COALESCE(SUM(paid),0) FROM customers").fetchone()[0]
    total_due = total_sales - total_paid
    total_expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    total_payments = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0]
    net_profit = total_sales - total_expenses

    category_expenses = conn.execute("""
        SELECT category, COALESCE(SUM(amount),0) AS total
        FROM expenses GROUP BY category ORDER BY total DESC
    """).fetchall()

    customers = conn.execute("""
        SELECT id,name,phone,sales,paid,(sales-paid) AS due
        FROM customers ORDER BY id DESC
    """).fetchall()

    payment_history = conn.execute("""
        SELECT payments.id,payments.amount,payments.payment_date,payments.note,
               customers.name,customers.phone
        FROM payments JOIN customers ON payments.customer_id=customers.id
        ORDER BY payments.id DESC
    """).fetchall()

    date_payments = []
    if payment_date:
        date_payments = conn.execute("""
            SELECT payments.id,payments.amount,payments.payment_date,payments.note,
                   customers.name,customers.phone
            FROM payments JOIN customers ON payments.customer_id=customers.id
            WHERE payments.payment_date=? ORDER BY payments.id DESC
        """, (payment_date,)).fetchall()

    date_expenses = []
    if expense_date:
        date_expenses = conn.execute("""
            SELECT * FROM expenses WHERE expense_date=? ORDER BY id DESC
        """, (expense_date,)).fetchall()

    conn.close()
    return jsonify(
        total_sales=total_sales,
        total_paid=total_paid,
        total_due=total_due,
        total_payments=total_payments,
        total_expenses=total_expenses,
        net_profit=net_profit,
        category_expenses=[dict(row) for row in category_expenses],
        customers=[dict(row) for row in customers],
        payment_history=[dict(row) for row in payment_history],
        date_payments=[dict(row) for row in date_payments],
        payment_date=payment_date or None,
        date_expenses=[dict(row) for row in date_expenses],
        expense_date=expense_date or None
    )

from central_api_core import register_core_routes
from central_project_files import register_project_file_routes
register_core_routes(app, db, require_token)
register_project_file_routes(app, db, require_token)

if __name__ == "__main__":
    init_cloud_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8000")))
