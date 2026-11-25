"""Credential grant expiry: how agents' bindings to credentials end.

The limits themselves live on each binding and are enforced by the broker when
it reads the binding. Set here, deployment-wide (one org per deployment):

- the org's defaults and maximums for new grants (D7, step 8): a default
  applies when a request to create a grant sets no policy at all; a maximum
  refuses (422) anything longer, and a grant with no such limit, on every
  path that creates or renews one;
- the owner's opt-in warning before a binding ends.

Every length of time is an ISO 8601 duration (``P30D``, ``PT36H``, ``P2W``;
weeks, days, hours, minutes or seconds, never years or months), as in the
API. Once loaded, each holds whole seconds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field, model_validator

from jentic_one.shared.iso_duration import DAY, iso_duration

if TYPE_CHECKING:  # seconds, once validated
    _Lifetime = int
    _IdlePeriod = int
    _Interval = int
    _Window = int
else:
    _Lifetime = iso_duration(1, config=True)
    _IdlePeriod = iso_duration(DAY, config=True)
    _Interval = iso_duration(60, config=True)
    _Window = iso_duration(3_600, config=True)


class GrantExpiryConfig(BaseModel):
    """Settings for credential grant (agent binding) expiry."""

    idle_warning_interval: _Interval = Field(
        default="P1D",
        validate_default=True,
        description=(
            "How often the job runs that warns an agent's owner before a binding with the "
            "warning on ends (ISO 8601, e.g. P1D). Defaults to once a day. Keep the warning "
            "window at least twice this: a binding is only warned when a run finds its "
            "deadline inside the window, so with a longer interval a deadline can fall "
            "between two runs and pass without a warning."
        ),
    )
    idle_warning_window: _Window = Field(
        default="PT48H",
        validate_default=True,
        description=(
            "How long before a binding's end date or idle deadline its owner is warned "
            "(ISO 8601, e.g. PT48H), once per deadline. Defaults to 48 hours. Keep it at "
            "least twice idle_warning_interval, or a deadline can pass between two runs "
            "of the warning job unwarned."
        ),
    )
    idle_warning_min_length: _IdlePeriod = Field(
        default="P4D",
        validate_default=True,
        description=(
            "Shortest idle limit, or grant length before an end date, that is ever warned "
            "about (ISO 8601, e.g. P4D). Below this a warning would cover half the window "
            "or more and arrive for bindings doing exactly what they were set up to do. "
            "Defaults to 4 days."
        ),
    )

    default_expires_in: _Lifetime | None = Field(
        default=None,
        description=(
            "Default lifetime of a new credential grant whose creation sets no policy (ISO "
            "8601, e.g. P30D): it ends this long after it is granted. Unset (the open-source "
            "default): such a grant has no end date."
        ),
    )
    max_expires_in: _Lifetime | None = Field(
        default=None,
        description=(
            "Longest a credential grant may last from when it is granted or renewed (ISO "
            "8601, e.g. P90D). When set, every grant must end: a grant with no end date, or "
            "one further out, is refused (422), and a creation that sets no policy gets the "
            "default (or this maximum when no default is set)."
        ),
    )
    default_idle_period: _IdlePeriod | None = Field(
        default=None,
        description=(
            "Default idle limit of a new credential grant whose creation sets no policy (ISO "
            "8601, P1D to P999D, e.g. P14D). Unset: no idle limit."
        ),
    )
    max_idle_period: _IdlePeriod | None = Field(
        default=None,
        description=(
            "Longest idle limit a credential grant may carry (ISO 8601, P1D to P999D). When "
            "set, every grant must carry one: none, or a longer one, is refused (422)."
        ),
    )
    single_use_by_default: bool = Field(
        default=False,
        description=(
            "Whether a new credential grant whose creation sets no policy allows a single "
            "call (a use count of 1)."
        ),
    )

    @model_validator(mode="after")
    def _defaults_within_maximums(self) -> Self:
        for default, maximum in (
            ("default_expires_in", "max_expires_in"),
            ("default_idle_period", "max_idle_period"),
        ):
            low, high = getattr(self, default), getattr(self, maximum)
            if low is not None and high is not None and low > high:
                raise ValueError(f"grant_expiry.{default} must not exceed {maximum}")
        return self
