"""Unit tests for the server-side release checker (``shared/release_check.py``).

Covers the config gates (disabled / kill-switch TTL / remote backend), the
tag_name normalisation, the best-effort degrade on fetch failure, and the
in-process TTL cache + single-flight coalescing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jentic_one.shared.config import AppConfig
from jentic_one.shared.release_check import CatalogFetchError, ReleaseChecker


def _config(sample_config_dict: dict[str, Any], **release_check: Any) -> AppConfig:
    cfg = dict(sample_config_dict)
    cfg["release_check"] = {"enabled": True, **release_check}
    return AppConfig.model_validate(cfg)


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, doc_or_exc: Any) -> list[str]:
    """Patch fetch_json to return ``doc_or_exc`` (or raise it); return call log."""
    urls: list[str] = []

    async def _fake(url: str, *, config: Any) -> dict[str, Any]:
        urls.append(url)
        if isinstance(doc_or_exc, Exception):
            raise doc_or_exc
        return doc_or_exc

    monkeypatch.setattr("jentic_one.shared.release_check.fetch_json", _fake)
    return urls


@pytest.mark.asyncio
async def test_returns_normalised_tag(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _stub_fetch(monkeypatch, {"tag_name": "v1.2.3"})
    checker = ReleaseChecker(_config(sample_config_dict, repo="acme/widget"))

    assert await checker.latest_version() == "1.2.3"
    assert urls == ["https://api.github.com/repos/acme/widget/releases/latest"]


@pytest.mark.asyncio
async def test_disabled_makes_no_request(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _stub_fetch(monkeypatch, {"tag_name": "v1.2.3"})
    checker = ReleaseChecker(_config(sample_config_dict, enabled=False))

    assert await checker.latest_version() is None
    assert urls == []


@pytest.mark.asyncio
async def test_zero_ttl_is_a_kill_switch(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _stub_fetch(monkeypatch, {"tag_name": "v1.2.3"})
    checker = ReleaseChecker(_config(sample_config_dict, cache_ttl_seconds=0))

    assert await checker.latest_version() is None
    assert urls == []


@pytest.mark.asyncio
async def test_remote_backend_makes_no_request(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _stub_fetch(monkeypatch, {"tag_name": "v1.2.3"})
    cfg = dict(sample_config_dict)
    cfg["release_check"] = {"enabled": True}
    cfg["server"] = {**cfg.get("server", {}), "backend": "remote"}
    checker = ReleaseChecker(AppConfig.model_validate(cfg))

    assert await checker.latest_version() is None
    assert urls == []


@pytest.mark.asyncio
async def test_fetch_failure_degrades_to_none(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch, CatalogFetchError("offline"))
    checker = ReleaseChecker(_config(sample_config_dict))

    assert await checker.latest_version() is None


@pytest.mark.asyncio
async def test_missing_or_bad_tag_name_is_none(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch, {"name": "no tag_name here"})
    checker = ReleaseChecker(_config(sample_config_dict))
    assert await checker.latest_version() is None


@pytest.mark.asyncio
async def test_prerelease_tag_normalises_to_none(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch, {"tag_name": "v1.2.3-rc1"})
    checker = ReleaseChecker(_config(sample_config_dict))
    assert await checker.latest_version() is None


@pytest.mark.asyncio
async def test_result_is_cached_within_ttl(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _stub_fetch(monkeypatch, {"tag_name": "v1.2.3"})
    checker = ReleaseChecker(_config(sample_config_dict, cache_ttl_seconds=3600))

    assert await checker.latest_version() == "1.2.3"
    assert await checker.latest_version() == "1.2.3"
    assert len(urls) == 1  # second read served from cache


@pytest.mark.asyncio
async def test_concurrent_reads_coalesce_into_one_fetch(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst of first-time readers makes a single GitHub request (single-flight)."""
    urls: list[str] = []

    async def _slow(url: str, *, config: Any) -> dict[str, Any]:
        urls.append(url)
        await asyncio.sleep(0.05)  # hold the lock so others pile up behind it
        return {"tag_name": "v2.0.0"}

    monkeypatch.setattr("jentic_one.shared.release_check.fetch_json", _slow)
    checker = ReleaseChecker(_config(sample_config_dict, cache_ttl_seconds=3600))

    results = await asyncio.gather(*(checker.latest_version() for _ in range(8)))

    assert results == ["2.0.0"] * 8
    assert len(urls) == 1
