"""Request-state ciphertexts are domain-separated from stored-secret ciphertexts."""

from __future__ import annotations

import base64
import json
import time

import pytest

from jentic_one.mcp.approvals import open_request_state, seal_request_state
from jentic_one.shared.config import EncryptionConfig
from jentic_one.shared.crypto import DecryptionError, EncryptionService

_AGENT_ID = "agent_1"
_ENVELOPE = {
    "held_execution_id": "held_1",
    "review_url": "https://jentic.example/app/agents/approvals/held_1",
    "expires_at": "2026-10-07T00:00:00Z",
    "_links": {"result": "https://jentic.example/executions/holds/held_1/result"},
}


def _encryption() -> EncryptionService:
    material = base64.b64encode(b"r" * 32).decode()
    return EncryptionService(
        EncryptionConfig.model_validate(
            {"active_id": "v1", "entries": [{"id": "v1", "material": material}]}
        )
    )


def test_request_state_does_not_open_a_stored_secret_blob() -> None:
    encryption = _encryption()
    state = {
        "v": 1,
        "held_execution_id": "held_1",
        "agent_id": _AGENT_ID,
        "review_url": _ENVELOPE["review_url"],
        "expires_at": _ENVELOPE["expires_at"],
        "links": _ENVELOPE["_links"],
        "exp": int(time.time()) + 600,
    }
    stored_secret_blob = encryption.encrypt(json.dumps(state, separators=(",", ":")))

    assert open_request_state(encryption, stored_secret_blob, agent_id=_AGENT_ID) is None


def test_stored_secret_decrypt_rejects_request_state() -> None:
    encryption = _encryption()
    request_state = seal_request_state(encryption, envelope=_ENVELOPE, agent_id=_AGENT_ID)

    with pytest.raises(DecryptionError):
        encryption.decrypt(request_state)


def test_request_state_round_trips_for_its_agent() -> None:
    encryption = _encryption()
    request_state = seal_request_state(encryption, envelope=_ENVELOPE, agent_id=_AGENT_ID)

    state = open_request_state(encryption, request_state, agent_id=_AGENT_ID)

    assert state is not None
    assert state["v"] == 1
    assert state["held_execution_id"] == _ENVELOPE["held_execution_id"]
    assert state["agent_id"] == _AGENT_ID
    assert state["review_url"] == _ENVELOPE["review_url"]
    assert state["expires_at"] == _ENVELOPE["expires_at"]
    assert state["links"] == _ENVELOPE["_links"]
