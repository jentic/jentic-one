"""The broker's agent-recovery contract: ``AgentDirective`` and its builders.

A directive is the machine-actionable recovery instruction a broker problem+json
body carries in its ``agent_directive`` member, so an autonomous-agent caller
can recover without reading docs. The error taxonomy in
``broker/core/errors.py`` carries one on each error that has a deterministic
recovery; the builders here produce the standard directives for each case.

Pure module — **no** FastAPI/httpx/DB imports.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from jentic_one.shared.access_guidance import ConnectTarget
from jentic_one.shared.broker.protocols import IdentityMismatch
from jentic_one.shared.permissions.matching import validate_path

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


def _render_identity(vendor: str, name: str | None, version: str | None) -> str:
    """Render a ``vendor/name/version`` identity with ``*`` for unset axes.

    Unset (``None`` or empty) name/version become ``*`` so a
    ``(vendor, None, "1.1")`` identity reads as ``vendor/*/1.1`` rather than the
    ambiguous ``vendor/1.1`` (which looks like vendor/name).
    """
    return "/".join((vendor, name or "*", version or "*"))


def credential_identity_mismatch_directive(*, mismatch: IdentityMismatch) -> AgentDirective:
    """Directive for a ``credential_identity_mismatch`` 403 — fix the credential.

    The agent is bound and a credential exists behind that binding, but the
    credential's stored API identity does not cover the resolved operation
    (#747/#748). This is not agent-recoverable, so the directive points at
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
            f"credential for '{expected}' so its identity is canonical, then retry. This is "
            f"not agent-recoverable — the binding already exists."
        )
    else:
        instruction = (
            f"A binding exists for this API, but its credential's identity '{found}' does "
            f"not match this API '{expected}'. Ask your operator to fix or re-provision the "
            f"credential so it targets '{expected}', then retry. This is not "
            f"agent-recoverable — the binding already exists."
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


# ---------------------------------------------------------------------------
# Direct-binding directives — agent→credential recovery guidance. The only
# authorization path since theme-5 Phase 6b deleted the legacy toolkit
# derivation fallback (and its directive twins).
# ---------------------------------------------------------------------------


def suggested_permission_rules(*, method: str, path: str) -> list[dict[str, Any]] | None:
    """The minimal permission rule allowing one denied request, in rule-schema shape.

    One ``allow`` rule on the request's method and exact upstream path — the
    narrowest grant that lets the denied call through. An approver widens it
    (a path pattern, read-only ``GET``) at review. ``None`` when the path is
    not a valid exact-mode rule path, so a directive never suggests a rule the
    rule schema would refuse.
    """
    if not method or validate_path(path, "exact") is not None:
        return None
    return [{"effect": "allow", "methods": [method.upper()], "path": path, "match_mode": "exact"}]


def connect_parameters(
    connect: ConnectTarget | None, suggested_rules: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """The provisioning fields a missing-credential directive adds to ``parameters``.

    ``suggested_command`` is the CLI form and ``connect`` the structured form
    an MCP client fills ``request_connection`` from — both only when something
    connectable covers the API. ``suggested_rules`` is the minimal rule to ask
    for with the credential, whoever provisions it.
    """
    params: dict[str, Any] = {}
    if connect is not None:
        params["suggested_command"] = connect.cli_command()
        params["connect"] = connect.as_parameters()
    if suggested_rules:
        params["suggested_rules"] = suggested_rules
    return params


def api_connect_parameters(*, vendor: str, name: str, version: str) -> dict[str, Any]:
    """The ``parameters`` a missing-credential directive adds for an API-target connect.

    ``suggested_command`` is the CLI form (``jentic connect --api
    <vendor/name/version>``) and ``connect.api`` the structured form an MCP
    client passes as ``request_connection``'s ``api`` argument.
    """
    return {
        "suggested_command": f"jentic connect --api {vendor}/{name}/{version}",
        "connect": {"api": {"vendor": vendor, "name": name, "version": version}},
    }


def api_connect_hint(*, vendor: str, name: str, version: str) -> str:
    """The prose naming both lanes' API-target connect call."""
    return (
        f'run `jentic connect --api {vendor}/{name}/{version} --reason "…"` (or call the '
        f'request_connection tool with api {{"vendor": "{vendor}", "name": "{name}", '
        f'"version": "{version}"}} and a reason)'
    )


def no_credential_binding_directive(
    *,
    vendor: str,
    name: str,
    version: str,
    api_served: bool,
    connect: ConnectTarget | None = None,
    suggested_rules: list[dict[str, Any]] | None = None,
    provisioning_url: str | None = None,
    connect_api: bool = False,
) -> AgentDirective:
    """Directive for a ``no_credential_binding`` 403 — recover the missing binding.

    The caller is authenticated but has no active direct credential binding
    covering this API. The right recovery depends on whether any credential
    serves the API *at all*:

    - ``api_served=True`` — a credential exists, the caller just isn't bound to
      it. The operator grants the binding in the dashboard (or via
      ``POST /agents/{id}/credentials``) — binding stays human.
    - ``api_served=False`` — no credential is provisioned for this API yet.
      The *provisioning* leg is agent-initiable when a vendor-registry entry
      or a shared OAuth-app registration covers the API (``connect``): the
      agent runs ``jentic connect <vendor>`` (or calls the
      ``request_connection`` MCP tool) — an agent-initiated connect session
      binds the agent at confirm time — and relays the approval_url to its
      operator; approval stays human. Otherwise the whole ask stays with the
      operator.

    ``connect`` is the target resolved for this API
    (``broker.services.credentials.connect_target``), or ``None`` when nothing
    connectable covers it; when set, the directive carries
    ``parameters.suggested_command`` (``jentic connect <vendor>``),
    and ``parameters.connect`` (``{vendor_key, registration_id?}``).
    ``suggested_rules`` (the minimal rule allowing the denied request) rides
    ``parameters.suggested_rules`` whenever given.

    ``provisioning_url`` is the owner deep link to the agent's open connect
    session for this API, when it has one: the agent already asked, so the
    directive carries the link to relay and neither ``suggested_command`` nor
    ``connect`` (a second connect would only duplicate the pending request).

    ``connect_api`` says the deployment takes connect requests for any
    registry API (``control.connect.manual_flows_enabled``). With no
    ``connect`` target, the directive then names the API itself
    (:func:`api_connect_parameters`) and the agent starts the connect; a human
    enters the credential, so the agent relays the URL, ends its turn and
    retries later. Without it the ask stays with the operator.

    Deliberately never enumerates other owners' credentials (that would leak
    instance inventory to an unbound agent).
    """
    api = "/".join(part for part in (vendor, name) if part)
    parameters: dict[str, Any] = {
        "api": {"vendor": vendor, "name": name, "version": version},
        "api_served": api_served,
        **connect_parameters(None if provisioning_url else connect, suggested_rules),
    }
    if provisioning_url:
        parameters["provisioning_url"] = provisioning_url
        return AgentDirective(
            strategy="prompt_human",
            parameters=parameters,
            human_readable_instruction=(
                f"You have no credential binding for '{api}' yet, and your request to "
                "connect a credential for it is still waiting for your operator. Do not "
                f"start another: relay {provisioning_url} to them to approve it. Once they "
                "confirm, retry this call."
            ),
        )
    connect_command = connect.cli_command() if connect is not None else None
    if connect is None and connect_api:
        parameters.update(api_connect_parameters(vendor=vendor, name=name, version=version))
    hint = api_connect_hint(vendor=vendor, name=name, version=version)
    if api_served:
        instruction = (
            f"You have no credential binding for '{api}', but a credential already serves "
            f"it. Ask your operator to bind this agent to that credential (in the dashboard, "
            "or via POST /agents/{agent_id}/credentials) — only a human can grant the "
            "binding. Once bound, retry this call."
        )
        if connect_command:
            instruction += (
                f" Alternatively, start connecting a fresh credential yourself with "
                f"`{connect_command}` (or the request_connection tool) and "
                "relay its approval_url to your operator — approval is still theirs."
            )
        elif connect_api:
            instruction += (
                f" Alternatively, start the request yourself: {hint}, and relay its "
                "approval_url to your operator — they can bind you to the existing credential "
                "from it. Approval is still theirs."
            )
    elif connect_command:
        instruction = (
            f"No credential is provisioned for '{api}' yet. Start connecting one yourself: "
            f"run `{connect_command}` (or call the request_connection tool) "
            "and relay the approval_url it returns to your operator — they approve the "
            "connection in the browser; only a human can approve. Once they confirm, retry "
            "this call."
        )
    elif connect_api:
        instruction = (
            f"No credential is provisioned for '{api}' yet. Start the request yourself: "
            f"{hint}, and relay the approval_url it returns to your operator — a human "
            "enters the credential in the browser, which can take a while, so end your turn "
            "and retry this call later. Only a human can approve."
        )
    else:
        instruction = (
            f"No credential is provisioned for '{api}' yet. Ask your operator to connect or "
            f"provision a credential for '{api}' in the dashboard and bind this agent to it "
            "— include the auth type and permission rules you read from the API spec, and "
            "why you need it. Only a human can do this. Once bound, retry this call."
        )
    return AgentDirective(
        strategy="prompt_human",
        parameters=parameters,
        human_readable_instruction=instruction,
    )


def credential_binding_suspended_directive(
    *, vendor: str, name: str, version: str
) -> AgentDirective:
    """Directive for a ``credential_binding_suspended`` 403 — only the operator can lift it.

    The agent's binding to a credential covering this API is suspended. The
    operator made that call, so the recovery is theirs: resume the binding.
    Deliberately carries no ``suggested_command``, ``connect`` or
    ``provisioning_url``: a new connect for the same API is refused while the
    suspension stands, so there is no connect to offer.
    """
    api = "/".join(part for part in (vendor, name) if part)
    return AgentDirective(
        strategy="prompt_human",
        parameters={"api": {"vendor": vendor, "name": name, "version": version}},
        human_readable_instruction=(
            f"Your operator has suspended your access to '{api}'. Ask your operator "
            "whether they will resume it — only they can. Do not request a new "
            "connection for this API. Once they resume it, retry this call."
        ),
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
            "its identity is canonical, then retry. This is not agent-recoverable — the "
            "binding already exists."
        )
    else:
        instruction = (
            f"You are bound to a credential whose identity '{found}' does not match this API "
            f"'{expected}'. Ask your operator to fix or re-provision the credential so it "
            f"targets '{expected}', then retry. This is not agent-recoverable — the binding "
            "already exists."
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


def approval_pending_limit_directive(*, pending: int, limit: int) -> AgentDirective:
    """Directive for the ``approval_pending_limit_reached`` 403.

    The call matched a require-approval rule, but the agent already has
    ``limit`` calls waiting on a reviewer. Only a human decision frees a slot.
    """
    return AgentDirective(
        strategy="prompt_human",
        parameters={"pending": pending, "limit": limit},
        human_readable_instruction=(
            "This call needs human approval, but you already have the maximum number of "
            "calls waiting for review. Ask the user to review your pending approvals, then "
            "send the call again once one is decided."
        ),
    )
