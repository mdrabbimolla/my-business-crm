import os
import sys
import tempfile
import importlib


def load_app():
    db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_file.close()

    import database
    database.DATABASE = db_file.name

    if "app" in sys.modules:
        del sys.modules["app"]

    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True)
    return app_module, db_file.name


def test_login_page_and_local_login():
    app_module, db_file = load_app()
    try:
        client = app_module.app.test_client()

        response = client.get("/")
        assert response.status_code == 200
        assert b"My Business CRM" in response.data

        response = client.post(
            "/",
            data={"username": "admin", "password": "1234"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")

        response = client.get("/dashboard")
        assert response.status_code == 200

        # Logout must invalidate the authenticated session and prevent the
        # Android/WebView from replaying a cached dashboard.
        response = client.get("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["Location"].endswith("/")
        assert "session=" in response.headers.get("Set-Cookie", "")
        assert "no-store" in response.headers.get("Cache-Control", "")

        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

        # Re-authenticate after the logout regression check before testing
        # admin-only user management routes.
        response = client.post(
            "/",
            data={"username": "admin", "password": "1234"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")

        print("ROUTES:", sorted(rule.rule for rule in app_module.app.url_map.iter_rules()))
        response = client.get("/users")
        assert response.status_code == 200
        assert b"CRM Users" in response.data

        response = client.post(
            "/add-user",
            data={"name": "Test Sales", "username": "testsales", "password": "test123", "role": "Sales", "active": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/users")

        response = client.get("/users")
        assert response.status_code == 200
        assert b"testsales" in response.data

        # Admin password reset must actually change the stored credential.
        reset_page = client.get("/reset-password/2")
        assert reset_page.status_code == 200
        assert b"testsales" in reset_page.data

        response = client.post(
            "/reset-password/2",
            data={"new_password": "reset456", "confirm_password": "reset456"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/users")

        # Verify the reset password works for the affected user.
        with client.session_transaction() as sess:
            sess.clear()
        response = client.post(
            "/",
            data={"username": "testsales", "password": "reset456"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
    finally:
        try:
            os.remove(db_file)
        except OSError:
            pass
