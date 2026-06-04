from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import rss_tools.cli as cli
import rss_tools.core.feed as feed
import rss_tools.core.identity as identity
import rss_tools.core.models as models
import rss_tools.core.state as state
import rss_tools.core.status as status

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
FUTURE = datetime(2999, 1, 1, 12, 0, tzinfo=UTC)


def test_source_rows_sort_by_severity_then_slug_with_errors_first() -> None:
    rows = [
        _source_row("z-ok", "ok"),
        _source_row("b-error", "error"),
        _source_row("d-skipped", "skipped"),
        _source_row("a-error", "error"),
        _source_row("c-stale", "stale"),
    ]

    sorted_rows = status.sort_source_rows(rows)

    assert [(row.status, row.slug) for row in sorted_rows] == [
        ("error", "a-error"),
        ("error", "b-error"),
        ("stale", "c-stale"),
        ("ok", "z-ok"),
        ("skipped", "d-skipped"),
    ]


def test_merger_rows_sort_by_slug() -> None:
    rows = [
        _merger_row("z-merged", title="Merged Z"),
        _merger_row("a-merged", title="Merged A"),
        _merger_row("m-merged", title="Merged M"),
    ]

    sorted_rows = status.sort_merger_rows(rows)

    assert [row.slug for row in sorted_rows] == [
        "a-merged",
        "m-merged",
        "z-merged",
    ]


def test_status_html_has_two_tables_columns_links_order_classes_and_escaped_errors() -> (
    None
):
    html = status.render_status_html(
        generated_at=NOW,
        rows=[
            _source_row("z-ok", "ok"),
            _source_row("b-stale", "stale"),
            _source_row("d-skipped", "skipped"),
            _source_row("a-error", "error", last_error='<boom & "bad">'),
            _merger_row("z-merged", title="Merged Z"),
            _merger_row("a-merged", title="Merged A"),
        ],
    )

    assert html.index("<h2>Merged Feeds</h2>") < html.index("<h2>Source Feeds</h2>")
    for column in ["Title", "Description", "Feed", "Items", "Status", "Error"]:
        assert f"<th>{column}</th>" in html
    for column in [
        "Slug",
        "Link",
        "Refresh Hours",
        "Status",
        "Last Success",
        "Items",
        "Error",
    ]:
        assert f"<th>{column}</th>" in html

    assert html.index("Merged A") < html.index("Merged Z")
    source_html = html[html.index("<h2>Source Feeds</h2>") :]
    assert source_html.index("a-error") < source_html.index("b-stale")
    assert source_html.index("b-stale") < source_html.index("z-ok")
    assert source_html.index("z-ok") < source_html.index("d-skipped")
    assert '<a href="a-merged/feed.xml">feed</a>' in html
    assert '<a href="https://feed.test/a-error/">https://feed.test/a-error/</a>' in html
    assert 'class="status-row status-error"' in html
    assert 'class="status-row status-stale"' in html
    assert 'class="status-row status-ok"' in html
    assert 'class="status-row status-skipped"' in html
    assert "&lt;boom &amp; &quot;bad&quot;&gt;" in html
    assert '<boom & "bad">' not in html
    assert "<script" not in html


def test_status_json_has_expected_shape_and_maps_source_failures(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "site"
    output_dir.mkdir()

    status.write_status_outputs(
        output_dir=output_dir,
        generated_at=NOW,
        source_configs=[
            models.FeedSourceConfig(
                slug="source",
                link="https://feed.test/source/",
                refresh_hours=6,
            )
        ],
        source_slugs=["source"],
        source_results=[
            models.SourceCollectResult(
                slug="source",
                status="failed",
                item_count=3,
                message="fetch failed",
                last_success_at=None,
                last_error="fetch failed",
            )
        ],
        merger_results=[
            models.MergedFeedResult(
                slug="merged",
                title="Merged",
                description="Merged feed",
                output_path="merged/feed.xml",
                item_count=12,
                message="merged 12 item(s)",
                status="ok",
                last_success_at=NOW,
                last_error=None,
                public_url="https://lab-anon.github.io/rss-tools/merged/feed.xml",
            )
        ],
        state_dir=tmp_path / ".state" / "sources",
    )

    payload = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))

    assert set(payload) == {"generated_at", "rows"}
    assert payload["generated_at"] == "2026-06-04T12:00:00Z"
    assert payload["rows"] == [
        {
            "type": "merger",
            "slug": "merged",
            "status": "ok",
            "last_success_at": "2026-06-04T12:00:00Z",
            "item_count": 12,
            "last_error": None,
            "title": "Merged",
            "description": "Merged feed",
            "public_url": "https://lab-anon.github.io/rss-tools/merged/feed.xml",
        },
        {
            "type": "source",
            "slug": "source",
            "status": "error",
            "last_success_at": None,
            "item_count": 3,
            "last_error": "fetch failed",
            "link": "https://feed.test/source/",
            "refresh_hours": 6,
        },
    ]
    assert (output_dir / "index.html").exists()


