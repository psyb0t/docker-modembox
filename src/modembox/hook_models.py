import json
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hook_http import allowed_address
from .sms import PHONE

Trigger = Literal["sms.received", "call.incoming", "call.ended"]
EventField = Literal[
    "id", "type", "modem", "time", "peer", "text", "outcome", "callerId", "messageId", "callId"
]
EVENT_FIELDS = set(EventField.__args__)
TEMPLATE = re.compile(r"\{\{([a-zA-Z]+)\}\}")
HEADER = re.compile(r"[-!#$%&'*+.^_|~0-9A-Za-z]+")
RESERVED = {
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "upgrade",
    "proxy-authorization",
    "proxy-connection",
    "x-modembox-delivery",
    "x-modembox-signature",
}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(Input):
    field: Literal["peer", "text", "outcome", "callerId"]
    op: Literal["equals", "contains", "startsWith", "in"]
    values: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        min_length=1, max_length=32
    )
    caseSensitive: bool = False

    @model_validator(mode="after")
    def check_values(self):
        if self.op != "in" and len(self.values) != 1:
            raise ValueError("This condition takes one value")
        return self


class SendSms(Input):
    type: Literal["sms"]
    recipient: str | None = Field(default=None, pattern=PHONE.pattern)
    modem: str | None = Field(default=None, max_length=120)
    template: str = Field(default="{{text}}", min_length=1, max_length=18000)


class Webhook(Input):
    type: Literal["http"]
    destination: int = Field(gt=0)
    fields: list[EventField] | None = Field(default=None, min_length=1, max_length=10)
    jsonBody: dict[str, str] | None = Field(default=None, max_length=32)
    textBody: str | None = Field(default=None, max_length=18000)

    @model_validator(mode="after")
    def check_body(self):
        if sum(value is not None for value in (self.fields, self.jsonBody, self.textBody)) > 1:
            raise ValueError("Select one HTTP body format")
        if self.jsonBody is not None and len(json.dumps(self.jsonBody)) > 18000:
            raise ValueError("HTTP JSON template is too large")
        return self


class Decline(Input):
    type: Literal["decline"]


Action = Annotated[SendSms | Webhook | Decline, Field(discriminator="type")]


class Rule(Input):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    trigger: Trigger
    modem: str | None = Field(default=None, max_length=120)
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list, max_length=16)
    actions: list[Action] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_actions(self):
        for condition in self.conditions:
            if condition.field == "text" and self.trigger != "sms.received":
                raise ValueError("Text conditions require an SMS trigger")
            if condition.field == "outcome" and self.trigger != "call.ended":
                raise ValueError("Outcome conditions require the call-ended trigger")
        for action in self.actions:
            if isinstance(action, Decline) and self.trigger != "call.incoming":
                raise ValueError("Decline requires the incoming call trigger")
            templates = []
            if isinstance(action, SendSms):
                templates = [action.template]
            elif isinstance(action, Webhook):
                templates = [action.textBody or "", *(action.jsonBody or {}).values()]
            if any(set(TEMPLATE.findall(template)) - EVENT_FIELDS for template in templates):
                raise ValueError("Unknown template field")
        return self


class Destination(Input):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    url: str = Field(min_length=8, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] | None = Field(default=None, max_length=16)
    signingKey: str | None = Field(default=None, max_length=512)
    privateAddress: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def check_transport(self):
        try:
            target = urlsplit(self.url)
            if (
                re.search(r"[^\x21-\x7e]", self.url)
                or "\\" in self.url
                or target.scheme not in {"http", "https"}
                or not target.hostname
                or target.username is not None
                or target.password is not None
                or target.fragment
                or target.hostname.lower().rstrip(".") == "localhost"
                or target.port == 0
            ):
                raise ValueError
            if self.privateAddress and not allowed_address(
                self.privateAddress, self.privateAddress
            ):
                raise ValueError
            if re.fullmatch(r"[0-9.]+", target.hostname) or ":" in target.hostname:
                if not allowed_address(target.hostname, self.privateAddress):
                    raise ValueError
        except ValueError as error:
            raise ValueError("Invalid or prohibited HTTP destination") from error
        for name, value in (self.headers or {}).items():
            if (
                not HEADER.fullmatch(name)
                or name.lower() in RESERVED
                or len(name) > 100
                or len(value) > 2048
                or re.search(r"[^\x20-\x7e]", value)
            ):
                raise ValueError("Invalid or reserved HTTP header")
        return self


class Preview(Input):
    rule: Rule
    messageId: int | None = Field(default=None, gt=0)
    callId: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def check_source(self):
        if (self.messageId is None) == (self.callId is None):
            raise ValueError("Select one source record")
        return self


def normalized_peer(peer: str) -> str:
    number = re.sub(r"[\s().-]", "", peer)
    return number if re.fullmatch(r"\+?[0-9]+", number) else peer.strip()


def matches(rule: Rule, event: dict) -> bool:
    if rule.trigger != event["type"] or (rule.modem and rule.modem != event["modem"]):
        return False
    if not rule.conditions:
        return True
    results = (matches_condition(condition, event) for condition in rule.conditions)
    return all(results) if rule.match == "all" else any(results)


def matches_condition(condition: Condition, event: dict) -> bool:
    value = event.get(condition.field)
    if value is None:
        return False
    candidates = condition.values
    if condition.field == "peer":
        value = normalized_peer(value)
        candidates = [normalized_peer(item) for item in candidates]
    if not condition.caseSensitive:
        value = value.casefold()
        candidates = [item.casefold() for item in candidates]
    match condition.op:
        case "contains":
            return candidates[0] in value
        case "startsWith":
            return value.startswith(candidates[0])
        case _:
            return value in candidates


def render(template: str, event: dict) -> str:
    return TEMPLATE.sub(lambda match: str(event.get(match[1]) or ""), template)
