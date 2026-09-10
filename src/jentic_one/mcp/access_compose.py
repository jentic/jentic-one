"""The request_access plan builder — the Go CLI's ``compose()`` in Python.

``AccessRequestService.file()`` accepts an already-composed ``items`` list and
only validates it: the composition — expanding ``provision``/``toolkits``/
``toolkit_ids``/``scopes`` with keyed ``auth``/``rules_json`` values into item
dicts, plus the duplicate/conflict validation — is client-side in the Go CLI
(``cli/internal/cli/api/access_plan.go``) and must be client-side here too.
This module is that port, kept ``tools.py``-free so the handler stays
handler-only.

Every validation error is raised as :class:`ComposeError` with the same
message shapes as the Go original, adapted to this tool's parameter names
(``"provision"`` instead of ``--provision`` and so on) — the messages are the
correctable-protocol-error contract, and the handler maps them to
``invalid_params``.

Composed items are **dicts shaped exactly like the REST wire form**; the
handler round-trips them through the REST pydantic schemas
(``AccessRequestFileRequest``/``AccessRequestItemRequest``) before calling
``file()``, mirroring the router — this module never talks to the service.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


class ComposeError(ValueError):
    """A correctable composition error (maps to invalid_params handler-side)."""


class AccessTargetRequiredError(ComposeError):
    """No target named at all (Go: ``errAccessTargetRequired``) — the handler
    substitutes the tool-flavored wording naming every accepted parameter."""

    def __init__(self) -> None:
        super().__init__(
            'specify what to request: "toolkits" (vendor/name), "toolkit_ids" (tk_…), '
            '"scopes", or "provision" (vendor/name plans) — repeat and combine to '
            "compose one request"
        )


#: Credential auth types ``auth`` accepts (Go: ``validAuthTypes``). "none"
#: marks a no-auth API: the plan's credential:provision item carries
#: ``security_scheme=no_auth`` and the wizard auto-creates a NO_AUTH
#: credential (no secret prompt).
_VALID_AUTH_TYPES = frozenset({"bearer", "api_key", "basic", "oauth2", "none"})

_API_SLUG_RE = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class AccessRequestOptions:
    """The normalized request_access filing arguments (Go: ``accessRequestOptions``)."""

    provisions: list[str] = field(default_factory=list)
    toolkits: list[str] = field(default_factory=list)
    toolkit_ids: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    auths: list[str] = field(default_factory=list)
    rules_jsons: list[str] = field(default_factory=list)
    reason: str = ""

    def target_count(self) -> int:
        """The number of distinct targets named — each provision plan counts as
        one, as does each toolkit/toolkit_id/scope item. Decides composite
        behavior (the duplicate-pending handling)."""
        return (
            len(clean_values(self.provisions))
            + len(clean_values(self.toolkits))
            + len(clean_values(self.toolkit_ids))
            + len(clean_values(self.scopes))
        )

    def has_filing_params(self) -> bool:
        """Whether ANY filing parameter rides the call — the poll-arm
        exclusivity check (a stray reason/auth/rules_json counts too).
        ``auths``/``rules_jsons`` are probed RAW, exactly like Go
        (``mcp_access.go`` checks ``len(opts.auths) > 0`` before any
        cleaning): a whitespace-only stray is still a confused call, never
        noise to drop into a silent poll. ``target_count()`` keeps counting
        cleaned values — the same asymmetry as the Go original."""
        return bool(self.target_count() or self.reason or self.auths or self.rules_jsons)

    def compose(self) -> list[dict[str, Any]]:
        """Build the full item list in fulfilment order (Go: ``compose()``).

        Provisioning plans first (one 4-item chain per provision, in argument
        order), then toolkit binds by reference, by id, and scope grants.
        Targets are validated as a set — duplicates and a toolkits/provision
        pair naming the same API are rejected, since they would file
        conflicting or redundant intents the approving human has to untangle.
        """
        provisions = clean_values(self.provisions)
        toolkits = clean_values(self.toolkits)
        toolkit_ids = clean_values(self.toolkit_ids)
        scopes = clean_values(self.scopes)

        if not (provisions or toolkits or toolkit_ids or scopes):
            raise AccessTargetRequiredError()
        if not provisions and (clean_values(self.auths) or clean_values(self.rules_jsons)):
            raise ComposeError('"auth" and "rules_json" only apply with "provision"')

        prov_keys = _canonical_ref_keys("provision", provisions)
        toolkit_keys = _canonical_ref_keys("toolkits", toolkits)
        for key in toolkit_keys:
            if key in prov_keys:
                raise ComposeError(
                    f'{key} is named by both "toolkits" and "provision"; a provisioning '
                    "plan already ends with the toolkit binding, so drop it from "
                    '"toolkits"'
                )
        if (dup := _first_duplicate(toolkit_ids)) is not None:
            raise ComposeError(f'"toolkit_ids" {dup} given more than once')
        if (dup := _first_duplicate(scopes)) is not None:
            raise ComposeError(f'"scopes" {dup} given more than once')

        auths = _resolve_keyed_values("auth", self.auths, prov_keys)
        rules_jsons = _resolve_keyed_values("rules_json", self.rules_jsons, prov_keys)

        items: list[dict[str, Any]] = []
        for i, provision in enumerate(provisions):
            items.extend(
                _build_provision_plan(
                    provision,
                    auths.get(prov_keys[i], ""),
                    rules_jsons.get(prov_keys[i], ""),
                )
            )
        for toolkit in toolkits:
            items.append(
                {
                    "resource_type": "toolkit",
                    "action": "bind",
                    "resource_reference": parse_toolkit_ref(toolkit),
                }
            )
        for toolkit_id in toolkit_ids:
            items.append({"resource_type": "toolkit", "action": "bind", "resource_id": toolkit_id})
        for scope in scopes:
            items.append({"resource_type": "scope", "action": "grant", "resource_id": scope})
        return items


def clean_values(values: list[str]) -> list[str]:
    """Trim each value and drop empties, preserving order (Go: ``cleanValues``)."""
    return [stripped for v in values if (stripped := v.strip())]


def _first_duplicate(values: list[str]) -> str | None:
    """The first value appearing more than once, or None (Go: ``firstDuplicate``)."""
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _canonical_ref_keys(param: str, values: list[str]) -> list[str]:
    """Parse each vendor/name[/version] value and return its canonical key
    form, rejecting duplicates within the parameter (Go: ``canonicalRefKeys``)."""
    keys: list[str] = []
    for value in values:
        key = _ref_key(parse_toolkit_ref(value))
        if key in keys:
            raise ComposeError(f'"{param}" {key} given more than once')
        keys.append(key)
    return keys


def _ref_key(ref: dict[str, Any]) -> str:
    """Render a parsed reference back to its canonical vendor/name[/version]
    string (Go: ``refKey``). Vendor/name are slugified exactly like the server
    (``shared.models.api_identity.slugify_api_field``) so raw-domain and slug
    spellings of the same API collide here instead of filing as two chains."""
    vendor = _slugify_api_field(str(ref.get("vendor", "")))
    name = _slugify_api_field(str(ref.get("name", "")))
    key = f"{vendor}/{name}"
    version = ref.get("version")
    if version:
        key += f"/{version}"
    return key


def _slugify_api_field(value: str) -> str:
    """The server's canonical slug form for API vendor/name fields (Go:
    ``slugifyAPIField``): lowercase, strip, runs of non-[a-z0-9-] become a
    single hyphen, leading/trailing hyphens trimmed."""
    return _API_SLUG_RE.sub("-", value.strip().lower()).strip("-")


def _resolve_keyed_values(param: str, values: list[str], prov_keys: list[str]) -> dict[str, str]:
    """Map repeatable per-API values (``auth``, ``rules_json``) onto their
    provision chains (Go: ``resolveKeyedValues``). Each value is either keyed
    ("vendor/name[/version]=<value>") or bare; the bare form is only
    unambiguous with exactly one provision. Missing keys mean "use the
    default"."""
    out: dict[str, str] = {}
    for raw in clean_values(values):
        split = _split_keyed_value(param, raw, prov_keys)
        if split is None:
            if len(prov_keys) != 1:
                example = prov_keys[0] if prov_keys else "vendor/name"
                raise ComposeError(
                    f'"{param}" {raw!r} must be keyed by API when "provision" repeats '
                    f'(e.g. "{example}=<value>")'
                )
            key, value = prov_keys[0], raw
        else:
            key, value = split
        if key in out:
            raise ComposeError(f'"{param}" given more than once for {key}')
        out[key] = value
    return out


