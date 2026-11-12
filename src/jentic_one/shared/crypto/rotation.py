"""Database re-encryption and key-retirement verification across persisted storage.

Covers every persisted :class:`~jentic_one.shared.crypto.encryption.EncryptionService`
consumer in the control and admin databases:

- ``control_db``:
  - ``customer_api_keys`` (``encrypted_key``)
  - ``token_value_credentials`` / ``bearer_token_credentials`` (``encrypted_token_value``)
  - ``basic_credentials`` / ``basic_auth_credentials`` (``encrypted_password``)
  - ``sigv4_credentials`` (``encrypted_secret_access_key``, ``encrypted_session_token``)
  - ``device_authorization_credentials`` (``encrypted_device_code``)
  - ``oauth_client_credentials`` (``encrypted_client_secret``)
  - ``authorization_code_app_registration_details`` / ``oauth_app_registrations``
    (``encrypted_client_secret``)
  - ``oauth_tokens`` (``encrypted_access_token``, ``encrypted_refresh_token``)
- ``admin_db``:
  - ``provider_configs`` (``config_json["client_secret"]``)
  - ``jobs`` (async execution ``payload["inbound_cookie_enc"]``)
  - ``held_executions`` (held job ``payload["_enc"]`` and nested ``inbound_cookie_enc``)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError, ProgrammingError

from jentic_one.shared.crypto.encryption import DecryptionError, EncryptionService
from jentic_one.shared.db import DatabaseSession

_ENCRYPTED_PAYLOAD_KEY = "_enc"
_INBOUND_COOKIE_PAYLOAD_KEY = "inbound_cookie_enc"
_PROVIDER_SECRET_FIELDS: tuple[str, ...] = ("client_secret",)
_MALFORMED_KEY_ID = "<malformed>"

#: Bidirectional aliases between logical family names and SQL table names.
_FAMILY_ALIASES: dict[str, str] = {
    "token_value_credentials": "bearer_token_credentials",
    "bearer_token_credentials": "bearer_token_credentials",
    "basic_credentials": "basic_auth_credentials",
    "basic_auth_credentials": "basic_auth_credentials",
    "authorization_code_app_registration_details": "oauth_app_registrations",
    "oauth_app_registrations": "oauth_app_registrations",
}


@dataclass(frozen=True, slots=True)
class _ColumnSpec:
    database: str
    family: str
    table: str
    columns: tuple[str, ...]


_CONTROL_COLUMN_SPECS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec(
        database="control",
        family="customer_api_keys",
        table="customer_api_keys",
        columns=("encrypted_key",),
    ),
    _ColumnSpec(
        database="control",
        family="bearer_token_credentials",
        table="token_value_credentials",
        columns=("encrypted_token_value",),
    ),
    _ColumnSpec(
        database="control",
        family="basic_auth_credentials",
        table="basic_credentials",
        columns=("encrypted_password",),
    ),
    _ColumnSpec(
        database="control",
        family="sigv4_credentials",
        table="sigv4_credentials",
        columns=("encrypted_secret_access_key", "encrypted_session_token"),
    ),
    _ColumnSpec(
        database="control",
        family="device_authorization_credentials",
        table="device_authorization_credentials",
        columns=("encrypted_device_code",),
    ),
    _ColumnSpec(
        database="control",
        family="oauth_client_credentials",
        table="oauth_client_credentials",
        columns=("encrypted_client_secret",),
    ),
    _ColumnSpec(
        database="control",
        family="oauth_app_registrations",
        table="authorization_code_app_registration_details",
        columns=("encrypted_client_secret",),
    ),
    _ColumnSpec(
        database="control",
        family="oauth_tokens",
        table="oauth_tokens",
        columns=("encrypted_access_token", "encrypted_refresh_token"),
    ),
)


@dataclass(frozen=True, slots=True)
class RotationFailure:
    """One persisted ciphertext or payload that failed inspection or re-encryption."""

    database: str
    family: str
    table: str
    row_id: str
    column: str
    key_id: str | None
    error: str


@dataclass(slots=True)
class FamilyRotationStats:
    """Per-storage-family encryption inventory and rotation counters."""

    database: str
    family: str
    table: str
    scanned_rows: int = 0
    scanned_values: int = 0
    rotated_rows: int = 0
    rotated_values: int = 0
    needs_rotation_values: int = 0
    remaining_non_active_values: int = 0
    counts_by_key_before: dict[str, int] = field(default_factory=dict)
    counts_by_key_after: dict[str, int] = field(default_factory=dict)
    failures: list[RotationFailure] = field(default_factory=list)

    @property
    def counts_by_key(self) -> dict[str, int]:
        """Key distribution across this family after the scan/rotation pass."""
        return self.counts_by_key_after


class FamilyStatsMap(dict[str, FamilyRotationStats]):
    """Dictionary of :class:`FamilyRotationStats` that resolves both family and table names."""

    def _canonical(self, key: object) -> object:
        if isinstance(key, str):
            return _FAMILY_ALIASES.get(key, key)
        return key

    def __getitem__(self, key: str) -> FamilyRotationStats:
        return super().__getitem__(str(self._canonical(key)))

    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._canonical(key))

    def get(  # type: ignore[override]
        self, key: str, default: FamilyRotationStats | None = None
    ) -> FamilyRotationStats | None:
        return super().get(str(self._canonical(key)), default)


class KeyRetirementError(DecryptionError):
    """Raised when a key retirement or active-key-only check fails closed."""

    def __init__(
        self,
        message: str,
        *,
        report: RotationReport,
        remaining_by_key: dict[str, int],
        remaining_by_family: dict[str, dict[str, int]],
    ) -> None:
        super().__init__(message)
        self.report = report
        self.remaining_by_key = remaining_by_key
        self.remaining_by_family = remaining_by_family
        self.failures = tuple(report.failures)


@dataclass(slots=True)
class RotationReport:
    """Aggregate report of a dry-run inventory or bounded-batch re-encryption pass."""

    active_key_id: str
    dry_run: bool
    batch_size: int
    batches_executed: int = 0
    scanned_rows: int = 0
    scanned_values: int = 0
    rotated_rows: int = 0
    rotated_values: int = 0
    needs_rotation_values: int = 0
    remaining_non_active_values: int = 0
    counts_by_key_before: dict[str, int] = field(default_factory=dict)
    counts_by_key_after: dict[str, int] = field(default_factory=dict)
    by_family: FamilyStatsMap = field(default_factory=FamilyStatsMap)
    by_table: dict[str, FamilyRotationStats] = field(default_factory=dict)
    failures: list[RotationFailure] = field(default_factory=list)

    @property
    def counts_by_key(self) -> dict[str, int]:
        """Key distribution across all scanned storage after this pass."""
        return self.counts_by_key_after

    @property
    def completed(self) -> bool:
        """True when no non-active ciphertexts and no failures remain."""
        return self.remaining_non_active_values == 0 and not self.failures

    def verify_key_retired(self, retired_key_id: str | None = None) -> None:
        """Fail closed if *retired_key_id* (or any non-active key when None) remains in storage."""
        if retired_key_id is None:
            self.assert_only_active_key()
            return
        if retired_key_id == self.active_key_id:
            raise KeyRetirementError(
                f"Key '{retired_key_id}' is still the active encryption key and cannot be retired",
                report=self,
                remaining_by_key={retired_key_id: self.counts_by_key_after.get(retired_key_id, 0)},
                remaining_by_family=_remaining_by_family(self, {retired_key_id}),
            )
        remaining_count = self.counts_by_key_after.get(retired_key_id, 0)
        if remaining_count > 0 or self.failures:
            remaining_by_key = {retired_key_id: remaining_count} if remaining_count > 0 else {}
            if _MALFORMED_KEY_ID in self.counts_by_key_after:
                remaining_by_key[_MALFORMED_KEY_ID] = self.counts_by_key_after[_MALFORMED_KEY_ID]
            raise KeyRetirementError(
                f"Key '{retired_key_id}' cannot be retired: {remaining_count} ciphertext(s) "
                f"still reference '{retired_key_id}' and {len(self.failures)} failure(s) occurred",
                report=self,
                remaining_by_key=remaining_by_key,
                remaining_by_family=_remaining_by_family(self, {retired_key_id, _MALFORMED_KEY_ID}),
            )

    def assert_only_active_key(self) -> None:
        """Fail closed if any persisted ciphertext uses a key other than :attr:`active_key_id`."""
        offending_keys = {
            k: count
            for k, count in self.counts_by_key_after.items()
            if k != self.active_key_id and count > 0
        }
        if offending_keys or self.failures:
            raise KeyRetirementError(
                f"Storage still contains non-active key ciphertext(s) {offending_keys} "
                f"or {len(self.failures)} failure(s)",
                report=self,
                remaining_by_key=offending_keys,
                remaining_by_family=_remaining_by_family(self, set(offending_keys.keys())),
            )


def _remaining_by_family(
    report: RotationReport, target_keys: set[str]
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for family, stats in report.by_family.items():
        matched = {
            k: count
            for k, count in stats.counts_by_key_after.items()
            if k in target_keys and count > 0
        }
        if matched:
            out[family] = matched
    return out


KeyRotationReport = RotationReport


def _inc(mapping: dict[str, int], key: str, delta: int = 1) -> None:
    mapping[key] = mapping.get(key, 0) + delta


def _safe_key_id(encryption: EncryptionService, blob: str) -> str | None:
    try:
        return encryption.key_id_of(blob)
    except DecryptionError:
        return None


def _decode_json_dict(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        if not raw.strip():
            return None
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("expected JSON object")
    raise ValueError(f"unsupported JSON column value type: {type(raw).__name__}")


@dataclass(slots=True)
class _RowCandidate:
    row_id: Any
    update_params: dict[str, Any] | None
    values_scanned: int
    values_needing_rotation: int
    before_keys: list[str]
    after_keys_if_written: list[str]
    after_keys_if_skipped: list[str]
    failures: list[RotationFailure]
    original_values: dict[str, Any] = field(default_factory=dict)


def _apply_candidate_stats(
    report: RotationReport,
    stats: FamilyRotationStats,
    candidate: _RowCandidate,
    *,
    written: bool,
) -> None:
    stats.scanned_rows += 1
    report.scanned_rows += 1
    stats.scanned_values += candidate.values_scanned
    report.scanned_values += candidate.values_scanned
    stats.needs_rotation_values += candidate.values_needing_rotation
    report.needs_rotation_values += candidate.values_needing_rotation

    for k in candidate.before_keys:
        _inc(stats.counts_by_key_before, k)
        _inc(report.counts_by_key_before, k)

    after_keys = candidate.after_keys_if_written if written else candidate.after_keys_if_skipped
    for k in after_keys:
        _inc(stats.counts_by_key_after, k)
        _inc(report.counts_by_key_after, k)

    if written and candidate.values_needing_rotation > 0:
        stats.rotated_rows += 1
        report.rotated_rows += 1
        stats.rotated_values += candidate.values_needing_rotation
        report.rotated_values += candidate.values_needing_rotation
    else:
        stats.remaining_non_active_values += candidate.values_needing_rotation
        report.remaining_non_active_values += candidate.values_needing_rotation

    if candidate.failures:
        stats.failures.extend(candidate.failures)
        report.failures.extend(candidate.failures)


async def _flush_write_batch(
    db: DatabaseSession,
    update_sql: str,
    pending: list[_RowCandidate],
    report: RotationReport,
    stats: FamilyRotationStats,
    *,
    dry_run: bool,
    max_batches: int | None,
) -> None:
    if not pending:
        return
    can_write = (not dry_run) and (max_batches is None or report.batches_executed < max_batches)
    if can_write:
        async with db.transaction() as session:
            for item in pending:
                assert item.update_params is not None
                predicates = " AND ".join(
                    f"(CAST({column} AS TEXT) = CAST(:original_{column} AS TEXT) "
                    f"OR ({column} IS NULL AND :original_{column} IS NULL))"
                    for column in item.original_values
                )
                if not predicates:
                    raise ValueError("rotation update has no optimistic-concurrency predicate")
                stmt = text(f"{update_sql} AND {predicates}")
                params = {
                    **item.update_params,
                    **{
                        f"original_{column}": value
                        for column, value in item.original_values.items()
                    },
                }
                result = await session.execute(stmt, params)
                written = isinstance(result, CursorResult) and result.rowcount == 1
                if not written:
                    item.failures.append(
                        RotationFailure(
                            database=stats.database,
                            family=stats.family,
                            table=stats.table,
                            row_id=str(item.row_id),
                            column=", ".join(item.original_values),
                            key_id=None,
                            error="row changed during rotation; rerun to retry",
                        )
                    )
                _apply_candidate_stats(report, stats, item, written=written)
        report.batches_executed += 1
    else:
        for item in pending:
            _apply_candidate_stats(report, stats, item, written=False)
    pending.clear()


async def _rotate_column_spec(
    db: DatabaseSession,
    encryption: EncryptionService,
    spec: _ColumnSpec,
    report: RotationReport,
    *,
    dry_run: bool,
    batch_size: int,
    max_batches: int | None,
) -> None:
    stats = report.by_family[spec.family]
    col_list = ", ".join(spec.columns)
    first_query = text(f"SELECT id, {col_list} FROM {spec.table} ORDER BY id ASC LIMIT :limit")
    paged_query = text(
        f"SELECT id, {col_list} FROM {spec.table} WHERE id > :after_id ORDER BY id ASC LIMIT :limit"
    )
    set_clause = ", ".join(f"{col} = :{col}" for col in spec.columns)
    update_sql = f"UPDATE {spec.table} SET {set_clause} WHERE id = :id"

    after_id: Any = None
    pending: list[_RowCandidate] = []

    while True:
        async with db.session() as session:
            if after_id is None:
                rows = (await session.execute(first_query, {"limit": batch_size})).all()
            else:
                rows = (
                    await session.execute(paged_query, {"after_id": after_id, "limit": batch_size})
                ).all()
        if not rows:
            break
        after_id = rows[-1][0]

        for row in rows:
            row_id = row[0]
            before_keys: list[str] = []
            after_written: list[str] = []
            after_skipped: list[str] = []
            failures: list[RotationFailure] = []
            updated_cols: dict[str, Any] = {}
            values_scanned = 0
            values_needing_rotation = 0
            row_failed = False

            for idx, col in enumerate(spec.columns, start=1):
                raw_val = row[idx]
                if raw_val is None or raw_val == "":
                    updated_cols[col] = raw_val
                    continue
                blob = str(raw_val)
                values_scanned += 1
                key_id = _safe_key_id(encryption, blob)
                recorded_key = key_id if key_id is not None else _MALFORMED_KEY_ID
                before_keys.append(recorded_key)

                try:
                    new_blob, changed = encryption.reencrypt(blob)
                except DecryptionError as exc:
                    row_failed = True
                    if recorded_key != encryption.active_key_id:
                        values_needing_rotation += 1
                    after_written.append(recorded_key)
                    after_skipped.append(recorded_key)
                    updated_cols[col] = blob
                    failures.append(
                        RotationFailure(
                            database=spec.database,
                            family=spec.family,
                            table=spec.table,
                            row_id=str(row_id),
                            column=col,
                            key_id=key_id,
                            error=str(exc),
                        )
                    )
                else:
                    updated_cols[col] = new_blob
                    if changed:
                        values_needing_rotation += 1
                        after_written.append(encryption.active_key_id)
                        after_skipped.append(recorded_key)
                    else:
                        after_written.append(recorded_key)
                        after_skipped.append(recorded_key)

            candidate = _RowCandidate(
                row_id=row_id,
                update_params=(
                    {"id": row_id, **updated_cols}
                    if (values_needing_rotation > 0 and not row_failed)
                    else None
                ),
                original_values={col: row[idx] for idx, col in enumerate(spec.columns, start=1)},
                values_scanned=values_scanned,
                values_needing_rotation=values_needing_rotation,
                before_keys=before_keys,
                after_keys_if_written=after_written,
                after_keys_if_skipped=after_skipped,
                failures=failures,
            )
            if candidate.update_params is not None:
                pending.append(candidate)
                if len(pending) >= batch_size:
                    await _flush_write_batch(
                        db,
                        update_sql,
                        pending,
                        report,
                        stats,
                        dry_run=dry_run,
                        max_batches=max_batches,
                    )
            else:
                _apply_candidate_stats(report, stats, candidate, written=False)

        if len(rows) < batch_size:
            break

    await _flush_write_batch(
        db,
        update_sql,
        pending,
        report,
        stats,
        dry_run=dry_run,
        max_batches=max_batches,
    )


async def _rotate_provider_configs(
    admin_db: DatabaseSession,
    encryption: EncryptionService,
    report: RotationReport,
    *,
    dry_run: bool,
    batch_size: int,
    max_batches: int | None,
) -> None:
    stats = report.by_family["provider_configs"]
    first_query = text("SELECT id, config_json FROM provider_configs ORDER BY id ASC LIMIT :limit")
    paged_query = text(
        "SELECT id, config_json FROM provider_configs "
        "WHERE id > :after_id ORDER BY id ASC LIMIT :limit"
    )
    update_sql = "UPDATE provider_configs SET config_json = :config_json WHERE id = :id"

    after_id: Any = None
    pending: list[_RowCandidate] = []

    while True:
        async with admin_db.session() as session:
            if after_id is None:
                rows = (await session.execute(first_query, {"limit": batch_size})).all()
            else:
                rows = (
                    await session.execute(paged_query, {"after_id": after_id, "limit": batch_size})
                ).all()
        if not rows:
            break
        after_id = rows[-1][0]

        for row in rows:
            row_id = row[0]
            raw_json = row[1]
            before_keys: list[str] = []
            after_written: list[str] = []
            after_skipped: list[str] = []
            failures: list[RotationFailure] = []
            values_scanned = 0
            values_needing_rotation = 0
            row_failed = False

            try:
                cfg_dict = _decode_json_dict(raw_json)
            except Exception as exc:
                failures.append(
                    RotationFailure(
                        database="admin",
                        family="provider_configs",
                        table="provider_configs",
                        row_id=str(row_id),
                        column="config_json",
                        key_id=None,
                        error=str(exc),
                    )
                )
                candidate = _RowCandidate(
                    row_id=row_id,
                    update_params=None,
                    values_scanned=1,
                    values_needing_rotation=1,
                    before_keys=[_MALFORMED_KEY_ID],
                    after_keys_if_written=[_MALFORMED_KEY_ID],
                    after_keys_if_skipped=[_MALFORMED_KEY_ID],
                    failures=failures,
                )
                _apply_candidate_stats(report, stats, candidate, written=False)
                continue

            if cfg_dict is None:
                cfg_dict = {}
            updated_cfg = dict(cfg_dict)

            for secret_field in _PROVIDER_SECRET_FIELDS:
                secret_val = cfg_dict.get(secret_field)
                if not isinstance(secret_val, str) or not secret_val:
                    continue
                values_scanned += 1
                key_id = _safe_key_id(encryption, secret_val)
                recorded_key = key_id if key_id is not None else _MALFORMED_KEY_ID
                before_keys.append(recorded_key)

                try:
                    new_secret, changed = encryption.reencrypt(secret_val)
                except DecryptionError as exc:
                    row_failed = True
                    if recorded_key != encryption.active_key_id:
                        values_needing_rotation += 1
                    after_written.append(recorded_key)
                    after_skipped.append(recorded_key)
                    failures.append(
                        RotationFailure(
                            database="admin",
                            family="provider_configs",
                            table="provider_configs",
                            row_id=str(row_id),
                            column=f"config_json.{secret_field}",
                            key_id=key_id,
                            error=str(exc),
                        )
                    )
                else:
                    updated_cfg[secret_field] = new_secret
                    if changed:
                        values_needing_rotation += 1
                        after_written.append(encryption.active_key_id)
                        after_skipped.append(recorded_key)
                    else:
                        after_written.append(recorded_key)
                        after_skipped.append(recorded_key)

            candidate = _RowCandidate(
                row_id=row_id,
                update_params=(
                    {"id": row_id, "config_json": json.dumps(updated_cfg)}
                    if (values_needing_rotation > 0 and not row_failed)
                    else None
                ),
                original_values={
                    "config_json": json.dumps(raw_json) if isinstance(raw_json, dict) else raw_json
                },
                values_scanned=values_scanned,
                values_needing_rotation=values_needing_rotation,
                before_keys=before_keys,
                after_keys_if_written=after_written,
                after_keys_if_skipped=after_skipped,
                failures=failures,
            )
            if candidate.update_params is not None:
                pending.append(candidate)
                if len(pending) >= batch_size:
                    await _flush_write_batch(
                        admin_db,
                        update_sql,
                        pending,
                        report,
                        stats,
                        dry_run=dry_run,
                        max_batches=max_batches,
                    )
            else:
                _apply_candidate_stats(report, stats, candidate, written=False)

        if len(rows) < batch_size:
            break

    await _flush_write_batch(
        admin_db,
        update_sql,
        pending,
        report,
        stats,
        dry_run=dry_run,
        max_batches=max_batches,
    )


def _inspect_held_job_payload(
    encryption: EncryptionService,
    row_id: Any,
    payload_dict: dict[str, Any],
) -> _RowCandidate:
    """Inspect and prepare re-encryption for a held execution's encrypted payload."""
    before_keys: list[str] = []
    after_written: list[str] = []
    after_skipped: list[str] = []
    failures: list[RotationFailure] = []
    updated_payload = dict(payload_dict)
    values_scanned = 0
    values_needing_rotation = 0
    row_failed = False

    enc_blob = payload_dict.get(_ENCRYPTED_PAYLOAD_KEY)
    if isinstance(enc_blob, str) and enc_blob:
        values_scanned += 1
        outer_key_id = _safe_key_id(encryption, enc_blob)
        outer_recorded = outer_key_id if outer_key_id is not None else _MALFORMED_KEY_ID

        try:
            decrypted_json = encryption.decrypt(enc_blob)
            inner_dict = _decode_json_dict(decrypted_json)
            if inner_dict is None:
                raise ValueError("decrypted held payload is empty")
        except Exception as exc:
            row_failed = True
            before_keys.append(outer_recorded)
            after_written.append(outer_recorded)
            after_skipped.append(outer_recorded)
            if outer_recorded != encryption.active_key_id:
                values_needing_rotation += 1
            failures.append(
                RotationFailure(
                    database="admin",
                    family="held_executions",
                    table="held_executions",
                    row_id=str(row_id),
                    column=f"payload.{_ENCRYPTED_PAYLOAD_KEY}",
                    key_id=outer_key_id,
                    error=str(exc),
                )
            )
        else:
            inner_updated = dict(inner_dict)
            inner_cookie = inner_dict.get(_INBOUND_COOKIE_PAYLOAD_KEY)
            inner_changed = False
            effective_before_key = outer_recorded

            if isinstance(inner_cookie, str) and inner_cookie:
                cookie_key_id = _safe_key_id(encryption, inner_cookie)
                cookie_recorded = cookie_key_id if cookie_key_id is not None else _MALFORMED_KEY_ID
                try:
                    new_cookie, inner_changed = encryption.reencrypt(inner_cookie)
                except DecryptionError as exc:
                    row_failed = True
                    effective_before_key = cookie_recorded
                    failures.append(
                        RotationFailure(
                            database="admin",
                            family="held_executions",
                            table="held_executions",
                            row_id=str(row_id),
                            column=(
                                f"payload.{_ENCRYPTED_PAYLOAD_KEY}.{_INBOUND_COOKIE_PAYLOAD_KEY}"
                            ),
                            key_id=cookie_key_id,
                            error=str(exc),
                        )
                    )
                else:
                    if inner_changed:
                        inner_updated[_INBOUND_COOKIE_PAYLOAD_KEY] = new_cookie
                        if effective_before_key == encryption.active_key_id:
                            effective_before_key = cookie_recorded

            before_keys.append(effective_before_key)
            if row_failed:
                after_written.append(effective_before_key)
                after_skipped.append(effective_before_key)
                if effective_before_key != encryption.active_key_id:
                    values_needing_rotation += 1
            else:
                outer_needs = outer_recorded != encryption.active_key_id
                if outer_needs or inner_changed:
                    values_needing_rotation += 1
                    reencrypted_outer = encryption.encrypt(json.dumps(inner_updated))
                    updated_payload[_ENCRYPTED_PAYLOAD_KEY] = reencrypted_outer
                    after_written.append(encryption.active_key_id)
                    after_skipped.append(effective_before_key)
                else:
                    after_written.append(effective_before_key)
                    after_skipped.append(effective_before_key)

    return _RowCandidate(
        row_id=row_id,
        update_params=(
            {"id": row_id, "payload": json.dumps(updated_payload)}
            if (values_needing_rotation > 0 and not row_failed)
            else None
        ),
        original_values={"payload": json.dumps(payload_dict)},
        values_scanned=values_scanned,
        values_needing_rotation=values_needing_rotation,
        before_keys=before_keys,
        after_keys_if_written=after_written,
        after_keys_if_skipped=after_skipped,
        failures=failures,
    )


