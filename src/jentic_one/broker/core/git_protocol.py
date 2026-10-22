"""Git smart-HTTP wire format for the governed git pipeline.

What the broker reads and writes of git's smart-HTTP protocol (``gitprotocol-http``,
``gitprotocol-pack``) when it governs a clone, fetch or push to github.com:

- the request URL (:func:`parse_git_url`): which repository and which of the
  three smart-HTTP endpoints;
- pkt-lines (:func:`pkt_line`, :class:`PktLineReader`);
- the head of a ``git-receive-pack`` request (:func:`parse_receive_pack_head`):
  optional ``shallow`` lines, the ref update commands (capabilities on the
  first), optional push options, then the pack;
- the ref namespaces a push may touch (:func:`ref_hard_deny_reason`);
- the ref advertisement, with ``refs/jentic/*`` and the ``push-cert``
  capability removed (:class:`AdvertisementFilter`);
- the report-status a refused push answers (:func:`denied_report_status`), on
  side-band 1 when the client asked for side-band, so git prints each reason.

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import BinaryIO
from urllib.parse import parse_qs, unquote, urlsplit

from jentic_one.shared.permissions.git_rules import (
    PUSHABLE_REF_PREFIXES,
    normalize_owner_name,
    normalize_repo_name,
)

#: The host whose git smart-HTTP the broker governs.
GIT_UPSTREAM_HOST = "github.com"

#: The all-zeros object name: the old value of a created ref, the new value of a deleted one.
ZERO_SHA = "0" * 40

#: A flush pkt-line.
FLUSH_PKT = b"0000"

#: The largest pkt-line payload (65520 bytes on the wire, minus the 4-byte length).
MAX_PKT_PAYLOAD = 65516

#: The largest payload of one ``side-band-64k`` pkt-line (the band byte takes one).
MAX_SIDEBAND_PAYLOAD = MAX_PKT_PAYLOAD - 1

#: The largest payload of one ``side-band`` pkt-line: that capability caps a
#: whole pkt-line at 1000 bytes.
MAX_SMALL_SIDEBAND_PAYLOAD = 1000 - 5

#: The ref namespace Jentic reserves: never pushed, never advertised.
JENTIC_REF_PREFIX = "refs/jentic/"

#: Capabilities the broker never advertises to the client.
WITHHELD_CAPABILITIES: frozenset[str] = frozenset({"push-cert"})

_WITHHELD: frozenset[bytes] = frozenset(c.encode() for c in WITHHELD_CAPABILITIES)
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


class GitService(StrEnum):
    """The two git services smart-HTTP serves."""

    UPLOAD_PACK = "git-upload-pack"
    RECEIVE_PACK = "git-receive-pack"


class GitEndpoint(StrEnum):
    """Which smart-HTTP endpoint a request is for."""

    INFO_REFS = "info/refs"
    RPC = "rpc"


@dataclass(frozen=True, slots=True)
class GitTarget:
    """A smart-HTTP request to one github.com repository.

    ``owner`` and ``repo`` are as the client spelled them (``repo`` without a
    ``.git`` suffix); ``rule_owner``/``rule_repo`` are the normalized names
    rules compare.
    """

    owner: str
    repo: str
    service: GitService
    endpoint: GitEndpoint

    @property
    def rule_owner(self) -> str:
        return normalize_owner_name(self.owner)

    @property
    def rule_repo(self) -> str:
        return normalize_repo_name(self.repo)

    @property
    def full_name(self) -> str:
        """``owner/repo``, as GitHub names the repository."""
        return f"{self.owner}/{self.repo}"

    @property
    def upstream_path(self) -> str:
        """The path of this endpoint on github.com (always the ``.git`` form)."""
        base = f"/{self.owner}/{self.repo}.git"
        if self.endpoint is GitEndpoint.INFO_REFS:
            return f"{base}/info/refs"
        return f"{base}/{self.service.value}"

    @property
    def upstream_query(self) -> str:
        """The query of this endpoint on github.com (``service=…`` on info/refs only)."""
        return f"service={self.service.value}" if self.endpoint is GitEndpoint.INFO_REFS else ""


def parse_git_url(url: str, method: str) -> GitTarget | None:
    """The github.com smart-HTTP request ``method url`` names, or ``None``.

    Accepts ``GET /<owner>/<repo>[.git]/info/refs?service=<service>`` and
    ``POST /<owner>/<repo>[.git]/<service>`` on ``https://github.com``
    (default port, no user info), for the two services; anything else
    (another host, path or method, a dumb-HTTP path, an encoded or dot
    segment) is ``None``.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    hostname = (parts.hostname or "").rstrip(".").lower()
    if (
        parts.scheme.lower() != "https"
        or hostname != GIT_UPSTREAM_HOST
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    segments = parts.path.split("/")
    if len(segments) not in (4, 5) or segments[0] != "":
        return None
    owner, repo = segments[1], segments[2]
    if (
        _NAME_RE.fullmatch(owner) is None
        or _NAME_RE.fullmatch(repo) is None
        or repo.lower() in (".git", ".", "..")
        or owner in (".", "..")
    ):
        return None
    repo = repo[:-4] if repo.lower().endswith(".git") else repo
    if not repo:
        return None
    rest = "/".join(segments[3:])
    method = method.upper()
    if rest == "info/refs" and method == "GET":
        services = parse_qs(parts.query, keep_blank_values=True).get("service", [])
        if len(services) != 1:
            return None
        try:
            service = GitService(services[0])
        except ValueError:
            return None
        return GitTarget(owner, repo, service, GitEndpoint.INFO_REFS)
    if method == "POST" and len(segments) == 4 and not parts.query:
        try:
            service = GitService(unquote(rest))
        except ValueError:
            return None
        if rest != service.value:
            return None
        return GitTarget(owner, repo, service, GitEndpoint.RPC)
    return None


def is_git_url(url: str) -> bool:
    """Whether ``url`` has the shape of a github.com smart-HTTP endpoint, for either method."""
    return parse_git_url(url, "GET") is not None or parse_git_url(url, "POST") is not None


# The third path segment of a git endpoint on a ``<owner>/<repo>`` URL without
# the ``.git`` suffix: smart HTTP and the dumb-HTTP files.
_GIT_ENDPOINT_SEGMENTS = frozenset(
    {"info", "git-upload-pack", "git-receive-pack", "HEAD", "objects"}
)


def is_github_git_path(url: str) -> bool:
    """Whether ``url`` is on github.com and names a git endpoint of a repository.

    Broader than :func:`parse_git_url`: any scheme or port, any method, smart
    or dumb HTTP, a ``<repo>.git`` path of any shape. The execute pipeline
    refuses every such URL, so git traffic to github.com only ever runs
    through the governed git pipeline, never under a REST path rule.
    """
    try:
        hostname = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if hostname != GIT_UPSTREAM_HOST:
        return False
    segments = urlsplit(url).path.split("/")
    if len(segments) < 3:
        return False
    if unquote(segments[2]).lower().endswith(".git"):
        return True
    return len(segments) >= 4 and unquote(segments[3]) in _GIT_ENDPOINT_SEGMENTS


def is_flush_only(body: bytes) -> bool:
    """Whether a request body is a lone flush pkt-line.

    git sends one as a probe before a push or fetch body too large to resend
    (``http.postBuffer``), to have authentication settled first.
    """
    return body == FLUSH_PKT


# ── pkt-lines ───────────────────────────────────────────────────────────────


class GitProtocolError(ValueError):
    """A git request the broker cannot read; its message is safe to show the client."""


def pkt_line(payload: bytes) -> bytes:
    """One pkt-line carrying ``payload``."""
    if len(payload) > MAX_PKT_PAYLOAD:
        raise ValueError("pkt-line payload too long")
    return f"{len(payload) + 4:04x}".encode("ascii") + payload


def sideband(band: int, payload: bytes, *, max_payload: int = MAX_SIDEBAND_PAYLOAD) -> bytes:
    """``payload`` on side-band ``band``, split into pkt-lines of at most ``max_payload`` bytes."""
    out = bytearray()
    for start in range(0, len(payload), max_payload):
        out += pkt_line(bytes([band]) + payload[start : start + max_payload])
    return bytes(out)


class PktLineReader:
    """Reads pkt-lines from a binary stream, tracking how many bytes it consumed."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self.consumed = 0

    def _read_exact(self, size: int) -> bytes:
        data = self._stream.read(size)
        if len(data) != size:
            raise GitProtocolError("the request ended inside a pkt-line")
        self.consumed += size
        return data

    def read(self) -> bytes | None:
        """The next pkt-line's payload, or ``None`` for a flush pkt-line."""
        header = self._read_exact(4)
        try:
            length = int(header, 16)
        except ValueError:
            raise GitProtocolError("malformed pkt-line length") from None
        if length == 0:
            return None
        if length < 4 or length > MAX_PKT_PAYLOAD + 4:
            raise GitProtocolError("malformed pkt-line length")
        return self._read_exact(length - 4)


# ── receive-pack request ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RefCommand:
    """One ref update of a push: ``old`` → ``new`` on ``ref``."""

    old: str
    new: str
    ref: str

    @property
    def is_create(self) -> bool:
        return self.old == ZERO_SHA

    @property
    def is_delete(self) -> bool:
        return self.new == ZERO_SHA


@dataclass(frozen=True, slots=True)
class ReceivePackHead:
    """The part of a ``git-receive-pack`` request before the pack.

    ``pack_offset`` is where the pack starts in the request body (its end
    when the push sends no pack, as a delete-only push does).
    """

    commands: tuple[RefCommand, ...]
    capabilities: frozenset[str]
    shallow: tuple[str, ...] = ()
    push_options: tuple[str, ...] = ()
    pack_offset: int = 0

    @property
    def side_band(self) -> bool:
        return bool({"side-band-64k", "side-band"} & self.capabilities)

    @property
    def side_band_payload(self) -> int:
        """The largest side-band payload the client accepts in one pkt-line."""
        if "side-band-64k" in self.capabilities:
            return MAX_SIDEBAND_PAYLOAD
        return MAX_SMALL_SIDEBAND_PAYLOAD

    @property
    def report_status(self) -> bool:
        return bool({"report-status", "report-status-v2"} & self.capabilities)

    @property
    def atomic(self) -> bool:
        return "atomic" in self.capabilities

    @property
    def needs_pack(self) -> bool:
        """Whether git sends a pack: every push but a delete-only one does."""
        return any(not c.is_delete for c in self.commands)


def _text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8").removesuffix("\n")
    except UnicodeDecodeError:
        raise GitProtocolError("a pkt-line is not valid UTF-8") from None


def parse_receive_pack_head(stream: BinaryIO, *, max_commands: int = 1000) -> ReceivePackHead:
    """Read the commands of a ``git-receive-pack`` request from ``stream``.

    Leaves ``stream`` positioned at the pack. Raises
    :class:`GitProtocolError` for anything git does not send: a malformed
    command or object name, a signed push (``push-cert``, never advertised),
    no command, or more than ``max_commands`` commands.
    """
    reader = PktLineReader(stream)
    shallow: list[str] = []
    commands: list[RefCommand] = []
    capabilities: frozenset[str] = frozenset()
    while True:
        payload = reader.read()
        if payload is None:
            break
        line = _text(payload)
        if line.startswith("shallow "):
            if commands:
                raise GitProtocolError("a shallow line follows the ref commands")
            sha = line[len("shallow ") :]
            if _SHA_RE.fullmatch(sha) is None:
                raise GitProtocolError("malformed shallow line")
            shallow.append(sha)
            continue
        if line.startswith("push-cert"):
            raise GitProtocolError("signed pushes are not supported through Jentic")
        if not commands:
            line, _, caps = line.partition("\0")
            capabilities = frozenset(caps.split())
        elif "\0" in line:
            raise GitProtocolError("capabilities on a later ref command")
        old, new, ref = [*line.split(" ", 2), "", ""][:3]
        if _SHA_RE.fullmatch(old) is None or _SHA_RE.fullmatch(new) is None or not ref:
            raise GitProtocolError("malformed ref command")
        commands.append(RefCommand(old=old, new=new, ref=ref))
        if len(commands) > max_commands:
            raise GitProtocolError(f"more than {max_commands} ref updates in one push")
    if not commands:
        raise GitProtocolError("the push has no ref commands")
    object_format = next(
        (c.partition("=")[2] for c in capabilities if c.startswith("object-format=")), "sha1"
    )
    if object_format != "sha1":
        raise GitProtocolError(f"the object format {object_format!r} is not supported")
    push_options: list[str] = []
    if "push-options" in capabilities:
        while True:
            payload = reader.read()
            if payload is None:
                break
            push_options.append(_text(payload))
    return ReceivePackHead(
        commands=tuple(commands),
        capabilities=capabilities,
        shallow=tuple(shallow),
        push_options=tuple(push_options),
        pack_offset=reader.consumed,
    )


# ── refs ────────────────────────────────────────────────────────────────────

_FORBIDDEN_REF_CHARS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def is_valid_ref_name(ref: str) -> bool:
    """Whether ``ref`` is a well-formed full ref name (``git check-ref-format`` rules)."""
    if not ref.startswith("refs/") or ref.endswith("/") or ref.endswith("."):
        return False
    if _FORBIDDEN_REF_CHARS.search(ref) or ".." in ref or "@{" in ref or "//" in ref:
        return False
    return all(
        component and not component.startswith(".") and not component.endswith(".lock")
        for component in ref.split("/")
    )


def ref_hard_deny_reason(ref: str) -> str | None:
    """Why no rule can let a push update ``ref``, or ``None``.

    Only well-formed branch and tag refs may be pushed; ``refs/jentic/*`` is
    reserved for Jentic.
    """
    if not is_valid_ref_name(ref):
        return "the ref name is not a valid git ref"
    if ref.startswith(JENTIC_REF_PREFIX):
        return "refs/jentic/ is reserved for Jentic"
    if not ref.startswith(PUSHABLE_REF_PREFIXES):
        return "only branches (refs/heads/) and tags (refs/tags/) can be pushed through Jentic"
    return None


def is_tag(ref: str) -> bool:
    return ref.startswith("refs/tags/")


# ── report-status ───────────────────────────────────────────────────────────


def _one_line(text: str) -> str:
    return " ".join(text.split())


def denied_report_status(
    head: ReceivePackHead,
    reasons: Mapping[str, str],
    *,
    others_reason: str,
    progress: Sequence[str] = (),
) -> bytes:
    """The report-status of a push the broker refuses without forwarding it.

    ``unpack ok``, then ``ng <ref> jentic: <reason>`` for every command: the
    denied refs with their own ``reasons``, every other ref with
    ``others_reason`` (nothing in the push moved). With side-band the report
    goes on band 1 and each ``progress`` line on band 2 first, which git
    prints as ``remote: …``.
    """
    report = bytearray(pkt_line(b"unpack ok\n"))
    for command in head.commands:
        reason = reasons.get(command.ref, others_reason)
        line = f"ng {command.ref} jentic: {_one_line(reason)}\n"
        report += pkt_line(line.encode("utf-8")[: MAX_PKT_PAYLOAD - 1])
    report += FLUSH_PKT
    if not head.side_band:
        return bytes(report)
    size = head.side_band_payload
    out = bytearray()
    for message in progress:
        out += sideband(2, f"{_one_line(message)}\n".encode(), max_payload=size)
    out += sideband(1, bytes(report), max_payload=size)
    out += FLUSH_PKT
    return bytes(out)


# ── advertisement ───────────────────────────────────────────────────────────


@dataclass
class AdvertisementFilter:
    """Filters an ``info/refs`` ref advertisement as it streams through.

    Drops every ``refs/jentic/*`` ref (and its peeled ``^{}`` line) and the
    :data:`WITHHELD_CAPABILITIES`. The capabilities ride on the first ref
    line, so when that line is dropped they move to the next one kept, or to
    the ``capabilities^{}`` placeholder git expects when no ref is left. The
    ``# service=`` preamble, ``version`` lines and a protocol v2 capability
    advertisement (which carries no refs) pass through unchanged.

    :meth:`feed` takes the upstream body in chunks and returns the filtered
    bytes ready so far; :meth:`close` the rest. A body that is not a pkt-line
    stream raises :class:`GitProtocolError`.
    """

    _buffer: bytearray = field(default_factory=bytearray)
    _flushes: int = 0
    _preamble: bool = True
    _caps: bytes | None = None
    _caps_sent: bool = False
    _passthrough: bool = False

    def feed(self, chunk: bytes) -> bytes:
        if self._passthrough:
            return chunk
        self._buffer += chunk
        out = bytearray()
        while len(self._buffer) >= 4 and not self._passthrough:
            try:
                length = int(self._buffer[:4], 16)
            except ValueError:
                raise GitProtocolError("the ref advertisement is not a pkt-line stream") from None
            if length == 0:
                del self._buffer[:4]
                out += self._flush()
                continue
            if length < 4:
                raise GitProtocolError("the ref advertisement is not a pkt-line stream")
            if len(self._buffer) < length:
                break
            payload = bytes(self._buffer[4:length])
            del self._buffer[:length]
            out += self._line(payload)
        if self._passthrough:
            out += self._buffer
            self._buffer.clear()
        return bytes(out)

    def close(self) -> bytes:
        rest = bytes(self._buffer)
        self._buffer.clear()
        if rest and not self._passthrough:
            raise GitProtocolError("the ref advertisement ended inside a pkt-line")
        return rest

    def _flush(self) -> bytes:
        self._flushes += 1
        if self._preamble and self._flushes == 1:
            self._preamble = False
            return FLUSH_PKT
        out = b""
        if not self._caps_sent and self._caps is not None:
            out = pkt_line(f"{ZERO_SHA} capabilities^{{}}\0".encode() + self._caps + b"\n")
            self._caps_sent = True
        self._passthrough = True
        return out + FLUSH_PKT

    def _line(self, payload: bytes) -> bytes:
        if self._preamble and payload.startswith(b"# service="):
            return pkt_line(payload)
        self._preamble = False
        if payload.startswith(b"version "):
            if payload.strip() == b"version 2":
                self._passthrough = True
            return pkt_line(payload)
        body = payload.removesuffix(b"\n")
        if self._caps is None and not self._caps_sent:
            line, _, caps = body.partition(b"\0")
            kept = [c for c in caps.split() if c.split(b"=", 1)[0] not in _WITHHELD]
            self._caps = b" ".join(kept)
            body = line
        _sha, _, ref = body.partition(b" ")
        if ref.startswith(JENTIC_REF_PREFIX.encode()):
            return b""
        return self._with_caps(body)

    def _with_caps(self, body: bytes) -> bytes:
        if not self._caps_sent:
            self._caps_sent = True
            return pkt_line(body + b"\0" + (self._caps or b"") + b"\n")
        return pkt_line(body + b"\n")


def error_text(message: str) -> bytes:
    """A plain-text error body git prints as ``remote: <line>`` on an HTTP error."""
    return f"{_one_line(message)}\n".encode()


__all__ = [
    "FLUSH_PKT",
    "GIT_UPSTREAM_HOST",
    "JENTIC_REF_PREFIX",
    "MAX_SIDEBAND_PAYLOAD",
    "MAX_SMALL_SIDEBAND_PAYLOAD",
    "WITHHELD_CAPABILITIES",
    "ZERO_SHA",
    "AdvertisementFilter",
    "GitEndpoint",
    "GitProtocolError",
    "GitService",
    "GitTarget",
    "PktLineReader",
    "ReceivePackHead",
    "RefCommand",
    "denied_report_status",
    "error_text",
    "is_flush_only",
    "is_git_url",
    "is_github_git_path",
    "is_tag",
    "is_valid_ref_name",
    "parse_git_url",
    "parse_receive_pack_head",
    "pkt_line",
    "ref_hard_deny_reason",
    "sideband",
]
