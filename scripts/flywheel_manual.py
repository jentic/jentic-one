"""Manual flywheel driver — exercises the admin-console flow against a running app.

Not a test; a throwaway script to observe real behaviour end-to-end:
  1. bootstrap admin
  2. admin creates two users (invite -> redeem -> login) with different scopes
  3. admin + each user create agents and toolkits
  4. verify reads by role (who can list what; who gets 403)

Run against `make start-app-sqlite` (http://127.0.0.1:8000).
"""

from __future__ import annotations

import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
PW = "S3curePassw0rd!"
c = httpx.Client(base_url=BASE, timeout=10.0)


def h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def sfx() -> str:
    return uuid.uuid4().hex[:8]


def check(label: str, got: int, expect: int | tuple[int, ...]) -> None:
    ok = got == expect if isinstance(expect, int) else got in expect
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}: {got} (want {expect})")
    if not ok:
        check.failures += 1  # type: ignore[attr-defined]


check.failures = 0  # type: ignore[attr-defined]


def bootstrap_admin() -> str:
    r = c.post(
        "/users:create-admin",
        json={
            "email": f"admin-{sfx()}@flywheel.test",
            "password": PW,
            "first_name": "Ada",
            "last_name": "Admin",
        },
    )
    if r.status_code == 410:  # already bootstrapped — log in as the known admin
        r = c.post(
            "/auth/login", json={"email": "admin@flywheel.test", "password": "AdminPassw0rd!"}
        )
    r.raise_for_status()
    return r.json()["access_token"]


def create_user(admin: str, email: str, perms: list[str]) -> str:
    """Admin invites a user; redeem the invite to set a password; log in."""
    r = c.post(
        "/users",
        headers=h(admin),
        json={"email": email, "first_name": "Test", "last_name": "User", "permissions": perms},
    )
    r.raise_for_status()
    token = r.json()["invite_token"]
    r = c.post("/users:redeem-invite", json={"invite_token": token, "password": PW})
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    print("== 1. bootstrap admin ==")
    admin = bootstrap_admin()
    print("  admin token acquired")

    print("== 2. admin creates users ==")
    # A 'manager' with broad read/write; a 'reader' with only read scopes.
    mgr_email = f"mgr-{sfx()}@flywheel.test"
    rdr_email = f"rdr-{sfx()}@flywheel.test"
    manager = create_user(
        admin, mgr_email, ["agents:read", "agents:write", "toolkits:read", "toolkits:write"]
    )
    reader = create_user(admin, rdr_email, ["agents:read", "toolkits:read"])
    print(f"  created manager={mgr_email} reader={rdr_email}")

    print("== 3a. create agents (admin + manager; reader should be denied write) ==")
    r = c.post("/agents", headers=h(admin), json={"name": f"admin-bot-{sfx()}"})
    check("admin create agent", r.status_code, 201)
    r = c.post("/agents", headers=h(manager), json={"name": f"mgr-bot-{sfx()}"})
    check("manager create agent", r.status_code, 201)
    r = c.post("/agents", headers=h(reader), json={"name": f"rdr-bot-{sfx()}"})
    check("reader create agent -> forbidden", r.status_code, 403)

    print("== 3b. create toolkits ==")
    r = c.post("/toolkits", headers=h(admin), json={"name": f"admin-tk-{sfx()}"})
    check("admin create toolkit", r.status_code, 201)
    r = c.post("/toolkits", headers=h(manager), json={"name": f"mgr-tk-{sfx()}"})
    check("manager create toolkit", r.status_code, 201)
    r = c.post("/toolkits", headers=h(reader), json={"name": f"rdr-tk-{sfx()}"})
    check("reader create toolkit -> forbidden", r.status_code, 403)

    print("== 4. reads by role ==")
    r = c.get("/agents", headers=h(admin))
    check("admin list agents", r.status_code, 200)
    admin_count = len(r.json().get("data", []))
    r = c.get("/agents", headers=h(manager))
    check("manager list agents", r.status_code, 200)
    mgr_count = len(r.json().get("data", []))
    r = c.get("/agents", headers=h(reader))
    check("reader list agents", r.status_code, 200)
    print(f"  agent visibility: admin={admin_count} manager={mgr_count}")

    r = c.get("/toolkits", headers=h(admin))
    check("admin list toolkits", r.status_code, 200)
    r = c.get("/toolkits", headers=h(reader))
    check("reader list toolkits", r.status_code, 200)

    print("== 5. admin-only reads (users list) ==")
    r = c.get("/users", headers=h(admin))
    check("admin list users", r.status_code, 200)
    r = c.get("/users", headers=h(reader))
    check("reader list users -> forbidden", r.status_code, 403)

    print(f"\n== DONE: {check.failures} failure(s) ==")  # type: ignore[attr-defined]
    return 1 if check.failures else 0  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
