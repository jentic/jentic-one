"""Pipeline context — typed data bag for passing values between stages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jentic_one.registry.ingest._typecheck import check_type
from jentic_one.registry.ingest.exc import (
    MissingProducedKeyError,
    MissingRequiredKeysError,
    WrongTypeProducedError,
    WrongTypeRequiredError,
)
from jentic_one.registry.ingest.models import IngestSpecification

if TYPE_CHECKING:
    from jentic_one.shared.config import AppConfig


class PipelineContext:
    """Carries session, specification, and a typed data bag between pipeline stages."""

    def __init__(
        self,
        *,
        session: Any,
        specification: IngestSpecification,
        created_by: str,
        config: AppConfig | None = None,
    ) -> None:
        self.session: Any = session
        self.specification = specification
        self.created_by = created_by
        #: The loaded AppConfig, when the caller provides it (the ingestor
        #: does). Registered extension stages read their own config section via
        #: ``ctx.config.extension("<name>")`` to gate themselves — built-in
        #: stages must keep taking feature flags explicitly (like
        #: include_search_text) rather than growing implicit config reads.
        self.config: AppConfig | None = config
        self._data: dict[str, Any] = {}

    def produce(self, key: str, value: Any, expected_type: type) -> None:
        """Store a value in the data bag, validating its type."""
        if not check_type(value, expected_type):
            actual = type(value).__name__
            msg = f"Cannot produce '{key}': expected {expected_type.__name__}, got {actual}"
            raise TypeError(msg)
        self._data[key] = value

    def require(self, key: str, expected_type: type) -> Any:
        """Retrieve a value from the data bag, raising if absent or wrong type."""
        if key not in self._data:
            raise MissingRequiredKeysError({key}, stage=None)
        value = self._data[key]
        if not check_type(value, expected_type):
            raise WrongTypeRequiredError(
                key=key,
                expected_type=expected_type,
                actual_type=type(value),
                stage=None,
            )
        return value

    def ensure_requires(self, keys: dict[str, type], stage: Any) -> None:
        """Check all required keys are present with correct types."""
        missing: set[str] = set()
        for key in keys:
            if key not in self._data:
                missing.add(key)
        if missing:
            raise MissingRequiredKeysError(missing, stage=stage)
        for key, expected_type in keys.items():
            value = self._data[key]
            if not check_type(value, expected_type):
                raise WrongTypeRequiredError(
                    key=key,
                    expected_type=expected_type,
                    actual_type=type(value),
                    stage=stage,
                )

    def ensure_produces(self, keys: dict[str, type], stage: Any) -> None:
        """Check all expected produced keys are present with correct types."""
        for key, expected_type in keys.items():
            if key not in self._data:
                raise MissingProducedKeyError(key, stage=stage)
            value = self._data[key]
            if not check_type(value, expected_type):
                raise WrongTypeProducedError(
                    key=key,
                    expected_type=expected_type,
                    actual_type=type(value),
                    stage=stage,
                )

    def get(self, key: str, default: Any = None) -> Any:
        """Non-raising dict-like access to the data bag."""
        return self._data.get(key, default)