def _inspect_async_job_payload(
    encryption: EncryptionService,
    row_id: Any,
    payload_dict: dict[str, Any],
) -> _RowCandidate:
    """Inspect and prepare re-encryption for an async execution job's encrypted cookie."""
    before_keys: list[str] = []
    after_written: list[str] = []
    after_skipped: list[str] = []
    failures: list[RotationFailure] = []
    updated_payload = dict(payload_dict)
    values_scanned = 0
    values_needing_rotation = 0
    row_failed = False

    cookie_blob = payload_dict.get(_INBOUND_COOKIE_PAYLOAD_KEY)
    if isinstance(cookie_blob, str) and cookie_blob:
        values_scanned += 1
        key_id = _safe_key_id(encryption, cookie_blob)
        recorded_key = key_id if key_id is not None else _MALFORMED_KEY_ID
        before_keys.append(recorded_key)

        try:
            new_cookie, changed = encryption.reencrypt(cookie_blob)
        except DecryptionError as exc:
            row_failed = True
            if recorded_key != encryption.active_key_id:
                values_needing_rotation += 1
            after_written.append(recorded_key)
            after_skipped.append(recorded_key)
            failures.append(
                RotationFailure(
                    database="admin",
                    family="jobs",
                    table="jobs",
                    row_id=str(row_id),
                    column=f"payload.{_INBOUND_COOKIE_PAYLOAD_KEY}",
                    key_id=key_id,
                    error=str(exc),
                )
            )
        else:
            updated_payload[_INBOUND_COOKIE_PAYLOAD_KEY] = new_cookie
            if changed:
                values_needing_rotation += 1
                after_written.append(encryption.active_key_id)
                after_skipped.append(recorded_key)
            else:
                after_written.append(recorded_key)
                after_skipped.append(recorded_key)

    return _RowCandidate(
        row_id=row_id,
        update_params=(
            {"id": row_id, "payload": json.dumps(updated_payload)}
            if (values_needing_rotation > 0 and not row_failed)
            else None
        ),
        original_values={"payload": json.dumps(payload_dict)},
        values_scanned=values_scanned,
        values_needing_rotation=values_needing_rotation,
        before_keys=before_keys,
        after_keys_if_written=after_written,
        after_keys_if_skipped=after_skipped,
        failures=failures,
    )


