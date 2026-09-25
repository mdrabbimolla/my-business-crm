import os
import tempfile
import unittest

os.environ["CRM_API_SECRET"] = "test-secret"
tmp = tempfile.NamedTemporaryFile(delete=False)
tmp.close()
os.environ["CRM_CLOUD_DB"] = tmp.name

from cloud_api import app, init_cloud_db

class CloudApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_cloud_db()

    def setUp(self):
        self.client = app.test_client()
        # Isolate tests: all tests share one temporary central DB file.
        from cloud_api import db
        conn = db()
        for table in (
            "auth_tokens", "lead_notes", "followups", "canceled_leads",
            "payments", "expenses", "leads", "customers", "projects", "users", "project_files", "receipt_settings",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])

    def test_admin_bootstrap_login_and_me(self):
        response = self.client.post(
            "/api/bootstrap-user",
            json={"username":"sales1","password":"pass-123","name":"Sales One","role":"Sales"},
            headers={"X-CRM-API-SECRET":"test-secret"},
        )
        self.assertIn(response.status_code, (201, 409))
        login = self.client.post("/api/login", json={"username":"sales1","password":"pass-123"})
        self.assertEqual(login.status_code, 200)
        token = login.json["token"]
        me = self.client.get("/api/me", headers={"Authorization":f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json["user"]["username"], "sales1")

    def test_admin_can_list_create_and_reset_users(self):
        bootstrap = self.client.post(
            "/api/bootstrap-user",
            json={"username":"admin-user","password":"admin-pass","name":"Admin","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"},
        )
        self.assertEqual(bootstrap.status_code, 201)
        login = self.client.post("/api/login", json={"username":"admin-user","password":"admin-pass"})
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": f"Bearer {login.json['token']}"}

        created = self.client.post(
            "/api/users",
            json={"username":"new-sales","password":"sales-pass","name":"New Sales","role":"Sales","active":1},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["user"]["username"], "new-sales")

        users = self.client.get("/api/users", headers=headers)
        self.assertEqual(users.status_code, 200)
        self.assertTrue(any(u["username"] == "new-sales" for u in users.json["users"]))

        reset = self.client.put(
            f"/api/users/{created.json['user']['id']}/password",
            json={"password":"new-sales-pass"},
            headers=headers,
        )
        self.assertEqual(reset.status_code, 200)

        new_login = self.client.post("/api/login", json={"username":"new-sales","password":"new-sales-pass"})
        self.assertEqual(new_login.status_code, 200)

    def test_wrong_password_rejected(self):
        response = self.client.post("/api/login", json={"username":"sales1","password":"wrong"})
        self.assertEqual(response.status_code, 401)

    def test_two_logins_share_projects_and_leads(self):
        for username in ("sales2", "sales3"):
            response = self.client.post(
                "/api/bootstrap-user",
                json={"username":username,"password":"pass-123","name":username,"role":"Sales"},
                headers={"X-CRM-API-SECRET":"test-secret"},
            )
            self.assertEqual(response.status_code, 201)

        login1 = self.client.post("/api/login", json={"username":"sales2","password":"pass-123"})
        login2 = self.client.post("/api/login", json={"username":"sales3","password":"pass-123"})
        self.assertEqual(login1.status_code, 200)
        self.assertEqual(login2.status_code, 200)
        token1, token2 = login1.json["token"], login2.json["token"]

        project = self.client.post(
            "/api/projects",
            json={"name":"Shared Project"},
            headers={"Authorization":f"Bearer {token1}"},
        )
        self.assertEqual(project.status_code, 201)
        project_id = project.json["project"]["id"]

        lead = self.client.post(
            "/api/leads",
            json={"name":"Shared Lead","phone":"01700000001","project_id":project_id},
            headers={"Authorization":f"Bearer {token1}"},
        )
        self.assertEqual(lead.status_code, 201)

        projects_from_phone2 = self.client.get(
            "/api/projects", headers={"Authorization":f"Bearer {token2}"}
        )
        self.assertEqual(projects_from_phone2.status_code, 200)
        self.assertEqual(projects_from_phone2.json["projects"][0]["name"], "Shared Project")

        # A Sales user sees only leads assigned to that user.
        leads_phone2 = self.client.get(
            "/api/leads", headers={"Authorization":f"Bearer {token2}"}
        )
        self.assertEqual(leads_phone2.status_code, 200)
        self.assertEqual(leads_phone2.json["leads"], [])

    def test_lead_filters_and_display_fields(self):
        for username, name in (("filter-admin", "Filter Admin"), ("filter-sales", "Filter Sales")):
            response = self.client.post(
                "/api/bootstrap-user",
                json={"username": username, "password": "pass-123", "name": name,
                      "role": "Admin" if "admin" in username else "Sales"},
                headers={"X-CRM-API-SECRET": "test-secret"},
            )
            self.assertEqual(response.status_code, 201)

        admin_login = self.client.post("/api/login", json={"username":"filter-admin","password":"pass-123"})
        sales_login = self.client.post("/api/login", json={"username":"filter-sales","password":"pass-123"})
        admin_token = admin_login.json["token"]
        sales_token = sales_login.json["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        project = self.client.post(
            "/api/projects", json={"name":"Filter Project"},
            headers=admin_headers,
        )
        self.assertEqual(project.status_code, 201)
        project_id = project.json["project"]["id"]

        lead = self.client.post(
            "/api/leads",
            json={"name":"Unique Filter Person","phone":"01900000011",
                  "project_id":project_id, "assigned_to":"filter-sales"},
            headers=admin_headers,
        )
        self.assertEqual(lead.status_code, 201)
        lead_id = lead.json["lead"]["id"]

        note = self.client.post(
            f"/api/leads/{lead_id}/notes",
            json={"note":"Latest central note","note_date":"2026-09-25"},
            headers={"Authorization": f"Bearer {sales_token}"},
        )
        self.assertEqual(note.status_code, 201)

        response = self.client.get(
            "/api/leads?search=Unique%20Filter&project_id=" + str(project_id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["leads"]), 1)
        item = response.json["leads"][0]
        self.assertEqual(item["project_name"], "Filter Project")
        self.assertEqual(item["assigned_user_name"], "Filter Sales")
        self.assertEqual(item["latest_note"], "Latest central note")
        self.assertEqual(item["latest_note_date"], "2026-09-25")

        sales_response = self.client.get(
            "/api/leads?search=Unique%20Filter",
            headers={"Authorization": f"Bearer {sales_token}"},
        )
        self.assertEqual(sales_response.status_code, 200)
        self.assertEqual(len(sales_response.json["leads"]), 1)

        empty_response = self.client.get(
            "/api/leads?search=Unique%20Filter&project_id=999999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json["leads"], [])

    def test_duplicate_phone_is_blocked(self):
        self.client.post("/api/bootstrap-user",
            json={"username":"dup-user","password":"pass-123","name":"Duplicate Test","role":"Sales"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        login = self.client.post("/api/login", json={"username":"dup-user","password":"pass-123"})
        self.assertEqual(login.status_code, 200)
        token = login.json["token"]
        first = self.client.post(
            "/api/leads", json={"name":"Phone Owner","phone":"01700000002"},
            headers={"Authorization":f"Bearer {token}"},
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/leads", json={"name":"Duplicate","phone":"01700000002"},
            headers={"Authorization":f"Bearer {token}"},
        )
        self.assertEqual(second.status_code, 409)

    def test_core_shared_customer_payment_flow(self):
        self.client.post("/api/bootstrap-user",
            json={"username":"customer-sales","password":"pass-123","name":"Customer Test","role":"Sales"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        login = self.client.post("/api/login", json={"username":"customer-sales","password":"pass-123"})
        self.assertEqual(login.status_code, 200)
        token = login.json["token"]
        headers = {"Authorization":f"Bearer {token}"}

        customer = self.client.post("/api/customers", json={
            "name":"Customer One","phone":"01800000003","sales":100000,"paid":0
        }, headers=headers)
        self.assertEqual(customer.status_code, 201)
        customer_id = customer.json["customer"]["id"]

        payment = self.client.post(f"/api/customers/{customer_id}/payments",
                                   json={"amount":25000,"payment_date":"2026-09-25"},
                                   headers=headers)
        self.assertEqual(payment.status_code, 201)

        view = self.client.get(f"/api/customers/{customer_id}", headers=headers)
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.json["customer"]["paid"], 25000)
        self.assertEqual(view.json["customer"]["due"], 75000)

    def test_customer_payment_crud_and_filters_are_central(self):
        response = self.client.post("/api/bootstrap-user",
            json={"username":"customer-crud","password":"pass-123","name":"Customer CRUD","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        self.assertEqual(response.status_code, 201)
        login = self.client.post("/api/login", json={"username":"customer-crud","password":"pass-123"})
        token = login.json["token"]
        headers = {"Authorization": f"Bearer {token}"}

        project = self.client.post("/api/projects", json={"name":"Customer Filter Project"}, headers=headers)
        self.assertEqual(project.status_code, 201)
        project_id = project.json["project"]["id"]

        customer = self.client.post("/api/customers", json={
            "name":"Central Customer","phone":"01811111111","sales":200000,
            "paid":10000,"project_id":project_id
        }, headers=headers)
        self.assertEqual(customer.status_code, 201)
        customer_id = customer.json["customer"]["id"]

        filtered = self.client.get(
            f"/api/customers?search=Central&filter=due&project_id={project_id}",
            headers=headers)
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(len(filtered.json["customers"]), 1)
        self.assertEqual(filtered.json["customers"][0]["project_name"], "Customer Filter Project")
        self.assertEqual(filtered.json["customers"][0]["due"], 190000)

        payment = self.client.post(f"/api/customers/{customer_id}/payments",
            json={"amount":25000,"payment_date":"2026-09-25","note":"Initial"},
            headers=headers)
        self.assertEqual(payment.status_code, 201)
        payment_id = payment.json["payment"]["id"]

        direct_payment = self.client.get(f"/api/payments/{payment_id}", headers=headers)
        self.assertEqual(direct_payment.status_code, 200)
        self.assertEqual(direct_payment.json["payment"]["customer_id"], customer_id)

        updated_payment = self.client.put(f"/api/payments/{payment_id}",
            json={"amount":30000,"payment_date":"2026-09-26","note":"Updated"},
            headers=headers)
        self.assertEqual(updated_payment.status_code, 200)

        detail = self.client.get(f"/api/customers/{customer_id}", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["customer"]["paid"], 40000)
        self.assertEqual(detail.json["customer"]["due"], 160000)
        self.assertEqual(len(detail.json["customer"]["payments"]), 1)
        self.assertEqual(detail.json["customer"]["payments"][0]["due_after_payment"], 170000)

        updated_customer = self.client.put(f"/api/customers/{customer_id}",
            json={"name":"Central Customer Updated","phone":"01811111111","sales":250000,
                  "paid":40000,"project_id":project_id},
            headers=headers)
        self.assertEqual(updated_customer.status_code, 200)
        self.assertEqual(updated_customer.json["customer"]["name"], "Central Customer Updated")

        deleted_payment = self.client.delete(f"/api/payments/{payment_id}", headers=headers)
        self.assertEqual(deleted_payment.status_code, 200)
        detail_after_payment = self.client.get(f"/api/customers/{customer_id}", headers=headers)
        self.assertEqual(detail_after_payment.json["customer"]["paid"], 10000)

        deleted_customer = self.client.delete(f"/api/customers/{customer_id}", headers=headers)
        self.assertEqual(deleted_customer.status_code, 200)
        self.assertEqual(self.client.get(f"/api/customers/{customer_id}", headers=headers).status_code, 404)

    def test_followup_crud_cancel_and_visibility(self):
        response = self.client.post("/api/bootstrap-user",
            json={"username":"followup-admin","password":"pass-123","name":"Followup Admin","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/bootstrap-user",
            json={"username":"followup-sales","password":"pass-123","name":"Followup Sales","role":"Sales"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        self.assertEqual(response.status_code, 201)

        admin_login = self.client.post("/api/login", json={"username":"followup-admin","password":"pass-123"})
        sales_login = self.client.post("/api/login", json={"username":"followup-sales","password":"pass-123"})
        admin_headers = {"Authorization": f"Bearer {admin_login.json['token']}"}
        sales_headers = {"Authorization": f"Bearer {sales_login.json['token']}"}

        project = self.client.post("/api/projects", json={"name":"Followup Project"}, headers=admin_headers)
        self.assertEqual(project.status_code, 201)
        project_id = project.json["project"]["id"]

        lead = self.client.post("/api/leads", json={
            "name":"Followup Lead","phone":"01611111111","project_id":project_id,
            "assigned_to":"followup-sales","follow_up_date":"2026-09-26"
        }, headers=admin_headers)
        self.assertEqual(lead.status_code, 201)
        lead_id = lead.json["lead"]["id"]

        created = self.client.post("/api/followups", json={
            "lead_id":lead_id,"name":"Followup Lead","phone":"01611111111",
            "follow_up_date":"2026-09-26","note":"Call tomorrow","status":"New"
        }, headers=sales_headers)
        self.assertEqual(created.status_code, 201)
        followup_id = created.json["followup"]["id"]

        visible = self.client.get("/api/followups", headers=sales_headers)
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(len(visible.json["followups"]), 1)

        updated = self.client.put(f"/api/followups/{followup_id}", json={
            "name":"Followup Lead Updated","phone":"01611111111",
            "follow_up_date":"2026-09-27","note":"Updated note","status":"New"
        }, headers=sales_headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["followup"]["follow_up_date"], "2026-09-27")

        detail = self.client.get(f"/api/followups/{followup_id}", headers=sales_headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["followup"]["note"], "Updated note")

        cancelled = self.client.put(f"/api/followups/{followup_id}", json={
            "name":"Followup Lead Updated","phone":"01611111111",
            "follow_up_date":"2026-09-27","note":"No longer interested","status":"Cancel"
        }, headers=sales_headers)
        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.json["cancelled"])

        self.assertEqual(self.client.get(f"/api/followups/{followup_id}", headers=sales_headers).status_code, 404)
        canceled = self.client.get("/api/canceled-leads", headers=sales_headers)
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(len(canceled.json["canceled_leads"]), 1)

        lead_after = self.client.get(f"/api/leads/{lead_id}", headers=sales_headers)
        self.assertEqual(lead_after.status_code, 200)
        self.assertIsNone(lead_after.json["lead"]["follow_up_date"])

    def test_expense_crud_is_central(self):
        response=self.client.post("/api/bootstrap-user",
            json={"username":"expense-admin","password":"pass-123","name":"Expense Admin","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        self.assertEqual(response.status_code,201)
        login=self.client.post("/api/login",json={"username":"expense-admin","password":"pass-123"})
        headers={"Authorization":f"Bearer {login.json['token']}"}

        created=self.client.post("/api/expenses",json={
            "amount":5000,"expense_date":"2026-09-25","category":"Office","note":"Test expense"
        },headers=headers)
        self.assertEqual(created.status_code,201)
        expense_id=created.json["expense"]["id"]

        detail=self.client.get(f"/api/expenses/{expense_id}",headers=headers)
        self.assertEqual(detail.status_code,200)
        self.assertEqual(detail.json["expense"]["category"],"Office")

        updated=self.client.put(f"/api/expenses/{expense_id}",json={
            "amount":7500,"expense_date":"2026-09-26","category":"Transport","note":"Updated"
        },headers=headers)
        self.assertEqual(updated.status_code,200)
        self.assertEqual(updated.json["expense"]["amount"],7500)

        listing=self.client.get("/api/expenses",headers=headers)
        self.assertEqual(listing.status_code,200)
        self.assertEqual(listing.json["total_expenses"],7500)

        deleted=self.client.delete(f"/api/expenses/{expense_id}",headers=headers)
        self.assertEqual(deleted.status_code,200)
        self.assertEqual(self.client.get(f"/api/expenses/{expense_id}",headers=headers).status_code,404)
        self.assertEqual(self.client.get("/api/expenses",headers=headers).json["total_expenses"],0)

    def test_dashboard_and_reports_are_central(self):
        response = self.client.post("/api/bootstrap-user",
            json={"username":"dashboard-admin","password":"pass-123","name":"Dashboard Admin","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"})
        self.assertEqual(response.status_code, 201)
        login = self.client.post("/api/login", json={"username":"dashboard-admin","password":"pass-123"})
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": f"Bearer {login.json['token']}"}

        project = self.client.post("/api/projects", json={"name":"Dashboard Project"}, headers=headers)
        self.assertEqual(project.status_code, 201)
        project_id = project.json["project"]["id"]

        customer = self.client.post("/api/customers", json={
            "name":"Dashboard Customer","phone":"01822222222","sales":100000,"paid":25000,
            "project_id":project_id
        }, headers=headers)
        self.assertEqual(customer.status_code, 201)
        customer_id = customer.json["customer"]["id"]

        lead = self.client.post("/api/leads", json={
            "name":"Dashboard Lead","phone":"01833333333","project_id":project_id,
            "visit_date":"2026-09-25","follow_up_date":"2026-09-25"
        }, headers=headers)
        self.assertEqual(lead.status_code, 201)

        followup = self.client.post("/api/followups", json={
            "lead_id":lead.json["lead"]["id"],"name":"Dashboard Lead","phone":"01833333333",
            "follow_up_date":"2026-09-25","note":"Today","status":"New"
        }, headers=headers)
        self.assertEqual(followup.status_code, 201)

        payment = self.client.post(f"/api/customers/{customer_id}/payments",
            json={"amount":25000,"payment_date":"2026-09-25","note":"Paid"},
            headers=headers)
        self.assertEqual(payment.status_code, 201)

        expense = self.client.post("/api/expenses",
            json={"amount":5000,"expense_date":"2026-09-25","category":"Office","note":"Expense"},
            headers=headers)
        self.assertEqual(expense.status_code, 201)

        dashboard = self.client.get("/api/dashboard", headers=headers)
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json["total_customers"], 1)
        self.assertEqual(dashboard.json["total_sales"], 100000)
        self.assertEqual(dashboard.json["today_followup_count"], 1)
        self.assertEqual(dashboard.json["visit_count"], 1)

        report = self.client.get("/api/reports?payment_date=2026-09-25&expense_date=2026-09-25", headers=headers)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json["total_sales"], 100000)
        self.assertEqual(report.json["total_paid"], 50000)
        self.assertEqual(report.json["total_due"], 50000)
        self.assertEqual(report.json["total_payments"], 25000)
        self.assertEqual(report.json["total_expenses"], 5000)
        self.assertEqual(len(report.json["date_payments"]), 1)
        self.assertEqual(len(report.json["date_expenses"]), 1)

    def test_core_unauthorized_access_is_blocked(self):

        self.assertEqual(self.client.get("/api/customers/1").status_code, 401)
        self.assertEqual(self.client.get("/api/expenses").status_code, 401)


    def test_project_files_are_central(self):
        bootstrap = self.client.post("/api/bootstrap-user", json={
            "username": "admin", "password": "1234", "name": "Admin", "role": "Admin"
        }, headers={"X-CRM-API-SECRET": "test-secret"})
        self.assertEqual(bootstrap.status_code, 201)
        login = self.client.post("/api/login", json={"username": "admin", "password": "1234"})
        self.assertEqual(login.status_code, 200)
        token = login.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        project = self.client.post(
            "/api/projects",
            json={"name": "File Project", "location": "Dhaka", "status": "Active"},
            headers=headers,
        )
        self.assertEqual(project.status_code, 201)
        project_id = project.get_json()["project"]["id"]

        text = self.client.post(
            f"/api/projects/{project_id}/files",
            json={"title": "Project Note", "item_type": "text", "text_content": "Central text"},
            headers=headers,
        )
        self.assertEqual(text.status_code, 201)
        text_id = text.get_json()["file"]["id"]

        import base64
        payload = base64.b64encode(b"central-pdf-bytes").decode("ascii")
        upload = self.client.post(
            f"/api/projects/{project_id}/files",
            json={
                "title": "Plan PDF",
                "item_type": "file",
                "file_name": "plan.pdf",
                "mime_type": "application/pdf",
                "data_base64": payload,
            },
            headers=headers,
        )
        self.assertEqual(upload.status_code, 201)
        file_id = upload.get_json()["file"]["id"]

        listing = self.client.get(f"/api/projects/{project_id}/files", headers=headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.get_json()["files"]), 2)

        fetched = self.client.get(f"/api/project-files/{file_id}", headers=headers)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_json()["file"]["data_base64"], payload)

        deleted = self.client.delete(f"/api/project-files/{text_id}", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        listing_after = self.client.get(f"/api/projects/{project_id}/files", headers=headers)
        self.assertEqual(len(listing_after.get_json()["files"]), 1)

if __name__ == "__main__":
    unittest.main()


    def test_receipt_settings_are_central_and_role_protected(self):
        admin_bootstrap = self.client.post(
            "/api/bootstrap-user",
            json={"username":"receipt-admin","password":"pass-123","name":"Receipt Admin","role":"Admin"},
            headers={"X-CRM-API-SECRET":"test-secret"},
        )
        self.assertEqual(admin_bootstrap.status_code, 201)
        sales_bootstrap = self.client.post(
            "/api/bootstrap-user",
            json={"username":"receipt-sales","password":"pass-123","name":"Receipt Sales","role":"Sales"},
            headers={"X-CRM-API-SECRET":"test-secret"},
        )
        self.assertEqual(sales_bootstrap.status_code, 201)

        admin_token = self.client.post(
            "/api/login", json={"username":"receipt-admin","password":"pass-123"}
        ).json["token"]
        sales_token = self.client.post(
            "/api/login", json={"username":"receipt-sales","password":"pass-123"}
        ).json["token"]

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        sales_headers = {"Authorization": f"Bearer {sales_token}"}

        initial = self.client.get("/api/receipt-settings", headers=sales_headers)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json["settings"]["header_name"], "ARKAM MC PARK")

        updated = self.client.put(
            "/api/receipt-settings",
            json={"header_name":"SWADESH BANGLA PROPERTY"},
            headers=admin_headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["settings"]["header_name"], "SWADESH BANGLA PROPERTY")

        shared = self.client.get("/api/receipt-settings", headers=sales_headers)
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(shared.json["settings"]["header_name"], "SWADESH BANGLA PROPERTY")

        denied = self.client.put(
            "/api/receipt-settings",
            json={"header_name":"Unauthorized Change"},
            headers=sales_headers,
        )
        self.assertEqual(denied.status_code, 403)

    def test_user_management_is_central(self):
        self._bootstrap("admin2", "adminpass", "Admin")
        login = self.client.post("/api/login", json={"username":"admin2","password":"adminpass"})
        token = login.get_json()["token"]
        headers={"Authorization":"Bearer "+token}
        created=self.client.post("/api/users", json={"username":"sales1","password":"secret","name":"Sales One","role":"Sales"}, headers=headers)
        self.assertEqual(created.status_code, 201)
        listed=self.client.get("/api/users", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["users"][0]["username"], "sales1")
        user_id=created.get_json()["user_id"]
        reset=self.client.put(f"/api/users/{user_id}/password", json={"password":"newsecret"}, headers=headers)
        self.assertEqual(reset.status_code, 200)
        sales_login=self.client.post("/api/login", json={"username":"sales1","password":"newsecret"})
        self.assertEqual(sales_login.status_code, 200)
        sales_token=sales_login.get_json()["token"]
        denied=self.client.get("/api/users", headers={"Authorization":"Bearer "+sales_token})
        self.assertEqual(denied.status_code, 403)
        changed=self.client.put("/api/me/password", json={"current_password":"newsecret","new_password":"finalsecret"}, headers={"Authorization":"Bearer "+sales_token})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/api/login", json={"username":"sales1","password":"finalsecret"}).status_code, 200)

    def test_lead_notes_and_reports_are_central(self):
        self._bootstrap("admin", "adminpass", "Admin")
        token=self.client.post("/api/login",json={"username":"admin","password":"adminpass"}).get_json()["token"]
        h={"Authorization":"Bearer "+token}
        p=self.client.post("/api/projects",json={"name":"Report Project"},headers=h).get_json()["project"]
        lead=self.client.post("/api/leads",json={"name":"Report Lead","phone":"01700000001","project_id":p["id"],"assigned_to":None,"visit_date":"2026-09-25"},headers=h).get_json()["lead"]
        n=self.client.post(f"/api/lead-notes/{lead['id']}",json={"note":"First call","note_date":"2026-09-25"},headers=h)
        self.assertEqual(n.status_code,201)
        notes=self.client.get(f"/api/lead-notes/{lead['id']}",headers=h)
        self.assertEqual(notes.status_code,200); self.assertEqual(len(notes.get_json()["notes"]),1)
        daily=self.client.get("/api/reports/daily?date=2026-09-25",headers=h)
        self.assertEqual(daily.status_code,200); self.assertEqual(daily.get_json()["talked_count"],1)
        monthly=self.client.get("/api/reports/monthly",headers=h)
        self.assertEqual(monthly.status_code,200)
