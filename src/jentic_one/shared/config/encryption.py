"""Encryption key configuration for credential storage."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from jentic_one.shared.config._common import ConfigError

_logger = structlog.get_logger(__name__)


_KEY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# AES-256 key size, mirrored from shared/crypto/encryption.py (which imports
# this package, so it cannot be imported here). Enforced at config load for
# material_file so a wrong target fails without leaking its observed length.
_ENCRYPTION_KEY_BYTES = 32
# A base64-encoded 32-byte key is 44 characters; anything near this cap is not
# a key, and the cap keeps an arbitrary-path read from pulling a large file
# into memory.
_MATERIAL_FILE_MAX_BYTES = 4096


class EncryptionKey(BaseModel):
    """A single named encryption key.

    Key material comes from exactly ONE source:

    - ``material``      — inline in the config (local dev / vault-templated files),
    - ``material_env``  — the name of an environment variable holding the key,
    - ``material_file`` — a regular file to read the key from (docker/k8s secret
      mounts, systemd ``LoadCredential`` paths). Pipes and ``/dev/fd`` sources
      are rejected: config may be validated more than once per process, and a
      source that cannot be re-read would hang or fail the second load.

    ``material_env``/``material_file`` are resolved once, at config load, into
    ``material`` — consumers keep reading ``resolved_material`` and never learn
    where the bytes came from (the source field is cleared after resolution, so
    a resolved key re-validates cleanly and never re-reads the environment or
    the file). Resolution failures (unset variable, unreadable file, empty
    value) fail validation loudly rather than booting a server that cannot
    decrypt its own credentials.
    """

    # Exclusivity is enforced at runtime by _resolve_material; mirroring it in
    # the exported JSON Schema lets schema-driven consumers (the generated Go
    # installer struct, editors) reject an entry with zero or multiple sources.
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {"required": ["material"]},
                {"required": ["material_env"]},
                {"required": ["material_file"]},
            ]
        }
    )

    id: str
    material: SecretStr | None = Field(
        default=None,
        description="Base64-encoded key material, inline in the config.",
    )
    material_env: str | None = Field(
        default=None,
        description="Name of an environment variable holding the base64-encoded key material.",
    )
    material_file: str | None = Field(
        default=None,
        description=(
            "Path to a regular file holding the base64-encoded key material "
            "(docker/k8s secret mount, systemd LoadCredential path)."
        ),
    )

    @field_validator("id")
    @classmethod
    def _validate_id_format(cls, v: str) -> str:
        if not _KEY_ID_RE.match(v):
            raise ValueError("key id must match [a-zA-Z0-9_-]+")
        return v

    @model_validator(mode="after")
    def _resolve_material(self) -> EncryptionKey:
        sources = [
            name
            for name, value in (
                ("material", self.material),
                ("material_env", self.material_env),
                ("material_file", self.material_file),
            )
            if value is not None
        ]
        if len(sources) != 1:
            raise ValueError(
                "exactly one of material / material_env / material_file must be set"
                + (f" (got: {', '.join(sources)})" if sources else "")
            )
        if self.material is not None:
            inline = self.material.get_secret_value()
            if not inline.strip():
                raise ValueError("material is empty")
            # Strip like the env/file sources do, so identical bytes produce
            # identical keys regardless of which source carried them.
            if inline != inline.strip():
                self.material = SecretStr(inline.strip())
            self._log_resolution(source="material")
        elif self.material_env is not None:
            value = os.environ.get(self.material_env)
            if value is None or not value.strip():
                raise ValueError(
                    f"material_env {self.material_env!r} is not set (or empty) in the environment"
                )
            self.material = SecretStr(value.strip())
            self._log_resolution(source="material_env", origin=self.material_env)
            self.material_env = None
        elif self.material_file is not None:
            self.material = SecretStr(self._read_material_file(self.material_file))
            self._log_resolution(source="material_file", origin=self.material_file)
            self.material_file = None
        return self

    @staticmethod
    def _read_material_file(path_str: str) -> str:
        """Read and vet key material from a regular file.

        Only regular files are accepted: config may be validated more than once
        per process, and a pipe or ``/dev/fd`` source cannot be re-read — a
        drained pipe fails the second load and a writer-less FIFO blocks boot
        forever. The content is required to be a base64-encoded 32-byte key so
        that pointing ``material_file`` at an arbitrary path (reachable with
        env-write privilege via ``JENTIC__…MATERIAL_FILE``) fails without the
        error message echoing the target's length or content.
        """
        path = Path(path_str)
        try:
            st = os.stat(path)
        except OSError as exc:
            raise ValueError(f"cannot read material_file {path_str!r}: {exc}") from exc
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(
                f"material_file {path_str!r} must be a regular file — pipes and "
                "/dev/fd sources cannot be re-read and are not supported"
            )
        if stat.S_IMODE(st.st_mode) & 0o077:
            # The file mode is this feature's security boundary: a
            # group/other-readable key file silently hands the master key to
            # every same-host account.
            _logger.warning(
                "encryption_material_file_permissive",
                path=path_str,
                mode=oct(stat.S_IMODE(st.st_mode)),
                detail="material_file should be readable only by the server user (0600)",
            )
        if st.st_size > _MATERIAL_FILE_MAX_BYTES:
            raise ValueError(f"material_file {path_str!r} does not contain a base64-encoded key")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read material_file {path_str!r}: {exc}") from exc
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            raise ValueError(
                f"material_file {path_str!r} does not contain a base64-encoded key"
            ) from None
        text = text.strip()
        if not text:
            raise ValueError(f"material_file {path_str!r} is empty")
        # Vet the content here, where the message can deliberately omit the
        # observed length — the generic decode error downstream would otherwise
        # echo the exact byte length of whatever the path pointed at.
        try:
            decoded = base64.b64decode(text, validate=True)
        except binascii.Error:
            raise ValueError(
                f"material_file {path_str!r} does not contain a base64-encoded key"
            ) from None
        if len(decoded) != _ENCRYPTION_KEY_BYTES:
            raise ValueError(
                f"material_file {path_str!r} does not contain a {_ENCRYPTION_KEY_BYTES}-byte key"
            )
        return text

    def _log_resolution(self, source: str, origin: str | None = None) -> None:
        """Record where this key's material came from — never the bytes.

        The source fields are cleared after resolution, so this boot-time line
        (key id, source kind, env-var name or path, and a short SHA-256
        fingerprint) is the only trail an operator has to distinguish key
        substitution from corruption, or to answer "which file fed this key".
        """
        material = self.material.get_secret_value() if self.material is not None else ""
        _logger.info(
            "encryption_key_material_resolved",
            key_id=self.id,
            source=source,
            origin=origin,
            fingerprint=hashlib.sha256(material.encode()).hexdigest()[:16],
        )

    @property
    def resolved_material(self) -> SecretStr:
        """The key material after source resolution (guaranteed by validation)."""
        if self.material is None:  # pragma: no cover — _resolve_material guarantees it
            raise ConfigError(f"encryption key {self.id!r} has no resolved material")
        return self.material


class EncryptionConfig(BaseModel):
    """Envelope-encryption keyset configuration."""

    active_id: str = "v1"
    entries: list[EncryptionKey] = Field(default_factory=list)
