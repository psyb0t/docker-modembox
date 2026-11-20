from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from .errors import OperationError
from .service import Service


def build_mcp(service: Service) -> FastMCP:
    mcp = FastMCP("ModemBox", instructions="Control the operator's configured cellular modems.")

    async def act(modem_id: str, action: str, **args):
        try:
            return await service.act(modem_id, "mcp", action, **args)
        except OperationError as error:
            raise ToolError(str(error)) from error

    @mcp.tool
    async def list_modems() -> list[dict]:
        """List detected modems, device details, SIM and network status, capabilities and calls."""
        return [modem.state for modem in service.modems.values()]

    @mcp.tool
    async def list_sms(
        modem_id: str | None = None,
        before: int | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[dict]:
        """Read the durable SMS inbox and outbox, newest first. Use before for older IDs."""
        return service.store.messages(modem_id, before, limit)

    @mcp.tool
    async def get_sms(message_id: int) -> dict:
        """Read a message by its durable database ID."""
        message = service.store.message(message_id)
        if message is None:
            raise ToolError("Message not found")
        return message

    @mcp.tool
    async def send_sms(modem_id: str, number: str, text: str) -> dict:
        """Submit one SMS to a phone number. A submitted status does not mean delivered."""
        return await act(modem_id, "send_sms", number=number, text=text)

    @mcp.tool
    async def unlock_sim(modem_id: str, pin: str, remember: bool = False) -> dict:
        """Unlock the current SIM once if enough retries remain. Optionally save its verified PIN."""
        return await act(modem_id, "unlock", pin=pin, remember=remember)

    @mcp.tool
    async def forget_pin(modem_id: str) -> dict:
        """Remove the saved PIN for the currently identified SIM."""
        return await act(modem_id, "forget_pin")

    @mcp.tool
    async def dial(modem_id: str, number: str) -> dict:
        """Place a voice call. Poll list_modems for ringing and answer state."""
        return await act(modem_id, "dial", number=number)

    @mcp.tool
    async def answer(modem_id: str) -> dict:
        """Answer the current incoming call."""
        return await act(modem_id, "answer")

    @mcp.tool
    async def hangup(modem_id: str) -> dict:
        """End the current call."""
        return await act(modem_id, "hangup")

    @mcp.tool
    async def play_audio(modem_id: str, wav: Annotated[str, Field(max_length=650_000)]) -> dict:
        """Play base64 mono 8 kHz PCM16 WAV into the active call, up to 30 seconds."""
        return await act(modem_id, "play_audio", wav=wav)

    @mcp.tool
    async def stop_audio(modem_id: str) -> dict:
        """Stop playback of the current WAV clip."""
        return await act(modem_id, "stop_audio")

    @mcp.tool
    async def capture_audio(modem_id: str, seconds: Annotated[int, Field(ge=1, le=10)] = 5) -> dict:
        """Return a recent window of caller audio as a base64 WAV while a call is active."""
        try:
            wav = service.modem(modem_id).media_required().recent(seconds)
        except OperationError as error:
            raise ToolError(str(error)) from error
        return {"wav": wav, "format": "audio/wav"}

    @mcp.tool
    async def modem_at(modem_id: str, command: str) -> dict:
        """Run a single allowed AT command and return response lines and final result."""
        return await act(modem_id, "at", command=command)

    return mcp
