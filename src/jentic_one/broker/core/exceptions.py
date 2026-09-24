"""Broker domain-exception taxonomy + the agent-recovery contract.

Pure module — **no** FastAPI/httpx/DB imports. Core/services/adapters raise these
typed domain exceptions; the single FastAPI handler in ``broker/web/errors.py``
maps the taxonomy to ``application/problem+json`` (the B-004 handler). This keeps
the transport coupling out of the domain/service layers (00-overview "Errors —
domain in, problem+json out").

Every error also carries the **agent-recovery contract**: an ``error_origin``
(``broker`` | ``upstream``) and, where a deterministic recovery exists, a
structured ``AgentDirective`` so the autonomous-agent caller can recover without
reading docs (02-core-proxy "Agent-centric error recovery").
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from jentic_one.shared.broker.execution import ErrorOrigin as ErrorOrigin
from jentic_one.shared.broker.protocols import IdentityMismatch

AgentStrategy = Literal[
    "wait",
    "retry",
    "modify_headers",
    "prompt_human",
    "switch_toolkit",
    "fatal",
]


class AgentDirective(BaseModel):
    """A machine-actionable recovery instruction embedded in a problem+json body.

    A fixed, trainable ``strategy`` vocabulary lets an agent's system prompt
    hard-code the recovery loop (e.g. *on ``wait`` sleep
    ``parameters.retry_after_seconds``; on ``modify_headers`` add
    ``parameters.headers`` and retry once*).
    """

    strategy: AgentStrategy
    parameters: dict[str, Any] = {}
    human_readable_instruction: str


class BrokerError(Exception):
    """Base broker domain error.

    Carries the problem+json ``detail``/``type`` plus optional extension members,
    the ``error_origin`` and an optional ``AgentDirective``. ``headers`` are extra
    response headers the central handler should attach (e.g. ``Retry-After``).
    """

    def __init__(
        self,
        detail: str,
        *,
        type: str = "about:blank",
        extra: dict[str, Any] | None = None,
        origin: ErrorOrigin = ErrorOrigin.BROKER,
        directive: AgentDirective | None = None,
        headers: dict[str, str] | None = None,
        instance: str | None = None,
        pre_send: bool = False,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.type = type
        self.extra = extra or {}
        self.origin = origin
        self.directive = directive
        self.headers = headers or {}
        # RFC 9457 ``instance`` — the request path this problem occurred on, so
        # the body identifies the specific occurrence (kept consistent with the
        # sibling ``jentic.problem_details`` errors that already set it).
        self.instance = instance
        # Whether the failure happened *before any request bytes hit the wire*
        # (connect-phase). A pre-send failure is safe to retry for ANY method —
        # the upstream cannot have acted; a post-send failure (read timeout, drop
        # after send) is only safe to retry for idempotent methods / with an
        # Idempotency-Key. Defaults to False (assume bytes were sent).
        self.pre_send = pre_send


class OperationNotFoundError(BrokerError):
    """The reconstructed URL+method matched no registered operation (404)."""


class AmbiguousMatchError(BrokerError):
    """The URL matched more than one operation and cannot be disambiguated (409)."""


class MethodNotAllowedError(BrokerError):
    """The URL matched an operation, but not for the requested method (405)."""


class TooManyCandidatesError(BrokerError):
    """Discovery produced too many candidates to resolve safely (503)."""


class UpstreamUrlNotAllowedError(BrokerError):
    """SSRF boundary: private-IP / metadata host / unformable upstream URL (400)."""


class UpgradeNotSupportedError(BrokerError):
    """A protocol-upgrade attempt (WebSocket / h2c) the buffered proxy can't carry (426)."""


class PayloadTooLargeError(BrokerError):
    """Request body exceeds the broker body cap (413)."""


class MutationRequiresIdempotencyKeyError(BrokerError):
    """An opt-in agent-safety guard requiring an Idempotency-Key on a mutation (428)."""


class IdempotencyConflictError(BrokerError):
    """An Idempotency-Key was reused with a *different* request fingerprint (409)."""


