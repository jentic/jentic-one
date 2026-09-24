#!/usr/bin/env python3
"""Fixture upstream for the Flow-3 catalog-update real-backend e2e.

Serves, on 127.0.0.1:<port> (default 8099), the two documents the app's catalog
update-notify loop needs:

  GET  /apis.json
        A curated `include`-style catalog manifest with a single entry whose
        per-API `url` matches the `.../apis/openapi/{domain}/{sub}/{version}/apis.json`
        shape the manifest parser expects (sub="main" is version-like, so the
        derived api_id is just the domain). The catalog derives the importable
        spec_url by swapping the trailing `apis.json` -> `openapi.json`.
  GET  /apis/openapi/flow3-e2e.test/main/1.0.0/openapi.json
        A minimal OpenAPI spec whose bytes change with the current "revision"
        (its operationId/summary embed the revision number), so a re-fetch after
        a bump yields a different digest — the signal the sweep reports as an
        available update. Supports conditional GET (ETag/If-None-Match).
  POST /control/bump
        Advances the served spec revision (so a test can simulate an upstream
        change without restarting the process). Returns the new revision.
  GET  /healthz -> {"ok": true}

Deliberately dependency-free (stdlib only) so CI can launch it with plain
`python` before the app starts. The app reaches it as an IP literal on loopback,
which the SSRF guard admits once `ingest.egress.allowed_private_subnets`
includes 127.0.0.0/8 (set via env in the e2e job).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import threading

HOST = "127.0.0.1"
PORT = int(os.environ.get("FLOW3_UPSTREAM_PORT", "8099"))
_API_DIR = f"http://{HOST}:{PORT}/apis/openapi/flow3-e2e.test/main/1.0.0"
_INCLUDE_URL = f"{_API_DIR}/apis.json"
SPEC_PATH = "/apis/openapi/flow3-e2e.test/main/1.0.0/openapi.json"
SPEC_URL = f"{_API_DIR}/openapi.json"

_lock = threading.Lock()
_revision = 1


def _spec_bytes() -> bytes:
    with _lock:
        rev = _revision
    spec = {
        "openapi": "3.0.0",
        "info": {"title": f"Flow3 E2E API (rev {rev})", "version": "1.0.0"},
        "servers": [{"url": "https://api.flow3-e2e.test"}],
        "paths": {
            "/ping": {
                "get": {
                    "operationId": f"ping_{rev}",
                    "summary": f"Ping revision {rev}",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    return json.dumps(spec, sort_keys=True).encode()


def _manifest_bytes() -> bytes:
    return json.dumps({"include": [{"url": _INCLUDE_URL}]}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep CI logs quiet
        pass

    def _send(self, body: bytes, *, status: int = 200, etag: str | None = None) -> None:
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/apis.json":
            self._send(_manifest_bytes())
        elif path == SPEC_PATH:
            body = _spec_bytes()
            etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
            self._send(body, etag=etag)
        elif path == "/healthz":
            self._send(b'{"ok":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] == "/control/bump":
            global _revision
            with _lock:
                _revision += 1
                rev = _revision
            self._send(json.dumps({"revision": rev}).encode())
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--print-spec-url":
        print(SPEC_URL)
        raise SystemExit(0)
    httpd = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"flow3-upstream listening on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()
