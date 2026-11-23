"""OpenAPI examples: the canonical parameter values and the spec example passes.

The first half is the catalogue of canonical example values for path, query
and header parameters. The second half is the generation-time passes that
``install_openapi_metadata`` runs over the generated document: they label,
compose and propagate examples, and also derive path-item summaries and
descriptions (``summarise_path_item``, ``describe_path_item``,
``describe_request_body``) and stamp ``oneOf`` discriminators
(``add_oneof_discriminators``).

Routers and schemas import the catalogue as ``ex`` from
:mod:`jentic_one.shared.web.openapi_examples`, which re-exports it, and attach
the values through FastAPI's ``openapi_examples=`` keyword, which
renders a Parameter-level ``examples`` map (``{name: {value: ...}}``) — the
form API linters and doc renderers read. (``examples=[...]`` would land inside
the parameter's ``schema`` instead, which parameter-example lint rules ignore.)

Identifier values follow the real wire formats so a reader can tell one entity
from another at a glance: KSUID-style ``<prefix>_<8-hex-seconds><16-hex>`` ids
(see ``shared/db/ids.py``), UUID revision ids, and the base64 ``{t, id}``
pagination cursor (see ``shared/pagination.py``). The values are illustrative
only — none of them resolve against a real deployment.

``tests/arch/test_openapi_metadata.py`` asserts every parameter in the
control-plane spec carries an example and that each example validates against
the parameter's schema.
"""

from __future__ import annotations

from typing import Any

from fastapi.openapi.models import Example

from jentic_one.shared.web.openapi_meta.content import HTTP_METHODS


def example(value: object, summary: str | None = None) -> dict[str, Example]:
    """Build a single-entry ``openapi_examples`` map for a parameter.

    ``summary`` defaults to the label of the canonical constant ``value`` is
    (see ``_VALUE_SUMMARIES``). Any other value gets one from the parameter it
    is attached to, when the spec is generated (``install_openapi_metadata``).
    """
    entry: Example = {"value": value}
    if summary is None and isinstance(value, str):
        summary = _VALUE_SUMMARIES.get(value)
    if summary is not None:
        entry["summary"] = summary
    return {"example": entry}


# --- Entity identifiers (KSUID-style unless noted) ---------------------------

AGENT_ID = "agnt_6968b3984d1c238dbe7e2e29"
USER_ID = "usr_6968b3987ee4c3ec0b4ee3b9"
CREDENTIAL_ID = "cred_6968b39855d91a3561684b32"
CREDENTIAL_BINDING_ID = "acb_6968b3989a4f1c2e7d3b5a60"  # direct agent↔credential binding
AGENT_API_KEY_ID = "agc_6968b398c1d2e3f4a5b6c7d8"  # agent API-key record (not the key)
RULE_SET_ID = "prs_6968b3987c22b63e87e79f05"
PERMISSION_RULE_ID = "apr_6968b3987c22b63e87e79f06"
OVERLAY_ID = "ovr_6968b398ae1c9a6e2fe6520a"
CONNECT_SESSION_ID = "cs_6968b3983b8b91c75627bee5"
CONNECT_SESSION_REVISION_ID = "csr_6968b3983b8b91c75627bee6"
OAUTH_APP_REGISTRATION_ID = "oar_6968b39821e5f89a6975f501"
OAUTH_CLIENT_RECORD_ID = "oac_6968b39840a935d9f7e2714b"
OAUTH_GRANT_ID = "ocg_6968b3985e21c5fdf643025d"
EXECUTION_ID = "exec_6968b3982706c619fe73f0cf"
EVENT_ID = "evt_6968b398b04f002b6dbff4d1"
AUDIT_ID = "aud_6968b3984275d71e90f7f09f"
JOB_ID = "job_6968b3985e8c9902207afaeb"
HELD_EXECUTION_ID = "held_6968b398a1c4e2d07b5f93c6"
NOTE_ID = "note_6968b398edb465624291e405"
TOOLKIT_ID = "tk_6968b3981ec22d56dbbc91e8"
REVISION_ID = "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c"  # registry revisions are UUIDs
# A later revision of the same API (e.g. the one an overlay materialized).
NEXT_REVISION_ID = "6992059c-0abd-4d22-bd07-5287e1bf5e6a"
REGISTRY_API_ID = "debfc9ff-7c37-4a09-801a-575bd4963ddd"  # registry API rows are UUIDs
# Registry operation id: ``op_`` + 40 hex of sha256(revision:path:method).
REGISTRY_OPERATION_ID = "op_f92d69f1621f381bce8aaff47053c61b069ffe34"
# Public OAuth ``client_id`` (``oc_`` + 32 url-safe chars), distinct from the
# ``oac_`` record id used in admin paths.
OAUTH_PUBLIC_CLIENT_ID = "oc_Xk3vQ9mT2pLw8RzN5bYc1HdF7gJs4AeU"  # pragma: allowlist secret
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"  # W3C trace-context trace id
REQUEST_ID = "req_6968b3984d1c238d"  # minted ``req_`` + 16 hex (see ``shared/logging.py``)

