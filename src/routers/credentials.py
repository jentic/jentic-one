"""Upstream API credentials vault routes."""

import json
import logging
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

import src.vault as vault
from src.audit import persist_audit, query_audit
from src.auth import client_ip, require_human_session
from src.config import JENTIC_PUBLIC_HOSTNAME
from src.db import get_db
from src.models import CredentialCreate, CredentialOut, CredentialPatch
from src.routers.catalog import ensure_catalog_api_imported, lazy_import_catalog_workflows
from src.routers.toolkits import check_credential_policy


log = logging.getLogger("jentic")
audit_log = logging.getLogger("jentic.audit")

# auth_type values written only by internal integrations — never settable via the
# public create/patch API. `pipedream_oauth` is written by the Pipedream sync;
# `JenticApiKey` marks the internal jentic-mini admin key.
_RESERVED_AUTH_TYPES = frozenset({"pipedream_oauth", "JenticApiKey"})


def _self_api_id() -> str:
    return JENTIC_PUBLIC_HOSTNAME


async def _agent_has_credential_write_permission(
    toolkit_id: str | None, method: str, path: str
) -> bool:
    """Check if an agent toolkit has been explicitly granted credential write access
    via a policy rule on the internal jentic-mini credential.
    Human sessions always bypass this check (handled by the caller).
    """
    if not toolkit_id:
        return False
    cred_ids = await vault.get_credential_ids_for_route(toolkit_id, _self_api_id())
    if not cred_ids:
        return False
    for cred_id in cred_ids:
        allowed, _ = await check_credential_policy(cred_id, method=method, path=path)
        if allowed:
            return True
    return False


router = APIRouter(prefix="/credentials")


async def _get_confirmed_scheme(api_id: str, scheme_name: str | None) -> dict | None:
    """
    Return the confirmed overlay row for (api_id, scheme_name), or None.
    If scheme_name is None, returns the first confirmed overlay for the API.
    """
    async with get_db() as db:
        if scheme_name:
            async with db.execute(
                """SELECT id, overlay FROM api_overlays
                   WHERE api_id=? AND status='confirmed'
                   AND json_extract(overlay, '$.actions[0].update.components.securitySchemes."' || ? || '"') IS NOT NULL
                   LIMIT 1""",
                (api_id, scheme_name),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT id, overlay FROM api_overlays WHERE api_id=? AND status='confirmed' LIMIT 1",
                (api_id,),
            ) as cur:
                row = await cur.fetchone()
    return row


