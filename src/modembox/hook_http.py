import asyncio
import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
from io import BufferedReader, RawIOBase
from time import monotonic, time
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 5
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)
TLS_CONTEXT = ssl.create_default_context()
IPV6_TRANSLATION = tuple(
    ipaddress.ip_network(network) for network in ("64:ff9b::/96", "64:ff9b:1::/48")
)


def allowed_address(value: str, approved: str | None) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return allowed_address(str(address.ipv4_mapped), approved)
        if (
            address.scope_id
            or address.sixtofour
            or address.teredo
            or any(address in network for network in IPV6_TRANSLATION)
        ):
            return False
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    if any(address in network for network in PRIVATE_NETWORKS):
        return approved is not None and address == ipaddress.ip_address(approved)
    return address.is_global and not address.is_reserved


class DeadlineReader(RawIOBase):
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        return self.sock.recv_into(buffer)


class DeadlineSocket:
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline

    def makefile(self, _mode):
        return BufferedReader(DeadlineReader(self.sock, self.deadline))

    def sendall(self, data):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        self.sock.sendall(data)

    def close(self):
        self.sock.close()


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, hostname, port, address, use_tls):
        super().__init__(hostname, port, timeout=CONNECT_TIMEOUT)
        self.address = address
        self.use_tls = use_tls

    def connect(self):
        deadline = monotonic() + self.timeout
        self.sock = socket.create_connection((self.address, self.port), timeout=self.timeout)
        if self.use_tls:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            self.sock = TLS_CONTEXT.wrap_socket(self.sock, server_hostname=self.host)
        self.sock = DeadlineSocket(self.sock, deadline)


def request(address, target, method, body, headers):
    use_tls = target.scheme == "https"
    port = target.port or (443 if use_tls else 80)
    connection = PinnedConnection(target.hostname, port, address, use_tls)
    try:
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        connection.request(method, path, body, headers)
        return connection.getresponse().status
    finally:
        connection.close()


async def deliver(destination: dict, payload: bytes | None, content_type: str, delivery_id: str):
    target = urlsplit(destination["url"])
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(CONNECT_TIMEOUT):
        addresses = await loop.getaddrinfo(
            target.hostname,
            target.port or (443 if target.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    allowed = next(
        (
            address[4][0]
            for address in addresses
            if allowed_address(address[4][0], destination["privateAddress"])
        ),
        None,
    )
    if allowed is None:
        return None
    headers = destination["headers"].copy()
    headers["X-ModemBox-Delivery"] = delivery_id
    if payload is not None and not any(name.lower() == "content-type" for name in headers):
        headers["Content-Type"] = content_type
    if key := destination["signingKey"]:
        stamp = str(int(time()))
        signature = hmac.new(
            key.encode(), stamp.encode() + b"." + (payload or b""), hashlib.sha256
        ).hexdigest()
        headers["X-ModemBox-Signature"] = f"t={stamp},v1={signature}"
    return await asyncio.to_thread(
        request, allowed, target, destination["method"], payload, headers
    )
