#!/usr/bin/env python3
"""Jentic One -> Slack relay (Approach 1: raw signed webhook + relay).

Jentic One POSTs a FIXED, HMAC-signed JSON envelope to this relay. This relay:

  1. verifies the Standard-Webhooks signature using the endpoint's signing secret,
  2. reshapes the event into a rich Slack Block Kit message
     ({"blocks": [...], "attachments": [{"color": ..., "blocks": [...]}]}),
  3. forwards it to your Slack incoming-webhook URL.

Point your Jentic webhook endpoint's *Target URL* at THIS relay's public URL,
NOT at Slack directly (Slack can't verify the signature or read Jentic's payload
shape). For local testing, expose this with:  ngrok http 8090

------------------------------------------------------------------------------
What Jentic sends (verified against this branch's code):

  Headers (shared/webhooks/signing.py):
    webhook-id:        <event id>
    webhook-timestamp: <unix seconds>
    webhook-signature: v1,<hex hmac-sha256>

  Signed content:  b"{webhook-id}.{webhook-timestamp}.{raw request body bytes}"
  Signature:       hmac_sha256(secret, signed_content).hexdigest(), prefixed "v1,"
  Tolerance:       300s timestamp drift (DEFAULT_TOLERANCE_SECONDS)

  Body (shared/webhooks/delivery.py::_serialise) — the EXACT bytes signed & sent,
  json.dumps(..., separators=(",", ":"), sort_keys=True):
    {"data": <inner>, "id": "<event_id>", "type": "<event_type>"}

  where <inner> (admin/services/webhooks/fanout.py::build_notification_payload) is:
    {
      "event_id":   "...",
      "event_type": "credential.expired",
      "severity":   "error",
      "summary":    "Credential ... has expired",
      "created_at": "2026-08-19T...Z",
      "data":       { ...event-specific... }
    }

  The synthetic "webhook.test" event has a SIMPLER inner shape (no severity /
  created_at / nested data) — this relay handles the missing fields gracefully:
    {"event_type": "webhook.test", "summary": "...", "triggered_by": "..."}
------------------------------------------------------------------------------

What this relay sends to Slack (Block Kit):

  A single incoming-webhook POST of the form

    {
      "blocks":      [ <header block: emoji + friendly title>,
                       <section: summary> ],
      "attachments": [ { "color": "<hex per severity>",
                         "blocks": [ <section with curated data fields>,
                                     <optional actions block: Approve button>,
                                     <context: readable timestamp + raw type> ] } ]
    }

  The `color` puts a coloured vertical rail beside the message:
    critical/error -> red (#E01E5A), warning -> amber (#ECB22E),
    info -> green (#2EB67D), unknown -> grey. The header carries a friendly,
    human title + emoji derived from the event type (see EVENT_META). Known event
    types (execution.completed/failed, access_request.*) get a CURATED field
    layout (operation/api/status/duration; requester + requested access) instead
    of an alphabetised dump; other types fall back to the generic humanized grid.
    When the payload carries a safe `approve_url`, an *Approve / review* button is
    rendered. The raw event id is de-emphasised into the small grey context
    footer. `created_at` is shown as a readable "Aug 19, 2026 16:18 UTC".
------------------------------------------------------------------------------

Delivery semantics:
  Signature is verified SYNCHRONOUSLY (an unverifiable request is rejected 401
  and never reaches Slack). A verified request is acked to Jentic IMMEDIATELY
  with 202 and forwarded to Slack on a background thread, so slow Slack calls
  can't provoke a Jentic-side retry.
------------------------------------------------------------------------------

Run:
    # one or more comma-separated secrets (paste "new,old" during a rotation):
    export JENTIC_ENDPOINT_SECRET="<paste the whsec_ secret shown once on create>"
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../..."
    python3 slack_relay.py                               # listens on 0.0.0.0:8090 (PORT overrides)
    ngrok http 8090                                      # -> public https URL for the Target URL
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Standard-Webhooks constants (must match shared/webhooks/signing.py) ------
HEADER_ID = "webhook-id"
HEADER_TIMESTAMP = "webhook-timestamp"
HEADER_SIGNATURE = "webhook-signature"
SCHEME = "v1"
TOLERANCE_SECONDS = 300

PORT = int(os.environ.get("PORT", "8090"))


def _parse_secrets(raw: str) -> list[str]:
    """Split the ``JENTIC_ENDPOINT_SECRET`` env into one or more candidate secrets.

    Rotating a signing secret leaves a **grace window** in which Jentic signs
    each delivery with BOTH the new and the previous secret (the header carries
    multiple ``v1,<hex>`` tokens). A relay that only ever held one secret would
    reject every delivery signed solely with the other during that window and
    drop notifications. Accepting a comma-separated list lets an operator paste
    ``new_secret,old_secret`` for the overlap and remove the old one once the
    rotation completes — no dropped deliveries. Blank/whitespace entries are
    ignored so a trailing comma is harmless.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


