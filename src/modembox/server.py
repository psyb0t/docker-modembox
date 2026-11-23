import asyncio
import hmac
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection

from .api_docs import (
    AtReply,
    AudioCapture,
    AuthMode,
    GatewayState,
    Health,
    HookDeletion,
    HookDestination,
    HookPage,
    HookPreview,
    HookRule,
    Message,
    MessagePage,
    ModemBoxAPI,
    ModemState,
    error_response,
    success,
)
from .config import Config
from .errors import OperationError
from .hook_models import Destination, Preview, Rule
from .mcp_tools import build_mcp
from .media import FRAME_BYTES
from .service import Service
from .sms import MAX_SMS_CHARACTERS

MAX_BODY = 1_048_576
QUEUE_SIZE = 64

ModemID = Annotated[str, Path(description="Modem ID returned by GET /v1/modems.")]
HookID = Annotated[int, Path(description="Numeric rule or destination ID.")]
PageLimit = Annotated[int, Query(ge=1, le=200, description="Maximum records to return.")]
MODEM_RESPONSES = {
    404: error_response("Modem not found."),
    409: error_response("The modem or call is in a state that prevents this operation."),
    503: error_response("The modem is offline or failed to complete the command."),
}
MODEM_STATE_RESPONSES = MODEM_RESPONSES | success(ModemState)
HOOK_NOT_FOUND = {404: error_response("Hook, destination, or source record not found.")}
HOOK_LIMIT = {409: error_response("Hook configuration limit reached.")}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Phone(Input):
    number: str = Field(
        pattern=r"^\+?[0-9]{3,20}$", description="Recipient or dialed phone number."
    )


class Sms(Phone):
    text: str = Field(min_length=1, max_length=MAX_SMS_CHARACTERS, description="SMS text.")


class Pin(Input):
    pin: str = Field(pattern=r"^[0-9]{4,8}$", description="Current SIM PIN.")
    remember: bool = Field(default=False, description="Save the PIN after a successful unlock.")


class Clip(Input):
    wav: str = Field(
        max_length=650_000, description="Base64 mono 8 kHz PCM16 WAV, up to 30 seconds."
    )


class Command(Input):
    command: str = Field(
        min_length=2, max_length=128, description="One AT command without line breaks."
    )


def matches(presented: str, expected: str) -> bool:
    return presented.isascii() and hmac.compare_digest(presented.encode(), expected.encode())


def same_origin(connection: HTTPConnection) -> bool:
    origin = connection.headers.get("origin")
    if origin is None:
        return True
    scheme = "https" if connection.url.scheme in {"https", "wss"} else "http"
    return origin == f"{scheme}://{connection.url.netloc}"