async def api_has_native_scheme(api_id: str) -> bool:
    """True if the API's own OpenAPI spec defines at least one security scheme."""
    async with get_db() as db:
        async with db.execute("SELECT spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return False
    try:
        with open(row[0]) as f:
            raw = f.read()
        if row[0].endswith((".yaml", ".yml")):
            spec = yaml.safe_load(raw)
        else:
            try:
                spec = json.loads(raw)
            except json.JSONDecodeError:
                spec = yaml.safe_load(raw)
        schemes = spec.get("components", {}).get("securitySchemes", {})
        return bool(schemes)
    except Exception:
        return False


@router.post(
    "",
    response_model=CredentialOut,
    status_code=201,
    summary="Store an upstream API credential — add a secret to the vault for broker injection",
    openapi_extra={
        "requestBody": {
            "description": "Credential details: label for identification, encrypted value (API key/token/password), optional identity (username/client ID), API ID, and auth type"
        }
    },
)
async def create(body: CredentialCreate, request: Request):
    """Store an encrypted credential in the vault for automatic broker injection.

    Values are encrypted at rest and **never returned** after creation. Set `api_id` to
    bind the credential to an API; the broker will inject it automatically when proxying
    calls to that API.

    ---

    ### `auth_type` reference

    Set `auth_type` to tell the broker how to inject the credential into upstream requests.
    Based on the [Postman auth type taxonomy](https://learning.postman.com/docs/sending-requests/authorization/authorization-types/).

    | `auth_type` | Status | Broker injects | `value` | `identity` |
    |---|---|---|---|---|
    | `bearer` | ✅ implemented | `Authorization: Bearer {value}` | Token, PAT, or OAuth access token | Not used |
    | `basic` | ✅ implemented | `Authorization: Basic base64({identity or "token"}:{value})` | Password or PAT | Username (optional — defaults to `"token"` if omitted, works for GitHub PATs) |
    | `apiKey` | ✅ implemented | Custom header or query param `= {value}` | API key | For **compound schemes** (e.g. Discourse `Api-Key` + `Api-Username`): set `identity` to the username — one credential covers both headers when the overlay uses canonical `Secret`/`Identity` scheme names |
    | `oauth2` | ⚠️ partial | `Authorization: Bearer {value}` — token must be pre-obtained | Access token (Pipedream-managed flows only via `pipedream_oauth`) | Not used |
    | `digest` | 🔲 planned | RFC 2617 challenge-response (nonce/HMAC handshake) | Password | Username |
    | `jwt` | 🔲 planned | `Authorization: Bearer {signed_jwt}` — auto-generated from signing key | Private key or secret | Key ID (`kid`) — signing algorithm and claims go in `context` |
    | `aws_sig4` | 🔲 planned | `Authorization: AWS4-HMAC-SHA256 ...` signed headers | AWS Secret Access Key | AWS Access Key ID — region and service go in `context` |
    | `oauth1` | 🔲 planned | HMAC-SHA1 signed request (nonce + timestamp) | OAuth secret | OAuth consumer key |
    | `hawk` | 🔲 planned | `Authorization: Hawk ...` HMAC request signing | Hawk secret | Hawk key ID |
    | `ntlm` | 🔲 not planned | Windows NTLM challenge-response | Password | Username + domain |
    | `akamai_edgegrid` | 🔲 not planned | Akamai EdgeGrid signing | Client secret | Client token + access token in `context` |

    **Notes:**
    - `pipedream_oauth` is a reserved value written by the Pipedream integration — do not set it manually.
    - For `oauth2` full flows (auth code, client credentials, PKCE, token refresh) see the roadmap.
    - `context` (not yet exposed) will hold auxiliary fields for multi-value schemes (JWT claims, AWS region/service, etc.).

    ---

    ### Workflow

    1. Call `GET /apis/{api_id}` — check `security_schemes` and `credentials_configured` to find gaps.
    2. Post this endpoint with `api_id`, `auth_type`, `value` (and `identity` if needed).
    3. The broker injects the credential automatically on every proxied call to that API.
    4. To scope a credential to a specific toolkit: `POST /toolkits/{id}/credentials`.

    If the API has no registered security scheme yet, submit an overlay first: `POST /apis/{api_id}/overlays`.
    """
    if not request.state.is_human_session:
        if not await _agent_has_credential_write_permission(
            request.state.toolkit_id, "POST", "/credentials"
        ):
            raise HTTPException(
                status_code=403,
                detail="Storing credentials requires a human session, or an agent key with an explicit POST /credentials allow rule on the jentic-mini credential.",
            )
    api_id = getattr(body, "api_id", None)
    scheme_name = getattr(body, "auth_type", None)

    # Reserved auth types are written only by internal integrations (Pipedream sync
    # writes `pipedream_oauth`; `JenticApiKey` is the internal admin-key marker).
    # Reject them on the public write path so a caller can't self-assign a reserved
    # value and create an unsupported credential with no backing broker account.
    if scheme_name in _RESERVED_AUTH_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"auth_type '{scheme_name}' is reserved for internal integrations and "
                "cannot be set manually."
            ),
        )

    if api_id and scheme_name != "none":
        # ── Lazy import: if api_id is a catalog API not yet locally registered, import it now ──
        resolved_id = await ensure_catalog_api_imported(api_id)
        if resolved_id and resolved_id != api_id:
            # Import changed the api_id (e.g. 'discord.com' → 'discord.com/api')
            api_id = resolved_id
            body = body.model_copy(update={"api_id": api_id})

        # Also import associated catalog workflows (fire-and-forget on error)
        try:
            await lazy_import_catalog_workflows(api_id)
        except Exception as _wf_err:
            log.warning("Workflow auto-import failed for '%s' (non-fatal): %s", api_id, _wf_err)

        # Check native spec first
        has_native = await api_has_native_scheme(api_id)
        if not has_native:
            # Check for any overlay (pending OR confirmed) — pending is enough to proceed.
            # The first successful broker call will confirm it. This is intentional bootstrap flow:
            # overlay submitted → credential added → broker call → overlay confirmed.
            async with get_db() as db:
                async with db.execute(
                    "SELECT id FROM api_overlays WHERE api_id=? LIMIT 1",
                    (api_id,),
                ) as cur:
                    any_overlay = await cur.fetchone()
            if not any_overlay:
                # No overlay at all — instruct the agent to contribute one
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "no_security_scheme",
                        "api_id": api_id,
                        "message": (
                            f"No security scheme is on record for '{api_id}'. "
                            f"Before adding a credential, submit an overlay that declares the scheme."
                        ),
                        "instructions": (
                            f"Research how '{api_id}' authenticates requests, then submit an "
                            f"OpenAPI Overlay 1.0 document to POST /apis/{api_id}/overlays. "
                            f"Once submitted, retry POST /credentials."
                        ),
                        "submit_to": f"POST /apis/{api_id}/overlays",
                        "examples": {
                            "api_key_in_header": {
                                "overlay": "1.0.0",
                                "info": {"title": f"{api_id} auth", "version": "1.0.0"},
                                "actions": [
                                    {
                                        "target": "$",
                                        "update": {
                                            "components": {
                                                "securitySchemes": {
                                                    "ApiKeyAuth": {
                                                        "type": "apiKey",
                                                        "in": "header",
                                                        "name": "Your-Header-Name",
                                                    }
                                                }
                                            }
                                        },
                                    }
                                ],
                            },
                            "compound_api_key_header_plus_username": {
                                "overlay": "1.0.0",
                                "info": {"title": f"{api_id} auth", "version": "1.0.0"},
                                "actions": [
                                    {
                                        "target": "$",
                                        "update": {
                                            "components": {
                                                "securitySchemes": {
                                                    "Secret": {
                                                        "type": "apiKey",
                                                        "in": "header",
                                                        "name": "Your-Secret-Header",
                                                    },
                                                    "Identity": {
                                                        "type": "apiKey",
                                                        "in": "header",
                                                        "name": "Your-Username-Header",
                                                    },
                                                }
                                            }
                                        },
                                    }
                                ],
                                "_note": "Compound scheme: broker injects credential.value into 'Secret' header and credential.identity into 'Identity' header. Use when the API requires both a key and a username (e.g. Discourse: Api-Key + Api-Username).",
                            },
                            "api_key_in_query": {
                                "overlay": "1.0.0",
                                "info": {"title": f"{api_id} auth", "version": "1.0.0"},
                                "actions": [
                                    {
                                        "target": "$",
                                        "update": {
                                            "components": {
                                                "securitySchemes": {
                                                    "ApiKeyAuth": {
                                                        "type": "apiKey",
                                                        "in": "query",
                                                        "name": "api_key",
                                                    }
                                                }
                                            }
                                        },
                                    }
                                ],
                            },
                            "bearer_token": {
                                "overlay": "1.0.0",
                                "info": {"title": f"{api_id} auth", "version": "1.0.0"},
                                "actions": [
                                    {
                                        "target": "$",
                                        "update": {
                                            "components": {
                                                "securitySchemes": {
                                                    "BearerAuth": {
                                                        "type": "http",
                                                        "scheme": "bearer",
                                                    }
                                                }
                                            }
                                        },
                                    }
                                ],
                            },
                            "basic_auth": {
                                "overlay": "1.0.0",
                                "info": {"title": f"{api_id} auth", "version": "1.0.0"},
                                "actions": [
                                    {
                                        "target": "$",
                                        "update": {
                                            "components": {
                                                "securitySchemes": {
                                                    "BasicAuth": {"type": "http", "scheme": "basic"}
                                                }
                                            }
                                        },
                                    }
                                ],
                                "_note": "Basic auth: broker injects Authorization: Basic base64(identity:value). Set 'identity' on the credential to the username.",
                            },
                        },
                        "note": (
                            "Set auth_type on the credential to 'bearer', 'basic', or 'apiKey'. "
                            "For compound apiKey schemes (key + username headers): name the two "
                            "securitySchemes 'Secret' (receives credential.value) and 'Identity' "
                            "(receives credential.identity) in your overlay — the broker handles injection automatically."
                        ),
                    },
                )

    try:
        cred = await vault.create_credential(
            body.label,
            body.value,
            api_id=api_id,
            scheme_name=scheme_name,
            identity=getattr(body, "identity", None),
            server_variables=getattr(body, "server_variables", None),
            scheme=getattr(body, "scheme", None),
            routes=getattr(body, "routes", None),
            description=getattr(body, "description", None),
        )
    except Exception:
        log.exception("Failed to create credential")
        raise HTTPException(400, "Failed to create credential.")

    actor = "human" if request.state.is_human_session else f"toolkit={request.state.toolkit_id}"
    await persist_audit(
        event="CREDENTIAL_CREATED",
        actor_kind="human" if request.state.is_human_session else "toolkit",
        actor_id=None if request.state.is_human_session else request.state.toolkit_id,
        ip=client_ip(request),
        target_kind="credential",
        target_id=cred["id"],
        payload={"label": cred["label"], "api_id": api_id, "actor": actor},
    )
    return cred


