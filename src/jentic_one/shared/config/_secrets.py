"""Dev-secret guard: required secrets in production, ephemeral ones in development."""

from __future__ import annotations

import contextvars
import os
import re
import secrets

import structlog
from pydantic import SecretStr

from jentic_one.shared.config._common import ConfigError

_logger = structlog.get_logger(__name__)


# Per-process cache of dev-generated secrets, keyed by dotted config path.
# Keeps repeated config loads within one process (tests, re-reads) signing
# with the same key, while never persisting a secret-shaped literal anywhere:
# shipped images must not contain default credentials (AWS Marketplace
# container policy — their scanner flags hardcoded defaults even when a
# production guard would reject them at boot).
_EPHEMERAL_DEV_SECRETS: dict[str, SecretStr] = {}

# A configured value matching this is a forgotten placeholder (copied from an
# old example or config), never a real secret — any such value is publicly
# known, so signing with it is equivalent to signing with no key at all.
# Expressed as a pattern, not a literal, so no secret-shaped string ships in
# the image.
_PLACEHOLDER_SECRET_RE = re.compile(r"change.?me", re.IGNORECASE)


# Which surfaces read each scalar secret the production guard below enforces.
# Verified against the code that consumes them: ``admin.auth.jwt_secret`` signs
# admin/session JWTs (admin, auth) and verifies them on every surface that
# installs the superset verifier (control, registry — see ``SURFACES_NEEDING_AUTH``
# in ``__main__``; the broker verifies with its own ``broker.jwt_secret`` /
# trusted issuers instead); ``admin.invite.pepper`` hashes invite tokens (admin
# only); ``credentials.connect.state_secret`` signs the OAuth connect ``state``
# (control only). Keep in sync with the Helm chart's per-surface secret mounts
# (``deploy/helm/jentic-one/charts/common/templates/_app-secrets.tpl``).
GUARDED_FIELD_SURFACES: dict[str, frozenset[str]] = {
    "admin.auth.jwt_secret": frozenset({"admin", "auth", "control", "registry"}),
    "admin.invite.pepper": frozenset({"admin"}),
    "credentials.connect.state_secret": frozenset({"control"}),
}

# Surfaces GUARDED_FIELD_SURFACES has an opinion about. An enabled surface outside
# this set (e.g. one registered by an extension) is assumed to read every
# secret, so the guard never relaxes for code it has not been audited against.
_KNOWN_SURFACES: frozenset[str] = frozenset({"admin", "auth", "broker", "control", "registry"})

# The surfaces the config being validated will run, set by load_config() for
# the duration of validation. None (direct model construction, tests, tools)
# means "unknown", which keeps the guard strict for every secret.
_ENABLED_APPS: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "jentic_one_config_enabled_apps", default=None
)


def _secret_required_by_enabled_apps(field_path: str) -> bool:
    """True unless the surfaces being loaded provably never read ``field_path``."""
    apps = _ENABLED_APPS.get()
    if apps is None:
        return True
    consumers = GUARDED_FIELD_SURFACES.get(field_path)
    if consumers is None or not apps <= _KNOWN_SURFACES:
        return True
    return bool(apps & consumers)


def _require_or_generate_secret(value: SecretStr, *, field_path: str) -> SecretStr:
    """Return the configured secret, or mint a per-process one for dev.

    A single guard for every scalar secret the app cannot safely default
    (admin ``jwt_secret``, invite ``pepper``, connect ``state_secret``). The
    shipped default for all of them is empty:

    - ``JENTIC_ENV=production``: an empty/whitespace value — or a change-me
      placeholder — is a hard ``ConfigError``. The install path (jenticctl
      install, the Helm chart's generated Secret) provides real values, so an
      unusable value reaching production means that step was skipped.
    - development/test: generate a random per-process secret so the app still
      boots with zero configuration. Cached per ``field_path`` so repeated
      loads in one process agree; deliberately NOT persisted, so restarts
      rotate it (dev sessions ending on restart beats shipping a known key).
      Per-process means multi-process dev setups (standalone surfaces,
      ``--workers > 1``) each mint their own value; each secret is consumed
      only by its own surface, so a shared value is never assumed.

    Production requires a value only when a surface this process runs reads
    the secret (``GUARDED_FIELD_SURFACES``, keyed off the ``apps`` being loaded): a
    standalone broker or registry is not handed secrets it never reads (the
    Helm chart mounts each surface only its own). Such a process gets a
    per-process random value instead of the empty one, so nothing can ever sign
    or verify with a known key.
    """
    secret = value.get_secret_value()
    if secret.strip() and not _PLACEHOLDER_SECRET_RE.search(secret):
        return value
    if os.environ.get("JENTIC_ENV", "development") == "production":
        if not _secret_required_by_enabled_apps(field_path):
            if field_path not in _EPHEMERAL_DEV_SECRETS:
                _EPHEMERAL_DEV_SECRETS[field_path] = SecretStr(secrets.token_urlsafe(32))
                _logger.info(
                    "secret not read by enabled surfaces; using per-process value",
                    field_path=field_path,
                )
            return _EPHEMERAL_DEV_SECRETS[field_path]
        raise ConfigError(
            f"{field_path} must be explicitly configured in production — "
            "empty and placeholder values are rejected "
            "(jenticctl install generates one automatically)"
        )
    if field_path not in _EPHEMERAL_DEV_SECRETS:
        _EPHEMERAL_DEV_SECRETS[field_path] = SecretStr(secrets.token_urlsafe(32))
        _logger.info("generated ephemeral dev secret", field_path=field_path)
    return _EPHEMERAL_DEV_SECRETS[field_path]
