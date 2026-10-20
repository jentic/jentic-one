"""Lists the vendor CLI facades this deployment serves and where agents reach them.

A CLI wrapper (``jentic cf``, ``jentic cli list``) reads this instead of
hard-coding an address: the facade address is operator config
(``cli_facades``), and in ``path`` mode it hangs off the broker's own address,
which only the deployment knows.

Config-only: nothing here reads a database, so there is nothing to scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jentic_one.shared.cli_facades import SERVED_FACADE_CLIS, facade_path_prefix
from jentic_one.shared.config import CLI_FACADE_VENDORS
from jentic_one.shared.context import Context
from jentic_one.shared.web.instance_identity import advertised_broker_url


@dataclass(frozen=True, slots=True)
class CliFacadeView:
    """One served facade: the vendor, its CLI, the addressing mode and the address."""

    vendor: str
    cli: str
    mode: Literal["host", "path"]
    address: str | None


class CliFacadeService:
    """Reads ``cli_facades`` into the facades a vendor CLI can be pointed at."""

    def __init__(self, ctx: Context) -> None:
        self._config = ctx.config

    def list_served(self) -> list[CliFacadeView]:
        """Every facade that is switched on and implemented by the broker.

        ``address`` is the vendor's ``public_address`` when set. Otherwise it
        is ``https://<host>`` in ``host`` mode, and in ``path`` mode the
        broker's advertised address plus the facade prefix; ``None`` when the
        deployment cannot honestly advertise a broker address (a ``remote``
        backend whose broker URL is loopback).
        """
        facades = self._config.cli_facades
        broker_url = advertised_broker_url(self._config)
        views: list[CliFacadeView] = []
        for vendor in CLI_FACADE_VENDORS:
            cli = SERVED_FACADE_CLIS.get(vendor)
            settings = facades.vendors.get(vendor)
            if cli is None or settings is None or not facades.vendor_enabled(vendor):
                continue
            address: str | None
            if settings.public_address:
                address = settings.public_address.rstrip("/")
            elif settings.mode == "host":
                address = f"https://{settings.host}" if settings.host else None
            elif broker_url:
                address = broker_url.rstrip("/") + facade_path_prefix(vendor)
            else:
                address = None
            views.append(CliFacadeView(vendor=vendor, cli=cli, mode=settings.mode, address=address))
        return views
