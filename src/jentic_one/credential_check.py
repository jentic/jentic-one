"""In-process credential checker: one safe read call that names why a credential fails.

Saving a credential used to store it without ever trying it, so a wrong key, a
wrong region or a missing scope surfaced as an agent's failed call several steps
later (#630; also #438, #948, #687). :class:`InProcessCredentialChecker` picks a
read call from the API's own spec, authenticates it with the broker's resolver
and injection code, sends it through the broker's HTTP runner (same egress
policy, DNS pinning, no redirects, size cap) under a short deadline, and maps
the answer to a :class:`CredentialCheckStatus`.

Composition layer, like ``wiring.py``: it needs the registry (to pick the call)
and the broker (to authenticate and send it), and control reaches it through
``CredentialCheckerProtocol``. It runs on demand in the control process, which
already decrypts OAuth client secrets for the connect flow. It never refreshes
an OAuth token: the broker owns refresh, and a second process refreshing on
SQLite (process-local locks) could race a rotating refresh token.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import structlog
from sqlalchemy import or_, select

from jentic_one.broker.adapters.http_client import build_client
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.sigv4 import SigV4SigningRunner
from jentic_one.broker.core.exceptions import (
    BrokerError,
    UpstreamResponseTooLargeError,
)
from jentic_one.broker.core.headers import REGION_MISMATCH_HINT
from jentic_one.broker.core.injection import InjectionResult, inject_auth
from jentic_one.broker.services.credentials.errors import CredentialNotProvisionedError
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.control.repos import CredentialRepository
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.url_index import merge_paths
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.services.inspect.inputs import build_operation_inputs
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.execution import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.shared.context import Context
from jentic_one.shared.credential_check import CredentialCheckResult, CredentialCheckStatus
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.events import emit_credential_access
from jentic_one.shared.models.api_identity import canonical_credential_scope, credential_covers
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.redaction import redact_url_query
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.url import apply_server_variables, has_host_server_variable
from jentic_one.shared.url_validation import validate_upstream_url

_logger = structlog.get_logger(__name__)

Status = CredentialCheckStatus

# ponytail: a fixed budget; make it config when an operator needs a slower API.
PROBE_TIMEOUT_S = 5.0

# Path segments that mark a "who am I" call: the most telling check of a key.
_WHOAMI_SEGMENTS = frozenset(
    {"me", "@me", "self", "user", "whoami", "account", "profile", "viewer"}
)

# Where an upstream says which scope a refused call needed: RFC 6750's
# ``WWW-Authenticate: Bearer error="insufficient_scope", scope="..."``, and
# GitHub's accepted-scope headers (classic and fine-grained tokens).
_SCOPE_PARAM = re.compile(r'scope="([^"]+)"')
_SCOPE_HEADERS = ("x-accepted-oauth-scopes", "x-accepted-github-permissions")

# A 400/403 whose text says the credential itself is bad, not under-scoped
# (Google answers a bad API key with 400, AWS a bad access key with 403).
_BAD_CREDENTIAL = re.compile(
    r"\b(?:invalid|not valid|incorrect|unrecognized)\b[^.\n]{0,40}"
    r"\b(?:key|token|credential|signature)"
    r"|\b(?:key|token|credential|signature)s?\b[^.\n]{0,40}\b(?:invalid|not valid|incorrect)\b"
    r"|invalidclienttokenid|signaturedoesnotmatch|invalidaccesskeyid|unrecognizedclient"
)
_AMBIGUOUS_EXPIRY = ("invalid or expired", "expired or invalid")


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """The read call a check makes: one GET taken from the API's own spec."""

    api: APIReference
    url: str
    """Server URL merged with the operation path; may still hold ``{server_var}`` placeholders."""
    defaults: dict[str, str] = field(default_factory=dict)
    scopes: tuple[str, ...] = ()
    """OAuth scopes the spec lists for the call (names what a 403 is missing)."""
    authenticated: bool = True
    """Whether the spec says the call needs auth; a public call proves reachability only."""


class _Verdict(Exception):  # noqa: N818 - control flow, not an error
    """Stop the check early with this result."""

    def __init__(self, status: Status, reason: str) -> None:
        super().__init__(reason)
        self.result = CredentialCheckResult(status=status, reason=reason)


