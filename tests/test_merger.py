from __future__ import annotations

import json
import xml.sax.saxutils
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

import rss_tools.core.feed as feed
import rss_tools.core.identity as identity
import rss_tools.core.merger as merger
import rss_tools.core.models as models
import rss_tools.core.state as state

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def test_merger_dedupes_retains_and_rewrites_synthetic_guid(tmp_path: Path) -> None:
    mergers_path = _write_mergers(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    shared_id = identity.stable_id_for_link("https://example.test/shared/")
    source_a = models.CachedSourceState(
        slug="a",
        link="https://feed.test/a/",
        refresh_hours=6,
        last_checked_at=NOW,
        last_success_at=NOW,
        last_changed_at=NOW,
        last_error=None,
        etag=None,
        last_modified=None,
        items=(
            _cached_item(
                title="Shared Old",
                link="https://example.test/shared/?utm_source=feed",
                published_at=NOW - timedelta(hours=2),
                source_guid="origin-old",
            ),
            _cached_item(
                title="Tie A",
                link="https://example.test/tie/",
                published_at=NOW - timedelta(hours=1),
                source_guid="origin-tie-a",
            ),
            _cached_item(
                title="Expired",
                link="https://example.test/expired/",
                published_at=NOW - timedelta(days=8),
                source_guid="origin-expired",
            ),
        ),
        namespaces={},
    )
    source_b = models.CachedSourceState(
        slug="b",
        link="https://feed.test/b/",
        refresh_hours=6,
        last_checked_at=NOW,
        last_success_at=NOW,
        last_changed_at=NOW,
        last_error=None,
        etag=None,
        last_modified=None,
        items=(
            _cached_item(
                title="Shared New",
                link="https://example.test/shared/",
                published_at=NOW - timedelta(minutes=30),
                source_guid="origin-new",
            ),
            _cached_item(
                title="Tie B",
                link="https://example.test/tie",
                published_at=NOW - timedelta(hours=1),
                source_guid="origin-tie-b",
            ),
        ),
        namespaces={},
    )
    state.write_source_state(state_dir, source_a)
    state.write_source_state(state_dir, source_b)

    result = merger.merge_feeds(
        mergers_path=mergers_path,
        state_dir=state_dir,
        output_dir=tmp_path / "site",
        now=NOW,
    )

    output_path = tmp_path / "site" / "merged" / "feed.xml"
    parsed = feed.parse_feed(output_path.read_bytes())
    root = ET.fromstring(output_path.read_bytes())
    item_nodes = root.findall("./channel/item")
    titles = [item.title for item in parsed.items]
    guid_texts: list[str | None] = []
    guid_attrs: list[dict[str, str]] = []
    for node in item_nodes:
        guid = node.find("guid")
        assert guid is not None
        guid_texts.append(guid.text)
        guid_attrs.append(guid.attrib)

    assert result.feed_results[0].output_path == "merged/feed.xml"
    assert titles == ["Shared New", "Tie A"]
    assert "Expired" not in titles
    assert all(text is not None for text in guid_texts)
    assert all(
        text.startswith(feed.SYNTHETIC_GUID_PREFIX) for text in guid_texts if text
    )
    assert f"{feed.SYNTHETIC_GUID_PREFIX}{shared_id}" in guid_texts
    assert "origin-new" not in output_path.read_text(encoding="utf-8")
    assert "origin-tie-a" not in output_path.read_text(encoding="utf-8")
    assert guid_attrs == [{"isPermaLink": "false"}, {"isPermaLink": "false"}]


def test_merger_builds_empty_feed_for_missing_source_state(tmp_path: Path) -> None:
    mergers_path = _write_mergers(tmp_path)

    result = merger.merge_feeds(
        mergers_path=mergers_path,
        state_dir=tmp_path / ".state" / "sources",
        output_dir=tmp_path / "site",
        now=NOW,
    )

    output_path = tmp_path / "site" / "merged" / "feed.xml"
    parsed = feed.parse_feed(output_path.read_bytes())
    assert result.feed_results[0].item_count == 0
    assert parsed.items == []


def test_merger_records_error_result_for_invalid_cached_item(tmp_path: Path) -> None:
    mergers_path = _write_mergers(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    _write_invalid_source_state(state_dir)

    result = merger.merge_feeds(
        mergers_path=mergers_path,
        state_dir=state_dir,
        output_dir=tmp_path / "site",
        now=NOW,
        capture_merger_errors=True,
    )

    feed_result = result.feed_results[0]
    payload = json.loads(
        (tmp_path / "site" / "status.json").read_text(encoding="utf-8")
    )
    merger_row = next(row for row in payload["rows"] if row["type"] == "merger")

    assert feed_result.status == "error"
    assert feed_result.last_error
    assert not (tmp_path / "site" / "merged" / "feed.xml").exists()
    assert merger_row["slug"] == "merged"
    assert merger_row["status"] == "error"
    assert merger_row["last_error"]


def test_merger_raises_by_default_for_invalid_cached_item(tmp_path: Path) -> None:
    mergers_path = _write_mergers(tmp_path)
    state_dir = tmp_path / ".state" / "sources"
    _write_invalid_source_state(state_dir)

    with pytest.raises(ET.ParseError):
        merger.merge_feeds(
            mergers_path=mergers_path,
            state_dir=state_dir,
            output_dir=tmp_path / "site",
            now=NOW,
        )


def _write_mergers(tmp_path: Path) -> Path:
    mergers_path = tmp_path / "mergers.json"
    mergers_path.write_text(
        json.dumps(
            [
                {
                    "slug": "merged",
                    "title": "Merged",
                    "description": "Merged feed",
                    "feeds": ["a", "b"],
                    "retention_days": 7,
                }
            ]
        ),
        encoding="utf-8",
    )
    return mergers_path


def _write_invalid_source_state(state_dir: Path) -> None:
    state.write_source_state(
        state_dir,
        models.CachedSourceState(
            slug="a",
            link="https://feed.test/a/",
            refresh_hours=6,
            last_checked_at=NOW,
            last_success_at=NOW,
            last_changed_at=NOW,
            last_error=None,
            etag=None,
            last_modified=None,
            items=(
                models.CachedItem(
                    stable_id=identity.stable_id_for_link("https://example.test/bad/"),
                    normalized_link=identity.canonicalize_link(
                        "https://example.test/bad/"
                    ),
                    title="Bad",
                    link="https://example.test/bad/",
                    published_at=NOW,
                    item_xml="<item>",
                ),
            ),
            namespaces={},
        ),
    )


def _cached_item(
    *,
    title: str,
    link: str,
    published_at: datetime,
    source_guid: str,
) -> models.CachedItem:
    return models.CachedItem(
        stable_id=identity.stable_id_for_link(link),
        normalized_link=identity.canonicalize_link(link),
        title=title,
        link=link,
        published_at=published_at,
        item_xml=(
            "<item>"
            f"<title>{xml.sax.saxutils.escape(title)}</title>"
            f"<link>{xml.sax.saxutils.escape(link)}</link>"
            f"<guid>{xml.sax.saxutils.escape(source_guid)}</guid>"
            f"<pubDate>{feed.format_feed_date(published_at)}</pubDate>"
            f"<description>{xml.sax.saxutils.escape(title)}</description>"
            "</item>"
        ),
    )