@router.post(
    "/{cid:path}/test",
    summary="Test a credential by issuing a low-impact upstream probe",
)
async def test_credential(
    cid: Annotated[str, Path(description="Credential ID to test")],
    request: Request,
):
    """Verify a credential by issuing a single 5-second probe to the upstream API.

    The probe URL is chosen, in priority order:
    1. `x-jentic-healthcheck` declared in the API's OpenAPI spec
    2. The first `GET` operation in the spec with no required parameters
    3. The root URL of the API's first declared server
    4. The credential's first declared route host

    Response shape: `{ ok: bool, status: int | null, hint: string | null, probe_url: string | null }`.
    A 2xx upstream response is `ok=true`. 401/403 returns `ok=false` with a hint that the
    credential is rejected. 404/405 on a probe path is treated as `ok=true` since the
    upstream **did respond** — we only care that the credential is plausibly valid.

    No body is sent.

    **Auth:** Requires a human session, or an agent key with an explicit
    `POST /credentials` allow rule on the jentic-mini credential. This probe
    decrypts and uses the stored secret to make an authenticated outbound call,
    so it is gated like the credential write endpoints — an agent cannot use it
    as an oracle against credentials it has no permission for.
    """
    if not request.state.is_human_session:
        if not await _agent_has_credential_write_permission(
            request.state.toolkit_id, "POST", "/credentials"
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Testing a credential requires a human session, or an agent key with an "
                    "explicit POST /credentials allow rule on the jentic-mini credential."
                ),
            )
    cred = await vault.get_credential(cid)
    if not cred:
        raise HTTPException(404, "Credential not found")

    api_id = cred.get("api_id")
    routes = cred.get("routes") or []
    fallback_host = None
    if api_id:
        fallback_host = api_id
    elif routes:
        fallback_host = routes[0].split("/", 1)[0]

    spec = await _load_api_spec(api_id) if api_id else None
    probe_url = _pick_probe_url(spec, fallback_host)
    if not probe_url:
        return {
            "ok": False,
            "status": None,
            "hint": "no_probe_url",
            "probe_url": None,
            "message": "Could not determine a probe URL from the spec or credential routes.",
        }

    if cred.get("auth_type") == "pipedream_oauth":
        # Pipedream credentials don't carry a direct token we can inject — the broker
        # proxies through Pipedream. Surface this as a non-fatal diagnostic so the UI
        # can show an informative tooltip rather than a fake red. (Checked before the
        # SSRF guard since we never make an outbound call for these.)
        return {
            "ok": False,
            "status": None,
            "hint": "pipedream_unsupported",
            "probe_url": probe_url,
            "message": (
                "Pipedream OAuth credentials cannot be probed directly — the upstream call "
                "is mediated by Pipedream. Use the broker (e.g. a small workflow run) to validate."
            ),
        }

    # SSRF guard: the probe URL is derived from user-controlled api_id / routes /
    # imported spec, and we attach the decrypted secret to the request. Refuse to
    # probe private, loopback, link-local (incl. 169.254.169.254 cloud metadata),
    # or otherwise-reserved hosts. To close the DNS-rebinding (TOCTOU) gap we
    # resolve the host exactly once here and pin the outbound connection to a
    # validated IP, so httpx cannot re-resolve to a private address mid-flight.
    from src.routers.apis import safe_resolve_public_ips  # noqa: PLC0415

    parsed_probe = urlparse(probe_url)
    probe_host = parsed_probe.hostname or ""
    safe_ips = safe_resolve_public_ips(probe_host)
    if not safe_ips:
        return {
            "ok": False,
            "status": None,
            "hint": "blocked_host",
            "probe_url": probe_url,
            "message": (
                "Refusing to probe a private, loopback, or non-resolvable host. "
                "Credential tests only run against public upstream APIs."
            ),
        }

    headers, _cred_with_value = await vault.build_inject_headers_for_credential(cid)

    # Pin DNS: rewrite the URL to connect to the validated IP, but keep the
    # original Host header + TLS SNI so the upstream still routes/validates
    # correctly. This eliminates the validate-then-reconnect window entirely.
    pinned_ip = safe_ips[0]
    default_port = 443 if parsed_probe.scheme == "https" else 80
    probe_port = parsed_probe.port or default_port
    pinned_netloc = (
        f"[{pinned_ip}]:{probe_port}" if ":" in pinned_ip else f"{pinned_ip}:{probe_port}"
    )
    pinned_url = parsed_probe._replace(netloc=pinned_netloc).geturl()
    headers = {**headers, "Host": parsed_probe.netloc}

    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,
            verify=True,
        ) as client:
            resp = await client.get(
                pinned_url,
                headers=headers,
                extensions={"sni_hostname": probe_host},
            )
        status = resp.status_code
    except httpx.TimeoutException:
        return {
            "ok": False,
            "status": None,
            "hint": "timeout",
            "probe_url": probe_url,
            "message": "Probe timed out after 5 seconds.",
        }
    except httpx.HTTPError as exc:
        # Log the detail server-side for debugging, but don't echo the exception
        # text to the caller — httpx error messages can embed the resolved host,
        # IP, or other connection internals (CodeQL: information exposure through
        # an exception). The agent only needs to know the probe failed.
        log.info("Credential test probe failed for %s: %s", cid, exc)
        return {
            "ok": False,
            "status": None,
            "hint": "network_error",
            "probe_url": probe_url,
            "message": "Could not reach the upstream host.",
        }

    # 401/403 = the credential was rejected by the upstream; this is an authoritative
    # "broken" signal. 404/405 means the probe path doesn't exist but the host responded —
    # we count that as "ok" for the UI because the credential itself wasn't rejected.
    ok = (status < 400) or (status in (404, 405))
    hint: str | None = None
    if status in (401, 403):
        hint = "unauthorized"
    elif status == 429:
        hint = "rate_limited"
    elif status >= 500:
        hint = "upstream_error"

    # Persist the verdict so the StatusDot reflects the explicit test, mirroring
    # what the broker writes on live traffic. We only persist a *positive* verdict
    # on a genuine success (status < 400) — a 404/405 means the probe path didn't
    # exist, which is no evidence the credential works, so we must NOT flip a
    # previously-broken credential green. Only 401/403 is an authoritative
    # "rejected" signal. Ambiguous statuses (404/405/429/5xx) leave `healthy`
    # untouched. This matches the broker's <400-only positive-health rule.
    if status < 400:
        await vault.mark_credential_used(cid)
        await vault.mark_credential_health(cid, healthy=True)
    elif status in (401, 403):
        await vault.mark_credential_health(cid, healthy=False)

    return {
        "ok": ok,
        "status": status,
        "hint": hint,
        "probe_url": probe_url,
    }


