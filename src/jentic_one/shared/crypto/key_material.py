"""Resolve encryption-key material from its configured source.

An :class:`~jentic_one.shared.config.EncryptionKey` entry carries exactly one
source: inline base64 (``material``), an environment-variable name
(``material_env``), or a macOS Keychain generic-password service name
(``material_keychain``). This module turns an entry into raw key bytes.

Resolution is invoked from ``EncryptionService.__init__`` — lazily, at first
use of ``Context.encryption`` — so config loading alone never touches the
environment or the Keychain (a Keychain read can block on a user prompt).

The Keychain path shells out to ``/usr/bin/security`` by ABSOLUTE path on
purpose: local installs treat same-user agent processes as the adversary, and
a ``PATH`` lookup would let any of them interpose a fake ``security`` binary.
No timeout is applied to the call — a locked keychain legitimately blocks on
a user-facing unlock/allow prompt.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys

from jentic_one.shared.config import ConfigError, EncryptionKey

_SECURITY_BIN = "/usr/bin/security"


def _run_security(service: str) -> subprocess.CompletedProcess[str]:
    """Execute the Keychain lookup. Module-level seam so tests can stub it."""
    # Fixed absolute binary, no shell — see the module docstring on PATH hijacking.
    return subprocess.run(
        [_SECURITY_BIN, "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )


def _from_keychain(key_id: str, service: str) -> str:
    if sys.platform != "darwin":
        raise ConfigError(
            f"Encryption key '{key_id}' uses material_keychain, which is only "
            f"supported on macOS (platform: {sys.platform})"
        )
    result = _run_security(service)
    if result.returncode != 0:
        raise ConfigError(
            f"Encryption key '{key_id}': Keychain item '{service}' could not be "
            f"read (security exited {result.returncode}). Create it with "
            f"'jenticctl install --keychain' or check its access control."
        )
    return result.stdout.strip()


def _from_env(key_id: str, var: str) -> str:
    value = os.environ.get(var, "")
    if not value:
        raise ConfigError(
            f"Encryption key '{key_id}': environment variable '{var}' "
            "(material_env) is not set or empty"
        )
    return value


def resolve_key_material(entry: EncryptionKey) -> bytes:
    """Return the raw key bytes for a keyset entry.

    Raises :exc:`ConfigError` when the source is unavailable or the material
    is not valid base64. Length validation stays with the caller
    (``EncryptionService``), which owns the cipher requirements.
    """
    if entry.material is not None:
        encoded = entry.material.get_secret_value()
    elif entry.material_env:
        encoded = _from_env(entry.id, entry.material_env)
    elif entry.material_keychain:
        encoded = _from_keychain(entry.id, entry.material_keychain)
    else:  # pragma: no cover — the EncryptionKey validator forbids this
        raise ConfigError(f"Encryption key '{entry.id}' has no material source")

    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ConfigError(f"Encryption key '{entry.id}': material is not valid base64") from exc
