"""Seed data against a running v0.44.x instance before upgrading to the candidate.

Exercises the HTTP API surface of the source release and writes a JSON manifest
consumed by ``tests/upgrade/verify/test_post_upgrade.py`` after the candidate
migrations and container restart complete.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

#: Source-version endpoint table. Stable endpoints apply across 0.41+; endpoints
#: outside that core set record the version range where their path and body shape
#: hold so future per-minor seeders can see the exact surface at a glance.
ENDPOINT_TABLE: dict[str, dict[str, str]] = {
    "create_admin": {"method": "POST", "path": "/users:create-admin", "since": "0.41.0"},
    "login": {"method": "POST", "path": "/auth/login", "since": "0.41.0"},
    "users_me": {"method": "GET", "path": "/users/me", "since": "0.41.0"},
    "create_user": {"method": "POST", "path": "/users", "since": "0.41.0"},
    "user_permissions": {
        "method": "PUT",
        "path": "/users/{user_id}/permissions",
        "since": "0.41.0",
    },
    "redeem_invite": {"method": "POST", "path": "/users:redeem-invite", "since": "0.41.0"},
    "create_agent": {"method": "POST", "path": "/agents", "since": "0.43.0"},
    "agent_permissions": {
        "method": "GET",
        "path": "/agents/{agent_id}/permissions",
        "since": "0.42.0",
    },
    "generate_agent_api_key": {
        "method": "POST",
        "path": "/agents/{agent_id}:generate-api-key",
        "since": "0.43.0",
    },
    "import_apis": {"method": "POST", "path": "/apis", "since": "0.41.0"},
    "list_revisions": {
        "method": "GET",
        "path": "/apis/{vendor}/{name}/{version}/revisions",
        "since": "0.41.0",
    },
    "promote_revision": {
        "method": "POST",
        "path": "/apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote",
        "since": "0.41.0",
    },
    "get_job": {"method": "GET", "path": "/jobs/{job_id}", "since": "0.41.0"},
    "get_job_result": {"method": "GET", "path": "/jobs/{job_id}/result", "since": "0.41.0"},
    "create_credential": {"method": "POST", "path": "/credentials", "since": "0.41.0"},
    "bind_agent_credential": {
        "method": "POST",
        "path": "/agents/{agent_id}/credentials",
        "since": "0.43.0",
    },
    "binding_permissions": {
        "method": "PUT",
        "path": "/credentials/{credential_id}/agents/{agent_id}/permissions",
        "since": "0.43.0",
    },
    "list_executions": {"method": "GET", "path": "/executions", "since": "0.43.0"},
}

ADMIN_EMAIL = "admin@local"
ADMIN_PASSWORD = "SmokeAdmin12345!"  # pragma: allowlist secret

MEMBER_EMAIL = "upgrade-member@test.local"
MEMBER_PASSWORD = "UpgradeMember12345!"  # pragma: allowlist secret
MEMBER_PERMISSIONS = [
    "apis:read",
    "credentials:read",
    "executions:read",
    "users:read",
]

INVITED_EMAIL = "upgrade-invited@test.local"
INVITED_PASSWORD = "UpgradeInvited12345!"  # pragma: allowlist secret
INVITED_PERMISSIONS = [
    "apis:read",
    "users:read",
]

AGENT_NAME = "upgrade-seed-agent"
AGENT_DESCRIPTION = "Agent seeded on 0.44.0 to verify post-upgrade continuity"
AGENT_PERMISSIONS = [
    "apis:read",
    "apis:write",
    "capabilities:execute",
    "credentials:read",
]

UPSTREAM_VENDOR = "upgrade-smoke-upstream"
CREDENTIAL_NAME = "upgrade-seed-bearer"
BEARER_SECRET = "upgrade-bearer-secret-v044-token"  # pragma: allowlist secret
ALLOW_RULE_PATH = "/auth/bearer"


def _request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    timeout: float = 30.0,
) -> tuple[Any, int]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(timeout=timeout) as client:
        resp = client.request(method, url, json=body, headers=headers)
        if not resp.content:
            return None, resp.status_code
        try:
            return resp.json(), resp.status_code
        except (json.JSONDecodeError, ValueError):
            return resp.text, resp.status_code


def _bootstrap_admin(app_url: str) -> tuple[str, str]:
    login_body, login_status = _request(
        f"{app_url}{ENDPOINT_TABLE['login']['path']}",
        method="POST",
        body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if login_status != 200:
        create_body, create_status = _request(
            f"{app_url}{ENDPOINT_TABLE['create_admin']['path']}",
            method="POST",
            body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if create_status != 200 or not isinstance(create_body, dict):
            raise RuntimeError(f"create-admin failed ({create_status}): {create_body}")
        login_body, login_status = _request(
            f"{app_url}{ENDPOINT_TABLE['login']['path']}",
            method="POST",
            body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )

    if login_status != 200 or not isinstance(login_body, dict):
        raise RuntimeError(f"admin login failed ({login_status}): {login_body}")
    token = str(login_body["access_token"])

    me_body, me_status = _request(
        f"{app_url}{ENDPOINT_TABLE['users_me']['path']}",
        token=token,
    )
    if me_status != 200 or not isinstance(me_body, dict):
        raise RuntimeError(f"GET /users/me failed ({me_status}): {me_body}")
    return str(me_body["id"]), token


def _seed_users(app_url: str, admin_token: str) -> dict[str, Any]:
    member_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['create_user']['path']}",
        method="POST",
        token=admin_token,
        body={
            "email": MEMBER_EMAIL,
            "first_name": "Upgrade",
            "last_name": "Member",
        },
    )
    if status != 201 or not isinstance(member_body, dict):
        raise RuntimeError(f"creating member user failed ({status}): {member_body}")
    member_user_id = str(member_body["user"]["id"])
    member_invite_token = str(member_body["invite_token"])

    perm_path = ENDPOINT_TABLE["user_permissions"]["path"].format(user_id=member_user_id)
    perm_body, status = _request(
        f"{app_url}{perm_path}",
        method="PUT",
        token=admin_token,
        body={"permissions": MEMBER_PERMISSIONS},
    )
    if status != 200:
        raise RuntimeError(f"granting member permissions failed ({status}): {perm_body}")

    redeem_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['redeem_invite']['path']}",
        method="POST",
        body={"invite_token": member_invite_token, "password": MEMBER_PASSWORD},
    )
    if status != 200 or not isinstance(redeem_body, dict):
        raise RuntimeError(f"redeeming member invite failed ({status}): {redeem_body}")

    invited_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['create_user']['path']}",
        method="POST",
        token=admin_token,
        body={
            "email": INVITED_EMAIL,
            "first_name": "Pending",
            "last_name": "Invite",
        },
    )
    if status != 201 or not isinstance(invited_body, dict):
        raise RuntimeError(f"creating invited user failed ({status}): {invited_body}")
    invited_user_id = str(invited_body["user"]["id"])
    unredeemed_invite_token = str(invited_body["invite_token"])

    inv_perm_path = ENDPOINT_TABLE["user_permissions"]["path"].format(user_id=invited_user_id)
    inv_perm_body, status = _request(
        f"{app_url}{inv_perm_path}",
        method="PUT",
        token=admin_token,
        body={"permissions": INVITED_PERMISSIONS},
    )
    if status != 200:
        raise RuntimeError(f"granting invited permissions failed ({status}): {inv_perm_body}")

    return {
        "member_user_id": member_user_id,
        "member_email": MEMBER_EMAIL,
        "member_password": MEMBER_PASSWORD,
        "member_permissions": MEMBER_PERMISSIONS,
        "invited_user_id": invited_user_id,
        "invited_email": INVITED_EMAIL,
        "invited_password": INVITED_PASSWORD,
        "invited_permissions": INVITED_PERMISSIONS,
        "unredeemed_invite_token": unredeemed_invite_token,
    }


def _seed_agent(app_url: str, admin_token: str) -> dict[str, Any]:
    agent_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['create_agent']['path']}",
        method="POST",
        token=admin_token,
        body={
            "name": AGENT_NAME,
            "description": AGENT_DESCRIPTION,
            "permissions": AGENT_PERMISSIONS,
        },
    )
    if status != 201 or not isinstance(agent_body, dict):
        raise RuntimeError(f"creating agent failed ({status}): {agent_body}")
    agent_id = str(agent_body["id"])

    perm_path = ENDPOINT_TABLE["agent_permissions"]["path"].format(agent_id=agent_id)
    perms_body, status = _request(
        f"{app_url}{perm_path}",
        token=admin_token,
    )
    if status != 200 or not isinstance(perms_body, dict):
        raise RuntimeError(f"reading agent permissions failed ({status}): {perms_body}")
    granted_permissions = sorted(str(p) for p in perms_body["permissions"])

    key_path = ENDPOINT_TABLE["generate_agent_api_key"]["path"].format(agent_id=agent_id)
    key_body, status = _request(
        f"{app_url}{key_path}",
        method="POST",
        token=admin_token,
    )
    if status != 200 or not isinstance(key_body, dict):
        raise RuntimeError(f"generating agent API key failed ({status}): {key_body}")
    api_key = str(key_body["key"])
    if not api_key.startswith("jak_"):
        raise RuntimeError(f"expected jak_ prefix on agent API key, got {api_key[:8]!r}")

    return {
        "agent_id": agent_id,
        "agent_name": AGENT_NAME,
        "agent_api_key": api_key,
        "agent_permissions": granted_permissions,
    }


def _import_upstream_spec(
    app_url: str,
    admin_token: str,
    upstream_url: str,
    *,
    timeout_s: float = 60.0,
) -> dict[str, str]:
    spec_url = f"{upstream_url.rstrip('/')}/specs/live.json"
    import_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['import_apis']['path']}",
        method="POST",
        token=admin_token,
        body={
            "sources": [
                {
                    "type": "url",
                    "url": spec_url,
                    "vendor": UPSTREAM_VENDOR,
                }
            ]
        },
    )
    if status != 202 or not isinstance(import_body, dict):
        raise RuntimeError(f"API import request failed ({status}): {import_body}")
    job_id = str(import_body["job_id"])

    job_path = ENDPOINT_TABLE["get_job"]["path"].format(job_id=job_id)
    deadline = time.monotonic() + timeout_s
    last_job: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        job_body, job_status = _request(f"{app_url}{job_path}", token=admin_token)
        if job_status != 200 or not isinstance(job_body, dict):
            raise RuntimeError(f"polling job {job_id} failed ({job_status}): {job_body}")
        last_job = job_body
        if job_body["status"] in ("completed", "failed"):
            break
        time.sleep(1.0)
    else:
        raise RuntimeError(f"import job {job_id} timed out after {timeout_s}s: {last_job}")

    if last_job is None or last_job.get("status") != "completed":
        raise RuntimeError(f"import job {job_id} failed: {last_job}")

    result_path = ENDPOINT_TABLE["get_job_result"]["path"].format(job_id=job_id)
    result_body, result_status = _request(f"{app_url}{result_path}", token=admin_token)
    if result_status != 200 or not isinstance(result_body, dict):
        raise RuntimeError(f"fetching job result failed ({result_status}): {result_body}")
    rev_entry = result_body["revisions"][0]
    api_ref = rev_entry["api"]
    vendor = str(api_ref["vendor"])
    name = str(api_ref["name"])
    version = str(api_ref["version"])
    revision_id = str(rev_entry.get("revision_id") or "")

    revs_path = ENDPOINT_TABLE["list_revisions"]["path"].format(
        vendor=vendor, name=name, version=version
    )
    revs_body, revs_status = _request(f"{app_url}{revs_path}", token=admin_token)
    if revs_status == 200 and isinstance(revs_body, dict):
        revisions = revs_body.get("data") or revs_body.get("revisions") or []
        if revisions and not any(r.get("is_current") for r in revisions):
            rev_to_promote = str(revisions[0]["revision_id"])
            promote_path = ENDPOINT_TABLE["promote_revision"]["path"].format(
                vendor=vendor, name=name, version=version, revision_id=rev_to_promote
            )
            prom_body, prom_status = _request(
                f"{app_url}{promote_path}", method="POST", token=admin_token
            )
            if prom_status != 200:
                raise RuntimeError(
                    f"promoting revision {rev_to_promote} failed ({prom_status}): {prom_body}"
                )
            revision_id = rev_to_promote

    return {
        "api_id": str(api_ref.get("id", "")),
        "vendor": vendor,
        "name": name,
        "version": version,
        "revision_id": revision_id,
        "import_job_id": job_id,
    }


def _seed_credential_and_binding(
    app_url: str,
    admin_token: str,
    agent_id: str,
    api_info: dict[str, str],
) -> dict[str, Any]:
    cred_body, status = _request(
        f"{app_url}{ENDPOINT_TABLE['create_credential']['path']}",
        method="POST",
        token=admin_token,
        body={
            "type": "bearer_token",
            "name": CREDENTIAL_NAME,
            "api": {
                "vendor": api_info["vendor"],
                "name": api_info["name"],
                "version": api_info["version"],
            },
            "provider": "static",
            "token": BEARER_SECRET,
        },
    )
    if status != 201 or not isinstance(cred_body, dict):
        raise RuntimeError(f"creating bearer credential failed ({status}): {cred_body}")
    credential_id = str(cred_body["credential"]["credential_id"])

    bind_path = ENDPOINT_TABLE["bind_agent_credential"]["path"].format(agent_id=agent_id)
    bind_body, status = _request(
        f"{app_url}{bind_path}",
        method="POST",
        token=admin_token,
        body={"credential_id": credential_id},
    )
    if status != 201 or not isinstance(bind_body, dict):
        raise RuntimeError(f"binding credential to agent failed ({status}): {bind_body}")

    rules_path = ENDPOINT_TABLE["binding_permissions"]["path"].format(
        credential_id=credential_id,
        agent_id=agent_id,
    )
    rules = [
        {
            "effect": "allow",
            "methods": ["GET"],
            "path": ALLOW_RULE_PATH,
            "match_mode": "exact",
        }
    ]
    rules_body, status = _request(
        f"{app_url}{rules_path}",
        method="PUT",
        token=admin_token,
        body=rules,
    )
    if status != 200:
        raise RuntimeError(f"setting binding permission rules failed ({status}): {rules_body}")

    return {
        "credential_id": credential_id,
        "credential_name": CREDENTIAL_NAME,
        "bearer_secret": BEARER_SECRET,
        "allow_rule_path": ALLOW_RULE_PATH,
    }


def _execute_pre_upgrade_broker_call(
    app_url: str,
    broker_url: str,
    upstream_url: str,
    admin_token: str,
    agent_id: str,
    agent_api_key: str,
) -> dict[str, Any]:
    target = f"{broker_url.rstrip('/')}/{upstream_url.rstrip('/')}{ALLOW_RULE_PATH}"
    exec_body, status = _request(
        target,
        method="GET",
        token=agent_api_key,
    )
    if status != 200 or exec_body != {"authenticated": True, "scheme": "bearer"}:
        raise RuntimeError(f"pre-upgrade broker execution failed ({status}): {exec_body}")

    executions_path = f"{ENDPOINT_TABLE['list_executions']['path']}?actor_id={agent_id}"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        list_body, list_status = _request(f"{app_url}{executions_path}", token=admin_token)
        if list_status == 200 and isinstance(list_body, dict):
            records = list_body.get("data", [])
            if records:
                record = records[0]
                return {
                    "execution_id": str(record["execution_id"]),
                    "actor_id": str(record["actor_id"]),
                    "actor_type": str(record["actor_type"]),
                    "credential_id": str(record["credential_id"]),
                    "operation_path": str(record["operation_path"]),
                    "status": str(record["status"]),
                }
        time.sleep(0.5)

    raise RuntimeError(f"pre-upgrade execution record for agent {agent_id} did not appear")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed v0.44.x data for upgrade testing.")
    parser.add_argument("--app-url", required=True, help="Base URL of the combined app")
    parser.add_argument("--broker-url", required=True, help="Base URL of the broker")
    parser.add_argument(
        "--upstream-url",
        required=True,
        help="In-network URL of the smoke-upstream service",
    )
    parser.add_argument("--manifest", required=True, help="Output path for seed-manifest.json")
    parser.add_argument(
        "--source-version",
        default="0.44.0",
        help="Source version tag being seeded",
    )
    args = parser.parse_args()

    app_url = args.app_url.rstrip("/")
    broker_url = args.broker_url.rstrip("/")
    upstream_url = args.upstream_url.rstrip("/")

    health_body, health_status = _request(f"{app_url}/health")
    if health_status != 200 or not isinstance(health_body, dict):
        raise RuntimeError(f"old app /health check failed ({health_status}): {health_body}")

    admin_user_id, admin_token = _bootstrap_admin(app_url)
    user_info = _seed_users(app_url, admin_token)
    agent_info = _seed_agent(app_url, admin_token)
    api_info = _import_upstream_spec(app_url, admin_token, upstream_url)
    cred_info = _seed_credential_and_binding(
        app_url,
        admin_token,
        agent_info["agent_id"],
        api_info,
    )
    exec_info = _execute_pre_upgrade_broker_call(
        app_url,
        broker_url,
        upstream_url,
        admin_token,
        agent_info["agent_id"],
        agent_info["agent_api_key"],
    )

    manifest = {
        "source_version": args.source_version,
        "old_health_version": health_body.get("version"),
        "app_url": app_url,
        "broker_url": broker_url,
        "upstream_url": upstream_url,
        "admin": {
            "user_id": admin_user_id,
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        },
        "users": user_info,
        "agent": agent_info,
        "api": api_info,
        "credential": cred_info,
        "pre_upgrade_execution": exec_info,
    }

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote upgrade seed manifest to {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
