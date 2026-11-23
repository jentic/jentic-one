from datetime import UTC, datetime, timedelta

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType


def test_identity_serialization_and_defaults() -> None:
    identity = Identity(sub="usr_123", email="test@example.com")

    assert identity.sub == "usr_123"
    assert identity.email == "test@example.com"
    assert identity.permissions == []
    assert not hasattr(identity, "parent_permissions")
    assert identity.actor_type == "user"
    assert identity.parent_actor_id is None
    assert identity.must_change_password is False
    assert identity.expires_at is None
    assert identity.active is True


def test_identity_agent_assignment() -> None:
    identity = Identity(
        sub="agt_123",
        email="",
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_abc",
        permissions=["broker:execute"],
    )

    assert identity.permissions == ["broker:execute"]
    assert identity.actor_type == "agent"
    assert identity.parent_actor_id == "usr_abc"


def test_identity_expires_at_and_active() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    identity = Identity(
        sub="agnt_1",
        permissions=["broker:execute"],
        expires_at=future,
        active=True,
    )
    assert identity.expires_at == future
    assert identity.active is True


def test_identity_inactive() -> None:
    identity = Identity(sub="agnt_1", active=False)
    assert identity.active is False


def test_identity_email_defaults_to_empty() -> None:
    identity = Identity(sub="svc_1")
    assert identity.email == ""
