"""git smart-HTTP wire format: URLs, pkt-lines, receive-pack heads, refs, report-status, adverts."""

from __future__ import annotations

import io

import pytest

from jentic_one.broker.core.git_protocol import (
    FLUSH_PKT,
    MAX_SMALL_SIDEBAND_PAYLOAD,
    ZERO_SHA,
    AdvertisementFilter,
    GitEndpoint,
    GitProtocolError,
    GitService,
    PktLineReader,
    ReceivePackHead,
    denied_report_status,
    is_flush_only,
    is_github_git_path,
    parse_git_url,
    parse_receive_pack_head,
    pkt_line,
    ref_hard_deny_reason,
    sideband,
)

_A = "a" * 40
_B = "b" * 40
_C = "c" * 40


def _head(*lines: bytes, flush: bool = True) -> bytes:
    return b"".join(pkt_line(line) for line in lines) + (FLUSH_PKT if flush else b"")


def _pkts(data: bytes) -> list[bytes | None]:
    reader = PktLineReader(io.BytesIO(data))
    out: list[bytes | None] = []
    while reader.consumed < len(data):
        out.append(reader.read())
    return out


# ── URLs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "url", "service", "endpoint", "repo"),
    [
        (
            "GET",
            "https://github.com/octo/app.git/info/refs?service=git-upload-pack",
            GitService.UPLOAD_PACK,
            GitEndpoint.INFO_REFS,
            "app",
        ),
        (
            "GET",
            "https://github.com/octo/app/info/refs?service=git-receive-pack",
            GitService.RECEIVE_PACK,
            GitEndpoint.INFO_REFS,
            "app",
        ),
        (
            "POST",
            "https://github.com/octo/app.git/git-upload-pack",
            GitService.UPLOAD_PACK,
            GitEndpoint.RPC,
            "app",
        ),
        (
            "POST",
            "https://GitHub.com:443/octo/App/git-receive-pack",
            GitService.RECEIVE_PACK,
            GitEndpoint.RPC,
            "App",
        ),
    ],
)
def test_smart_http_urls_parse(
    method: str, url: str, service: GitService, endpoint: GitEndpoint, repo: str
) -> None:
    target = parse_git_url(url, method)
    assert target is not None
    assert (target.owner, target.repo, target.service, target.endpoint) == (
        "octo",
        repo,
        service,
        endpoint,
    )
    assert target.upstream_path.startswith(f"/octo/{repo}.git/")
    assert target.rule_repo == repo.lower()


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "https://api.github.com/octo/app.git/info/refs?service=git-upload-pack"),
        ("GET", "http://github.com/octo/app.git/info/refs?service=git-upload-pack"),
        ("GET", "https://github.com:8443/octo/app.git/info/refs?service=git-upload-pack"),
        ("GET", "https://user@github.com/octo/app.git/info/refs?service=git-upload-pack"),
        ("GET", "https://github.com/octo/app.git/info/refs"),
        ("GET", "https://github.com/octo/app.git/info/refs?service=git-shell"),
        ("GET", "https://github.com/octo/app.git/info/refs?service=a&service=b"),
        ("POST", "https://github.com/octo/app.git/info/refs?service=git-upload-pack"),
        ("GET", "https://github.com/octo/app.git/git-upload-pack"),
        ("POST", "https://github.com/octo/app.git/git%2Dupload-pack"),
        ("POST", "https://github.com/octo/app.git/git-upload-pack?x=1"),
        ("GET", "https://github.com/octo/app.git/HEAD"),
        ("POST", "https://github.com/../app.git/git-upload-pack"),
        ("POST", "https://github.com/octo/.git/git-upload-pack"),
        ("POST", "https://github.com/octo/a%2Fb.git/git-upload-pack"),
    ],
)
def test_other_urls_are_not_smart_http(method: str, url: str) -> None:
    assert parse_git_url(url, method) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/octo/app.git/info/refs?service=git-upload-pack", True),
        ("https://github.com/octo/app.git/HEAD", True),
        ("https://github.com/octo/app/objects/info/packs", True),
        ("http://github.com:8080/octo/app/git-receive-pack", True),
        ("https://github.com/octo/app/pulls", False),
        ("https://api.github.com/repos/octo/app.git", False),
        ("https://github.com/octo", False),
    ],
)
def test_github_git_paths(url: str, expected: bool) -> None:
    assert is_github_git_path(url) is expected


# ── pkt-lines and the receive-pack head ──────────────────────────────────────


