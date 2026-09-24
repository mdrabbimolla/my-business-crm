from datetime import date
from urllib.parse import quote
import json
import urllib.request
import os
import io
import uuid
import tempfile
import zipfile
import shutil
import ssl
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

app = Flask(__name__)

app.secret_key = "mycrm-secret-key"


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

def _share_android_text_now(text_value):
    Intent = autoclass("android.content.Intent")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    activity = cast("android.app.Activity", PythonActivity.mActivity)
    intent = Intent(Intent.ACTION_SEND)
    intent.setType("text/plain")
    intent.putExtra(Intent.EXTRA_TEXT, text_value or "")
    chooser = Intent.createChooser(intent, "Send Daily Report")
    activity.startActivity(chooser)
    return True

def share_android_text(text_value):
    if autoclass is None or cast is None:
        return False
    try:
        if run_on_ui_thread is not None:
            run_on_ui_thread(_share_android_text_now)(text_value)
            return True
        return _share_android_text_now(text_value)
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
        values.put("relative_path", Environment.DIRECTORY_DOWNLOADS + "/My Business CRM/Project Files")
        uri = resolver.insert(MediaStoreFiles.getContentUri("external"), values)
        if uri is None:
            return False
        stream = resolver.openOutputStream(uri)
        with open(file_path, "rb") as source:
            stream.write(source.read())
        stream.close()
        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime_type)
        intent.putExtra(Intent.EXTRA_STREAM, uri)
        intent.setClipData(ClipData.newRawUri("file", uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.setPackage("com.whatsapp.w4b")
        activity.startActivity(Intent.createChooser(intent, "Send Project File"))
        return True
    except Exception as exc:
        print("FILE SHARE ERROR:", exc)
        return False

PROJECT_FILE_PICKER_REQUEST = 18741
_project_file_picker_status = {}
_project_file_picker_listeners = {}

def _find_webview(view):
    try:
        if "WebView" in str(view.getClass().getName()):
            return cast("android.webkit.WebView", view)
    except Exception:
        pass
    try:
        child_count = view.getChildCount()
    except Exception:
        return None
    for index in range(child_count):
        try:
            found = _find_webview(view.getChildAt(index))
            if found is not None:
                return found
        except Exception:
            continue
    return None

def _reload_webview(url):
    if autoclass is None or cast is None:
        return False
    try:
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = cast("android.app.Activity", PythonActivity.mActivity)
        root = activity.getWindow().getDecorView()
        webview = _find_webview(root)
        if webview is None:
            return False
        webview.loadUrl(url)
        return True
    except Exception as exc:
        print("WEBVIEW RELOAD ERROR:", exc)
        return False

# Android print is asynchronous. Keep the adapter and PrintJob alive until
# Android reports that the job has completed, failed, or was cancelled.
_android_print_status = {}
_android_print_jobs = {}
_android_print_adapters = {}


def _cleanup_android_print(token):
    _android_print_jobs.pop(token, None)
    _android_print_adapters.pop(token, None)


def _android_print_current_page(token):
    if autoclass is None or cast is None:
        _android_print_status[token] = {"done": True, "ok": False, "error": "PyJNIus is unavailable"}
        return
    try:
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = cast("android.app.Activity", PythonActivity.mActivity)
        root = activity.getWindow().getDecorView()
        webview = _find_webview(root)
        if webview is None:
            _android_print_status[token] = {"done": True, "ok": False, "error": "WebView not found"}
            return

        PrintAttributesBuilder = autoclass("android.print.PrintAttributes$Builder")
        print_manager = activity.getSystemService("print")
        if print_manager is None:
            _android_print_status[token] = {"done": True, "ok": False, "error": "Android PrintManager is unavailable"}
            return

        adapter = webview.createPrintDocumentAdapter("My Business CRM - Daily Sales Report")
        if adapter is None:
            _android_print_status[token] = {"done": True, "ok": False, "error": "PrintDocumentAdapter could not be created"}
            return

        attributes = PrintAttributesBuilder().build()
        # PrintManager.print() is asynchronous. Strong references are required.
        print_job = print_manager.print(
            "My Business CRM - Daily Sales Report", adapter, attributes
        )
        _android_print_adapters[token] = adapter
        _android_print_jobs[token] = print_job
        _android_print_status[token] = {
            "done": True,
            "ok": True,
            "submitted": True,
            "message": "Android Print Service accepted the print job"
        }
        print("ANDROID PRINT: print job submitted successfully")
    except Exception as exc:
        print("ANDROID PRINT ERROR:", repr(exc))
        _android_print_status[token] = {"done": True, "ok": False, "error": str(exc) or "Print could not be started"}


def _android_print_current_page_ui(token):
    if run_on_ui_thread is not None:
        run_on_ui_thread(_android_print_current_page)(token)
    else:
        _android_print_current_page(token)


def _android_print_job_status(token):
    status = _android_print_status.get(token)
    if not status:
        return {"done": True, "ok": False, "error": "Print session not found"}
    if status.get("done"):
        return status

    job = _android_print_jobs.get(token)
    if job is None:
        return {"done": True, "ok": False, "error": "Android PrintJob was lost"}

    try:
        if job.isCompleted():
            result = {"done": True, "ok": True, "message": "Print job completed"}
            _android_print_status[token] = result
            _cleanup_android_print(token)
            return result
        if job.isFailed():
            result = {"done": True, "ok": False, "error": "Android Print Service reported a print failure"}
            _android_print_status[token] = result
            _cleanup_android_print(token)
            return result
        if job.isCancelled():
            result = {"done": True, "ok": False, "cancelled": True, "error": "Print job was cancelled"}
            _android_print_status[token] = result
            _cleanup_android_print(token)
            return result
        return {"done": False, "ok": True, "message": "Print job is still processing"}
    except Exception as exc:
        print("ANDROID PRINT STATUS ERROR:", repr(exc))
        return {"done": False, "ok": True, "message": "Waiting for Android Print Service"}

def _complete_project_file_picker(token, project_id, title, result_code, intent):
    try:
        if result_code != -1 or intent is None:
            _project_file_picker_status[token] = {"done": True, "ok": False, "project_id": project_id}
            return

        uri = intent.getData()
        if uri is None:
            _project_file_picker_status[token] = {"done": True, "ok": False, "project_id": project_id}
            return

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = cast("android.app.Activity", PythonActivity.mActivity)
        resolver = activity.getContentResolver()

        display_name = None
        try:
            OpenableColumns = autoclass("android.provider.OpenableColumns")
            cursor = resolver.query(uri, [OpenableColumns.DISPLAY_NAME], None, None, None)
            if cursor is not None:
                if cursor.moveToFirst():
                    display_name = cursor.getString(cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME))
                cursor.close()
        except Exception:
            pass

        file_name = display_name or "project_file"
        safe_name = secure_filename(str(file_name)) or "project_file"
        ext = project_file_extension(safe_name)
        if ext not in ALLOWED_PROJECT_FILE_EXTENSIONS:
            _project_file_picker_status[token] = {"done": True, "ok": False, "project_id": project_id, "error": "Only TXT, PDF, JPG, JPEG and PNG files are allowed"}
            return

        stream = resolver.openInputStream(uri)
        data = stream.readAllBytes()
        stream.close()

        folder = ensure_project_file_dir(project_id)
        stored_name = uuid.uuid4().hex + "_" + safe_name
        stored_path = os.path.join(folder, stored_name)
        with open(stored_path, "wb") as output:
            output.write(bytes(data))

        mime_type = resolver.getType(uri) or project_file_mime(safe_name)
        final_title = (title or "").strip() or safe_name

        conn = get_db()
        conn.execute(
            "INSERT INTO project_files (project_id,title,item_type,file_name,file_path,mime_type) VALUES (?,?, 'file',?,?,?)",
            (project_id, final_title, safe_name, stored_path, mime_type)
        )
        conn.commit()
        conn.close()

        _project_file_picker_status[token] = {"done": True, "ok": True, "project_id": project_id}
    except Exception as exc:
        print("PROJECT FILE PICKER ERROR:", exc)
        _project_file_picker_status[token] = {"done": True, "ok": False, "project_id": project_id, "error": "File could not be saved"}
    finally:
        listener = _project_file_picker_listeners.pop(token, None)
        if listener is not None:
            try:
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                PythonActivity.mActivity.unregisterActivityResultListener(listener)
            except Exception:
                pass

def start_android_project_file_picker(project_id, title):
    if autoclass is None or android_activity is None:
        return None
    token = uuid.uuid4().hex
    _project_file_picker_status[token] = {"done": False, "project_id": project_id}
    try:
        Intent = autoclass("android.content.Intent")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")

        if PythonJavaClass is None or java_method is None:
            return None

        def on_activity_result(request_code, result_code, data):
            if request_code == PROJECT_FILE_PICKER_REQUEST:
                _complete_project_file_picker(token, project_id, title, result_code, data)

        class PickerListener(PythonJavaClass):
            __javainterfaces__ = ["org/kivy/android/PythonActivity$ActivityResultListener"]
            __javacontext__ = "app"

            @java_method("(IILandroid/content/Intent;)V")
            def onActivityResult(self, request_code, result_code, data):
                on_activity_result(request_code, result_code, data)

        listener = PickerListener()
        _project_file_picker_listeners[token] = listener
        activity.registerActivityResultListener(listener)
        activity.startActivityForResult(intent, PROJECT_FILE_PICKER_REQUEST)
        return token
    except Exception as exc:
        print("PROJECT FILE PICKER START ERROR:", exc)
        _project_file_picker_listeners.pop(token, None)
        _project_file_picker_status[token] = {"done": True, "ok": False, "project_id": project_id, "error": "File picker could not be opened"}
        return token

def get_update_info():
    """Check the latest public GitHub Release for a newer APK."""
    try:
        api_url = "https://api.github.com/repos/mdrabbimolla/my-business-crm/releases/latest"
        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "My-Business-CRM"})
        with urllib.request.urlopen(req, timeout=5, context=_https_context()) as response:
            release = json.loads(response.read().decode("utf-8"))
        tag = (release.get("tag_name") or "").lstrip("v")
        latest_version = tuple(int(p) for p in tag.split(".") if p.isdigit())
        current_version = tuple(int(p) for p in APP_VERSION.split(".") if p.isdigit())
        if not latest_version or latest_version <= current_version:
            return None
        apk_url = next((a.get("browser_download_url") for a in release.get("assets", []) if (a.get("name") or "").lower().endswith(".apk")), None)
        return {"current_version": APP_VERSION, "latest_version": tag, "release_url": release.get("html_url"), "apk_url": apk_url}
    except Exception as exc:
        print("UPDATE CHECK ERROR:", exc)
    return None


