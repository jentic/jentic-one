"""Runtime dependencies that jentic_one declares but never imports directly.

``sniffio`` is one: httpcore's ``current_async_library()`` attempts
``import sniffio`` on every lock/event/shield it constructs (several per
outbound request). Failed imports are not cached, so if sniffio is missing each
call walks the finder chain and stats every ``sys.path`` entry. Because nothing
in ``src/`` imports it, it looks unused — these tests stop it being dropped.
"""

import importlib.util
import re
from importlib.metadata import requires

# PEP 508: the distribution name is the leading run of name characters.
_REQ_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _unconditional_requirement_names() -> set[str]:
    names = set()
    for req in requires("jentic-one") or []:
        if ";" in req:  # extras / environment markers — not a base install dep
            continue
        match = _REQ_NAME.match(req.strip())
        if match:
            names.add(match.group(0).lower())
    return names


def test_sniffio_is_declared_runtime_dependency() -> None:
    assert "sniffio" in _unconditional_requirement_names()


def test_sniffio_is_importable() -> None:
    assert importlib.util.find_spec("sniffio") is not None
