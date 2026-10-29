"""FieldPilot as a desktop app: starts the server inside the process and opens its own window.

Run from source:  python -m fieldpilot.app      Packaged:  FieldPilot.exe (scripts\\build_exe.bat)
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .paths import FROZEN, ROOT


def _free_port(preferred: int = 8080) -> int:
    for p in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def main() -> int:
    os.chdir(ROOT)
    if FROZEN:                      # windowed exe: no console, so send output to a log file
        log = open(ROOT / "FieldPilot_log.txt", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    import uvicorn

    from .api import app
    port = _free_port(int(os.environ.get("FP_PORT", "8080")))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    os.environ["FP_URL"] = f"http://127.0.0.1:{port}"     # the simulation lab's device feeds post here
    url = f"http://127.0.0.1:{port}/desk"
    try:
        import webview
        webview.create_window("FieldPilot · Future Agri", url, width=1360, height=860, min_size=(900, 600))
        webview.start()             # returns when the window is closed
    except Exception as e:          # no WebView2 / pywebview: the default browser instead
        print("window unavailable, using the browser:", e)
        webbrowser.open(url)
        while not server.should_exit:
            time.sleep(1)
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
