"""Unit and CLI tests for #1079 and #1388 credential/API identity repair."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from jentic_one import __main__ as cli_mod
from jentic_one.control.services import upgrade_steps as steps_mod
from jentic_one.migrations import run as run_mod
from jentic_one.shared.models.credential_identity_repair import (
    ApiReferenceCounts,
    ConnectSessionSnapshot,
    CredentialReferenceCounts,
    CredentialSnapshot,
    IdentityRepairBackupError,
    RegistryApiSnapshot,
    build_backup_document,
    build_repair_plan,
    looks_like_domain_segment,
    validate_backup_document,
)


def test_domain_segment_distinguishes_hosts_from_semver() -> None:
    assert looks_like_domain_segment("api.github.com") is True
    assert looks_like_domain_segment("stripe.com") is True
    assert looks_like_domain_segment("finage.co.uk") is True
    assert looks_like_domain_segment("1.1.4") is False
    assert looks_like_domain_segment("1.1.4-beta.1") is False
    assert looks_like_domain_segment("v2.0.alpha") is False
    assert looks_like_domain_segment("openapi.json") is False


def test_1388_github_openapi_path_repairs_to_unique_registered_api() -> None:
    """Pins #1388: github-com/openapi/api.github.com maps to github-com/api-github-com/1.1.4."""
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
            catalog_api_id="github.com/api.github.com",
        )
    ]
    creds = [
        CredentialSnapshot(
            id="cred_gh_1388",
            name="GitHub",
            created_by="usr_1",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
            catalog_api_id=None,
        )
    ]
    plan = build_repair_plan(apis=apis, credentials=creds)
    assert len(plan.credential_repairs) == 1
    rep = plan.credential_repairs[0]
    assert (rep.after_vendor, rep.after_name, rep.after_version) == (
        "github-com",
        "api-github-com",
        "1.1.4",
    )
    assert not plan.conflicts
    assert not plan.unresolvable


def test_1388_reports_unresolvable_without_mutation_when_no_api_registered() -> None:
    """When no matching API is imported, path-derived rows report unresolvable without guessing."""
    creds = [
        CredentialSnapshot(
            id="cred_gh_missing",
            name="GitHub",
            created_by="usr_1",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
        )
    ]
    manifest = [{"api_id": "github.com/api.github.com", "spec_url": "https://example.test/gh.json"}]
    plan = build_repair_plan(apis=[], credentials=creds, manifest_entries=manifest)
    assert plan.credential_repairs == ()
    assert plan.conflicts == ()
    assert len(plan.unresolvable) == 1
    finding = plan.unresolvable[0]
    assert finding.category == "unresolvable"
    assert finding.reason == "no_matching_registered_api"
    assert "github.com/api.github.com" in finding.remediation


def test_1388_fails_closed_when_multiple_candidate_versions_exist() -> None:
    """Multiple registered API versions for a path-pinned credential fail closed."""
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
        ),
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="2.0.0",
        ),
    ]
    creds = [
        CredentialSnapshot(
            id="cred_gh_ambig",
            name="GitHub",
            created_by="usr_1",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
        )
    ]
    plan = build_repair_plan(apis=apis, credentials=creds)
    assert plan.credential_repairs == ()
    assert len(plan.conflicts) == 1
    conflict = plan.conflicts[0]
    assert conflict.reason == "multiple_candidate_apis"
    assert "github-com/api-github-com/1.1.4" in conflict.remediation
    assert "github-com/api-github-com/2.0.0" in conflict.remediation


def test_path_derived_variants_and_vendor_slash_forms() -> None:
    """Inventory of pre-split and unsplit path forms resolves when unique."""
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
            catalog_api_id="github.com/api.github.com",
        ),
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="stripe-com",
            name="stripe-com",
            version="2024-04-10",
            catalog_api_id="stripe.com",
        ),
    ]
    creds = [
        # Unsplit path in api_version
        CredentialSnapshot(
            id="cred_unsplit_ver",
            name="GH 1",
            created_by="usr_a",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com/main/1.1.4",
        ),
        # Vendor holding domain/sub slugged by write path (#1388 comment)
        CredentialSnapshot(
            id="cred_doubled_vendor",
            name="GH 2",
            created_by="usr_b",
            api_vendor="github-com-api-github-com",
            api_name=None,
            api_version=None,
        ),
        # Filename stem in api_version
        CredentialSnapshot(
            id="cred_spec_json",
            name="Stripe",
            created_by="usr_a",
            api_vendor="stripe-com",
            api_name="stripe-com",
            api_version="openapi.json",
        ),
    ]
    plan = build_repair_plan(apis=apis, credentials=creds)
    by_id = {c.credential_id: c for c in plan.credential_repairs}
    assert not plan.conflicts
    assert (
        by_id["cred_unsplit_ver"].after_vendor,
        by_id["cred_unsplit_ver"].after_name,
        by_id["cred_unsplit_ver"].after_version,
    ) == ("github-com", "api-github-com", "1.1.4")
    assert (
        by_id["cred_doubled_vendor"].after_vendor,
        by_id["cred_doubled_vendor"].after_name,
        by_id["cred_doubled_vendor"].after_version,
    ) == ("github-com", "api-github-com", None)
    assert (
        by_id["cred_spec_json"].after_vendor,
        by_id["cred_spec_json"].after_name,
        by_id["cred_spec_json"].after_version,
    ) == ("stripe-com", "stripe-com", "2024-04-10")