async def relay(send, receive):
    tasks = {asyncio.create_task(send()), asyncio.create_task(receive())}
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(config: Config | None = None, *, service: Service | None = None):
    config = config or Config.from_env()
    gateway = service or Service(config)
    mcp_app = build_mcp(gateway).http_app(path="/", stateless_http=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with gateway.lifecycle(), mcp_app.lifespan(mcp_app):
            yield

    app = ModemBoxAPI(
        title="ModemBox",
        version="1.0.0",
        description=(
            "Manage USB modems, SMS, calls, and hooks. Protected routes use bearer auth "
            "by default. Omit the Authorization header when MODEMBOX_DISABLE_AUTH=true."
        ),
        servers=[{"url": "http://localhost:8000", "description": "Default local installation"}],
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        path = request.url.path
        if path == "/mcp" or path.startswith(("/mcp/", "/v1/")):
            supplied = request.headers.get("authorization", "")
            if config.disable_auth and not same_origin(request):
                return JSONResponse({"code": "FORBIDDEN", "message": "Origin rejected"}, 403)
            if not config.disable_auth and not matches(supplied, "Bearer " + config.token):
                return JSONResponse(
                    {"code": "UNAUTHORIZED", "message": "Bearer token required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if request.method in {"POST", "PUT", "PATCH"}:
                length = request.headers.get("content-length", "")
                if not length.isdecimal() or len(length) > 10 or int(length) > MAX_BODY:
                    return JSONResponse(
                        {"code": "BODY_LIMIT", "message": "Request body too large"}, status_code=413
                    )
            if path == "/mcp":
                request.scope["path"] = "/mcp/"
                request.scope["raw_path"] = b"/mcp/"
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if path.startswith(("/v1/", "/mcp")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(OperationError)
    async def operation_error(_request, error):
        return JSONResponse({"code": error.code, "message": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse(
            {"code": "INVALID_REQUEST", "message": "Request validation failed"}, status_code=422
        )

    async def authorize_ws(ws: WebSocket):
        await ws.accept()
        if config.disable_auth:
            allowed = same_origin(ws)
            if not allowed:
                await ws.close(4403)
            return allowed
        try:
            token = await asyncio.wait_for(ws.receive_text(), timeout=5)
        except (TimeoutError, KeyError, WebSocketDisconnect):
            with suppress(RuntimeError):
                await ws.close(4401)
            return False
        authorized = len(token) <= 256 and matches(token, config.token)
        if not authorized:
            await ws.close(4401)
        return authorized

    @app.get("/healthz", tags=["Service"], summary="Check liveness", responses=success(Health))
    async def health():
        """Return whether ModemBox is running."""
        return {"ok": True}

    @app.get(
        "/auth-mode",
        tags=["Service"],
        summary="Check authentication mode",
        responses=success(AuthMode),
    )
    async def auth_mode():
        """Return the token requirement for this instance as tokenRequired."""
        return JSONResponse(
            {"tokenRequired": not config.disable_auth}, headers={"Cache-Control": "no-store"}
        )

    @app.get(
        "/v1/state", tags=["Modems"], summary="Get gateway state", responses=success(GatewayState)
    )
    async def state():
        """Read the modem, message, activity, and hook snapshot."""
        return gateway.snapshot()["data"]

    @app.get(
        "/v1/modems", tags=["Modems"], summary="List modems", responses=success(list[ModemState])
    )
    async def modems():
        """Read device IDs, capabilities, SIM status, network state, and calls."""
        return [modem.state for modem in gateway.modems.values()]

    @app.get(
        "/v1/hooks/{resource}",
        tags=["Hooks"],
        summary="List hook records",
        responses=success(HookPage),
    )
    async def hook_page(
        resource: Literal["rules", "destinations", "runs", "calls"],
        limit: PageLimit = 50,
        offset: Annotated[int, Query(ge=0, description="Records to skip before this page.")] = 0,
    ):
        """List rules, destinations, runs, or incoming calls, newest first."""
        return gateway.hooks.page(resource, limit, offset)

    @app.post(
        "/v1/hooks/rules",
        status_code=201,
        tags=["Hooks"],
        summary="Create a hook rule",
        responses=success(HookRule, 201) | HOOK_NOT_FOUND | HOOK_LIMIT,
    )
    async def create_hook_rule(rule: Rule):
        """Save a rule that matches an incoming SMS or call event."""
        return gateway.hooks.save_rule(rule)

    @app.put(
        "/v1/hooks/rules/{key}",
        tags=["Hooks"],
        summary="Replace a hook rule",
        responses=success(HookRule) | HOOK_NOT_FOUND,
    )
    async def replace_hook_rule(key: HookID, rule: Rule):
        """Replace a rule. Disabling it cancels queued actions."""
        return gateway.hooks.save_rule(rule, key)

    @app.post(
        "/v1/hooks/destinations",
        status_code=201,
        tags=["Hooks"],
        summary="Create a webhook destination",
        responses=success(HookDestination, 201) | HOOK_LIMIT,
    )
    async def create_hook_destination(destination: Destination):
        """Save an HTTP destination. The response omits header values and signing keys."""
        return gateway.hooks.save_destination(destination)

    @app.put(
        "/v1/hooks/destinations/{key}",
        tags=["Hooks"],
        summary="Replace a webhook destination",
        responses=success(HookDestination) | HOOK_NOT_FOUND,
    )
    async def replace_hook_destination(key: HookID, destination: Destination):
        """Replace an HTTP destination and cancel queued deliveries for it."""
        return gateway.hooks.save_destination(destination, key)

    @app.delete(
        "/v1/hooks/{resource}/{key}",
        tags=["Hooks"],
        summary="Delete a hook rule or destination",
        responses=success(HookDeletion) | HOOK_NOT_FOUND,
    )
    async def delete_hook(resource: Literal["rules", "destinations"], key: HookID):
        """Delete the record and cancel queued actions that reference it."""
        return gateway.hooks.remove(resource, key)

    @app.post(
        "/v1/hooks/preview",
        tags=["Hooks"],
        summary="Preview a hook rule",
        responses=success(HookPreview) | HOOK_NOT_FOUND,
    )
    async def preview_hook(preview: Preview):
        """Evaluate a draft rule against one incoming message ID or call ID without running its actions."""
        return gateway.hooks.preview(preview)

    @app.get(
        "/v1/messages", tags=["SMS"], summary="List SMS messages", responses=success(MessagePage)
    )
    async def messages(
        modem: Annotated[str | None, Query(description="Filter by modem ID.")] = None,
        before: Annotated[
            int | None, Query(ge=1, description="Return IDs below this cursor.")
        ] = None,
        limit: PageLimit = 50,
    ):
        """Read messages in descending ID order. Pass the returned next cursor as before on your next request."""
        rows = gateway.store.messages(modem, before, limit + 1)
        return {"items": rows[:limit], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    @app.get(
        "/v1/messages/{message_id}",
        tags=["SMS"],
        summary="Get an SMS message",
        responses=success(Message) | {404: error_response("Message not found.")},
    )
    async def message(message_id: Annotated[int, Path(description="Numeric message ID.")]):
        """Read one message, including per-segment status for a multipart send."""
        result = gateway.store.message(message_id)
        if result is None:
            raise OperationError("Message not found", "NOT_FOUND", 404)
        return result

    @app.post(
        "/v1/modems/{modem_id}/sms",
        tags=["SMS"],
        summary="Send an SMS",
        responses=MODEM_RESPONSES | success(Message),
    )
    async def send_sms(modem_id: ModemID, body: Sms):
        """Send a text through a modem. Check the outbox before retrying a partial or uncertain send."""
        return await gateway.act(modem_id, "http", "send_sms", number=body.number, text=body.text)

    @app.post(
        "/v1/modems/{modem_id}/pin",
        tags=["Modems"],
        summary="Unlock a SIM",
        responses=MODEM_STATE_RESPONSES,
    )
    async def pin(modem_id: ModemID, body: Pin):
        """Submit the SIM PIN. Saving it requires a successful unlock and a usable SIM identity."""
        return await gateway.act(modem_id, "http", "unlock", pin=body.pin, remember=body.remember)

    @app.delete(
        "/v1/modems/{modem_id}/pin",
        tags=["Modems"],
        summary="Forget a saved SIM PIN",
        responses=MODEM_STATE_RESPONSES,
    )
    async def forget_pin(modem_id: ModemID):
        """Remove the stored PIN for the current SIM and return the modem state."""
        return await gateway.act(modem_id, "http", "forget_pin")

    @app.post(
        "/v1/modems/{modem_id}/calls",
        tags=["Voice"],
        summary="Place a call",
        responses=MODEM_STATE_RESPONSES,
    )
    async def dial(modem_id: ModemID, body: Phone):
        """Dial a phone number on a modem with voice support."""
        return await gateway.act(modem_id, "http", "dial", number=body.number)

    @app.post(
        "/v1/modems/{modem_id}/calls/answer",
        tags=["Voice"],
        summary="Answer an incoming call",
        responses=MODEM_STATE_RESPONSES,
    )
    async def answer(modem_id: ModemID):
        """Answer the ringing call. Send Content-Length: 0 when the request has no body."""
        return await gateway.act(modem_id, "http", "answer")

    @app.delete(
        "/v1/modems/{modem_id}/calls/current",
        tags=["Voice"],
        summary="Hang up a call",
        responses=MODEM_STATE_RESPONSES,
    )
    async def hangup(modem_id: ModemID):
        """End the current call and return the modem state."""
        return await gateway.act(modem_id, "http", "hangup")

    @app.post(
        "/v1/modems/{modem_id}/audio",
        tags=["Voice"],
        summary="Play a WAV in a call",
        responses=MODEM_STATE_RESPONSES,
    )
    async def play(modem_id: ModemID, body: Clip):
        """Play a clip into the active call on a modem with call audio support."""
        return await gateway.act(modem_id, "http", "play_audio", wav=body.wav)

    @app.delete(
        "/v1/modems/{modem_id}/audio",
        tags=["Voice"],
        summary="Stop WAV playback",
        responses=MODEM_STATE_RESPONSES,
    )
    async def stop_audio(modem_id: ModemID):
        """Stop the active clip and return the modem state."""
        return await gateway.act(modem_id, "http", "stop_audio")

    @app.get(
        "/v1/modems/{modem_id}/audio",
        tags=["Voice"],
        summary="Capture recent caller audio",
        responses=success(AudioCapture) | {code: MODEM_RESPONSES[code] for code in (404, 409)},
    )
    async def capture(
        modem_id: ModemID,
        seconds: Annotated[
            int, Query(ge=1, le=10, description="Seconds of recent caller audio.")
        ] = 5,
    ):
        """Return recent caller audio as a base64 WAV from the in-memory call buffer."""
        return {
            "wav": gateway.modem(modem_id).media_required().recent(seconds),
            "format": "audio/wav",
        }

    @app.post(
        "/v1/modems/{modem_id}/at",
        tags=["Modems"],
        summary="Run an AT command",
        responses=MODEM_RESPONSES | success(AtReply),
    )
    async def at(modem_id: ModemID, body: Command):
        """Run one allowed command. Raw writes require MODEMBOX_ALLOW_RAW_AT and are disabled during calls."""
        return await gateway.act(modem_id, "http", "at", command=body.command)

    @app.websocket("/v1/events")
    async def events(ws: WebSocket):
        if not await authorize_ws(ws):
            return
        queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        gateway.clients.add(queue)
        queue.put_nowait(gateway.snapshot())

        async def send():
            while True:
                await asyncio.wait_for(ws.send_json(await queue.get()), timeout=5)

        async def receive():
            await ws.receive_text()

        try:
            await relay(send, receive)
            await ws.close()
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            pass
        finally:
            gateway.clients.discard(queue)

    @app.websocket("/v1/modems/{modem_id}/audio/ws")
    async def audio(ws: WebSocket, modem_id: str):
        if not await authorize_ws(ws):
            return
        try:
            modem = gateway.modem(modem_id)
            media = modem.media_required()
        except OperationError:
            await ws.close(4409)
            return
        owner = uuid4().hex
        queue = asyncio.Queue(maxsize=12)
        media.listeners.add(queue)

        async def send():
            while (frame := await queue.get()) is not None:
                await asyncio.wait_for(ws.send_bytes(frame), timeout=3)

        async def receive():
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                text, data = message.get("text"), message.get("bytes")
                if text == "mic:on" and media.mic_owner is None:
                    media.mic_owner = owner
                    modem.audio_changed()
                    gateway.record("browser", modem_id, "microphone_on", "ok")
                elif text == "mic:off" and media.mic_owner == owner:
                    media.mic_owner = None
                    media.clear_mic()
                    modem.audio_changed()
                    gateway.record("browser", modem_id, "microphone_off", "ok")
                elif data is not None and len(data) == FRAME_BYTES and media.mic_owner == owner:
                    if media.active and not media.clip:
                        if media.mic.full():
                            media.mic.get_nowait()
                        media.mic.put_nowait(data)

        try:
            await relay(send, receive)
            with suppress(RuntimeError):
                await ws.close()
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            pass
        finally:
            media.listeners.discard(queue)
            if media.mic_owner == owner:
                media.mic_owner = None
                media.clear_mic()
                modem.audio_changed()

    app.mount("/mcp", mcp_app)
    if config.web.is_dir():
        app.mount("/", StaticFiles(directory=config.web, html=True))
    return app
