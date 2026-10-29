"""Show exactly what loopback UDP does on this machine (Windows firewall / VPN problems)."""
import select
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
threading.excepthook = lambda a: print("THREAD DIED:", a.exc_type.__name__, a.exc_value) or traceback.print_tb(a.exc_traceback)


def mav():
    from pymavlink import mavutil
    from fieldpilot.bench import _mavutil
    from fieldpilot import winsock
    mavutil = _mavutil()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    print("port", port, "has SIO_UDP_CONNRESET:", hasattr(socket, "SIO_UDP_CONNRESET"))
    veh = winsock.vehicle_connection(mavutil, port, source_system=1, source_component=1)
    print("vehicle socket:", veh.port.getsockname(), "fd", veh.fd)
    back = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=254, source_component=190)
    print("backend socket:", back.port.getsockname())
    got_v, got_b = [], []
    stop = threading.Event()

    def vloop():
        while not stop.is_set():
            try:
                m = veh.recv_match(blocking=True, timeout=0.3)
                if m is not None:
                    got_v.append(m.get_type())
            except Exception as e:
                got_v.append("EXC " + type(e).__name__ + str(e))
                time.sleep(0.2)

    def bloop():
        while not stop.is_set():
            try:
                m = back.recv_match(blocking=True, timeout=0.3)
                if m is not None:
                    got_b.append(m.get_type())
            except Exception as e:
                got_b.append("EXC " + type(e).__name__ + str(e))
                time.sleep(0.2)

    threading.Thread(target=vloop, daemon=True).start()
    threading.Thread(target=bloop, daemon=True).start()
    for i in range(5):
        veh.mav.heartbeat_send(2, 3, 0, 0, 3)
        time.sleep(0.2)
    print("backend got:", sorted(set(got_b)), "clients:", back.clients)
    for i in range(5):
        back.mav.mission_count_send(1, 1, 1, 0)
        time.sleep(0.2)
    time.sleep(0.5)
    print("vehicle got:", sorted(set(got_v)), "count", len(got_v))
    print("backend got after reply:", sorted(set(got_b)))
    stop.set()


try:
    mav()
except Exception:
    traceback.print_exc()