class IdempotencyInProgressError(BrokerError):
    """An Idempotency-Key retry arrived while the original is still in-flight (409).

    Distinct ``type`` from :class:`IdempotencyConflictError` (same status) so the
    client can tell "same key, different body" (don't retry) from "original still
    running" (retry after ``Retry-After``).
    """


class InvalidRevisionPinError(BrokerError):
    """A ``Jentic-Revision`` pin that fails the spec regex / is unknown / is archived (422).

    Covers the parameter-shape failure (malformed value), an unknown revision,
    and an ``archived`` revision — all parameter-validation failures the agent
    fixes by changing the header (same 422 class as FastAPI ``RequestValidationError``).
    """


class UnauthorizedRevisionPinError(BrokerError):
    """A ``Jentic-Revision`` pin to a ``draft`` revision the caller may not access (403).

    Distinct from :class:`InvalidRevisionPinError` (422): the pin is well-formed
    and the revision exists, but it is an unpublished ``draft`` the caller does
    not own — an entitlement failure, not a parameter-shape failure.
    """


class CircuitOpenError(BrokerError):
    """The circuit breaker is open for the target upstream (503)."""


class RateLimitExceededError(BrokerError):
    """A broker-side rate limit was exceeded (429)."""


class CredentialNotProvisionedError(BrokerError):
    """No credential is provisioned for the resolved API/caller (424)."""


class CredentialUndecryptableError(BrokerError):
    """A resolved credential's stored ciphertext cannot be decrypted (424).

    Same-class failure as :class:`CredentialNotProvisionedError` (the request
    is fine; a stored dependency is unusable), but a different recovery: the
    caller cannot re-provision itself — an operator has to re-add the
    credential because the encryption key that produced the stored blob is
    gone. Common cause: a reinstall regenerated the encryption keyset under
    the same key id, so lookup succeeds and AES-GCM authentication fails
    (see ``shared/crypto/encryption.py`` — ``InvalidTag`` →
    :class:`~jentic_one.shared.crypto.DecryptionError`); other paths include
    a hand-rotated key without keeping the retired entry, a
    corrupted row, or a database restored under a different key. The
    ``prompt_human`` directive tells the agent to escalate to an operator
    rather than retry. Never carry ciphertext or key material in the body
    (redaction rule).
    """


class CredentialNeedsReconnectError(BrokerError):
    """The OAuth2 grant was revoked/disconnected — the caller must reconnect (401)."""


class CredentialRefreshTransientError(BrokerError):
    """The IdP returned a transient error during token refresh (502)."""


class InvalidCredentialNameError(BrokerError):
    """The Jentic-Credential-Name header value doesn't match any active credential (400)."""


class ActionDeniedError(BrokerError):
    """The request was denied by a permission rule on the caller's binding (403)."""


class CredentialIdentityMismatchError(BrokerError):
    """Bound, but no bound credential's identity covers this API (403).

    Distinct from :class:`ActionDeniedError` and the missing-binding denials
    (``no_credential_binding``; ``no_toolkit_binding`` on the flag-off
    fallback): the agent *is* bound, but the bound credential's stored API
    identity does not cover the resolved operation identity (#747/#748). The
    recovery is to fix/re-provision the **credential**, not to file an access
    request (which would auto-deny).
    """


class UpstreamTimeoutError(BrokerError):
    """The upstream did not respond within the deadline (504)."""


class DeadlineExceededError(BrokerError):
    """The overall request deadline elapsed before the call completed (504).

    Distinct from :class:`UpstreamTimeoutError` (a single attempt's connect/read
    timeout): this is the **whole-call wall-clock budget** the ``DeadlineRunner``
    enforces *around* the transport (and, once it lands, the retry loop). Same
    ``504`` class, but the cause is "we ran out of time across the envelope", not
    "one attempt stalled". ``upstream`` origin — the time was spent waiting on the
    vendor — so the agent's directive points at the async-pivot / wait recovery.
    """


