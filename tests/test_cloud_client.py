import json
import unittest
from unittest.mock import patch

from cloud_client import CloudAPIError, CloudCRMClient


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CloudClientTests(unittest.TestCase):
    def test_disabled_without_base_url(self):
        client = CloudCRMClient(base_url="")
        self.assertFalse(client.enabled)
        with self.assertRaises(CloudAPIError):
            client.health()

    @patch("cloud_client.urllib.request.urlopen")
    def test_login_sends_json_and_returns_response(self, urlopen):
        urlopen.return_value = FakeResponse({
            "ok": True,
            "token": "abc123",
            "user": {"username": "sales1", "role": "Sales"},
        })

        client = CloudCRMClient(base_url="https://crm.example.test")
        result = client.login("sales1", "secret")

        self.assertEqual(result["token"], "abc123")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://crm.example.test/api/login")
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"username": "sales1", "password": "secret"},
        )

    @patch("cloud_client.urllib.request.urlopen")
    def test_authenticated_request_sends_bearer_token(self, urlopen):
        urlopen.return_value = FakeResponse({"ok": True, "user": {"username": "sales1"}})

        client = CloudCRMClient(base_url="https://crm.example.test", token="token-xyz")
        result = client.me()

        self.assertTrue(result["ok"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer token-xyz")
        self.assertEqual(request.full_url, "https://crm.example.test/api/me")

    @patch("cloud_client.urllib.request.urlopen")
    def test_projects_returns_project_list(self, urlopen):
        urlopen.return_value = FakeResponse({
            "projects": [{"id": 1, "name": "Shared Project"}]
        })

        client = CloudCRMClient(base_url="https://crm.example.test", token="t")
        projects = client.projects()

        self.assertEqual(projects[0]["name"], "Shared Project")

    @patch("cloud_client.urllib.request.urlopen")
    def test_http_error_becomes_cloud_api_error(self, urlopen):
        from urllib.error import HTTPError
        from io import BytesIO

        urlopen.side_effect = HTTPError(
            "https://crm.example.test/api/me",
            401,
            "Unauthorized",
            {},
            BytesIO(b'{"error":"Invalid or expired session"}'),
        )

        client = CloudCRMClient(base_url="https://crm.example.test", token="bad")
        with self.assertRaises(CloudAPIError) as ctx:
            client.me()

        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(ctx.exception.payload["error"], "Invalid or expired session")


if __name__ == "__main__":
    unittest.main()