def _is_missing_held_table_error(exc: OperationalError | ProgrammingError) -> bool:
    """Whether the dialect reports that the optional held-execution table is absent."""
    message = str(exc.orig).lower()
    return "held_executions" in message and (
        "does not exist" in message or "no such table" in message
    )


async def _rotate_jobs_and_held_executions(
    admin_db: DatabaseSession,
    encryption: EncryptionService,
    report: RotationReport,
    *,
    target_held: bool,
    dry_run: bool,
    batch_size: int,
    max_batches: int | None,
) -> None:
    family_name = "held_executions" if target_held else "jobs"
    stats = report.by_family[family_name]

    first_with_held = text(
        "SELECT j.id, j.status, j.payload, "
        "EXISTS(SELECT 1 FROM held_executions h WHERE h.job_id = j.id) AS is_held "
        "FROM jobs j ORDER BY j.id ASC LIMIT :limit"
    )
    paged_with_held = text(
        "SELECT j.id, j.status, j.payload, "
        "EXISTS(SELECT 1 FROM held_executions h WHERE h.job_id = j.id) AS is_held "
        "FROM jobs j WHERE j.id > :after_id ORDER BY j.id ASC LIMIT :limit"
    )
    first_jobs_only = text(
        "SELECT j.id, j.status, j.payload, 0 AS is_held FROM jobs j ORDER BY j.id ASC LIMIT :limit"
    )
    paged_jobs_only = text(
        "SELECT j.id, j.status, j.payload, 0 AS is_held "
        "FROM jobs j WHERE j.id > :after_id ORDER BY j.id ASC LIMIT :limit"
    )
    update_sql = "UPDATE jobs SET payload = :payload WHERE id = :id"

    after_id: Any = None
    use_held_subquery = True
    pending: list[_RowCandidate] = []

    while True:
        async with admin_db.session() as session:
            try:
                if after_id is None:
                    q = first_with_held if use_held_subquery else first_jobs_only
                    rows = (await session.execute(q, {"limit": batch_size})).all()
                else:
                    q = paged_with_held if use_held_subquery else paged_jobs_only
                    rows = (
                        await session.execute(q, {"after_id": after_id, "limit": batch_size})
                    ).all()
            except (OperationalError, ProgrammingError) as exc:
                if not use_held_subquery or not _is_missing_held_table_error(exc):
                    raise
                use_held_subquery = False
                # PostgreSQL aborts the current transaction after this error.
                # Leave this session and issue the compatibility query in a new one.
                continue

        if not rows:
            break
        after_id = rows[-1][0]

        for row in rows:
            row_id = row[0]
            job_status = str(row[1] or "")
            raw_payload = row[2]
            is_held_fk = bool(row[3])

            try:
                payload_dict = _decode_json_dict(raw_payload)
            except Exception as exc:
                is_held_row = is_held_fk or job_status == "held"
                if is_held_row != target_held:
                    continue
                failure = RotationFailure(
                    database="admin",
                    family=family_name,
                    table=stats.table,
                    row_id=str(row_id),
                    column="payload",
                    key_id=None,
                    error=str(exc),
                )
                candidate = _RowCandidate(
                    row_id=row_id,
                    update_params=None,
                    values_scanned=1,
                    values_needing_rotation=1,
                    before_keys=[_MALFORMED_KEY_ID],
                    after_keys_if_written=[_MALFORMED_KEY_ID],
                    after_keys_if_skipped=[_MALFORMED_KEY_ID],
                    failures=[failure],
                )
                _apply_candidate_stats(report, stats, candidate, written=False)
                continue

            if payload_dict is None:
                payload_dict = {}

            is_held_row = (
                is_held_fk or job_status == "held" or _ENCRYPTED_PAYLOAD_KEY in payload_dict
            )
            if is_held_row != target_held:
                continue

            if target_held:
                candidate = _inspect_held_job_payload(encryption, row_id, payload_dict)
            else:
                candidate = _inspect_async_job_payload(encryption, row_id, payload_dict)

            if candidate.update_params is not None:
                pending.append(candidate)
                if len(pending) >= batch_size:
                    await _flush_write_batch(
                        admin_db,
                        update_sql,
                        pending,
                        report,
                        stats,
                        dry_run=dry_run,
                        max_batches=max_batches,
                    )
            else:
                _apply_candidate_stats(report, stats, candidate, written=False)

        if len(rows) < batch_size:
            break

    await _flush_write_batch(
        admin_db,
        update_sql,
        pending,
        report,
        stats,
        dry_run=dry_run,
        max_batches=max_batches,
    )


