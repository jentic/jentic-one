"""The webhook pipeline owns its egress policy, independent of the broker.

Phase 1 item 6: outbound webhook delivery is semantically owned by the webhook
feature, so its SSRF/egress policy lives on ``config.webhooks.egress`` — a
dedicated, always-non-None ``EgressConfig`` — not on ``config.broker.egress``.
A process with no broker surface must still deliver webhooks under a safe
policy, and Phase 3's per-endpoint allowlist extends *this* seam.
"""

from __future__ import annotations

from jentic_one.shared.config import AppConfig, EgressConfig, WebhookConfig


def test_webhook_egress_is_non_none_and_strict_by_default() -> None:
    """A default WebhookConfig always yields a concrete, strict egress policy."""
    egress = WebhookConfig().egress
    assert isinstance(egress, EgressConfig)
    # Strict by default: no private ranges allowlisted.
    assert egress.allowed_private_subnets == []


def test_webhook_egress_instance_is_independent_of_broker_default() -> None:
    """The webhook egress default is its own instance, not the broker's.

    Two freshly-defaulted configs must not share the same EgressConfig object,
    so tuning one never silently changes the other.
    """
    assert WebhookConfig().egress is not WebhookConfig().egress


def test_webhook_config_is_registered_on_app_config() -> None:
    """``AppConfig`` exposes a ``webhooks`` field of type ``WebhookConfig``.

    Checked via the model schema (no full AppConfig instantiation, which would
    require a databases block) so the wiring is asserted directly.
    """
    field = AppConfig.model_fields.get("webhooks")
    assert field is not None
    assert field.annotation is WebhookConfig


def test_webhook_config_default_factory_produces_webhook_config() -> None:
    field = AppConfig.model_fields["webhooks"]
    assert field.default_factory is not None
    assert isinstance(field.default_factory(), WebhookConfig)  # type: ignore[call-arg]
