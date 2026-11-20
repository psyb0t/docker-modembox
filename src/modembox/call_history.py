import csv
from dataclasses import dataclass
from time import monotonic, time
from uuid import uuid4

CALLER_ID_GRACE = 1.2
RING_SILENCE = 7
RINGING = {"incoming", "waiting"}
ANSWERED = {"active", "held"}


def caller_fields(line, validity_index):
    try:
        fields = next(csv.reader([line.partition(":")[2]], skipinitialspace=True, strict=True))
        validity = fields[validity_index].strip() if len(fields) > validity_index else ""
        return classify_caller(fields[0], validity)
    except (csv.Error, IndexError):
        return "", "unavailable"


def classify_caller(peer, validity):
    peer = peer.strip()[:120]
    if validity == "1":
        return "", "withheld"
    if validity == "2" or not peer:
        return "", "unavailable"
    return peer, "present"


@dataclass
class Incoming:
    id: str
    time: float
    deadline: float
    peer: str = ""
    caller_id: str = "unavailable"
    state: str = "incoming"
    outcome: str | None = None
    ended: float | None = None
    emitted: bool = False
    answered: bool = False
    declining: bool = False

    def data(self, modem):
        return {
            "id": self.id,
            "modem": modem,
            "time": self.time,
            "ended": self.ended,
            "peer": self.peer,
            "callerId": self.caller_id,
            "state": self.state,
            "outcome": self.outcome,
        }


class IncomingCalls:
    def __init__(self, writer, modem):
        self.writer = writer
        self.modem = modem
        self.active = {}
        self.unindexed = None
        self.caller = None
        self.ended = False
        self.last_ring = 0

    def start(self):
        call = Incoming(uuid4().hex, time(), monotonic() + CALLER_ID_GRACE)
        self.save(call)
        return call

    def save(self, call, *events):
        if self.writer:
            self.writer(call.data(self.modem()), events)

    def incoming(self, call):
        if not call.emitted:
            call.emitted = True
            self.save(call, "call.incoming")

    def signal(self, line):
        if line == "NO CARRIER" or line.startswith("^CEND:"):
            self.ended = True
        if line == "RING" or line.startswith(("+CRING:", "+CLIP:")):
            if self.ended and len(self.active) <= 1:
                self.clear()
            if line.startswith("+CLIP:"):
                self.caller = caller_fields(line, 5)
            self.last_ring = monotonic()
            if not self.active and not self.unindexed:
                self.unindexed = self.start()

    def finish(self, call, interrupted=False):
        self.incoming(call)
        call.state = "ended"
        call.ended = time()
        call.outcome = (
            "interrupted"
            if interrupted
            else "declined"
            if call.declining
            else "answered"
            if call.answered
            else "missed"
        )
        self.save(call, "call.ended")

    def sync(self, calls):
        incoming = {call.index: call for call in calls if call.direction == "incoming"}
        for index in self.active.keys() - incoming.keys():
            self.finish(self.active.pop(index))
        for index, live in incoming.items():
            current = self.active.get(index)
            if not current:
                current = self.unindexed or self.start()
                self.active[index] = current
                self.unindexed = None
            old = current.data(self.modem())
            current.state = live.state
            current.answered |= live.state in ANSWERED
            if live.caller_id != "unavailable":
                current.peer, current.caller_id = live.peer, live.caller_id
            elif live.state in RINGING and self.caller and len(incoming) == 1:
                current.peer, current.caller_id = self.caller
            if current.data(self.modem()) != old:
                self.save(current)
            if (
                current.caller_id != "unavailable"
                or monotonic() >= current.deadline
                or current.answered
            ):
                self.incoming(current)
        if self.unindexed:
            if self.caller:
                self.unindexed.peer, self.unindexed.caller_id = self.caller
                self.save(self.unindexed)
            now = monotonic()
            if now >= self.unindexed.deadline:
                self.incoming(self.unindexed)
            if self.ended or now - self.last_ring >= RING_SILENCE:
                self.finish(self.unindexed)
                self.unindexed = None
        self.caller = None
        self.ended = False

    def clear(self, interrupted=False):
        for call in self.active.values():
            self.finish(call, interrupted)
        if self.unindexed:
            self.finish(self.unindexed, interrupted)
        self.active.clear()
        self.unindexed = None
        self.caller = None
        self.ended = False
