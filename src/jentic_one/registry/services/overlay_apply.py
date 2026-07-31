"""Apply an OpenAPI Overlay 1.0 document onto a base spec (remove-then-set subset).

This is the server-side counterpart to the ``contribute-spec-fix`` skill's tested
applier. It supports the JSONPath subset those overlays actually use — ``$`` (the
document root), ``.key`` and ``['key']`` object-key segments — and the two Overlay
actions ``remove`` and ``update``. Anything outside that subset (array-index or
filter targets, a ``target`` that resolves to nothing) raises ``OverlayApplyError``
rather than silently no-op'ing, so a materialize-on-confirm surfaces the conflict
instead of shipping a spec the overlay did not actually change.

The registry stores specs as parsed JSON (JSONB), so — unlike the on-disk skill
applier — key ordering and formatting are irrelevant here; we only compute the
resulting object.

Intentional deviations from the Overlay 1.0.0 spec (deliberate for materialize-on-
confirm; not general Overlay compliance):

* **Single-node targets only.** The spec allows ``target`` to select a *nodelist*
  (e.g. wildcards like ``$.paths.*.get``) and defines object ``update`` as a
  *recursive* merge. This applier resolves exactly one node and does a shallow
  one-level ``dict.update`` (full replacement of nested values). Multi-match and
  recursive-merge targets are **rejected** (``OverlayApplyError``), never attempted
  — a hard error, never a silent mis-apply. The skill only emits single-node,
  remove-then-set overlays, for which shallow replacement is the safer, deterministic
  choice. If overlays ever need the full JSONPath surface, swap this resolver for a
  real JSONPath engine (jsonpath-ng) rather than growing the regex.
* **A zero-match target is a hard error, not a no-op.** Overlay 1.1 clarifies that a
  target selecting zero nodes succeeds unchanged; here it raises. That is deliberate:
  an overlay authored against a drifted base should be rejected so confirm surfaces
  the conflict, not silently ship a spec the overlay did not change.
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: A JSONPath segment: either ``.key`` or ``['key']``. The full target is ``$``
#: followed by zero or more of these. Anything else (``[0]``, ``[?(...)]``, ``..``)
#: is unsupported and rejected.
_SEGMENT = re.compile(r"\.([A-Za-z0-9_]+)|\['([^']*)'\]")


class OverlayApplyError(Exception):
    """Raised when an overlay cannot be applied cleanly to the base spec."""


def _resolve(root: Any, target: str) -> tuple[Any, str | None, Any]:
    """Resolve a JSONPath ``target`` to ``(parent, key, node)``.

    ``parent``/``key`` are ``None`` when ``target`` is the root ``$``. Raises
    ``OverlayApplyError`` for an unsupported expression or a path that does not
    resolve against ``root``.
    """
    if not isinstance(target, str) or not target.startswith("$"):
        raise OverlayApplyError(f"unsupported overlay target (must start with '$'): {target!r}")
    if target == "$":
        return None, None, root

    rest = target[1:]
    # Validate the whole tail is only supported segments — reject array indices,
    # filters, recursive descent, etc. by checking the concatenation round-trips.
    matches = list(_SEGMENT.finditer(rest))
    if not matches or "".join(m.group(0) for m in matches) != rest:
        raise OverlayApplyError(f"unsupported overlay target expression: {target!r}")

    node: Any = root
    parent: Any = None
    key: str | None = None
    for m in matches:
        seg_key = m.group(1) if m.group(1) is not None else m.group(2)
        if not isinstance(node, dict) or seg_key not in node:
            raise OverlayApplyError(f"overlay target does not resolve: {target!r}")
        parent, key, node = node, seg_key, node[seg_key]
    return parent, key, node


def apply_overlay(base: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Return a new spec with the overlay ``document``'s actions applied to ``base``.

    ``base`` is never mutated. Each action must have a ``target`` and either
    ``remove: true`` or an ``update`` object. Raises ``OverlayApplyError`` on a
    malformed document, an unsupported/unresolvable target, or an action that is
    neither a remove nor an update.
    """
    actions = document.get("actions")
    if not isinstance(actions, list):
        raise OverlayApplyError("overlay document has no 'actions' list")

    spec = copy.deepcopy(base)
    for idx, action in enumerate(actions):
        if not isinstance(action, dict) or "target" not in action:
            raise OverlayApplyError(f"overlay action {idx} missing 'target'")
        parent, key, node = _resolve(spec, action["target"])

        if action.get("remove"):
            if parent is None:
                raise OverlayApplyError(f"overlay action {idx}: cannot remove the document root")
            del parent[key]
        elif "update" in action:
            upd = action["update"]
            if parent is None:  # target "$": merge each key onto the root
                if not isinstance(upd, dict):
                    raise OverlayApplyError(f"overlay action {idx}: root update must be an object")
                for k, v in upd.items():
                    node[k] = v
            elif isinstance(node, dict) and isinstance(upd, dict):
                node.update(upd)
            else:
                parent[key] = upd
        else:
            raise OverlayApplyError(f"overlay action {idx} is neither a 'remove' nor an 'update'")

    return spec