def test_1079_preserves_bare_domain_and_manifest_clash_groups() -> None:
    """Bare-domain entries and genuine sub-name clash groups are never stripped."""
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="asana-com",
            name="asana-com",
            version="1.0",
            catalog_api_id="asana.com",
        ),
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="stripe-com",
            name="stripe-com-checkout",
            version="1.0",
            catalog_api_id="stripe.com/checkout",
        ),
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="stripe-com",
            name="api-stripe-com-checkout",
            version="1.0",
            catalog_api_id="api.stripe.com/checkout",
        ),
    ]
    creds = [
        CredentialSnapshot(
            id="cred_preimport_clean",
            name="Unimported",
            created_by="usr_1",
            api_vendor="linear-app",
            api_name="linear-api",
            api_version=None,
            catalog_api_id="linear.app/linear-api",
        ),
        CredentialSnapshot(
            id="cred_semver_beta",
            name="Beta",
            created_by="usr_1",
            api_vendor="linear-app",
            api_name="linear-api",
            api_version="1.1.4-beta.1",
        ),
    ]
    plan = build_repair_plan(apis=apis, credentials=creds)
    assert plan.api_repairs == ()
    assert plan.credential_repairs == ()
    assert plan.conflicts == ()
    assert plan.unresolvable == ()


def test_separate_owners_and_separate_agents_repair_without_false_collision() -> None:
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
        )
    ]
    creds = [
        CredentialSnapshot(
            id="cred_alice",
            name="GitHub",
            created_by="usr_alice",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
        ),
        CredentialSnapshot(
            id="cred_bob",
            name="GitHub",
            created_by="usr_bob",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
        ),
    ]
    plan = build_repair_plan(
        apis=apis,
        credentials=creds,
        active_bound_agents_by_credential={
            "cred_alice": frozenset({"agnt_alice"}),
            "cred_bob": frozenset({"agnt_bob"}),
        },
    )
    assert len(plan.credential_repairs) == 2
    assert not plan.conflicts


def test_shared_agent_binding_collision_fails_closed() -> None:
    apis = [
        RegistryApiSnapshot(
            id=str(uuid.uuid4()),
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
        )
    ]
    creds = [
        CredentialSnapshot(
            id="cred_malformed",
            name="GitHub A",
            created_by="usr_alice",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
        ),
        CredentialSnapshot(
            id="cred_clean",
            name="GitHub B",
            created_by="usr_bob",
            api_vendor="github-com",
            api_name="api-github-com",
            api_version="1.1.4",
        ),
    ]
    plan = build_repair_plan(
        apis=apis,
        credentials=creds,
        active_bound_agents_by_credential={
            "cred_malformed": frozenset({"agnt_shared"}),
            "cred_clean": frozenset({"agnt_shared"}),
        },
    )
    assert plan.credential_repairs == ()
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].reason == "agent_binding_scope_collision"


def test_open_session_collision_cancels_derived_standalone_repair() -> None:
    """A cancelled API/credential pair cannot leave a session on the cancelled target."""
    api_id = str(uuid.uuid4())
    plan = build_repair_plan(
        apis=[
            RegistryApiSnapshot(
                id=api_id,
                vendor="posthog-com",
                name="posthog-com-posthog-api",
                version="1.0.0",
                catalog_api_id="posthog.com/posthog-api",
            )
        ],
        credentials=[
            CredentialSnapshot(
                id="cred_malformed",
                name="Malformed",
                created_by="usr_1",
                api_vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version=None,
                catalog_api_id="posthog.com/posthog-api",
            )
        ],
        connect_sessions=[
            ConnectSessionSnapshot(
                id="cs_clean_agent_1",
                credential_id="cred_other",
                agent_id="agnt_1",
                initiator_actor_id="agnt_1",
                state="created",
                target_kind="api",
                vendor="posthog-com",
                api_name="posthog-api",
                api_version="1.0.0",
            ),
            ConnectSessionSnapshot(
                id="cs_malformed_agent_1",
                credential_id="cred_malformed",
                agent_id="agnt_1",
                initiator_actor_id="agnt_1",
                state="created",
                target_kind="api",
                vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version="1.0.0",
            ),
            ConnectSessionSnapshot(
                id="cs_standalone_agent_2",
                credential_id="cred_other",
                agent_id="agnt_2",
                initiator_actor_id="agnt_2",
                state="created",
                target_kind="api",
                vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version="1.0.0",
            ),
        ],
    )
    assert plan.api_repairs == ()
    assert plan.credential_repairs == ()
    assert plan.open_session_repairs == ()
    assert {finding.reason for finding in plan.conflicts} == {
        "open_connect_session_collision",
        "dependent_credential_collision",
    }


