"""Credential orchestration — the broker-side ``CredentialInjector``.

Extracts the resolve → refresh → inject sequence out of the web edge so the
**same** path is shared by the sync router and the async worker (via the shared
``CredentialInjector`` protocol — ``shared/jobs/`` never imports ``broker/``).

The service is transport-neutral in shape (it returns a shared ``InjectedAuth``)
but owns the credential-error → broker-domain-exception mapping so both
call-sites get identical problem+json semantics.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from typing import Any

import structlog

from jentic_one.broker.core.exceptions import (
    AgentDirective,
    AmbiguousMatchError,
    CredentialNeedsReconnectError,
    CredentialRefreshTransientError,
    CredentialUndecryptableError,
    ErrorOrigin,
    InvalidCredentialNameError,
    ambiguous_credential_binding_directive,
    api_connect_hint,
    api_connect_parameters,
    connect_parameters,
    suggested_permission_rules,
)
from jentic_one.broker.core.exceptions import (
    CredentialNotProvisionedError as DomainCredentialNotProvisionedError,
)
from jentic_one.broker.core.injection import inject_auth
from jentic_one.broker.services.credentials.connect_target import resolve_connect_target
from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialIdNotFoundError,
    CredentialNameNotFoundError,
    CredentialNotProvisionedError,
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.provisioning import open_session_provisioning_url
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.shared.access_guidance import ConnectTarget
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ConnectableRegistrationSourceProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.events import (
    emit_credential_access,
    emit_event_best_effort,
    summary_label,
    valid_trace_id_or_none,
)
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.models.events import ErrorSource, EventSeverity, EventTag, EventType
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger()

_EMPTY = InjectedAuth(headers={}, query_params={}, cookies={})


class CredentialService:
    """Broker-side ``CredentialInjector``: resolve, refresh, decrypt, inject.

    Implements the shared ``CredentialInjector`` protocol so it can be injected
    into the async worker without ``shared/jobs/`` importing ``broker/``.
    """

    def __init__(
        self,
        ctx: Context,
        *,
        connect_registrations: ConnectableRegistrationSourceProtocol | None = None,
    ) -> None:
        self._ctx = ctx
        # Shared OAuth-app registrations the 424 directive may suggest; ``None``
        # reads them from the control DB on the denial path.
        self._connect_registrations = connect_registrations

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Identity,
        credential_name: str | None = None,
        credential_id: str | None = None,
        allowed_credential_ids: Collection[str] | None = None,
        trace_id: str | None = None,
        preresolved: ResolvedCredential | None = None,
        request_server_variables: Mapping[str, str] | None = None,
        server_variables_unresolved: bool = False,
    ) -> InjectedAuth:
        """Resolve + inject the credential for the API tuple.

        Returns an empty ``InjectedAuth`` when the API tuple has no vendor (no
        credential path). Credential failures are mapped to broker-domain
        exceptions (424/409/401/502) so both call-sites render identical
        problem+json.

        ``credential_id`` / ``allowed_credential_ids`` are the direct-binding
        path (theme-5 Phase 2): the allowed set is the **injection boundary**
        (Q-02 — only credentials the caller is bound to may resolve; ``None``
        keeps the legacy unfiltered behaviour, an empty set denies all), and the
        id is the authoritative ``Jentic-Credential-Id`` tie-breaker. When the
        boundary is active an ambiguity maps to ``ambiguous_credential_binding``
        (candidates are the caller's own bound credentials) instead of the
        legacy ``ambiguous_credential``.

        ``trace_id`` is stamped onto the ``CREDENTIAL_ACCESSED`` audit event so
        an operator inspecting an execution can join the credential-use record
        back to the specific execution that triggered it (#740). Optional so
        non-execution call-sites (e.g. bind-time probes) don't
        have to fabricate one. A malformed value degrades to an uncorrelated
        event rather than failing the injection (#903).

        ``preresolved`` short-circuits resolution: the sync router's direct
        path already ran :meth:`select` (it needed the credential id for rule
        evaluation *before* any secret is decrypted), so injection reuses that
        result instead of resolving twice.
        """
        if not api_vendor:
            return _EMPTY

        api = APIReference(vendor=api_vendor, name=api_name or "", version=api_version or "")
        resolved = (
            preresolved
            if preresolved is not None
            else await self._resolve_mapped(
                api,
                identity,
                credential_name=credential_name,
                credential_id=credential_id,
                allowed_credential_ids=allowed_credential_ids,
                request_server_variables=request_server_variables,
                server_variables_unresolved=server_variables_unresolved,
            )
        )
        try:
            try:
                access_token: str | None = None
                if resolved.wire_type == CredentialType.OAUTH2:
                    access_token = await TokenRefresher(self._ctx).ensure_fresh(
                        resolved=resolved, caller=identity.sub
                    )

                result = inject_auth(resolved, ctx=self._ctx, access_token=access_token)
            except DecryptionError as exc:
                # A blob resolved by id/name but its ciphertext will not
                # decrypt: the encryption key that produced it is gone (a
                # reinstall regenerated it under the same key id, a hand
                # rotation dropped the retired entry, or a DB was restored
                # under a different key). The agent cannot self-recover —
                # only an operator can re-add the credential — so map to a
                # dedicated 424 with a prompt_human directive and flag the
                # event requires_action so it reaches the Action Inbox
                # (unlike not_provisioned/refresh_failed, no agent-side
                # reconnect can fix this). Redaction: carry the credential
                # id (opaque, not a secret) in extra; never ciphertext or
                # key material.
                await self._emit_credential_failure(
                    type=EventType.CREDENTIAL_UNDECRYPTABLE,
                    summary=(
                        f"Credential {summary_label(resolved.name, resolved.credential_id)} "
                        f"cannot be decrypted for '{api.vendor}'"
                    ),
                    identity=identity,
                    credential_owner=resolved.created_by,
                    requires_action=True,
                    data={"credential_id": resolved.credential_id, "api_vendor": api.vendor},
                )
                raise CredentialUndecryptableError(
                    detail=(
                        f"Credential '{resolved.credential_id}' for "
                        f"'{api.vendor}' cannot be decrypted with the "
                        "configured encryption keys"
                    ),
                    type="credential_undecryptable",
                    extra={
                        "credential_id": resolved.credential_id,
                        "api_vendor": api.vendor,
                    },
                    directive=AgentDirective(
                        strategy="prompt_human",
                        parameters={
                            "credential_id": resolved.credential_id,
                            "vendor": api.vendor,
                        },
                        human_readable_instruction=(
                            f"The stored credential for '{api.vendor}' can "
                            "no longer be decrypted. Ask an operator to "
                            "remove and re-add it; retrying will not fix "
                            "this."
                        ),
                    ),
                ) from exc

            async with self._ctx.admin_db.transaction() as session:
                await emit_credential_access(
                    session,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                    credential_id=resolved.credential_id,
                    provider=resolved.provider,
                    wire_type=resolved.wire_type.value,
                    api_vendor=api.vendor,
                    api_name=api.name,
                    api_version=api.version,
                    credential_owner=resolved.created_by,
                    credential_name=resolved.name,
                    # The resolver that authenticated this caller already read
                    # the agent row, so the summary can name the actor the way
                    # it names the credential (#1543); empty degrades to the id.
                    actor_name=identity.actor_name,
                    # Sanitised: emit_event raises on a malformed trace_id, and
                    # a 500 here would fail the whole execute request (#903).
                    trace_id=valid_trace_id_or_none(trace_id),
                )
            return InjectedAuth(
                headers=result.headers,
                query_params=result.query_params,
                cookies=result.cookies,
                server_variables=resolved.server_variables,
                credential_id=resolved.credential_id,
                credential_name=resolved.name,
                signing=result.signing,
            )
        except CredentialNotProvisionedError as exc:
            # Defensive: resolve-phase mapping lives in ``_resolve_mapped``; kept
            # here so a downstream raise (e.g. an unknown wire type surfacing
            # late) still renders the canonical 424 instead of a 500.
            await self._emit_credential_failure(
                type=EventType.CREDENTIAL_NOT_PROVISIONED,
                summary=f"No credential provisioned for '{api.vendor}'",
                identity=identity,
            )
            raise await self._not_provisioned(
                api, identity, connect=await self._connect_target(api)
            ) from exc
        except RefreshInvalidGrantError as exc:
            # Jentic-side auth failure: our OAuth refresh against the token
            # endpoint was rejected (invalid_grant). The auth source rides as a
            # tag on the single ``credential_refresh_failed`` event rather than a
            # separate ``auth_failure`` — the flat telemetry payload carries no
            # per-request correlation id, so two same-timestamp events would be
            # indistinguishable from two concurrent requests and permanently skew
            # the funnel (see #446 review, item 3). The upstream-rejected
            # (auth_thirdparty) half is not observable here — inject() prepares
            # auth but never makes the upstream call.
            await self._emit_credential_failure(
                type=EventType.CREDENTIAL_REFRESH_FAILED,
                summary=(
                    f"Credential {summary_label(resolved.name, resolved.credential_id)} "
                    f"refresh failed for '{api.vendor}'"
                ),
                identity=identity,
                credential_owner=resolved.created_by,
                tags={ErrorSource.AUTH_JENTIC},
            )
            raise CredentialNeedsReconnectError(
                detail=str(exc),
                type="credential_needs_reconnect",
                directive=AgentDirective(
                    strategy="prompt_human",
                    human_readable_instruction="The connected credential must be reconnected.",
                ),
            ) from exc
        except RefreshTransientError as exc:
            # ``str(exc)`` is the credential id + exception class only (the
            # refresher never forwards raw exception text); ``from None`` keeps
            # any chained provider/transport error out of rendered tracebacks.
            raise CredentialRefreshTransientError(
                detail=str(exc), type="refresh_transient_error", origin=ErrorOrigin.UPSTREAM
            ) from None

    async def select(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Identity,
        credential_name: str | None = None,
        credential_id: str | None = None,
        allowed_credential_ids: Collection[str] | None = None,
        request_server_variables: Mapping[str, str] | None = None,
        server_variables_unresolved: bool = False,
        method: str = "",
        path: str = "",
    ) -> ResolvedCredential | None:
        """Resolve-only credential selection — no refresh, decrypt, or audit.

        The direct-binding path (theme-5 Phase 2) must know *which* credential
        was selected **before** permission rules can be evaluated (rules are
        keyed on the ``(agent, credential)`` binding) — and enforcement must
        happen before any secret is touched or a ``CREDENTIAL_ACCESSED`` audit
        event fires. This runs the same resolution + error mapping as
        :meth:`inject` and returns the resolved metadata; pass it back to
        :meth:`inject` as ``preresolved`` to avoid resolving twice.

        ``method`` / ``path`` are the request's, for the 424 directive's
        ``suggested_rules``.

        Returns ``None`` when the API tuple has no vendor (no credential path).
        """
        if not api_vendor:
            return None
        api = APIReference(vendor=api_vendor, name=api_name or "", version=api_version or "")
        return await self._resolve_mapped(
            api,
            identity,
            credential_name=credential_name,
            credential_id=credential_id,
            allowed_credential_ids=allowed_credential_ids,
            request_server_variables=request_server_variables,
            server_variables_unresolved=server_variables_unresolved,
            method=method,
            path=path,
        )

    async def _resolve_mapped(
        self,
        api: APIReference,
        identity: Identity,
        *,
        credential_name: str | None,
        credential_id: str | None,
        allowed_credential_ids: Collection[str] | None,
        request_server_variables: Mapping[str, str] | None = None,
        server_variables_unresolved: bool = False,
        method: str = "",
        path: str = "",
    ) -> ResolvedCredential:
        """Resolve via ``CredentialResolver``, mapping errors to the broker taxonomy.

        Shared by :meth:`inject` and :meth:`select` so both surfaces render
        identical problem+json for the resolve-phase failures
        (424/400/409).
        """
        try:
            return await CredentialResolver(self._ctx).resolve(
                api=api,
                caller=identity.sub,
                credential_name=credential_name,
                credential_id=credential_id,
                allowed_credential_ids=allowed_credential_ids,
                request_server_variables=request_server_variables,
                server_variables_unresolved=server_variables_unresolved,
            )
        except CredentialNotProvisionedError as exc:
            await self._emit_credential_failure(
                type=EventType.CREDENTIAL_NOT_PROVISIONED,
                summary=f"No credential provisioned for '{api.vendor}'",
                identity=identity,
            )
            raise await self._not_provisioned(
                api,
                identity,
                connect=await self._connect_target(api),
                suggested_rules=suggested_permission_rules(method=method, path=path),
            ) from exc
        except CredentialNameNotFoundError as exc:
            raise InvalidCredentialNameError(
                detail=str(exc),
                type="credential_name_not_found",
                extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
            ) from exc
        except CredentialIdNotFoundError as exc:
            # The Jentic-Credential-Id tie-breaker named a credential that is not
            # among the caller's covering candidates. Same class of caller error
            # as a bad Jentic-Credential-Name (fix the header value and retry) —
            # candidates carried so the caller can pick a valid id.
            raise InvalidCredentialNameError(
                detail=str(exc),
                type="credential_id_not_found",
                extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
            ) from exc
        except AmbiguousCredentialError as exc:
            # On the direct-binding path (allowed set present) the candidates are
            # the caller's own bound credentials — a *binding* ambiguity resolved
            # with the Jentic-Credential-Id header — so it gets the Phase-2 wire
            # type + directive. The legacy unfiltered path keeps its vocabulary.
            if allowed_credential_ids is not None:
                raise AmbiguousMatchError(
                    detail=str(exc),
                    type="ambiguous_credential_binding",
                    extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
                    directive=ambiguous_credential_binding_directive(
                        [c.id for c in exc.candidates]
                    ),
                ) from exc
            raise AmbiguousMatchError(
                detail=str(exc),
                type="ambiguous_credential",
                extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
            ) from exc

    async def _emit_credential_failure(
        self,
        *,
        type: str,
        summary: str,
        identity: Identity,
        credential_owner: str | None = None,
        tags: set[EventTag] | None = None,
        requires_action: bool = False,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit a credential-health event on the admin DB (best-effort).

        ``actor_id`` is the identity whose request hit the failure. When the
        failure concerns a resolved credential, ``credential_owner`` names its
        owner as the event's ``created_by`` so the owner sees it under
        owner-scoped event reads; otherwise ``created_by`` is the actor.
        """
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=type,
                    severity=EventSeverity.WARNING,
                    summary=summary,
                    created_by=credential_owner or identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                    tags=tags,
                    requires_action=requires_action,
                    data=data,
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=type, exc_info=True)

    async def _connect_target(self, api: APIReference) -> ConnectTarget | None:
        """The connect target the 424 suggests, resolved before the error is built."""
        return await resolve_connect_target(
            self._ctx, api, registrations=self._connect_registrations
        )

    async def _not_provisioned(
        self,
        api: APIReference,
        identity: Identity,
        *,
        connect: ConnectTarget | None,
        suggested_rules: list[dict[str, Any]] | None = None,
    ) -> DomainCredentialNotProvisionedError:
        """Build the 424 with a ``prompt_human`` directive enabling a human handoff.

        When the denied agent already has an open connect session for the API,
        the directive carries its owner deep link as ``provisioning_url`` and
        tells the agent to relay it rather than start another connect (no
        ``suggested_command`` / ``connect``). Otherwise, when a vendor-registry
        entry or a shared OAuth-app registration covers the API (``connect``),
        the provisioning leg is agent-initiable — the directive carries a
        runnable ``parameters.suggested_command`` (``jentic connect <key>``,
        the connect key, never the API identity), the structured
        ``parameters.connect`` and the prose teaches the relay loop. With no
        such target but ``control.connect.manual_flows_enabled`` on, the API
        itself is the target (``jentic connect --api …`` and
        ``parameters.connect.api``). Otherwise the ask stays with the
        operator (same pattern as the 403
        ``no_credential_binding`` directive); approval stays human either way.
        ``parameters.vendor`` is the API's vendor axis, not a connect key.
        """
        intent_id = f"intent_{uuid.uuid4().hex}"
        provisioning_url = await open_session_provisioning_url(
            self._ctx, identity=identity, api=api
        )
        params: dict[str, object] = {
            "intent_id": intent_id,
            "vendor": api.vendor,
            **connect_parameters(None if provisioning_url else connect, suggested_rules),
        }
        connect_command = connect.cli_command() if connect is not None else None
        # Off every connect target, the API itself is the connect target when
        # the deployment takes API connect requests (the control gate).
        connect_api = (
            connect is None
            and self._ctx.config.control.connect.manual_flows_enabled
            and bool(api.vendor and api.name and api.version)
        )
        if connect_api and not provisioning_url:
            params.update(
                api_connect_parameters(vendor=api.vendor, name=api.name, version=api.version)
            )

        if provisioning_url:
            params["provisioning_url"] = provisioning_url
            instruction = (
                f"No credential is connected for '{api.vendor}' yet, and your request to "
                "connect one is still waiting for your human operator. Do not start "
                f"another: relay {provisioning_url} to them to approve it, then retry once "
                "they confirm."
            )
        elif connect_command:
            instruction = (
                f"No credential is connected for '{api.vendor}'. Start connecting one "
                f"yourself: run `{connect_command}` (or call the "
                "request_connection tool) and relay the approval_url to your human "
                "operator — they approve it in the browser; you cannot. Once they "
                "confirm, verify the new binding with whoami and retry."
            )
        elif connect_api:
            hint = api_connect_hint(vendor=api.vendor, name=api.name, version=api.version)
            instruction = (
                f"No credential is connected for '{api.vendor}'. Start the request "
                f"yourself: {hint}, and relay the approval_url to your human operator — "
                "a human enters the credential in the browser, which can take a while, so "
                "end your turn and retry later."
            )
        else:
            instruction = (
                f"No credential is connected for '{api.vendor}'; "
                "ask the user to connect the account before retrying."
            )

        return DomainCredentialNotProvisionedError(
            detail=f"No credential provisioned for '{api.vendor}'.",
            type="credential_not_provisioned",
            extra={"intent_id": intent_id},
            directive=AgentDirective(
                strategy="prompt_human",
                parameters=params,
                human_readable_instruction=instruction,
            ),
        )
