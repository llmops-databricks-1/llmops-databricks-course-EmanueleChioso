"""curl_cffi-based HTTP fetcher with robots.txt compliance, polite delay, and retries.

Uses curl_cffi to impersonate a real browser TLS fingerprint, which is
required to bypass Cloudflare bot protection on Fandom wikis.

Note on robots.txt: Python's urllib.robotparser mishandles Fandom's
Allow-heavy robots.txt (no blanket Disallow: /), returning False for all
URLs. We use a custom RFC 9309-compliant parser instead, and fall back to
permissive mode when robots.txt itself is blocked by Cloudflare.
"""

import time
import urllib.parse
from typing import Any, Literal

import backoff
from curl_cffi import requests as cf_requests
from curl_cffi.requests.exceptions import RequestException
from loguru import logger

from fandom_wiki_scraper.scraper.fetcher import BaseFetcher

ROBOTS_TXT_URL = "https://onepiece.fandom.com/robots.txt"
BROWSER_IMPERSONATE = "chrome124"
MAX_RETRY_TRIES = 5
MAX_RETRY_TIME = 60

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _giveup_on_status(exc: Exception) -> bool:
    """Give up immediately on non-retryable HTTP errors (e.g. 404, 403)."""
    if isinstance(exc, cf_requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        return code not in _RETRYABLE_STATUS_CODES
    return False


def _log_backoff(details: dict[str, Any]) -> None:
    logger.warning(
        "Backoff attempt={tries} wait={wait:.1f}s url={args[1]}",
        **details,
    )


class _RobotsRules:
    """Minimal RFC 9309-compliant robots.txt rule checker.

    Parses the User-agent: * section and applies longest-match wins:
    - Allow rule beats Disallow rule when both match at equal specificity
    - If no rule matches, the URL is allowed (permissive default)
    """

    def __init__(self, robots_text: str) -> None:
        self._allows: list[str] = []
        self._disallows: list[str] = []
        self._parse(robots_text)

    def _parse(self, text: str) -> None:
        in_wildcard_section = False
        for raw_line in text.splitlines():
            line = raw_line.split("#")[0].strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()

            if key == "user-agent":
                in_wildcard_section = val == "*"
            elif in_wildcard_section:
                if key == "disallow" and val:
                    self._disallows.append(val)
                elif key == "allow" and val:
                    self._allows.append(val)

    def is_allowed(self, url: str) -> bool:
        path = urllib.parse.urlparse(url).path

        best_disallow = ""
        for rule in self._disallows:
            if path.startswith(rule) and len(rule) > len(best_disallow):
                best_disallow = rule

        if not best_disallow:
            return True  # no matching Disallow → allowed

        # Allow rule of equal or greater specificity overrides Disallow
        best_allow = ""
        for rule in self._allows:
            if path.startswith(rule) and len(rule) > len(best_allow):
                best_allow = rule

        return len(best_allow) >= len(best_disallow)


class CurlCffiFetcher(BaseFetcher):
    """curl_cffi-based fetcher that impersonates a real browser.

    Bypasses Cloudflare bot protection by using curl-impersonate, which
    replicates a real Chrome TLS fingerprint and HTTP/2 behaviour.

    Enforces:
    - robots.txt compliance (loaded once at init; permissive if blocked)
    - per-request polite delay
    - exponential backoff retries on transient errors

    Use as a context manager to ensure the session is closed cleanly:

        with CurlCffiFetcher() as fetcher:
            html = fetcher.fetch_html(url)
    """

    def __init__(
        self,
        impersonate: str = BROWSER_IMPERSONATE,
        delay: float = 1.5,
    ) -> None:
        self._delay = delay
        self._session = cf_requests.Session(impersonate=impersonate)
        self._robots: _RobotsRules | None = None
        self._load_robots()

    @property
    def source_type(self) -> Literal["dom", "content"]:
        return "dom"

    def __enter__(self) -> "CurlCffiFetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_html(self, url: str) -> str:
        """Fetch a page URL and return decoded HTML text.

        Raises PermissionError if robots.txt disallows the URL.
        Retries on transient errors (transport, 429, 5xx).
        """
        if not self.is_allowed(url):
            raise PermissionError(f"robots.txt disallows: {url}")
        return self._fetch_html_with_retry(url)

    def fetch_binary(self, url: str) -> bytes:
        """Fetch a binary resource (image, etc.) — best-effort, no retry."""
        time.sleep(self._delay)
        response = self._session.get(url)
        response.raise_for_status()
        return response.content

    def is_allowed(self, url: str) -> bool:
        """Return True if robots.txt permits fetching this URL."""
        if self._robots is None:
            return True
        return self._robots.is_allowed(url)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_robots(self) -> None:
        try:
            response = self._session.get(ROBOTS_TXT_URL)
            response.raise_for_status()
            text = response.text.strip()
            # Cloudflare challenge pages return HTML — detect and fall back
            if text.startswith("<"):
                logger.warning(
                    "robots.txt returned HTML (Cloudflare challenge) "
                    "— proceeding permissively"
                )
                self._robots = None
                return
            self._robots = _RobotsRules(text)
            logger.debug(
                "robots.txt loaded: {} allow rules, {} disallow rules",
                len(self._robots._allows),
                len(self._robots._disallows),
            )
        except Exception as exc:
            logger.warning(
                "Could not fetch robots.txt — proceeding permissively. error={}", exc
            )
            self._robots = None

    @backoff.on_exception(
        backoff.expo,
        exception=(RequestException,),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _fetch_html_with_retry(self, url: str) -> str:
        time.sleep(self._delay)
        response = self._session.get(url)
        response.raise_for_status()
        return response.text
