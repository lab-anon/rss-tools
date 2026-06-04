from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import rss_tools.core.feed as feed
import rss_tools.core.identity as identity


def test_canonicalize_link_applies_minimum_normalization() -> None:
    assert (
        identity.canonicalize_link(
            "https://Example.TEST/Case/Sensitive/?utm_source=news&a=1&fbclid=x#top"
        )
        == "https://example.test/Case/Sensitive?a=1"
    )
    assert (
        identity.canonicalize_link("https://example.test/path/")
        == "https://example.test/path"
    )
    assert identity.canonicalize_link("https://example.test") == "https://example.test/"


def test_stable_id_uses_sha256_of_normalized_link() -> None:
    normalized = "https://example.test/comic"
    expected = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    assert (
        identity.stable_id_for_link("https://EXAMPLE.test/comic/?utm_campaign=x")
        == expected
    )


def test_parse_feed_date_normalizes_to_utc() -> None:
    parsed = feed.parse_feed_date("Tue, 03 Jun 2026 10:15:00 +0200")
    assert parsed == datetime(2026, 6, 3, 8, 15, tzinfo=UTC)
