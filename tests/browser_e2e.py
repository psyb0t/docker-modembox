import time

import httpx

from test_gateway import TOKEN

BROWSER_URL = "http://127.0.0.1:8080"
APP_URL = "http://127.0.0.1:8000"
BROWSER_TOKEN = "EXAMPLE-DO-NOT-USE-browser"
NUMBER = "+1234567890"
MESSAGE = "Example SMS sent from the browser"


class Browser:
    def __init__(self, client):
        self.client = client

    def action(self, action, **arguments):
        response = self.client.post("/", json={"action": action, **arguments})
        response.raise_for_status()
        result = response.json()
        assert result["success"], result
        return result["data"]

    def wait(self, *needles):
        deadline = time.monotonic() + 8
        text = ""
        while time.monotonic() < deadline:
            text = self.action("get_text")["text"]
            if all(needle in text for needle in needles):
                return text
            time.sleep(0.2)
        raise AssertionError(f"Could not find {needles!r} in {text!r}")

    def click(self, title):
        self.action("click", selector=f'xpath=//button[normalize-space(.)="{title}"]')

    def fill(self, selector, text):
        self.action("fill", selector=selector, value=text)

    def check_page(self):
        assert self.action("detect_challenge")["status"] == "absent"


def test_static_console_sms_and_call():
    with (
        httpx.Client(
            base_url=BROWSER_URL, timeout=20, headers={"Authorization": "Bearer " + BROWSER_TOKEN}
        ) as client,
        httpx.Client(
            base_url=APP_URL, timeout=5, headers={"Authorization": "Bearer " + TOKEN}
        ) as gateway,
    ):
        deadline = time.monotonic() + 75
        while True:
            try:
                if client.get("/health").is_success:
                    break
            except httpx.TransportError:
                if time.monotonic() >= deadline:
                    raise
            assert time.monotonic() < deadline, "Browser failed to become ready"
            time.sleep(0.5)
        browser = Browser(client)
        browser.action("goto", url=APP_URL)
        browser.check_page()
        browser.wait("Connect to ModemBox")
        browser.fill(".login input", "EXAMPLE-DO-NOT-USE-wrong")
        browser.click("Connect")
        browser.check_page()
        rejected = browser.wait("Token rejected")
        assert "Example received text" not in rejected

        browser.fill(".login input", TOKEN)
        browser.click("Connect")
        browser.check_page()
        initial = browser.wait(
            "E173", "Example received text", "Example sent text", "Live activity", "Joined message"
        )
        assert "Example archived text" not in initial
        browser.click("All modems")
        browser.wait("Example archived text", "Selected modem")
        browser.click("Selected modem")
        assert "Example archived text" not in browser.wait("All modems", "Example received text")

        browser.fill(".compose input[type=tel]", NUMBER)
        browser.fill(".compose textarea", MESSAGE)
        browser.click("Send SMS")
        browser.check_page()
        browser.wait(MESSAGE, "http send sms ok")
        messages = gateway.get("/v1/messages").json()["items"]
        assert any(item["body"] == MESSAGE and item["status"] == "submitted" for item in messages)

        browser.fill("form:not(.compose) input[type=tel]", NUMBER)
        browser.click("Dial")
        browser.check_page()
        browser.wait(NUMBER, "active", "Hang up", "http dial ok")
        assert gateway.get("/v1/modems").json()[0]["calls"][0]["state"] == "active"

        browser.click("Hang up")
        browser.check_page()
        browser.wait("Idle", "http hangup ok")
        assert gateway.get("/v1/modems").json()[0]["calls"] == []

        browser.action("send_key", key="ctrl+r")
        browser.check_page()
        browser.wait("Idle", MESSAGE, "http hangup ok")

        browser.click("Hooks")
        browser.wait("Hooks", "No rules yet.")
        browser.click("New rule")
        browser.wait("Rule name", "SMS template", "Dry run")
        browser.fill('section[aria-label="Hooks"] form input[required]', "Browser forwarding rule")
        browser.click("Save rule")
        browser.wait("Browser forwarding rule", "Enabled")
        assert (
            gateway.get("/v1/hooks/rules").json()["items"][0]["name"] == "Browser forwarding rule"
        )

        response = gateway.post(
            "/v1/hooks/rules",
            json={
                "name": "API call rule",
                "trigger": "call.incoming",
                "actions": [{"type": "decline"}],
            },
        )
        assert response.status_code == 201
        browser.wait("API call rule", "Browser forwarding rule")
        browser.action("send_key", key="ctrl+r")
        browser.wait("Idle", "Live")
        browser.click("Hooks")
        browser.wait("API call rule", "Browser forwarding rule")
