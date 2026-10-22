"""Vendor CLI facades available to the caller (``GET /cli-facades``)."""

from jentic_one.control.services.cli_facades.service import CliFacadeService, CliFacadeView

__all__ = ["CliFacadeService", "CliFacadeView"]
