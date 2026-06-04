from __future__ import annotations

import copy
import email.utils
import typing
from datetime import UTC, datetime
from io import BytesIO
from xml.etree import ElementTree as ET

import rss_tools.core.identity as identity
import rss_tools.core.models as models
import rss_tools.settings as settings

ATOM_NS = "http://www.w3.org/2005/Atom"
DEFAULT_NAMESPACES = {
    "atom": ATOM_NS,
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "slash": "http://purl.org/rss/1.0/modules/slash/",
    "sy": "http://purl.org/rss/1.0/modules/syndication/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
}
SYNTHETIC_GUID_PREFIX = "rss-tools:link-sha256:"


def parse_feed_date(value: str) -> datetime:
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_feed_date(value: datetime) -> str:
    return email.utils.format_datetime(value.astimezone(UTC))


def parse_feed(data: bytes) -> models.ParsedFeed:
    namespaces = dict(DEFAULT_NAMESPACES)
    for _, node in ET.iterparse(BytesIO(data), events=("start-ns",)):
        prefix, uri = typing.cast(tuple[str, str], node)
        namespaces[_normalize_namespace_prefix(prefix, uri, namespaces)] = uri

    register_namespaces(namespaces)
    root = ET.fromstring(data)
    items: list[models.FeedItem] = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue

        normalized_link = identity.canonicalize_link(link)
        items.append(
            models.FeedItem(
                stable_id=identity.stable_id_for_link(link),
                normalized_link=normalized_link,
                date=parse_feed_date(pub_date),
                title=title,
                link=link,
                element=copy.deepcopy(item),
            )
        )

    return models.ParsedFeed(items=items, namespaces=namespaces)


def register_namespaces(namespaces: dict[str, str]) -> None:
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)


def serialize_item_xml(item: models.FeedItem, namespaces: dict[str, str]) -> str:
    register_namespaces(namespaces)
    return ET.tostring(item.element, encoding="unicode")


def parse_item_xml(item_xml: str, namespaces: dict[str, str]) -> ET.Element:
    register_namespaces(namespaces)
    return ET.fromstring(item_xml)


def _normalize_namespace_prefix(
    prefix: str,
    uri: str,
    namespaces: dict[str, str],
) -> str:
    if prefix:
        return prefix

    candidate = "wp" if "com-wordpress:feed-additions" in uri else "ext"

    if candidate not in namespaces:
        return candidate

    suffix = 2
    while f"{candidate}{suffix}" in namespaces:
        suffix += 1
    return f"{candidate}{suffix}"


def dedupe_items(items: list[models.FeedItem]) -> list[models.FeedItem]:
    items_by_stable_id: dict[str, models.FeedItem] = {}
    for item in items:
        current = items_by_stable_id.get(item.stable_id)
        if current is None or item.date > current.date:
            items_by_stable_id[item.stable_id] = item
    return sort_items(list(items_by_stable_id.values()))


def sort_items(items: list[models.FeedItem]) -> list[models.FeedItem]:
    return sorted(
        items,
        key=lambda item: (item.date, item.title.casefold(), item.link),
        reverse=True,
    )


def filter_items_since(
    items: list[models.FeedItem], cutoff: datetime
) -> list[models.FeedItem]:
    return [item for item in items if item.date >= cutoff]


def render_feed(
    *,
    title: str,
    description: str,
    self_url: str,
    site_url: str,
    build_date: datetime,
    items: list[models.FeedItem],
    namespaces: dict[str, str],
) -> bytes:
    final_namespaces = dict(DEFAULT_NAMESPACES)
    final_namespaces.update(namespaces)
    register_namespaces(final_namespaces)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {"href": self_url, "rel": "self", "type": "application/rss+xml"},
    )
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "lastBuildDate").text = format_feed_date(build_date)
    ET.SubElement(channel, "language").text = settings.SITE_LANGUAGE
    ET.SubElement(channel, "generator").text = "rss-tools"

    for item in sort_items(items):
        element = copy.deepcopy(item.element)
        rewrite_synthetic_guid(element, item.stable_id)
        channel.append(element)

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def rewrite_synthetic_guid(item_element: ET.Element, stable_id: str) -> None:
    for child in list(item_element):
        if _local_name(child.tag) == "guid":
            item_element.remove(child)

    guid = ET.Element("guid", {"isPermaLink": "false"})
    guid.text = f"{SYNTHETIC_GUID_PREFIX}{stable_id}"

    insert_at = len(item_element)
    for index, child in enumerate(list(item_element)):
        if _local_name(child.tag) == "link":
            insert_at = index + 1
            break
    item_element.insert(insert_at, guid)


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", maxsplit=1)[-1]
    return tag
