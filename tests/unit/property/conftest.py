"""Hypothesis profile configuration for property-based unit tests."""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile(
    "dev",
    max_examples=100,
    derandomize=True,
    deadline=None,
)
settings.register_profile(
    "ci",
    max_examples=300,
    derandomize=True,
    deadline=None,
)
settings.load_profile(
    os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev")
)