def test_credential_session_uses_repaired_name_with_wildcard_version() -> None:
    """A vendor-axis repair carries its named, wildcard-version scope to open sessions."""
    plan = build_repair_plan(
        apis=[
            RegistryApiSnapshot(
                id=str(uuid.uuid4()),
                vendor="github-com",
                name="api-github-com",
                version="1.1.4",
                catalog_api_id="github.com/api.github.com",
            )
        ],
        credentials=[
            CredentialSnapshot(
                id="cred_doubled_vendor",
                name="GitHub",
                created_by="usr_1",
                api_vendor="github-com-api-github-com",
                api_name=None,
                api_version=None,
            )
        ],
        connect_sessions=[
            ConnectSessionSnapshot(
                id="cs_wildcard_version",
                credential_id="cred_doubled_vendor",
                agent_id="agnt_1",
                initiator_actor_id="agnt_1",
                state="created",
                target_kind="api",
                vendor="github-com-api-github-com",
                api_name="api.github.com",
                api_version=None,
            )
        ],
    )
    (credential,) = plan.credential_repairs
    assert (credential.after_name, credential.after_version) == ("api-github-com", None)
    (session,) = credential.connect_sessions
    assert (session.after_vendor, session.after_name, session.after_version) == (
        "github-com",
        "api-github-com",
        None,
    )
    assert plan.open_session_repairs == ()


def test_backup_document_validation_rejects_tampered_payloads() -> None:
    doc = build_backup_document(tool_version="0.42.0", apis=(), credentials=())
    parsed = validate_backup_document(doc)
    assert parsed == {"apis": [], "credentials": [], "connect_sessions": []}

    with pytest.raises(IdentityRepairBackupError, match="unsupported backup format"):
        validate_backup_document({**doc, "format": "wrong"})

    with pytest.raises(IdentityRepairBackupError, match="unsupported schema_version"):
        validate_backup_document({**doc, "schema_version": 99})

    bad_counts = {
        **doc,
        "tables": {**doc["tables"], "apis": {"row_count": 2, "rows": []}},
    }
    with pytest.raises(IdentityRepairBackupError, match="row_count"):
        validate_backup_document(bad_counts)


