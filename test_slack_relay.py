"""Tests for the standalone Jentic -> Slack relay (``slack_relay.py``).

The relay lives at the repo root, outside the ``jentic_one`` package and outside
``tests/`` (so it is not collected by the default ``testpaths``). Run it with
coverage disabled and pointed at this file, e.g.::

    uv run pytest test_slack_relay.py --no-cov -p no:cacheprovider

These cover the Phase-2 relay hardening:
  * multi-secret verification across a rotation grace window,
  * synchronous verify + immediate 2xx with async Slack forwarding,
  * curated Block Kit for the two enriched events + the Approve button.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import time

# Import fresh so we can control module-level SECRETS / SLACK_WEBHOOK_URL.
relay = importlib.import_module("slack_relay")


def _sign(secret: str, msg_id: str, ts: str, body: bytes) -> str:
    signed = b".".join((msg_id.encode(), ts.encode(), body))
    return "v1," + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def _headers(sig: str, msg_id: str = "evt_1", ts: str | None = None) -> dict[str, str]:
    return {
        relay.HEADER_ID: msg_id,
        relay.HEADER_TIMESTAMP: ts or str(int(time.time())),
        relay.HEADER_SIGNATURE: sig,
    }


# --- multi-secret verification ------------------------------------------------


def test_verify_accepts_signature_from_any_held_secret(monkeypatch):
    body = b'{"hello":"world"}'
    ts = str(int(time.time()))
    monkeypatch.setattr(relay, "SECRETS", ["new_secret", "old_secret"])

    # A delivery signed with the OLD secret must still verify during the overlap.
    sig = _sign("old_secret", "evt_1", ts, body)
    ok, reason = relay._verify(_headers(sig, ts=ts), body)
    assert ok, reason

    # And one signed with the NEW secret.
    sig = _sign("new_secret", "evt_1", ts, body)
    ok, reason = relay._verify(_headers(sig, ts=ts), body)
    assert ok, reason


def test_verify_accepts_dual_signed_header(monkeypatch):
    body = b"{}"
    ts = str(int(time.time()))
    monkeypatch.setattr(relay, "SECRETS", ["new_secret"])
    # Header carries both signatures (Jentic dual-signs during rotation); we only
    # hold the new secret — the new token must match.
    both = f"{_sign('old_secret', 'evt_1', ts, body)} {_sign('new_secret', 'evt_1', ts, body)}"
    ok, reason = relay._verify(_headers(both, ts=ts), body)
    assert ok, reason


def test_verify_rejects_unknown_secret(monkeypatch):
    body = b"{}"
    ts = str(int(time.time()))
    monkeypatch.setattr(relay, "SECRETS", ["known"])
    sig = _sign("attacker", "evt_1", ts, body)
    ok, reason = relay._verify(_headers(sig, ts=ts), body)
    assert not ok
    assert reason == "signature mismatch"


def test_verify_rejects_stale_timestamp(monkeypatch):
    body = b"{}"
    old_ts = str(int(time.time()) - relay.TOLERANCE_SECONDS - 10)
    monkeypatch.setattr(relay, "SECRETS", ["known"])
    sig = _sign("known", "evt_1", old_ts, body)
    ok, reason = relay._verify(_headers(sig, ts=old_ts), body)
    assert not ok
    assert "tolerance" in reason


def test_parse_secrets_splits_and_trims():
    assert relay._parse_secrets("a, b ,c,") == ["a", "b", "c"]
    assert relay._parse_secrets("") == []


# --- curated Block Kit templates ---------------------------------------------


def _envelope(event_type: str, data: dict, summary: str = "s") -> dict:
    return {
        "id": "evt_1",
        "type": event_type,
        "data": {
            "event_id": "evt_1",
            "event_type": event_type,
            "severity": "info",
            "summary": summary,
            "created_at": "2026-08-19T10:30:00+00:00",
            "data": data,
        },
    }


def _all_blocks(message: dict) -> list[dict]:
    blocks = list(message.get("blocks", []))
    for att in message.get("attachments", []):
        blocks.extend(att.get("blocks", []))
    return blocks


def test_execution_completed_curated_fields():
    env = _envelope(
        "execution.completed",
        {
            "execution_id": "exec_1",
            "operation_id": "op_send",
            "api": {"vendor": "acme", "name": "widgets", "version": "v1"},
            "duration_ms": 123,
            "http_status": 200,
            "toolkit_id": "tk_1",
        },
        summary="Execution of op_send (acme) completed in 123ms",
    )
    message = relay._build_slack_message(env)
    text = json.dumps(message)
    assert "Operation" in text and "op_send" in text
    assert "acme/widgets/v1" in text
    assert "123 ms" in text
    assert "200" in text
    # The event id is footer-only, never a headline field: it must not appear in
    # a section field label.
    section_fields = [
        f["text"]
        for b in _all_blocks(message)
        if b.get("type") == "section"
        for f in b.get("fields", [])
    ]
    assert not any("exec_1" in f and "*Execution ID*" in f for f in section_fields)


def test_access_request_filed_curated_fields_and_approve_button():
    approve = "https://app.jentic.com/access-requests/req_1"
    env = _envelope(
        "access_request.filed",
        {
            "request_id": "req_1",
            "status": "pending",
            "requested_by": "user_42",
            "approve_url": approve,
            "items": [{"action": "credential:read", "resource": "Acme prod key"}],
        },
        summary="user_42 requested credential:read on Acme prod key",
    )
    message = relay._build_slack_message(env)
    blocks = _all_blocks(message)

    # An actions block with a primary button linking to the approve_url.
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert actions, "expected an Approve actions block"
    button = actions[0]["elements"][0]
    assert button["url"] == approve
    assert button["style"] == "primary"

    text = json.dumps(message)
    assert "user_42" in text
    assert "credential:read" in text
    assert "Acme prod key" in text
    # approve_url must NOT also be rendered as a plain field (it's a button).
    section_fields = [
        f["text"] for b in blocks if b.get("type") == "section" for f in b.get("fields", [])
    ]
    assert not any(approve in f for f in section_fields)


def test_no_approve_button_without_url():
    env = _envelope("access_request.filed", {"request_id": "req_1", "status": "pending"})
    message = relay._build_slack_message(env)
    assert not [b for b in _all_blocks(message) if b.get("type") == "actions"]


def test_unsafe_approve_url_scheme_is_dropped():
    env = _envelope(
        "access_request.filed",
        {"request_id": "req_1", "status": "pending", "approve_url": "javascript:alert(1)"},
    )
    message = relay._build_slack_message(env)
    assert not [b for b in _all_blocks(message) if b.get("type") == "actions"]


def test_unknown_event_falls_back_to_generic_grid():
    env = _envelope("widget.frobnicated", {"widget_id": "w_1", "count": 3})
    message = relay._build_slack_message(env)
    text = json.dumps(message)
    assert "w_1" in text and "3" in text


# --- async forward + immediate 2xx -------------------------------------------


def test_forward_async_returns_immediately_and_forwards(monkeypatch):
    import threading

    started = threading.Event()
    done = threading.Event()
    captured: dict = {}

    def fake_post(message):
        started.set()
        captured["message"] = message
        # Simulate a slow Slack; the caller must NOT block on this.
        time.sleep(0.05)
        done.set()
        return 200, "ok"

    monkeypatch.setattr(relay, "_post_to_slack", fake_post)

    relay._forward_async({"blocks": []}, "execution.completed")
    # The call returned before the (slow) Slack post finished.
    assert not done.is_set()
    assert started.wait(timeout=2.0)
    assert done.wait(timeout=2.0)
    assert captured["message"] == {"blocks": []}
