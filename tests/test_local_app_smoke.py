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
