"""Web-edge seam contracts: protocols consumed via ``AppContainer``.

These protocols are **deliberately web-shaped** (FastAPI ``Request``/``Response``
in their signatures) — they are invoked only by the web layer, so they live here
rather than in ``shared/broker/protocols`` (which stays transport-neutral).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import Request, Response

from jentic_one.shared.auth.identity import Identity


@runtime_checkable
class UnregisteredUrlHandler(Protocol):
    """Intercepts broker traffic to an **unregistered** METHOD+URL.

    The seam for observing/serving traffic to hosts the registry does not
    know — audit sinks, catalog-suggestion UIs, monitor modes. Injected via
    ``AppContainer.unregistered_url_handler``; ``None`` (the default) preserves
    today's behaviour exactly.

    Scope — what fires it and what does not:

    - Fires **only** at the sync web edge's unregistered-URL discovery miss.
      That miss lives in the broker catch-all, and the broker runs as the
      **sole surface** of its process — so the handler is reachable only via
      ``jentic_one.broker.web.app.create_app`` (a combined app never includes
      the broker; setting the field there wires a hook that can never fire).
      The *pinned-revision* miss raises the identical ``operation_not_found``
      problem type but never invokes this handler — that miss is a caller pin
      error on a **registered** API, not an unregistered flow.
    - Fires for every catch-all method, including ``HEAD`` and ``OPTIONS``.
    - A ``Prefer: respond-async`` request with an unregistered URL **does**
      reach it (discovery precedes the async branch); the handler's response
      is returned synchronously. The async worker and the pinned re-resolve
      run post-discovery and never see an unregistered URL.
    - Runs **after** authentication (``RequireToolkitAccess``), so anonymous
      traffic to unregistered hosts never reaches it — it 401s first.

    Egress contract:

    - ``upstream_url`` has passed ``validate_upstream_url`` — the **pre-flight**
      allow/deny check only, which is TOCTOU-vulnerable by design (it resolves
      DNS once; the actual connection re-resolves — see
      ``jentic_one.shared.egress``). The connection-time layer that closes that
      window (``DnsPinningTransport``) belongs to the client, so **any fetch the
      handler performs must go through
      ``jentic_one.broker.adapters.http_client.HttpClientProvider`` (or a
      transport wrapped in ``DnsPinningTransport``)** to get DNS pinning.
    - An implementation that forwards must send the request **only to that
      exact URL**, re-validating if it mutates it — this hook must never
      become a door around the egress policy.

    Security & resilience tradeoff — the handler runs *instead of* the
    registered-operation pipeline, so **none** of the built-in controls apply:

    - The only controls that have run are authentication
      (``RequireToolkitAccess``), the per-actor execute rate limit, and the
      egress pre-check. No toolkit derivation, no permission-rule (PBAC)
      evaluation, no credential injection. An implementation that forwards is
      making a policy decision (e.g. monitor mode) and owns that decision's
      audit trail.
    - Core's body caps (``max_request_bytes(_by_type)``), response-size caps,
      transfer deadlines, and the resilience stack (retries, circuit breaking,
      bulkheads) do not apply. A forwarding implementation owns its own caps,
      timeouts, and transport.
    - Core records no execution row and emits no event — only the
      ``broker.unregistered_url.handled`` counter (outcome-attributed:
      ``handled``/``declined``/``error``).
    - ``request`` is the **raw inbound request**: it carries the caller's
      ``Authorization`` header (the platform token), cookies, and body — an
      implementation must never log or persist those verbatim (redaction
      rules apply). Through ``request.app.state`` it also reaches the full
      deployment context (DB sessions, decrypted key material, signing
      secrets, mutable config), so a handler is **fully trusted deployment
      code**, not a sandboxed plugin — wire one only from your own
      composition root.

    Return contract:

    - Return a complete ``Response`` to short-circuit — the router returns it
      verbatim (headers included, unvalidated), so a ``StreamingResponse``
      streams unbuffered — or ``None`` to fall through to the standard
      ``OperationNotFoundError`` (404).
    - A raised ``ProblemDetailException`` propagates as the handler's
      deliberate problem response. Any other exception is logged and counted
      (``outcome="error"``) by core and **falls through to the 404** — a
      broken handler degrades to today's behaviour instead of converting the
      miss into a 500.

    ``method`` is a convenience equal to ``request.method``; ``upstream_url``
    is the validated reconstruction (not derivable from ``request`` alone).
    ``__call__`` must be an ``async def`` — the router awaits it (the
    compliance base enforces this).
    """

    async def __call__(
        self, *, method: str, upstream_url: str, identity: Identity, request: Request
    ) -> Response | None: ...
