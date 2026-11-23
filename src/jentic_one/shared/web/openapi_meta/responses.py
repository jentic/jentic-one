"""Per-operation response and security post-processing.

Folds Problem Details error responses into ``application/problem+json``,
documents the RFC 9457 ``default`` response, and stamps each operation's
``security`` from its recovered identity dependency. The response catalogues
routers attach live in :mod:`jentic_one.shared.web.openapi_responses`.
"""

from __future__ import annotations

from typing import Any

from jentic_one.shared.web.openapi_meta.content import authenticated_security
from jentic_one.shared.web.openapi_responses import PROBLEM_JSON, STATUS_EXAMPLES

_PROBLEM_DETAIL_REF = "#/components/schemas/ProblemDetail"


def _is_problem_detail_media(media: Any) -> bool:
    return (
        isinstance(media, dict) and (media.get("schema") or {}).get("$ref") == _PROBLEM_DETAIL_REF
    )


def normalise_error_responses(responses: dict[str, Any]) -> None:
    """Render Problem Details error responses as ``application/problem+json``.

    FastAPI files an error ``model=ProblemDetail`` under the route's response
    media type: ``application/json`` by default, but ``text/html`` for an
    HTML-page route (``response_class=HTMLResponse``). This moves whichever
    entry carries the ``ProblemDetail`` schema to the RFC 9457 media type and
    attaches the canonical per-status example, matching the platform error
    contract. Entries in another dialect (e.g. the RFC 6749 §5.2 OAuth error
    body, or the framework's plain route-not-found body) keep their
    ``application/json`` media type.
    """
    for code, response in responses.items():
        if not isinstance(response, dict) or not code.startswith(("4", "5")):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        # An error ``model=`` on an HTML-page route lands under ``text/html``;
        # error bodies are always JSON, so fold it into ``application/json``.
        html = content.get("text/html")
        if isinstance(html, dict) and html.get("schema"):
            content.pop("text/html")
            json_media = content.setdefault("application/json", {})
            json_media.setdefault("schema", html["schema"])
        if code not in STATUS_EXAMPLES:
            continue
        problem_media_types = [mt for mt, m in content.items() if _is_problem_detail_media(m)]
        if not problem_media_types:
            continue
        media = content[problem_media_types[0]]
        for mt in problem_media_types:
            content.pop(mt, None)
        # A schema-less problem+json placeholder (an earlier pass, or a route
        # that pre-declared the media type) is superseded by the real entry.
        content.pop(PROBLEM_JSON, None)
        media["example"] = STATUS_EXAMPLES[code]
        content[PROBLEM_JSON] = media


def add_default_problem_response(responses: dict[str, Any]) -> None:
    """Document the RFC 9457 catch-all as the operation's ``default`` response.

    Any status the operation does not list (an unmapped 5xx, a proxy-injected
    error) still arrives as Problem Details: ``unhandled_exception_handler``
    guarantees it for an unclaimed exception, so every operation — the bare
    public probes included — gets the same ``default``.
    """
    if "default" not in responses:
        responses["default"] = {
            "description": "Unexpected error, as RFC 9457 Problem Details.",
            "content": {
                PROBLEM_JSON: {
                    "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                    "example": STATUS_EXAMPLES["500"],
                }
            },
        }


def stamp_permission_metadata(
    method: str,
    path: str,
    operation: dict[str, Any],
    operation_auth: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Stamp the operation's ``security`` from its recovered identity dependency.

    The OpenAPI document models only the real authentication mechanism — an
    opaque bearer token (not a JWT), described as ``BearerAuth`` and, for the
    interactive authorization-code flow that issues it, ``OAuth2``. The per-operation
    permission/actor-type join is *not* expressed here: OpenAPI's ``security`` model
    cannot faithfully carry our OR-of-permissions semantics, the ``org:admin``
    superuser bypass, the typical-caller hint, or the fact that many permissions are
    enforced in the service layer rather than at the gateway. Encoding it as a
    fabricated OAuth2 flow
    would misrepresent enforcement, so that richer authorization reference lives
    in the endpoint reference (:mod:`jentic_one.shared.web.endpoint_reference`,
    served at ``GET /reference/endpoints.json``), which the CLI and docs SPA
    consume.

    Always sets an explicit per-operation ``security`` so downstream consumers
    never fall back to the global document-level requirement to decide whether an
    operation authenticates: an operation with no recovered identity dependency is
    stamped ``security: []`` (public); an authenticated one is stamped
    ``[{"BearerAuth": []}, {"OAuth2": []}]`` (either scheme, no per-operation
    scopes). This keeps the public/authenticated boundary derived
    from the actual route dependency rather than inferred.
    """
    info = operation_auth.get((method.upper(), path))
    if info is None or not info["authenticated"]:
        # No identity dependency recovered for this operation: it does not
        # authenticate. Mark it explicitly public so the boundary is unambiguous.
        operation["security"] = []
        responses = operation.get("responses", {})
        for code in ("401", "403"):
            responses.pop(code, None)
        return
    operation["security"] = authenticated_security()
