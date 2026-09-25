import json
import os
import urllib.error
import urllib.request


class CloudAPIError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class CloudCRMClient:
    """Small JSON client used by the local Flask UI to reach the central CRM API."""

    def __init__(self, base_url=None, token=None, timeout=10):
        base_url = base_url if base_url is not None else os.environ.get("CRM_CLOUD_API_URL", "")
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.base_url)

    def _request(self, method, path, payload=None):
        if not self.base_url:
            raise CloudAPIError("Central CRM API URL is not configured")

        url = self.base_url + "/" + path.lstrip("/")
        headers = {
            "Accept": "application/json",
            "User-Agent": "My-Business-CRM",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=_https_context()) as response:
                raw = response.read().decode("utf-8")
                try:
                    return json.loads(raw) if raw else {}
                except json.JSONDecodeError as exc:
                    raise CloudAPIError("Central API returned invalid JSON", response.status) from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload_data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload_data = {"error": raw or "HTTP error"}
            raise CloudAPIError(
                payload_data.get("error") or "Central API request failed",
                exc.code,
                payload_data,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CloudAPIError("Central CRM API is unreachable") from exc

    def health(self):
        return self._request("GET", "/api/health")

    def login(self, username, password):
        return self._request(
            "POST",
            "/api/login",
            {"username": username, "password": password},
        )

    def me(self):
        return self._request("GET", "/api/me")

    def projects(self):
        return self._request("GET", "/api/projects").get("projects", [])

    def create_project(self, data):
        return self._request("POST", "/api/projects", data)

    def update_project(self, project_id, data):
        return self._request("PUT", f"/api/projects/{int(project_id)}", data)

    def delete_project(self, project_id):
        return self._request("DELETE", f"/api/projects/{int(project_id)}")

    def leads(self, params=None):
        path = "/api/leads"
        if params:
            from urllib.parse import urlencode
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean: path += "?" + urlencode(clean)
        return self._request("GET", path).get("leads", [])

    def delete_lead(self, lead_id):
        return self._request("DELETE", f"/api/leads/{int(lead_id)}")

    def create_lead(self, data):
        return self._request("POST", "/api/leads", data)

    def get_lead(self, lead_id):
        return self._request("GET", f"/api/leads/{int(lead_id)}")

    def update_lead(self, lead_id, data):
        return self._request("PUT", f"/api/leads/{int(lead_id)}", data)

    def add_lead_note(self, lead_id, note, note_date=None):
        payload = {"note": note}
        if note_date:
            payload["note_date"] = note_date
        return self._request("POST", f"/api/leads/{int(lead_id)}/notes", payload)

    def followups(self):
        return self._request("GET", "/api/followups").get("followups", [])

    def create_followup(self, data):
        return self._request("POST", "/api/followups", data)

    def update_followup(self, followup_id, data):
        return self._request("PUT", f"/api/followups/{int(followup_id)}", data)

    def delete_followup(self, followup_id):
        return self._request("DELETE", f"/api/followups/{int(followup_id)}")

    def canceled_leads(self):
        return self._request("GET", "/api/canceled-leads").get("canceled_leads", [])

    def customers(self, params=None):
        path = "/api/customers"
        if params:
            from urllib.parse import urlencode
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                path += "?" + urlencode(clean)
        return self._request("GET", path).get("customers", [])

    def get_customer(self, customer_id):
        return self._request("GET", f"/api/customers/{int(customer_id)}")

    def create_customer(self, data):
        return self._request("POST", "/api/customers", data)

    def update_customer(self, customer_id, data):
        return self._request("PUT", f"/api/customers/{int(customer_id)}", data)

    def delete_customer(self, customer_id):
        return self._request("DELETE", f"/api/customers/{int(customer_id)}")

    def create_payment(self, customer_id, data):
        return self._request("POST", f"/api/customers/{int(customer_id)}/payments", data)

    def get_payment(self, payment_id):
        return self._request("GET", f"/api/payments/{int(payment_id)}")

    def update_payment(self, payment_id, data):
        return self._request("PUT", f"/api/payments/{int(payment_id)}", data)

    def delete_payment(self, payment_id):
        return self._request("DELETE", f"/api/payments/{int(payment_id)}")

    def expenses(self):
        return self._request("GET", "/api/expenses")

    def create_expense(self, data):
        return self._request("POST", "/api/expenses", data)


def _https_context():
    # Keep certificate verification enabled for production HTTPS.
    import ssl
    return ssl.create_default_context()
