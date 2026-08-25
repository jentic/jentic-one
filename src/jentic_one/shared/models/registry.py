"""Registry-related enums shared across modules."""

from enum import StrEnum


class ApiRevisionState(StrEnum):
    """Lifecycle state of an API revision."""

    DRAFT = "draft"
    PUBLISHED = "published"
    IMPORTED = "imported"
    ARCHIVED = "archived"


class ApiRevisionSourceType(StrEnum):
    """Source type for an API revision spec."""

    URL = "url"
    INLINE = "inline"
    UNKNOWN = "unknown"


class OverlayStatus(StrEnum):
    """Lifecycle status of an overlay."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"


class OverlayDeprecationReason(StrEnum):
    """Why an overlay transitioned to DEPRECATED — the *cause* behind the terminal state.

    Persisted alongside ``deprecated_at`` at each of the three deprecation sites so
    clients can label the historical event durably ("rolled back" vs "deprecated" vs
    "superseded by re-import") instead of re-deriving the verb from the API's *current*
    revision pointer, which keeps moving after the fact. Stored as a free-form string
    column (like ``ApiRevision.origin``); NULL on rows deprecated before this field
    existed — treat NULL as "unknown cause".
    """

    #: An operator explicitly deprecated the overlay (DELETE endpoint). Says nothing
    #: about the served spec — a materialized overlay's revision may still be current.
    MANUAL = "manual"
    #: An operator rolled the overlay back (A5b): its revision was archived and the
    #: superseded revision restored to current.
    ROLLBACK = "rollback"
    #: An authorized catalog re-import superseded the overlay's materialized revision
    #: (A4b) and auto-deprecated it.
    SUPERSEDED_BY_REIMPORT = "superseded_by_reimport"


class RevisionOrigin(StrEnum):
    """Provenance of an API revision's spec — where the served bytes came from.

    ``origin`` is persisted as a free-form string on the revision row (older rows may
    carry values outside this enum, e.g. ``"imported"``), so treat this as the set of
    values the *current* code produces and branches on — not a closed DB constraint.
    Compare with ``==``/``in`` against ``revision.origin`` directly: as a ``StrEnum``
    each member equals its string value, so existing string comparisons keep working.

    Two members are load-bearing today:

    - :attr:`OVERLAY` — ``CreateRevisionStage`` archives *all* active revisions (not just
      the same-origin one) so the overlaid revision supersedes whatever is served.
    - :attr:`CATALOG` — the Flow-3 update-notify scanner treats a catalog-origin revision
      (and an overlay-origin one whose ``source_url`` still points at a catalog spec) as an
      upstream-tracked candidate.

    :attr:`UPLOADED` reserves the name for a user-supplied spec (direct upload / paste),
    which is neither upstream-tracked nor overlay-managed; add branches for it as those
    paths land.
    """

    OVERLAY = "overlay"
    CATALOG = "catalog"
    UPLOADED = "uploaded"


#: Revision ``origin`` marker for a spec produced by materializing a confirmed overlay.
#: Alias of :attr:`RevisionOrigin.OVERLAY`; kept so the service that enqueues the
#: materialize job and the stage that consumes it share one symbol.
ORIGIN_OVERLAY = RevisionOrigin.OVERLAY

#: Revision ``origin`` marker for a spec imported from a public-catalog entry. Alias of
#: :attr:`RevisionOrigin.CATALOG`; kept so the importer and the scanner classifier agree.
ORIGIN_CATALOG = RevisionOrigin.CATALOG
