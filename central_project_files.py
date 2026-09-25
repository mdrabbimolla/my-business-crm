import base64
import binascii
import sqlite3
from flask import Blueprint, jsonify, request, g


ALLOWED_EXTENSIONS = {"txt", "pdf", "jpg", "jpeg", "png"}
MAX_FILE_BYTES = 12 * 1024 * 1024


def _extension(filename):
    return filename.rsplit(".", 1)[1].lower() if filename and "." in filename else ""


def register_project_file_routes(app, db, require_token):
    api = Blueprint("central_project_files", __name__)

    def project_row(conn, project_id):
        return conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    @api.get("/api/projects/<int:project_id>/files")
    @require_token
    def list_project_files(project_id):
        conn = db()
        project = project_row(conn, project_id)
        if not project:
            conn.close()
            return jsonify(error="project not found"), 404
        rows = conn.execute(
            "SELECT id,project_id,title,item_type,file_name,mime_type,text_content,created_at "
            "FROM project_files WHERE project_id=? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        conn.close()
        return jsonify(project=dict(project), files=[dict(row) for row in rows])

    @api.post("/api/projects/<int:project_id>/files")
    @require_token
    def create_project_file(project_id):
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        item_type = (data.get("item_type") or "file").strip().lower()
        conn = db()
        project = project_row(conn, project_id)
        if not project:
            conn.close()
            return jsonify(error="project not found"), 404
        if item_type == "text":
            text_content = (data.get("text_content") or "").strip()
            if not title or not text_content:
                conn.close()
                return jsonify(error="title and text are required"), 400
            cur = conn.execute(
                "INSERT INTO project_files(project_id,title,item_type,text_content,mime_type,created_at) "
                "VALUES(?,?,?,?,?,datetime('now'))",
                (project_id, title, "text", text_content, "text/plain"),
            )
        else:
            file_name = (data.get("file_name") or "").strip()
            ext = _extension(file_name)
            raw = data.get("data_base64") or ""
            if not file_name or ext not in ALLOWED_EXTENSIONS or not raw:
                conn.close()
                return jsonify(error="valid file_name and data_base64 are required"), 400
            try:
                payload = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                conn.close()
                return jsonify(error="invalid base64 file data"), 400
            if not payload:
                conn.close()
                return jsonify(error="file is empty"), 400
            if len(payload) > MAX_FILE_BYTES:
                conn.close()
                return jsonify(error="file is too large; maximum is 12 MB"), 413
            mime_type = (data.get("mime_type") or "application/octet-stream").strip()
            cur = conn.execute(
                "INSERT INTO project_files(project_id,title,item_type,file_name,mime_type,file_data,created_at) "
                "VALUES(?,?,?,?,?,?,datetime('now'))",
                (project_id, title or file_name, "file", file_name, mime_type, payload),
            )
        conn.commit()
        row = conn.execute(
            "SELECT id,project_id,title,item_type,file_name,mime_type,text_content,created_at "
            "FROM project_files WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        conn.close()
        return jsonify(file=dict(row)), 201

    @api.get("/api/project-files/<int:file_id>")
    @require_token
    def get_project_file(file_id):
        conn = db()
        row = conn.execute("SELECT * FROM project_files WHERE id=?", (file_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify(error="file not found"), 404
        payload = dict(row)
        if row["item_type"] == "file":
            payload["data_base64"] = base64.b64encode(row["file_data"] or b"").decode("ascii")
        payload.pop("file_data", None)
        return jsonify(file=payload)

    @api.delete("/api/project-files/<int:file_id>")
    @require_token
    def delete_project_file(file_id):
        conn = db()
        row = conn.execute("SELECT project_id FROM project_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify(error="file not found"), 404
        conn.execute("DELETE FROM project_files WHERE id=?", (file_id,))
        conn.commit()
        conn.close()
        return jsonify(deleted=True, project_id=row["project_id"])

    app.register_blueprint(api)