class UpstreamResponseTooLargeError(BrokerError):
    """The upstream response body exceeded the broker's response-size cap (502).

    Enforced mid-stream so a hostile/large upstream can't OOM the instance — the
    response-side counterpart to :class:`PayloadTooLargeError`. ``upstream`` origin
    (the vendor sent too much), so the agent can ``switch_toolkit`` to an
    alternative vendor.
    """


class RunnerSchemeUnsupportedError(BrokerError):
    """No runner is registered for the upstream URL's scheme (501).

    The broker has no transport that can speak this scheme (e.g. an ``ftp://``
    upstream when only the HTTP runner is registered). Distinct from a
    :class:`OperationNotFoundError` (404 — the scheme is known but the URL maps
    to no operation): here the broker simply *cannot* carry the protocol, so the
    capability gap is the agent's to resolve (use a different upstream).
    """


class RunnerUnavailableError(BrokerError):
    """A runner exists for the scheme but is degraded/unavailable (503).

    A runner whose ``startup()`` failed is marked unavailable rather than aborting
    app start (so a flaky non-HTTP runner never blocks HTTP proxying). A request
    routed to that scheme is *rejected, not dropped* — a transient ``503`` the
    agent can retry — distinct from :class:`RunnerSchemeUnsupportedError` (501,
    no such runner at all).
    """


def switch_toolkit_directive(status: int) -> AgentDirective:
    """The standard directive for a mirrored upstream 5xx — try an alternative vendor.

    The ``switch_toolkit`` strategy literal is wire contract (agents hard-code
    the strategy vocabulary); the prose names the surviving recovery — pick a
    different vendor/API you already have a credential binding for.
    """
    return AgentDirective(
        strategy="switch_toolkit",
        parameters={"upstream_status": status},
        human_readable_instruction=(
            "The upstream vendor returned a server error; try an alternative "
            "vendor/API you are already bound for."
        ),
    )


def no_toolkit_binding_directive(
    *, vendor: str, name: str, version: str, toolkit_serves_api: bool
) -> AgentDirective:
    """Directive for a ``no_toolkit_binding`` 403 — recover the missing binding.

    Emitted only on the flag-off toolkit-derivation fallback
    (``broker.direct_bindings_enabled=false``): the caller is authenticated but
    bound to no toolkit that serves this API. The remediation it names is the
    surviving access-request vocabulary — a ``credential:bind`` filed by API
    reference (``jentic access request --api``) or a ``credential:provision``
    plan (``--provision``) — chosen by whether anything serves the API at all:

    - ``toolkit_serves_api=True`` — something already serves it, the caller
      just isn't bound. Filing a bind request by API reference is the correct,
      approvable next step.
    - ``toolkit_serves_api=False`` — nothing serves it yet (no credential has
      been provisioned/bound). A bare bind request here would be a guaranteed
      dead-end: the approval denies with "no toolkit serves API …" because an
      API is only served once a credential for it exists (issue #683). So point
      the caller at the provisioning plan first — the step that actually
      creates a serving credential — before the binding. Deliberately
      text-only — the broker never enumerates other credentials here (that
      would leak instance inventory to an unbound agent, and this stateless
      edge has no scoped view of what a future approver could reuse).

    Both variants name the exact next step so an autonomous agent can hand it to
    its operator verbatim instead of reading docs.
    """
    api = "/".join(part for part in (vendor, name) if part)
    parameters: dict[str, Any] = {
        "api": {"vendor": vendor, "name": name, "version": version},
        "toolkit_serves_api": toolkit_serves_api,
    }

    if toolkit_serves_api:
        parameters["suggested_command"] = f"jentic access request --api {api} --wait"
        instruction = (
            f"You are not bound for '{api}'. File an access request yourself with "
            f"`jentic access request --api {api} --wait`, then ask your operator to approve "
            f"it — only a human can grant the binding. Once approved, retry this call."
        )
        return AgentDirective(
            strategy="prompt_human",
            parameters=parameters,
            human_readable_instruction=instruction,
        )

    # Nothing serves this API yet: a credential must be provisioned and bound
    # first, then the agent is bound to it. A bare bind request (`--api`) now
    # can never be approved — so steer the agent to file the whole path as one
    # provisioning plan (`--provision`), which a human fulfils and approves in
    # the dashboard (they enter the secret and grant the plan).
    parameters["suggested_command"] = (
        f'jentic access request --provision {api} --reason "<why you need this>" --wait'
    )
    instruction = (
        f"Nothing serves '{api}' yet, so a bare binding request (`--api`) cannot be approved. "
        f'File a provisioning plan with `jentic access request --provision {api} --reason "<why>" '
        "--wait` — propose the auth type (`--auth`) and permission rules (`--rules-json`) you read "
        "from the API spec, and pass a `--reason` the approver sees; then ask your operator to "
        "fulfil it in the dashboard (they enter the credential secret and approve). "
        "Only a human can grant this. Once approved, retry this call."
    )
    return AgentDirective(
        strategy="prompt_human",
        parameters=parameters,
        human_readable_instruction=instruction,
    )