def pick_probe_operation(
    rows: Iterable[tuple[str, str, Any]],
) -> tuple[str, str, dict[str, Any]] | None:
    """Pick the safest, most telling read call from ``(id, path, raw_operation)`` GET rows.

    Only a GET with no path parameter and no required query, header or body input
    qualifies: the check cannot invent arguments, and a write could have side
    effects. Among those it prefers a call the spec says needs auth (a public
    endpoint accepts any key), then a "who am I" path such as ``/me`` or
    ``/user``, then anything but the API root, then the shortest path. ``None``
    means nothing qualifies, so the check reports ``untested`` instead of guessing.
    """
    eligible: list[tuple[str, str, dict[str, Any]]] = []
    for op_id, path, raw in rows:
        op = raw if isinstance(raw, dict) else {}
        inputs = build_operation_inputs(op)
        if "{" in path or any(p.required for p in (*inputs.query, *inputs.header)):
            continue
        if inputs.body is not None and inputs.body.required:
            continue
        eligible.append((op_id, path, op))

    def rank(row: tuple[str, str, dict[str, Any]]) -> tuple[bool, bool, bool, int, int, str]:
        _, path, op = row
        segments = [s for s in path.lower().split("/") if s]
        return (
            not op.get("security"),
            _WHOAMI_SEGMENTS.isdisjoint(segments),
            not segments,
            len(segments),
            len(path),
            path,
        )

    return min(eligible, key=rank, default=None)


def classify(
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
    *,
    url: str,
    scopes: Sequence[str] = (),
) -> tuple[Status, str]:
    """Name what an upstream answer says about the credential that made the call."""
    host = urlsplit(url).hostname or url
    lowered = {k.lower(): v for k, v in headers.items()}
    challenge = lowered.get("www-authenticate", "")
    text = f"{challenge} {body[:4096].decode('utf-8', 'replace')}".lower()

    if 200 <= status_code < 300:
        return Status.OK, f"{host} accepted the credential (HTTP {status_code})."
    if 300 <= status_code < 400:
        moved_to = urlsplit(lowered.get("location", "")).hostname or "another URL"
        return Status.WRONG_BASE_URL, (
            f"{host} redirected the call to {moved_to} (HTTP {status_code}): "
            "the base URL or region is probably wrong."
        )
    if status_code in (400, 401, 403):
        if "expired" in text and not any(p in text for p in _AMBIGUOUS_EXPIRY):
            return Status.EXPIRED, (
                f"{host} says the credential has expired (HTTP {status_code}): "
                "rotate it or reconnect it."
            )
        if status_code == 401 or _BAD_CREDENTIAL.search(text):
            return Status.BAD_KEY, (
                f"{host} rejected the credential (HTTP {status_code}): "
                "the key or token is wrong or revoked."
            )
        if status_code == 403:
            match = _SCOPE_PARAM.search(challenge)
            needed = (
                match.group(1)
                if match
                else next((lowered[h] for h in _SCOPE_HEADERS if lowered.get(h)), "")
                or " ".join(scopes)
            )
            return Status.MISSING_SCOPE, (
                f"{host} knows the credential but refused this call (HTTP 403): "
                "a scope or permission is missing" + (f" (needs: {needed})." if needed else ".")
            )
    if status_code == 404:
        return Status.WRONG_BASE_URL, (
            f"{host} has no {urlsplit(url).path or '/'} (HTTP 404) although the API's spec "
            "lists it: the base URL, region or API version is probably wrong."
        )
    if status_code >= 500:
        return Status.UNREACHABLE, f"{host} failed with HTTP {status_code}; try again later."
    return (
        Status.UNTESTED,
        f"{host} answered HTTP {status_code}, which says nothing about the credential.",
    )


