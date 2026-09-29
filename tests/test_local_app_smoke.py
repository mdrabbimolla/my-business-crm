import os
import tempfile
import unittest


class LocalAppSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(cls.tmpdir.name)
        import database
        database.DATABASE = os.path.join(cls.tmpdir.name, "database.db")
        import app
        cls.app = app.app
        cls.app.config.update(TESTING=True, SECRET_KEY="smoke-test-secret")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_login_page(self):
        response = self.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My Business CRM", response.data)

    def test_local_login_and_dashboard(self):
        client = self.app.test_client()
        response = client.post("/", data={"username": "admin", "password": "1234"}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        response = client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My Business CRM", response.data)


    def test_legacy_database_user_migration_and_add_user(self):
        import sqlite3
        import database
        legacy_path = os.path.join(self.tmpdir.name, "legacy.db")
        conn = sqlite3.connect(legacy_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)")
        conn.execute("INSERT INTO users(username,password) VALUES('admin','1234')")
        conn.commit(); conn.close()
        database.DATABASE = legacy_path
        database.init_db()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"
            session["auth_mode"] = "local"
        response = client.post("/add-user", data={
            "name": "Legacy Smoke User", "username": "legacy_user",
            "password": "legacy123", "role": "Sales", "active": 1,
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.data[:1000])
        self.assertEqual(response.headers["Location"], "/users")
        response = client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"legacy_user", response.data)
        database.DATABASE = os.path.join(self.tmpdir.name, "database.db")
        database.init_db()

    def test_local_add_user(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"
            session["auth_mode"] = "local"
        response = client.post("/add-user", data={
            "name": "Smoke User",
            "username": "smoke_user",
            "password": "smoke123",
            "role": "Sales",
            "active": "1",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.data[:1000])
        self.assertEqual(response.headers["Location"], "/users")
        response = client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"smoke_user", response.data)

    def test_cloud_add_user_route(self):
        from unittest.mock import Mock, patch

        client = self.app.test_client()
        fake_cloud = Mock()
        fake_cloud.enabled = True
        fake_cloud.me.return_value = {"user": {"role": "Admin"}}
        fake_cloud.create_user.return_value = {"ok": True, "user_id": 99}

        with client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"
            session["auth_mode"] = "cloud"
            session["cloud_token"] = "test-token"

        with patch("app._cloud_client", return_value=fake_cloud):
            response = client.post("/add-user", data={
                "name": "Cloud Smoke User",
                "username": "cloud_smoke_user",
                "password": "cloud123",
                "role": "Sales",
                "active": 1,
            }, follow_redirects=False)

        self.assertEqual(response.status_code, 302, response.data[:1000])
        self.assertEqual(response.headers["Location"], "/users")
        fake_cloud.create_user.assert_called_once_with({
            "name": "Cloud Smoke User",
            "username": "cloud_smoke_user",
            "password": "cloud123",
            "role": "Sales",
            "active": "1",
        })

    def test_core_authenticated_pages(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"
            session["auth_mode"] = "local"
        for path in ("/customers", "/leads", "/projects", "/followups", "/reports"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.data[:500])


if __name__ == "__main__":
    unittest.main()