# --- Registry API identity ---------------------------------------------------

API_VENDOR = "stripe.com"
API_NAME = "stripe"
#: An illustrative upstream API version, not the platform ``API_VERSION``
#: (``__version__``) in :mod:`jentic_one.shared.web.openapi_meta.content`.
API_VERSION = "2024-04-10"
API_REF = f"{API_VENDOR}:{API_NAME}:{API_VERSION}"  # ``vendor[:name[:version]]`` filter
API_VENDOR_NAME = f"{API_VENDOR}/{API_NAME}"  # ``vendor/name`` monitoring filter
CATALOG_API_ID = "stripe.com"  # catalog manifest identity: ``domain[/sub-api]``
OPERATION_ID = "listCharges"
OPERATION_METHOD_URL = "GET https://api.stripe.com/v1/charges"
VENDOR_KEY = "github"  # vendor-registry key (connect flow / auth capabilities)
PROVIDER_NAME = "pipedream"  # credential provider config name
# Public base URL of a deployment; response ``_links`` are absolute under it.
BASE_URL = "https://jentic.example.com"

# --- Pagination, time windows, conditional requests --------------------------

# ``encode_cursor(2026-01-15T09:30:00Z, REVISION_ID)`` — opaque to clients.
CURSOR = "eyJ0IjogIjIwMjYtMDEtMTVUMDk6MzA6MDArMDA6MDAiLCAiaWQiOiAiM2YyYjhjMWUtN2Q0YS00ZTliLWE2YzUtMWQyZTNmNGE1YjZjIn0="  # noqa: E501  # pragma: allowlist secret
DATETIME_FROM = "2026-01-01T00:00:00Z"
DATETIME_TO = "2026-01-31T23:59:59Z"
EPOCH_SINCE = 1767225600  # 2026-01-01T00:00:00Z, Unix seconds
EPOCH_UNTIL = 1769903999  # 2026-01-31T23:59:59Z, Unix seconds
NOTE_ETAG = '"3"'  # notes ETag: quoted revision counter
GOVERNED_HOSTS_ETAG = (
    '"9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"'  # pragma: allowlist secret
)
# Connect-session review digest: unquoted SHA-256 hex of the review payload.
REVIEW_DIGEST = (
    "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"  # pragma: allowlist secret
)

# --- OAuth / OIDC (RFC 6749, RFC 7636, OIDC Core) ----------------------------

REDIRECT_URI = "https://client.example.com/oauth/callback"
OAUTH_STATE = "af0ifjsldkj"
OIDC_NONCE = "n-0S6_WzA2Mj"
# RFC 7636 Appendix B worked example.
PKCE_CODE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # pragma: allowlist secret
AUTHORIZATION_CODE = "SplxlOBeZQQYbYS6WxSbIA"  # RFC 6749 §4.1.2 example  # pragma: allowlist secret
# Signed flow blobs: ``<urlsafe-b64 JSON>.<hex HMAC>``. Truncated, illustrative.
SIGNED_FLOW_STATE = (
    "eyJfcHVycG9zZSI6ICJleGFtcGxlIn0=.5d41402abc4b2a76b9719d911017c592"  # pragma: allowlist secret
)
# Opaque ``secrets.token_urlsafe(32)`` handles (consent handle, connect poll token).
OPAQUE_TOKEN = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # pragma: allowlist secret