@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE username = ?
            AND password = ?
            AND active = 1
            """,
            (username, password)
        ).fetchone()

        conn.close()

        if user:
            session["logged_in"] = True
            session["username"] = user["username"]
            return redirect("/dashboard")

        return """
        <h3>Wrong username or password!</h3>
        <a href="/">Try Again</a>
        """

    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>My Business CRM</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:Arial,sans-serif;background:linear-gradient(145deg,#07142f,#123b73);display:flex;align-items:center;justify-content:center;padding:24px;color:#172554}.login-wrap{width:100%;max-width:430px}.brand{text-align:center;color:#fff;margin-bottom:18px}.brand-icon{width:72px;height:72px;margin:0 auto 12px;border-radius:22px;background:linear-gradient(145deg,#f4d56b,#d4af37);display:flex;align-items:center;justify-content:center;font-size:38px;box-shadow:0 12px 30px rgba(0,0,0,.25)}.brand h1{margin:0;font-size:27px}.brand p{margin:7px 0 0;color:#dbeafe;font-size:13px}.login-card{background:rgba(255,255,255,.98);border-radius:24px;padding:26px;box-shadow:0 20px 60px rgba(0,0,0,.28)}.eyebrow{text-transform:uppercase;letter-spacing:1.5px;font-size:11px;color:#64748b;font-weight:800}.login-card h2{margin:6px 0 20px;font-size:23px}.field{margin-bottom:14px}.field label{display:block;font-size:13px;font-weight:700;margin-bottom:7px}.field input{width:100%;padding:14px;border:1px solid #cbd5e1;border-radius:12px;font-size:15px;outline:none}.field input:focus{border-color:#173b70;box-shadow:0 0 0 3px #dbeafe}.login-btn{width:100%;border:0;border-radius:12px;padding:14px;background:linear-gradient(135deg,#173b70,#2563a8);color:#fff;font-size:16px;font-weight:800;box-shadow:0 8px 18px rgba(23,59,112,.25)}.footer{text-align:center;color:#bfdbfe;font-size:11px;margin-top:16px}</style>
</head><body><div class="login-wrap"><div class="brand"><div class="brand-icon">🏢</div><h1>My Business CRM</h1><p>Real Estate Sales & Customer Management</p></div><div class="login-card"><div class="eyebrow">Secure Access</div><h2>Welcome back 👋</h2><form method="POST"><div class="field"><label>Username</label><input type="text" name="username" placeholder="Enter username" autocomplete="username" required></div><div class="field"><label>Password</label><input type="password" name="password" placeholder="Enter password" autocomplete="current-password" required></div><button class="login-btn" type="submit">🔐 Sign in to CRM</button></form></div><div class="footer">Professional • Fast • Organized</div></div></body></html>
    """

@app.route("/android-call")
def android_call():
    if not session.get("logged_in"):
        return redirect("/")

    phone = clean_phone(request.args.get("phone"))

    if not phone:
        return "Phone number is missing"

    if not request_android_call_permission():
        return """
        <h3>📞 Phone permission needed</h3>
        <p>Please tap <strong>Allow</strong> when Android asks for call permission, then tap Call again.</p>
        <a href="/leads">← Back to Leads</a>
        """

    opened = open_android_url("tel:" + phone, action="CALL")

    if not opened:
        return """
        <h3>📞 Call could not be opened</h3>
        <p>Please make sure a phone/dialer app is installed.</p>
        <a href="/leads">← Back to Leads</a>
        """

    return """
    <h3>📞 Calling...</h3>
    <a href="/leads">← Back to Leads</a>
    """


@app.route("/android-whatsapp")
def android_whatsapp():
    if not session.get("logged_in"):
        return redirect("/")

    phone = clean_phone(request.args.get("phone"))

    if not phone:
        return "Phone number is missing"

    # WhatsApp expects the country code without the leading +.
    whatsapp_phone = phone.lstrip("+")
    whatsapp_url = "whatsapp://send?phone=" + whatsapp_phone

    # Only WhatsApp Business is allowed for this CRM.
    opened = open_android_url(
        whatsapp_url,
        action="VIEW",
        package_name="com.whatsapp.w4b"
    )

    if not opened:
        return """
        <h3>💬 WhatsApp could not be opened</h3>
        <p>Please make sure WhatsApp Business is installed.</p>
        <a href="/customers">← Back</a>
        """

    return """
    <h3>💬 Opening WhatsApp...</h3>
    <a href="/customers">← Back to Customers</a>
    """


@app.route("/change-password", methods=["GET", "POST"])
def change_password():

    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":

        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        conn = get_db()

        current_username = session.get("username")

        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (current_username,)
        ).fetchone()

        if not user:
            conn.close()
            return "User not found"

        if current_password != user["password"]:
            conn.close()
            return "Current password is incorrect"

        if not new_password:
            conn.close()
            return "New password cannot be empty"

        if new_password != confirm_password:
            conn.close()
            return "New passwords do not match"

        conn.execute(
            """
            UPDATE users
            SET password = ?
            WHERE username = ?
            """,
            (new_password, current_username)
        )

        conn.commit()
        conn.close()

        return """
        <h2>✅ Password Changed Successfully!</h2>
        <a href="/dashboard">⬅ Back to Dashboard</a>
        """

    return render_template("change_password.html")

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")

