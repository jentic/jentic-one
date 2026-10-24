"""Mock GitHub upstream on the host. Logs each call and the Authorization prefix it received."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], "a")
PRS = [{"number": n, "title": f"spike PR {n}", "state": "OPEN", "url": f"https://github.com/cli/cli/pull/{n}",
        "headRefName": f"b{n}", "headRepositoryOwner": {"id": "1", "login": "cli"}, "isCrossRepository": False,
        "isDraft": False, "createdAt": "2026-10-19T00:00:00Z"} for n in (101, 102)]

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        LOG.write(json.dumps({"m": self.command, "path": self.path, "auth": (self.headers.get("Authorization") or "")[:22]}) + "\n"); LOG.flush()
        if self.path.startswith("/graphql"):
            out = {"data": {"repository": {"pullRequests": {"totalCount": 2, "nodes": PRS, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        else:
            out = [{"number": p["number"], "title": p["title"], "state": "open"} for p in PRS]
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = _handle
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
