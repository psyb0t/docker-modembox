import asyncio
import base64
import binascii
import io
import logging
import os
import wave
from collections import deque

import serial

from .serial_at import close_port, open_port

logger = logging.getLogger(__name__)
RATE = 8000
FRAME_SECONDS = 0.02
FRAME_BYTES = 320
MAX_CLIP_BYTES = RATE * 2 * 30
SILENCE = bytes(FRAME_BYTES)


def decode_clip(encoded: str) -> bytes:
    if len(encoded) > (MAX_CLIP_BYTES + 4096) * 4 // 3 + 4:
        raise ValueError("Audio clip exceeds 30 seconds")
    try:
        raw = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(raw)) as wav:
            if wav.getparams()[:3] != (1, 2, RATE):
                raise ValueError("Expected mono 8 kHz PCM16 WAV")
            if not 0 < wav.getnframes() <= MAX_CLIP_BYTES // 2:
                raise ValueError("Audio clip exceeds 30 seconds or is empty")
            data = wav.readframes(wav.getnframes())
            if len(data) != wav.getnframes() * 2:
                raise ValueError("Incomplete WAV payload")
            return data
    except (binascii.Error, EOFError, wave.Error) as error:
        raise ValueError("Invalid WAV") from error


def encode_clip(data: bytes) -> str:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(data)
    return base64.b64encode(stream.getvalue()).decode("ascii")


class Media:
    def __init__(self, path: str, changed):
        self.port = open_port(path, audio=True)
        self.changed = changed
        self.listeners: set[asyncio.Queue] = set()
        self.capture = deque(maxlen=500)
        self.mic: asyncio.Queue[bytes] = asyncio.Queue(maxsize=12)
        self.mic_owner = None
        self.clip = b""
        self.active = False
        self.task = asyncio.create_task(self.run())

    def close(self):
        self.task.cancel()
        close_port(self.port)
        for listener in self.listeners:
            if listener.full():
                listener.get_nowait()
            listener.put_nowait(None)

    def recent(self, seconds: int = 5):
        if not 1 <= seconds <= 10:
            raise ValueError("Capture window must be 1 to 10 seconds")
        return encode_clip(b"".join(list(self.capture)[-seconds * 50 :]))

    def clear_mic(self):
        while not self.mic.empty():
            self.mic.get_nowait()

    async def run(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        pending = bytearray()
        output = b""
        try:
            while True:
                if self.active:
                    pending.extend(self.port.read(4096))
                    while len(pending) >= FRAME_BYTES:
                        frame = bytes(pending[:FRAME_BYTES])
                        del pending[:FRAME_BYTES]
                        self.capture.append(frame)
                        for listener in self.listeners:
                            if listener.full():
                                listener.get_nowait()
                            listener.put_nowait(frame)
                    if not output and self.clip:
                        output, self.clip = self.clip[:FRAME_BYTES], self.clip[FRAME_BYTES:]
                        output = output.ljust(FRAME_BYTES, b"\0")
                        if not self.clip:
                            self.changed()
                    elif not output and not self.mic.empty():
                        output = self.mic.get_nowait()
                    elif not output:
                        output = SILENCE
                    try:
                        output = output[os.write(self.port.fileno(), output) :]
                    except BlockingIOError:
                        # Keep a partial frame until the next media tick.
                        logger.debug("Audio write deferred")
                else:
                    self.port.read(4096)
                deadline = max(deadline + FRAME_SECONDS, loop.time())
                await asyncio.sleep(max(0, deadline - loop.time()))
        except (OSError, serial.SerialException):
            logger.warning("Call audio port disconnected")
            self.active = False
            close_port(self.port)
            self.changed()
            for listener in self.listeners:
                if listener.full():
                    listener.get_nowait()
                listener.put_nowait(None)
