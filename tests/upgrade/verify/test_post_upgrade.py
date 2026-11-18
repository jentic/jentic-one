"""Version-agnostic post-upgrade verification suite.

Reads the JSON manifest written by the source-version seeder before the upgrade
and verifies through the candidate's public HTTP API and captured startup logs
that all pre-upgrade state, secrets, bindings, permissions, and execution
records survive the upgrade intact.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from jentic_one import __version__

pytestmark = pytest.mark.smoke

#: Expected deprecation or informational warning keys that may appear during
#: upgrade without constituting an error.
ALLOWED_WARNING_PATTERNS: tuple[str, ...] = ("config_deprecated_setting",)

_REVISION_APPLY_RE = re.compile(r"^\s*\[(registry|control|admin)\]\s+(upgrade|downgrade)\s+")
_ERROR_LINE_RE = re.compile(
    r'("level":\s*"error"|\blevel=error\b|\bERROR\b|Traceback \(most recent call last\))'
)


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


@pytest.fixture(scope="session")
def manifest_path() -> Path:
    raw = os.environ.get("UPGRADE_MANIFEST", "")
    if not raw:
        pytest.skip("UPGRADE_MANIFEST is not set (run via `make test-upgrade`)")
    path = Path(raw)
    if not path.is_file():
        pytest.fail(f"UPGRADE_MANIFEST file does not exist: {path}")
    return path


@pytest.fixture(scope="session")
def manifest(manifest_path: Path) -> dict[str, Any]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="session")
def out_dir(manifest_path: Path) -> Path:
    explicit = os.environ.get("UPGRADE_OUT_DIR", "")
    if explicit:
        return Path(explicit)
    return manifest_path.parent


@pytest.fixture(scope="session")
def admin_token(manifest: dict[str, Any]) -> str:
    app_url = str(manifest["app_url"])
    admin = manifest["admin"]
    body, status = _request(
        f"{app_url}/auth/login",
        method="POST",
        body={"email": admin["email"], "password": admin["password"]},
    )
    assert status == 200, f"Post-upgrade admin login failed ({status}): {body}"
    assert isinstance(body, dict)
    return str(body["access_token"])


def test_migration_check_and_idempotency_logs(out_dir: Path) -> None:
    """Candidate migration run, --check, and second idempotent run all succeed cleanly."""
    migrate_log = out_dir / "migrate.log"
    check_log = out_dir / "migrate-check.log"
    idempotent_log = out_dir / "migrate-idempotent.log"

    assert migrate_log.is_file(), f"Missing {migrate_log}"
    assert check_log.is_file(), f"Missing {check_log}"
    assert idempotent_log.is_file(), f"Missing {idempotent_log}"

    migrate_text = migrate_log.read_text(encoding="utf-8")
    assert "==> WARNING" not in migrate_text, (
        f"Unexpected '==> WARNING' in migrate.log:\n{migrate_text}"
    )

    check_text = check_log.read_text(encoding="utf-8")
    assert "OVERALL current" in check_text, (
        f"Expected 'OVERALL current' in migrate-check.log:\n{check_text}"
    )

    idempotent_text = idempotent_log.read_text(encoding="utf-8")
    assert "==> WARNING" not in idempotent_text, (
        f"Unexpected '==> WARNING' in second migration run:\n{idempotent_text}"
    )
    applied_on_second_run = [
        line for line in idempotent_text.splitlines() if _REVISION_APPLY_RE.search(line)
    ]
    assert not applied_on_second_run, (
        f"Second migration run was not a schema no-op; applied: {applied_on_second_run}"
    )


def test_admin_login_and_seeded_users_and_invite(
    manifest: dict[str, Any],
    admin_token: str,
) -> None:
    """Admin and seeded member logins work; users are listed; unredeemed invite redeems."""
    app_url = str(manifest["app_url"])
    users_info = manifest["users"]

    users_body, status = _request(f"{app_url}/users", token=admin_token)
    assert status == 200, f"GET /users failed ({status}): {users_body}"
    assert isinstance(users_body, dict)
    user_ids = {u["id"] for u in users_body["data"]}
    assert manifest["admin"]["user_id"] in user_ids
    assert users_info["member_user_id"] in user_ids
    assert users_info["invited_user_id"] in user_ids

    member_login, member_status = _request(
        f"{app_url}/auth/login",
        method="POST",
        body={
            "email": users_info["member_email"],
            "password": users_info["member_password"],
        },
    )
    assert member_status == 200, f"Seeded member login failed ({member_status}): {member_login}"
    assert isinstance(member_login, dict)
    assert "access_token" in member_login

    redeem_body, redeem_status = _request(
        f"{app_url}/users:redeem-invite",
        method="POST",
        body={
            "invite_token": users_info["unredeemed_invite_token"],
            "password": users_info["invited_password"],
        },
    )
    assert redeem_status == 200, (
        f"Redeeming pre-upgrade invite failed after upgrade ({redeem_status}): {redeem_body}"
    )
    assert isinstance(redeem_body, dict)
    invited_token = str(redeem_body["access_token"])

    me_body, me_status = _request(f"{app_url}/users/me", token=invited_token)
    assert me_status == 200, f"GET /users/me for redeemed user failed ({me_status}): {me_body}"
    assert isinstance(me_body, dict)
    assert me_body["id"] == users_info["invited_user_id"]
    assert me_body["email"] == users_info["invited_email"]


def test_seeded_agent_permissions_and_api_key(
    manifest: dict[str, Any],
    admin_token: str,
) -> None:
    """Pre-upgrade agent stays active, keeps permissions, and authenticates with its jak_ key."""
    app_url = str(manifest["app_url"])
    agent = manifest["agent"]
    agent_id = str(agent["agent_id"])
    agent_api_key = str(agent["agent_api_key"])

    agent_body, status = _request(f"{app_url}/agents/{agent_id}", token=admin_token)
    assert status == 200, f"GET /agents/{agent_id} failed ({status}): {agent_body}"
    assert isinstance(agent_body, dict)
    assert agent_body["id"] == agent_id
    assert agent_body["status"] == "active"
    assert agent_body["has_api_key"] is True

    perms_body, perms_status = _request(
        f"{app_url}/agents/{agent_id}/permissions",
        token=admin_token,
    )
    assert perms_status == 200, (
        f"GET /agents/{agent_id}/permissions failed ({perms_status}): {perms_body}"
    )
    assert isinstance(perms_body, dict)
    assert sorted(perms_body["permissions"]) == sorted(agent["agent_permissions"])

    self_body, self_status = _request(f"{app_url}/agents/{agent_id}", token=agent_api_key)
    assert self_status == 200, (
        f"Agent self-read with pre-upgrade jak_ key failed ({self_status}): {self_body}"
    )
    assert isinstance(self_body, dict)
    assert self_body["id"] == agent_id


def test_seeded_credential_binding_and_post_upgrade_broker_call(
    manifest: dict[str, Any],
    admin_token: str,
) -> None:
    """Pre-upgrade bearer credential and allow rule decrypt and authorize a broker call."""
    app_url = str(manifest["app_url"])
    broker_url = str(manifest["broker_url"])
    upstream_url = str(manifest["upstream_url"])
    agent_id = str(manifest["agent"]["agent_id"])
    agent_api_key = str(manifest["agent"]["agent_api_key"])
    cred_info = manifest["credential"]
    credential_id = str(cred_info["credential_id"])
    allow_path = str(cred_info["allow_rule_path"])

    bindings_body, status = _request(
        f"{app_url}/agents/{agent_id}/credentials",
        token=admin_token,
    )
    assert status == 200, f"GET /agents/{agent_id}/credentials failed ({status}): {bindings_body}"
    assert isinstance(bindings_body, dict)
    matching = [b for b in bindings_body["data"] if b["credential_id"] == credential_id]
    assert len(matching) == 1, f"Expected binding for {credential_id}, got {bindings_body['data']}"
    assert matching[0]["suspended"] is False

    rules_body, rules_status = _request(
        f"{app_url}/credentials/{credential_id}/agents/{agent_id}/permissions",
        token=admin_token,
    )
    assert rules_status == 200, f"GET binding permissions failed ({rules_status}): {rules_body}"
    assert isinstance(rules_body, dict)
    rules = rules_body["data"]
    assert len(rules) == 1
    assert rules[0]["effect"] == "allow"
    assert rules[0]["path"] == allow_path

    exec_body, exec_status = _request(
        f"{broker_url.rstrip('/')}/{upstream_url.rstrip('/')}{allow_path}",
        method="GET",
        token=agent_api_key,
    )
    assert exec_status == 200, (
        f"Post-upgrade broker call with pre-upgrade jak_ key and bearer credential failed "
        f"({exec_status}): {exec_body}"
    )
    assert exec_body == {"authenticated": True, "scheme": "bearer"}


def test_pre_upgrade_execution_record_preserved(
    manifest: dict[str, Any],
    admin_token: str,
) -> None:
    """Execution recorded on the old release remains queryable with intact attribution."""
    app_url = str(manifest["app_url"])
    pre_exec = manifest["pre_upgrade_execution"]
    execution_id = str(pre_exec["execution_id"])

    detail_body, detail_status = _request(
        f"{app_url}/executions/{execution_id}",
        token=admin_token,
    )
    assert detail_status == 200, (
        f"GET /executions/{execution_id} failed ({detail_status}): {detail_body}"
    )
    assert isinstance(detail_body, dict)
    assert detail_body["execution_id"] == execution_id
    assert detail_body["actor_id"] == pre_exec["actor_id"]
    assert detail_body["actor_type"] == pre_exec["actor_type"]
    assert detail_body["credential_id"] == pre_exec["credential_id"]
    assert detail_body["operation_path"] == pre_exec["operation_path"]
    assert detail_body["status"] == "completed"
    assert detail_body["http_status"] == 200
    assert isinstance(detail_body.get("api"), dict)
    assert detail_body["api"]["vendor"] == manifest["api"]["vendor"]

    list_body, list_status = _request(
        f"{app_url}/executions?actor_id={pre_exec['actor_id']}",
        token=admin_token,
    )
    assert list_status == 200, f"GET /executions failed ({list_status}): {list_body}"
    assert isinstance(list_body, dict)
    listed_ids = {item["execution_id"] for item in list_body["data"]}
    assert execution_id in listed_ids


def test_candidate_health_and_clean_startup_logs(
    manifest: dict[str, Any],
    out_dir: Path,
) -> None:
    """Candidate app and broker report the candidate version on /health with no ERROR logs."""
    app_url = str(manifest["app_url"])
    broker_url = str(manifest["broker_url"])
    expected_version = os.environ.get("EXPECTED_CANDIDATE_VERSION", __version__)

    app_health, app_status = _request(f"{app_url}/health")
    assert app_status == 200, f"Candidate app /health failed ({app_status}): {app_health}"
    assert isinstance(app_health, dict)
    assert app_health.get("status") == "ok"
    assert app_health.get("version") == expected_version

    broker_health, broker_status = _request(f"{broker_url}/health")
    assert broker_status == 200, (
        f"Candidate broker /health failed ({broker_status}): {broker_health}"
    )
    assert isinstance(broker_health, dict)
    assert broker_health.get("status") == "ok"
    assert broker_health.get("version") == expected_version

    for log_name in ("migrate.log", "new-app.log", "new-broker.log"):
        log_path = out_dir / log_name
        assert log_path.is_file(), f"Expected log file {log_path} to exist"
        offending: list[str] = []
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(allowed in line for allowed in ALLOWED_WARNING_PATTERNS):
                continue
            if _ERROR_LINE_RE.search(line):
                offending.append(line)
        assert not offending, f"Unexpected ERROR log lines in {log_name}:\n" + "\n".join(offending)
