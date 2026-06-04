from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import rss_tools.core.models as models
import rss_tools.core.state as source_state
import rss_tools.settings as settings

STATUS_ORDER = {
    "error": 0,
    "stale": 1,
    "ok": 2,
    "skipped": 3,
}
ERROR_MAX_LENGTH = 180

StatusRowPayload = dict[str, str | int | None]
StatusDocumentPayload = dict[str, str | list[StatusRowPayload]]


@dataclass(frozen=True)
class StatusRow:
    type: str
    slug: str
    status: str
    last_success_at: datetime | None
    item_count: int
    last_error: str | None
    title: str | None = None
    description: str | None = None
    link: str | None = None
    refresh_hours: int | None = None
    output_path: str | None = None
    public_url: str | None = None


def write_status_outputs(
    *,
    output_dir: Path,
    generated_at: datetime,
    source_configs: list[models.FeedSourceConfig] | None,
    source_slugs: list[str],
    source_results: list[models.SourceCollectResult] | None,
    merger_results: list[models.MergedFeedResult],
    state_dir: Path | str,
) -> None:
    rows = build_status_rows(
        source_configs=source_configs,
        source_slugs=source_slugs,
        source_results=source_results,
        merger_results=merger_results,
        state_dir=state_dir,
    )
    document = status_document_to_json(generated_at=generated_at, rows=rows)
    (output_dir / settings.STATUS_FILENAME).write_text(
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / settings.INDEX_FILENAME).write_text(
        render_status_html(generated_at=generated_at, rows=rows),
        encoding="utf-8",
    )


def build_status_rows(
    *,
    source_configs: list[models.FeedSourceConfig] | None,
    source_slugs: list[str],
    source_results: list[models.SourceCollectResult] | None,
    merger_results: list[models.MergedFeedResult],
    state_dir: Path | str,
) -> list[StatusRow]:
    result_by_slug = {result.slug: result for result in source_results or []}
    config_by_slug = {source.slug: source for source in source_configs or []}
    source_rows = [
        _source_row(
            slug=slug,
            source=config_by_slug.get(slug),
            result=result_by_slug.get(slug),
            state_dir=state_dir,
        )
        for slug in source_slugs
    ]
    merger_rows = [_merger_row(result) for result in merger_results]
    return [
        *sort_merger_rows(merger_rows),
        *sort_source_rows(source_rows),
    ]


def sort_source_rows(rows: list[StatusRow]) -> list[StatusRow]:
    return sorted(
        rows,
        key=lambda row: (
            STATUS_ORDER.get(row.status, STATUS_ORDER["error"]),
            row.slug.casefold(),
        ),
    )


def sort_merger_rows(rows: list[StatusRow]) -> list[StatusRow]:
    return sorted(rows, key=lambda row: row.slug.casefold())


def status_document_to_json(
    *,
    generated_at: datetime,
    rows: list[StatusRow],
) -> StatusDocumentPayload:
    formatted_generated_at = source_state.format_json_datetime(generated_at)
    if formatted_generated_at is None:
        raise ValueError("generated_at is required")
    return {
        "generated_at": formatted_generated_at,
        "rows": [
            status_row_to_json(row)
            for row in [
                *sort_merger_rows([row for row in rows if row.type == "merger"]),
                *sort_source_rows([row for row in rows if row.type == "source"]),
            ]
        ],
    }


def status_row_to_json(row: StatusRow) -> StatusRowPayload:
    payload: StatusRowPayload = {
        "type": row.type,
        "slug": row.slug,
        "status": row.status,
        "last_success_at": source_state.format_json_datetime(row.last_success_at),
        "item_count": row.item_count,
        "last_error": row.last_error,
    }
    if row.type == "merger":
        payload.update(
            {
                "title": row.title,
                "description": row.description,
                "public_url": row.public_url,
            }
        )
    if row.type == "source":
        payload.update(
            {
                "link": row.link,
                "refresh_hours": row.refresh_hours,
            }
        )
    return payload