SECRETS = _parse_secrets(os.environ.get("JENTIC_ENDPOINT_SECRET", ""))
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Colour of the attachment rail per severity (Slack `attachment.color`).
#   critical/error -> red, warning -> amber, info -> green, unknown -> grey.
_SEVERITY_COLOR = {
    "critical": "#E01E5A",
    "error": "#E01E5A",
    "warning": "#ECB22E",
    "info": "#2EB67D",
}
_DEFAULT_COLOR = "#9AA0A6"

# Friendly (emoji, title) per relayable event type. This mirrors the UI's
# authoritative catalog (ui/src/modules/webhooks/api/eventCatalog.ts), which is
# itself the relayable set = EventType.ALL (shared/models/events.py) minus
# NEVER_RELAYED (admin/services/webhooks/fanout.py). Every relayable type is
# listed; unknown/future types fall back to a generic emoji + title-cased type
# (see _event_meta). "webhook.test" is included for the synthetic test event.
EVENT_META: dict[str, tuple[str, str]] = {
    # --- Credentials ---------------------------------------------------------
    "credential.expired": (":lock:", "Credential expired"),
    "credential.expiring_soon": (":hourglass_flowing_sand:", "Credential expiring soon"),
    "credential.stored": (":floppy_disk:", "Credential stored"),
    "credential.connected": (":link:", "Credential connected"),
    "credential.connection_failed": (":x:", "Credential connection failed"),
    "credential.refresh_failed": (":arrows_counterclockwise:", "Credential refresh failed"),
    "credential.not_provisioned": (":no_entry:", "Credential not provisioned"),
    "credential.undecryptable": (":lock_with_ink_pen:", "Credential undecryptable"),
    "credential.bound_to_toolkit": (":key:", "Credential bound to toolkit"),
    "credential.unbound_from_toolkit": (":unlock:", "Credential unbound from toolkit"),
    # --- Executions & jobs ---------------------------------------------------
    "execution.completed": (":white_check_mark:", "Execution completed"),
    "execution.failed": (":x:", "Execution failed"),
    "execution.repeated_failure": (":rotating_light:", "Execution repeated failure"),
    "upstream.circuit_open": (":electric_plug:", "Upstream circuit opened"),
    "import.completed": (":inbox_tray:", "Import completed"),
    "import.failed": (":x:", "Import failed"),
    "job.failed_permanently": (":skull:", "Job failed permanently"),
    # --- Catalog & overlays --------------------------------------------------
    "catalog.update_available": (":sparkles:", "Catalog update available"),
    "catalog.update_conflicts_overlay": (":warning:", "Catalog update conflicts with overlay"),
    "overlay.deprecated": (":wastebasket:", "Overlay deprecated"),
    # --- Access requests -----------------------------------------------------
    "access_request.filed": (":inbox_tray:", "Access request filed"),
    "access_request.approved": (":white_check_mark:", "Access request approved"),
    "access_request.denied": (":no_entry_sign:", "Access request denied"),
    "access_request.withdrawn": (":leftwards_arrow_with_hook:", "Access request withdrawn"),
    # --- Toolkits ------------------------------------------------------------
    "toolkit.created": (":toolbox:", "Toolkit created"),
    "toolkit.key_created": (":old_key:", "Toolkit key created"),
    "toolkit.permission_rule_set": (":closed_lock_with_key:", "Toolkit permission rule set"),
    # --- Agents --------------------------------------------------------------
    "agent.created": (":robot_face:", "Agent created"),
    "agent.self_registered": (":wave:", "Agent self-registered"),
    "agent.registration_approved": (":white_check_mark:", "Agent registration approved"),
    "agent.registration_denied": (":no_entry_sign:", "Agent registration denied"),
    "toolkit.bound_to_agent": (":handshake:", "Toolkit bound to agent"),
    "toolkit.unbound_from_agent": (":wave:", "Toolkit unbound from agent"),
    # --- Security ------------------------------------------------------------
    "security.unauthorized_access_attempt": (":rotating_light:", "Unauthorized access attempt"),
    "broker.pbac_denied": (":no_entry:", "Permission denied (PBAC)"),
    "broker.toolkit_binding_unserved": (":electric_plug:", "Toolkit binding unserved"),
    # --- Synthetic test event ------------------------------------------------
    "webhook.test": (":test_tube:", "Webhook test"),
}