def _render_identity(vendor: str, name: str | None, version: str | None) -> str:
    """Render a ``vendor/name/version`` identity with ``*`` for unset axes.

    Unset (``None`` or empty) name/version become ``*`` so a
    ``(vendor, None, "1.1")`` identity reads as ``vendor/*/1.1`` rather than the
    ambiguous ``vendor/1.1`` (which looks like vendor/name).
    """
    return "/".join((vendor, name or "*", version or "*"))


def credential_identity_mismatch_directive(*, mismatch: IdentityMismatch) -> AgentDirective:
    """Directive for a ``credential_identity_mismatch`` 403 — fix the credential.

    The flag-off (toolkit-derivation fallback) variant: the agent is bound and
    a credential exists behind that binding, but the credential's stored API
    identity does not cover the resolved operation (#747/#748). Filing an
    access request here auto-denies, so the directive points at
    re-provisioning/fixing the **credential** and names the concrete
    expected-vs-found identity so the operator has an actionable diagnostic. The
    strategy is the fixed ``prompt_human`` (only a human can fix the credential).

    No ``suggested_command`` is emitted: fixing a credential is an operator
    action with no single verbatim CLI command an agent can run (there is no
    ``jentic credentials`` command group), so the prose instruction is the
    contract. Unset identity axes render as ``*`` (e.g. ``vendor/*/1.1``) so the
    caller never confuses a missing name for a version.
    """
    expected = _render_identity(
        mismatch.expected_vendor, mismatch.expected_name, mismatch.expected_version
    )
    found = _render_identity(mismatch.found_vendor, mismatch.found_name, mismatch.found_version)
    if mismatch.would_match_if_normalized:
        instruction = (
            f"A binding exists for this API, but the bound credential's stored identity "
            f"'{found}' only matches '{expected}' after normalization — it was stored in a "
            f"non-canonical form. Ask your operator to re-provision (or recreate) the "
            f"credential for '{expected}' so its identity is canonical, then retry. Do not "
            f"file an access request — the binding already exists."
        )
    else:
        instruction = (
            f"A binding exists for this API, but its credential's identity '{found}' does "
            f"not match this API '{expected}'. Ask your operator to fix or re-provision the "
            f"credential so it targets '{expected}', then retry. Do not file an access "
            f"request — the binding already exists."
        )
    return AgentDirective(
        strategy="prompt_human",
        parameters={
            "expected": {
                "vendor": mismatch.expected_vendor,
                "name": mismatch.expected_name,
                "version": mismatch.expected_version,
            },
            "found": {
                "vendor": mismatch.found_vendor,
                "name": mismatch.found_name,
                "version": mismatch.found_version,
            },
            "would_match_if_normalized": mismatch.would_match_if_normalized,
        },
        human_readable_instruction=instruction,
    )