#: ``summary`` labels for the canonical values above, applied by ``example()``.
#: Keyed by value, so two constants that share one (``API_VENDOR`` and
#: ``CATALOG_API_ID``) share a label too.
_VALUE_SUMMARIES: dict[str, str] = {
    AGENT_ID: "Agent ID",
    USER_ID: "User ID",
    CREDENTIAL_ID: "Credential ID",
    CREDENTIAL_BINDING_ID: "Credential binding ID",
    AGENT_API_KEY_ID: "Agent API key record ID",
    RULE_SET_ID: "Permission rule set ID",
    OVERLAY_ID: "Overlay ID",
    CONNECT_SESSION_ID: "Connect session ID",
    OAUTH_APP_REGISTRATION_ID: "OAuth app registration ID",
    OAUTH_CLIENT_RECORD_ID: "OAuth client record ID",
    OAUTH_GRANT_ID: "OAuth consent grant ID",
    EXECUTION_ID: "Execution ID",
    EVENT_ID: "Event ID",
    AUDIT_ID: "Audit entry ID",
    JOB_ID: "Job ID",
    NOTE_ID: "Note ID",
    TOOLKIT_ID: "Toolkit ID",
    REVISION_ID: "Revision ID (UUID)",
    NEXT_REVISION_ID: "Later revision ID (UUID)",
    REGISTRY_API_ID: "Registry API ID (UUID)",
    REGISTRY_OPERATION_ID: "Registry operation ID",
    OAUTH_PUBLIC_CLIENT_ID: "Public OAuth client_id",
    TRACE_ID: "W3C trace ID",
    REQUEST_ID: "Request ID",
    API_VENDOR: "API vendor domain",
    API_NAME: "API name",
    API_VERSION: "API version",
    API_REF: "API reference (vendor:name:version)",
    API_VENDOR_NAME: "API vendor and name (vendor/name)",
    OPERATION_ID: "Upstream operationId",
    OPERATION_METHOD_URL: "Upstream operation (method and URL)",
    VENDOR_KEY: "Vendor registry key",
    PROVIDER_NAME: "Credential provider name",
    BASE_URL: "Deployment base URL",
    CURSOR: "Opaque cursor from a previous page",
    DATETIME_FROM: "Start of a time window (RFC 3339)",
    DATETIME_TO: "End of a time window (RFC 3339)",
    NOTE_ETAG: "Note ETag (quoted revision)",
    GOVERNED_HOSTS_ETAG: "Governed-hosts ETag",
    REVIEW_DIGEST: "Connect-session review digest",
    REDIRECT_URI: "Registered redirect URI",
    OAUTH_STATE: "Client OAuth state",
    OIDC_NONCE: "OIDC nonce",
    PKCE_CODE_CHALLENGE: "PKCE S256 code challenge",
    AUTHORIZATION_CODE: "OAuth authorization code",
    SIGNED_FLOW_STATE: "Signed flow state blob",
    OPAQUE_TOKEN: "Opaque flow handle",
}


# --- Spec passes: examples, summaries, descriptions, discriminators ----------


def summarise_parameter_examples(operation: dict[str, Any]) -> None:
    """Give every parameter example a ``summary``.

    :func:`example` labels the canonical values itself; any other
    value (a page size, a status filter) is labelled from its parameter here,
    because only the spec knows which parameter an example is attached to.
    """
    for param in operation.get("parameters") or []:
        for entry in (param.get("examples") or {}).values():
            if isinstance(entry, dict) and not entry.get("summary"):
                entry["summary"] = f"Example {param['in']} value for `{param['name']}`"


def summarise_path_item(path_item: dict[str, Any]) -> None:
    """Set the path item's ``summary`` from its operations' summaries.

    OpenAPI's path-level ``summary`` applies to every operation on the path;
    FastAPI never emits one. Joining the operation summaries in spec order
    (``"List agents; Register agent"``) states exactly what the path offers.
    """
    if path_item.get("summary"):
        return
    summaries = [
        op["summary"]
        for method, op in path_item.items()
        if method.lower() in HTTP_METHODS and isinstance(op, dict) and op.get("summary")
    ]
    if summaries:
        path_item["summary"] = "; ".join(summaries)


