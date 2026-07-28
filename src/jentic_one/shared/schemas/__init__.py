"""Shared Pydantic schemas used across service and web layers."""

from jentic_one.shared.schemas.api_reference import APIReference, APIReferenceRequest, ServedApiRef

__all__ = [
    "APIReference",
    "APIReferenceRequest",
    "ServedApiRef",
]
