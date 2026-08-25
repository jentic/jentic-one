"""Toolkit permission-rule path matching (single source of truth).

Rules are authored on two surfaces — the toolkit-bindings API
(``control/web/schemas/toolkits.py``) and the access-request API
(``control/web/schemas/access_requests.py``) — and enforced on a third
(``broker/repos/rule_evaluator.py``). This module is the one place that
knows how a ``(path, match_mode)`` pair is validated at save time and how
it matches an inbound request path at enforce time, so the three surfaces
cannot drift.

Design invariants
-----------------
* Save-time validation raises structured errors (returned as ``None`` when
  valid) so schemas can surface the underlying ``re.error`` to callers as
  ``422`` — a stored pattern that could not be compiled at save time never
  reaches the enforce path.
* Enforce-time matching is **fail-closed**: an already-stored pattern that
  fails to compile (a legacy row written before validation existed) yields
  a matcher that never matches, rather than today's silent wildcard.
* Regex mode uses **full-match** semantics (``re.fullmatch``) — the
  pattern must describe the whole request path — replacing the previous
  ``re.match`` (start-anchored) behaviour, which silently over-matched.
* ``path is None`` means "no path constraint" (the rule matches every
  path) and is the sole trigger for the condition-less-``allow`` guard;
  an empty string is rejected by ``validate_path`` in every mode so it
  cannot slip past that guard.

This module has no framework or ORM dependencies and stays in ``shared/``
so both broker and control can import it (broker cannot import control).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MatchMode = Literal["regex", "prefix", "exact"]
"""How a rule's ``path`` string is interpreted against an inbound request path."""

MATCH_MODES: tuple[MatchMode, ...] = ("regex", "prefix", "exact")
"""All valid match modes, in the same order as the Literal for OpenAPI stability."""

MAX_PATTERN_LENGTH: int = 1000
"""Save- and enforce-time cap; matches the ``VARCHAR(1000)`` on ``path``."""

PathValidationCode = Literal["invalid_regex", "pattern_too_long", "empty_path", "unknown_mode"]


@dataclass(frozen=True, slots=True)
class PathValidationError:
    """Structured save-time rejection reason (schemas surface ``reason`` as 422)."""

    code: PathValidationCode
    reason: str


@dataclass(frozen=True, slots=True)
class PathMatcher:
    """Compiled path matcher — immutable so the evaluator can cache it safely.

    ``pattern`` is the compiled regex for regex mode, ``None`` for literal
    modes. ``never`` is the fail-closed flag: a stored pattern that failed
    to compile at load time matches nothing (instead of today's silent
    wildcard). ``literal`` carries the original string for literal modes.
    """

    mode: MatchMode
    literal: str | None
    pattern: re.Pattern[str] | None
    never: bool = False

    def matches(self, request_path: str) -> bool:
        """True iff the request path is constrained by this matcher and matches it."""
        if self.never:
            return False
        if self.mode == "regex":
            # `pattern` is present unless `never` is True (guarded above).
            assert self.pattern is not None
            return self.pattern.fullmatch(request_path) is not None
        # Literal modes — `literal` is present unless `never` is True.
        assert self.literal is not None
        if self.mode == "prefix":
            return request_path.startswith(self.literal)
        return request_path == self.literal


def _check(path: str | None, mode: str) -> PathValidationError | None:
    """Shared save-and-load validation; returns ``None`` when valid.

    ``path is None`` is always valid (no constraint). Otherwise the pair
    ``(path, mode)`` must pass the mode-specific checks. Empty strings
    are rejected in every mode because:

    * ``prefix``/``exact``: ``""`` is a truthy field (satisfies the
      condition-less-``allow`` guard) yet matches every request in prefix
      mode — an unrestricted grant disguised as a constraint.
    * ``regex``: ``""`` full-matches only the empty path — never useful,
      always a mistake — and would similarly slip past the guard.
    """
    if path is None:
        return None
    if mode not in MATCH_MODES:
        return PathValidationError(
            code="unknown_mode",
            reason=f"Unknown match_mode {mode!r}; expected one of {list(MATCH_MODES)}",
        )
    if len(path) > MAX_PATTERN_LENGTH:
        return PathValidationError(
            code="pattern_too_long",
            reason=f"path exceeds maximum length ({MAX_PATTERN_LENGTH} characters)",
        )
    if path == "":
        return PathValidationError(
            code="empty_path",
            reason="path must not be empty; omit it to mean 'no path constraint'",
        )
    if mode == "regex":
        try:
            re.compile(path)
        except re.error as exc:
            return PathValidationError(
                code="invalid_regex",
                reason=f"invalid regex: {exc.msg} (at position {exc.pos})"
                if exc.pos is not None
                else f"invalid regex: {exc.msg}",
            )
    return None


def validate_path(path: str | None, mode: str) -> PathValidationError | None:
    """Validate an authored ``(path, match_mode)`` pair.

    Returns ``None`` when the pair is valid; otherwise a structured error
    the schema layer surfaces to the client as ``422``.
    """
    return _check(path, mode)


def compile_matcher(path: str | None, mode: str) -> PathMatcher | None:
    """Compile a stored ``(path, match_mode)`` pair into a matcher.

    Returns ``None`` when there is no path constraint on the rule
    (``path is None``). Never raises: a stored pattern that fails to
    compile (legacy row written before ``validate_path`` existed) returns
    a ``PathMatcher(never=True)`` — a matcher that never matches. That is
    the opposite of today's silent-wildcard behaviour and is enforced
    fail-closed so legacy bad rows cannot accidentally grant access.
    """
    if path is None:
        return None
    error = _check(path, mode)
    if error is not None:
        return PathMatcher(mode="regex", literal=None, pattern=None, never=True)
    if mode == "regex":
        return PathMatcher(mode="regex", literal=None, pattern=re.compile(path))
    return PathMatcher(mode=mode, literal=path, pattern=None)  # type: ignore[arg-type]