def test_build_command_generates_status_json_index_and_feed(tmp_path: Path) -> None:
    feeds_path = tmp_path / "feeds.json"
    mergers_path = tmp_path / "mergers.json"
    state_dir = tmp_path / ".state" / "sources"
    output_dir = tmp_path / "site"
    feeds_path.write_text(
        json.dumps(
            [
                {
                    "slug": "source",
                    "link": "https://feed.test/source/",
                    "refresh_hours": 24,
                }
            ]
        ),
        encoding="utf-8",
    )
    mergers_path.write_text(
        json.dumps(
            [
                {
                    "slug": "merged",
                    "title": "Merged",
                    "description": "Merged feed",
                    "feeds": ["source"],
                    "retention_days": 30,
                }
            ]
        ),
        encoding="utf-8",
    )
    state.write_source_state(
        state_dir,
        models.CachedSourceState(
            slug="source",
            link="https://feed.test/source/",
            refresh_hours=24,
            last_checked_at=FUTURE,
            last_success_at=FUTURE,
            last_changed_at=FUTURE,
            last_error=None,
            etag=None,
            last_modified=None,
            items=(_cached_item("Known", "https://example.test/known/"),),
            namespaces={},
        ),
    )

    result = cli.main(
        [
            "build",
            "--feeds",
            str(feeds_path),
            "--mergers",
            str(mergers_path),
            "--state-dir",
            str(state_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result == 0
    assert (output_dir / "merged" / "feed.xml").exists()
    assert (output_dir / "status.json").exists()
    assert (output_dir / "index.html").exists()
    payload = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))
    assert any(
        row["type"] == "merger" and row["slug"] == "merged" and row["item_count"] == 1
        for row in payload["rows"]
    )
    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "<h2>Merged Feeds</h2>" in html
    assert "<h2>Source Feeds</h2>" in html


def _source_row(
    slug: str,
    row_status: str,
    *,
    last_error: str | None = None,
) -> status.StatusRow:
    return status.StatusRow(
        type="source",
        slug=slug,
        status=row_status,
        last_success_at=NOW,
        item_count=1,
        last_error=last_error,
        link=f"https://feed.test/{slug}/",
        refresh_hours=6,
    )


def _merger_row(
    slug: str,
    *,
    title: str | None = None,
    row_status: str = "ok",
) -> status.StatusRow:
    return status.StatusRow(
        type="merger",
        slug=slug,
        status=row_status,
        last_success_at=NOW,
        item_count=1,
        last_error=None,
        title=title or slug,
        description=f"{title or slug} feed",
        output_path=f"{slug}/feed.xml",
        public_url=f"https://lab-anon.github.io/rss-tools/{slug}/feed.xml",
    )


def _cached_item(title: str, link: str) -> models.CachedItem:
    return models.CachedItem(
        stable_id=identity.stable_id_for_link(link),
        normalized_link=identity.canonicalize_link(link),
        title=title,
        link=link,
        published_at=NOW,
        item_xml=(
            "<item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            "<guid>source-guid</guid>"
            f"<pubDate>{feed.format_feed_date(NOW)}</pubDate>"
            f"<description>{title}</description>"
            "</item>"
        ),
    )