def test_pkt_line_round_trip_and_flush() -> None:
    data = pkt_line(b"hello\n") + FLUSH_PKT
    assert data == b"000ahello\n0000"
    assert _pkts(data) == [b"hello\n", None]
    assert is_flush_only(FLUSH_PKT)
    assert not is_flush_only(data)


@pytest.mark.parametrize("data", [b"00", b"zzzz", b"0003", b"0009abc"])
def test_malformed_pkt_lines_are_refused(data: bytes) -> None:
    with pytest.raises(GitProtocolError):
        PktLineReader(io.BytesIO(data)).read()


def test_head_with_shallow_lines_capabilities_and_push_options() -> None:
    body = (
        _head(
            f"shallow {_C}".encode(),
            f"{_A} {_B} refs/heads/main\0report-status side-band-64k atomic push-options "
            "agent=git/2.49\n".encode(),
            f"{ZERO_SHA} {_B} refs/heads/new\n".encode(),
            f"{_A} {ZERO_SHA} refs/heads/gone\n".encode(),
        )
        + _head(b"ci.skip")
        + b"PACK..."
    )
    stream = io.BytesIO(body)

    head = parse_receive_pack_head(stream)

    assert head.shallow == (_C,)
    assert [c.ref for c in head.commands] == [
        "refs/heads/main",
        "refs/heads/new",
        "refs/heads/gone",
    ]
    assert head.commands[1].is_create and head.commands[2].is_delete
    assert head.side_band and head.report_status and head.atomic
    assert head.push_options == ("ci.skip",)
    assert head.needs_pack
    assert stream.read() == b"PACK..."
    assert head.pack_offset == len(body) - len(b"PACK...")


def test_a_delete_only_push_needs_no_pack() -> None:
    head = parse_receive_pack_head(
        io.BytesIO(_head(f"{_A} {ZERO_SHA} refs/heads/gone\0report-status\n".encode()))
    )
    assert not head.needs_pack


@pytest.mark.parametrize(
    ("lines", "fragment"),
    [
        ((), "no ref commands"),
        ((b"garbage\n",), "malformed ref command"),
        ((f"{_A} {_B}\n".encode(),), "malformed ref command"),
        ((f"{_A} {_B} refs/heads/a\n".encode(), f"shallow {_C}".encode()), "shallow line"),
        ((f"{_A} {_B} refs/heads/a\n".encode(), f"{_A} {_B} refs/heads/b\0x\n".encode()), "later"),
        ((b"push-cert\0report-status\n",), "signed pushes"),
        ((f"{_A} {_B} refs/heads/a\0object-format=sha256\n".encode(),), "object format"),
        ((b"shallow nothex",), "malformed shallow"),
    ],
)
def test_malformed_heads_are_refused(lines: tuple[bytes, ...], fragment: str) -> None:
    with pytest.raises(GitProtocolError, match=fragment):
        parse_receive_pack_head(io.BytesIO(_head(*lines)))


def test_too_many_ref_updates_are_refused() -> None:
    lines = [f"{_A} {_B} refs/heads/b{i}\n".encode() for i in range(3)]
    with pytest.raises(GitProtocolError, match="more than 2"):
        parse_receive_pack_head(io.BytesIO(_head(*lines)), max_commands=2)


# ── refs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "fragment"),
    [
        ("refs/heads/feature/x", None),
        ("refs/tags/v1.2", None),
        ("refs/jentic/q/1", "reserved for Jentic"),
        ("refs/notes/commits", "only branches"),
        ("refs/pull/1/head", "only branches"),
        ("HEAD", "not a valid git ref"),
        ("refs/heads/a..b", "not a valid git ref"),
        ("refs/heads/x.lock", "not a valid git ref"),
        ("refs/heads/a b", "not a valid git ref"),
        ("refs/heads/", "not a valid git ref"),
    ],
)
def test_ref_namespace_limits(ref: str, fragment: str | None) -> None:
    reason = ref_hard_deny_reason(ref)
    if fragment is None:
        assert reason is None
    else:
        assert reason is not None and fragment in reason


# ── report-status ────────────────────────────────────────────────────────────


def _parse_head(caps: str, *refs: str) -> ReceivePackHead:
    lines = [f"{_A} {_B} {refs[0]}\0{caps}\n".encode()]
    lines += [f"{_A} {_B} {ref}\n".encode() for ref in refs[1:]]
    return parse_receive_pack_head(io.BytesIO(_head(*lines)))


