"""Recording mock reached via HTTP_PROXY: logs absolute target + key headers; serves canned GitHub/AWS shapes."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[2], "a")
GH = "http://api.github.localhost"

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _send(self, code, obj=None, headers=None, ctype="application/json"):
        data = b"" if obj is None else (obj.encode() if isinstance(obj, str) else json.dumps(obj).encode())
        self.send_response(code)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = b"<chunked>"
        LOG.write(json.dumps({"m": self.command, "t": self.path, "host": self.headers.get("Host"),
            "x-amz-content-sha256": self.headers.get("x-amz-content-sha256"),
            "content-encoding": self.headers.get("Content-Encoding"),
            "auth": (self.headers.get("Authorization") or "")[:40], "len": len(body)}) + "\n"); LOG.flush()
        p = self.path
        if p.endswith("/graphql"):
            return self._send(200, {"data": {"repository": {"pullRequest": {"number": 1, "title": "t", "state": "OPEN", "url": "https://github.com/o/r/pull/1", "author": {"login": "a"}}, "pullRequests": {"totalCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False}}}}})
        if "/issues" in p and "page=2" not in p:
            return self._send(200, [{"number": 1, "title": "one"}], {"Link": '<https://api.github.com/repositories/1/issues?page=2>; rel="next"'})
        if "/issues" in p:
            return self._send(200, [{"number": 2, "title": "two"}])
        if "/actions/runs/1/logs" in p or "/actions/jobs/" in p and p.endswith("/logs"):
            return self._send(302, None, {"Location": "https://productionresultssa0.blob.core.windows.net/logs/1.zip?sig=REDACTED"})
        if "/actions/runs/1/jobs" in p:
            return self._send(200, {"total_count": 1, "jobs": [{"id": 9, "name": "build", "status": "completed", "conclusion": "success", "steps": []}]})
        if "/actions/runs/1" in p:
            return self._send(200, {"id": 1, "name": "ci", "status": "completed", "conclusion": "success", "jobs_url": GH + "/repos/o/r/actions/runs/1/jobs", "logs_url": GH + "/repos/o/r/actions/runs/1/logs", "head_branch": "main", "event": "push", "workflow_id": 1, "run_attempt": 1, "html_url": "https://github.com/o/r/actions/runs/1", "created_at": "2026-10-19T00:00:00Z", "updated_at": "2026-10-19T00:00:00Z"})
        if "/releases/tags/v1" in p:
            return self._send(200, {"id": 1, "tag_name": "v1", "assets": [{"id": 7, "name": "a.txt", "url": "https://api.github.com/repos/o/r/releases/assets/7", "browser_download_url": "https://github.com/o/r/releases/download/v1/a.txt", "size": 3}]})
        if "/releases/assets/7" in p:
            return self._send(302, None, {"Location": "https://objects.githubusercontent.com/release-assets/7?token=REDACTED"})
        if self.headers.get("Host", "").startswith("api.github") or "/repos/" in p:
            return self._send(200, {})
        if self.command == "GET" and "list-type=2" in p:
            return self._send(200, '<?xml version="1.0"?><ListBucketResult><Name>b</Name><KeyCount>0</KeyCount><IsTruncated>false</IsTruncated></ListBucketResult>', ctype="application/xml")
        if self.command == "GET":
            return self._send(200, '<?xml version="1.0"?><ListAllMyBucketsResult><Buckets/></ListAllMyBucketsResult>', ctype="application/xml")
        return self._send(200, "", {"ETag": '"x"'}, ctype="application/xml")
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _handle
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