def describe_path_item(path_item: dict[str, Any]) -> None:
    """Set the path item's ``description`` to a method-by-method index.

    The joined ``summary`` says what the path offers; the description adds which
    HTTP method does what (``"`GET`: List agents. `POST`: Register agent."``),
    so a reader of the path alone can pick the right operation.
    """
    if path_item.get("description"):
        return
    entries = [
        f"`{method.upper()}`: {op['summary'].rstrip('.')}."
        for method, op in path_item.items()
        if method.lower() in HTTP_METHODS and isinstance(op, dict) and op.get("summary")
    ]
    if entries:
        path_item["description"] = " ".join(entries)


def describe_request_body(operation: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Give the operation's ``requestBody`` a ``description``.

    FastAPI never emits one. Prefer the body model's own description (its
    docstring, already public on the component); otherwise fall back to naming
    the operation the payload is for.
    """
    body = operation.get("requestBody")
    if not isinstance(body, dict) or body.get("description"):
        return
    for media in (body.get("content") or {}).values():
        schema = media.get("schema") or {}
        ref = schema.get("$ref", "")
        target = schemas.get(ref.rsplit("/", 1)[-1], {}) if ref else schema
        if target.get("description"):
            body["description"] = target["description"]
            return
    op_id = operation.get("operationId")
    if op_id:
        body["description"] = f"Request payload for the `{op_id}` operation."


_MISSING = object()


def _example_for(schema: Any, schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """One example for ``schema``, or ``_MISSING`` if it cannot be built from leaves.

    Uses the schema's own ``examples``/``example``, else follows a ``$ref`` to the
    component (composing it if needed), else the first ``anyOf``/``oneOf`` branch
    that yields one, else builds an array from ``items`` or an object from its
    properties. ``stack`` stops recursion through self-referencing models.
    """
    if not isinstance(schema, dict):
        return _MISSING
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in stack or name not in schemas:
            return _MISSING
        return _component_example(name, schemas, stack)
    for key in ("anyOf", "oneOf"):
        for branch in schema.get(key) or []:
            if isinstance(branch, dict) and branch.get("type") == "null":
                continue
            value = _example_for(branch, schemas, stack)
            if value is not _MISSING:
                return value
    if schema.get("type") == "array":
        item = _example_for(schema.get("items"), schemas, stack)
        return _MISSING if item is _MISSING else [item]
    if "properties" in schema:
        return _object_example(schema, schemas, stack)
    return _MISSING


def _object_example(schema: dict[str, Any], schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """An object example from its properties; ``_MISSING`` if a required one has none."""
    required = set(schema.get("required") or [])
    value: dict[str, Any] = {}
    for prop, prop_schema in (schema.get("properties") or {}).items():
        example = _example_for(prop_schema, schemas, stack)
        if example is _MISSING:
            if prop in required:
                return _MISSING
            continue
        value[prop] = example
    return value


def _component_example(name: str, schemas: dict[str, Any], stack: tuple[str, ...]) -> Any:
    component = schemas[name]
    if component.get("examples"):
        return component["examples"][0]
    if "example" in component:
        return component["example"]
    if "properties" not in component:
        return _example_for(component, schemas, (*stack, name))
    return _object_example(component, schemas, (*stack, name))


def compose_component_examples(schemas: dict[str, Any]) -> None:
    """Give object components and their nested-model properties a composed example.

    Field-level ``examples=`` sit on leaf properties; a property typed as another
    model (``$ref``, ``list[Model]``, ``Model | None``) and the component as a
    whole have none. Composing them from the leaves keeps one source of truth —
    a composed value is valid exactly when its leaves are, which the arch tests
    check — while giving readers (and agents) a complete, realistic object.
    Components that already declare ``examples`` are left as written.
    """
    for name, component in schemas.items():
        if not isinstance(component, dict) or "properties" not in component:
            continue
        for prop_schema in component["properties"].values():
            if not isinstance(prop_schema, dict) or "examples" in prop_schema:
                continue
            if "example" in prop_schema:
                continue
            value = _example_for(prop_schema, schemas, (name,))
            if value is not _MISSING:
                prop_schema["examples"] = [value]
        if "examples" not in component and "example" not in component:
            value = _object_example(component, schemas, (name,))
            if value is not _MISSING and value:
                component["examples"] = [value]


def _variant_tag(ref: str, schemas: dict[str, Any], prop: str) -> str | None:
    """The single constant value ``prop`` takes in the ``$ref``'d variant, if any."""
    variant = schemas.get(ref.rsplit("/", 1)[-1]) or {}
    spec = (variant.get("properties") or {}).get(prop) or {}
    if "const" in spec:
        return str(spec["const"])
    enum = spec.get("enum")
    return str(enum[0]) if isinstance(enum, list) and len(enum) == 1 else None


