"""Loopback forwarder for the spike: plain-HTTP proxy -> Jentic broker.

Accepts absolute-form requests from a CLI configured with HTTP_PROXY (no TLS, no CA),
rejects hosts outside the allowlist, maps each allowed host to its upstream via HOST_MAP,
drops the CLI's own Authorization header, and relays to the broker as the agent.

usage: forwarder.py <listen-port> <broker-base> <agent-token> <log> host=upstream [host=upstream ...]
"""
import json, sys
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT, BROKER, TOKEN, LOG = int(sys.argv[1]), sys.argv[2].rstrip("/"), sys.argv[3], open(sys.argv[4], "a")
HOST_MAP = dict(a.split("=", 1) for a in sys.argv[5:])
STRIP = {"authorization", "proxy-authorization", "proxy-connection", "connection", "host", "content-length", "transfer-encoding"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log(self, **kw):
        LOG.write(json.dumps({"m": self.command, "target": self.path, **kw}) + "\n"); LOG.flush()

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        parts = urlsplit(self.path)
        upstream = HOST_MAP.get(parts.netloc)
        if not parts.scheme or upstream is None:
            self._log(verdict="rejected_host", host=parts.netloc)
            return self._reply(403, b'{"error":"host_not_allowed"}', [("Content-Type", "application/json")])
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        req = urllib.request.Request(f"{BROKER}/{upstream.rstrip('/')}{target}", data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in STRIP:
                req.add_header(k, v)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            resp = e
        data = resp.read()
        self._log(verdict="forwarded", host=parts.netloc, status=resp.status,
                  execution=resp.headers.get("jentic-execution-id"))
        self._reply(resp.status, data, [(k, v) for k, v in resp.headers.items() if k.lower() in ("content-type", "jentic-execution-id")])

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def do_CONNECT(self):
        self._log(verdict="rejected_connect")
        self._reply(403, b'{"error":"connect_not_supported"}', [("Content-Type", "application/json")])

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