def _split_keyed_value(param: str, raw: str, prov_keys: list[str]) -> tuple[str, str] | None:
    """Split "key=value" when the key part is shaped like an API reference
    (Go: ``splitKeyedValue``). A shaped key matching no provision target is an
    error (a typo, or a chain never requested); anything not shaped like a
    keyed form — including JSON payloads that happen to contain '=' — is a
    bare value (None)."""
    # A value that starts like a JSON document is always a bare payload — a
    # rules array can legitimately contain '=' (e.g. inside a path regex) and
    # must not be probed for a key.
    if raw.strip().startswith(("[", "{")):
        return None
    eq = raw.find("=")
    if eq <= 0:
        return None
    candidate = raw[:eq].strip()
    try:
        ref = parse_toolkit_ref(candidate)
    except ComposeError:
        return None  # an unparsable key prefix means "bare value", not a failure
    canonical = _ref_key(ref)
    if canonical in prov_keys:
        return canonical, raw[eq + 1 :]
    raise ComposeError(
        f'"{param}" is keyed to {canonical}, which is not among the "provision" targets'
    )


def _build_provision_plan(provision: str, auth: str, rules_json: str) -> list[dict[str, Any]]:
    """One full provisioning plan for a provision target (Go:
    ``buildProvisionPlan``): the fixed 4-item chain (toolkit:create,
    credential:provision, credential:bind, toolkit:bind) in fulfilment order.
    The agent files intent; a human fulfils the create/provision steps via the
    dashboard, which writes the resulting ids back onto the bind items before
    approving."""
    ref = parse_toolkit_ref(provision)

    auth = auth.strip() or "bearer"
    if auth not in _VALID_AUTH_TYPES:
        raise ComposeError(
            f'"auth" must be one of bearer, api_key, basic, oauth2, none; got {auth!r}'
        )
    # The credential:provision item carries the credential's security_scheme,
    # which the UI maps to a CredentialType. "none" maps to the NO_AUTH type
    # ("no_auth"); the other values already match the scheme names.
    auth_scheme = "no_auth" if auth == "none" else auth

    rules = _parse_proposed_rules(rules_json)

    items: list[dict[str, Any]] = []
    # Step 1: create a toolkit that will serve this API.
    items.append({"resource_type": "toolkit", "action": "create", "resource_reference": ref})
    # Step 2: provision a credential for this API. security_scheme carries the
    # agent-detected auth type so the operator's credential form can pre-select
    # it; the operator enters the secret — it never rides in the agent-filed
    # plan. For a no-auth API we still emit this item with
    # security_scheme=no_auth: a credential row is required for the
    # credential:bind effect to attach the toolkit binding + rules to.
    items.append(
        {
            "resource_type": "credential",
            "action": "provision",
            "resource_reference": {**ref, "security_scheme": auth_scheme},
        }
    )
    # Step 3: bind the (to-be-created) credential to the (to-be-created)
    # toolkit, carrying the agent's proposed first-pass rules. The API
    # reference is stamped on so the item names its chain in a composite
    # request (item order is not guaranteed server-side); the server ignores
    # it for credential:bind — only the amended ids wire the effect.
    bind_item: dict[str, Any] = {
        "resource_type": "credential",
        "action": "bind",
        "resource_reference": ref,
    }
    if rules is not None:
        bind_item["rules"] = rules
    items.append(bind_item)
    # Step 4: bind the agent to the toolkit, named by the same API reference.
    items.append({"resource_type": "toolkit", "action": "bind", "resource_reference": ref})
    return items


