from pathlib import Path

from fastapi.testclient import TestClient

from modembox.config import Config
from modembox.server import create_app
from modembox.service import Service
from test_gateway import HEADERS, TOKEN


def test_messages_cursor_excludes_other_modems_and_walks_each_page(tmp_path):
    config = Config(TOKEN, tmp_path, Path("web/build"))
    service = Service(config)
    expected = []
    for index in range(7):
        message = service.store.add_message(
            "target", "incoming", "+1234", f"Example {index}", "received"
        )
        expected.insert(0, message["id"])
        service.store.add_message("other", "incoming", "+1256", "Other", "received")
    with TestClient(create_app(config, service=service)) as client:
        cursor = None
        seen = []
        for count in (3, 3, 1):
            params = {"modem": "target", "limit": 3}
            if cursor is not None:
                params["before"] = cursor
            response = client.get("/v1/messages", params=params, headers=HEADERS)
            assert response.status_code == 200
            page = response.json()
            assert len(page["items"]) == count
            assert all(message["modem"] == "target" for message in page["items"])
            seen.extend(message["id"] for message in page["items"])
            cursor = page["next"]
        assert cursor is None
        assert seen == expected
        empty = client.get(
            "/v1/messages", params={"modem": "target", "before": seen[-1]}, headers=HEADERS
        )
        assert empty.json() == {"items": [], "next": None}
        invalid = client.get("/v1/messages?limit=0", headers=HEADERS)
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "INVALID_REQUEST"
