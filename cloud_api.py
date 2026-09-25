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
    return conn

def init_cloud_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'Sales',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()

def require_api_secret(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not API_SECRET:
            return jsonify(error="CRM_API_SECRET is not configured"), 503
        supplied = request.headers.get("X-CRM-API-SECRET", "")
        if not secrets.compare_digest(supplied, API_SECRET):
            return jsonify(error="Unauthorized"), 401
        return fn(*args, **kwargs)
    return wrapper

def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify(error="Authentication required"), 401
        token = header[7:].strip()
        conn = db()
        user = conn.execute("""
            SELECT users.*
            FROM auth_tokens
            JOIN users ON users.id = auth_tokens.user_id
            WHERE auth_tokens.token = ? AND users.active = 1
        """, (token,)).fetchone()
        conn.close()
        if not user:
            return jsonify(error="Invalid or expired session"), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

@app.get("/api/health")
def health():
    return jsonify(ok=True, service="my-business-crm-central-api")

@app.post("/api/bootstrap-user")
@require_api_secret
def bootstrap_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "Sales").strip()
    if not username or not password:
        return jsonify(error="username and password are required"), 400
    if role not in {"Admin", "Manager", "Sales"}:
        return jsonify(error="invalid role"), 400
    conn = db()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify(error="username already exists"), 409
    cur = conn.execute("""
        INSERT INTO users(username, password_hash, name, role, active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
    """, (username, generate_password_hash(password), name, role, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return jsonify(ok=True, user_id=user_id), 201

@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        conn.close()
        return jsonify(error="Wrong username or password"), 401
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_tokens(token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user["id"], datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify(ok=True, token=token, user={
        "id": user["id"], "username": user["username"], "name": user["name"], "role": user["role"]
    })

@app.get("/api/me")
@require_token
def me():
    return jsonify(ok=True, user={
        "id": g.user["id"], "username": g.user["username"], "name": g.user["name"], "role": g.user["role"]
    })

if __name__ == "__main__":
    init_cloud_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
