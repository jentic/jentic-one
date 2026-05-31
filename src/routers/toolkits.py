"""Toolkits router — scoped bundles of upstream API credentials with client API keys and access policies.

A toolkit is the central agent identity in Jentic:
- Has its own client API key(s) issued to the agent (scope: execute by default)
- Bundles upstream API credentials from the vault (injected by the broker on outbound calls)
- Has an access control policy (allow/deny rules by API/method/path)

This models the Jentic v2 design spec's toolkits concept exactly.
"""

import json
import logging
import re
import secrets
import time
import uuid
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from src.auth import client_ip, default_allowed_ips, require_human_session
from src.db import DEFAULT_TOOLKIT_ID, get_db
from src.models import (
    CredentialBindingOut,
    PermissionRule,
    PermissionRuleOut,
    PermissionsPatch,
    ToolkitKeyCreated,
    ToolkitKeyOut,
    ToolkitOut,
)
from src.validators import NormModel


audit_log = logging.getLogger("jentic.audit")
router = APIRouter(prefix="/toolkits")
policy_router = (
    APIRouter()
)  # mounted separately with tags=["permissions"], prefix="/toolkits" added at include time


# ── Models ────────────────────────────────────────────────────────────────────


class ToolkitCreate(NormModel):
    """Create a new toolkit with scoped credentials and access control. Optionally generates first API key."""

    name: str = Field(description="Toolkit name for identification")
    description: str | None = Field(
        default=None, description="Optional description of this toolkit's purpose"
    )
    simulate: bool = Field(
        default=False, description="If true, toolkit operates in dry-run mode (no real API calls)"
    )
    initial_key_label: str | None = Field(
        None, description="Label for the first key created with this toolkit (e.g. 'Agent A')"
    )
    initial_key_allowed_ips: list[str] | None = Field(
        None, description="IP allowlist for the first key. NULL = unrestricted."
    )


class ToolkitPatch(NormModel):
    """Update toolkit metadata or toggle disabled/simulate flags. Only provided fields are changed."""

    name: str | None = Field(default=None, description="New toolkit name (optional)")
    description: str | None = Field(default=None, description="New description (optional)")
    simulate: bool | None = Field(default=None, description="Toggle dry-run mode (optional)")
    disabled: bool | None = Field(
        default=None, description="Toggle toolkit disabled state (optional)"
    )


class KeyCreate(NormModel):
    """Create a new API key for this toolkit. The full key value is only returned once at creation time."""

    label: str | None = Field(
        None, description="Human-readable label, e.g. 'Agent A', 'Staging bot'"
    )
    allowed_ips: list[str] | None = Field(
        None, description="IP allowlist for this key only. NULL = unrestricted."
    )


class KeyOut(BaseModel):
    id: str
    label: str | None
    allowed_ips: list[str] | None
    created_at: float
    revoked_at: float | None = None
    # api_key only returned on create (shown once, never again)


class ToolkitCredentialAdd(NormModel):
    """Bind an existing credential to this toolkit. Agent rules apply; system safety rules are always active."""

    credential_id: str = Field(
        description="Credential ID to bind to this toolkit (format: cred_{12chars})"
    )


# Keep PolicyRule as an alias for backward compat within this file
PolicyRule = PermissionRule