class InProcessCredentialChecker:
    """``CredentialCheckerProtocol`` over the registry, control and broker code in-process.

    ``runner`` is the transport seam: production builds a fresh broker runner per
    check (checks are rare and on demand); tests inject one over a fake upstream.
    """

    def __init__(
        self,
        ctx: Context,
        *,
        runner: UpstreamRunner | None = None,
        timeout_s: float = PROBE_TIMEOUT_S,
    ) -> None:
        self._ctx = ctx
        self._runner = runner
        self._timeout_s = timeout_s

    async def check(self, *, credential_id: str, identity: Identity) -> CredentialCheckResult:
        """Run the check; every failure, ours or the upstream's, is a verdict."""
        try:
            async with self._ctx.control_db.session() as session:
                credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                raise _Verdict(Status.UNTESTED, "The credential no longer exists.")
            if not credential.active:
                raise _Verdict(
                    Status.UNTESTED, "The credential is disabled; enable it to check it."
                )
            target = await self._find_target(
                credential.api_vendor, credential.api_name, credential.api_version
            )
            try:
                resolved = await CredentialResolver(self._ctx).resolve(
                    api=target.api,
                    caller=identity.sub,
                    credential_id=credential_id,
                    allowed_credential_ids=[credential_id],
                )
            except CredentialNotProvisionedError as exc:
                raise _Verdict(
                    Status.UNTESTED, f"The credential does not cover {target.api.vendor}."
                ) from exc
            result = await self.probe(resolved, target, identity=identity)
        except _Verdict as verdict:
            result = verdict.result
        except Exception:  # a crashed check must never turn a saved credential into a 500
            _logger.exception("credential_check.failed")
            result = CredentialCheckResult(
                Status.UNTESTED, "The check could not run; the error is in the server log."
            )
        _logger.info(
            "credential_check.completed",
            credential_id=credential_id,
            status=result.status.value,
            upstream_status=result.upstream_status,
            probe=result.probe,
        )
        return result

    async def probe(
        self, resolved: ResolvedCredential, target: ProbeTarget, *, identity: Identity
    ) -> CredentialCheckResult:
        """Authenticate ``target`` with ``resolved``, send it once, and classify the answer."""
        try:
            return await self._probe(resolved, target, identity)
        except _Verdict as verdict:
            return verdict.result

    async def _probe(
        self, resolved: ResolvedCredential, target: ProbeTarget, identity: Identity
    ) -> CredentialCheckResult:
        url = apply_server_variables(target.url, resolved.server_variables, target.defaults)
        if has_host_server_variable(url):
            raise _Verdict(
                Status.UNTESTED,
                f"The API's host is a template ({urlsplit(url).netloc}); set the credential's "
                "server_variables so the check knows which host to call.",
            )
        # Validated before any secret is decrypted or attached.
        try:
            url = validate_upstream_url(url, self._ctx.config.broker.egress)
        except ValueError as exc:
            raise _Verdict(
                Status.UNTESTED,
                f"The broker's egress policy does not allow {redact_url_query(url)}.",
            ) from exc

        injection = self._inject(resolved)
        async with self._ctx.admin_db.transaction() as session:
            await emit_credential_access(
                session,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                credential_id=resolved.credential_id,
                provider=resolved.provider,
                wire_type=resolved.wire_type.value,
                api_vendor=target.api.vendor,
                api_name=target.api.name,
                api_version=target.api.version,
            )

        url, headers = _with_auth(url, injection)
        probe = f"GET {redact_url_query(url)}"
        host = urlsplit(url).hostname or url
        request = RunnerRequest(
            method="GET",
            url=url,
            headers=headers,
            timeout_s=self._timeout_s,
            signing=injection.signing,
        )
        try:
            async with asyncio.timeout(self._timeout_s):
                answer = await self._run(request)
        except TimeoutError:
            return CredentialCheckResult(
                Status.UNREACHABLE, f"{host} did not answer within {self._timeout_s:g}s.", probe
            )
        except UpstreamResponseTooLargeError:
            return CredentialCheckResult(
                Status.UNTESTED, f"{host} answered with a body over the broker's size cap.", probe
            )
        except BrokerError as exc:  # transport failure; ``detail`` never quotes request material
            return CredentialCheckResult(
                Status.UNREACHABLE, f"Could not reach {host} ({exc.detail.rstrip('.')}).", probe
            )
        except ValueError:  # the DNS-pinning transport: no such host, or a blocked address
            return CredentialCheckResult(
                Status.UNREACHABLE,
                f"{host} did not resolve, or resolves to an address the egress policy blocks: "
                "check the base URL and the network.",
                probe,
            )

        status, reason = classify(
            answer.status_code, answer.headers, answer.body, url=url, scopes=target.scopes
        )
        if status is Status.OK and not target.authenticated:
            reason += " The spec does not say this call needs auth, so it proves reachability only."
        if status in (Status.BAD_KEY, Status.MISSING_SCOPE) and has_host_server_variable(
            target.url
        ):
            # #630: the spec default region quietly answers "invalid key" for a key
            # from another region, so say which values were the credential's own.
            pinned = resolved.server_variables or {}
            used = ", ".join(
                f"{k}={v}" if k in pinned else f"{k}={v} (the spec default)"
                for k, v in sorted({**target.defaults, **pinned}.items())
            )
            reason = f"{reason} {REGION_MISMATCH_HINT} This check used {used}."
        return CredentialCheckResult(status, reason, probe, answer.status_code)

    def _inject(self, resolved: ResolvedCredential) -> InjectionResult:
        """Decrypt and inject with the broker's code; use a stored OAuth token, never refresh it."""
        try:
            access_token = None
            if resolved.wire_type == CredentialType.OAUTH2:
                access_token = self._stored_access_token(resolved)
            return inject_auth(resolved, ctx=self._ctx, access_token=access_token)
        except DecryptionError as exc:
            raise _Verdict(
                Status.UNTESTED,
                "The stored secret cannot be decrypted with the configured encryption keys; "
                "re-add the credential.",
            ) from exc
        except ValueError as exc:  # inject_auth: a type with no stored secret
            raise _Verdict(Status.UNTESTED, "The credential has no stored secret to send.") from exc

    def _stored_access_token(self, resolved: ResolvedCredential) -> str:
        if resolved.encrypted_access_token is None:
            raise _Verdict(
                Status.UNTESTED,
                "The credential has no access token yet: finish connecting it, then check again.",
            )
        expires = resolved.token_expires_at
        if expires is not None and expires <= datetime.now(UTC):
            when = f"{expires:%Y-%m-%d %H:%M} UTC"
            if resolved.encrypted_refresh_token is None and resolved.provider_account_ref is None:
                raise _Verdict(
                    Status.EXPIRED,
                    f"The access token expired at {when} and there is no refresh token: "
                    "reconnect the credential.",
                )
            raise _Verdict(
                Status.UNTESTED,
                f"The access token expired at {when}. The broker refreshes it on the next call; "
                "a check never refreshes tokens.",
            )
        return self._ctx.encryption.decrypt(resolved.encrypted_access_token)

    async def _run(self, request: RunnerRequest) -> RunnerResult:
        if self._runner is not None:
            return await self._runner.run(request)
        upstream = self._ctx.config.broker.resilience.upstream
        client = build_client(upstream, self._ctx.config.broker.egress)
        try:
            runner = SigV4SigningRunner(
                HttpRunner(client, max_response_bytes=upstream.max_response_bytes)
            )
            return await runner.run(request)
        finally:
            await client.aclose()

    async def _find_target(self, vendor: str, name: str | None, version: str | None) -> ProbeTarget:
        """The probe call for the first registered API the credential covers that has one."""
        scope = canonical_credential_scope(vendor=vendor, name=name, version=version)
        async with self._ctx.registry_db.session() as session:
            apis = await ApiRepository.list_by_vendor(session, scope.vendor)
            covered = sorted(
                (
                    a
                    for a in apis
                    if a.current_revision_id is not None
                    and credential_covers(scope, vendor=a.vendor, name=a.name, version=a.version)
                ),
                key=lambda a: (a.name, a.version),
            )
            if not covered:
                raise _Verdict(
                    Status.UNTESTED,
                    f"No registered API matches the credential ({vendor}); import the API, "
                    "then check again.",
                )
            if len(covered) > 1:
                # Never choose between specs: importing an API (apis:write) is a different
                # permission from using this credential, so a second spec under the same
                # vendor could otherwise point the check, and the secret, at its own host.
                raise _Verdict(
                    Status.UNTESTED,
                    f"The credential covers {len(covered)} registered {vendor} APIs; a check "
                    "only calls the one API a credential is scoped to, so nothing was sent.",
                )
            for api in covered:
                rows = await session.execute(
                    select(Operation.id, Operation.path, Operation.raw_operation).where(
                        Operation.revision_id == api.current_revision_id,
                        Operation.method == "GET",
                        Operation.deprecated.is_(False),
                    )
                )
                picked = pick_probe_operation(rows.tuples())
                if picked is None:
                    continue
                op_id, path, op = picked
                servers = (
                    (
                        await session.execute(
                            select(Server).where(
                                Server.revision_id == api.current_revision_id,
                                or_(Server.operation_id == op_id, Server.operation_id.is_(None)),
                            )
                        )
                    )
                    .unique()
                    .scalars()
                    .all()
                )
                # Operation-level servers win, like inspect's server resolution.
                server = next((s for s in servers if s.operation_id == op_id), None) or next(
                    iter(servers), None
                )
                if server is None or not server.url.startswith(("http://", "https://")):
                    raise _Verdict(
                        Status.UNTESTED,
                        f"{api.vendor}/{api.name} declares no absolute server URL to call.",
                    )
                return ProbeTarget(
                    api=APIReference(vendor=api.vendor, name=api.name, version=api.version),
                    url=merge_paths(server.url, path),
                    defaults={
                        v.name: v.default_value
                        for v in server.variables
                        if v.default_value is not None
                    },
                    scopes=_spec_scopes(op),
                    authenticated=bool(op.get("security")),
                )
        raise _Verdict(
            Status.UNTESTED,
            "The API's spec has no read call the check can make on its own (a GET with no "
            "required input), so nothing was sent.",
        )


def _spec_scopes(op: Mapping[str, Any]) -> tuple[str, ...]:
    """The OAuth scopes an operation's effective ``security`` lists, in order, deduplicated."""
    scopes: dict[str, None] = {}
    for requirement in op.get("security") or []:
        if isinstance(requirement, dict):
            for names in requirement.values():
                scopes.update(dict.fromkeys(n for n in names or [] if isinstance(n, str)))
    return tuple(scopes)


def _with_auth(url: str, injection: InjectionResult) -> tuple[str, dict[str, str]]:
    """Attach the injected query parameters, headers and cookies (the broker's own rules)."""
    if injection.query_params:
        parts = urlsplit(url)
        query = "&".join(q for q in (parts.query, urlencode(injection.query_params)) if q)
        url = urlunsplit(parts._replace(query=query))
    headers = {"Accept": "application/json", **injection.headers}
    if injection.cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in injection.cookies.items())
    return url, headers
