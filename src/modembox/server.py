import asyncio
import hmac
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection

from .config import Config
from .errors import OperationError
from .mcp_tools import build_mcp
from .media import FRAME_BYTES
from .service import Service

MAX_BODY = 1_048_576
QUEUE_SIZE = 64


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Phone(Input):
    number: str = Field(pattern=r"^\+?[0-9]{3,20}$")


class Sms(Phone):
    text: str = Field(min_length=1, max_length=70)


class Pin(Input):
    pin: str = Field(pattern=r"^[0-9]{4,8}$")
    remember: bool = False


class Clip(Input):
    wav: str = Field(max_length=650_000)


class Command(Input):
    command: str = Field(min_length=2, max_length=128)


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

    app = FastAPI(
        title="ModemBox", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
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

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.get("/auth-mode")
    async def auth_mode():
        return JSONResponse(
            {"tokenRequired": not config.disable_auth}, headers={"Cache-Control": "no-store"}
        )

    @app.get("/v1/state")
    async def state():
        return gateway.snapshot()["data"]

    @app.get("/v1/modems")
    async def modems():
        return [modem.state for modem in gateway.modems.values()]

    @app.get("/v1/messages")
    async def messages(
        modem: str | None = None,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        rows = gateway.store.messages(modem, before, limit + 1)
        return {"items": rows[:limit], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    @app.get("/v1/messages/{message_id}")
    async def message(message_id: int):
        result = gateway.store.message(message_id)
        if result is None:
            raise OperationError("Message not found", "NOT_FOUND", 404)
        return result

    @app.post("/v1/modems/{modem_id}/sms")
    async def send_sms(modem_id: str, body: Sms):
        return await gateway.act(modem_id, "http", "send_sms", number=body.number, text=body.text)

    @app.post("/v1/modems/{modem_id}/pin")
    async def pin(modem_id: str, body: Pin):
        return await gateway.act(modem_id, "http", "unlock", pin=body.pin, remember=body.remember)

    @app.delete("/v1/modems/{modem_id}/pin")
    async def forget_pin(modem_id: str):
        return await gateway.act(modem_id, "http", "forget_pin")

    @app.post("/v1/modems/{modem_id}/calls")
    async def dial(modem_id: str, body: Phone):
        return await gateway.act(modem_id, "http", "dial", number=body.number)

    @app.post("/v1/modems/{modem_id}/calls/answer")
    async def answer(modem_id: str):
        return await gateway.act(modem_id, "http", "answer")

    @app.delete("/v1/modems/{modem_id}/calls/current")
    async def hangup(modem_id: str):
        return await gateway.act(modem_id, "http", "hangup")

    @app.post("/v1/modems/{modem_id}/audio")
    async def play(modem_id: str, body: Clip):
        return await gateway.act(modem_id, "http", "play_audio", wav=body.wav)

    @app.delete("/v1/modems/{modem_id}/audio")
    async def stop_audio(modem_id: str):
        return await gateway.act(modem_id, "http", "stop_audio")

    @app.get("/v1/modems/{modem_id}/audio")
    async def capture(modem_id: str, seconds: Annotated[int, Query(ge=1, le=10)] = 5):
        return {
            "wav": gateway.modem(modem_id).media_required().recent(seconds),
            "format": "audio/wav",
        }

    @app.post("/v1/modems/{modem_id}/at")
    async def at(modem_id: str, body: Command):
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
