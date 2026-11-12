"""Unit and real-SQLite tests for EncryptionService and persisted key rotation."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Table, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateTable

import jentic_one.admin.core.schema
import jentic_one.control.core.schema  # noqa: F401
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.provider_configs import ProviderConfigRecord
from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.shared.config import ConfigError, DatabaseConfig, EncryptionConfig, EncryptionKey
from jentic_one.shared.crypto import (
    DecryptionError,
    EncryptedStorageRotator,
    EncryptionService,
    KeyRetirementError,
    KeyRotationReport,
    RotationFailure,
    assert_only_active_key,
    rotate_encrypted_storage,
    verify_key_retired,
)
from jentic_one.shared.db import AdminBase, ControlBase, DatabaseSession


def _make_key(key_id: str = "v1") -> EncryptionKey:
    material = base64.b64encode(os.urandom(32)).decode()
    return EncryptionKey(id=key_id, material=SecretStr(material))


def _make_config(key_id: str = "v1", keys: list[EncryptionKey] | None = None) -> EncryptionConfig:
    if keys is None:
        keys = [_make_key(key_id)]
    return EncryptionConfig(active_id=key_id, entries=keys)


# --- Round-trip ---


def test_encrypt_decrypt_roundtrip():
    svc = EncryptionService(_make_config())
    plaintext = "super-secret-token-value"
    blob = svc.encrypt(plaintext)
    assert svc.decrypt(blob) == plaintext


def test_roundtrip_unicode():
    svc = EncryptionService(_make_config())
    plaintext = "пароль-密码-🔑"
    assert svc.decrypt(svc.encrypt(plaintext)) == plaintext


def test_roundtrip_empty_string():
    svc = EncryptionService(_make_config())
    assert svc.decrypt(svc.encrypt("")) == ""


# --- Blob format ---


def test_blob_has_key_id_prefix():
    svc = EncryptionService(_make_config(key_id="v2", keys=[_make_key("v2")]))
    blob = svc.encrypt("hello")
    assert blob.startswith("v2:")


def test_blob_is_nondeterministic():
    svc = EncryptionService(_make_config())
    blob1 = svc.encrypt("same")
    blob2 = svc.encrypt("same")
    assert blob1 != blob2


# --- Decryption errors ---


def test_tampered_blob_raises():
    svc = EncryptionService(_make_config())
    blob = svc.encrypt("secret")
    parts = blob.split(":", 1)
    payload = list(parts[1])
    payload[5] = "A" if payload[5] != "A" else "B"
    tampered = parts[0] + ":" + "".join(payload)
    with pytest.raises(DecryptionError):
        svc.decrypt(tampered)


def test_unknown_key_id_raises():
    svc = EncryptionService(_make_config())
    with pytest.raises(DecryptionError, match="Unknown key_id"):
        svc.decrypt("nonexistent_key:AAAA")


def test_missing_separator_raises():
    svc = EncryptionService(_make_config())
    with pytest.raises(DecryptionError, match="missing key_id separator"):
        svc.decrypt("nocolonhere")


def test_short_payload_raises():
    svc = EncryptionService(_make_config())
    short = base64.b64encode(b"short").decode()
    with pytest.raises(DecryptionError):
        svc.decrypt(f"v1:{short}")


# --- Multi-key rotation & EncryptionService helpers ---


def test_old_key_blobs_still_decrypt():
    old_key = _make_key("v1")
    new_key = _make_key("v2")

    old_svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[old_key]))
    old_blob = old_svc.encrypt("legacy-secret")

    rotated_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    assert rotated_svc.decrypt(old_blob) == "legacy-secret"


def test_new_writes_use_current_key():
    old_key = _make_key("v1")
    new_key = _make_key("v2")

    svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    blob = svc.encrypt("new-secret")
    assert blob.startswith("v2:")


def test_key_id_of_and_key_metadata():
    old_key = _make_key("v1")
    new_key = _make_key("v2")
    svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    assert svc.active_key_id == "v2"
    assert svc.key_ids == ("v1", "v2")

    blob = svc.encrypt("secret")
    assert EncryptionService.key_id_of(blob) == "v2"

    with pytest.raises(DecryptionError, match="missing key_id separator"):
        EncryptionService.key_id_of("malformed")
    with pytest.raises(DecryptionError, match="empty key_id"):
        EncryptionService.key_id_of(":payload")


def test_needs_reencryption_and_reencrypt():
    old_key = _make_key("v1")
    new_key = _make_key("v2")

    v1_svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[old_key]))
    v1_blob = v1_svc.encrypt("rotate-me")

    v2_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    assert v2_svc.needs_reencryption(v1_blob) is True

    rotated_blob, changed = v2_svc.reencrypt(v1_blob)
    assert changed is True
    assert rotated_blob.startswith("v2:")
    assert v2_svc.needs_reencryption(rotated_blob) is False
    assert v2_svc.decrypt(rotated_blob) == "rotate-me"

    # Re-encrypting an already-current ciphertext is a no-op that still validates auth tag.
    noop_blob, changed_again = v2_svc.reencrypt(rotated_blob)
    assert changed_again is False
    assert noop_blob == rotated_blob

    # Tampered active-key blob still fails closed during reencrypt.
    parts = rotated_blob.split(":", 1)
    tampered = parts[0] + ":" + ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
    with pytest.raises(DecryptionError):
        v2_svc.reencrypt(tampered)


# --- Preview ---


def test_preview_long_string():
    assert EncryptionService.preview("my-api-token") == "…ken"


def test_preview_short_string_masked():
    assert EncryptionService.preview("ab") == "…***"


def test_preview_exactly_three_masked():
    assert EncryptionService.preview("abc") == "…***"


def test_preview_four_chars():
    assert EncryptionService.preview("abcd") == "…bcd"


# --- Boot validation ---


def test_empty_entries_raises():
    cfg = EncryptionConfig(active_id="v1", entries=[])
    with pytest.raises(ConfigError, match="must not be empty"):
        EncryptionService(cfg)


def test_active_id_not_in_entries_raises():
    cfg = EncryptionConfig(active_id="missing", entries=[_make_key("v1")])
    with pytest.raises(ConfigError, match="not found in entries"):
        EncryptionService(cfg)


def test_short_key_material_raises():
    short_material = base64.b64encode(b"tooshort").decode()
    entry = EncryptionKey(id="v1", material=SecretStr(short_material))
    cfg = EncryptionConfig(active_id="v1", entries=[entry])
    with pytest.raises(ConfigError, match="must be 32 bytes"):
        EncryptionService(cfg)


def test_duplicate_key_id_raises():
    key1 = _make_key("v1")
    key2 = _make_key("v1")
    cfg = EncryptionConfig(active_id="v1", entries=[key1, key2])
    with pytest.raises(ConfigError, match="Duplicate encryption key id"):
        EncryptionService(cfg)


def test_key_id_with_colon_rejected():
    with pytest.raises(ValidationError, match="key id must match"):
        EncryptionKey(id="aws:v1", material=SecretStr("dummy"))


def test_key_id_with_space_rejected():
    with pytest.raises(ValidationError, match="key id must match"):
        EncryptionKey(id="key 1", material=SecretStr("dummy"))


# --- Real-SQLite storage rotation & key retirement verification ---


def _create_sqlite_tables(sync_conn: Connection, *tables: Table) -> None:
    """Create ORM tables on SQLite, temporarily stripping Postgres-only server defaults."""
    for table in tables:
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


async def _make_sqlite_dbs(tmp_path: Path) -> tuple[DatabaseSession, DatabaseSession]:
    control_db = DatabaseSession(
        DatabaseConfig(backend="sqlite", path=str(tmp_path / "control.db"))
    )
    admin_db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")))
    await control_db.connect()
    await admin_db.connect()
    async with control_db.engine.begin() as conn:
        await conn.run_sync(_create_sqlite_tables, *ControlBase.metadata.sorted_tables)
    async with admin_db.engine.begin() as conn:
        await conn.run_sync(_create_sqlite_tables, *AdminBase.metadata.sorted_tables)
    return control_db, admin_db


async def _seed_all_encrypted_families(
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    v1_svc: EncryptionService,
) -> None:
    async with control_db.transaction() as session:
        # 1. customer_api_keys
        c_apikey = Credential(id="cred_apikey", type="api_key", name="k1", api_vendor="acme")
        session.add(c_apikey)
        session.add(
            CustomerAPIKey(
                id="cak_1",
                credential_id=c_apikey.id,
                encrypted_key=v1_svc.encrypt("apikey-secret-1"),
            )
        )

        # 2. token_value_credentials
        c_bearer = Credential(id="cred_bearer", type="bearer", name="k2", api_vendor="acme")
        session.add(c_bearer)
        session.add(
            TokenValueCredential(
                credential_id=c_bearer.id,
                encrypted_token_value=v1_svc.encrypt("bearer-secret-1"),
            )
        )

        # 3. basic_credentials
        c_basic = Credential(id="cred_basic", type="basic", name="k3", api_vendor="acme")
        session.add(c_basic)
        session.add(
            BasicCredential(
                credential_id=c_basic.id,
                username="alice",
                encrypted_password=v1_svc.encrypt("basic-password-1"),
            )
        )

        # 4. sigv4_credentials (2 encrypted columns)
        c_sigv4 = Credential(id="cred_sigv4", type="sigv4", name="k4", api_vendor="aws")
        session.add(c_sigv4)
        session.add(
            Sigv4Credential(
                id="sigv4_1",
                credential_id=c_sigv4.id,
                access_key_id="AKIAIOSFODNN7EXAMPLE",
                encrypted_secret_access_key=v1_svc.encrypt("sigv4-secret-key"),
                encrypted_session_token=v1_svc.encrypt("sigv4-session-token"),
                region="us-east-1",
                service="s3",
            )
        )

        # 5. device_authorization_credentials
        c_device = Credential(id="cred_device", type="device", name="k5", api_vendor="github")
        session.add(c_device)
        session.add(
            DeviceAuthorizationCredential(
                id=c_device.id,
                client_id="dev-client",
                token_url="https://example.com/token",
                authorization_endpoint="https://example.com/device",
                encrypted_device_code=v1_svc.encrypt("device-code-secret"),
            )
        )

        # 6. oauth_client_credentials
        c_oauth_client = Credential(
            id="cred_oauth_client", type="oauth2", name="k6", api_vendor="slack"
        )
        session.add(c_oauth_client)
        session.add(
            OAuthClientCredential(
                id=c_oauth_client.id,
                token_url="https://example.com/oauth/token",
                client_id="oauth-client",
                encrypted_client_secret=v1_svc.encrypt("oauth-client-secret"),
            )
        )

        # 7. authorization_code_app_registration_details
        reg = OAuthAppRegistration(
            id="oar_1",
            name="Acme OAuth App",
            api_vendor="acme",
            flow_kind="authorization_code",
            client_id="reg-client-id",
            catalog_api_id="acme/core",
            display_name="Acme",
        )
        session.add(reg)
        session.add(
            AuthorizationCodeAppRegistrationDetails(
                id=reg.id,
                encrypted_client_secret=v1_svc.encrypt("app-reg-client-secret"),
                authorize_url="https://example.com/authorize",
                token_url="https://example.com/token",
            )
        )

        # 8. oauth_tokens (2 encrypted columns)
        c_oauth_tok = Credential(id="cred_oauth_tok", type="oauth2", name="k8", api_vendor="acme")
        session.add(c_oauth_tok)
        session.add(
            OAuthToken(
                id="oat_1",
                credential_id=c_oauth_tok.id,
                encrypted_access_token=v1_svc.encrypt("oauth-access-token"),
                encrypted_refresh_token=v1_svc.encrypt("oauth-refresh-token"),
            )
        )

    async with admin_db.transaction() as session:
        # 9. provider_configs
        session.add(
            ProviderConfigRecord(
                id="pcfg_1",
                name="auth0-prod",
                config_json={
                    "issuer": "https://auth.example.com",
                    "client_secret": v1_svc.encrypt("provider-client-secret"),
                },
            )
        )

        # 10. jobs (async execution job with encrypted inbound cookie)
        session.add(
            Job(
                id="job_async_1",
                kind="execution",
                status="queued",
                actor_type="agent",
                attempts=0,
                payload={
                    "operation_id": "op_1",
                    "inbound_cookie_enc": v1_svc.encrypt("async-job-cookie"),
                },
            )
        )

        # 11. held_executions (held job with _enc envelope + nested inbound_cookie_enc)
        held_inner = {
            "method": "POST",
            "path": "/v1/Charges",
            "inbound_cookie_enc": v1_svc.encrypt("held-nested-cookie"),
        }
        held_job = Job(
            id="job_held_1",
            kind="execution",
            status="held",
            actor_type="agent",
            attempts=0,
            payload={"_enc": v1_svc.encrypt(json.dumps(held_inner))},
        )
        session.add(held_job)
        await session.flush()
        session.add(
            HeldExecution(
                id="held_1",
                job_id=held_job.id,
                agent_id="agt_1",
                credential_id="cred_apikey",
                api_vendor="acme",
                api_name="payments",
                api_version="v1",
                method="POST",
                path="/v1/Charges",
                request_fingerprint="fp_1",
                state="pending",
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            )
        )


async def test_rotate_encrypted_storage_all_families_dry_run_resume_and_retire(
    tmp_path: Path,
) -> None:
    old_key = _make_key("v1")
    new_key = _make_key("v2")
    v1_svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[old_key]))
    v2_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    v2_only_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[new_key]))

    control_db, admin_db = await _make_sqlite_dbs(tmp_path)
    try:
        await _seed_all_encrypted_families(control_db, admin_db, v1_svc)

        # 1. Dry-run inventories all 11 families (13 total encrypted values across 11 rows)
        dry_report = await rotate_encrypted_storage(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            dry_run=True,
            batch_size=2,
        )
        assert isinstance(dry_report, KeyRotationReport)
        assert dry_report.dry_run is True
        assert dry_report.batches_executed == 0
        assert dry_report.scanned_rows == 11
        assert dry_report.scanned_values == 13
        assert dry_report.rotated_rows == 0
        assert dry_report.rotated_values == 0
        assert dry_report.needs_rotation_values == 13
        assert dry_report.remaining_non_active_values == 13
        assert dry_report.counts_by_key_before == {"v1": 13}
        assert dry_report.counts_by_key_after == {"v1": 13}
        assert dry_report.completed is False

        # All table and family aliases resolve in by_family and by_table
        for family_or_table, expected_values in (
            ("customer_api_keys", 1),
            ("token_value_credentials", 1),
            ("bearer_token_credentials", 1),
            ("basic_credentials", 1),
            ("basic_auth_credentials", 1),
            ("sigv4_credentials", 2),
            ("device_authorization_credentials", 1),
            ("oauth_client_credentials", 1),
            ("authorization_code_app_registration_details", 1),
            ("oauth_app_registrations", 1),
            ("oauth_tokens", 2),
            ("provider_configs", 1),
            ("jobs", 1),
            ("held_executions", 1),
        ):
            assert dry_report.by_family[family_or_table].scanned_values == expected_values
            assert dry_report.by_table[family_or_table].needs_rotation_values == expected_values

        # 2. verify_key_retired and assert_only_active_key fail closed while v1 ciphertexts remain
        with pytest.raises(KeyRetirementError) as exc_info:
            await verify_key_retired(
                v2_svc,
                "v1",
                control_db=control_db,
                admin_db=admin_db,
            )
        assert exc_info.value.remaining_by_key == {"v1": 13}
        assert len(exc_info.value.remaining_by_family) == 11

        with pytest.raises(KeyRetirementError):
            await assert_only_active_key(
                v2_svc,
                control_db=control_db,
                admin_db=admin_db,
            )

        # Retiring the currently active key also fails closed
        with pytest.raises(KeyRetirementError, match="still the active encryption key"):
            dry_report.verify_key_retired("v2")

        # 3. Partial run with max_batches=4 rotates only 4 rows and leaves 7 rows for resume
        partial_report = await rotate_encrypted_storage(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            dry_run=False,
            batch_size=1,
            max_batches=4,
        )
        assert partial_report.batches_executed == 4
        assert partial_report.rotated_rows == 4
        assert partial_report.rotated_values == 5  # includes sigv4's 2 columns
        assert partial_report.remaining_non_active_values == 8
        assert partial_report.completed is False

        with pytest.raises(KeyRetirementError) as partial_exc:
            await verify_key_retired(
                v2_svc,
                "v1",
                control_db=control_db,
                admin_db=admin_db,
            )
        assert partial_exc.value.remaining_by_key == {"v1": 8}

        # 4. Resume rotation to completion using EncryptedStorageRotator
        rotator = EncryptedStorageRotator(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            batch_size=2,
        )
        resumed_report = await rotator.rotate(dry_run=False)
        assert resumed_report.completed is True
        assert resumed_report.rotated_rows == 7
        assert resumed_report.rotated_values == 8
        assert resumed_report.remaining_non_active_values == 0
        assert resumed_report.counts_by_key_before == {"v2": 5, "v1": 8}
        assert resumed_report.counts_by_key_after == {"v2": 13}

        # 5. Retirement checks now pass and v2_only_svc (without v1) decrypts every stored secret
        retired_report = await rotator.verify_key_retired("v1")
        assert retired_report.completed is True
        active_only_report = await rotator.assert_only_active_key()
        assert active_only_report.counts_by_key == {"v2": 13}

        # Verify v2_only_svc (old key removed from entries) passes assert_only_active_key
        final_check = await assert_only_active_key(
            v2_only_svc,
            control_db=control_db,
            admin_db=admin_db,
        )
        assert final_check.completed is True
        assert final_check.failures == []

        # Verify nested held_executions payload decrypts cleanly with v2_only_svc
        async with admin_db.session() as session:
            held_payload_raw = (
                await session.execute(text("SELECT payload FROM jobs WHERE id = 'job_held_1'"))
            ).scalar_one()
            held_payload = (
                json.loads(held_payload_raw)
                if isinstance(held_payload_raw, str)
                else held_payload_raw
            )
            inner_decrypted = json.loads(v2_only_svc.decrypt(held_payload["_enc"]))
            assert v2_only_svc.decrypt(inner_decrypted["inbound_cookie_enc"]) == (
                "held-nested-cookie"
            )
    finally:
        await control_db.close()
        await admin_db.close()


async def test_verify_key_retired_fails_closed_on_corrupted_ciphertext(
    tmp_path: Path,
) -> None:
    key_v1 = _make_key("v1")
    svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[key_v1]))

    control_db, admin_db = await _make_sqlite_dbs(tmp_path)
    try:
        async with control_db.transaction() as session:
            cred = Credential(id="cred_bad", type="api_key", name="bad", api_vendor="acme")
            session.add(cred)
            session.add(
                CustomerAPIKey(
                    id="cak_bad",
                    credential_id=cred.id,
                    encrypted_key="v1:not-valid-ciphertext",
                )
            )

        with pytest.raises(KeyRetirementError) as exc_info:
            await assert_only_active_key(svc, control_db=control_db, admin_db=admin_db)

        assert len(exc_info.value.failures) == 1
        failure = exc_info.value.failures[0]
        assert isinstance(failure, RotationFailure)
        assert failure.family == "customer_api_keys"
        assert failure.column == "encrypted_key"
    finally:
        await control_db.close()
        await admin_db.close()
