import json
from pathlib import Path
from shlex import quote
from tempfile import TemporaryDirectory
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from .hook_models import Rule
from .media import encode_clip

DEMO_NUMBER = "+12025550123"
DEMO_RULE = {
    "name": "Forward incoming SMS",
    "enabled": False,
    "trigger": "sms.received",
    "actions": [{"type": "sms", "recipient": DEMO_NUMBER}],
}
REQUEST_EXAMPLES = {
    "Phone": {"number": DEMO_NUMBER},
    "Sms": {"number": DEMO_NUMBER, "text": "Example message"},
    "Pin": {"pin": "0000", "remember": False},
    "Clip": {"wav": encode_clip(bytes(2))},
    "Command": {"command": "AT+CSQ"},
    "Rule": DEMO_RULE,
    "Destination": {"name": "Example webhook", "url": "https://example.invalid/hooks/modembox"},
    "Preview": {"rule": DEMO_RULE, "messageId": 1},
}


class ApiError(BaseModel):
    code: str
    message: str


class Health(BaseModel):
    ok: Literal[True]


class AuthMode(BaseModel):
    tokenRequired: bool


class ModemState(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    connected: bool
    calls: list[dict[str, str]]
    capabilities: dict[str, bool | str]


class MessagePart(BaseModel):
    part: int
    status: str


class Message(BaseModel):
    id: int
    modem: str
    direction: str
    peer: str
    body: str
    status: str
    created: str = Field(description="UTC timestamp.")
    parts: list[MessagePart] | None = Field(
        default=None, description="Present on multipart outgoing message details."
    )


class MessagePage(BaseModel):
    items: list[Message]
    next: int | None = Field(description="Cursor for the next page, or null on the last page.")


class HookPage(BaseModel):
    items: list[dict[str, Any]] = Field(description="Records of the requested hook resource.")
    limit: int
    offset: int
    hasMore: bool


class HookRule(Rule):
    id: int


class HookDestination(BaseModel):
    id: int
    name: str
    enabled: bool
    url: str
    method: str
    privateAddress: str | None
    hasHeaders: bool
    hasSigningKey: bool


class HookDeletion(BaseModel):
    id: int
    deleted: Literal[True]


class HookPreview(BaseModel):
    matches: bool
    actions: list[dict[str, Any]] = Field(
        description="Actions that would run for the source record."
    )


class GatewayState(BaseModel):
    modems: list[ModemState]
    messages: list[Message]
    activity: list[dict[str, Any]]
    hooks: dict[str, list[dict[str, Any]]]


class AudioCapture(BaseModel):
    wav: str
    format: str


class AtReply(BaseModel):
    lines: list[str]
    terminal: str


def success(model: Any, status: int = 200) -> dict:
    return {status: {"model": model}}


def error_response(description: str) -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}},
    }


def curl_sample(path: str, method: str, operation: dict) -> dict:
    for parameter in operation.get("parameters", []):
        if parameter["in"] != "path":
            continue
        name = parameter["name"]
        schema = parameter["schema"]
        value = schema.get("enum", [1])[0]
        if name == "modem_id":
            value = "${MODEMBOX_MODEM_ID}"
        path = path.replace("{" + name + "}", str(value))
    if path == "/v1/messages":
        path += "?limit=10"
    lines = [f'curl --request {method.upper()} "${{MODEMBOX_URL:-http://localhost:8000}}{path}"']
    if operation.get("security"):
        lines.append('  --header "Authorization: Bearer ${MODEMBOX_TOKEN}"')
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if content:
        model = content["schema"].get("$ref", "").rsplit("/", 1)[-1]
        example = REQUEST_EXAMPLES.get(model)
        data = "@request.json"
        if example is not None:
            content["example"] = example
            data = quote(json.dumps(example, indent=2))
        lines.extend(["  --header 'Content-Type: application/json'", "  --data " + data])
    elif method == "post":
        lines.append("  --data ''")
    continuation = " \\" + "\n"
    return {"lang": "curl", "label": "curl", "source": continuation.join(lines)}


class ModemBoxAPI(FastAPI):
    def openapi(self):
        if self.openapi_schema is not None:
            return self.openapi_schema

        schema = super().openapi()
        schema["components"]["schemas"]["ApiError"] = ApiError.model_json_schema()
        schema["components"]["securitySchemes"] = {
            "operatorToken": {
                "type": "http",
                "scheme": "bearer",
                "description": "Use the operator token unless the server disables authentication.",
            }
        }
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if path.startswith("/v1/"):
                    operation["security"] = [{"operatorToken": []}]
                    responses = operation["responses"]
                    responses.setdefault("401", error_response("Missing or invalid bearer token."))
                    responses.setdefault("403", error_response("Origin or operation forbidden."))
                    if method in {"post", "put"}:
                        responses["413"] = error_response(
                            "Missing or invalid body length, or body too large."
                        )
                    if "422" in responses:
                        responses["422"] = error_response("Invalid request parameters or body.")
                operation["x-codeSamples"] = [curl_sample(path, method, operation)]
        schema["components"]["schemas"].pop("HTTPValidationError", None)
        schema["components"]["schemas"].pop("ValidationError", None)
        return schema


def main():
    from .config import Config
    from .server import create_app
    from .service import Service

    with TemporaryDirectory() as directory:
        data = Path(directory)
        config = Config(token="", data=data, web=data / "web")
        service = Service(config)
        try:
            print(json.dumps(create_app(config, service=service).openapi(), indent=2))
        finally:
            service.store.db.close()


if __name__ == "__main__":
    main()