def _init_report(
    encryption: EncryptionService,
    *,
    include_control: bool,
    include_admin: bool,
    dry_run: bool,
    batch_size: int,
) -> RotationReport:
    report = RotationReport(
        active_key_id=encryption.active_key_id,
        dry_run=dry_run,
        batch_size=batch_size,
    )
    if include_control:
        for spec in _CONTROL_COLUMN_SPECS:
            fstats = FamilyRotationStats(
                database=spec.database,
                family=spec.family,
                table=spec.table,
            )
            report.by_family[spec.family] = fstats
            report.by_table[spec.table] = fstats
            report.by_table[spec.family] = fstats
    if include_admin:
        for family, table in (
            ("provider_configs", "provider_configs"),
            ("jobs", "jobs"),
            ("held_executions", "held_executions"),
        ):
            fstats = FamilyRotationStats(
                database="admin",
                family=family,
                table=table,
            )
            report.by_family[family] = fstats
            report.by_table[table] = fstats
    return report


async def rotate_encrypted_storage(
    encryption: EncryptionService,
    *,
    control_db: DatabaseSession | None = None,
    admin_db: DatabaseSession | None = None,
    dry_run: bool = False,
    batch_size: int = 100,
    max_batches: int | None = None,
) -> RotationReport:
    """Inventory and optionally re-encrypt all persisted ciphertext across databases.

    Processes rows in keyset-ordered pages of *batch_size* and commits each write
    batch in its own transaction, so an interrupted run can be resumed safely:
    already rotated rows carry :attr:`EncryptionService.active_key_id` and are
    skipped without rewriting.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be >= 1 when specified")
    if control_db is None and admin_db is None:
        raise ValueError("At least one of control_db or admin_db must be provided")

    report = _init_report(
        encryption,
        include_control=control_db is not None,
        include_admin=admin_db is not None,
        dry_run=dry_run,
        batch_size=batch_size,
    )

    if control_db is not None:
        for spec in _CONTROL_COLUMN_SPECS:
            await _rotate_column_spec(
                control_db,
                encryption,
                spec,
                report,
                dry_run=dry_run,
                batch_size=batch_size,
                max_batches=max_batches,
            )

    if admin_db is not None:
        await _rotate_provider_configs(
            admin_db,
            encryption,
            report,
            dry_run=dry_run,
            batch_size=batch_size,
            max_batches=max_batches,
        )
        await _rotate_jobs_and_held_executions(
            admin_db,
            encryption,
            report,
            target_held=False,
            dry_run=dry_run,
            batch_size=batch_size,
            max_batches=max_batches,
        )
        await _rotate_jobs_and_held_executions(
            admin_db,
            encryption,
            report,
            target_held=True,
            dry_run=dry_run,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    return report


async def verify_key_retired(
    encryption: EncryptionService,
    retired_key_id: str | None = None,
    *,
    control_db: DatabaseSession | None = None,
    admin_db: DatabaseSession | None = None,
    batch_size: int = 100,
) -> RotationReport:
    """Scan persisted ciphertexts (dry-run) and fail closed if *retired_key_id* remains.

    When *retired_key_id* is ``None``, asserts that every persisted ciphertext uses
    :attr:`EncryptionService.active_key_id`. Raises :exc:`KeyRetirementError` if any
    row still requires the retired/non-active key or fails decryption.
    """
    report = await rotate_encrypted_storage(
        encryption,
        control_db=control_db,
        admin_db=admin_db,
        dry_run=True,
        batch_size=batch_size,
    )
    report.verify_key_retired(retired_key_id)
    return report


async def assert_only_active_key(
    encryption: EncryptionService,
    *,
    control_db: DatabaseSession | None = None,
    admin_db: DatabaseSession | None = None,
    batch_size: int = 100,
) -> RotationReport:
    """Scan persisted ciphertexts and fail closed if any non-active key remains."""
    return await verify_key_retired(
        encryption,
        None,
        control_db=control_db,
        admin_db=admin_db,
        batch_size=batch_size,
    )


class EncryptedStorageRotator:
    """Stateful helper binding an :class:`EncryptionService` to target databases."""

    def __init__(
        self,
        encryption: EncryptionService,
        *,
        control_db: DatabaseSession | None = None,
        admin_db: DatabaseSession | None = None,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if control_db is None and admin_db is None:
            raise ValueError("At least one of control_db or admin_db must be provided")
        self._encryption = encryption
        self._control_db = control_db
        self._admin_db = admin_db
        self._batch_size = batch_size

    async def rotate(
        self,
        *,
        dry_run: bool = False,
        batch_size: int | None = None,
        max_batches: int | None = None,
    ) -> RotationReport:
        """Run a dry-run inventory or bounded-batch re-encryption pass."""
        return await rotate_encrypted_storage(
            self._encryption,
            control_db=self._control_db,
            admin_db=self._admin_db,
            dry_run=dry_run,
            batch_size=batch_size if batch_size is not None else self._batch_size,
            max_batches=max_batches,
        )

    async def verify_key_retired(
        self,
        retired_key_id: str | None = None,
        *,
        batch_size: int | None = None,
    ) -> RotationReport:
        """Verify that *retired_key_id* (or all non-active keys) is absent from storage."""
        return await verify_key_retired(
            self._encryption,
            retired_key_id,
            control_db=self._control_db,
            admin_db=self._admin_db,
            batch_size=batch_size if batch_size is not None else self._batch_size,
        )

    async def assert_only_active_key(
        self,
        *,
        batch_size: int | None = None,
    ) -> RotationReport:
        """Verify that every persisted ciphertext uses the active key."""
        return await assert_only_active_key(
            self._encryption,
            control_db=self._control_db,
            admin_db=self._admin_db,
            batch_size=batch_size if batch_size is not None else self._batch_size,
        )
