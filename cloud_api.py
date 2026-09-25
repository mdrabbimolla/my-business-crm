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
        id INTEGER PRIMARY KEY, header_name TEXT NOT NULL
    );
    INSERT OR IGNORE INTO receipt_settings(id, header_name) VALUES (1, 'ARKAM MC PARK');
    CREATE INDEX IF NOT EXISTS idx_cloud_leads_project ON leads(project_id);
    CREATE INDEX IF NOT EXISTS idx_cloud_customers_project ON customers(project_id);
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
    if g.user["role"] == "Sales":
        rows = conn.execute("SELECT * FROM leads WHERE assigned_to=? ORDER BY id DESC",
                            (g.user["username"],)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
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
    rows = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify(customers=[dict(r) for r in rows])


def now_iso():
    return datetime.now(timezone.utc).isoformat()

def lead_visible(conn, lead_id):
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row or (g.user["role"] == "Sales" and row["assigned_to"] != g.user["username"]):
        return None
    return row

def ensure_phone_available(conn, phone, exclude_lead_id=None, exclude_customer_id=None):
    if not phone:
        return None
    q = "SELECT id,name FROM leads WHERE phone=?"; p = [phone]
    if exclude_lead_id is not None: q += " AND id!=?"; p.append(exclude_lead_id)
    row = conn.execute(q,p).fetchone()
    if row: return {"type":"lead","id":row["id"],"name":row["name"]}
    q = "SELECT id,name FROM customers WHERE phone=?"; p = [phone]
    if exclude_customer_id is not None: q += " AND id!=?"; p.append(exclude_customer_id)
    row = conn.execute(q,p).fetchone()
    return {"type":"customer","id":row["id"],"name":row["name"]} if row else None

@app.put("/api/projects/<int:project_id>")
@require_token
def update_project(project_id):
    data=request.get_json(silent=True) or {}; conn=db()
    project=conn.execute("SELECT * FROM projects WHERE id=?",(project_id,)).fetchone()
    if not project: conn.close(); return jsonify(error="project not found"),404
    name=(data.get("name",project["name"]) or "").strip()
    if not name: conn.close(); return jsonify(error="project name is required"),400
    try:
        conn.execute("UPDATE projects SET name=?,location=?,status=?,notes=? WHERE id=?",
                     (name,data.get("location",project["location"]),data.get("status",project["status"]) or "Active",
                      data.get("notes",project["notes"]),project_id))
        conn.commit(); row=conn.execute("SELECT * FROM projects WHERE id=?",(project_id,)).fetchone()
        return jsonify(project=dict(row))
    except sqlite3.IntegrityError:
        return jsonify(error="project already exists"),409
    finally: conn.close()

@app.get("/api/leads/<int:lead_id>")
@require_token
def get_lead(lead_id):
    conn=db(); lead=lead_visible(conn,lead_id)
    if not lead: conn.close(); return jsonify(error="lead not found"),404
    notes=conn.execute("SELECT * FROM lead_notes WHERE lead_id=? ORDER BY id DESC",(lead_id,)).fetchall()
    conn.close(); payload=dict(lead); payload["notes_history"]=[dict(x) for x in notes]
    return jsonify(lead=payload)

@app.put("/api/leads/<int:lead_id>")
@require_token
def update_lead(lead_id):
    data=request.get_json(silent=True) or {}; conn=db(); lead=lead_visible(conn,lead_id)
    if not lead: conn.close(); return jsonify(error="lead not found or access denied"),404
    phone=(data.get("phone",lead["phone"]) or "").strip()
    duplicate=ensure_phone_available(conn,phone,exclude_lead_id=lead_id)
    if duplicate: conn.close(); return jsonify(error="phone number already exists",duplicate=duplicate),409
    assigned=lead["assigned_to"]
    if g.user["role"] in {"Admin","Manager"} and "assigned_to" in data: assigned=data.get("assigned_to") or None
    conn.execute("""UPDATE leads SET name=?,phone=?,project_id=?,notes=?,follow_up_date=?,status=?,
                    assigned_to=?,visit_date=?,visit_time=?,visit_status=?,visit_completed_date=? WHERE id=?""",
        (data.get("name",lead["name"]),phone,data.get("project_id",lead["project_id"]),data.get("notes",lead["notes"]),
         data.get("follow_up_date",lead["follow_up_date"]),data.get("status",lead["status"]) or "New",assigned,
         data.get("visit_date",lead["visit_date"]),data.get("visit_time",lead["visit_time"]),
         data.get("visit_status",lead["visit_status"]) or "Planned",data.get("visit_completed_date",lead["visit_completed_date"]),lead_id))
    if "notes" in data and (data.get("notes") or "").strip() and data.get("notes") != (lead["notes"] or ""):
        conn.execute("INSERT INTO lead_notes(lead_id,note,note_date,created_at) VALUES(?,?,?,?)",
                     (lead_id,data["notes"].strip(),datetime.now(timezone.utc).date().isoformat(),now_iso()))
    conn.execute("DELETE FROM followups WHERE lead_id=?",(lead_id,))
    if data.get("follow_up_date"):
        conn.execute("INSERT INTO followups(lead_id,name,phone,follow_up_date,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     (lead_id,data.get("name",lead["name"]),phone,data["follow_up_date"],data.get("notes",lead["notes"]) or "","New",now_iso()))
    conn.commit(); row=conn.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone(); conn.close()
    return jsonify(lead=dict(row))

@app.post("/api/leads/<int:lead_id>/notes")
@require_token
def add_lead_note(lead_id):
    data=request.get_json(silent=True) or {}; note=(data.get("note") or "").strip()
    if not note: return jsonify(error="note is required"),400
    conn=db()
    if not lead_visible(conn,lead_id): conn.close(); return jsonify(error="lead not found or access denied"),404
    cur=conn.execute("INSERT INTO lead_notes(lead_id,note,note_date,created_at) VALUES(?,?,?,?)",
                     (lead_id,note,data.get("note_date") or datetime.now(timezone.utc).date().isoformat(),now_iso()))
    conn.commit(); row=conn.execute("SELECT * FROM lead_notes WHERE id=?",(cur.lastrowid,)).fetchone(); conn.close()
    return jsonify(note=dict(row)),201

@app.get("/api/followups")
@require_token
def list_followups():
    conn=db()
    if g.user["role"]=="Sales":
        rows=conn.execute("""SELECT f.* FROM followups f LEFT JOIN leads l ON f.lead_id=l.id
                             WHERE f.lead_id IS NULL OR l.assigned_to=? ORDER BY f.follow_up_date ASC,f.id DESC""",
                          (g.user["username"],)).fetchall()
    else: rows=conn.execute("SELECT * FROM followups ORDER BY follow_up_date ASC,id DESC").fetchall()
    conn.close(); return jsonify(followups=[dict(r) for r in rows])

@app.post("/api/followups")
@require_token
def create_followup():
    data=request.get_json(silent=True) or {}; conn=db(); lead_id=data.get("lead_id")
    if lead_id is not None and not lead_visible(conn,int(lead_id)):
        conn.close(); return jsonify(error="lead not found or access denied"),404
    cur=conn.execute("INSERT INTO followups(lead_id,name,phone,follow_up_date,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     (lead_id,data.get("name"),data.get("phone"),data.get("follow_up_date"),data.get("note"),data.get("status") or "New",now_iso()))
    conn.commit(); row=conn.execute("SELECT * FROM followups WHERE id=?",(cur.lastrowid,)).fetchone(); conn.close()
    return jsonify(followup=dict(row)),201

@app.put("/api/followups/<int:followup_id>")
@require_token
def update_followup(followup_id):
    data=request.get_json(silent=True) or {}; conn=db()
    f=conn.execute("SELECT * FROM followups WHERE id=?",(followup_id,)).fetchone()
    if not f: conn.close(); return jsonify(error="follow-up not found"),404
    if f["lead_id"] and not lead_visible(conn,f["lead_id"]): conn.close(); return jsonify(error="access denied"),403
    status=data.get("status",f["status"]) or "New"
    if status=="Cancel":
        lead=conn.execute("""SELECT l.*,p.name AS project_name FROM leads l LEFT JOIN projects p ON p.id=l.project_id WHERE l.id=?""",(f["lead_id"],)).fetchone() if f["lead_id"] else None
        conn.execute("""INSERT INTO canceled_leads(lead_id,name,phone,project_id,project_name,assigned_to,follow_up_date,note,cancelled_date,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (f["lead_id"],data.get("name",f["name"]),data.get("phone",f["phone"]),lead["project_id"] if lead else None,
                      lead["project_name"] if lead else None,lead["assigned_to"] if lead else None,
                      data.get("follow_up_date",f["follow_up_date"]),data.get("note",f["note"]),
                      datetime.now(timezone.utc).date().isoformat(),now_iso()))
        if f["lead_id"]: conn.execute("UPDATE leads SET follow_up_date=NULL WHERE id=?",(f["lead_id"],))
        conn.execute("DELETE FROM followups WHERE id=?",(followup_id,))
    else:
        conn.execute("UPDATE followups SET name=?,phone=?,follow_up_date=?,note=?,status=? WHERE id=?",
                     (data.get("name",f["name"]),data.get("phone",f["phone"]),data.get("follow_up_date",f["follow_up_date"]),
                      data.get("note",f["note"]),status,followup_id))
    conn.commit(); conn.close(); return jsonify(ok=True)

@app.get("/api/canceled-leads")
@require_token
def list_canceled():
    conn=db()
    if g.user["role"]=="Sales": rows=conn.execute("SELECT * FROM canceled_leads WHERE assigned_to=? ORDER BY cancelled_date DESC,id DESC",(g.user["username"],)).fetchall()
    else: rows=conn.execute("SELECT * FROM canceled_leads ORDER BY cancelled_date DESC,id DESC").fetchall()
    conn.close(); return jsonify(canceled_leads=[dict(r) for r in rows])

@app.get("/api/customers/<int:customer_id>")
@require_token
def get_customer(customer_id):
    conn=db(); customer=conn.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not customer: conn.close(); return jsonify(error="customer not found"),404
    payments=conn.execute("SELECT * FROM payments WHERE customer_id=? ORDER BY id DESC",(customer_id,)).fetchall()
    conn.close(); payload=dict(customer); payload["due"]=(customer["sales"] or 0)-(customer["paid"] or 0); payload["payments"]=[dict(x) for x in payments]
    return jsonify(customer=payload)

@app.post("/api/customers")
@require_token
def create_customer():
    data=request.get_json(silent=True) or {}; name=(data.get("name") or "").strip(); phone=(data.get("phone") or "").strip()
    if not name: return jsonify(error="name is required"),400
    conn=db(); duplicate=ensure_phone_available(conn,phone)
    if duplicate: conn.close(); return jsonify(error="phone number already exists",duplicate=duplicate),409
    cur=conn.execute("""INSERT INTO customers(name,phone,address,business,notes,follow_up,sales,paid,project_id,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (name,phone,data.get("address"),data.get("business"),data.get("notes"),data.get("follow_up"),
                      float(data.get("sales") or 0),float(data.get("paid") or 0),data.get("project_id"),now_iso()))
    conn.commit(); row=conn.execute("SELECT * FROM customers WHERE id=?",(cur.lastrowid,)).fetchone(); conn.close()
    return jsonify(customer=dict(row)),201

@app.put("/api/customers/<int:customer_id>")
@require_token
def update_customer(customer_id):
    data=request.get_json(silent=True) or {}; conn=db(); customer=conn.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not customer: conn.close(); return jsonify(error="customer not found"),404
    phone=(data.get("phone",customer["phone"]) or "").strip(); duplicate=ensure_phone_available(conn,phone,exclude_customer_id=customer_id)
    if duplicate: conn.close(); return jsonify(error="phone number already exists",duplicate=duplicate),409
    conn.execute("""UPDATE customers SET name=?,phone=?,address=?,business=?,notes=?,follow_up=?,sales=?,paid=?,project_id=? WHERE id=?""",
                 (data.get("name",customer["name"]),phone,data.get("address",customer["address"]),data.get("business",customer["business"]),
                  data.get("notes",customer["notes"]),data.get("follow_up",customer["follow_up"]),float(data.get("sales",customer["sales"]) or 0),
                  float(data.get("paid",customer["paid"]) or 0),data.get("project_id",customer["project_id"]),customer_id))
    conn.commit(); row=conn.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone(); conn.close(); return jsonify(customer=dict(row))

@app.post("/api/customers/<int:customer_id>/payments")
@require_token
def add_payment_api(customer_id):
    data=request.get_json(silent=True) or {}; amount=float(data.get("amount") or 0)
    if amount<=0: return jsonify(error="payment amount must be greater than zero"),400
    conn=db(); customer=conn.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not customer: conn.close(); return jsonify(error="customer not found"),404
    cur=conn.execute("INSERT INTO payments(customer_id,amount,payment_date,note,created_at) VALUES(?,?,?,?,?)",(customer_id,amount,data.get("payment_date"),data.get("note"),now_iso()))
    conn.execute("UPDATE customers SET paid=COALESCE(paid,0)+? WHERE id=?",(amount,customer_id)); conn.commit()
    row=conn.execute("SELECT * FROM payments WHERE id=?",(cur.lastrowid,)).fetchone(); conn.close(); return jsonify(payment=dict(row)),201

@app.put("/api/payments/<int:payment_id>")
@require_token
def update_payment_api(payment_id):
    data=request.get_json(silent=True) or {}; conn=db(); payment=conn.execute("SELECT * FROM payments WHERE id=?",(payment_id,)).fetchone()
    if not payment: conn.close(); return jsonify(error="payment not found"),404
    new_amount=float(data.get("amount",payment["amount"]) or 0)
    if new_amount<=0: conn.close(); return jsonify(error="payment amount must be greater than zero"),400
    conn.execute("UPDATE payments SET amount=?,payment_date=?,note=? WHERE id=?",(new_amount,data.get("payment_date",payment["payment_date"]),data.get("note",payment["note"]),payment_id))
    conn.execute("UPDATE customers SET paid=COALESCE(paid,0)+? WHERE id=?",(new_amount-payment["amount"],payment["customer_id"]))
    conn.commit(); conn.close(); return jsonify(ok=True)

@app.delete("/api/payments/<int:payment_id>")
@require_token
def delete_payment_api(payment_id):
    conn=db(); payment=conn.execute("SELECT * FROM payments WHERE id=?",(payment_id,)).fetchone()
    if not payment: conn.close(); return jsonify(error="payment not found"),404
    conn.execute("DELETE FROM payments WHERE id=?",(payment_id,)); conn.execute("UPDATE customers SET paid=COALESCE(paid,0)-? WHERE id=?",(payment["amount"],payment["customer_id"]))
    conn.commit(); conn.close(); return jsonify(ok=True)

@app.get("/api/expenses")
@require_token
def list_expenses():
    conn=db(); rows=conn.execute("SELECT * FROM expenses ORDER BY expense_date DESC,id DESC").fetchall(); total=conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]; conn.close()
    return jsonify(expenses=[dict(r) for r in rows],total_expenses=total)

@app.post("/api/expenses")
@require_token
def create_expense():
    data=request.get_json(silent=True) or {}; amount=float(data.get("amount") or 0)
    if amount<=0: return jsonify(error="expense amount must be greater than zero"),400
    conn=db(); cur=conn.execute("INSERT INTO expenses(amount,expense_date,category,note,created_at) VALUES(?,?,?,?,?)",(amount,data.get("expense_date"),data.get("category"),data.get("note"),now_iso()))
    conn.commit(); row=conn.execute("SELECT * FROM expenses WHERE id=?",(cur.lastrowid,)).fetchone(); conn.close(); return jsonify(expense=dict(row)),201

@app.put("/api/expenses/<int:expense_id>")
@require_token
def update_expense(expense_id):
    data=request.get_json(silent=True) or {}; conn=db(); row=conn.execute("SELECT * FROM expenses WHERE id=?",(expense_id,)).fetchone()
    if not row: conn.close(); return jsonify(error="expense not found"),404
    conn.execute("UPDATE expenses SET amount=?,expense_date=?,category=?,note=? WHERE id=?",(float(data.get("amount",row["amount"]) or 0),data.get("expense_date",row["expense_date"]),data.get("category",row["category"]),data.get("note",row["note"]),expense_id))
    conn.commit(); conn.close(); return jsonify(ok=True)

@app.delete("/api/expenses/<int:expense_id>")
@require_token
def delete_expense(expense_id):
    conn=db(); row=conn.execute("SELECT id FROM expenses WHERE id=?",(expense_id,)).fetchone()
    if not row: conn.close(); return jsonify(error="expense not found"),404
    conn.execute("DELETE FROM expenses WHERE id=?",(expense_id,)); conn.commit(); conn.close(); return jsonify(ok=True)

@app.get("/api/receipt-settings")
@require_token
def get_receipt_settings():
    conn=db(); row=conn.execute("SELECT * FROM receipt_settings WHERE id=1").fetchone(); conn.close(); return jsonify(settings=dict(row))

@app.put("/api/receipt-settings")
@require_token
def update_receipt_settings():
    data=request.get_json(silent=True) or {}; header=(data.get("header_name") or "").strip()
    if not header: return jsonify(error="header_name is required"),400
    conn=db(); conn.execute("UPDATE receipt_settings SET header_name=? WHERE id=1",(header,)); conn.commit(); row=conn.execute("SELECT * FROM receipt_settings WHERE id=1").fetchone(); conn.close()
    return jsonify(settings=dict(row))
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
        id INTEGER PRIMARY KEY, header_name TEXT NOT NULL
    );
    INSERT OR IGNORE INTO receipt_settings(id, header_name) VALUES (1, 'ARKAM MC PARK');
    CREATE INDEX IF NOT EXISTS idx_cloud_leads_project ON leads(project_id);
    CREATE INDEX IF NOT EXISTS idx_cloud_customers_project ON customers(project_id);
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
    if g.user["role"] == "Sales":
        rows = conn.execute("SELECT * FROM leads WHERE assigned_to=? ORDER BY id DESC",
                            (g.user["username"],)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
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
    rows = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify(customers=[dict(r) for r in rows])

if __name__ == "__main__":
    init_cloud_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8000")))

    init_cloud_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8000")))
