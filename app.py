from datetime import date
from urllib.parse import quote
from flask import Flask, request, redirect, session, render_template
from database import get_db, init_db

try:
    from jnius import autoclass
except ImportError:
    autoclass = None

app = Flask(__name__)

app.secret_key = "mycrm-secret-key"


init_db()


def open_android_url(url):
    if autoclass is None:
        return False

    try:
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")

        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        PythonActivity.mActivity.startActivity(intent)
        return True
    except Exception:
        return False


def clean_phone(phone):
    phone = (phone or "").strip()
    phone = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("880"):
        return phone
    if phone.startswith("0"):
        return "88" + phone
    return phone


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
    <h1>My Business CRM</h1>

    <form method="POST">

        <input
            type="text"
            name="username"
            placeholder="Username"
            required
        >

        <br><br>

        <input
            type="password"
            name="password"
            placeholder="Password"
            required
        >

        <br><br>

        <button type="submit">
            Login
        </button>

    </form>
    """

@app.route("/android-call")
def android_call():
    if not session.get("logged_in"):
        return redirect("/")

    phone = clean_phone(request.args.get("phone"))
    next_url = request.args.get("next", "/customers")

    if not phone:
        return "Phone number is missing"

    open_android_url("tel:" + phone)
    return redirect(next_url)


@app.route("/android-whatsapp")
def android_whatsapp():
    if not session.get("logged_in"):
        return redirect("/")

    phone = clean_phone(request.args.get("phone"))
    next_url = request.args.get("next", "/customers")

    if not phone:
        return "Phone number is missing"

    open_android_url("https://wa.me/" + phone)
    return redirect(next_url)


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

    total_paid = conn.execute(
        "SELECT COALESCE(SUM(paid), 0) FROM customers"
    ).fetchone()[0]

    total_payments = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments"
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

    today = date.today().isoformat()

    today_followups = conn.execute(
        "SELECT * FROM followups WHERE follow_up_date = ?",
        (today,)
    ).fetchall()

    today_followup_count = len(today_followups)

    missed_followup_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM followups
    WHERE follow_up_date < ?
    """,
    (today,)
    ).fetchone()[0]

    upcoming_followup_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM followups
    WHERE follow_up_date > ?
    """,
    (today,)
    ).fetchone()[0]

    number_off_count = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE status = 'Number Off'"
    ).fetchone()[0]

    not_received_count = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE status = 'Not Received'"
    ).fetchone()[0]

    positive_count = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE status = 'Positive'"
    ).fetchone()[0]

    cancel_count = conn.execute(
        "SELECT COUNT(*) FROM followups WHERE status = 'Cancel'"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        total_customers=total_customers,
        total_sales=total_sales,
        total_paid=total_paid,
        total_payments=total_payments,
        category_expenses=category_expenses,
        total_due=total_due,
        total_expenses=total_expenses,
        net_profit=net_profit,
        today_followup_count=today_followup_count,
        missed_followup_count=missed_followup_count,
        upcoming_followup_count=upcoming_followup_count,
        number_off_count=number_off_count,
        not_received_count=not_received_count,
        positive_count=positive_count,
        cancel_count=cancel_count,
        today_followups=today_followups
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


@app.route("/delete-project/<int:project_id>")
def delete_project(project_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    conn.execute(
        "DELETE FROM projects WHERE id = ?",
        (project_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/projects")

@app.route("/projects")
def projects():

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

    projects = conn.execute("""
        SELECT *
        FROM projects
        WHERE status = 'Active'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "projects.html",
        projects=projects
    )

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
@app.route("/delete-payment/<int:payment_id>")
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
@app.route("/delete-customer/<int:customer_id>")
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

        name = request.form.get("name")
        phone = request.form.get("phone")
        follow_up_date = request.form.get("follow_up_date")
        note = request.form.get("note")
        status = request.form.get("status")

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

        conn.commit()
        conn.close()

        return redirect("/followups")

    conn.close()

    return render_template(
        "edit_followup.html",
        followup=followup
    )
@app.route("/delete-followup/<int:followup_id>")
def delete_followup(followup_id):

    if not session.get("logged_in"):
        return redirect("/")

    conn = get_db()

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


@app.route("/delete-expense/<int:expense_id>")
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
@app.route("/leads")
def leads():

    if not session.get("logged_in"):
        return redirect("/")

    search = request.args.get("search", "").strip()

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
            users.name AS assigned_user_name
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

    conn.close()

    return render_template(
        "leads.html",
        leads=leads,
        search=search
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

        conn.execute("""
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

        conn.commit()
        conn.close()

        return redirect("/leads")

    conn.close()

    return render_template(
        "add_lead.html",
        projects=projects,
        sales_users=sales_users
    )
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

        conn.execute("""
            UPDATE leads
            SET visit_date = ?,
                visit_time = ?
            WHERE id = ?
        """, (
            visit_date,
            visit_time,
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
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