# System safety rules — always appended after agent rules.
# Visible to agents via GET /toolkits/{id}/credentials/{cred_id}/permissions.
SYSTEM_SAFETY_RULES: list[dict] = [
    {
        "effect": "deny",
        "path": r"admin|pay|billing|webhook|secret|token",
        "_system": True,
        "_comment": "Deny requests to sensitive path segments",
    },
    {
        "effect": "deny",
        "methods": ["POST", "PUT", "PATCH", "DELETE"],
        "_system": True,
        "_comment": "Deny write methods by default — add an explicit allow rule above to unlock specific writes",
    },
    {
        "effect": "allow",
        "_system": True,
        "_comment": "Allow everything else (reads pass through by default)",
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Convert a display name to a URL-safe hyphen slug.
    'My ElevenLabs Toolkit' → 'my-elevenlabs-toolkit'
    Lowercased, non-alphanumeric runs replaced with hyphens, leading/trailing stripped.
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "toolkit"


def _gen_toolkit_key() -> str:
    """Generate a toolkit API key (col_ prefix)."""
    return "tk_" + secrets.token_urlsafe(24)


def _toolkit_links(toolkit_id: str) -> dict:
    return {
        "self": f"/toolkits/{toolkit_id}",
        "keys": f"/toolkits/{toolkit_id}/keys",
        "credentials": f"/toolkits/{toolkit_id}/credentials",
        "search": f"/search?toolkit_id={toolkit_id}",
    }


def _generate_policy_summary(rules: list[dict]) -> str:
    """Human-readable one-liner for a set of agent rules."""
    if not rules:
        return "Read-only (system safety rules apply; add allow rules to unlock writes)."
    allow_rules = [r for r in rules if r.get("effect") == "allow"]
    deny_rules = [r for r in rules if r.get("effect") == "deny"]
    parts = []
    if allow_rules:
        parts.append(f"{len(allow_rules)} allow rule(s)")
    if deny_rules:
        parts.append(f"{len(deny_rules)} deny rule(s)")
    return f"Agent rules: {', '.join(parts)}. System safety rules apply below."


def check_policy(
    agent_rules: list[dict],
    operation_id: str | None,
    method: str | None = None,
    path: str | None = None,
) -> tuple[bool, str]:
    """
    Evaluate access for a request against a credential's rules.
    Order: agent rules → system safety rules → explicit allow-all.
    Returns (allowed: bool, reason: str).
    """
    all_rules = list(agent_rules) + SYSTEM_SAFETY_RULES

    for rule in all_rules:
        effect = rule.get("effect", "allow")
        methods = rule.get("methods")
        path_regex = rule.get("path")
        operation_regexes = rule.get("operations")

        matched = True

        # Method match
        if matched and methods:
            if method and method.upper() not in [m.upper() for m in methods]:
                matched = False

        # Path regex match (re.search — substring by default)
        if matched and path_regex:
            if path:
                try:
                    if not re.search(path_regex, path, re.IGNORECASE):
                        matched = False
                except re.error:
                    matched = False
            else:
                matched = False

        # Operations regex match — any regex in the list matches
        if matched and operation_regexes:
            if operation_id:
                try:
                    op_match = any(
                        re.search(pat, operation_id, re.IGNORECASE) for pat in operation_regexes
                    )
                    if not op_match:
                        matched = False
                except re.error:
                    matched = False
            else:
                matched = False

        if matched:
            comment = rule.get("_comment", "")
            return (
                effect == "allow"
            ), f"Matched rule ({effect}){': ' + comment if comment else ''}"

    return True, "Default action: allow"


# ── Routes ────────────────────────────────────────────────────────────────────


def _strip_key(d: dict) -> dict:
    """Remove api_key from a toolkit dict. Keys are write-once (shown only on creation)."""
    return {k: v for k, v in d.items() if k != "api_key"}


@router.post(
    "",
    status_code=201,
    summary="Create a toolkit — scoped bundle of upstream API credentials with a client API key",
    response_model=ToolkitOut,
    openapi_extra={
        "requestBody": {
            "description": "Toolkit details: name, optional description, simulate flag for dry-run mode, and optional first API key configuration"
        }
    },
)
async def create_toolkit(body: ToolkitCreate):
    """Creates a toolkit: a named bundle of upstream API credentials with a scoped client API key for the agent.
    Returns a toolkit API key (tk_xxx) — shown once, not recoverable.
    Bind credentials via POST /toolkits/{id}/credentials.
    Set access policy via PUT /toolkits/{id}/credentials/{cred_id}/permissions.
    Agents use toolkit keys to call the broker; only bound credentials are injected.
    """
    coll_id = _slugify(body.name)
    api_key = _gen_toolkit_key()
    key_id = "ck_" + str(uuid.uuid4())[:8]
    key_label = body.initial_key_label or "Default key"
    allowed_ips_json = (
        json.dumps(body.initial_key_allowed_ips)
        if body.initial_key_allowed_ips is not None
        else json.dumps(default_allowed_ips())
        if default_allowed_ips()
        else None
    )
    now = time.time()

    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (coll_id,)) as cur:
            if await cur.fetchone():
                raise HTTPException(
                    409, f"A toolkit with slug '{coll_id}' already exists. Choose a different name."
                )
        await db.execute(
            """INSERT INTO toolkits (id, name, description, api_key, simulate, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (coll_id, body.name, body.description, api_key, int(body.simulate), now, now),
        )
        # Store the key in toolkit_keys (authoritative for auth)
        await db.execute(
            """INSERT INTO toolkit_keys (id, toolkit_id, api_key, label, allowed_ips, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key_id, coll_id, api_key, key_label, allowed_ips_json, now),
        )
        # Create default read-only policy (no agent rules; system safety rules apply)
        policy_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO toolkit_policies (id, toolkit_id, default_action, rules, summary)
               VALUES (?, ?, 'allow', '[]', 'Read-only (system safety rules apply; add allow rules to unlock writes).')""",
            (policy_id, coll_id),
        )
        await db.commit()

    return {
        "id": coll_id,
        "name": body.name,
        "description": body.description,
        "simulate": body.simulate,
        "created_at": now,
        "keys": [
            {
                "id": key_id,
                "label": key_label,
                "api_key": api_key,  # shown ONLY here, never again
                "allowed_ips": body.initial_key_allowed_ips,
                "created_at": now,
            }
        ],
        "_notice": "Store api_key securely — it will not be shown again. Add more keys via POST /toolkits/{id}/keys.",
        "_links": {**_toolkit_links(coll_id), "keys": f"/toolkits/{coll_id}/keys"},
    }


@router.get("", summary="List toolkits", response_model=list[ToolkitOut])
async def list_toolkits(request: Request):
    """List all toolkits with metadata summary.

    Returns all toolkits visible to the caller with key counts and bound credential counts.
    Admin users see all toolkits. Agents see only their own toolkit.

    Each toolkit includes:
    - Metadata (name, description, disabled state, simulation mode)
    - Active key count (revoked keys excluded)
    - Bound credential count (upstream API credentials available to this toolkit)

    The default toolkit implicitly has access to all credentials without explicit binding.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT
                t.id, t.name, t.description, t.simulate, t.disabled, t.created_at,
                COALESCE(tk.key_count, 0) AS key_count,
                COALESCE(tc.bound_credential_count, 0) AS bound_credential_count
            FROM toolkits t
            LEFT JOIN (
                SELECT toolkit_id, COUNT(*) AS key_count
                FROM toolkit_keys
                WHERE revoked_at IS NULL
                GROUP BY toolkit_id
            ) AS tk ON t.id = tk.toolkit_id
            LEFT JOIN (
                SELECT toolkit_id, COUNT(*) AS bound_credential_count
                FROM toolkit_credentials
                GROUP BY toolkit_id
            ) AS tc ON t.id = tc.toolkit_id
            """
        ) as cur:
            rows = await cur.fetchall()

        # The default toolkit implicitly sees ALL credentials, not just
        # those explicitly bound via toolkit_credentials.
        async with db.execute("SELECT COUNT(*) FROM credentials") as cur:
            total_cred_count = (await cur.fetchone())[0]

    return [
        {
            "id": r[0],
            "name": r[1],
            "description": r[2],
            "simulate": bool(r[3]),
            "disabled": bool(r[4]),
            "created_at": r[5],
            "key_count": r[6],
            "credential_count": total_cred_count if r[0] == DEFAULT_TOOLKIT_ID else r[7],
            "_links": {**_toolkit_links(r[0]), "keys": f"/toolkits/{r[0]}/keys"},
        }
        for r in rows
    ]


def _toolkit_to_markdown(data: dict) -> str:
    """Render toolkit detail as a human-readable Markdown document."""
    lines = [
        f"# Toolkit: {data.get('name') or data['id']}",
        "",
    ]
    if data.get("description"):
        lines += [data["description"], ""]
    lines += [
        f"**ID:** `{data['id']}`  ",
        f"**Simulate mode:** {'yes' if data.get('simulate') else 'no'}  ",
        "",
    ]

    credentials = data.get("credentials", [])
    if credentials:
        lines += ["## Bound Credentials", ""]
        for cred in credentials:
            lines.append(f"### `{cred['credential_id']}`")
            if cred.get("label"):
                lines.append(f"- **Label:** {cred['label']}")
            if cred.get("api_id"):
                lines.append(f"- **API:** `{cred['api_id']}`")
            user_rules = [r for r in cred.get("permissions", []) if not r.get("_system")]
            if user_rules:
                lines.append("- **Custom permissions:**")
                for rule in user_rules:
                    effect = rule.get("effect", "allow").upper()
                    methods = ", ".join(rule.get("methods", ["*"]))
                    path = rule.get("path", "*")
                    lines.append(f"  - `{effect}` `{methods}` `{path}`")
            lines.append("")
    else:
        lines += ["## Bound Credentials", "", "_No credentials bound._", ""]

    bound_apis = data.get("bound_apis", [])
    if bound_apis:
        lines += ["## Accessible APIs", ""]
        for api in bound_apis:
            lines.append(f"- `{api}`")
        lines.append("")

    return "\n".join(lines)


_TOOLKIT_CONTENT_TYPES = {
    "application/json": {"schema": {"type": "object"}},
    "application/yaml": {"schema": {"type": "string", "description": "Toolkit detail as YAML"}},
    "text/markdown": {"schema": {"type": "string", "description": "LLM-friendly toolkit summary"}},
}


@router.get(
    "/{toolkit_id}",
    summary="Get toolkit — metadata, bound upstream API credentials, client API keys, and policy summary",
    responses={
        200: {
            "description": "Toolkit detail — format controlled by Accept header.",
            "content": _TOOLKIT_CONTENT_TYPES,
        }
    },
)
async def get_toolkit(
    toolkit_id: Annotated[
        str, Path(description="Toolkit ID (e.g. 'default' or custom toolkit identifier)")
    ],
    request: Request,
):
    """Get toolkit with all inline context: metadata, bound upstream API credentials, client API key count, and policy summary.
    The default toolkit implicitly contains ALL upstream API credentials — no explicit binding needed.
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id, name, description, simulate, disabled, created_at FROM toolkits WHERE id=?",
            (toolkit_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")

    async with get_db() as db:
        if toolkit_id == DEFAULT_TOOLKIT_ID:
            # Default toolkit sees all credentials implicitly
            async with db.execute(
                "SELECT id, id, label, api_id, created_at FROM credentials ORDER BY created_at DESC",
            ) as cur:
                cred_rows = await cur.fetchall()
        else:
            async with db.execute(
                """SELECT cc.id, cc.credential_id, c.label, c.api_id, cc.created_at
                   FROM toolkit_credentials cc
                   JOIN credentials c ON cc.credential_id = c.id
                   WHERE cc.toolkit_id = ?""",
                (toolkit_id,),
            ) as cur:
                cred_rows = await cur.fetchall()

        # Load per-credential permissions
        cred_ids = [r[1] for r in cred_rows]
        cred_policies: dict[str, list] = {}
        if cred_ids:
            placeholders = ",".join("?" * len(cred_ids))
            async with db.execute(
                f"SELECT credential_id, rules FROM credential_policies WHERE credential_id IN ({placeholders})",
                cred_ids,
            ) as cur:
                for pol_row in await cur.fetchall():
                    cred_policies[pol_row[0]] = json.loads(pol_row[1])

    bound_apis = sorted({r[3] for r in cred_rows if r[3]})
    credentials = [
        {
            "credential_id": r[1],
            "label": r[2],
            "api_id": r[3],
            "bound_at": r[4],
            "permissions": cred_policies.get(r[1], []) + SYSTEM_SAFETY_RULES,
            "_links": {
                "permissions": f"/toolkits/{toolkit_id}/credentials/{r[1]}/permissions",
            },
        }
        for r in cred_rows
    ]

    data = {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "simulate": bool(row[3]),
        "disabled": bool(row[4]),
        "created_at": row[5],
        "bound_apis": bound_apis,
        "credentials": credentials,
        "_links": _toolkit_links(toolkit_id),
    }

    accept = request.headers.get("accept", "application/json")
    if "application/yaml" in accept:
        return Response(
            content=yaml.dump(data, allow_unicode=True, sort_keys=False),
            media_type="application/yaml",
        )
    if "text/markdown" in accept:
        return Response(
            content=_toolkit_to_markdown(data),
            media_type="text/markdown; charset=utf-8",
        )
    return data


@router.patch(
    "/{toolkit_id}",
    summary="Update toolkit — rename or update description",
    response_model=ToolkitOut,
    openapi_extra={
        "requestBody": {
            "description": "Fields to update: name, description, simulate flag, or disabled flag — only provided fields are changed"
        }
    },
    dependencies=[Depends(require_human_session)],
)
async def patch_toolkit(
    toolkit_id: Annotated[str, Path(description="Toolkit ID to update")],
    body: ToolkitPatch,
    request: Request,
):
    """
    Update toolkit metadata — name, description, disabled state, or simulation mode.

    Only changed fields need to be included in the request body. Omitted fields are left unchanged.

    **Note:** The default toolkit's name and description cannot be modified (403 error).

    **Auth:** Requires human session (admin).
    """
    if toolkit_id == DEFAULT_TOOLKIT_ID and (
        body.name is not None or body.description is not None or body.simulate is not None
    ):
        raise HTTPException(
            403, "The default toolkit's name, description and simulate flag cannot be modified."
        )
    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (toolkit_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")
        updates = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.description is not None:
            updates["description"] = body.description
        if body.simulate is not None:
            updates["simulate"] = int(body.simulate)
        if body.disabled is not None:
            updates["disabled"] = int(body.disabled)
        if updates:
            updates["updated_at"] = time.time()
            set_clause = ", ".join(f"{k}=?" for k in updates)
            await db.execute(
                f"UPDATE toolkits SET {set_clause} WHERE id=?",
                list(updates.values()) + [toolkit_id],
            )
            await db.commit()
    return await get_toolkit(toolkit_id, request)


@router.delete(
    "/{toolkit_id}",
    status_code=204,
    summary="Delete toolkit and revoke all its client API keys",
    dependencies=[Depends(require_human_session)],
)
async def delete_toolkit(toolkit_id: Annotated[str, Path(description="Toolkit ID to delete")]):
    """
    Permanently delete a toolkit and revoke all its access keys.

    All agents using keys from this toolkit will immediately receive 401 errors.
    Credential bindings are removed, but the credentials themselves remain in the vault.

    **Note:** The default toolkit cannot be deleted (403 error).

    **Auth:** Requires human session (admin).

    **Warning:** This operation cannot be undone.
    """
    if toolkit_id == DEFAULT_TOOLKIT_ID:
        raise HTTPException(403, "The default toolkit cannot be deleted.")
    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (toolkit_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Toolkit not found")
        # Explicit cleanup (belt-and-suspenders alongside CASCADE)
        await db.execute("DELETE FROM toolkit_keys WHERE toolkit_id=?", (toolkit_id,))
        await db.execute("DELETE FROM toolkit_credentials WHERE toolkit_id=?", (toolkit_id,))
        await db.execute("DELETE FROM toolkit_policies WHERE toolkit_id=?", (toolkit_id,))
        await db.execute("DELETE FROM permission_requests WHERE toolkit_id=?", (toolkit_id,))
        await db.execute("DELETE FROM toolkits WHERE id=?", (toolkit_id,))
        await db.commit()


# ── Toolkit Keys ───────────────────────────────────────────────────────────
# One toolkit can have many access keys — one per agent/client.
# Each key can be individually revoked without affecting other agents.
# IP restrictions live at the key level, not the toolkit level.


@router.post(
    "/{toolkit_id}/keys",
    status_code=201,
    summary="Issue a new client API key for this toolkit",
    response_model=ToolkitKeyCreated,
    dependencies=[Depends(require_human_session)],
    openapi_extra={
        "requestBody": {
            "description": "Key configuration: optional label and optional IP allowlist (CIDR ranges)"
        }
    },
)
async def create_toolkit_key(
    toolkit_id: Annotated[str, Path(description="Toolkit ID to issue key for")], body: KeyCreate
):
    """Issues an additional client API key (tk_xxx) for this toolkit. Hand this key to the agent. Optionally restrict by IP (CIDR list). Returned once — not recoverable."""
    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (toolkit_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")

    api_key = _gen_toolkit_key()
    key_id = "ck_" + str(uuid.uuid4())[:8]
    allowed_ips_json = (
        json.dumps(body.allowed_ips)
        if body.allowed_ips is not None
        else json.dumps(default_allowed_ips())
        if default_allowed_ips()
        else None
    )
    now = time.time()

    async with get_db() as db:
        await db.execute(
            """INSERT INTO toolkit_keys (id, toolkit_id, api_key, label, allowed_ips, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key_id, toolkit_id, api_key, body.label, allowed_ips_json, now),
        )
        await db.commit()

    return {
        "id": key_id,
        "toolkit_id": toolkit_id,
        "label": body.label,
        "key": api_key,  # shown ONLY here, never again
        "allowed_ips": body.allowed_ips,
        "created_at": now,
        "_notice": "Store key securely — it will not be shown again.",
        "_links": {
            "toolkit": f"/toolkits/{toolkit_id}",
            "revoke": f"/toolkits/{toolkit_id}/keys/{key_id}",
        },
    }


@router.get(
    "/{toolkit_id}/keys",
    summary="List client API keys for this toolkit — metadata only, no secret values",
)
async def list_toolkit_keys(
    toolkit_id: Annotated[str, Path(description="Toolkit ID to list keys for")],
):
    """
    List all access keys for this toolkit.

    Active and revoked keys are shown (revoked keys have `revoked_at` set).
    The `api_key` value is never returned — only the key ID and metadata.
    """
    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (toolkit_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")
        async with db.execute(
            """SELECT id, label, allowed_ips, created_at, revoked_at
               FROM toolkit_keys WHERE toolkit_id=?
               ORDER BY created_at ASC""",
            (toolkit_id,),
        ) as cur:
            rows = await cur.fetchall()

    return {
        "toolkit_id": toolkit_id,
        "keys": [
            {
                "id": r[0],
                "label": r[1],
                "allowed_ips": json.loads(r[2]) if r[2] else None,
                "created_at": r[3],
                "revoked_at": r[4],
                "status": "revoked" if r[4] else "active",
                "_links": {
                    "revoke": f"/toolkits/{toolkit_id}/keys/{r[0]}",
                },
            }
            for r in rows
        ],
        "_links": {"toolkit": f"/toolkits/{toolkit_id}"},
    }


@router.patch(
    "/{toolkit_id}/keys/{key_id}",
    summary="Update a client API key — rename or change IP restrictions",
    response_model=ToolkitKeyOut,
    openapi_extra={
        "requestBody": {
            "description": "Fields to update: label or IP allowlist — only provided fields are changed"
        }
    },
)
async def patch_toolkit_key(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    key_id: Annotated[str, Path(description="Key ID to update")],
    body: KeyCreate,
):
    """Update label or IP restrictions on a client API key.

    Modifies the metadata for an existing toolkit key. The key value itself cannot be
    changed - to rotate a key, revoke the old one and create a new one.

    Parameters:
        toolkit_id: Toolkit ID containing the key
        key_id: Key ID to update (format: ck_xxxxxxxx)
        body: Update request with optional label and/or allowed_ips

    Updatable fields:
        - label: Human-readable name (e.g. "Production bot", "Staging agent")
        - allowed_ips: IP allowlist in CIDR notation (e.g. ["192.168.1.0/24"]).
          Set to null or empty array to allow all IPs.

    Returns:
        Updated key metadata including new label and IP restrictions.

    The key remains active with its original value. Changes take effect immediately.
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM toolkit_keys WHERE id=? AND toolkit_id=?", (key_id, toolkit_id)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Key '{key_id}' not found in toolkit '{toolkit_id}'")

        updates: dict = {}
        if body.label is not None:
            updates["label"] = body.label
        if body.allowed_ips is not None:
            updates["allowed_ips"] = json.dumps(body.allowed_ips) if body.allowed_ips else None

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            await db.execute(
                f"UPDATE toolkit_keys SET {set_clause} WHERE id=?",
                list(updates.values()) + [key_id],
            )
            await db.commit()

    # Return updated key metadata (no api_key)
    async with get_db() as db:
        async with db.execute(
            "SELECT id, label, allowed_ips, created_at, revoked_at FROM toolkit_keys WHERE id=?",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
    return {
        "id": row[0],
        "toolkit_id": toolkit_id,
        "label": row[1],
        "allowed_ips": json.loads(row[2]) if row[2] else None,
        "created_at": row[3],
        "revoked_at": row[4],
        "status": "revoked" if row[4] else "active",
    }


@router.delete("/{toolkit_id}/keys/{key_id}", status_code=204, summary="Revoke a client API key")
async def revoke_toolkit_key(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    key_id: Annotated[str, Path(description="Key ID to revoke")],
    request: Request,
):
    """
    Revoke a single access key.

    Other keys for this toolkit remain active. The revoked key immediately
    stops working — any agent using it will receive 401 on their next request.
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM toolkit_keys WHERE id=? AND toolkit_id=?", (key_id, toolkit_id)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Key '{key_id}' not found in toolkit '{toolkit_id}'")
        await db.execute("UPDATE toolkit_keys SET revoked_at=? WHERE id=?", (time.time(), key_id))
        await db.commit()
    audit_log.info(
        "KEY_REVOKED toolkit=%s key=%s actor=human ip=%s", toolkit_id, key_id, client_ip(request)
    )


# ── Toolkit Credentials ────────────────────────────────────────────────────


@router.post(
    "/{toolkit_id}/credentials",
    status_code=201,
    summary="Bind an upstream API credential to this toolkit — enable broker injection",
    response_model=CredentialBindingOut,
    openapi_extra={
        "requestBody": {
            "description": "Credential binding: credential_id to bind to this toolkit (enables broker to inject auth for that API)"
        }
    },
)
async def add_credential_to_toolkit(
    toolkit_id: Annotated[str, Path(description="Toolkit ID to bind credential to")],
    body: ToolkitCredentialAdd,
    request: Request,
):
    """Enrolls an existing upstream API credential in this toolkit. The broker automatically injects it into outbound calls for the API it's bound to, when the agent calls using this toolkit's client API key."""
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(403, "Only the admin key can modify toolkit credentials.")
    async with get_db() as db:
        async with db.execute("SELECT id FROM toolkits WHERE id=?", (toolkit_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Toolkit '{toolkit_id}' not found")
        async with db.execute(
            "SELECT id, label FROM credentials WHERE id=?", (body.credential_id,)
        ) as cur:
            cred = await cur.fetchone()
        if not cred:
            raise HTTPException(404, f"Credential '{body.credential_id}' not found")

        cc_id = str(uuid.uuid4())
        try:
            await db.execute(
                """INSERT INTO toolkit_credentials (id, toolkit_id, credential_id, alias, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (cc_id, toolkit_id, body.credential_id, cred[1], time.time()),
            )
            await db.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                raise HTTPException(409, "Credential already in toolkit")
            raise

    return {
        "id": cc_id,
        "toolkit_id": toolkit_id,
        "credential_id": body.credential_id,
        "credential_label": cred[1],
    }


@router.get(
    "/{toolkit_id}/credentials",
    summary="List upstream API credentials bound to this toolkit",
    response_model=list[CredentialBindingOut],
)
async def list_toolkit_credentials(
    toolkit_id: Annotated[str, Path(description="Toolkit ID to list credentials for")],
    request: Request,
):
    """List upstream API credentials bound to this toolkit.
    Admin (human session) may list any toolkit's credentials.
    Agents may list credentials for their own toolkit only.
    """
    is_admin = getattr(request.state, "is_admin", False)
    caller_toolkit = getattr(request.state, "toolkit_id", None)
    if not is_admin:
        if not caller_toolkit:
            raise HTTPException(403, "Authentication required to list toolkit credentials.")
        if caller_toolkit != toolkit_id:
            raise HTTPException(403, "Agents may only list credentials for their own toolkit.")
    async with get_db() as db:
        async with db.execute(
            """SELECT cc.id, cc.credential_id, c.label, c.api_id, cc.created_at
               FROM toolkit_credentials cc
               JOIN credentials c ON cc.credential_id = c.id
               WHERE cc.toolkit_id = ?""",
            (toolkit_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "credential_id": r[1],
            "credential_label": r[2],
            "api_id": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


@router.delete(
    "/{toolkit_id}/credentials/{credential_id:path}",
    status_code=204,
    summary="Unbind an upstream API credential from this toolkit",
)
async def remove_credential_from_toolkit(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    credential_id: Annotated[str, Path(description="Credential ID to unbind")],
):
    """
    Unbind a credential from this toolkit.

    Agents using this toolkit will immediately lose access to the upstream API.
    The credential remains in the vault and can be bound to other toolkits.

    To delete the credential entirely (from all toolkits), use `DELETE /credentials/{id}`.
    """
    async with get_db() as db:
        await db.execute(
            "DELETE FROM toolkit_credentials WHERE toolkit_id=? AND credential_id=?",
            (toolkit_id, credential_id),
        )
        await db.commit()


# ── Credential-scoped Permissions ────────────────────────────────────────────


@policy_router.get(
    "/{toolkit_id}/credentials/{cred_id:path}/permissions",
    summary="Get the permission rules for a specific credential in this toolkit",
    tags=["toolkits"],
    response_model=list[PermissionRuleOut],
)
async def get_credential_permissions(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    cred_id: Annotated[str, Path(description="Credential ID to get permissions for")],
):
    """Returns all rules in evaluation order for this credential: agent-defined rules first,
    then the immutable system safety rules appended by the server. First match wins.

    Since rules are scoped to a single credential (which is bound to a specific API),
    path and operation patterns apply only to calls made using this credential.
    System rules are tagged `_system: true` — they cannot be removed.
    """
    async with get_db() as db:
        # Verify credential belongs to this toolkit (or is accessible)
        async with db.execute(
            """SELECT c.id FROM credentials c
               LEFT JOIN toolkit_credentials tc ON c.id = tc.credential_id AND tc.toolkit_id = ?
               WHERE c.id = ?""",
            (toolkit_id, cred_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"Credential '{cred_id}' not found in toolkit '{toolkit_id}'")

        async with db.execute(
            "SELECT rules FROM credential_policies WHERE credential_id=?", (cred_id,)
        ) as cur:
            pol = await cur.fetchone()

    agent_rules = json.loads(pol[0]) if pol else []
    return agent_rules + SYSTEM_SAFETY_RULES


@policy_router.put(
    "/{toolkit_id}/credentials/{cred_id:path}/permissions",
    summary="Replace permission rules for a specific credential",
    tags=["toolkits"],
    response_model=list[PermissionRuleOut],
    dependencies=[Depends(require_human_session)],
    openapi_extra={
        "requestBody": {
            "description": "Array of permission rules to replace the entire agent rule list for this credential — each rule specifies effect (allow/deny), optional methods, optional path regex, and optional operation IDs"
        }
    },
)
async def set_credential_permissions(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    cred_id: Annotated[str, Path(description="Credential ID to set permissions for")],
    body: list[PolicyRule],
    request: Request,
):
    """Replaces the entire agent rule list for this credential.
    System safety rules are always appended server-side and cannot be removed.
    Use `PATCH` to add or remove individual rules without replacing the full list.
    """
    async with get_db() as db:
        async with db.execute("SELECT id FROM credentials WHERE id=?", (cred_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"Credential '{cred_id}' not found")

    rules_list = [r.model_dump(exclude_none=True) for r in body]
    result = await write_credential_permissions(cred_id, rules_list)
    audit_log.info(
        "PERMISSIONS_SET credential=%s rules=%d actor=human ip=%s",
        cred_id,
        len(rules_list),
        client_ip(request),
    )
    return result


@policy_router.patch(
    "/{toolkit_id}/credentials/{cred_id:path}/permissions",
    summary="Add or remove individual permission rules for a specific credential",
    tags=["toolkits"],
    response_model=list[PermissionRuleOut],
    dependencies=[Depends(require_human_session)],
    openapi_extra={
        "requestBody": {
            "description": "Incremental update: arrays of rules to add and/or remove from this credential's policy — rules are matched by exact equality for removal"
        }
    },
)
async def patch_credential_permissions(
    toolkit_id: Annotated[str, Path(description="Toolkit ID")],
    cred_id: Annotated[str, Path(description="Credential ID to patch permissions for")],
    body: PermissionsPatch,
    request: Request,
):
    """Incrementally update rules for this credential without replacing the full list.

    - `add`: rules appended (deduplicated)
    - `remove`: rules removed by exact match

    Example — unlock TTS writes for this credential:
    ```json
    {"add": [{"effect": "allow", "methods": ["POST"], "path": "text-to-speech"}]}
    ```
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT rules FROM credential_policies WHERE credential_id=?", (cred_id,)
        ) as cur:
            row = await cur.fetchone()

    current_rules: list[dict] = json.loads(row[0]) if row else []

    if body.remove:
        remove_set = [r.model_dump(exclude_none=True) for r in body.remove]
        current_rules = [r for r in current_rules if r not in remove_set]

    if body.add:
        existing = set(json.dumps(r, sort_keys=True) for r in current_rules)
        for rule in body.add:
            rule_dict = rule.model_dump(exclude_none=True)
            if json.dumps(rule_dict, sort_keys=True) not in existing:
                current_rules.append(rule_dict)
                existing.add(json.dumps(rule_dict, sort_keys=True))

    result = await write_credential_permissions(cred_id, current_rules)
    added = len(body.add) if body.add else 0
    removed = len(body.remove) if body.remove else 0
    audit_log.info(
        "PERMISSIONS_PATCHED credential=%s added=%d removed=%d actor=human ip=%s",
        cred_id,
        added,
        removed,
        client_ip(request),
    )
    return result


async def write_credential_permissions(credential_id: str, rules_list: list[dict]) -> list:
    """Persist agent rules for a credential and return the flat effective rule list."""
    summary = _generate_policy_summary(rules_list)
    rules_json = json.dumps(rules_list)
    now = time.time()
    policy_id = str(uuid.uuid4())

    async with get_db() as db:
        await db.execute(
            """INSERT INTO credential_policies (id, credential_id, rules, summary, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(credential_id) DO UPDATE SET
                 rules=excluded.rules,
                 summary=excluded.summary,
                 updated_at=excluded.updated_at""",
            (policy_id, credential_id, rules_json, summary, now, now),
        )
        await db.commit()

    return rules_list + SYSTEM_SAFETY_RULES


# ── Policy Enforcement (called by broker) ─────────────────────────────────────


async def check_credential_policy(
    credential_id: str,
    operation_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
) -> tuple[bool, str]:
    """Check if an operation is permitted by the credential's policy rules.
    Returns (allowed: bool, reason: str).
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT rules FROM credential_policies WHERE credential_id=?", (credential_id,)
        ) as cur:
            row = await cur.fetchone()

    agent_rules = json.loads(row[0]) if row else []
    return check_policy(agent_rules, operation_id, method, path)