def ambiguous_toolkit_directive(candidates: list[str]) -> AgentDirective:
    """Directive for an ``ambiguous_toolkit`` 409 — pick one bound toolkit and retry.

    Emitted only on the flag-off toolkit-derivation fallback
    (``broker.direct_bindings_enabled=false``), where several toolkits the
    caller is bound to serve this API; the agent must resend with the
    ``Jentic-Toolkit-Id`` header naming one of ``candidates`` — the one header
    that disambiguates *this* denial (it stays functional for as long as the
    fallback exists). The direct-binding path has its own twin,
    :func:`ambiguous_credential_binding_directive`, which disambiguates on the
    credential axis via ``Jentic-Credential-Name`` / ``Jentic-Credential-Id``.
    The directive names the exact CLI flag form (``jentic execute --header``)
    so an autonomous agent can recover without reading docs, mirroring
    :func:`no_toolkit_binding_directive`.
    """
    pick = candidates[0] if candidates else "<toolkit_id>"
    return AgentDirective(
        strategy="switch_toolkit",
        parameters={
            "candidates": candidates,
            # A real, runnable example using the first candidate — not a literal
            # ellipsis template — so an agent (or its operator) can paste it as-is
            # and only swap the id if it prefers another candidate.
            "suggested_command": f"jentic execute --header Jentic-Toolkit-Id={pick} ...",
        },
        human_readable_instruction=(
            "Multiple toolkits serve this API. Resend the same execute with "
            "`--header Jentic-Toolkit-Id=<id>`, using one of the ids in "
            "parameters.candidates."
        ),
    )


def action_denied_directive() -> AgentDirective:
    """Directive for an ``action_denied`` 403 — a permission rule forbids this op.

    The flag-off (toolkit-derivation fallback) variant: the caller *is* bound
    for this API, but a permission rule on the legacy toolkit path denies this
    specific operation. The agent cannot self-recover by switching bindings or
    filing a binding request — only a human can relax the rule — so the
    strategy is ``prompt_human``. No ``suggested_command``: relaxing a rule is
    an operator action with no verbatim agent-runnable command, mirroring
    :func:`direct_action_denied_directive`.
    """
    return AgentDirective(
        strategy="prompt_human",
        parameters={},
        human_readable_instruction=(
            "This operation is denied by a permission rule. You are bound "
            "for this API, but the rule forbids this specific call — "
            "ask your operator to adjust the permission rules governing "
            "your access. This is not something you can grant yourself."
        ),
    )


# ---------------------------------------------------------------------------
# Direct-binding directives — the agent→credential twins of the toolkit
# directives above. These serve the default path
# (``broker.direct_bindings_enabled``, default on); the toolkit directives
# serve only the flag-off derivation fallback until it is retired.
# ---------------------------------------------------------------------------


def no_credential_binding_directive(
    *, vendor: str, name: str, version: str, api_served: bool
) -> AgentDirective:
    """Directive for a ``no_credential_binding`` 403 — recover the missing binding.

    The caller is authenticated but has no active direct credential binding
    covering this API. The right recovery depends on whether any credential
    serves the API *at all*:

    - ``api_served=True`` — a credential exists, the caller just isn't bound to
      it. Filing a ``credential:bind`` access request by API reference
      (``jentic access request --api``) is the correct, approvable next step;
      an operator can also grant the binding directly
      (``POST /agents/{id}/credentials``).
    - ``api_served=False`` — no credential is provisioned for this API yet, so a
      bare bind request auto-denies: file a provisioning plan
      (``jentic access request --provision``) that a human fulfils and approves
      in the dashboard.

    Deliberately never enumerates other owners' credentials (that would leak
    instance inventory to an unbound agent).
    """
    api = "/".join(part for part in (vendor, name) if part)
    parameters: dict[str, Any] = {
        "api": {"vendor": vendor, "name": name, "version": version},
        "api_served": api_served,
    }
    if api_served:
        parameters["suggested_command"] = f"jentic access request --api {api} --wait"
        instruction = (
            f"You have no credential binding for '{api}'. File an access request yourself "
            f"with `jentic access request --api {api} --wait`, then ask your operator to "
            "approve it — only a human can grant the binding (they can also bind directly "
            "via POST /agents/{agent_id}/credentials). Once bound, retry this call."
        )
    else:
        parameters["suggested_command"] = (
            f'jentic access request --provision {api} --reason "<why you need this>" --wait'
        )
        instruction = (
            f"No credential is provisioned for '{api}' yet, so a bare binding request "
            f"(`--api`) cannot be approved. File a provisioning plan with `jentic access "
            f'request --provision {api} --reason "<why>" --wait` — propose the auth type '
            "(`--auth`) and permission rules (`--rules-json`) you read from the API spec; "
            "then ask your operator to fulfil it in the dashboard (they enter the "
            "credential secret and approve). Only a human can do this. Once bound, retry "
            "this call."
        )
    return AgentDirective(
        strategy="prompt_human",
        parameters=parameters,
        human_readable_instruction=instruction,
    )


