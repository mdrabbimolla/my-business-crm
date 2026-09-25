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
        bootstrap = self.client.post(
            "/api/bootstrap-user",
            json={"username": "sales1", "password": "pass-123", "name": "Sales One", "role": "Sales"},
            headers={"X-CRM-API-SECRET": "test-secret"},
        )
        self.assertEqual(bootstrap.status_code, 201)
        login = self.client.post("/api/login", json={"username": "sales1", "password": "pass-123"})
        self.assertEqual(login.status_code, 200)
        token = login.json["token"]
        me = self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json["user"]["username"], "sales1")
        self.assertEqual(me.json["user"]["role"], "Sales")

    def test_wrong_password_rejected(self):
        response = self.client.post("/api/login", json={"username": "sales1", "password": "wrong"})
        self.assertEqual(response.status_code, 401)

if __name__ == "__main__":
    unittest.main()