def render_status_html(
    *,
    generated_at: datetime,
    rows: list[StatusRow],
) -> str:
    merger_rows = sort_merger_rows([row for row in rows if row.type == "merger"])
    source_rows = sort_source_rows([row for row in rows if row.type == "source"])
    return (
        "<!doctype html>\n"
        f'<html lang="{html.escape(settings.SITE_LANGUAGE, quote=True)}">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        f"    <title>{html.escape(settings.SITE_TITLE)}</title>\n"
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "    <style>\n"
        "      :root { color-scheme: light; }\n"
        "      body { margin: 2rem; font-family: system-ui, sans-serif; "
        "color: #182016; background: #fbfaf5; }\n"
        "      main { max-width: 1100px; }\n"
        "      h2 { margin-top: 2rem; }\n"
        "      table { width: 100%; border-collapse: collapse; "
        "background: white; }\n"
        "      th, td { padding: 0.65rem 0.75rem; border-bottom: 1px solid #ddd8ca; "
        "text-align: left; vertical-align: top; }\n"
        "      th { background: #263238; color: #fffdf5; font-size: 0.85rem; }\n"
        "      a { color: #205f76; font-weight: 700; overflow-wrap: anywhere; }\n"
        "      .status-row.status-error { background: #fde8e8; }\n"
        "      .status-row.status-stale { background: #fff3cd; }\n"
        "      .status-row.status-ok { background: #edf7ed; }\n"
        "      .status-row.status-skipped { background: #f1f3f5; }\n"
        "      .status-label { font-weight: 700; text-transform: uppercase; }\n"
        "      .error-text { max-width: 32rem; }\n"
        "      @media (max-width: 760px) { body { margin: 1rem; } "
        "table { font-size: 0.9rem; } th, td { padding: 0.5rem; } }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <main>\n"
        f"      <h1>{html.escape(settings.SITE_TITLE)}</h1>\n"
        f"      <p>{html.escape(settings.SITE_DESCRIPTION)}</p>\n"
        f"      <p><strong>Generated:</strong> {html.escape(_format_datetime(generated_at))}</p>\n"
        f"{_render_merged_table(merger_rows)}\n"
        f"{_render_source_table(source_rows)}\n"
        "    </main>\n"
        "  </body>\n"
        "</html>\n"
    )


def _source_row(
    *,
    slug: str,
    source: models.FeedSourceConfig | None,
    result: models.SourceCollectResult | None,
    state_dir: Path | str,
) -> StatusRow:
    link = source.link if source is not None else None
    refresh_hours = source.refresh_hours if source is not None else None

    if result is not None:
        status = _source_status(result.status)
        last_error = result.last_error
        if status == "error" and last_error is None:
            last_error = result.message
        return StatusRow(
            type="source",
            slug=slug,
            status=status,
            last_success_at=result.last_success_at,
            item_count=result.item_count,
            last_error=last_error,
            link=link,
            refresh_hours=refresh_hours,
        )

    try:
        state = source_state.load_source_state(state_dir, slug)
    except Exception as exc:
        return StatusRow(
            type="source",
            slug=slug,
            status="error",
            last_success_at=None,
            item_count=0,
            last_error=str(exc) or exc.__class__.__name__,
            link=link,
            refresh_hours=refresh_hours,
        )

    if state is None:
        return StatusRow(
            type="source",
            slug=slug,
            status="error",
            last_success_at=None,
            item_count=0,
            last_error="missing source state",
            link=link,
            refresh_hours=refresh_hours,
        )

    if link is None:
        link = state.link
    if refresh_hours is None:
        refresh_hours = state.refresh_hours

    return StatusRow(
        type="source",
        slug=slug,
        status="error" if state.last_error else "ok",
        last_success_at=state.last_success_at,
        item_count=len(state.items),
        last_error=state.last_error,
        link=link,
        refresh_hours=refresh_hours,
    )