def _exec(db: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(sql, params)
        conn.commit()


def _fetch(db: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def test_cli_dry_run_apply_backup_and_restore_roundtrip(
    sqlite_stack: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pins CLI --dry-run, --report, --backup-out, apply idempotence, and --restore."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    api_id = str(uuid.uuid4())
    _exec(
        sqlite_stack / "registry.db",
        "INSERT INTO apis (id, vendor, name, version, catalog_api_id) VALUES (?, ?, ?, ?, ?)",
        (api_id, "posthog-com", "posthog-com-posthog-api", "1.0.0", "posthog.com/posthog-api"),
    )
    _exec(
        sqlite_stack / "control.db",
        "INSERT INTO credentials "
        "(id, type, name, api_vendor, api_name, api_version, catalog_api_id) "
        "VALUES (?, 'bearer', 'PostHog', 'posthog-com', 'posthog-com-posthog-api', "
        "NULL, 'posthog.com/posthog-api')",
        ("cred_cli_ph",),
    )
    _exec(
        sqlite_stack / "registry.db",
        "INSERT INTO apis (id, vendor, name, version, catalog_api_id) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "github-com", "api-github-com", "1.1.4", "github.com/api.github.com"),
    )
    _exec(
        sqlite_stack / "control.db",
        "INSERT INTO credentials (id, type, name, api_vendor, api_name, api_version) "
        "VALUES (?, 'bearer', 'GH', 'github-com', 'openapi', 'api.github.com')",
        ("cred_cli_gh",),
    )
    capsys.readouterr()

    report_file = tmp_path / "dry-report.jsonl"
    backup_file = tmp_path / "backup.json"

    # Dry-run writes report and backup without mutating DB rows.
    assert (
        cli_mod.main(
            [
                "repair-credential-identities",
                "--dry-run",
                "--report",
                str(report_file),
                "--backup-out",
                str(backup_file),
            ]
        )
        == 0
    )
    assert _fetch(
        sqlite_stack / "registry.db", "SELECT name FROM apis WHERE id = ?", (api_id,)
    ) == [("posthog-com-posthog-api",)]
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT api_name, api_version FROM credentials WHERE id = 'cred_cli_gh'",
    ) == [("openapi", "api.github.com")]
    report_lines = [json.loads(line) for line in report_file.read_text().splitlines()]
    assert {item["category"] for item in report_lines} == {"would_repair"}
    assert len(report_lines) == 3

    # Apply repairs and persist backup.
    assert (
        cli_mod.main(
            [
                "repair-credential-identities",
                "--backup-out",
                str(backup_file),
            ]
        )
        == 0
    )
    assert _fetch(
        sqlite_stack / "registry.db", "SELECT name FROM apis WHERE id = ?", (api_id,)
    ) == [("posthog-api",)]
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT api_name FROM credentials WHERE id = 'cred_cli_ph'",
    ) == [("posthog-api",)]
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT api_name, api_version FROM credentials WHERE id = 'cred_cli_gh'",
    ) == [("api-github-com", "1.1.4")]

    # Rerun is a strict no-op.
    capsys.readouterr()
    assert cli_mod.main(["repair-credential-identities"]) == 0
    out_noop = capsys.readouterr()
    assert out_noop.out == ""
    assert "repaired apis=0, credentials=0" in out_noop.err

    # Restore rolls back both control and registry rows, and rerun is idempotent.
    assert cli_mod.main(["repair-credential-identities", "--restore", str(backup_file)]) == 0
    assert _fetch(
        sqlite_stack / "registry.db", "SELECT name FROM apis WHERE id = ?", (api_id,)
    ) == [("posthog-com-posthog-api",)]
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT api_name FROM credentials WHERE id = 'cred_cli_ph'",
    ) == [("posthog-com-posthog-api",)]
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT api_name, api_version FROM credentials WHERE id = 'cred_cli_gh'",
    ) == [("openapi", "api.github.com")]
    assert cli_mod.main(["repair-credential-identities", "--restore", str(backup_file)]) == 0


def test_migrations_runner_fails_closed_on_collision_and_succeeds_once_resolved(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrations.run exits EXIT_UPGRADE_STEP_FAILED (4) when a collision blocks repair."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _exec(
        sqlite_stack / "registry.db",
        "INSERT INTO apis (id, vendor, name, version, catalog_api_id) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "github-com", "api-github-com", "1.1.4", "github.com/api.github.com"),
    )
    # Malformed + clean credential for the same owner -> collision!
    _exec(
        sqlite_stack / "control.db",
        "INSERT INTO credentials (id, type, name, api_vendor, api_name, api_version, created_by) "
        "VALUES ('cred_bad', 'bearer', 'GH old', 'github-com', 'openapi', "
        "'api.github.com', 'usr_1')",
    )
    _exec(
        sqlite_stack / "control.db",
        "INSERT INTO credentials (id, type, name, api_vendor, api_name, api_version, created_by) "
        "VALUES ('cred_good', 'bearer', 'GH clean', 'github-com', 'api-github-com', "
        "'1.1.4', 'usr_1')",
    )
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    captured = capsys.readouterr()
    assert f"upgrade step {steps_mod.CREDENTIAL_IDENTITY_REPAIR}: failed" in captured.out
    assert "cred_bad" in captured.err
    assert "cred_good" in captured.err
    # Neither row mutated.
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT id, api_name, api_version FROM credentials ORDER BY id",
    ) == [
        ("cred_bad", "openapi", "api.github.com"),
        ("cred_good", "api-github-com", "1.1.4"),
    ]

    # Once the operator removes the redundant clean row, re-running succeeds.
    _exec(sqlite_stack / "control.db", "DELETE FROM credentials WHERE id = 'cred_good'")
    assert run_mod.main([]) == 0
    assert _fetch(
        sqlite_stack / "control.db",
        "SELECT id, api_name, api_version FROM credentials",
    ) == [("cred_bad", "api-github-com", "1.1.4")]


def test_reference_counts_serialize_cleanly() -> None:
    api_counts = ApiReferenceCounts(
        revisions=2, operations=10, overlays=1, notes=3, execution_records=5
    )
    cred_counts = CredentialReferenceCounts(
        active_bindings=1, permission_rules=4, execution_records=5
    )
    assert api_counts.as_dict()["overlays"] == 1
    assert cred_counts.as_dict()["permission_rules"] == 4
