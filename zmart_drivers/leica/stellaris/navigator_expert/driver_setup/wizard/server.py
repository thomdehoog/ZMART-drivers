"""The local web server behind the wizard's page.

It does two things and nothing else: it serves the finished page from
``page/`` (plain HTML, CSS and JavaScript), and it carries the page's calls to
the wizard's steps as small JSON messages. It listens only on this computer
(``127.0.0.1``), so nobody on the network can reach it.

Pictures the driver saves (for example the orientation check) are handed to
the page by a short random address, and only files the driver itself named can
be fetched that way, never an arbitrary path.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .framework import Wizard

PAGE = Path(__file__).resolve().parent / "page"


class WizardServer:
    """Serve *wizard* on a free port of this computer until ``stop`` is called."""

    def __init__(self, wizard: Wizard, port: int = 0):
        self.wizard = wizard
        self._pictures: dict[str, Path] = {}
        self._http = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_address[1]}/"

    def start(self) -> "WizardServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def _publish_pictures(self, result: object) -> object:
        """Swap a saved picture's file path for an address the page may fetch."""
        if isinstance(result, dict) and isinstance(result.get("image"), str):
            token = secrets.token_urlsafe(8) + Path(result["image"]).suffix
            self._pictures[token] = Path(result["image"])
            result = {**result, "image": f"files/{token}"}
        return result

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console quiet
                pass

            def _send(self, status: int, body: bytes, kind: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, payload: object, status: int = 200) -> None:
                self._send(status, json.dumps(payload).encode(), "application/json")

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/steps":
                    return self._json(server.wizard.outline())
                if path.startswith("/files/"):
                    picture = server._pictures.get(path[len("/files/"):])
                    if picture is None or not picture.is_file():
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, picture.read_bytes(), mimetypes.guess_type(picture.name)[0] or "image/png")
                relative = "index.html" if path == "/" else path.lstrip("/")
                target = (PAGE / relative).resolve()
                if PAGE.resolve() not in target.parents or not target.is_file():
                    return self._send(404, b"not found", "text/plain")
                self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

            def do_POST(self):
                parts = self.path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "api":
                    return self._send(404, b"not found", "text/plain")
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    values = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._json({"ok": False, "error": "The request was not understood."}, 400)
                answer = server.wizard.call(parts[1], parts[2], values)
                if answer["ok"]:
                    answer["result"] = server._publish_pictures(answer["result"])
                self._json(answer)

        return Handler