@router.get(
    "/{cid:path}/bindings",
    summary="List toolkits this credential is bound to",
    dependencies=[Depends(require_human_session)],
)
async def list_credential_bindings(
    cid: Annotated[str, Path(description="Credential ID")],
):
    """Return `[{toolkit_id, toolkit_name, alias}]` for every toolkit this credential is bound to.

    Powers the per-row "Used by N toolkits" chip cluster in the credentials list and the
    cascade-impact preview in `ConfirmDeleteDialog`'s credential variant. A single indexed
    JOIN — avoids the N+1 fan-out the UI would otherwise have to do.
    """
    if not await vault.get_credential(cid):
        raise HTTPException(404, "Credential not found")
    async with get_db() as db:
        async with db.execute(
            """SELECT t.id, t.name, tc.alias
               FROM toolkit_credentials tc
               JOIN toolkits t ON t.id = tc.toolkit_id
               WHERE tc.credential_id = ?
               ORDER BY t.name""",
            (cid,),
        ) as cur:
            rows = await cur.fetchall()
    return [{"toolkit_id": r[0], "toolkit_name": r[1], "alias": r[2]} for r in rows]


@router.get(
    "/{cid:path}", response_model=CredentialOut, summary="Get an upstream API credential by ID"
)
async def get_credential(cid: str):
    """Retrieve metadata for a single credential. Value is never returned."""
    cred = await vault.get_credential(cid)
    if not cred:
        raise HTTPException(404, "Credential not found")
    return cred


