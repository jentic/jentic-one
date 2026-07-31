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


#: Revision ``origin`` marker for a spec produced by materializing a confirmed
#: overlay. ``origin`` is otherwise a free-form provenance string (e.g. ``"catalog"``,
#: ``"imported"``), but this value is load-bearing: ``CreateRevisionStage`` uses it to
#: archive *all* active revisions (not just the same-origin one) so the overlaid
#: revision supersedes whatever is currently served. Keep the service that enqueues
#: the materialize job and the stage that consumes it in sync via this constant.
ORIGIN_OVERLAY = "overlay"

#: Revision ``origin`` marker for a spec imported from a public-catalog entry. Set by
#: ``CatalogService`` when it builds the import source, and load-bearing for Flow-3: the
#: update-notify scanner treats a ``"catalog"``-origin revision (and an overlay-origin one
#: whose ``source_url`` still points at a catalog spec) as an upstream-tracked candidate.
#: Kept as a shared constant so the importer and the scanner classifier agree.
ORIGIN_CATALOG = "catalog"
