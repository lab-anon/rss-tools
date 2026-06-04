from __future__ import annotations

import inspect
import time
import urllib.error
import urllib.request

import rss_tools.core.models as models

_DEFAULT_REQUEST_SETTINGS = models.RequestSettings(
    timeout_seconds=30,
    retries=3,
    user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) "
        "Gecko/20100101 Firefox/150.0"
    ),
    retryable_status_codes=frozenset({403, 429, 500, 502, 503, 504}),
)


class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched."""


class NotFoundError(FetchError):
    """Raised when a URL returns 404."""


def fetch_bytes(
    url: str,
    *,
    request_settings: models.RequestSettings | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    return fetch_response(
        url,
        request_settings=request_settings,
        headers=headers,
    ).content


def fetch_response(
    url: str,
    *,
    request_settings: models.RequestSettings | None = None,
    headers: dict[str, str] | None = None,
) -> models.HttpResponse:
    resolved_settings = (
        _DEFAULT_REQUEST_SETTINGS if request_settings is None else request_settings
    )
    request_headers = {"User-Agent": resolved_settings.user_agent}
    if headers is not None:
        request_headers.update(headers)

    last_error: Exception | None = None
    for attempt in range(resolved_settings.retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(
                request,
                timeout=resolved_settings.timeout_seconds,
            ) as response:
                return models.HttpResponse(
                    content=response.read(),
                    status_code=response.status,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return models.HttpResponse(
                    content=b"",
                    status_code=304,
                    etag=exc.headers.get("ETag"),
                    last_modified=exc.headers.get("Last-Modified"),
                )
            if exc.code == 404:
                raise NotFoundError(f"URL not found: {url}") from exc

            last_error = exc
            if (
                exc.code not in resolved_settings.retryable_status_codes
                or attempt == resolved_settings.retries
            ):
                raise FetchError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt == resolved_settings.retries:
                raise FetchError(f"Failed to fetch {url}: {exc.reason}") from exc

        time.sleep(0.2 * (attempt + 1))

    raise FetchError(f"Failed to fetch {url}") from last_error


def fetch_response_with_settings(
    fetcher: models.FetchResponse,
    url: str,
    request_settings: models.RequestSettings | None,
    headers: dict[str, str] | None = None,
) -> models.HttpResponse:
    kwargs = {}
    if _accepts_parameter(fetcher, "request_settings"):
        kwargs["request_settings"] = request_settings
    if headers is not None and _accepts_parameter(fetcher, "headers"):
        kwargs["headers"] = headers

    response = fetcher(url, **kwargs)
    if isinstance(response, models.HttpResponse):
        return response
    return models.HttpResponse(content=response)


def _accepts_parameter(fetcher, name: str) -> bool:
    try:
        parameters = inspect.signature(fetcher).parameters.values()
    except (TypeError, ValueError):
        return False

    for parameter in parameters:
        if parameter.name == name:
            return True
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False