@router.patch(
    "/{cid:path}",
    response_model=CredentialOut,
    summary="Update an upstream API credential — rotate a secret or fix its API binding",
    openapi_extra={
        "requestBody": {
            "description": "Fields to update: label, value (for rotation), identity, api_id, or auth_type — only provided fields are changed"
        }
    },
)
async def patch(
    cid: Annotated[str, Path(description="Credential ID to update")],
    body: CredentialPatch,
    request: Request,
):
    """
    Update a credential's label, secret value, identity field, API binding, or auth_type.

    Common use cases:
    - Rotate an expired token or password (update `value`)
    - Fix incorrect API binding (update `api_id`)
    - Add username to existing credential (update `identity`)
    - Relabel for clarity (update `label`)

    Only changed fields need to be included in the request body. Omitted fields are left unchanged.

    **Auth:** Requires human session OR agent key with explicit `PATCH /credentials` allow rule on jentic-mini credential.
    """
    if not request.state.is_human_session:
        if not await _agent_has_credential_write_permission(
            request.state.toolkit_id, "PATCH", f"/credentials/{cid}"
        ):
            raise HTTPException(
                status_code=403,
                detail="Updating credentials requires a human session, or an agent key with an explicit PATCH /credentials allow rule on the jentic-mini credential.",
            )
    if body.auth_type in _RESERVED_AUTH_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"auth_type '{body.auth_type}' is reserved for internal integrations and "
                "cannot be set manually."
            ),
        )
    row = await vault.patch_credential(
        cid,
        body.label,
        body.value,
        body.api_id,
        body.auth_type,
        identity=getattr(body, "identity", None),
        server_variables=getattr(body, "server_variables", None),
        scheme=getattr(body, "scheme", None),
        routes=getattr(body, "routes", None),
        description=getattr(body, "description", None),
    )
    if not row:
        raise HTTPException(404, "Credential not found")
    actor = "human" if request.state.is_human_session else f"toolkit={request.state.toolkit_id}"
    await persist_audit(
        event="CREDENTIAL_UPDATED",
        actor_kind="human" if request.state.is_human_session else "toolkit",
        actor_id=None if request.state.is_human_session else request.state.toolkit_id,
        ip=client_ip(request),
        target_kind="credential",
        target_id=cid,
        payload={"actor": actor},
    )
    return row