# Humanized labels for `data` keys. Anything not listed falls back to a
# title-cased version of the snake_case key (see _humanize_label).
_FIELD_LABELS: dict[str, str] = {
    "credential_id": "Credential ID",
    "api_vendor": "Vendor",
    "api_name": "API name",
    "api_version": "API version",
    "api_host": "API host",
    "expires_at": "Expires at",
    "toolkit_id": "Toolkit ID",
    "agent_id": "Agent ID",
    "execution_id": "Execution ID",
    "job_id": "Job ID",
    "operation_id": "Operation ID",
    "overlay_id": "Overlay ID",
    "access_request_id": "Access request ID",
    "trace_id": "Trace ID",
    "host": "Host",
    "vendor": "Vendor",
    "name": "Name",
    "version": "Version",
    "tags": "Tags",
    "triggered_by": "Triggered by",
    "source": "Source",
    "reason": "Reason",
}


def _verify(headers: dict[str, str], raw_body: bytes) -> tuple[bool, str]:
    """Return (ok, reason). Verifies exactly like a Standard-Webhooks consumer.

    Accepts across a secret rotation on BOTH axes: the header may carry several
    ``v1,<hex>`` signatures, and this relay may hold several candidate secrets
    (see ``_parse_secrets``). A delivery is genuine if ANY header signature
    matches the expected signature under ANY held secret — so neither rotating
    the secret nor Jentic dual-signing during the grace window drops a delivery.
    All comparisons are constant-time.
    """
    msg_id = headers.get(HEADER_ID, "")
    ts = headers.get(HEADER_TIMESTAMP, "")
    sig_header = headers.get(HEADER_SIGNATURE, "")
    if not (msg_id and ts and sig_header):
        return False, "missing signature headers"

    # Replay window: reject stale/of-the-future timestamps.
    try:
        drift = abs(int(time.time()) - int(ts))
    except ValueError:
        return False, "bad timestamp"
    if drift > TOLERANCE_SECONDS:
        return False, f"timestamp outside tolerance ({drift}s)"

    # Recompute over the RAW received bytes — never re-serialise the JSON. One
    # expected signature per held secret (the rotation set).
    signed_content = b".".join((msg_id.encode(), ts.encode(), raw_body))
    expected = [
        hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest() for secret in SECRETS
    ]

    # The header may carry multiple space-separated "v1,<hex>" signatures
    # (during a secret rotation). Accept if any header signature matches any
    # expected signature, in constant time.
    for token in sig_header.split():
        scheme, _, value = token.partition(",")
        if scheme != SCHEME:
            continue
        for candidate in expected:
            if hmac.compare_digest(value, candidate):
                return True, "ok"
    return False, "signature mismatch"


def _event_meta(event_type: str) -> tuple[str, str]:
    """(emoji, friendly title) for an event type, with a graceful fallback."""
    if event_type in EVENT_META:
        return EVENT_META[event_type]
    # Unknown/future type: generic bell + a readable title-cased version of the
    # dotted type (e.g. "widget.frobnicated" -> "Widget frobnicated").
    tail = event_type.split(".")[-1].replace("_", " ").strip()
    title = tail.capitalize() if tail else event_type
    return ":bell:", title


def _humanize_label(key: str) -> str:
    """Human label for a `data` key ("api_vendor" -> "Vendor")."""
    if key in _FIELD_LABELS:
        return _FIELD_LABELS[key]
    return key.replace("_", " ").strip().capitalize()


def _format_timestamp(created_at: str | None) -> str | None:
    """Turn an ISO-8601 `created_at` into "Aug 19, 2026 16:18 UTC".

    Returns None when there is nothing usable to show (so callers can omit the
    footer timestamp). Never raises on a malformed value — falls back to the
    raw string so a formatting quirk can't drop the event.
    """
    if not created_at:
        return None
    raw = str(created_at)
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%b %-d, %Y %H:%M UTC")


def _flatten(prefix: str, value: object) -> list[tuple[str, str]]:
    """Flatten a (possibly nested) data value into (label, text) pairs.

    Nested dicts (e.g. execution's ``api: {vendor, name, version}``) are
    flattened with humanized child labels; lists are joined; scalars stringified.
    """
    if isinstance(value, dict):
        pairs: list[tuple[str, str]] = []
        for k in sorted(value.keys()):
            pairs.extend(_flatten(str(k), value[k]))
        return pairs
    label = _humanize_label(prefix)
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(v) for v in value) if value else "—"
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value)
    return [(label, text)]


