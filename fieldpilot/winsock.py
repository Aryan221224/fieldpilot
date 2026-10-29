"""Windows UDP quirk fix for pymavlink.

On Windows, a UDP socket that sent a datagram to a port nobody is listening on yet gets an ICMP
"port unreachable" back, and the *next* recvfrom() on that socket raises ConnectionResetError
(WSAECONNRESET, 10054) instead of waiting. pymavlink's mavudp.recv() does not expect it, so the
exception escapes and kills whatever thread was reading -- typically a simulator or vehicle link
that starts before FieldPilot has bound its end (ArduPilot SITL, the fake vehicle in the tests).
Linux ignores the ICMP error on an unconnected socket, which is why this only shows on Windows.

apply() makes mavudp.recv() treat a reset as "no data" instead of raising. It is idempotent and
harmless off Windows.
"""
from __future__ import annotations

import socket
import sys

_WIN = sys.platform == "win32"


def apply() -> None:
    from pymavlink import mavutil
    cls = mavutil.mavudp
    if getattr(cls, "_fp_winsock", False):
        return
    cls._fp_winsock = True

    orig_recv = cls.recv

    def recv(self, n=None):
        try:
            return orig_recv(self, n)
        except ConnectionResetError:
            return ""

    cls.recv = recv


def vehicle_connection(mavutil, port: int, **kw):
    """A pymavlink ``udpout`` link to 127.0.0.1:<port> for a simulated vehicle in the same process.

    On Windows pymavlink binds a udpout socket to 0.0.0.0:<same port> it sends to. A vehicle and
    the flight-side ``udpin`` link in one process then share a port number, the link's replies go
    back to its own socket and the vehicle never hears them. A real simulator is a separate
    process on another port and is not affected; this only matters for in-process test peers.
    Rebinding the vehicle to an ephemeral port gives it the same behaviour as on Linux.
    """
    conn = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", **kw)
    if _WIN:
        old = conn.port
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.setblocking(False)
        conn.port, conn.fd = s, s.fileno()
        old.close()
    return conn