@router.delete("/{cid:path}", status_code=204, summary="Delete an upstream API credential")
async def delete(
    cid: Annotated[str, Path(description="Credential ID to delete")], request: Request
):
    """
    Permanently delete a credential.

    The credential is removed from the vault and unbound from all toolkits that reference it.
    Agents using toolkits with this credential will immediately lose access to the upstream API.

    For credentials backed by Pipedream OAuth (`auth_type == 'pipedream_oauth'`), the
    upstream Pipedream grant is also revoked so the connection cannot be re-used out-of-band.
    Failures on the upstream revoke are logged but do not block local deletion — local
    cleanup is the source of truth.

    **Auth:** Requires human session OR agent key with explicit `DELETE /credentials` allow rule on jentic-mini credential.

    **Warning:** This operation cannot be undone. The secret value is irrecoverably destroyed.
    """
    if not request.state.is_human_session:
        if not await _agent_has_credential_write_permission(
            request.state.toolkit_id, "DELETE", f"/credentials/{cid}"
        ):
            raise HTTPException(
                status_code=403,
                detail="Deleting credentials requires a human session, or an agent key with an explicit DELETE /credentials allow rule on the jentic-mini credential.",
            )
    cred = await vault.get_credential(cid)
    if not cred:
        raise HTTPException(404, "Credential not found")

    pipedream_revoked: bool | None = None
    pipedream_account: tuple[str, str] | None = None
    if cred.get("auth_type") == "pipedream_oauth":
        # Map credential row → broker/account so we can revoke upstream *after*
        # the local delete commits. The link is materialized in
        # oauth_broker_accounts; we keep this query tight rather than parsing the
        # cred id format.
        async with get_db() as db:
            async with db.execute(
                "SELECT broker_id, account_id FROM oauth_broker_accounts "
                "WHERE broker_id || '-' || account_id || '-' || replace(api_host, '.', '-') = ? "
                "LIMIT 1",
                (cid,),
            ) as cur:
                row = await cur.fetchone()
        if row:
            pipedream_account = (row[0], row[1])

    # Atomic local delete (credential + routes + oauth_broker_accounts link) in a
    # single transaction. Local state is the source of truth, so the best-effort
    # upstream revoke runs only *after* this commit succeeds.
    if not await vault.delete_credential_cascade(cid):
        raise HTTPException(404, "Credential not found")

    if pipedream_account is not None:
        from src.routers.oauth_brokers import (  # noqa: PLC0415  (lazy import avoids circular dependency)
            revoke_pipedream_account_upstream,
        )

        pipedream_revoked = await revoke_pipedream_account_upstream(*pipedream_account)
    actor = "human" if request.state.is_human_session else f"toolkit={request.state.toolkit_id}"
    await persist_audit(
        event="CREDENTIAL_DELETED",
        actor_kind="human" if request.state.is_human_session else "toolkit",
        actor_id=None if request.state.is_human_session else request.state.toolkit_id,
        ip=client_ip(request),
        target_kind="credential",
        target_id=cid,
        payload={
            "actor": actor,
            "auth_type": cred.get("auth_type"),
            "pipedream_revoked": pipedream_revoked,
        },
    )