def test_denied_report_status_without_side_band() -> None:
    head = _parse_head("report-status", "refs/heads/main", "refs/heads/ok")

    report = denied_report_status(
        head,
        {"refs/heads/main": "pushing to\nmain is not allowed"},
        others_reason="not applied",
    )

    assert _pkts(report) == [
        b"unpack ok\n",
        b"ng refs/heads/main jentic: pushing to main is not allowed\n",
        b"ng refs/heads/ok jentic: not applied\n",
        None,
    ]


def test_denied_report_status_on_side_band_with_progress() -> None:
    head = _parse_head("report-status-v2 side-band-64k", "refs/heads/main")

    report = denied_report_status(
        head,
        {"refs/heads/main": "nope"},
        others_reason="",
        progress=["Jentic refused this push"],
    )

    outer = _pkts(report)
    assert outer[-1] is None
    progress, *bands = outer[:-1]
    assert progress == b"\x02Jentic refused this push\n"
    inner = b"".join(band[1:] for band in bands if band is not None)
    assert all(band is not None and band[:1] == b"\x01" for band in bands)
    assert _pkts(inner) == [b"unpack ok\n", b"ng refs/heads/main jentic: nope\n", None]


def test_small_side_band_splits_into_1000_byte_packets() -> None:
    head = _parse_head("report-status side-band", "refs/heads/main")

    report = denied_report_status(
        head,
        {"refs/heads/main": "x" * 3000},
        others_reason="",
    )

    for packet in _pkts(report):
        if packet is not None:
            assert len(packet) + 4 <= 1000
    assert len(sideband(1, b"y" * 2000, max_payload=MAX_SMALL_SIDEBAND_PAYLOAD)) > 2000


# ── advertisement filtering ──────────────────────────────────────────────────


def _advert(*lines: bytes, service: str = "git-upload-pack") -> bytes:
    return (
        pkt_line(f"# service={service}\n".encode())
        + FLUSH_PKT
        + b"".join(pkt_line(line) for line in lines)
        + FLUSH_PKT
    )


def _filtered(data: bytes, chunk: int = 7) -> bytes:
    advert = AdvertisementFilter()
    out = bytearray()
    for start in range(0, len(data), chunk):
        out += advert.feed(data[start : start + chunk])
    out += advert.close()
    return bytes(out)


def test_refs_jentic_and_push_cert_are_filtered_even_from_the_first_line() -> None:
    data = _advert(
        f"{_A} refs/jentic/q/1\0report-status push-cert=1234 atomic\n".encode(),
        f"{_B} refs/heads/main\n".encode(),
        f"{_C} refs/tags/v1\n".encode(),
        f"{_A} refs/tags/v1^{{}}\n".encode(),
        f"{_C} refs/jentic/x^{{}}\n".encode(),
    )

    out = _pkts(_filtered(data))

    assert out == [
        b"# service=git-upload-pack\n",
        None,
        f"{_B} refs/heads/main\0report-status atomic\n".encode(),
        f"{_C} refs/tags/v1\n".encode(),
        f"{_A} refs/tags/v1^{{}}\n".encode(),
        None,
    ]


def test_an_advert_left_without_refs_carries_the_capabilities_placeholder() -> None:
    data = _advert(f"{_A} refs/jentic/only\0report-status push-cert=1\n".encode())

    out = _pkts(_filtered(data, chunk=3))

    assert out[2] == f"{ZERO_SHA} capabilities^{{}}\0report-status\n".encode()
    assert out[-1] is None


def test_an_empty_repository_advert_passes_through() -> None:
    data = _advert(
        f"{ZERO_SHA} capabilities^{{}}\0report-status delete-refs\n".encode(),
        service="git-receive-pack",
    )
    assert _filtered(data) == data


def test_a_protocol_v2_advert_passes_through() -> None:
    data = (
        pkt_line(b"# service=git-upload-pack\n")
        + FLUSH_PKT
        + pkt_line(b"version 2\n")
        + pkt_line(b"ls-refs=unborn\n")
        + FLUSH_PKT
    )
    assert _filtered(data) == data


def test_a_body_that_is_not_pkt_lines_is_refused() -> None:
    with pytest.raises(GitProtocolError):
        AdvertisementFilter().feed(b"<html>not git</html>")
    advert = AdvertisementFilter()
    advert.feed(pkt_line(b"# service=git-upload-pack\n") + FLUSH_PKT + b"00ff")
    with pytest.raises(GitProtocolError):
        advert.close()
