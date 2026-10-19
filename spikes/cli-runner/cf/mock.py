"""Recording mock for the Cloudflare v4 API: logs every call, returns an empty success envelope."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], "a")

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode(errors="replace") if n else ""
        LOG.write(json.dumps({"m": self.command, "t": self.path, "host": self.headers.get("Host"),
                              "auth": (self.headers.get("Authorization") or "")[:20], "body": body[:200]}) + "\n"); LOG.flush()
        data = json.dumps({"success": True, "errors": [], "messages": [], "result": [],
                           "result_info": {"page": 1, "per_page": 20, "count": 0, "total_count": 0}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