@router.get(
    "",
    summary="List upstream API credentials — labels and API bindings only, no secret values",
    response_model=list[CredentialOut],
)
async def list_credentials(
    request: Request,
    api_id: Annotated[
        str | None, Query(description="Filter credentials by API ID (hostname)")
    ] = None,
):
    """List stored upstream API credentials. Values are never returned.

    All authenticated callers (agent keys and human sessions) can see all credential
    labels and IDs — this is intentional. Labels are not secrets, and agents need
    to discover credential IDs in order to file targeted `grant` access requests
    (e.g. "bind Work Gmail" vs "bind Personal Gmail").

    Use `GET /credentials/{id}` to retrieve a specific credential by ID.
    Filter with `?api_id=api.github.com` to list all credentials for a given API.
    """

    conditions = []
    params: list = []

    if api_id:
        conditions.append("c.api_id = ?")
        params.append(api_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with get_db() as db:
        async with db.execute(
            f"SELECT c.id, c.label, c.api_id, c.auth_type, c.created_at, c.updated_at, c.identity, "
            f"       c.server_variables, c.scheme, c.last_used_at, c.description, "
            f"       oba.account_id, oba.app_slug, oba.synced_at, oba.healthy, "
            f"       c.healthy, c.health_checked_at "
            f"FROM credentials c "
            f"LEFT JOIN oauth_broker_accounts oba ON oba.broker_id || '-' || oba.account_id || '-' || replace(oba.api_host, '.', '-') = c.id "
            f"{where} ORDER BY c.created_at DESC",
            params,
        ) as cur:
            rows = await cur.fetchall()

        # Fetch routes for all credentials in one query
        cred_ids = [r[0] for r in rows]
        routes_map: dict[str, list[str]] = {}
        if cred_ids:
            placeholders = ",".join("?" * len(cred_ids))
            async with db.execute(
                f"SELECT credential_id, host, path_prefix FROM credential_routes "
                f"WHERE credential_id IN ({placeholders}) ORDER BY length(path_prefix) DESC",
                cred_ids,
            ) as cur:
                for cid, host, pp in await cur.fetchall():
                    route_str = host + pp if pp != "/" else host
                    routes_map.setdefault(cid, []).append(route_str)

    return [
        {
            "id": r[0],
            "label": r[1],
            "api_id": r[2],
            "auth_type": r[3],
            "created_at": r[4],
            "updated_at": r[5],
            "identity": r[6] if len(r) > 6 else None,
            "server_variables": json.loads(r[7]) if len(r) > 7 and r[7] else None,
            "scheme": json.loads(r[8]) if len(r) > 8 and r[8] else None,
            "last_used_at": r[9] if len(r) > 9 else None,
            "description": r[10] if len(r) > 10 else None,
            "routes": routes_map.get(r[0]),
            "account_id": r[11] if len(r) > 11 else None,
            "app_slug": r[12] if len(r) > 12 else None,
            "synced_at": r[13] if len(r) > 13 else None,
            # Health precedence: a Pipedream-managed credential carries its health
            # on the joined oauth_broker_accounts row (oba.healthy, r[14]); that
            # wins. A manual credential has no oba row, so we fall back to its own
            # credentials.healthy (r[15]), written by the broker / Test connection.
            # Both are SQLite INTEGER (0/1) or NULL — coerce to bool/None so the
            # JSON shape matches the Pydantic model. NULL on both = "unknown".
            "healthy": (
                bool(r[14])
                if len(r) > 14 and r[14] is not None
                else (bool(r[15]) if len(r) > 15 and r[15] is not None else None)
            ),
            "health_checked_at": r[16] if len(r) > 16 else None,
        }
        for r in rows
    ]


# ── Credential health: bindings + test connection ─────────────────────────────


async def _load_api_spec(api_id: str) -> dict | None:
    """Best-effort load of the OpenAPI spec for an API (yaml or json on disk)."""
    async with get_db() as db:
        async with db.execute("SELECT spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        with open(row[0]) as f:
            raw = f.read()
        if row[0].endswith((".yaml", ".yml")):
            return yaml.safe_load(raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return yaml.safe_load(raw)
    except Exception:
        return None


def _spec_servers(spec: dict) -> list[str]:
    """Extract concrete server URLs from a spec, dropping any with `{var}` templates we can't resolve."""
    servers = spec.get("servers") or []
    out: list[str] = []
    for s in servers:
        url = (s or {}).get("url")
        if not url or "{" in url:
            continue
        out.append(url.rstrip("/"))
    return out


def _pick_probe_url(spec: dict | None, fallback_host: str | None) -> str | None:
    """Choose a probe URL for `POST /credentials/{id}/test`.

    Priority (per Phase 0 spec):
      1. `x-jentic-healthcheck` (top-level, on a path, or on an operation) — explicit operator opt-in
      2. First `GET` operation with no required parameters
      3. First server URL (root)
      4. Fallback host root
    """
    if spec:
        # 1a. Top-level x-jentic-healthcheck
        hc = spec.get("x-jentic-healthcheck")
        if isinstance(hc, str) and hc.startswith("http"):
            return hc
        if isinstance(hc, dict) and isinstance(hc.get("url"), str):
            return hc["url"]

        servers = _spec_servers(spec)
        base = servers[0] if servers else None

        paths = spec.get("paths") or {}

        # 1b. Path- or operation-level x-jentic-healthcheck
        for path, item in paths.items():
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("x-jentic-healthcheck"), (str, bool)) and base:
                return f"{base}{path}"
            for verb in ("get", "head"):
                op = item.get(verb)
                if isinstance(op, dict) and op.get("x-jentic-healthcheck") and base:
                    return f"{base}{path}"

        # 2. First GET operation with no required parameters.
        if base:
            for path, item in paths.items():
                if not isinstance(item, dict):
                    continue
                op = item.get("get")
                if not isinstance(op, dict):
                    continue
                params = op.get("parameters") or []
                # Path templating means we can't safely call `/users/{id}` — skip those.
                if "{" in path:
                    continue
                required = [p for p in params if isinstance(p, dict) and p.get("required")]
                if not required:
                    return f"{base}{path}"

        # 3. Server root.
        if base:
            return base

    if fallback_host:
        return f"https://{fallback_host}/"
    return None


# ── Audit log query (top-level for cross-resource use) ────────────────────────


audit_router = APIRouter(
    prefix="/audit",
    tags=["credentials"],
    dependencies=[Depends(require_human_session)],
)


@audit_router.get("", summary="Query the persistent audit log")
async def query_audit_events(
    target_kind: Annotated[
        str | None, Query(description="Filter by target kind (e.g. 'credential', 'toolkit')")
    ] = None,
    target_id: Annotated[str | None, Query(description="Filter by target ID")] = None,
    credential_id: Annotated[
        str | None,
        Query(description="Convenience: equivalent to target_kind=credential&target_id=<this>"),
    ] = None,
    event: Annotated[str | None, Query(description="Filter by event name")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Max rows to return")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> list[dict[str, Any]]:
    """Return audit rows newest-first. Drives the credential history panel in the UI."""
    if credential_id and not (target_kind or target_id):
        target_kind = "credential"
        target_id = credential_id
    return await query_audit(
        target_kind=target_kind,
        target_id=target_id,
        event=event,
        limit=limit,
        offset=offset,
    )