def direct_credential_identity_mismatch_directive(*, mismatch: IdentityMismatch) -> AgentDirective:
    """Directive for the direct-binding ``credential_identity_mismatch`` 403.

    Same diagnostic contract as :func:`credential_identity_mismatch_directive`
    (#747/#748) with direct-binding prose: the agent *is* bound to credential(s),
    but none of their stored identities cover the resolved operation. The fix is
    the **credential**, never a new binding request (the binding already exists).
    """
    expected = _render_identity(
        mismatch.expected_vendor, mismatch.expected_name, mismatch.expected_version
    )
    found = _render_identity(mismatch.found_vendor, mismatch.found_name, mismatch.found_version)
    if mismatch.would_match_if_normalized:
        instruction = (
            f"You are bound to a credential whose stored identity '{found}' only matches "
            f"'{expected}' after normalization — it was stored in a non-canonical form. Ask "
            f"your operator to re-provision (or recreate) the credential for '{expected}' so "
            "its identity is canonical, then retry. Do not request a new binding — one "
            "already exists."
        )
    else:
        instruction = (
            f"You are bound to a credential whose identity '{found}' does not match this API "
            f"'{expected}'. Ask your operator to fix or re-provision the credential so it "
            f"targets '{expected}', then retry. Do not request a new binding — one already "
            "exists."
        )
    return AgentDirective(
        strategy="prompt_human",
        parameters={
            "expected": {
                "vendor": mismatch.expected_vendor,
                "name": mismatch.expected_name,
                "version": mismatch.expected_version,
            },
            "found": {
                "vendor": mismatch.found_vendor,
                "name": mismatch.found_name,
                "version": mismatch.found_version,
            },
            "would_match_if_normalized": mismatch.would_match_if_normalized,
        },
        human_readable_instruction=instruction,
    )


def ambiguous_credential_binding_directive(candidates: list[str]) -> AgentDirective:
    """Directive for an ``ambiguous_credential_binding`` 409 — pick one and retry.

    Several credentials the caller is bound to cover this API at the same
    specificity; the agent must resend with the ``Jentic-Credential-Id`` header
    (the authoritative tie-breaker) naming one of ``candidates`` — or
    ``Jentic-Credential-Name`` when names are unique. Candidate ids are the
    caller's own bound credentials, so naming them here leaks nothing.
    """
    pick = candidates[0] if candidates else "<credential_id>"
    return AgentDirective(
        strategy="modify_headers",
        parameters={
            "candidates": candidates,
            "headers": {"Jentic-Credential-Id": pick},
        },
        human_readable_instruction=(
            "Multiple bound credentials cover this API. Resend the same request "
            "with the Jentic-Credential-Id header set to one of the ids in "
            "parameters.candidates (Jentic-Credential-Name also works when names "
            "are unique)."
        ),
    )


def direct_action_denied_directive() -> AgentDirective:
    """Directive for the direct-binding ``action_denied`` 403.

    The caller *is* bound to the selected credential, but the binding's
    permission rules (inline or via its attached rule set) deny this specific
    operation. Only a human can relax the rules, so the strategy is
    ``prompt_human``.
    """
    return AgentDirective(
        strategy="prompt_human",
        parameters={},
        human_readable_instruction=(
            "This operation is denied by your credential binding's permission "
            "rules. You are bound to a credential for this API, but the rules "
            "forbid this specific call — ask your operator to adjust the "
            "binding's permission rules. This is not something you can grant "
            "yourself."
        ),
    )
