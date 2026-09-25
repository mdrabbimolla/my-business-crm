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

        updated_payment = self.client.put(f"/api/payments/{payment_id}",
            json={"amount":30000,"payment_date":"2026-09-26","note":"Updated"},
            headers=headers)
        self.assertEqual(updated_payment.status_code, 200)

        detail = self.client.get(f"/api/customers/{customer_id}", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["customer"]["paid"], 40000)
        self.assertEqual(detail.json["customer"]["due"], 160000)

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

    def test_core_unauthorized_access_is_blocked(self):
        self.assertEqual(self.client.get("/api/customers/1").status_code, 401)
        self.assertEqual(self.client.get("/api/expenses").status_code, 401)

if __name__ == "__main__":
    unittest.main()
