"""Tiny static-file launcher for The Forge.

Cold start is intentionally lazy: nothing GL-related touches the server, and
the browser is auto-opened only after the listening socket is ready. Caching
headers are tuned so reloads during dev are instant but the JS modules don't
get pinned to a stale version.

Run:
    python forge/server.py            # auto-picks port, opens browser
    python forge/server.py --port 8765
    python forge/server.py --no-open
"""

from __future__ import annotations

import argparse
import http.server
import os
import socket
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = "index.html"


class ForgeHandler(http.server.SimpleHTTPRequestHandler):
    # Lazy: only resolved when the first request arrives. Keeps cold start
    # cheap when the user just wants to verify the launcher works.
    _mime_init = False

    def __init__(self, *a, **kw):
        if not ForgeHandler._mime_init:
            self.extensions_map.update(
                {
                    ".js":   "text/javascript; charset=utf-8",
                    ".mjs":  "text/javascript; charset=utf-8",
                    ".css":  "text/css; charset=utf-8",
                    ".html": "text/html; charset=utf-8",
                    ".glsl": "text/plain; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".svg":  "image/svg+xml",
                    ".wasm": "application/wasm",
                }
            )
            ForgeHandler._mime_init = True
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        # Disable the long-lived cache so changes show up on reload.
        self.send_header("Cache-Control", "no-cache, max-age=0")
        # Cross-origin isolation isn't required, but harmless and lets us use
        # SharedArrayBuffer-backed APIs later if the sim grows.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[forge] %s - %s\n" % (self.address_string(), fmt % args))


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def pick_port(preferred: int) -> int:
    if preferred and _port_free(preferred):
        return preferred
    # Fall back to ephemeral.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not auto-open the browser")
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    port = pick_port(args.port)
    url = f"http://{args.host}:{port}/{INDEX}"

    httpd = ReusableTCPServer((args.host, port), ForgeHandler)
    print(f"[forge] listening at {url}")
    print(f"[forge] root: {ROOT}")

    if not args.no_open:
        # Open the browser as soon as the socket is bound; the static file is
        # already on disk so the page can start fetching modules immediately.
        threading.Thread(
            target=lambda: (time.sleep(0.15), webbrowser.open(url)),
            daemon=True,
        ).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[forge] stopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