def _merger_row(result: models.MergedFeedResult) -> StatusRow:
    status = result.status if result.status in STATUS_ORDER else "error"
    return StatusRow(
        type="merger",
        slug=result.slug,
        status=status,
        last_success_at=result.last_success_at,
        item_count=result.item_count,
        last_error=result.last_error,
        title=result.title,
        description=result.description,
        output_path=result.output_path,
        public_url=result.public_url,
    )


def _source_status(status: str) -> str:
    if status in {"failed", "error"}:
        return "error"
    if status == "skipped":
        return "skipped"
    if status in {"not_modified", "refreshed", "ok"}:
        return "ok"
    if status == "stale":
        return "stale"
    return "error"


def _render_merged_table(rows: list[StatusRow]) -> str:
    body_rows = "\n".join(_render_merged_row(row) for row in rows)
    return (
        "      <h2>Merged Feeds</h2>\n"
        "      <table>\n"
        "        <thead>\n"
        "          <tr>\n"
        "            <th>Title</th>\n"
        "            <th>Description</th>\n"
        "            <th>Feed</th>\n"
        "            <th>Items</th>\n"
        "            <th>Status</th>\n"
        "            <th>Error</th>\n"
        "          </tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{body_rows}\n"
        "        </tbody>\n"
        "      </table>"
    )


def _render_source_table(rows: list[StatusRow]) -> str:
    body_rows = "\n".join(_render_source_row(row) for row in rows)
    return (
        "      <h2>Source Feeds</h2>\n"
        "      <table>\n"
        "        <thead>\n"
        "          <tr>\n"
        "            <th>Slug</th>\n"
        "            <th>Link</th>\n"
        "            <th>Refresh Hours</th>\n"
        "            <th>Status</th>\n"
        "            <th>Last Success</th>\n"
        "            <th>Items</th>\n"
        "            <th>Error</th>\n"
        "          </tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{body_rows}\n"
        "        </tbody>\n"
        "      </table>"
    )


def _render_merged_row(row: StatusRow) -> str:
    return (
        f'          <tr class="status-row status-{html.escape(row.status, quote=True)}">\n'
        f"            <td>{html.escape(row.title or row.slug)}</td>\n"
        f"            <td>{html.escape(row.description or '')}</td>\n"
        f"            <td>{_render_feed_link(row)}</td>\n"
        f"            <td>{row.item_count}</td>\n"
        f"            <td>{_render_status(row.status)}</td>\n"
        f'            <td class="error-text">{html.escape(_short_error(row.last_error))}</td>\n'
        "          </tr>"
    )


def _render_source_row(row: StatusRow) -> str:
    return (
        f'          <tr class="status-row status-{html.escape(row.status, quote=True)}">\n'
        f"            <td>{html.escape(row.slug)}</td>\n"
        f"            <td>{_render_source_link(row)}</td>\n"
        f"            <td>{'' if row.refresh_hours is None else row.refresh_hours}</td>\n"
        f"            <td>{_render_status(row.status)}</td>\n"
        f"            <td>{html.escape(_format_datetime(row.last_success_at))}</td>\n"
        f"            <td>{row.item_count}</td>\n"
        f'            <td class="error-text">{html.escape(_short_error(row.last_error))}</td>\n'
        "          </tr>"
    )


def _render_feed_link(row: StatusRow) -> str:
    if not row.output_path:
        return ""
    href = html.escape(row.output_path, quote=True)
    return f'<a href="{href}">feed</a>'


def _render_source_link(row: StatusRow) -> str:
    if not row.link:
        return ""
    href = html.escape(row.link, quote=True)
    return f'<a href="{href}">{html.escape(row.link)}</a>'


def _render_status(status: str) -> str:
    return f'<span class="status-label">{html.escape(status)}</span>'


def _short_error(error: str | None) -> str:
    if not error:
        return ""
    collapsed = " ".join(error.split())
    if len(collapsed) <= ERROR_MAX_LENGTH:
        return collapsed
    return f"{collapsed[: ERROR_MAX_LENGTH - 3]}..."


def _format_datetime(value: datetime | None) -> str:
    return source_state.format_json_datetime(value) or "never"