def _data_fields(data: dict) -> list[dict]:
    """Render a `data` dict as Slack `section.fields` mrkdwn entries.

    Keys are humanized and sorted for a stable order; nested dicts are flattened.
    Returns [] when there is nothing to show. Slack caps a section at 10 fields.
    """
    pairs: list[tuple[str, str]] = []
    for key in sorted(data.keys()):
        pairs.extend(_flatten(key, data[key]))
    fields = [{"type": "mrkdwn", "text": f"*{label}*\n`{text}`"} for label, text in pairs]
    return fields[:10]


# Keys rendered by a curated template directly (or deliberately hidden) — kept
# out of the generic field grid so they are not shown twice.
_APPROVE_URL_KEY = "approve_url"


def _field(label: str, text: str) -> dict:
    return {"type": "mrkdwn", "text": f"*{label}*\n{text}"}


def _execution_fields(data: dict) -> list[dict]:
    """Curated fields for ``execution.completed`` — operation, api, status, time.

    Reads the Phase-2 enriched ``data`` (operation_id / api{vendor,name,version}
    / duration_ms / http_status). Renders a compact, human-ordered grid instead
    of an alphabetised dump, and gracefully shows only what is present.
    """
    fields: list[dict] = []
    if data.get("operation_id"):
        fields.append(_field("Operation", f"`{data['operation_id']}`"))
    api = data.get("api")
    if isinstance(api, dict) and api:
        api_text = "/".join(str(api[k]) for k in ("vendor", "name", "version") if api.get(k))
        if api_text:
            fields.append(_field("API", api_text))
    if data.get("http_status") is not None:
        fields.append(_field("HTTP status", f"`{data['http_status']}`"))
    if data.get("duration_ms") is not None:
        fields.append(_field("Duration", f"{data['duration_ms']} ms"))
    if data.get("toolkit_id"):
        fields.append(_field("Toolkit", f"`{data['toolkit_id']}`"))
    return fields


def _access_request_fields(data: dict) -> list[dict]:
    """Curated fields for ``access_request.filed`` — requester + what they asked for.

    Reads the Phase-2 enriched ``data`` (requested_by + items[{action,resource}]);
    ``approve_url`` is intentionally not rendered here — it becomes an *Approve*
    button (see ``_action_blocks``).
    """
    fields: list[dict] = []
    if data.get("requested_by"):
        fields.append(_field("Requested by", f"`{data['requested_by']}`"))
    items = data.get("items")
    if isinstance(items, list) and items:
        lines = []
        for item in items:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "")
            resource = item.get("resource")
            lines.append(f"• `{action}`" + (f" → {resource}" if resource else ""))
        if lines:
            fields.append(_field("Requested access", "\n".join(lines)))
    if data.get("status"):
        fields.append(_field("Status", f"`{data['status']}`"))
    return fields


# Per-event curated field renderers. Any event type not listed falls back to the
# generic humanized grid (``_data_fields``), so future/unknown types still render.
_CURATED_FIELDS = {
    "execution.completed": _execution_fields,
    "execution.failed": _execution_fields,
    "access_request.filed": _access_request_fields,
    "access_request.approved": _access_request_fields,
    "access_request.denied": _access_request_fields,
    "access_request.withdrawn": _access_request_fields,
}


def _curated_fields(event_type: str, data: dict) -> list[dict]:
    """Curated per-event fields when available, else the generic humanized grid."""
    renderer = _CURATED_FIELDS.get(event_type)
    if renderer is not None:
        fields = renderer(data)
        if fields:
            return fields[:10]
    # Fall back to the generic grid, but never re-show the approve_url (it is a
    # button, not a field).
    generic = {k: v for k, v in data.items() if k != _APPROVE_URL_KEY}
    return _data_fields(generic)


def _action_blocks(data: dict) -> list[dict]:
    """An *Approve* button when a safe ``approve_url`` is on the wire, else [].

    The url is Jentic's canonical review link (no bearer token); following it
    still requires the reviewer to authenticate, so surfacing it as a button is
    safe and turns a filed request into a one-click review.
    """
    url = data.get(_APPROVE_URL_KEY)
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return []
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve / review", "emoji": True},
                    "url": url,
                    "style": "primary",
                }
            ],
        }
    ]