def add_oneof_discriminators(node: Any, schemas: dict[str, Any]) -> None:
    """Declare the discriminator Pydantic omits for a callable-discriminated union.

    A union discriminated by a callable (``Discriminator(fn)``) — used where one
    variant may omit the tag and is selected by default — renders as a bare
    ``oneOf`` of ``$ref`` schemas. When every variant pins the same property to a
    distinct constant, that property *is* the discriminator; declare it (with
    the explicit mapping) so clients can deserialise without trial-matching.
    """
    if isinstance(node, list):
        for item in node:
            add_oneof_discriminators(item, schemas)
        return
    if not isinstance(node, dict):
        return
    for value in node.values():
        add_oneof_discriminators(value, schemas)
    variants = node.get("oneOf")
    if "discriminator" in node or not isinstance(variants, list) or len(variants) < 2:
        return
    refs = [v["$ref"] for v in variants if isinstance(v, dict) and isinstance(v.get("$ref"), str)]
    if len(refs) != len(variants):
        return
    first = schemas.get(refs[0].rsplit("/", 1)[-1]) or {}
    for prop in first.get("properties") or {}:
        tags = [_variant_tag(r, schemas, prop) for r in refs]
        if all(tags) and len(set(tags)) == len(tags):
            node["discriminator"] = {
                "propertyName": prop,
                "mapping": dict(zip(tags, refs, strict=True)),
            }
            return


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _matches_type(value: Any, branch: dict[str, Any]) -> bool:
    """Whether ``value`` has the JSON type ``branch`` declares (bool is not a number)."""
    kinds = _JSON_TYPES.get(branch.get("type", ""))
    if kinds is None or (isinstance(value, bool) and bool not in kinds):
        return False
    return isinstance(value, kinds)


def _seed_schema_example(target: dict[str, Any], value: Any) -> None:
    """Put ``value`` on a parameter schema and on each typed branch it satisfies."""
    target.setdefault("examples", [value])
    for key in ("anyOf", "oneOf"):
        for branch in target.get(key) or []:
            if isinstance(branch, dict) and _matches_type(value, branch):
                _seed_schema_example(branch, value)
    items = target.get("items")
    if isinstance(items, dict) and isinstance(value, list) and value:
        _seed_schema_example(items, value[0])


def _first_example(holder: dict[str, Any]) -> Any:
    """The first example value declared on a parameter or schema, else ``_MISSING``."""
    if "example" in holder:
        return holder["example"]
    examples = holder.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        return first.get("value", _MISSING) if isinstance(first, dict) else _MISSING
    if isinstance(examples, list) and examples:
        return examples[0]
    return _MISSING


def propagate_examples(paths: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Surface existing examples where clients look for them.

    A parameter's example lives on the parameter (a named map for the docs UI);
    JSON-Schema-driven tooling reads ``schema.examples`` instead, so mirror it
    there. A JSON request/response that references a component with an example
    gets that example on the media type, so it renders without resolution.
    Nothing is invented: every value is one the spec already declares.
    """
    for path_item in paths.values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters") or []:
                value = _first_example(param)
                if value is not _MISSING and isinstance(param.get("schema"), dict):
                    _seed_schema_example(param["schema"], value)
            bodies = [operation.get("requestBody") or {}]
            bodies += list((operation.get("responses") or {}).values())
            for body in bodies:
                for media_type, media in (body.get("content") or {}).items():
                    if "json" not in media_type or "example" in media or "examples" in media:
                        continue
                    media_schema = media.get("schema") or {}
                    ref = media_schema.get("$ref")
                    source = (
                        schemas.get(ref.rsplit("/", 1)[-1], {})
                        if isinstance(ref, str)
                        else media_schema
                    )
                    value = _first_example(source)
                    if value is not _MISSING:
                        media["example"] = value
