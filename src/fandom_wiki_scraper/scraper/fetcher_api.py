"""MediaWiki API fetcher — uses Fandom's api.php endpoint.

api.php is explicitly allowed in robots.txt and is not behind Cloudflare,
so no browser impersonation is needed. Returns structured JSON which is
wrapped in a minimal HTML shell for compatibility with PageParser.
"""

import time
import urllib.parse
from typing import Any, ClassVar, Literal

import backoff
import httpx
from loguru import logger

from fandom_wiki_scraper.scraper.fetcher import BaseFetcher

MAX_RETRY_TRIES = 5
MAX_RETRY_TIME = 60

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _giveup_on_status(exc: Exception) -> bool:
    """Give up immediately on non-retryable HTTP errors (e.g. 404, 403)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in _RETRYABLE_STATUS_CODES
    return False


def _log_backoff(details: dict[str, Any]) -> None:
    logger.warning(
        "Backoff attempt={tries} wait={wait:.1f}s",
        **details,
    )


class MediaWikiFetcher(BaseFetcher):
    """Fandom MediaWiki API fetcher using httpx.

    Fetches wiki pages via api.php?action=parse and wraps the returned
    HTML fragment in a minimal shell so PageParser works identically
    across all backends.

    Use as a context manager to close the HTTP client cleanly:

        with MediaWikiFetcher() as fetcher:
            html = fetcher.fetch_html(url)
    """

    API_BASE: ClassVar[str] = "https://onepiece.fandom.com/api.php"

    def __init__(self, delay: float = 1.5) -> None:
        self._delay = delay
        self._client = httpx.Client(follow_redirects=True, timeout=30.0)

    @property
    def source_type(self) -> Literal["dom", "content"]:
        return "content"

    def __enter__(self) -> "MediaWikiFetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_html(self, url: str) -> str:
        """Fetch a wiki page via api.php and return an HTML string.

        Raises FileNotFoundError if the page does not exist.
        Retries on transient errors (429, 5xx).
        """
        return self._fetch_html_with_retry(url)

    def fetch_binary(self, url: str) -> bytes:
        """Fetch a binary resource (image) from the CDN.

        CDN URLs are not behind the same Cloudflare as wiki pages and
        usually work fine. Logs a WARNING and re-raises on failure so
        the caller can handle gracefully.
        """
        time.sleep(self._delay)
        try:
            response = self._client.get(url)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            logger.warning("Failed to download media {}: {}", url, exc)
            raise

    def is_allowed(self, url: str) -> bool:
        """api.php is explicitly allowed in robots.txt."""
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @backoff.on_exception(
        backoff.expo,
        exception=(httpx.HTTPStatusError, httpx.TransportError),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _fetch_html_with_retry(self, url: str) -> str:
        slug = urllib.parse.unquote(url.split("/wiki/")[-1])
        params = {
            "action": "parse",
            "format": "json",
            "page": slug,
            "prop": "text|links|images",
            "redirects": "1",
        }
        time.sleep(self._delay)
        response = self._client.get(self.API_BASE, params=params)
        response.raise_for_status()
        data = response.json()
        title, content_html = self._parse_api_response(data)
        return self._wrap_html(title, content_html)

    def _parse_api_response(self, data: dict[str, Any]) -> tuple[str, str]:
        """Extract (title, content_html) from api.php JSON response.

        Raises FileNotFoundError if the page is missing or the API
        returns an error.
        """
        if "error" in data:
            info = data["error"].get("info", str(data["error"]))
            raise FileNotFoundError(f"MediaWiki API error: {info}")
        parse = data.get("parse", {})
        if "missing" in parse:
            raise FileNotFoundError(
                f"MediaWiki page not found: {parse.get('title', '?')}"
            )
        title = parse.get("title", "")
        content_html = parse.get("text", {}).get("*", "")
        return title, content_html

    @staticmethod
    def _wrap_html(title: str, content_html: str) -> str:
        """Wrap an API HTML fragment in a minimal shell for PageParser."""
        return (
            "<html><body>"
            f'<h1 id="firstHeading">{title}</h1>'
            f'<div class="mw-parser-output">{content_html}</div>'
            "</body></html>"
        )
