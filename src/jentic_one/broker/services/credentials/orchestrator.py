"""Credential orchestration — the broker-side ``CredentialInjector`` (§02b).

Extracts the resolve → refresh → inject sequence out of the web edge so the
**same** path is shared by the sync router and the async worker (via the shared
``CredentialInjector`` protocol — ``shared/jobs/`` never imports ``broker/``).

The service is transport-neutral in shape (it returns a shared ``InjectedAuth``)
but owns the credential-error → broker-domain-exception mapping so both
call-sites get identical problem+json semantics (plan.md §7.3 home).
"""

from __future__ import annotations

import uuid

import structlog

from jentic_one.broker.core.exceptions import (
    AgentDirective,
    AmbiguousMatchError,
    CredentialNeedsReconnectError,
    CredentialRefreshTransientError,
    ErrorOrigin,
    InvalidCredentialNameError,
)
from jentic_one.broker.core.exceptions import (
    CredentialNotProvisionedError as DomainCredentialNotProvisionedError,
)
from jentic_one.broker.core.injection import inject_auth
from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialNameNotFoundError,
    CredentialNotProvisionedError,
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_credential_access, emit_event_best_effort
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

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Identity,
        credential_name: str | None = None,
    ) -> InjectedAuth:
        """Resolve + inject the credential for the API tuple.

        Returns an empty ``InjectedAuth`` when the API tuple has no vendor (no
        credential path). Credential failures are mapped to broker-domain
        exceptions (424/409/401/502) so both call-sites render identical
        problem+json.
        """
        if not api_vendor:
            return _EMPTY

        api = APIReference(vendor=api_vendor, name=api_name or "", version=api_version or "")
        try:
            resolved = await CredentialResolver(self._ctx).resolve(
                api=api, caller=identity.sub, credential_name=credential_name
            )

            access_token: str | None = None
            if resolved.wire_type == CredentialType.OAUTH2:
                access_token = await TokenRefresher(self._ctx).ensure_fresh(
                    resolved=resolved, caller=identity.sub
                )

            result = inject_auth(resolved, ctx=self._ctx, access_token=access_token)
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
                )
            return InjectedAuth(
                headers=result.headers,
                query_params=result.query_params,
                cookies=result.cookies,
                server_variables=resolved.server_variables,
                credential_id=resolved.credential_id,
                credential_name=resolved.name,
            )
        except CredentialNotProvisionedError as exc:
            await self._emit_credential_failure(
                type=EventType.CREDENTIAL_NOT_PROVISIONED,
                summary=f"No credential provisioned for '{api.vendor}'",
                identity=identity,
            )
            raise self._not_provisioned(api, identity) from exc
        except CredentialNameNotFoundError as exc:
            raise InvalidCredentialNameError(
                detail=str(exc),
                type="credential_name_not_found",
                extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
            ) from exc
        except AmbiguousCredentialError as exc:
            raise AmbiguousMatchError(
                detail=str(exc),
                type="ambiguous_credential",
                extra={"candidates": [c.model_dump(mode="json") for c in exc.candidates]},
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
            # auth but never makes the upstream call (see plan §446 item 14b).
            await self._emit_credential_failure(
                type=EventType.CREDENTIAL_REFRESH_FAILED,
                summary=f"Credential refresh failed for '{api.vendor}'",
                identity=identity,
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
            raise CredentialRefreshTransientError(
                detail=str(exc), type="refresh_transient_error", origin=ErrorOrigin.UPSTREAM
            ) from exc

    async def _emit_credential_failure(
        self,
        *,
        type: str,
        summary: str,
        identity: Identity,
        tags: set[EventTag] | None = None,
    ) -> None:
        """Emit a credential-health event on the admin DB (best-effort)."""
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=type,
                    severity=EventSeverity.WARNING,
                    summary=summary,
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                    tags=tags,
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=type, exc_info=True)

    def _not_provisioned(
        self, api: APIReference, identity: Identity
    ) -> DomainCredentialNotProvisionedError:
        """Build the 424 with a ``prompt_human`` directive enabling a human handoff."""
        intent_id = f"intent_{uuid.uuid4().hex}"
        params: dict[str, object] = {"intent_id": intent_id, "vendor": api.vendor}

        base = self._ctx.config.broker.account_linking_base_url
        instruction = (
            f"No credential is connected for '{api.vendor}'; "
            "ask the user to connect the account before retrying."
        )
        if base:
            provisioning_url = (
                f"{base.rstrip('/')}/connect/{api.vendor}?actor={identity.sub}&intent={intent_id}"
            )
            params["provisioning_url"] = provisioning_url
            instruction = (
                f"No credential is connected for '{api.vendor}'. Ask the user to open "
                f"{provisioning_url} to authorize, then retry once they confirm."
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
