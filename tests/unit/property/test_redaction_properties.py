"""Property-based tests for shared secret and URL redaction."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from jentic_one.shared.redaction import (
    _MAX_REDACT_DEPTH,
    REDACTED,
    SENSITIVE_KEY_SUBSTRINGS,
    redact_url_query,
    redact_value,
)

_PARAM_NAMES = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.",
    min_size=1,
    max_size=12,
).map(lambda s: f"k_{s}")

_SECRETS = st.text(
    alphabet="abcdef0123456789XYZ",
    min_size=8,
    max_size=20,
).map(lambda s: f"sec_{s}")

_SENSITIVE_KEYS = sorted(SENSITIVE_KEY_SUBSTRINGS | {"token"})


@given(
    params=st.lists(st.tuples(_PARAM_NAMES, _SECRETS), min_size=1, max_size=5),
    userinfo_secret=st.one_of(st.none(), _SECRETS),
    fragment_secret=st.one_of(st.none(), _SECRETS),
)
def test_redact_url_query_masks_every_value(
    params: list[tuple[str, str]],
    userinfo_secret: str | None,
    fragment_secret: str | None,
) -> None:
    """Every query value, userinfo credential, and fragment is masked while param names survive."""
    userinfo = f"user:{userinfo_secret}@" if userinfo_secret is not None else ""
    query = "&".join(f"{name}={secret}" for name, secret in params)
    fragment = f"#{fragment_secret}" if fragment_secret is not None else ""
    url = f"https://{userinfo}api.example.com/v1/items?{query}{fragment}"

    redacted = redact_url_query(url)

    for name, secret in params:
        assert secret not in redacted
        assert f"{name}={REDACTED}" in redacted
    if userinfo_secret is not None:
        assert userinfo_secret not in redacted
    if fragment_secret is not None:
        assert fragment_secret not in redacted


@given(
    payload=st.dictionaries(
        st.one_of(st.sampled_from(_SENSITIVE_KEYS), st.text(max_size=24)),
        st.text(max_size=32),
        max_size=10,
    ),
)
def test_redact_value_never_returns_sensitive_keyed_value(payload: dict[str, str]) -> None:
    """Any key matching the sensitive-key rules has its value replaced with REDACTED."""
    redacted = redact_value(payload)
    for key in payload:
        lowered = key.lower()
        if lowered == "token" or any(sub in lowered for sub in SENSITIVE_KEY_SUBSTRINGS):
            assert redacted[key] == REDACTED


@given(
    extra_depth=st.integers(min_value=1, max_value=16),
    leaf=st.text(alphabet="abcdef0123456789", min_size=8, max_size=16).map(lambda s: f"leaf_{s}"),
)
def test_redact_value_depth_bounded(extra_depth: int, leaf: str) -> None:
    """Nesting beyond _MAX_REDACT_DEPTH (e.g. depth 40) never raises and truncates to REDACTED."""
    total_depth = _MAX_REDACT_DEPTH + extra_depth
    nested: Any = leaf
    for _ in range(total_depth):
        nested = {"child": nested}

    redacted = redact_value(nested)

    cursor: Any = redacted
    for _ in range(_MAX_REDACT_DEPTH):
        assert isinstance(cursor, dict)
        cursor = cursor["child"]
    assert cursor == REDACTED
    assert leaf not in str(redacted)
