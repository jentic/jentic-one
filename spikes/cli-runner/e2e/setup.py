"""Provision the e2e spike through Jentic's real APIs: admin, spec, agent, credential, binding, rules."""
import json, sys, time, uuid
from pathlib import Path

from tests.smoke.conftest import (agent_token_exchange, approve_agent, authed_request,
                                  generate_ed25519_jwks, register_agent)

BASE, UP = "http://127.0.0.1:58000", "http://127.0.0.1:58200"
VENDOR, NAME, VERSION = "github.com", "github-spike", "1.0.0"

def must(resp, ok, what):
    body, status = resp
    assert status in ok, f"{what}: {status} {body}"
    return body

body, status = authed_request(f"{BASE}/auth/login", method="POST", body={"email": "admin@local", "password": "Spike-Passw0rd!x"})
admin = body["access_token"] if status == 200 else must(authed_request(f"{BASE}/users:create-admin", method="POST",
        body={"email": "admin@local", "password": "Spike-Passw0rd!x"}), (200,), "create-admin")["access_token"]

ok = {"responses": {"200": {"description": "OK"}}}
spec = {"openapi": "3.0.3", "info": {"title": "GitHub (spike)", "version": VERSION}, "servers": [{"url": UP}],
        "paths": {"/graphql": {"post": {"operationId": "graphql", **ok}},
                  "/repos/{owner}/{repo}/pulls": {"get": {"operationId": "pulls/list", "parameters": [
                      {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                      {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}}], **ok}}}}
job = must(authed_request(f"{BASE}/apis", method="POST", token=admin, body={"sources": [{"type": "inline",
      "content": json.dumps(spec), "filename": "github-spike.json", "vendor": VENDOR, "api_name": NAME}]}), (202,), "import")
for _ in range(30):
    j = must(authed_request(f"{BASE}/jobs/{job['job_id']}", token=admin), (200,), "job")
    if j["status"] in ("completed", "failed"): break
    time.sleep(1)
print("import job:", j["status"])
revs = must(authed_request(f"{BASE}/apis/github-com/{NAME}/{VERSION}/revisions", token=admin), (200,), "revisions")["data"]
if not any(r.get("is_current") for r in revs):
    must(authed_request(f"{BASE}/apis/github-com/{NAME}/{VERSION}/revisions/{revs[0]['revision_id']}:promote",
                        method="POST", token=admin), (200,), "promote")

key, jwks = generate_ed25519_jwks()
agent_id, _ = register_agent(BASE, f"cli-runner-spike-{uuid.uuid4().hex[:6]}", jwks)
approve_agent(BASE, agent_id, admin)
agent_token = agent_token_exchange(BASE, agent_id, key)

CRED_NAME = "gh-spike"
def find_cred():
    listing = must(authed_request(f"{BASE}/credentials", token=admin), (200,), "list credentials")
    return next((c["credential_id"] for c in listing["data"] if c["name"] == CRED_NAME), None)
cred_id = find_cred()
if cred_id is None:
    must(authed_request(f"{BASE}/credentials", method="POST", token=admin, body={
        "type": "bearer_token", "name": CRED_NAME, "provider": "static",
        "api": {"vendor": VENDOR, "name": NAME, "version": VERSION}, "token": "ghp_SPIKE_FAKE_not_a_real_token"}), (200, 201), "credential")
    cred_id = find_cred()
must(authed_request(f"{BASE}/agents/{agent_id}/credentials", method="POST", token=admin, body={"credential_id": cred_id}), (200, 201), "bind")
must(authed_request(f"{BASE}/credentials/{cred_id}/agents/{agent_id}/permissions", method="PUT", token=admin, body=[
    {"effect": "allow", "methods": ["POST"], "path": "/graphql", "match_mode": "exact"},
    {"effect": "allow", "methods": ["GET"], "path": "/repos/.*", "match_mode": "regex"}]), (200,), "rules")

state = Path(__file__).with_name("state.json")
state.write_text(json.dumps({"admin_token": admin, "agent_token": agent_token, "agent_id": agent_id, "credential_id": cred_id}))
state.chmod(0o600)
print("agent", agent_id, "credential", cred_id, "-> state.json")
