from datetime import date
from urllib.parse import quote
import json
import urllib.request
import os
import io
import csv
import uuid
import tempfile
import zipfile
import shutil
from flask import Flask, request, redirect, session, render_template, send_file
from werkzeug.utils import secure_filename
from database import get_db, init_db

try:
    from jnius import autoclass, cast, PythonJavaClass, java_method
except ImportError:
    autoclass = None
    cast = None
    PythonJavaClass = None
    java_method = None

try:
    from android.permissions import request_permissions, check_permission, Permission
except ImportError:
    request_permissions = None
    check_permission = None
    Permission = None

try:
    from android.runnable import run_on_ui_thread
except ImportError:
    run_on_ui_thread = None

try:
    from android import activity as android_activity
except ImportError:
    android_activity = None

REMOTE_REPO = "mdrabbimolla/my-business-crm"
REMOTE_BRANCH = "main"
RUNTIME_TEMPLATE_DIR = os.path.join(os.path.expanduser("~"), ".mycrm_runtime", "templates")
RUNTIME_VERSION_FILE = os.path.join(os.path.dirname(RUNTIME_TEMPLATE_DIR), "version.txt")


def _remote_update_info():
    url = f"https://api.github.com/repos/{REMOTE_REPO}/branches/{REMOTE_BRANCH}"
    request = urllib.request.Request(url, headers={"User-Agent": "My-Business-CRM-Updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["commit"]["sha"]


def _sync_remote_templates(force=False):
    try:
        remote_sha = _remote_update_info()
        local_sha = ""
        if os.path.exists(RUNTIME_VERSION_FILE):
            with open(RUNTIME_VERSION_FILE, "r", encoding="utf-8") as f:
                local_sha = f.read().strip()
        if not force and local_sha == remote_sha and os.path.isdir(RUNTIME_TEMPLATE_DIR):
            return {"ok": True, "updated": False, "sha": remote_sha, "message": "Already up to date"}

        zip_url = f"https://codeload.github.com/{REMOTE_REPO}/zip/{remote_sha}"
        request = urllib.request.Request(zip_url, headers={"User-Agent": "My-Business-CRM-Updater"})
        with urllib.request.urlopen(request, timeout=20) as response:
            archive = response.read()

        runtime_root = os.path.dirname(RUNTIME_TEMPLATE_DIR)
        os.makedirs(runtime_root, exist_ok=True)
        staging_root = tempfile.mkdtemp(prefix="mycrm_update_", dir=runtime_root)
        try:
            archive_path = os.path.join(staging_root, "repo.zip")
            with open(archive_path, "wb") as f:
                f.write(archive)
            extract_root = os.path.join(staging_root, "extract")
            os.makedirs(extract_root, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_root)
            top_dirs = [os.path.join(extract_root, name) for name in os.listdir(extract_root)]
            repo_root = next((p for p in top_dirs if os.path.isdir(p)), None)
            source_templates = os.path.join(repo_root, "templates") if repo_root else None
            if not source_templates or not os.path.isdir(source_templates):
                raise RuntimeError("Remote templates folder was not found")

            new_templates = os.path.join(staging_root, "templates")
            shutil.copytree(source_templates, new_templates)
            old_templates = RUNTIME_TEMPLATE_DIR + ".old"
            if os.path.exists(old_templates):
                shutil.rmtree(old_templates, ignore_errors=True)
            if os.path.exists(RUNTIME_TEMPLATE_DIR):
                os.replace(RUNTIME_TEMPLATE_DIR, old_templates)
            os.replace(new_templates, RUNTIME_TEMPLATE_DIR)
            shutil.rmtree(old_templates, ignore_errors=True)
            with open(RUNTIME_VERSION_FILE, "w", encoding="utf-8") as f:
                f.write(remote_sha)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        app.template_folder = RUNTIME_TEMPLATE_DIR
        app.jinja_loader = app.jinja_env.loader = app.create_global_jinja_loader()
        return {"ok": True, "updated": True, "sha": remote_sha, "message": "CRM UI updated successfully"}
    except Exception as exc:
        print("REMOTE UPDATE ERROR:", repr(exc))
        return {"ok": False, "updated": False, "message": str(exc) or "Update could not be completed"}


app = Flask(__name__)

app.secret_key = "mycrm-secret-key"

# Use bundled templates at startup. Live UI updates are checked manually from the CRM menu.
# Network update work is intentionally excluded from app startup so Android WebView opens immediately.


init_db()

try:
    from app_version import APP_VERSION
except ImportError:
    APP_VERSION = "1.0.0"


def _start_android_url(url, action="VIEW", package_name=None):
    if autoclass is None or cast is None:
        return False

    try:
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        current_activity = cast("android.app.Activity", PythonActivity.mActivity)

        if action == "CALL":
            intent = Intent(Intent.ACTION_CALL)
        elif action == "DIAL":
            intent = Intent(Intent.ACTION_DIAL)
        else:
            intent = Intent(Intent.ACTION_VIEW)

        intent.setData(Uri.parse(url))

        if package_name:
            intent.setPackage(package_name)

        current_activity.startActivity(intent)
        return True
    except Exception as exc:
        print("ANDROID INTENT ERROR:", exc)
        return False


def open_android_url(url, action="VIEW", package_name=None):
    if autoclass is None or cast is None:
        return False

    if run_on_ui_thread is not None:
        try:
            run_on_ui_thread(_start_android_url)(url, action, package_name)
            return True
        except Exception as exc:
            print("ANDROID UI THREAD ERROR:", exc)
            return False

    return _start_android_url(url, action, package_name)


def android_call_permission_granted():
    if check_permission is None or Permission is None:
        return False
    try:
        return bool(check_permission(Permission.CALL_PHONE))
    except Exception:
        return False


def request_android_call_permission():
    if request_permissions is None or Permission is None:
        return False
    try:
        if not android_call_permission_granted():
            request_permissions([Permission.CALL_PHONE])
            return False
        return True
    except Exception as exc:
        print("ANDROID PERMISSION ERROR:", exc)
        return False

def clean_phone(phone):
    phone = (phone or "").strip()
    phone = "".join(ch for ch in phone if ch.isdigit() or ch == "+")

    # Android tel: URI needs the international +880 format.
    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("880"):
        return "+" + phone

    if phone.startswith("0"):
        return "+88" + phone

    return "+" + phone if phone else ""



PROJECT_FILE_ROOT = os.path.join(os.getcwd(), "project_files")
ALLOWED_PROJECT_FILE_EXTENSIONS = {"txt", "pdf", "jpg", "jpeg", "png"}

def project_file_extension(filename):
    return filename.rsplit(".", 1)[1].lower() if filename and "." in filename else ""

def project_file_mime(filename):
    return {
        "txt": "text/plain",
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(project_file_extension(filename), "application/octet-stream")

def ensure_project_file_dir(project_id):
    path = os.path.join(PROJECT_FILE_ROOT, str(project_id))
    os.makedirs(path, exist_ok=True)
    return path

def share_android_text(text_value):
    if autoclass is None or cast is None:
        return False
    try:
        Intent = autoclass("android.content.Intent")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = cast("android.app.Activity", PythonActivity.mActivity)
        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, text_value or "")
        intent.setPackage("com.whatsapp.w4b")
        activity.startActivity(Intent.createChooser(intent, "Send Project Text"))
        return True
    except Exception as exc:
        print("TEXT SHARE ERROR:", exc)
        return False

def share_android_file(file_path, mime_type):
    if autoclass is None or cast is None or not os.path.exists(file_path):
        return False
    try:
        Intent = autoclass("android.content.Intent")
        ContentValues = autoclass("android.content.ContentValues")
        MediaStoreFiles = autoclass("android.provider.MediaStore$Files")
        Environment = autoclass("android.os.Environment")
        ClipData = autoclass("android.content.ClipData")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = cast("android.app.Activity", PythonActivity.mActivity)
        resolver = activity.getContentResolver()
        values = ContentValues()
        values.put("_display_name", os.path.basename(file_path))
        values.put("mime_type", mime_type)
        values.put("relative_path", Environment.DIRECTORY_DOWNLOADS + "/My Business CRM/Reports")
        uri = resolver.insert(MediaStoreFiles.getContentUri("external"), values)
        if uri is None:
            return False
        stream = resolver.openOutputStream(uri)
        if stream is None:
            return False
        with open(file_path, "rb") as source:
            data = source.read()
            stream.write(data)
        stream.close()
        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime_type)
        intent.putExtra(Intent.EXTRA_STREAM, uri)
        intent.setClipData(ClipData.newRawUri("report", uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        activity.startActivity(Intent.createChooser(intent, "Send Daily Report"))
        return True
    except Exception as exc:
        print("FILE SHARE ERROR:", repr(exc))
        return False


def _pdf_draw_text(canvas, paint, text, x, y, max_chars=70, line_gap=14):
    text = str(text or "").replace("\\n", " ").strip()
    if not text:
        canvas.drawText("", x, y, paint)
        return y + line_gap
    while len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        canvas.drawText(text[:cut], x, y, paint)
        y += line_gap
        text = text[cut:].strip()
    canvas.drawText(text, x, y, paint)
    return y + line_gap


def _create_daily_report_pdf(token, report):
    try:
        PdfDocument = autoclass("android.graphics.pdf.PdfDocument")
        Paint = autoclass("android.graphics.Paint")
        Typeface = autoclass("android.graphics.Typeface")
        pdf = PdfDocument()
        page_width, page_height = 595, 842
        margin = 28
        page_no = 1
        y = margin

        title_paint = Paint()
        title_paint.setTextSize(18)
        title_paint.setTypeface(Typeface.DEFAULT_BOLD)
        body_paint = Paint()
        body_paint.setTextSize(9)
        header_paint = Paint()
        header_paint.setTextSize(9)
        header_paint.setTypeface(Typeface.DEFAULT_BOLD)

        def start_page():
            nonlocal page_no, y
            info = PdfDocument.PageInfo.Builder(page_width, page_height, page_no).create()
            page = pdf.startPage(info)
            y = margin
            page_no += 1
            return page

        page = start_page()
        canvas = page.getCanvas()
        canvas.drawText("My Business CRM - Daily Sales Report", margin, y, title_paint)
        y += 24
        canvas.drawText("Report Date: " + report["date"], margin, y, body_paint)
        y += 18
        if report.get("project"):
            canvas.drawText("Project: " + report["project"], margin, y, body_paint)
            y += 18
        canvas.drawText(
            "Talked: {}   New: {}   Follow-up: {}   Visit Set: {}   Completed: {}   Cancelled: {}".format(
                report["talked"], report["new"], report["followup"], report["visits"], report["completed"], report["cancelled"]
            ),
            margin, y, body_paint
        )
        y += 24

        columns = [("#", 25), ("Name", 105), ("Phone", 85), ("Project", 90), ("Type", 70), ("Visit", 75), ("Done", 45), ("Note", 72)]
        x_positions = []
        x = margin
        for _, width in columns:
            x_positions.append(x)
            x += width
        right = page_width - margin

        def draw_header():
            nonlocal y
            canvas.drawLine(margin, y - 10, right, y - 10, header_paint)
            for idx, (label, _) in enumerate(columns):
                canvas.drawText(label, x_positions[idx], y, header_paint)
            y += 15
            canvas.drawLine(margin, y - 6, right, y - 6, header_paint)

        draw_header()
        for row in report["rows"]:
            if y > page_height - 45:
                pdf.finishPage(page)
                page = start_page()
                canvas = page.getCanvas()
                canvas.drawText("My Business CRM - Daily Sales Report (continued)", margin, y, title_paint)
                y += 24
                draw_header()
            values = [row["no"], row["name"], row["phone"], row["project"], row["type"], row["visit"], row["done"], row["note"]]
            row_y = y
            max_lines = 1
            for idx, value in enumerate(values):
                text_value = str(value or "-").replace("\\n", " ")
                width = columns[idx][1]
                max_chars = max(4, int(width / 5.3))
                chunks = []
                while len(text_value) > max_chars:
                    cut = text_value.rfind(" ", 0, max_chars)
                    if cut <= 0:
                        cut = max_chars
                    chunks.append(text_value[:cut])
                    text_value = text_value[cut:].strip()
                chunks.append(text_value)
                for line_idx, chunk in enumerate(chunks[:3]):
                    canvas.drawText(chunk, x_positions[idx], row_y + line_idx * 11, body_paint)
                max_lines = max(max_lines, min(3, len(chunks)))
            y += max_lines * 11 + 8
            canvas.drawLine(margin, y - 4, right, y - 4, body_paint)

        pdf.finishPage(page)
        out_dir = os.path.join("/tmp", "mycrm_reports")
        os.makedirs(out_dir, exist_ok=True)
        filename = "Daily_Report_" + report["date"] + ".pdf"
        path = os.path.join(out_dir, filename)
        output = open(path, "wb")
        pdf.writeTo(output)
        output.close()
        pdf.close()

        ok = share_android_file(path, "application/pdf")
        if ok:
            _android_pdf_status[token] = {"done": True, "ok": True, "message": "PDF created and share opened"}
        else:
            _android_pdf_status[token] = {"done": True, "ok": False, "error": "PDF was created but Android share could not be opened"}
    except Exception as exc:
        print("PDF REPORT ERROR:", repr(exc))
        _android_pdf_status[token] = {"done": True, "ok": False, "error": str(exc) or "PDF could not be created"}
    finally:
        _android_pdf_reports.pop(token, None)
@app.route("/daily-report-pdf")
def daily_report_pdf():
    if not session.get("logged_in"):
        return {"ok": False, "error": "Not logged in"}, 401

    report_date = request.args.get("date") or date.today().isoformat()
    project_filter = request.args.get("project_id", "").strip()
    conn = get_db()
    current_user = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (session.get("username"),)).fetchone()
    if not current_user:
        conn.close()
        return {"ok": False, "error": "User not found"}, 404

    params = [report_date, report_date]
    query = """
        SELECT leads.name, leads.phone, projects.name AS project_name,
               leads.created_at, leads.follow_up_date,
               leads.visit_date, leads.visit_time, leads.visit_status,
               leads.visit_completed_date, ln.note
        FROM lead_notes ln
        JOIN leads ON leads.id = ln.lead_id
        LEFT JOIN projects ON projects.id = leads.project_id
        WHERE ln.note_date = ?
          AND ln.id = (SELECT MAX(ln2.id) FROM lead_notes ln2 WHERE ln2.lead_id = ln.lead_id AND ln2.note_date = ?)
    """
    if project_filter:
        query += " AND leads.project_id = ?"
        params.append(project_filter)
    if current_user["role"] == "Sales":
        query += " AND leads.assigned_to = ?"
        params.append(current_user["username"])
    query += " ORDER BY projects.name ASC, leads.id DESC"
    rows = conn.execute(query, params).fetchall()

    visit_params = [report_date]
    visit_query = "SELECT COUNT(*) FROM leads WHERE visit_date = ? AND visit_date IS NOT NULL"
    if project_filter:
        visit_query += " AND project_id = ?"; visit_params.append(project_filter)
    if current_user["role"] == "Sales":
        visit_query += " AND assigned_to = ?"; visit_params.append(current_user["username"])
    visits = conn.execute(visit_query, visit_params).fetchone()[0]

    completed_params = [report_date]
    completed_query = "SELECT COUNT(*) FROM leads WHERE visit_status = 'Completed' AND visit_completed_date = ?"
    if project_filter:
        completed_query += " AND project_id = ?"; completed_params.append(project_filter)
    if current_user["role"] == "Sales":
        completed_query += " AND assigned_to = ?"; completed_params.append(current_user["username"])
    completed = conn.execute(completed_query, completed_params).fetchone()[0]

    cancelled_params = [report_date]
    cancelled_query = "SELECT COUNT(*) FROM canceled_leads WHERE cancelled_date = ?"
    if project_filter:
        cancelled_query += " AND project_id = ?"; cancelled_params.append(project_filter)
    if current_user["role"] == "Sales":
        cancelled_query += " AND assigned_to = ?"; cancelled_params.append(current_user["username"])
    cancelled = conn.execute(cancelled_query, cancelled_params).fetchone()[0]

    project_name = ""
    if project_filter:
        project_row = conn.execute("SELECT name FROM projects WHERE id = ?", (project_filter,)).fetchone()
        project_name = project_row["name"] if project_row else ""
    conn.close()

    pdf_rows = []
    new_count = 0
    followup_count = 0
    for idx, lead in enumerate(rows, 1):
        created_date = (lead["created_at"] or "")[:10]
        if created_date == report_date:
            lead_type = "New Lead"; new_count += 1
        elif lead["follow_up_date"] == report_date:
            lead_type = "Follow-up"; followup_count += 1
        else:
            lead_type = "Conversation"
        visit = "—"
        if lead["visit_date"]:
            visit = str(lead["visit_date"]) + ((" " + str(lead["visit_time"])) if lead["visit_time"] else "")
        pdf_rows.append({
            "no": idx,
            "name": lead["name"] or "Name not added",
            "phone": lead["phone"] or "",
            "project": lead["project_name"] or "Unassigned",
            "type": lead_type,
            "visit": visit,
            "done": "Yes" if lead["visit_status"] == "Completed" and lead["visit_completed_date"] == report_date else "—",
            "note": lead["note"] or ""
        })

    token = uuid.uuid4().hex
    _android_pdf_reports[token] = {
        "date": report_date, "project": project_name, "talked": len(pdf_rows),
        "new": new_count, "followup": followup_count, "visits": visits,
        "completed": completed, "cancelled": cancelled, "rows": pdf_rows
    }
    _android_pdf_status[token] = {"done": False, "ok": True, "message": "Preparing PDF"}
    try:
        if run_on_ui_thread is not None:
            run_on_ui_thread(_create_daily_report_pdf)(token, _android_pdf_reports[token])
        else:
            _create_daily_report_pdf(token, _android_pdf_reports[token])
        return {"ok": True, "token": token}
    except Exception as exc:
        _android_pdf_status[token] = {"done": True, "ok": False, "error": str(exc) or "PDF could not be started"}
        _android_pdf_reports.pop(token, None)
        return {"ok": False, "error": "PDF could not be started"}, 500


@app.route("/daily-report-pdf-status/<token>")
def daily_report_pdf_status(token):
    if not session.get("logged_in"):
        return {"done": True, "ok": False, "error": "Not logged in"}, 401
    return _android_pdf_status.get(token, {"done": True, "ok": False, "error": "PDF session not found"})





if __name__ == "__main__":
    # Start the Flask server explicitly for the Android WebView bootstrap.
    # Keep startup local and synchronous; no network/update work runs here.
    app.run(host="0.0.0.0", port=5000, debug=False)
