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
    finally:
        try:
            os.remove(db_file)
        except OSError:
            pass