@app.route("/add-user", methods=["GET", "POST"])
def add_user():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    current_user = conn.execute(
        "SELECT role FROM users WHERE username = ?",
        (session.get("username"),)
    ).fetchone()

    if not current_user or current_user["role"] != "Admin":
        conn.close()
        return "Access Denied"

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "Sales")
        active = request.form.get("active", "1")

        if not username or not password:
            conn.close()
            return "Username and password are required"

        existing_user = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing_user:
            conn.close()
            return "Username already exists"

        conn.execute("""
            INSERT INTO users
            (name, username, password, role, active)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name,
            username,
            password,
            role,
            int(active)
        ))

        conn.commit()
        conn.close()

        return redirect("/users")

    conn.close()

    return render_template("add_user.html")


@app.route("/reset-password/<int:user_id>", methods=["GET", "POST"])
def reset_password(user_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    admin = conn.execute("SELECT role FROM users WHERE username = ? AND active = 1", (session.get("username"),)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not admin or admin["role"] != "Admin":
        conn.close()
        return "Access Denied"
    if not user:
        conn.close()
        return "User not found"
    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        if not new_password or new_password != confirm_password:
            conn.close()
            return "Password is empty or passwords do not match"
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))
        conn.commit()
        conn.close()
        return redirect("/users")
    conn.close()
    return render_template("reset_password.html", user=user)

@app.route("/users")
def users():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    current_user = conn.execute(
        "SELECT role FROM users WHERE username = ? AND active = 1",
        (session.get("username"),)
    ).fetchone()

    if not current_user or current_user["role"] != "Admin":
        conn.close()
        return "Access Denied"

    conn = get_db()

    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "users.html",
        users=users
    )

@app.route("/update")
def update_app():
    if not session.get("logged_in"):
        return redirect("/")
    update_info = get_update_info()
    if not update_info:
        return "<h2>✅ You are using the latest version.</h2><a href='/dashboard'>← Back to Dashboard</a>"
    update_url = update_info.get("apk_url") or update_info.get("release_url")
    if update_url and open_android_url(update_url, action="VIEW"):
        return f"<h2>⬇️ Update download is opening...</h2><p>Current: {update_info['current_version']}</p><p>New: {update_info['latest_version']}</p><p>Download the APK and tap Install. Uninstall is not required.</p><a href='/dashboard'>← Back to Dashboard</a>"
    return f"<h2>🆕 Update Available</h2><p>Current: {update_info['current_version']}</p><p>New: {update_info['latest_version']}</p><a href='{update_url or '#'}' target='_blank'>Download Update</a><br><br><a href='/dashboard'>← Back to Dashboard</a>"


@app.route("/dashboard")
def dashboard():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    total_customers = conn.execute(
        "SELECT COUNT(*) FROM customers"
    ).fetchone()[0]

    total_sales = conn.execute(
        "SELECT COALESCE(SUM(sales), 0) FROM customers"
    ).fetchone()[0]

    today = date.today().isoformat()

    today_followups = conn.execute(
        "SELECT * FROM followups WHERE follow_up_date = ? ORDER BY id DESC",
        (today,)
    ).fetchall()

    today_followup_count = len(today_followups)

    missed_followup_count = conn.execute("""
        SELECT COUNT(*)
        FROM followups
        WHERE follow_up_date < ?
    """, (today,)).fetchone()[0]

    upcoming_followup_count = conn.execute("""
        SELECT COUNT(*)
        FROM followups
        WHERE follow_up_date > ?
    """, (today,)).fetchone()[0]

    visit_count = conn.execute("""
        SELECT COUNT(*)
        FROM leads
        WHERE visit_date IS NOT NULL
        AND TRIM(visit_date) != ''
    """).fetchone()[0]

    cancel_count = conn.execute(
        "SELECT COUNT(*) FROM canceled_leads"
    ).fetchone()[0]

    conn.close()

    update_info = get_update_info()

    return render_template(
        "dashboard.html",
        total_customers=total_customers,
        total_sales=total_sales,
        today_followup_count=today_followup_count,
        missed_followup_count=missed_followup_count,
        upcoming_followup_count=upcoming_followup_count,
        visit_count=visit_count,
        cancel_count=cancel_count,
        today_followups=today_followups,
        app_version=APP_VERSION,
        update_info=update_info
    )


@app.route("/backup")
def backup():

    if not session.get("logged_in"):
        return redirect("/")

    import shutil
    from datetime import datetime

    backup_name = "database_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db"

    shutil.copy2("database.db", backup_name)

    return f"""
    <h2>✅ Database Backup Successful!</h2>
    <p>Backup file: {backup_name}</p>
    <a href="/dashboard">⬅ Back to Dashboard</a>
    """
@app.route("/restore")
def restore():

    if not session.get("logged_in"):
        return redirect("/")

    import os

    backup_files = [
        file for file in os.listdir(".")
        if file.startswith("database_backup_")
        and file.endswith(".db")
    ]

    backup_files.sort(reverse=True)

    return render_template(
        "restore.html",
        backup_files=backup_files
    )
@app.route("/restore", methods=["POST"])
def restore_database():

    if not session.get("logged_in"):
        return redirect("/")

    import os
    import shutil
    from datetime import datetime

    backup_file = request.form.get("backup_file")

    if not backup_file:
        return "Backup file not selected"

    if not backup_file.startswith("database_backup_") or not backup_file.endswith(".db"):
        return "Invalid backup file"

    if not os.path.exists(backup_file):
        return "Backup file not found"

    safety_backup = (
        "database_backup_before_restore_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".db"
    )

    shutil.copy2("database.db", safety_backup)
    shutil.copy2(backup_file, "database.db")

    return """
    <h2>✅ Database Restore Successful!</h2>
    <p>Selected backup has been restored.</p>
    <p>A safety backup of the previous database was also created.</p>
    <br>
    <a href="/dashboard">⬅ Back to Dashboard</a>
    """
@app.route("/reports")
def reports():

    if not session.get("logged_in"):
        return redirect("/")

    payment_date = request.args.get("payment_date")
    expense_date = request.args.get("expense_date")

    conn = get_db()

    total_sales = conn.execute(
        "SELECT COALESCE(SUM(sales), 0) FROM customers"
    ).fetchone()[0]

    total_paid = conn.execute(
        "SELECT COALESCE(SUM(paid), 0) FROM customers"
    ).fetchone()[0]

    total_due = total_sales - total_paid

    total_expenses = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses"
    ).fetchone()[0]

    net_profit = total_sales - total_expenses

    category_expenses = conn.execute("""
        SELECT
            category,
            COALESCE(SUM(amount), 0) AS total
        FROM expenses
        GROUP BY category
        ORDER BY total DESC
    """).fetchall()

    total_payments = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments"
    ).fetchone()[0]
    customers = conn.execute("""
        SELECT
            id,
            name,
            phone,
            sales,
            paid,
            (sales - paid) AS due
        FROM customers
        ORDER BY id DESC
    """).fetchall()
    payment_history = conn.execute("""
        SELECT
            payments.id,
            payments.amount,
            payments.payment_date,
            payments.note,
            customers.name,
            customers.phone
        FROM payments
        JOIN customers
        ON payments.customer_id = customers.id
        ORDER BY payments.id DESC
    """).fetchall()
    if payment_date:
        date_payments = conn.execute("""
            SELECT
                payments.id,
                payments.amount,
                payments.payment_date,
                payments.note,
                customers.name,
                customers.phone
            FROM payments
            JOIN customers
            ON payments.customer_id = customers.id
            WHERE payments.payment_date = ?
            ORDER BY payments.id DESC
        """, (payment_date,)).fetchall()
    else:
        date_payments = []

    if expense_date:
        date_expenses = conn.execute("""
            SELECT *
            FROM expenses
            WHERE expense_date = ?
            ORDER BY id DESC
        """, (expense_date,)).fetchall()
    else:
        date_expenses = []

    conn.close()

    return render_template(
        "reports.html",
        total_sales=total_sales,
        total_paid=total_paid,
        total_due=total_due,
        total_payments=total_payments,
        total_expenses=total_expenses,
        net_profit=net_profit,
        category_expenses=category_expenses,
        customers=customers,
        payment_history=payment_history,
        date_payments=date_payments,
        payment_date=payment_date,
        date_expenses=date_expenses,
        expense_date=expense_date
    )

@app.route("/export-customers")
def export_customers():

    if not session.get("logged_in"):
        return redirect("/")

    import csv
    import io
    from flask import Response

    conn = get_db()

    customers = conn.execute("""
        SELECT
            name,
            phone,
            address,
            business,
            sales,
            paid,
            (sales - paid) AS due,
            notes
        FROM customers
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Name",
        "Phone",
        "Address",
        "Business",
        "Sales",
        "Paid",
        "Due",
        "Notes"
    ])

    for customer in customers:
        writer.writerow([
            customer["name"],
            customer["phone"],
            customer["address"],
            customer["business"],
            customer["sales"],
            customer["paid"],
            customer["due"],
            customer["notes"]
        ])

    response = Response(
        output.getvalue(),
        mimetype="text/csv"
    )

    response.headers["Content-Disposition"] = \
        "attachment; filename=customers.csv"

    return response

