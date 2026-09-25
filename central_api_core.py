from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
import sqlite3

def register_core_routes(app, db, require_token):
    api = Blueprint("central_core", __name__)

    def now():
        return datetime.now(timezone.utc).isoformat()

    def visible_lead(conn, lead_id):
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row or (g.user["role"] == "Sales" and row["assigned_to"] != g.user["username"]):
            return None
        return row

    def duplicate_phone(conn, phone, lead_id=None, customer_id=None):
        if not phone:
            return None
        q="SELECT id,name FROM leads WHERE phone=?"; p=[phone]
        if lead_id is not None: q+=" AND id!=?"; p.append(lead_id)
        row=conn.execute(q,p).fetchone()
        if row: return {"type":"lead","id":row["id"],"name":row["name"]}
        q="SELECT id,name FROM customers WHERE phone=?"; p=[phone]
        if customer_id is not None: q+=" AND id!=?"; p.append(customer_id)
        row=conn.execute(q,p).fetchone()
        return {"type":"customer","id":row["id"],"name":row["name"]} if row else None

    @api.put("/api/projects/<int:project_id>")
    @require_token
    def update_project(project_id):
        data=request.get_json(silent=True) or {}; conn=db()
        row=conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="project not found"),404
        name=(data.get("name",row["name"]) or "").strip()
        if not name: conn.close(); return jsonify(error="project name is required"),400
        try:
            conn.execute("UPDATE projects SET name=?,location=?,status=?,notes=? WHERE id=?",
                (name,data.get("location",row["location"]),data.get("status",row["status"]) or "Active",data.get("notes",row["notes"]),project_id))
            conn.commit(); out=conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            return jsonify(project=dict(out))
        except sqlite3.IntegrityError:
            return jsonify(error="project already exists"),409
        finally: conn.close()

    @api.get("/api/leads/<int:lead_id>")
    @require_token
    def get_lead(lead_id):
        conn=db(); row=visible_lead(conn,lead_id)
        if not row: conn.close(); return jsonify(error="lead not found or access denied"),404
        notes=conn.execute("SELECT * FROM lead_notes WHERE lead_id=? ORDER BY id DESC",(lead_id,)).fetchall()
        conn.close(); out=dict(row); out["notes_history"]=[dict(x) for x in notes]
        return jsonify(lead=out)

    @api.put("/api/leads/<int:lead_id>")
    @require_token
    def update_lead(lead_id):
        data=request.get_json(silent=True) or {}; conn=db(); row=visible_lead(conn,lead_id)
        if not row: conn.close(); return jsonify(error="lead not found or access denied"),404
        phone=(data.get("phone",row["phone"]) or "").strip()
        dup=duplicate_phone(conn,phone,lead_id=lead_id)
        if dup: conn.close(); return jsonify(error="phone number already exists",duplicate=dup),409
        assigned=row["assigned_to"]
        if g.user["role"] in {"Admin","Manager"} and "assigned_to" in data: assigned=data.get("assigned_to") or None
        conn.execute("""UPDATE leads SET name=?,phone=?,project_id=?,notes=?,follow_up_date=?,status=?,
                        assigned_to=?,visit_date=?,visit_time=?,visit_status=?,visit_completed_date=? WHERE id=?""",
            (data.get("name",row["name"]),phone,data.get("project_id",row["project_id"]),data.get("notes",row["notes"]),
             data.get("follow_up_date",row["follow_up_date"]),data.get("status",row["status"]) or "New",assigned,
             data.get("visit_date",row["visit_date"]),data.get("visit_time",row["visit_time"]),
             data.get("visit_status",row["visit_status"]) or "Planned",data.get("visit_completed_date",row["visit_completed_date"]),lead_id))
        if "notes" in data and (data.get("notes") or "").strip() and data.get("notes") != (row["notes"] or ""):
            conn.execute("INSERT INTO lead_notes(lead_id,note,note_date,created_at) VALUES(?,?,?,?)",
                         (lead_id,data["notes"].strip(),datetime.now(timezone.utc).date().isoformat(),now()))
        conn.execute("DELETE FROM followups WHERE lead_id=?", (lead_id,))
        if data.get("follow_up_date"):
            conn.execute("""INSERT INTO followups(lead_id,name,phone,follow_up_date,note,status,created_at)
                            VALUES(?,?,?,?,?,?,?)""",
                         (lead_id,data.get("name",row["name"]),phone,data["follow_up_date"],data.get("notes",row["notes"]) or "","New",now()))
        conn.commit(); out=conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone(); conn.close()
        return jsonify(lead=dict(out))

    @api.delete("/api/leads/<int:lead_id>")
    @require_token
    def delete_lead(lead_id):
        if g.user["role"] != "Admin": return jsonify(error="access denied"),403
        conn=db(); row=conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="lead not found"),404
        conn.execute("DELETE FROM followups WHERE lead_id=?", (lead_id,))
        conn.execute("DELETE FROM lead_notes WHERE lead_id=?", (lead_id,))
        conn.execute("DELETE FROM canceled_leads WHERE lead_id=?", (lead_id,))
        conn.execute("DELETE FROM leads WHERE id=?", (lead_id,)); conn.commit(); conn.close()
        return jsonify(deleted=True)

    @api.post("/api/leads/<int:lead_id>/notes")
    @require_token
    def add_note(lead_id):
        data=request.get_json(silent=True) or {}; note=(data.get("note") or "").strip()
        if not note: return jsonify(error="note is required"),400
        conn=db()
        if not visible_lead(conn,lead_id): conn.close(); return jsonify(error="lead not found or access denied"),404
        cur=conn.execute("INSERT INTO lead_notes(lead_id,note,note_date,created_at) VALUES(?,?,?,?)",
                         (lead_id,note,data.get("note_date") or datetime.now(timezone.utc).date().isoformat(),now()))
        conn.commit(); out=conn.execute("SELECT * FROM lead_notes WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close()
        return jsonify(note=dict(out)),201

    @api.get("/api/followups")
    @require_token
    def list_followups():
        conn=db()
        if g.user["role"]=="Sales":
            rows=conn.execute("""SELECT f.* FROM followups f LEFT JOIN leads l ON f.lead_id=l.id
                                 WHERE f.lead_id IS NULL OR l.assigned_to=? ORDER BY f.follow_up_date ASC,f.id DESC""",(g.user["username"],)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM followups ORDER BY follow_up_date ASC,id DESC").fetchall()
        conn.close(); return jsonify(followups=[dict(x) for x in rows])

    @api.post("/api/followups")
    @require_token
    def create_followup():
        data=request.get_json(silent=True) or {}; conn=db(); lead_id=data.get("lead_id")
        if lead_id is not None and not visible_lead(conn,int(lead_id)):
            conn.close(); return jsonify(error="lead not found or access denied"),404
        cur=conn.execute("""INSERT INTO followups(lead_id,name,phone,follow_up_date,note,status,created_at)
                            VALUES(?,?,?,?,?,?,?)""",
                         (lead_id,data.get("name"),data.get("phone"),data.get("follow_up_date"),data.get("note"),data.get("status") or "New",now()))
        conn.commit(); out=conn.execute("SELECT * FROM followups WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close()
        return jsonify(followup=dict(out)),201

    @api.get("/api/canceled-leads")
    @require_token
    def canceled():
        conn=db()
        if g.user["role"]=="Sales": rows=conn.execute("SELECT * FROM canceled_leads WHERE assigned_to=? ORDER BY cancelled_date DESC,id DESC",(g.user["username"],)).fetchall()
        else: rows=conn.execute("SELECT * FROM canceled_leads ORDER BY cancelled_date DESC,id DESC").fetchall()
        conn.close(); return jsonify(canceled_leads=[dict(x) for x in rows])

    @api.get("/api/customers/<int:customer_id>")
    @require_token
    def get_customer(customer_id):
        conn=db(); row=conn.execute("""SELECT customers.*, projects.name AS project_name
                                       FROM customers LEFT JOIN projects ON projects.id=customers.project_id
                                       WHERE customers.id=?""", (customer_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="customer not found"),404
        payments=conn.execute("SELECT * FROM payments WHERE customer_id=? ORDER BY id ASC",(customer_id,)).fetchall()
        sales=float(row["sales"] or 0)
        running_due=sales
        payment_list=[]
        for payment in payments:
            running_due -= float(payment["amount"] or 0)
            item=dict(payment)
            item["due_after_payment"]=running_due
            payment_list.append(item)
        payment_list.reverse()
        conn.close()
        out=dict(row)
        out["due"]=sales-float(row["paid"] or 0)
        out["payments"]=payment_list
        return jsonify(customer=out)

    @api.get("/api/payments/<int:payment_id>")
    @require_token
    def get_payment(payment_id):
        conn=db()
        row=conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify(error="payment not found"),404
        conn.close()
        return jsonify(payment=dict(row))

    @api.post("/api/customers")
    @require_token
    def create_customer():
        data=request.get_json(silent=True) or {}; name=(data.get("name") or "").strip(); phone=(data.get("phone") or "").strip()
        if not name: return jsonify(error="name is required"),400
        conn=db(); dup=duplicate_phone(conn,phone)
        if dup: conn.close(); return jsonify(error="phone number already exists",duplicate=dup),409
        try:
            cur=conn.execute("""INSERT INTO customers(name,phone,address,business,notes,follow_up,sales,paid,project_id,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                             (name,phone,data.get("address"),data.get("business"),data.get("notes"),data.get("follow_up"),
                              float(data.get("sales") or 0),float(data.get("paid") or 0),data.get("project_id"),now()))
            conn.commit(); out=conn.execute("SELECT * FROM customers WHERE id=?", (cur.lastrowid,)).fetchone()
            return jsonify(customer=dict(out)),201
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            return jsonify(error="customer could not be created"),400
        finally: conn.close()

    @api.put("/api/customers/<int:customer_id>")
    @require_token
    def update_customer(customer_id):
        data=request.get_json(silent=True) or {}; conn=db()
        row=conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="customer not found"),404
        phone=(data.get("phone",row["phone"]) or "").strip()
        dup=duplicate_phone(conn,phone,customer_id=customer_id)
        if dup: conn.close(); return jsonify(error="phone number already exists",duplicate=dup),409
        try:
            conn.execute("""UPDATE customers SET name=?,phone=?,address=?,business=?,notes=?,follow_up=?,
                            sales=?,paid=?,project_id=? WHERE id=?""",
                         ((data.get("name",row["name"]) or "").strip(),phone,
                          data.get("address",row["address"]),data.get("business",row["business"]),
                          data.get("notes",row["notes"]),data.get("follow_up",row["follow_up"]),
                          float(data.get("sales",row["sales"]) or 0),float(data.get("paid",row["paid"]) or 0),
                          data.get("project_id",row["project_id"]),customer_id))
            conn.commit(); out=conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            return jsonify(customer=dict(out))
        except ValueError:
            conn.rollback(); return jsonify(error="invalid customer data"),400
        finally: conn.close()

    @api.delete("/api/customers/<int:customer_id>")
    @require_token
    def delete_customer(customer_id):
        conn=db(); row=conn.execute("SELECT id FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="customer not found"),404
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,)); conn.commit(); conn.close()
        return jsonify(deleted=True)

    @api.post("/api/customers/<int:customer_id>/payments")
    @require_token
    def create_payment(customer_id):
        data=request.get_json(silent=True) or {}; amount=float(data.get("amount") or 0)
        if amount<=0: return jsonify(error="payment amount must be greater than zero"),400
        conn=db(); customer=conn.execute("SELECT id FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer: conn.close(); return jsonify(error="customer not found"),404
        cur=conn.execute("INSERT INTO payments(customer_id,amount,payment_date,note,created_at) VALUES(?,?,?,?,?)",
                         (customer_id,amount,data.get("payment_date"),data.get("note"),now()))
        conn.execute("UPDATE customers SET paid=COALESCE(paid,0)+? WHERE id=?",(amount,customer_id))
        conn.commit(); out=conn.execute("SELECT * FROM payments WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close()
        return jsonify(payment=dict(out)),201

    @api.put("/api/payments/<int:payment_id>")
    @require_token
    def update_payment(payment_id):
        data=request.get_json(silent=True) or {}; conn=db()
        row=conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="payment not found"),404
        try:
            amount=float(data.get("amount",row["amount"]) or 0)
            if amount<=0: conn.close(); return jsonify(error="payment amount must be greater than zero"),400
            diff=amount-float(row["amount"] or 0)
            conn.execute("UPDATE payments SET amount=?,payment_date=?,note=? WHERE id=?",
                         (amount,data.get("payment_date",row["payment_date"]),data.get("note",row["note"]),payment_id))
            conn.execute("UPDATE customers SET paid=COALESCE(paid,0)+? WHERE id=?",(diff,row["customer_id"]))
            conn.commit(); out=conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
            return jsonify(payment=dict(out))
        except ValueError:
            conn.rollback(); return jsonify(error="invalid payment data"),400
        finally: conn.close()

    @api.delete("/api/payments/<int:payment_id>")
    @require_token
    def delete_payment(payment_id):
        conn=db(); row=conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not row: conn.close(); return jsonify(error="payment not found"),404
        conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
        conn.execute("UPDATE customers SET paid=COALESCE(paid,0)-? WHERE id=?",(row["amount"],row["customer_id"]))
        conn.commit(); conn.close(); return jsonify(deleted=True)

    @api.get("/api/expenses")
    @require_token
    def expenses():
        conn=db(); rows=conn.execute("SELECT * FROM expenses ORDER BY expense_date DESC,id DESC").fetchall()
        total=conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]; conn.close()
        return jsonify(expenses=[dict(x) for x in rows],total_expenses=total)

    @api.post("/api/expenses")
    @require_token
    def create_expense():
        data=request.get_json(silent=True) or {}; amount=float(data.get("amount") or 0)
        if amount<=0: return jsonify(error="expense amount must be greater than zero"),400
        conn=db(); cur=conn.execute("INSERT INTO expenses(amount,expense_date,category,note,created_at) VALUES(?,?,?,?,?)",
                                    (amount,data.get("expense_date"),data.get("category"),data.get("note"),now()))
        conn.commit(); out=conn.execute("SELECT * FROM expenses WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close()
        return jsonify(expense=dict(out)),201

    app.register_blueprint(api)
