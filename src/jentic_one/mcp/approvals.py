"""Held (require-approval) executions on the mount: the per-call front doors.

The broker answers a call that matched a require-approval rule with the 202
held envelope. This module shapes it for the MCP client by what the client
declares on THIS request (``_meta["io.modelcontextprotocol/clientCapabilities"]``
— there is no session to remember it):

- ``elicitation.url`` declared → **URL elicitation** via a multi-round-trip
  ``InputRequiredResult``: the client asks the user's consent and opens the
  review page; its retry carries our sealed ``requestState``, and the job is
  read again (terminal → the result; still held → the short wait, then the
  held result). The elicitation ``accept`` is consent to open the link, never
  a decision — only a signed-in reviewer's ``:decide`` releases the job.
- otherwise → the **short wait** (poll the job up to ``SHORT_WAIT_SECONDS``),
  then the **held result**: the envelope as a normal tool result, which the
  model polls with ``get_execution_result``.

Form-mode elicitation is never used for approvals (an ``accept`` there is
client-authored data). Tasks are not offered: no task front door is served,
so a client declaring the Tasks extension gets the URL elicitation (when it
declares ``elicitation.url``) or the short wait and held result.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import mcp.types as mcp_types
import structlog

from jentic_one.mcp.envelopes import SCHEMA_VERSION

logger = structlog.get_logger(__name__)

#: The short server-side wait before a held result: inside a host's
#: tool-call timeout, long enough for a reviewer who is already looking.
SHORT_WAIT_SECONDS = 30.0
SHORT_WAIT_POLL_SECONDS = 2.0

#: Job statuses the short wait stops on.
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled", "dead_letter"})

#: The ``inputRequests`` key of the review-page URL elicitation.
REVIEW_INPUT_KEY = "review_approval"

FRONT_DOOR_URL_ELICITATION = "url_elicitation"
FRONT_DOOR_SHORT_WAIT = "short_wait"

_STATE_VERSION = 1


def client_capabilities(meta: Any) -> dict[str, Any]:
    """The request's declared client capabilities (empty when absent/malformed)."""
    if not isinstance(meta, dict):
        return {}
    caps = meta.get(mcp_types.CLIENT_CAPABILITIES_META_KEY)
    return caps if isinstance(caps, dict) else {}


def declares_url_elicitation(caps: dict[str, Any]) -> bool:
    """True only for an explicit ``elicitation.url`` (an empty ``elicitation`` is form-only)."""
    elicitation = caps.get("elicitation")
    return isinstance(elicitation, dict) and isinstance(elicitation.get("url"), dict)


def front_door(caps: dict[str, Any]) -> str:
    """The held-call front door this request's capabilities allow."""
    return FRONT_DOOR_URL_ELICITATION if declares_url_elicitation(caps) else FRONT_DOOR_SHORT_WAIT


def seal_request_state(encryption: Any, *, envelope: dict[str, Any], agent_id: str) -> str:
    """AEAD-seal the retry state: the held job, the approval, the agent, an expiry."""
    approval = envelope["approval"]
    state = {
        "v": _STATE_VERSION,
        "job_id": envelope["job_id"],
        "agent_id": agent_id,
        "approval": {
            "id": approval["id"],
            "review_url": approval["review_url"],
            "expires_at": approval["expires_at"],
        },
        "links": envelope.get("_links", {}),
        "exp": int(time.time() + SHORT_WAIT_SECONDS * 20),
    }
    return str(encryption.encrypt(json.dumps(state, separators=(",", ":"))))


def open_request_state(encryption: Any, token: str, *, agent_id: str) -> dict[str, Any] | None:
    """The sealed retry state, or None when it is forged, expired, or another agent's."""
    try:
        state = json.loads(encryption.decrypt(token))
    except Exception:
        return None
    if not isinstance(state, dict) or state.get("v") != _STATE_VERSION:
        return None
    if state.get("agent_id") != agent_id:
        return None
    exp = state.get("exp")
    if not isinstance(exp, int) or exp < time.time():
        return None
    if not isinstance(state.get("job_id"), str) or not isinstance(state.get("approval"), dict):
        return None
    return state


def envelope_from_state(state: dict[str, Any], directive: str) -> dict[str, Any]:
    """Rebuild the held envelope a retry answers with while the job is still held."""
    return {
        "job_id": state["job_id"],
        "status": "held",
        "approval": state["approval"],
        "agent_directive": directive,
        "_links": state.get("links") or {},
    }


def review_message(method: str, path: str) -> str:
    """The URL elicitation's consent prompt: who wants to do what."""
    return (
        f"This agent wants to {method} {path}, which needs human approval. "
        "Open the review page to approve or deny it (you sign in there)."
    )


def url_elicitation_result(
    *, review_url: str, message: str, request_state: str
) -> mcp_types.InputRequiredResult:
    """The multi-round-trip answer carrying one URL-mode elicitation of the review page."""
    return mcp_types.InputRequiredResult(
        input_requests={
            REVIEW_INPUT_KEY: mcp_types.ElicitRequest(
                params=mcp_types.ElicitRequestURLParams(message=message, url=review_url)
            )
        },
        request_state=request_state,
    )


def held_result_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    """The held result: the broker's envelope verbatim under the execute envelope keys."""
    return {"schema_version": SCHEMA_VERSION, "status": 202, "headers": {}, "body": envelope}


async def short_wait(
    poll: Any,
    job_id: str,
    *,
    window: float | None = None,
    interval: float | None = None,
) -> dict[str, Any] | None:
    """Poll the job for up to ``window`` seconds; its terminal poll payload, or None."""
    window = SHORT_WAIT_SECONDS if window is None else window
    interval = SHORT_WAIT_POLL_SECONDS if interval is None else interval
    deadline = time.monotonic() + window
    while True:
        payload: dict[str, Any] = await poll(job_id)
        if payload.get("status") in TERMINAL_JOB_STATUSES:
            return payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(interval, remaining))
