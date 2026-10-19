"""Loopback forwarder for the spike: plain-HTTP proxy -> Jentic broker.

Accepts absolute-form requests from a CLI configured with HTTP_PROXY (no TLS, no CA),
rejects hosts outside the allowlist, maps each allowed host to its upstream via the host map,
and relays to the broker as the agent. Redirects are never followed: 3xx responses go back to
the CLI, whose next request is checked against the allowlist again.

usage: forwarder.py <listen-port> <broker-base> <token-file> <log> host=upstream [host=upstream ...]

The token is read from <token-file> (not argv or env) so `ps` does not expose it.
"""
import http.client
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT, BROKER, LOG = int(sys.argv[1]), urlsplit(sys.argv[2]), open(sys.argv[4], "a")
TOKEN = open(sys.argv[3]).read().strip()
HOST_MAP = dict(a.split("=", 1) for a in sys.argv[5:])
INVOCATION = "inv_" + secrets.token_hex(8)

# Only these CLI request headers reach the broker. Broker-control headers
# (Jentic-Revision, Jentic-Credential-Name, Prefer, Idempotency-Key) are never forwarded.
FORWARD_HEADERS = {"accept", "accept-encoding", "accept-language", "content-type", "user-agent",
                   "if-none-match", "if-modified-since", "range", "x-github-api-version"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailer", "transfer-encoding", "upgrade", "content-length"}


def read_body(rfile, headers):
    if "chunked" in (headers.get("Transfer-Encoding") or "").lower():
        out = bytearray()
        while True:
            size = int(rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while rfile.readline().strip():  # trailers
                    pass
                return bytes(out)
            out += rfile.read(size)
            rfile.readline()
    n = int(headers.get("Content-Length") or 0)
    return rfile.read(n) if n else b""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log(self, **kw):
        LOG.write(json.dumps({"invocation": INVOCATION, "m": self.command, "target": self.path, **kw}) + "\n")
        LOG.flush()

    def _reply(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status, code):
        self._reply(status, json.dumps({"error": code}).encode(), [("Content-Type", "application/json")])

    def _handle(self):
        parts = urlsplit(self.path)
        upstream = HOST_MAP.get(parts.netloc)
        body = read_body(self.rfile, self.headers)
        if not parts.scheme or upstream is None:
            self._log(verdict="rejected_host", host=parts.netloc)
            return self._error(403, "host_not_allowed")
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_HEADERS}
        headers["Authorization"] = f"Bearer {TOKEN}"
        try:
            conn = http.client.HTTPConnection(BROKER.hostname, BROKER.port, timeout=30)
            conn.request(self.command, f"/{upstream.rstrip('/')}{target}", body=body or None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as e:
            self._log(verdict="broker_unreachable", host=parts.netloc, error=type(e).__name__)
            return self._error(502, "broker_unreachable")
        finally:
            conn.close()
        self._log(verdict="forwarded", host=parts.netloc, status=resp.status,
                  execution=resp.getheader("jentic-execution-id"))
        out = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP_BY_HOP]
        self._reply(resp.status, data, out)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def do_CONNECT(self):
        self._log(verdict="rejected_connect")
        self._error(403, "connect_not_supported")

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
