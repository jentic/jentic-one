"""AWS Marketplace entitlement integration (the license gate).

Active only for the AWS Marketplace container listing — gated on
``entitlement.enabled`` (default off), so every other deployment never imports
a live code path from here. See ``EntitlementConfig`` in ``shared/config.py``
for the failure posture and ``docs/development/marketplace-publishing.md`` for
the operator story.
"""