def _parse_proposed_rules(raw: str) -> list[dict[str, Any]] | None:
    """Decode the agent's proposed permission rules from a JSON array (Go:
    ``parseProposedRules``). Empty input yields None (the server substitutes a
    read-only default on the credential:bind item) so ``rules`` stays omitted
    on the wire; the REST pydantic round-trip handler-side enforces the rule
    SHAPE, this only enforces "a JSON array"."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        raise ComposeError(f'"rules_json" must be a JSON array of rules: {exc}') from None
    if not isinstance(decoded, list) or not all(isinstance(rule, dict) for rule in decoded):
        raise ComposeError('"rules_json" must be a JSON array of rules')
    return decoded


def parse_toolkit_ref(value: str) -> dict[str, Any]:
    """Split "vendor/name[/version]" into a resource_reference (Go:
    ``parseToolkitRef``). The agent names the API it discovered via search;
    the server resolves it to a concrete toolkit at decide time."""
    parts = value.strip().split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ComposeError(
            f"an API reference must be vendor/name or vendor/name/version, got {value!r}"
        )
    ref: dict[str, Any] = {"vendor": parts[0], "name": parts[1]}
    if len(parts) >= 3 and parts[2]:
        ref["version"] = parts[2]
    return ref


def rules_json_values(value: Any) -> list[str]:
    """Normalize the raw ``rules_json`` argument onto compose()'s repeatable
    string values (Go: ``rulesJSONValues``). Models send it three ways: the
    natural JSON array of rule objects (kept whole as ONE stringified value —
    commas inside rules must never split), a single string (bare or keyed), or
    a list of keyed strings for multi-provision requests."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        values: list[str] = []
        for entry in value:
            if not isinstance(entry, str):
                # An array carrying rule OBJECTS is the rules document itself:
                # one bare value, verbatim.
                return [json.dumps(value, ensure_ascii=False)]
            values.append(entry)
        return values
    if isinstance(value, dict):
        # A single rule object: wrap it into the array compose expects.
        return [json.dumps([value], ensure_ascii=False)]
    raise ComposeError(
        f'parameter "rules_json": expected a JSON array of rules, a string, or a '
        f"list of keyed strings, got {type(value).__name__}"
    )