def _build_slack_message(envelope: dict) -> dict:
    """Reshape Jentic's fixed envelope into a Slack Block Kit payload.

    Handles both real events (with severity/created_at/nested data) and the
    simpler synthetic ``webhook.test`` event (which lacks those), never crashing
    on a missing key. Known event types get a curated field layout and, when the
    payload carries an ``approve_url``, an *Approve* button; the raw event id is
    de-emphasised into the small context footer (or hidden entirely).
    """
    inner = envelope.get("data") or {}
    event_type = str(inner.get("event_type", envelope.get("type", "event")))
    severity = str(inner.get("severity", "info")).lower()
    summary = inner.get("summary") or "(no summary)"
    emoji, title = _event_meta(event_type)
    color = _SEVERITY_COLOR.get(severity, _DEFAULT_COLOR)

    # Top-level blocks: a header (emoji + friendly title) and the summary.
    header_text = f"{emoji} {title}"
    top_blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
    ]

    # Attachment blocks (inside the coloured rail): event-specific fields, an
    # optional action button, then a de-emphasised context footer.
    attachment_blocks: list[dict] = []

    data = inner.get("data")
    # The test event carries no nested `data`; surface its `triggered_by`
    # instead so the message still shows something useful.
    if not isinstance(data, dict) or not data:
        data = {}
        if inner.get("triggered_by"):
            data = {"triggered_by": inner["triggered_by"]}
    fields = _curated_fields(event_type, data)
    if fields:
        attachment_blocks.append({"type": "section", "fields": fields})

    attachment_blocks.extend(_action_blocks(data))

    context_elements: list[dict] = []
    when = _format_timestamp(inner.get("created_at"))
    if when:
        context_elements.append({"type": "mrkdwn", "text": f":clock3: {when}"})
    context_elements.append({"type": "mrkdwn", "text": f"`{event_type}`"})
    # The event id is debugging metadata, not something a human reads — keep it
    # in the small grey context footer only (never a headline field).
    event_id = inner.get("event_id") or envelope.get("id")
    if event_id:
        context_elements.append({"type": "mrkdwn", "text": f"id `{event_id}`"})
    attachment_blocks.append({"type": "context", "elements": context_elements})

    return {
        "blocks": top_blocks,
        "attachments": [{"color": color, "blocks": attachment_blocks}],
    }


def _post_to_slack(message: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=json.dumps(message).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def _forward_async(message: dict, event_type: str | None) -> None:
    """Forward to Slack on a background thread, logging the outcome.

    Slack call latency (or an outage) must not couple to Jentic's delivery
    timeout: the request handler has already answered 2xx before this runs, so a
    slow Slack POST can't trigger a Jentic-side retry/backoff. Errors are logged
    for the operator; there is no back-channel to Jentic once we've acked (an
    at-most-once relay by design — Jentic already retries transport failures on
    its side, and duplicate suppression uses the event id).
    """

    def _run() -> None:
        status, resp = _post_to_slack(message)
        if 200 <= status < 300:
            print(f"[ok] forwarded {event_type} to Slack")
        else:
            print(f"[slack-error] {status}: {resp}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: str) -> None:
        self.send_response(code)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:
        # Liveness endpoint so platform health checks (Render/Fly/Cloud Run) can
        # probe with a plain GET instead of a signed POST. Any GET path -> 200.
        self._reply(200, "ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        headers = {k.lower(): v for k, v in self.headers.items()}

        # Verify BEFORE the async handoff: an unverifiable request must never
        # reach Slack, and rejecting synchronously keeps the 401 meaningful.
        ok, reason = _verify(headers, raw)
        if not ok:
            print(f"[reject] {reason}", file=sys.stderr)
            # 401 is deliberate: an unverifiable request must never reach Slack.
            self._reply(401, f"rejected: {reason}")
            return

        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, "invalid json")
            return

        message = _build_slack_message(envelope)
        # Answer Jentic immediately, then forward to Slack asynchronously. Slack
        # latency no longer couples to Jentic's delivery timeout, so a slow Slack
        # call can't provoke a Jentic-side retry. 202 = "accepted for delivery".
        _forward_async(message, envelope.get("type"))
        self._reply(202, "accepted")

    def log_message(self, *args) -> None:  # silence default access log
        pass


def main() -> None:
    missing = []
    if not SECRETS:
        missing.append("JENTIC_ENDPOINT_SECRET")
    if not SLACK_WEBHOOK_URL:
        missing.append("SLACK_WEBHOOK_URL")
    if missing:
        print(f"error: set these env vars first: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.allow_reuse_address = True
    print(f"Jentic -> Slack relay listening on http://0.0.0.0:{PORT}")
    print("  verifying deliveries with JENTIC_ENDPOINT_SECRET, forwarding to Slack")
    print("  set your endpoint's Target URL to this relay's PUBLIC url (e.g. ngrok https URL)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