@app.route("/edit-project/<int:project_id>", methods=["GET", "POST"])
def edit_project(project_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    if not project:
        conn.close()
        return "Project not found"

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        status = request.form.get("status", "Active")
        notes = request.form.get("notes", "").strip()

        if not name:
            conn.close()
            return "Project name is required"

        try:
            conn.execute("""
                UPDATE projects
                SET name = ?,
                    location = ?,
                    status = ?,
                    notes = ?
                WHERE id = ?
            """, (
                name,
                location,
                status,
                notes,
                project_id
            ))

            conn.commit()

        except Exception:
            conn.close()
            return "Project name already exists"

        conn.close()

        return redirect("/projects")

    conn.close()

    return render_template(
        "edit_project.html",
        project=project
    )


@app.route("/delete-project/<int:project_id>", methods=["GET", "POST"])
def delete_project(project_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    lead_count = conn.execute("SELECT COUNT(*) FROM leads WHERE project_id = ?", (project_id,)).fetchone()[0]
    customer_count = conn.execute("SELECT COUNT(*) FROM customers WHERE project_id = ?", (project_id,)).fetchone()[0]
    if lead_count or customer_count:
        conn.execute("UPDATE projects SET status = 'Inactive' WHERE id = ?", (project_id,))
    else:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return redirect("/projects")


@app.route("/android-print")
def android_print():
    if not session.get("logged_in"):
        return {"ok": False, "error": "Not logged in"}, 401
    token = uuid.uuid4().hex
    _android_print_status[token] = {"done": False, "ok": True, "message": "Starting Android Print Service"}
    try:
        _android_print_current_page_ui(token)
        return {"ok": True, "done": False, "token": token}
    except Exception as exc:
        print("PRINT UI THREAD ERROR:", repr(exc))
        _android_print_status[token] = {"done": True, "ok": False, "error": str(exc) or "Print could not be started"}
        return {"ok": False, "error": "Print could not be started"}, 500


@app.route("/android-print-status/<token>")
def android_print_status(token):
    if not session.get("logged_in"):
        return {"done": True, "ok": False, "error": "Not logged in"}, 401
    return _android_print_job_status(token)

@app.route("/android-pick-project-file/<int:project_id>")
def android_pick_project_file(project_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    project = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not project:
        return "Project not found"
    title = request.args.get("title", "").strip()
    token = start_android_project_file_picker(project_id, title)
    if not token:
        return "Android file picker is not available"
    return render_template("project_file_picker.html", token=token)

@app.route("/android-project-file-status/<token>")
def android_project_file_status(token):
    if not session.get("logged_in"):
        return {"done": True, "ok": False, "error": "Not logged in"}
    status = _project_file_picker_status.get(token)
    if not status:
        return {"done": True, "ok": False, "error": "Picker session not found"}
    return status

@app.route("/project-files/<int:project_id>", methods=["GET", "POST"])
def project_files(project_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return "Project not found"
    if request.method == "POST":
        item_type = request.form.get("item_type", "file")
        title = request.form.get("title", "").strip()
        if item_type == "text":
            text_content = request.form.get("text_content", "").strip()
            if not title or not text_content:
                conn.close()
                return "Title and text are required"
            conn.execute("INSERT INTO project_files (project_id,title,item_type,text_content,mime_type) VALUES (?,?, 'text',?, 'text/plain')", (project_id,title,text_content))
            conn.commit()
            conn.close()
            return redirect(f"/project-files/{project_id}")
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            conn.close()
            return "Please select a file"
        safe_name = secure_filename(uploaded.filename)
        ext = project_file_extension(safe_name)
        if ext not in ALLOWED_PROJECT_FILE_EXTENSIONS:
            conn.close()
            return "Only TXT, PDF, JPG, JPEG and PNG files are allowed"
        folder = ensure_project_file_dir(project_id)
        stored_name = uuid.uuid4().hex + "_" + safe_name
        stored_path = os.path.join(folder, stored_name)
        uploaded.save(stored_path)
        conn.execute("INSERT INTO project_files (project_id,title,item_type,file_name,file_path,mime_type) VALUES (?,?, 'file',?,?,?)", (project_id,title or safe_name,safe_name,stored_path,project_file_mime(safe_name)))
        conn.commit()
        conn.close()
        return redirect(f"/project-files/{project_id}")
    files = conn.execute("SELECT * FROM project_files WHERE project_id = ? ORDER BY id DESC", (project_id,)).fetchall()
    conn.close()
    return render_template("project_files.html", project=project, files=files)

@app.route("/project-file-download/<int:file_id>")
def project_file_download(file_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    item = conn.execute("SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    if not item or item["item_type"] != "file" or not item["file_path"] or not os.path.exists(item["file_path"]):
        return "File not found"
    return send_file(item["file_path"], as_attachment=True, download_name=item["file_name"], mimetype=item["mime_type"])

@app.route("/delete-project-file/<int:file_id>", methods=["GET", "POST"])
def delete_project_file(file_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    item = conn.execute("SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
    if not item:
        conn.close()
        return redirect("/projects")
    project_id = item["project_id"]
    if item["file_path"] and os.path.exists(item["file_path"]):
        try:
            os.remove(item["file_path"])
        except OSError:
            pass
    conn.execute("DELETE FROM project_files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
    return redirect(f"/project-files/{project_id}")

@app.route("/share-project-file/<int:file_id>")
def share_project_file(file_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    item = conn.execute("SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    if not item:
        return "File not found"
    ok = share_android_text(item["text_content"]) if item["item_type"] == "text" else share_android_file(item["file_path"], item["mime_type"])
    return ("<h3>💬 WhatsApp খুলছে...</h3>" if ok else "<h3>⚠️ WhatsApp Share চালু করা যায়নি</h3>") + f"<a href='/project-files/{item['project_id']}'>← Back</a>"

@app.route("/projects")
def projects():
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    projects = conn.execute("SELECT * FROM projects ORDER BY CASE WHEN status = 'Active' THEN 0 ELSE 1 END, name").fetchall()
    conn.close()
    return render_template("projects.html", projects=projects)

@app.route("/toggle-project/<int:project_id>", methods=["GET", "POST"])
def toggle_project(project_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    project = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return "Project not found"
    new_status = "Inactive" if project["status"] == "Active" else "Active"
    conn.execute("UPDATE projects SET status = ? WHERE id = ?", (new_status, project_id))
    conn.commit()
    conn.close()
    return redirect("/projects")

@app.route("/add-project", methods=["GET", "POST"])
def add_project():

    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        status = request.form.get("status", "Active")
        notes = request.form.get("notes", "").strip()

        if not name:
            return "Project name is required"

        conn = get_db()

        try:
            conn.execute("""
                INSERT INTO projects
                (name, location, status, notes)
                VALUES (?, ?, ?, ?)
            """, (
                name,
                location,
                status,
                notes
            ))

            conn.commit()

        except Exception:
            conn.close()
            return "Project name already exists"

        conn.close()

        return redirect("/projects")

    return render_template("add_project.html")

@app.route("/customers")
def customers():

    if not session.get("logged_in"):
        return redirect("/")

    search = request.args.get("search", "").strip()
    customer_filter = request.args.get("filter", "").strip()
    project_filter = request.args.get("project_id", "").strip()

    conn = get_db()

    query = """
        SELECT customers.*,
           (customers.sales - customers.paid) AS due,
           projects.name AS project_name
        FROM customers
        LEFT JOIN projects
        ON customers.project_id = projects.id
        WHERE 1=1
    """
    params = []

    if search:
        query += """
            AND (
                name LIKE ?
                OR phone LIKE ?
                OR business LIKE ?
                OR address LIKE ?
            )
        """

        search_value = f"%{search}%"

        params.extend([
            search_value,
            search_value,
            search_value,
            search_value
        ])

    if customer_filter == "due":
        query += " AND (sales - paid) > 0"

    elif customer_filter == "paid":
        query += " AND (sales - paid) <= 0"

    if project_filter:
        query += " AND customers.project_id = ?"
        params.append(project_filter)

    query += " ORDER BY id DESC"

    customers = conn.execute(
        query,
        params
    ).fetchall()

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "customers.html",
        customers=customers,
        search=search,
        customer_filter=customer_filter,
        projects=projects,
        project_filter=project_filter
    )

@app.route("/add-customer", methods=["GET", "POST"])
def add_customer():

    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":

        name = request.form.get("name")
        phone = request.form.get("phone")
        address = request.form.get("address")
        business = request.form.get("business")
        notes = request.form.get("notes")
        follow_up = request.form.get("follow_up")

        project_id = request.form.get("project_id") or None

        sales = float(request.form.get("sales") or 0)
        paid = float(request.form.get("paid") or 0)

        conn = get_db()

        # Check duplicate phone in customers
        existing_customer = conn.execute("""
            SELECT name
            FROM customers
            WHERE phone = ?
        """, (phone,)).fetchone()

        if existing_customer:
            existing_name = existing_customer["name"] or "Name not added"

            conn.close()

            return f"""
            <h2>⚠️ Duplicate Phone Number</h2>
            <p>এই ফোন নম্বরটি আগে থেকেই Customer হিসেবে আছে।</p>
            <p><strong>👤 Name:</strong> {existing_name}</p>
            <a href="/add-customer">← Back to Add Customer</a>
            """

        # Check duplicate phone in leads
        existing_lead = conn.execute("""
            SELECT
                leads.name,
                projects.name AS project_name
            FROM leads
            LEFT JOIN projects
                ON leads.project_id = projects.id
            WHERE leads.phone = ?
        """, (phone,)).fetchone()

        if existing_lead:
            existing_name = existing_lead["name"] or "Name not added"
            project_name = existing_lead["project_name"] or "Unassigned"

            conn.close()

            return f"""
            <h2>⚠️ Duplicate Phone Number</h2>
            <p>এই ফোন নম্বরটি আগে থেকেই Lead হিসেবে CRM-এ আছে।</p>
            <p><strong>👤 Name:</strong> {existing_name}</p>
            <p><strong>🏗️ Project:</strong> {project_name}</p>
            <a href="/add-customer">← Back to Add Customer</a>
            """

        conn.execute("""
            INSERT INTO customers
            (name, phone, address, business, notes, follow_up, sales, paid, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            phone,
            address,
            business,
            notes,
            follow_up,
            sales,
            paid,
            project_id
        ))

        conn.commit()
        conn.close()

        return redirect("/customers")

    conn = get_db()

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "add_customer.html",
        projects=projects
    )

@app.route("/edit-customer/<int:customer_id>", methods=["GET", "POST"])
def edit_customer(customer_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id = ?",
        (customer_id,)
    ).fetchone()

    if not customer:
        conn.close()
        return "Customer not found"

    if request.method == "POST":

        name = request.form.get("name")
        phone = request.form.get("phone")
        address = request.form.get("address")
        business = request.form.get("business")
        notes = request.form.get("notes")
        follow_up = request.form.get("follow_up")

        project_id = request.form.get("project_id") or None

        sales = float(request.form.get("sales") or 0)
        paid = float(request.form.get("paid") or 0)

        conn.execute("""
                UPDATE customers
                SET name = ?,
                phone = ?,
                address = ?,
                business = ?,
                notes = ?,
                follow_up = ?,
                sales = ?,
                paid = ?,
                project_id = ?
            WHERE id = ?
        """, (
            name,
            phone,
            address,
            business,
            notes,
            follow_up,
            sales,
            paid,
            project_id,
            customer_id
        ))

        conn.commit()
        conn.close()

        return redirect("/customers")

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "edit_customer.html",
        customer=customer,
        projects=projects
    )
@app.route("/customer/<int:customer_id>")
def customer_profile(customer_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    customer = conn.execute("""
        SELECT customers.*,
           projects.name AS project_name
        FROM customers
        LEFT JOIN projects
        ON customers.project_id = projects.id
        WHERE customers.id = ?
    """, (customer_id,)).fetchone()
    if not customer:
        conn.close()
        return "Customer not found"

    payments = conn.execute("""
        SELECT *
        FROM payments
        WHERE customer_id = ?
        ORDER BY id ASC
    """, (customer_id,)).fetchall()

    sales = customer["sales"] or 0
    running_due = sales

    payment_list = []

    for payment in payments:

        running_due -= payment["amount"]

        payment_data = dict(payment)
        payment_data["due_after_payment"] = running_due

        payment_list.append(payment_data)

    payment_list.reverse()

    due = sales - (customer["paid"] or 0)

    conn.close()

    return render_template(
        "customer_profile.html",
        customer=customer,
        due=due,
        payments=payment_list
    )
@app.route("/add-payment/<int:customer_id>", methods=["GET", "POST"])
def add_payment(customer_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    customer = conn.execute(
        "SELECT * FROM customers WHERE id = ?",
        (customer_id,)
    ).fetchone()

    if not customer:
        conn.close()
        return "Customer not found"

    if request.method == "POST":

        amount = float(request.form.get("amount") or 0)
        payment_date = request.form.get("payment_date")
        note = request.form.get("note")

        conn.execute("""
            INSERT INTO payments
            (customer_id, amount, payment_date, note)
            VALUES (?, ?, ?, ?)
        """, (
            customer_id,
            amount,
            payment_date,
            note
        ))

        conn.execute("""
            UPDATE customers
            SET paid = paid + ?
            WHERE id = ?
        """, (
            amount,
            customer_id
        ))

        conn.commit()
        conn.close()

        return redirect(f"/customer/{customer_id}")

    conn.close()

    return render_template(
        "add_payment.html",
        customer=customer
    )
@app.route("/edit-payment/<int:payment_id>", methods=["GET", "POST"])
def edit_payment(payment_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    payment = conn.execute(
        "SELECT * FROM payments WHERE id = ?",
        (payment_id,)
    ).fetchone()

    if not payment:
        conn.close()
        return "Payment not found"

    customer_id = payment["customer_id"]

    if request.method == "POST":

        new_amount = float(request.form.get("amount") or 0)
        payment_date = request.form.get("payment_date")
        note = request.form.get("note")

        old_amount = payment["amount"]
        difference = new_amount - old_amount

        conn.execute("""
            UPDATE payments
            SET amount = ?,
                payment_date = ?,
                note = ?
            WHERE id = ?
        """, (
            new_amount,
            payment_date,
            note,
            payment_id
        ))

        conn.execute("""
            UPDATE customers
            SET paid = paid + ?
            WHERE id = ?
        """, (
            difference,
            customer_id
        ))

        conn.commit()
        conn.close()

        return redirect(f"/customer/{customer_id}")

    conn.close()

    return render_template(
        "edit_payment.html",
        payment=payment
    )
@app.route("/delete-payment/<int:payment_id>", methods=["GET", "POST"])
def delete_payment(payment_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    payment = conn.execute(
        "SELECT * FROM payments WHERE id = ?",
        (payment_id,)
    ).fetchone()

    if not payment:
        conn.close()
        return "Payment not found"

    customer_id = payment["customer_id"]
    amount = payment["amount"]

    conn.execute(
        "DELETE FROM payments WHERE id = ?",
        (payment_id,)
    )

    conn.execute("""
        UPDATE customers
        SET paid = paid - ?
        WHERE id = ?
    """, (
        amount,
        customer_id
    ))

    conn.commit()
    conn.close()

    return redirect(f"/customer/{customer_id}")
@app.route("/delete-customer/<int:customer_id>", methods=["GET", "POST"])
def delete_customer(customer_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    conn.execute(
        "DELETE FROM customers WHERE id = ?",
        (customer_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/customers")
@app.route("/receipt-options/<int:payment_id>")
def receipt_options(payment_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    payment = conn.execute(
        "SELECT * FROM payments WHERE id = ?",
        (payment_id,)
    ).fetchone()

    if not payment:
        conn.close()
        return "Payment not found"

    customer = conn.execute(
        "SELECT * FROM customers WHERE id = ?",
        (payment["customer_id"],)
    ).fetchone()

    conn.close()

    if not customer:
        return "Customer not found"

    return render_template(
        "receipt_options.html",
        payment=payment,
        customer=customer
    )
@app.route("/payment-receipt/<int:payment_id>")
def payment_receipt(payment_id):

    if not session.get("logged_in"):
        return redirect("/")

    show_due = request.args.get("show_due") == "1"

    conn = get_db()

    payment = conn.execute(
        "SELECT * FROM payments WHERE id = ?",
        (payment_id,)
    ).fetchone()

    if not payment:
        conn.close()
        return "Payment not found"

    customer = conn.execute(
        "SELECT * FROM customers WHERE id = ?",
        (payment["customer_id"],)
    ).fetchone()

    if not customer:
        conn.close()
        return "Customer not found"

    due = customer["sales"] - customer["paid"]

    settings = conn.execute(
        "SELECT * FROM receipt_settings WHERE id = 1"
    ).fetchone()

    conn.close()

    return render_template(
        "payment_receipt.html",
        payment=payment,
        customer=customer,
        due=due,
        show_due=show_due,
        settings=settings
    )
@app.route("/receipt-settings", methods=["GET", "POST"])
def receipt_settings():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    if request.method == "POST":

        header_name = request.form.get("header_name")

        conn.execute("""
            UPDATE receipt_settings
            SET header_name = ?
            WHERE id = 1
        """, (header_name,))

        conn.commit()

    settings = conn.execute(
        "SELECT * FROM receipt_settings WHERE id = 1"
    ).fetchone()

    print("SETTINGS:", settings)

    conn.close()

    return render_template(
        "receipt_settings.html",
        settings=settings
    )
@app.route("/add-followup", methods=["GET", "POST"])
def add_followup():

    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":

        name = request.form.get("name")
        phone = request.form.get("phone")
        follow_up_date = request.form.get("follow_up_date")
        note = request.form.get("note")
        status = request.form.get("status")

        conn = get_db()

        conn.execute("""
            INSERT INTO followups
            (name, phone, follow_up_date, note, status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name,
            phone,
            follow_up_date,
            note,
            status
        ))

        conn.commit()
        conn.close()

        return redirect("/followups")

    return render_template("add_followup.html")
@app.route("/followups")
def followups():

    if not session.get("logged_in"):
        return redirect("/")

    status = request.args.get("status")
    date_filter = request.args.get("date_filter")

    conn = get_db()

    if status and date_filter:
        followups = conn.execute("""
            SELECT * FROM followups
            WHERE status = ?
            AND follow_up_date = ?
            ORDER BY follow_up_date ASC, id DESC
        """, (status, date_filter)).fetchall()

    elif status:
        followups = conn.execute("""
            SELECT * FROM followups
            WHERE status = ?
            ORDER BY follow_up_date ASC, id DESC
        """, (status,)).fetchall()

    elif date_filter == "today":
        followups = conn.execute("""
            SELECT * FROM followups
            WHERE follow_up_date = ?
            ORDER BY follow_up_date ASC, id DESC
        """, (date.today().isoformat(),)).fetchall()

    elif date_filter == "missed":
        followups = conn.execute("""
            SELECT * FROM followups
            WHERE follow_up_date < ?
            ORDER BY follow_up_date ASC, id DESC
        """, (date.today().isoformat(),)).fetchall()

    elif date_filter == "upcoming":
        followups = conn.execute("""
            SELECT * FROM followups
            WHERE follow_up_date > ?
            ORDER BY follow_up_date ASC, id DESC
        """, (date.today().isoformat(),)).fetchall()

    else:
        followups = conn.execute("""
            SELECT * FROM followups
            ORDER BY follow_up_date ASC, id DESC
        """).fetchall()
    conn.close()

    today = date.today().isoformat()

    today_followups = []
    upcoming_followups = []
    old_followups = []

    for followup in followups:

        followup_date = followup["follow_up_date"]

        if followup_date == today:
            today_followups.append(followup)

        elif followup_date and followup_date > today:
            upcoming_followups.append(followup)

        else:
            old_followups.append(followup)

    return render_template(
        "followups.html",
        followups=followups,
        today_followups=today_followups,
        upcoming_followups=upcoming_followups,
        old_followups=old_followups
    )
@app.route("/edit-followup/<int:followup_id>", methods=["GET", "POST"])
def edit_followup(followup_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    followup = conn.execute(
        "SELECT * FROM followups WHERE id = ?",
        (followup_id,)
    ).fetchone()

    if not followup:
        conn.close()
        return "Follow-up not found"

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        follow_up_date = request.form.get("follow_up_date") or None
        note = request.form.get("note", "").strip()
        status = request.form.get("status", "New")

        # Cancel means: remove it from active Follow-ups and store it separately.
        if status == "Cancel":
            project_id = None
            project_name = None
            assigned_to = None

            if followup["lead_id"]:
                lead = conn.execute("""
                    SELECT leads.*, projects.name AS project_name
                    FROM leads
                    LEFT JOIN projects ON leads.project_id = projects.id
                    WHERE leads.id = ?
                """, (followup["lead_id"],)).fetchone()

                if lead:
                    project_id = lead["project_id"]
                    project_name = lead["project_name"]
                    assigned_to = lead["assigned_to"]

            conn.execute("""
                INSERT INTO canceled_leads
                (lead_id, name, phone, project_id, project_name, assigned_to,
                 follow_up_date, note, cancelled_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                followup["lead_id"],
                name or followup["name"],
                phone or followup["phone"],
                project_id,
                project_name,
                assigned_to,
                follow_up_date or followup["follow_up_date"],
                note or followup["note"],
                date.today().isoformat()
            ))

            if followup["lead_id"]:
                conn.execute("""
                    UPDATE leads
                    SET follow_up_date = NULL
                    WHERE id = ?
                """, (followup["lead_id"],))

            conn.execute(
                "DELETE FROM followups WHERE id = ?",
                (followup_id,)
            )

            conn.commit()
            conn.close()

            return redirect("/canceled")

        conn.execute("""
            UPDATE followups
            SET name = ?,
                phone = ?,
                follow_up_date = ?,
                note = ?,
                status = ?
            WHERE id = ?
        """, (
            name,
            phone,
            follow_up_date,
            note,
            status,
            followup_id
        ))

        if followup["lead_id"]:
            conn.execute("""
                UPDATE leads
                SET name = ?,
                    phone = ?,
                    follow_up_date = ?,
                    notes = ?
                WHERE id = ?
            """, (
                name,
                phone,
                follow_up_date,
                note,
                followup["lead_id"]
            ))

        conn.commit()
        conn.close()

        return redirect("/followups")

    conn.close()

    return render_template(
        "edit_followup.html",
        followup=followup
    )


@app.route("/delete-followup/<int:followup_id>", methods=["GET", "POST"])
def delete_followup(followup_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    followup = conn.execute(
        "SELECT lead_id FROM followups WHERE id = ?",
        (followup_id,)
    ).fetchone()

    if not followup:
        conn.close()
        return redirect("/followups")

    # Clear the lead's follow-up date too. Otherwise database migration
    # would recreate the deleted follow-up on the next app start.
    if followup["lead_id"]:
        conn.execute("""
            UPDATE leads
            SET follow_up_date = NULL
            WHERE id = ?
        """, (followup["lead_id"],))

    conn.execute(
        "DELETE FROM followups WHERE id = ?",
        (followup_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/followups")


@app.route("/add-expense", methods=["GET", "POST"])
def add_expense():

    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":

        amount = request.form.get("amount")
        expense_date = request.form.get("expense_date")
        category = request.form.get("category")
        note = request.form.get("note")

        conn = get_db()

        conn.execute("""
            INSERT INTO expenses
            (amount, expense_date, category, note)
            VALUES (?, ?, ?, ?)
        """, (
            amount,
            expense_date,
            category,
            note
        ))

        conn.commit()
        conn.close()

        return redirect("/expenses")

    return render_template("add_expense.html")
@app.route("/expenses")
def expenses():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    expenses = conn.execute("""
        SELECT *
        FROM expenses
        ORDER BY expense_date DESC, id DESC
    """).fetchall()

    total_expenses = conn.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
    """).fetchone()[0]

    conn.close()

    return render_template(
        "expenses.html",
        expenses=expenses,
        total_expenses=total_expenses
    )
@app.route("/edit-expense/<int:expense_id>", methods=["GET", "POST"])
def edit_expense(expense_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    expense = conn.execute(
        "SELECT * FROM expenses WHERE id = ?",
        (expense_id,)
    ).fetchone()

    if not expense:
        conn.close()
        return "Expense not found"

    if request.method == "POST":

        amount = request.form.get("amount")
        expense_date = request.form.get("expense_date")
        category = request.form.get("category")
        note = request.form.get("note")

        conn.execute("""
            UPDATE expenses
            SET amount = ?,
                expense_date = ?,
                category = ?,
                note = ?
            WHERE id = ?
        """, (
            amount,
            expense_date,
            category,
            note,
            expense_id
        ))

        conn.commit()
        conn.close()

        return redirect("/expenses")

    conn.close()

    return render_template(
        "edit_expense.html",
        expense=expense
    )


@app.route("/delete-expense/<int:expense_id>", methods=["GET", "POST"])
def delete_expense(expense_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    conn.execute(
        "DELETE FROM expenses WHERE id = ?",
        (expense_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/expenses")
def save_lead_note(conn, lead_id, note):
    note = (note or "").strip()
    if not note:
        return
    conn.execute("""
        INSERT INTO lead_notes (lead_id, note, note_date)
        VALUES (?, ?, ?)
    """, (lead_id, note, date.today().isoformat()))


@app.route("/leads")
def leads():

    if not session.get("logged_in"):
        return redirect("/")

    search = request.args.get("search", "").strip()
    project_filter = request.args.get("project_id", "").strip()

    conn = get_db()

    # Current logged-in user
    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
    """, (session.get("username"),)).fetchone()

    if not current_user:
        conn.close()
        return "User not found"

    query = """
        SELECT
            leads.*,
            projects.name AS project_name,
            users.name AS assigned_user_name,
            (SELECT note FROM lead_notes WHERE lead_id = leads.id ORDER BY id DESC LIMIT 1) AS latest_note,
            (SELECT note_date FROM lead_notes WHERE lead_id = leads.id ORDER BY id DESC LIMIT 1) AS latest_note_date
        FROM leads
        LEFT JOIN projects
            ON leads.project_id = projects.id
        LEFT JOIN users
            ON leads.assigned_to = users.username
        WHERE 1=1
    """

    params = []

    # Sales users can see only their own leads
    if current_user["role"] == "Sales":

        query += """
            AND leads.assigned_to = ?
        """

        params.append(current_user["username"])

    # Project-wise filter
    if project_filter:
        query += " AND leads.project_id = ?"
        params.append(project_filter)

    # Search
    if search:

        query += """
            AND (
                leads.name LIKE ?
                OR leads.phone LIKE ?
            )
        """

        search_value = f"%{search}%"

        params.extend([
            search_value,
            search_value
        ])

    query += """
        ORDER BY leads.id DESC
    """

    leads = conn.execute(
        query,
        params
    ).fetchall()

    projects = conn.execute("""
        SELECT id, name
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "leads.html",
        leads=leads,
        search=search,
        projects=projects,
        project_filter=project_filter,
        current_role=current_user["role"]
    )
@app.route("/add-lead", methods=["GET", "POST"])
def add_lead():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    sales_users = conn.execute("""
        SELECT *
        FROM users
        WHERE active = 1
        AND role = 'Sales'
        ORDER BY name, username
    """).fetchall()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        project_id = request.form.get("project_id") or None
        notes = request.form.get("notes", "").strip()
        follow_up_date = request.form.get("follow_up_date") or None
        visit_date = request.form.get("visit_date") or None
        visit_time = request.form.get("visit_time") or None
        status = request.form.get("status", "New")
        assigned_to = request.form.get("assigned_to") or None

        if not phone:
            conn.close()
            return "Phone number is required"

        # Check duplicate phone in leads
        existing = conn.execute("""
            SELECT
                leads.*,
                projects.name AS project_name
            FROM leads
            LEFT JOIN projects
                ON leads.project_id = projects.id
            WHERE leads.phone = ?
        """, (phone,)).fetchone()

        if existing:
            existing_name = existing["name"] or "Name not added"
            project_name = existing["project_name"] or "Unassigned"

            conn.close()

            return f"""
            <h2>⚠️ Duplicate Phone Number</h2>
            <p>এই নম্বরটি আগে থেকেই Lead হিসেবে CRM-এ আছে।</p>
            <p><strong>👤 Name:</strong> {existing_name}</p>
            <p><strong>🏗️ Project:</strong> {project_name}</p>
            <a href="/add-lead">← Back to Add Lead</a>
            """

        # Check duplicate phone in customers
        existing_customer = conn.execute("""
            SELECT name
            FROM customers
            WHERE phone = ?
        """, (phone,)).fetchone()

        if existing_customer:
            existing_name = existing_customer["name"] or "Name not added"

            conn.close()

            return f"""
            <h2>⚠️ Duplicate Phone Number</h2>
            <p>এই ফোন নম্বরটি আগে থেকেই Customer হিসেবে CRM-এ আছে।</p>
            <p><strong>👤 Name:</strong> {existing_name}</p>
            <a href="/add-lead">← Back to Add Lead</a>
            """

        cursor = conn.execute("""
            INSERT INTO leads
            (name, phone, project_id, notes, follow_up_date, status, assigned_to, visit_date, visit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            phone,
            project_id,
            notes,
            follow_up_date,
            status,
            assigned_to,
            visit_date,
            visit_time
        ))

        lead_id = cursor.lastrowid

        if notes:
            save_lead_note(conn, lead_id, notes)

        if follow_up_date:
            conn.execute("""
                INSERT INTO followups
                (lead_id, name, phone, follow_up_date, note, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                lead_id,
                name,
                phone,
                follow_up_date,
                notes,
                "New"
            ))

        conn.commit()
        conn.close()

        return redirect("/leads")

    conn.close()

    return render_template(
        "add_lead.html",
        projects=projects,
        sales_users=sales_users
    )
@app.route("/edit-lead/<int:lead_id>", methods=["GET", "POST"])
def edit_lead(lead_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ? AND active = 1
    """, (session.get("username"),)).fetchone()

    if not current_user:
        conn.close()
        return "User not found"

    lead = conn.execute("""
        SELECT
            leads.*,
            projects.name AS project_name
        FROM leads
        LEFT JOIN projects
            ON leads.project_id = projects.id
        WHERE leads.id = ?
    """, (lead_id,)).fetchone()

    if not lead:
        conn.close()
        return "Lead not found"

    # Sales users may edit only their own assigned leads.
    if current_user["role"] == "Sales" and lead["assigned_to"] != current_user["username"]:
        conn.close()
        return "Access Denied"

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    sales_users = conn.execute("""
        SELECT *
        FROM users
        WHERE active = 1
        AND role = 'Sales'
        ORDER BY name, username
    """).fetchall()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        project_id = request.form.get("project_id") or None
        notes = request.form.get("notes", "").strip()
        follow_up_date = request.form.get("follow_up_date") or None
        visit_date = request.form.get("visit_date") or None
        visit_time = request.form.get("visit_time") or None
        status = request.form.get("status", "New")

        if not phone:
            conn.close()
            return "Phone number is required"

        # Phone must remain unique across leads and customers.
        existing_lead = conn.execute("""
            SELECT id
            FROM leads
            WHERE phone = ?
            AND id != ?
        """, (phone, lead_id)).fetchone()

        if existing_lead:
            conn.close()
            return "Phone number already exists in another lead"

        existing_customer = conn.execute("""
            SELECT id
            FROM customers
            WHERE phone = ?
        """, (phone,)).fetchone()

        if existing_customer:
            conn.close()
            return "Phone number already exists in customers"

        assigned_to = lead["assigned_to"]

        # Only Admin/Manager can change lead assignment.
        if current_user["role"] in ["Admin", "Manager"]:
            assigned_to = request.form.get("assigned_to") or None

        previous_note = lead["notes"] or ""

        conn.execute("""
            UPDATE leads
            SET name = ?,
                phone = ?,
                project_id = ?,
                notes = ?,
                follow_up_date = ?,
                visit_date = ?,
                visit_time = ?,
                status = ?,
                assigned_to = ?
            WHERE id = ?
        """, (
            name,
            phone,
            project_id,
            notes,
            follow_up_date,
            visit_date,
            visit_time,
            status,
            assigned_to,
            lead_id
        ))

        if notes and notes != previous_note:
            save_lead_note(conn, lead_id, notes)

        existing_followup = conn.execute("""
            SELECT id
            FROM followups
            WHERE lead_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (lead_id,)).fetchone()

        if follow_up_date:
            if existing_followup:
                conn.execute("""
                    UPDATE followups
                    SET name = ?,
                        phone = ?,
                        follow_up_date = ?,
                        note = ?
                    WHERE id = ?
                """, (
                    name,
                    phone,
                    follow_up_date,
                    notes,
                    existing_followup["id"]
                ))
            else:
                conn.execute("""
                    INSERT INTO followups
                    (lead_id, name, phone, follow_up_date, note, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    lead_id,
                    name,
                    phone,
                    follow_up_date,
                    notes,
                    "New"
                ))
        elif existing_followup:
            conn.execute("DELETE FROM followups WHERE id = ?", (existing_followup["id"],))

        conn.commit()
        conn.close()

        return redirect("/leads")

    conn.close()

    return render_template(
        "edit_lead.html",
        lead=lead,
        projects=projects,
        sales_users=sales_users,
        current_role=current_user["role"]
    )


@app.route("/delete-lead/<int:lead_id>", methods=["GET", "POST"])
def delete_lead(lead_id):
    if not session.get("logged_in"):
        return redirect("/")
    conn = get_db()
    current_user = conn.execute("SELECT role, username FROM users WHERE username = ? AND active = 1", (session.get("username"),)).fetchone()
    lead = conn.execute("SELECT assigned_to FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not current_user or not lead:
        conn.close()
        return redirect("/leads")
    if current_user["role"] != "Admin":
        conn.close()
        return "Access Denied"
    conn.execute("DELETE FROM followups WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM lead_notes WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM canceled_leads WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return redirect("/leads")

@app.route("/set-visit/<int:lead_id>", methods=["GET", "POST"])
def set_visit(lead_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    lead = conn.execute("""
        SELECT *
        FROM leads
        WHERE id = ?
    """, (lead_id,)).fetchone()

    if not lead:
        conn.close()
        return "Lead not found"

    if request.method == "POST":

        visit_date = request.form.get("visit_date") or None
        visit_time = request.form.get("visit_time") or None
        visit_status = request.form.get("visit_status", "Planned")
        completed_date = lead["visit_completed_date"]

        if visit_status == "Completed":
            completed_date = date.today().isoformat()
        elif visit_status != "Completed":
            completed_date = None

        conn.execute("""
            UPDATE leads
            SET visit_date = ?,
                visit_time = ?,
                visit_status = ?,
                visit_completed_date = ?
            WHERE id = ?
        """, (
            visit_date,
            visit_time,
            visit_status,
            completed_date,
            lead_id
        ))

        conn.commit()
        conn.close()

        return redirect("/leads")

    conn.close()

    return render_template(
        "set_visit.html",
        lead=lead
    )
@app.route("/visits")
def visits():

    if not session.get("logged_in"):
        return redirect("/")

    selected_date = request.args.get("date")

    conn = get_db()

    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ? AND active = 1
    """, (session.get("username"),)).fetchone()

    if not current_user:
        conn.close()
        return "User not found"

    date_query = """
        SELECT
            visit_date,
            COUNT(*) AS visit_count
        FROM leads
        WHERE visit_date IS NOT NULL
        AND TRIM(visit_date) != ''
    """
    date_params = []

    if current_user["role"] == "Sales":
        date_query += " AND assigned_to = ?"
        date_params.append(current_user["username"])

    date_query += """
        GROUP BY visit_date
        ORDER BY visit_date ASC
    """

    visit_dates = conn.execute(date_query, date_params).fetchall()

    visits = []

    if selected_date:
        visit_query = """
            SELECT
                leads.*,
                projects.name AS project_name,
                users.name AS assigned_user_name,
                (SELECT note FROM lead_notes
                 WHERE lead_id = leads.id
                 ORDER BY id DESC LIMIT 1) AS latest_note,
                (SELECT note_date FROM lead_notes
                 WHERE lead_id = leads.id
                 ORDER BY id DESC LIMIT 1) AS latest_note_date
            FROM leads
            LEFT JOIN projects ON leads.project_id = projects.id
            LEFT JOIN users ON leads.assigned_to = users.username
            WHERE leads.visit_date = ?
        """
        visit_params = [selected_date]

        if current_user["role"] == "Sales":
            visit_query += " AND leads.assigned_to = ?"
            visit_params.append(current_user["username"])

        visit_query += " ORDER BY leads.visit_time ASC, leads.id DESC"

        visits = conn.execute(
            visit_query,
            visit_params
        ).fetchall()

    conn.close()

    return render_template(
        "visits.html",
        visit_dates=visit_dates,
        visits=visits,
        selected_date=selected_date
    )


@app.route("/canceled")
def canceled():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ? AND active = 1
    """, (session.get("username"),)).fetchone()

    if not current_user:
        conn.close()
        return "User not found"

    query = """
        SELECT *
        FROM canceled_leads
        WHERE 1=1
    """
    params = []

    if current_user["role"] == "Sales":
        query += " AND assigned_to = ?"
        params.append(current_user["username"])

    query += " ORDER BY cancelled_date DESC, id DESC"

    canceled_leads = conn.execute(query, params).fetchall()

    conn.close()

    return render_template(
        "canceled.html",
        canceled_leads=canceled_leads
    )


@app.route("/delete-canceled/<int:canceled_id>", methods=["GET", "POST"])
def delete_canceled(canceled_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()
    conn.execute(
        "DELETE FROM canceled_leads WHERE id = ?",
        (canceled_id,)
    )
    conn.commit()
    conn.close()

    return redirect("/canceled")


@app.route("/daily-report")
def daily_report():

    if not session.get("logged_in"):
        return redirect("/")

    report_date = request.args.get("date") or date.today().isoformat()
    project_filter = request.args.get("project_id", "").strip()

    conn = get_db()

    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ? AND active = 1
    """, (session.get("username"),)).fetchone()

    if not current_user:
        conn.close()
        return "User not found"

    projects = conn.execute("""
        SELECT id, name
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    params = [report_date, report_date]
    query = """
        SELECT
            leads.id,
            leads.name,
            leads.phone,
            leads.project_id,
            leads.created_at,
            leads.follow_up_date,
            leads.visit_date,
            leads.visit_time,
            leads.visit_status,
            leads.visit_completed_date,
            projects.name AS project_name,
            ln.note,
            ln.note_date,
            (SELECT COUNT(*) FROM lead_notes z WHERE z.lead_id = ln.lead_id AND z.id < ln.id) AS previous_note_count
        FROM lead_notes ln
        JOIN leads ON leads.id = ln.lead_id
        LEFT JOIN projects ON projects.id = leads.project_id
        WHERE ln.note_date = ?
          AND ln.id = (
              SELECT MAX(ln2.id)
              FROM lead_notes ln2
              WHERE ln2.lead_id = ln.lead_id
                AND ln2.note_date = ?
          )
    """

    if project_filter:
        query += " AND leads.project_id = ?"
        params.append(project_filter)

    if current_user["role"] == "Sales":
        query += " AND leads.assigned_to = ?"
        params.append(current_user["username"])

    query += " ORDER BY projects.name ASC, leads.id DESC"

    talked_leads = conn.execute(query, params).fetchall()

    visit_params = [report_date]
    visit_query = """
        SELECT COUNT(*)
        FROM leads
        WHERE visit_date = ?
          AND visit_date IS NOT NULL
    """
    if project_filter:
        visit_query += " AND project_id = ?"
        visit_params.append(project_filter)
    if current_user["role"] == "Sales":
        visit_query += " AND assigned_to = ?"
        visit_params.append(current_user["username"])
    visits_scheduled = conn.execute(visit_query, visit_params).fetchone()[0]

    completed_params = [report_date]
    completed_query = """
        SELECT COUNT(*)
        FROM leads
        WHERE visit_status = 'Completed'
          AND visit_completed_date = ?
    """
    if project_filter:
        completed_query += " AND project_id = ?"
        completed_params.append(project_filter)
    if current_user["role"] == "Sales":
        completed_query += " AND assigned_to = ?"
        completed_params.append(current_user["username"])
    visits_completed = conn.execute(completed_query, completed_params).fetchone()[0]

    cancelled_params = [report_date]
    cancelled_query = """
        SELECT COUNT(*)
        FROM canceled_leads
        WHERE cancelled_date = ?
    """
    if project_filter:
        cancelled_query += " AND project_id = ?"
        cancelled_params.append(project_filter)
    if current_user["role"] == "Sales":
        cancelled_query += " AND assigned_to = ?"
        cancelled_params.append(current_user["username"])
    cancelled_count = conn.execute(cancelled_query, cancelled_params).fetchone()[0]

    new_leads_count = sum(
        1 for lead in talked_leads
        if (lead["created_at"] or "")[:10] == report_date
        and (lead["previous_note_count"] or 0) == 0
    )
    followup_leads_count = sum(
        1 for lead in talked_leads
        if lead["follow_up_date"] == report_date
        and (lead["previous_note_count"] or 0) > 0
    )
    conn.close()

    return render_template(
        "daily_report.html",
        report_date=report_date,
        project_filter=project_filter,
        projects=projects,
        talked_leads=talked_leads,
        talked_count=len(talked_leads),
        new_leads_count=new_leads_count,
        followup_leads_count=followup_leads_count,
        visits_scheduled=visits_scheduled,
        visits_completed=visits_completed,
        cancelled_count=cancelled_count
    )


@app.route("/monthly-report")
def monthly_report():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    monthly_report = conn.execute("""
        SELECT
            month,
            SUM(sales) AS sales,
            SUM(payments) AS payments,
            SUM(expenses) AS expenses
        FROM (

            SELECT
                strftime('%Y-%m', created_at) AS month,
                sales,
                0 AS payments,
                0 AS expenses
            FROM customers

            UNION ALL

            SELECT
                strftime('%Y-%m', payment_date) AS month,
                0 AS sales,
                amount AS payments,
                0 AS expenses
            FROM payments
            WHERE payment_date IS NOT NULL

            UNION ALL

            SELECT
                strftime('%Y-%m', expense_date) AS month,
                0 AS sales,
                0 AS payments,
                amount AS expenses
            FROM expenses
            WHERE expense_date IS NOT NULL

        )
        WHERE month IS NOT NULL
        GROUP BY month
        ORDER BY month DESC
    """).fetchall()

    conn.close()

    return render_template(
        "monthly_report.html",
        monthly_report=monthly_report
    )
@app.route("/lead-notes/<int:lead_id>")
def lead_notes(lead_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    if not lead:
        conn.close()
        return "Lead not found"

    notes = conn.execute("""
        SELECT *
        FROM lead_notes
        WHERE lead_id = ?
        ORDER BY id DESC
    """, (lead_id,)).fetchall()

    conn.close()

    return render_template("lead_notes.html", lead=lead, notes=notes)


@app.route("/reassign-lead/<int:lead_id>", methods=["GET", "POST"])
def reassign_lead(lead_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    current_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
    """, (session.get("username"),)).fetchone()

    if not current_user or current_user["role"] not in ["Admin", "Manager"]:
        conn.close()
        return "Access Denied"

    lead = conn.execute("""
        SELECT
            leads.*,
            projects.name AS project_name,
            users.name AS assigned_user_name
        FROM leads
        LEFT JOIN projects
            ON leads.project_id = projects.id
        LEFT JOIN users
            ON leads.assigned_to = users.username
        WHERE leads.id = ?
    """, (lead_id,)).fetchone()

    if not lead:
        conn.close()
        return "Lead not found"

    sales_users = conn.execute("""
        SELECT *
        FROM users
        WHERE active = 1
        AND role = 'Sales'
        ORDER BY name, username
    """).fetchall()

    if request.method == "POST":

        assigned_to = request.form.get("assigned_to") or None

        conn.execute("""
            UPDATE leads
            SET assigned_to = ?
            WHERE id = ?
        """, (assigned_to, lead_id))

        conn.commit()
        conn.close()

        return redirect("/leads")

    conn.close()

    return render_template(
        "reassign_lead.html",
        lead=lead,
        sales_users=sales_users
    )
def _https_context():
    """Create a verified HTTPS context using the bundled CA certificate store."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception as exc:
        print("CERTIFI CONTEXT ERROR:", repr(exc))
        return ssl.create_default_context()


REMOTE_REPO = "mdrabbimolla/my-business-crm"
REMOTE_BRANCH = "main"
def _runtime_root_dir():
    # Android may expose HOME as /data, which is not writable by the app.
    # Store runtime update files inside the app-private files directory instead.
    if autoclass is not None:
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = cast("android.app.Activity", PythonActivity.mActivity)
            return os.path.join(activity.getFilesDir().getAbsolutePath(), ".mycrm_runtime")
        except Exception as exc:
            print("ANDROID RUNTIME DIR ERROR:", repr(exc))
    return os.path.join(os.path.expanduser("~"), ".mycrm_runtime")

RUNTIME_TEMPLATE_DIR = os.path.join(_runtime_root_dir(), "templates")
RUNTIME_VERSION_FILE = os.path.join(_runtime_root_dir(), "version.txt")


def _remote_update_info():
    url = f"https://api.github.com/repos/{REMOTE_REPO}/branches/{REMOTE_BRANCH}"
    request = urllib.request.Request(url, headers={"User-Agent": "My-Business-CRM-Updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=5, context=_https_context()) as response:
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
        with urllib.request.urlopen(request, timeout=20, context=_https_context()) as response:
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



_android_pdf_reports = {}
_android_pdf_status = {}


def save_android_pdf_file(file_path):
    """Publish a generated PDF to the public Downloads/My Business CRM/Reports folder."""
    if autoclass is None or cast is None or not os.path.exists(file_path):
        return False, "Android bridge or generated PDF file is unavailable"

    uri = None
    try:
        ContentValues = autoclass("android.content.ContentValues")
        MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
        Environment = autoclass("android.os.Environment")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")

        activity = cast("android.app.Activity", PythonActivity.mActivity)
        resolver = activity.getContentResolver()

        values = ContentValues()
        values.put("display_name", os.path.basename(file_path))
        values.put("mime_type", "application/pdf")
        values.put("relative_path", Environment.DIRECTORY_DOWNLOADS + "/My Business CRM/Reports")
        # Follow Android's documented MediaStore flow:
        # create as pending -> write -> publish by setting IS_PENDING to 0.
        values.put("is_pending", 1)

        downloads_uri = MediaStoreDownloads.getContentUri("external")
        uri = resolver.insert(downloads_uri, values)
        if uri is None:
            return False, "MediaStore could not create the Downloads entry"

        pfd = resolver.openFileDescriptor(uri, "w", None)
        if pfd is None:
            raise RuntimeError("MediaStore could not open the PDF for writing")

        try:
            # Use ParcelFileDescriptor.getFd() + a duplicated native fd.
            # This avoids PyJNIus byte[]/OutputStream overload issues.
            native_fd = int(pfd.getFd())
            with os.fdopen(os.dup(native_fd), "wb", closefd=True) as target:
                with open(file_path, "rb") as source:
                    shutil.copyfileobj(source, target, length=64 * 1024)
                target.flush()
                os.fsync(target.fileno())
        finally:
            pfd.close()

        publish_values = ContentValues()
        publish_values.put("is_pending", 0)
        updated = resolver.update(uri, publish_values, None, None)
        if updated <= 0:
            raise RuntimeError("MediaStore could not publish the PDF")

        return True, "PDF published successfully"

    except Exception as exc:
        error = repr(exc)
        print("PDF SAVE ERROR:", error)
        if uri is not None:
            try:
                resolver.delete(uri, None, None)
            except Exception:
                pass
        return False, error

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
        RectF = autoclass("android.graphics.RectF")
        pdf = PdfDocument()
        page_width, page_height = 595, 842
        margin = 28
        page_no = 1
        y = margin

        navy = 0xFF172554
        gold = 0xFFD4AF37
        slate = 0xFF475569
        light = 0xFFF8FAFC
        white = 0xFFFFFFFF

        title_paint = Paint()
        title_paint.setTextSize(19)
        title_paint.setTypeface(Typeface.DEFAULT_BOLD)
        title_paint.setColor(navy)

        sub_paint = Paint()
        sub_paint.setTextSize(9)
        sub_paint.setColor(slate)

        body_paint = Paint()
        body_paint.setTextSize(8.5)
        body_paint.setColor(navy)

        header_paint = Paint()
        header_paint.setTextSize(8)
        header_paint.setTypeface(Typeface.DEFAULT_BOLD)
        header_paint.setColor(white)

        line_paint = Paint()
        line_paint.setColor(0xFFE2E8F0)
        line_paint.setStrokeWidth(1)

        fill_paint = Paint()
        fill_paint.setColor(light)

        gold_paint = Paint()
        gold_paint.setColor(gold)

        navy_paint = Paint()
        navy_paint.setColor(navy)

        def start_page():
            nonlocal page_no, y
            info = PdfDocument.PageInfo.Builder(page_width, page_height, page_no).create()
            page = pdf.startPage(info)
            y = margin
            return page

        page = start_page()
        canvas = page.getCanvas()

        def draw_top_brand():
            nonlocal y
            canvas.drawRect(0, 0, page_width, 68, navy_paint)
            canvas.drawRect(0, 64, page_width, 68, gold_paint)
            canvas.drawText("My Business CRM", margin, 28, header_paint)
            canvas.drawText("DAILY SALES REPORT", margin, 49, header_paint)
            y = 92

        def draw_summary():
            nonlocal y
            summary = [
                ("Talked", report["talked"]),
                ("New", report["new"]),
                ("Follow-up", report["followup"]),
                ("Visit Set", report["visits"]),
                ("Completed", report["completed"]),
                ("Cancelled", report["cancelled"]),
            ]
            box_w = (page_width - 2 * margin - 5 * 8) / 6.0
            x = margin
            for label, value in summary:
                canvas.drawRoundRect(RectF(x, y, x + box_w, y + 43), 6, 6, fill_paint)
                canvas.drawText(str(value), x + 7, y + 17, title_paint)
                canvas.drawText(label, x + 7, y + 34, sub_paint)
                x += box_w + 8
            y += 56

        draw_top_brand()
        canvas.drawText("Report Date: " + report["date"], margin, y, sub_paint)
        if report.get("project"):
            canvas.drawText("Project: " + report["project"], margin + 145, y, sub_paint)
        y += 18
        draw_summary()

        columns = [
            ("#", 25), ("Name", 98), ("Phone", 78), ("Project", 92),
            ("Type", 67), ("Visit", 72), ("Done", 40), ("Note", 95)
        ]
        x_positions = []
        x = margin
        for _, width in columns:
            x_positions.append(x)
            x += width
        right = page_width - margin

        def draw_header():
            nonlocal y
            canvas.drawRect(margin, y - 13, right, y + 7, navy_paint)
            for idx, (label, _) in enumerate(columns):
                canvas.drawText(label, x_positions[idx] + 3, y + 1, header_paint)
            y += 18
            canvas.drawLine(margin, y, right, y, line_paint)

        draw_header()

        for row in report["rows"]:
            if y > page_height - 48:
                footer = Paint()
                footer.setTextSize(7)
                footer.setColor(slate)
                canvas.drawText("My Business CRM • Page " + str(page_no), margin, page_height - 18, footer)
                pdf.finishPage(page)
                page = start_page()
                canvas = page.getCanvas()
                draw_top_brand()
                canvas.drawText("Report Date: " + report["date"] + " • Continued", margin, y, sub_paint)
                y += 22
                draw_header()

            values = [
                row["no"], row["name"], row["phone"], row["project"],
                row["type"], row["visit"], row["done"], row["note"]
            ]
            row_y = y
            max_lines = 1
            for idx, value in enumerate(values):
                text_value = str(value or "-").replace("\\n", " ")
                width = columns[idx][1]
                max_chars = max(4, int(width / 5.0))
                chunks = []
                while len(text_value) > max_chars:
                    cut = text_value.rfind(" ", 0, max_chars)
                    if cut <= 0:
                        cut = max_chars
                    chunks.append(text_value[:cut])
                    text_value = text_value[cut:].strip()
                chunks.append(text_value)
                for line_idx, chunk in enumerate(chunks[:3]):
                    canvas.drawText(chunk, x_positions[idx] + 3, row_y + line_idx * 10, body_paint)
                max_lines = max(max_lines, min(3, len(chunks)))
            y += max_lines * 10 + 8
            canvas.drawLine(margin, y - 4, right, y - 4, line_paint)

        footer = Paint()
        footer.setTextSize(7)
        footer.setColor(slate)
        canvas.drawText("My Business CRM • Page " + str(page_no), margin, page_height - 18, footer)
        pdf.finishPage(page)

        out_dir = os.path.join("/tmp", "mycrm_reports")
        os.makedirs(out_dir, exist_ok=True)
        filename = "Daily_Report_" + report["date"] + ".pdf"
        path = os.path.join(out_dir, filename)
        FileOutputStream = autoclass("java.io.FileOutputStream")
        output = FileOutputStream(path)
        try:
            pdf.writeTo(output)
            output.flush()
        finally:
            output.close()
            pdf.close()

        ok, save_message = save_android_pdf_file(path)
        if ok:
            _android_pdf_status[token] = {
                "done": True,
                "ok": True,
                "message": "Premium PDF saved to Downloads/My Business CRM/Reports"
            }
        else:
            _android_pdf_status[token] = {
                "done": True,
                "ok": False,
                "error": "PDF save failed: " + save_message
            }
    except Exception as exc:
        print("PDF REPORT ERROR:", repr(exc))
        _android_pdf_status[token] = {
            "done": True,
            "ok": False,
            "error": str(exc) or "PDF could not be created"
        }
    finally:
        _android_pdf_reports.pop(token, None)

@app.route("/share-daily-report", methods=["POST"])
def share_daily_report():
    if not session.get("logged_in"):
        return {"ok": False, "error": "Not logged in"}, 401
    payload = request.get_json(silent=True) or {}
    report_text = str(payload.get("text") or "").strip()
    if not report_text:
        return {"ok": False, "error": "Report text is empty"}, 400
    if len(report_text) > 50000:
        return {"ok": False, "error": "Report is too large to share"}, 400
    if not share_android_text(report_text):
        return {"ok": False, "error": "Android Share could not be opened"}, 500
    return {"ok": True}

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
        # PDF generation and MediaStore I/O do not require the Android UI thread.
        # Running them directly avoids losing the job between Flask and the UI thread.
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




@app.route("/check-updates")
def check_updates():
    if not session.get("logged_in"):
        return redirect("/")
    result = _sync_remote_templates(force=False)
    if result.get("ok"):
        if result.get("updated"):
            message = "✅ CRM UI update installed successfully. Please reload this page."
        else:
            message = "✅ You already have the latest CRM UI."
    else:
        message = "❌ Update check failed: " + (result.get("message") or "Unknown error")
    return render_template("update.html", message=message)

if __name__ == "__main__":
    # Ask for call permission automatically when the Android app starts.
    if request_permissions is not None and Permission is not None and run_on_ui_thread is not None:
        try:
            run_on_ui_thread(request_android_call_permission)()
        except Exception as exc:
            print("STARTUP PERMISSION ERROR:", exc)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
