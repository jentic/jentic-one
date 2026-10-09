"""Local request recorder for the connect-flow security regression suite.

Listens on 127.0.0.1, answers every request with ``200 {"ok": true}`` and
appends one JSON line per request (method, path, query, headers, body) to
``--log``, so a test can assert that a stored credential never reached a host
it was not connected for. ``GET /__recorder/health`` is the readiness probe
and is not logged.

    python -m tests.harness.request_recorder --port 55803 --log /tmp/recorder.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_lock = threading.Lock()


def _handler(log_path: str) -> type[BaseHTTPRequestHandler]:
    class Recorder(BaseHTTPRequestHandler):
        def _record(self) -> None:
            if self.path == "/__recorder/health":
                self._reply()
                return
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            parts = urlsplit(self.path)
            entry = {
                "method": self.command,
                "path": parts.path,
                "query": parts.query,
                "headers": dict(self.headers.items()),
                "body": body,
            }
            with _lock, open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            self._reply()

        def _reply(self) -> None:
            payload = b'{"ok": true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._record()

        def do_POST(self) -> None:
            self._record()

        def do_PUT(self) -> None:
            self._record()

        def do_PATCH(self) -> None:
            self._record()

        def do_DELETE(self) -> None:
            self._record()

        def log_message(self, format: str, *args: object) -> None:
            return

    return Recorder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(args.log))
    server.serve_forever()


if __name__ == "__main__":
    main()
