from __future__ import annotations

import hashlib
import urllib.parse

TRACKING_QUERY_PARAMS = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
}


def canonicalize_link(link: str) -> str:
    parsed = urllib.parse.urlparse(link.strip())
    scheme = parsed.scheme.lower()
    netloc = _normalize_netloc(parsed)
    path = _normalize_path(parsed.path)
    query = _normalize_query(parsed.query)
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def stable_id_for_link(link: str) -> str:
    normalized_link = canonicalize_link(link)
    return hashlib.sha256(normalized_link.encode("utf-8")).hexdigest()


def _normalize_netloc(parsed) -> str:
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    port = f":{parsed.port}" if parsed.port is not None else ""

    if parsed.username is None:
        credentials = ""
    elif parsed.password is None:
        credentials = f"{parsed.username}@"
    else:
        credentials = f"{parsed.username}:{parsed.password}@"

    return f"{credentials}{hostname}{port}"


def _normalize_path(path: str) -> str:
    resolved = path or "/"
    if resolved != "/":
        resolved = resolved.rstrip("/")
    return resolved or "/"


def _normalize_query(query: str) -> str:
    retained_params = []
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key.startswith("utm_"):
            continue
        if normalized_key in TRACKING_QUERY_PARAMS:
            continue
        retained_params.append((key, value))
    return urllib.parse.urlencode(retained_params, doseq=True)
