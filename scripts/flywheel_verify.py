"""Self-verifying harness for the three spec-flywheel flows.

A companion to `skills/demo-flywheel/SKILL.md`: the skill is the narrated,
human-in-the-loop walkthrough an agent performs live; this script is the
non-interactive proof that each flow's *real effects* landed on the local
control plane. It asserts server state over HTTP and exits non-zero on any
failure, so it doubles as a smoke test.

Scope: control-plane surfaces only (matches `make start-app-sqlite`, which does
NOT start the broker). It never touches GitHub — the genuine `gh` PR/issue steps
are the human-gated part of the skill; here Flow 2 verifies only the *local*
registry import effect. Every mutation uses a per-run suffix so re-runs stay
fresh (no existence-gate / duplicate-version collisions).

Flows (run in the skill's 2 -> 1 -> 3 narrative order):
  2  import a brand-new API into the local registry (inline POST /apis -> job)
  1  improve a catalogued API via an overlay: submit -> confirm (materialize)
     -> served spec reflects the fix -> rollback reverts it
  3  react to an upstream change: needs the flow3 fixture on :8099
     (import -> bump -> catalog:refresh -> update_available flips -> re-import clears)

Usage:
  python scripts/flywheel_verify.py --flow all
  python scripts/flywheel_verify.py --flow 1
  BASE=http://127.0.0.1:8000 python scripts/flywheel_verify.py --flow 3 \
      --fixture http://127.0.0.1:8099

Requires the stack up (`make start-app-sqlite`). On a fresh DB the harness
bootstraps the org-admin via POST /users:create-admin; on a re-run it logs in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
FIXTURE = os.environ.get("FLOW3_FIXTURE", "http://127.0.0.1:8099")

# A stable admin identity so a re-run can log back in after the create-admin
# endpoint self-closes (410). Password is deterministic for the demo box only.
ADMIN_EMAIL = "flywheel-admin@demo.test"
ADMIN_PW = "FlywheelDemo!234"

client = httpx.Client(base_url=BASE, timeout=30.0)


# --------------------------------------------------------------------------- #
# PASS/FAIL harness (modeled on scripts/flywheel_manual.py)                    #
# --------------------------------------------------------------------------- #
_results: list[tuple[str, bool, str]] = []


def check(label: str, got: object, expect: object) -> bool:
    """Record a PASS/FAIL row; return whether it passed."""
    ok = got == expect
    _results.append((label, ok, f"got={got!r} want={expect!r}"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got={got!r} want={expect!r}")
    return ok


def note(msg: str) -> None:
    print(f"  .. {msg}")


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def rid() -> str:
    """A short per-run id so every run picks fresh identifiers."""
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
def admin_token() -> str:
    """Bootstrap (fresh DB) or log in (re-run) the org-admin, return its token.

    POST /users:create-admin returns an already-org-admin LoginResponse with
    must_change_password:false (no rotation). It self-closes (410) after the
    first admin exists, so on a re-run we fall back to /auth/login.
    """
    r = client.post(
        "/users:create-admin",
        json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PW,
            "first_name": "Fly",
            "last_name": "Wheel",
        },
    )
    if r.status_code == 410:
        r = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    r.raise_for_status()
    token: str = r.json()["access_token"]
    return token


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #
def wait_for_job(token: str, job_id: str, *, timeout: float = 90.0) -> str:
    """Poll GET /jobs/{id} until terminal; return the final status string."""
    terminal_ok = {"succeeded", "completed", "done"}
    terminal_bad = {"failed", "cancelled", "canceled", "error"}
    deadline = time.time() + timeout
    status = "unknown"
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}", headers=h(token))
        if r.status_code == 200:
            status = str(r.json().get("status", "unknown")).lower()
            if status in terminal_ok or status in terminal_bad:
                return status
        time.sleep(1.0)
    return status


def job_ok(status: str) -> bool:
    return status in {"succeeded", "completed", "done"}


def promote_draft(token: str, v: str, n: str, ver: str) -> str | None:
    """Promote the API's draft revision to live so it has a current revision.

    A fresh `POST /apis` import lands as a DRAFT; overlays (and the served spec)
    need a current/live revision, so the demo promotes it — mirroring
    `jentic apis promote` in the import-new-api skill. Returns the promoted
    revision id, or None if no draft was found.
    """
    r = client.get(f"/apis/{v}/{n}/{ver}/revisions?state=draft", headers=h(token))
    if r.status_code != 200:
        return None
    rows = r.json().get("data", [])
    if not rows:
        return None
    revision_id = rows[0]["revision_id"]
    p = client.post(
        f"/apis/{v}/{n}/{ver}/revisions/{revision_id}:promote",
        headers=h(token),
        json={},
    )
    return revision_id if 200 <= p.status_code < 300 else None


def _find_api(token: str, name_prefix: str) -> dict[str, Any] | None:
    """Return the first GET /apis row whose api.name starts with name_prefix."""
    r = client.get("/apis", headers=h(token))
    rows = r.json().get("data", []) if r.status_code == 200 else []
    return next(
        (row for row in rows if (row.get("api") or {}).get("name", "").startswith(name_prefix)),
        None,
    )


def _minimal_spec(title: str, server: str, op_id: str) -> dict[str, Any]:
    """A tiny valid OpenAPI 3 spec with x-vendor so local ingest resolves vendor."""
    vendor_domain = f"{title.lower().replace(' ', '-')}.demo.test"
    return {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": "1.0.0",
            "x-vendor": vendor_domain,
            "x-jentic-source-url": f"https://{vendor_domain}/openapi.json",
        },
        "servers": [{"url": server}],
        "paths": {
            "/ping": {
                "get": {
                    "operationId": op_id,
                    "summary": "Ping",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


# --------------------------------------------------------------------------- #
# Flow 2 — import a brand-new API into the local registry                      #
# --------------------------------------------------------------------------- #
def flow2(token: str, run_id: str) -> None:
    print("\n== Flow 2: import a NEW API into the local registry ==")
    title = f"Flywheel Demo {run_id}"
    op_id = f"ping_{run_id}"
    spec = _minimal_spec(title, "https://api.flywheel-demo.test", op_id)

    r = client.post(
        "/apis",
        headers=h(token),
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(spec),
                    "filename": "openapi.json",
                }
            ]
        },
    )
    if not check("POST /apis accepted (202)", r.status_code, 202):
        note(f"import not accepted: {r.status_code} {r.text[:200]}")
        return
    job_id = r.json()["job_id"]
    note(f"import job {job_id}")
    status = wait_for_job(token, job_id)
    check("import job reached success", job_ok(status), True)

    # Resolve the slugified identity from the listing (don't guess the slug).
    match = _find_api(token, "flywheel-demo")
    check("imported API is listable", match is not None, True)
    if match is None:
        return

    # A fresh import is a draft — promote it so the API is live and executable.
    api = match["api"]
    promote_draft(token, api["vendor"], api["name"], api["version"])
    refreshed = _find_api(token, "flywheel-demo")
    check(
        "imported API has a live revision",
        (refreshed or {}).get("current_revision_id") is not None,
        True,
    )


# --------------------------------------------------------------------------- #
# Flow 1 — improve a catalogued API via an overlay (submit/confirm/rollback)   #
# --------------------------------------------------------------------------- #
def flow1(token: str, run_id: str) -> None:
    print("\n== Flow 1: improve an API via an overlay (submit -> confirm -> rollback) ==")
    # Seed a catalogued API to overlay: import a spec whose servers we will fix.
    title = f"Overlay Subject {run_id}"
    op_id = f"call_{run_id}"
    bad_server = "https://us.overlay-subject.demo.test"
    spec = _minimal_spec(title, bad_server, op_id)

    r = client.post(
        "/apis",
        headers=h(token),
        json={
            "sources": [{"type": "inline", "content": json.dumps(spec), "filename": "openapi.json"}]
        },
    )
    if r.status_code != 202:
        check("seed import accepted (202)", r.status_code, 202)
        note(f"cannot seed overlay subject: {r.text[:200]}")
        return
    if wait_for_job(token, r.json()["job_id"]) not in {"succeeded", "completed", "done"}:
        check("seed import completed", False, True)
        return

    # Resolve the slugified registry identity of the API we just imported.
    subject = _find_api(token, "overlay-subject")
    if subject is None:
        check("overlay subject present after import", False, True)
        return
    api = subject["api"]
    v, n, ver = api["vendor"], api["name"], api["version"]
    note(f"registry identity: {v}/{n}/{ver}")

    # Promote the draft so the API has a current (live) revision — overlays and
    # the served spec require one (else confirm 404s with no_current_revision).
    promote_draft(token, v, n, ver)

    # Submit an idempotent remove-then-set overlay that corrects the server URL.
    good_server = "https://eu.overlay-subject.demo.test"
    overlay_doc = {
        "overlay": "1.0.0",
        "info": {"title": f"Overlay for {title}", "version": "1.0.0"},
        "actions": [
            {"description": "Drop the wrong server.", "target": "$.servers", "remove": True},
            {
                "description": "Add the corrected server.",
                "target": "$",
                "update": {"servers": [{"url": good_server}]},
            },
        ],
    }
    r = client.post(
        f"/apis/{v}/{n}/{ver}/overlays",
        headers=h(token),
        json={"document": overlay_doc, "contributed_by": "flywheel_verify"},
    )
    if not check("overlay submitted (201)", r.status_code, 201):
        note(f"submit failed: {r.text[:200]}")
        return
    overlay_id = r.json()["id"]
    check("submitted overlay is pending", r.json().get("status"), "pending")

    # Confirm (materialize). Async: returns before the re-ingest completes, and
    # the confirm response carries no job id — poll the overlay itself.
    r = client.post(
        f"/apis/{v}/{n}/{ver}/overlays/{overlay_id}:confirm",
        headers=h(token),
        json={},
    )
    if not check("confirm accepted (2xx)", 200 <= r.status_code < 300, True):
        note(f"confirm failed: {r.status_code} {r.text[:200]}")
        return

    confirmed_rev = _poll_overlay_confirmed(token, v, n, ver, overlay_id)
    check("overlay materialized (confirmed_revision_id set)", confirmed_rev is not None, True)

    # The served spec must now reflect the fix.
    served = client.get(f"/apis/{v}/{n}/{ver}/openapi", headers=h(token))
    served_servers = served.json().get("servers", []) if served.status_code == 200 else []
    check(
        "served spec shows the fix",
        any(good_server in s.get("url", "") for s in served_servers),
        True,
    )

    # Roll back — restores the superseded revision; overlay becomes deprecated.
    r = client.post(
        f"/apis/{v}/{n}/{ver}/overlays/{overlay_id}:rollback",
        headers=h(token),
        json={},
    )
    check("rollback accepted (2xx)", 200 <= r.status_code < 300, True)
    reverted = _poll_served_reverts(token, v, n, ver, good_server)
    check("served spec reverted after rollback", reverted, True)
    after = client.get(f"/apis/{v}/{n}/{ver}/overlays/{overlay_id}", headers=h(token))
    check("overlay is deprecated after rollback", after.json().get("status"), "deprecated")


def _poll_overlay_confirmed(
    token: str, v: str, n: str, ver: str, overlay_id: str, *, timeout: float = 60.0
) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/apis/{v}/{n}/{ver}/overlays/{overlay_id}", headers=h(token))
        if r.status_code == 200:
            crid = r.json().get("confirmed_revision_id")
            if crid:
                return str(crid)
        time.sleep(1.0)
    return None


def _poll_served_reverts(
    token: str, v: str, n: str, ver: str, gone_url: str, *, timeout: float = 60.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        served = client.get(f"/apis/{v}/{n}/{ver}/openapi", headers=h(token))
        if served.status_code == 200:
            servers = served.json().get("servers", [])
            if not any(gone_url in s.get("url", "") for s in servers):
                return True
        time.sleep(1.0)
    return False


# --------------------------------------------------------------------------- #
# Flow 3 — react to an upstream change (needs the flow3 fixture on :8099)      #
# --------------------------------------------------------------------------- #
def flow3(token: str, fixture_url: str) -> None:
    print("\n== Flow 3: react to an upstream change (update-notify loop) ==")
    fixture = httpx.Client(base_url=fixture_url, timeout=10.0)
    try:
        if fixture.get("/healthz").status_code != 200:
            raise httpx.ConnectError("bad healthz")
    except httpx.HTTPError:
        _results.append(("Flow 3 fixture reachable on :8099", False, "SKIPPED"))
        print(
            f"  [SKIP] Flow 3: fixture upstream not reachable at {fixture_url} "
            f"(start it: python scripts/flow3_upstream_fixture.py)"
        )
        return

    catalog_api_id = "flow3-e2e.test"
    import_path = f"/catalog/{catalog_api_id}:import"

    # 1. Refresh loads the fixture manifest -> flow3-e2e.test becomes importable.
    r = client.post("/catalog:refresh", headers=h(token))
    check("catalog:refresh (load manifest) ok", 200 <= r.status_code < 300, True)

    # 2. Import the catalog API (async).
    r = client.post(import_path, headers=h(token), json={})
    if not check("catalog import accepted (202)", r.status_code, 202):
        note(f"import failed: {r.text[:200]}")
        return
    if wait_for_job(token, r.json()["job_id"]) not in {"succeeded", "completed", "done"}:
        check("catalog import completed", False, True)
        return

    check("fresh import is not outdated", _fixture_update_available(token), False)

    # 3. Simulate an upstream change (new bytes -> new digest).
    check("fixture bump ok", 200 <= fixture.post("/control/bump").status_code < 300, True)

    # 4. Trigger the sweep.
    r = client.post("/catalog:refresh", headers=h(token))
    check("catalog:refresh (sweep) ok", 200 <= r.status_code < 300, True)

    # 5. The sweep must flag the API and emit an actionable event.
    check("update_available flips true", _poll_update_available(token, True), True)
    outdated = client.get("/catalog?outdated_only=true", headers=h(token))
    check("catalog outdated_count >= 1", outdated.json().get("outdated_count", 0) >= 1, True)
    check("actionable catalog.update_available event exists", _has_update_event(token), True)

    # 6. Re-import adopts upstream: clears the flag.
    r = client.post(import_path, headers=h(token), json={})
    if wait_for_job(token, r.json()["job_id"]) not in {"succeeded", "completed", "done"}:
        check("re-import completed", False, True)
        return
    check("update_available cleared after re-import", _poll_update_available(token, False), False)


def _fixture_row(token: str) -> dict[str, Any]:
    r = client.get("/apis", headers=h(token))
    rows = r.json().get("data", []) if r.status_code == 200 else []
    return next((row for row in rows if row.get("origin") == "catalog"), {})


def _fixture_update_available(token: str) -> bool:
    return bool(_fixture_row(token).get("update_available"))


def _poll_update_available(token: str, want: bool, *, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _fixture_update_available(token) == want:
            return want
        time.sleep(1.0)
    return _fixture_update_available(token)


def _has_update_event(token: str) -> bool:
    r = client.get(
        "/events?event_type=catalog.update_available&requires_action=true",
        headers=h(token),
    )
    if r.status_code != 200:
        return False
    return any(e.get("type") == "catalog.update_available" for e in r.json().get("data", []))


# --------------------------------------------------------------------------- #
# Entrypoint                                                                   #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the three spec-flywheel flows.")
    parser.add_argument("--flow", choices=["1", "2", "3", "all"], default="all")
    parser.add_argument("--fixture", default=FIXTURE, help="Flow-3 upstream base URL")
    args = parser.parse_args()

    print(f"== flywheel_verify against {BASE} (flow={args.flow}) ==")
    try:
        token = admin_token()
    except httpx.HTTPError as exc:
        print(f"FATAL: could not obtain admin token from {BASE}: {exc}")
        print("Is the stack up? Try: make start-app-sqlite")
        return 2

    run_id = rid()
    note(f"run id: {run_id}")

    if args.flow in ("2", "all"):
        flow2(token, run_id)
    if args.flow in ("1", "all"):
        flow1(token, run_id)
    if args.flow in ("3", "all"):
        flow3(token, args.fixture)

    print("\n== summary ==")
    failures = 0
    for label, ok, detail in _results:
        mark = "PASS" if ok else ("SKIP" if detail == "SKIPPED" else "FAIL")
        if mark == "FAIL":
            failures += 1
        print(f"  {mark:4}  {label}")
    print(f"\n{failures} failure(s), {len(_results)} check(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
