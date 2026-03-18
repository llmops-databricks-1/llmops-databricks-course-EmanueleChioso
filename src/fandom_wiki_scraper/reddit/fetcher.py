"""Reddit JSON client — OAuth2 authenticated endpoints.

Requires environment variables:
    REDDIT_CLIENT_ID     — from https://www.reddit.com/prefs/apps (script app)
    REDDIT_CLIENT_SECRET — secret for the same app
"""

import os
import time
from typing import Any

import backoff
import httpx
from loguru import logger

MAX_RETRY_TRIES = 5
MAX_RETRY_TIME = 60
RATE_LIMIT_DELAY = 6.0

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

USER_AGENT = "fandom_wiki_scraper:reddit/0.1 (educational project; respectful scraping)"

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
# OAuth2 tokens are valid for 1 hour; refresh 60 s early to be safe.
_TOKEN_LIFETIME = 3600 - 60


def _giveup_on_status(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in _RETRYABLE_STATUS_CODES
    return False


def _log_backoff(details: dict[str, Any]) -> None:
    logger.warning(
        "Backoff attempt={tries} wait={wait:.1f}s",
        **details,
    )


def _load_credentials() -> tuple[str, str]:
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set. "
            "Register a 'script' app at https://www.reddit.com/prefs/apps"
        )
    return client_id, client_secret


class RedditFetcher:
    """Fetches Reddit data via OAuth2-authenticated API endpoints.

    Usage as a context manager:

        with RedditFetcher() as fetcher:
            listings, after = fetcher.search_threads()
    """

    SEARCH_URL = "https://oauth.reddit.com/r/OnePiece/search"
    COMMENTS_URL = "https://oauth.reddit.com/r/OnePiece/comments/{thread_id}"
    MORE_CHILDREN_URL = "https://oauth.reddit.com/api/morechildren"

    SEARCH_QUERY = (
        'flair:"Theory" OR flair:"Analysis" OR flair:"Discussion" OR flair:"Big News"'
    )

    def __init__(self, delay: float = RATE_LIMIT_DELAY) -> None:
        self._delay = delay
        self._client_id, self._client_secret = _load_credentials()
        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": USER_AGENT},
        )

    def _ensure_token(self) -> None:
        """Fetch or refresh the OAuth2 bearer token if expired."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return
        resp = self._client.post(
            _TOKEN_URL,
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        payload = resp.json()
        self._access_token = payload["access_token"]
        self._token_expiry = time.monotonic() + _TOKEN_LIFETIME
        self._client.headers["Authorization"] = f"bearer {self._access_token}"
        logger.debug("Reddit OAuth2 token acquired.")

    def __enter__(self) -> "RedditFetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_threads(
        self,
        time_range: str = "week",
        after: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Search r/OnePiece for target flairs.

        Returns (list_of_listing_dicts, next_after_cursor).
        next_after_cursor is None when there are no more pages.
        """
        return self._search_with_retry(time_range, after)

    def fetch_thread(
        self,
        thread_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch a thread's post data and full comment tree.

        Returns (post_data, comments_listing_children).
        """
        return self._fetch_thread_with_retry(thread_id)

    def fetch_more_children(
        self,
        link_id: str,
        children_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Expand 'more' comment stubs via /api/morechildren.

        Returns a flat list of comment 'things'.
        """
        return self._fetch_more_with_retry(link_id, children_ids)

    def fetch_thread_metadata(
        self,
        thread_id: str,
    ) -> dict[str, Any]:
        """Cheap metadata fetch (no comments) for refresh checks."""
        return self._fetch_metadata_with_retry(thread_id)

    # ------------------------------------------------------------------
    # Private helpers with retry
    # ------------------------------------------------------------------

    @backoff.on_exception(
        backoff.expo,
        exception=(httpx.HTTPStatusError, httpx.TransportError),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _search_with_retry(
        self,
        time_range: str,
        after: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        self._ensure_token()
        params: dict[str, str] = {
            "q": self.SEARCH_QUERY,
            "sort": "top",
            "t": time_range,
            "restrict_sr": "on",
            "limit": "100",
            "raw_json": "1",
        }
        if after:
            params["after"] = after

        time.sleep(self._delay)
        resp = self._client.get(self.SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        children = data.get("data", {}).get("children", [])
        listings = [c["data"] for c in children if c.get("kind") == "t3"]
        next_after = data.get("data", {}).get("after")
        return listings, next_after

    @backoff.on_exception(
        backoff.expo,
        exception=(httpx.HTTPStatusError, httpx.TransportError),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _fetch_thread_with_retry(
        self,
        thread_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self._ensure_token()
        url = self.COMMENTS_URL.format(thread_id=thread_id)
        params = {
            "limit": "500",
            "depth": "10",
            "sort": "top",
            "raw_json": "1",
        }
        time.sleep(self._delay)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        post_data = data[0]["data"]["children"][0]["data"]
        comments = data[1]["data"]["children"]
        return post_data, comments

    @backoff.on_exception(
        backoff.expo,
        exception=(httpx.HTTPStatusError, httpx.TransportError),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _fetch_more_with_retry(
        self,
        link_id: str,
        children_ids: list[str],
    ) -> list[dict[str, Any]]:
        self._ensure_token()
        params = {
            "api_type": "json",
            "link_id": link_id,
            "children": ",".join(children_ids),
            "sort": "top",
            "raw_json": "1",
        }
        time.sleep(self._delay)
        resp = self._client.get(self.MORE_CHILDREN_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        things = data.get("json", {}).get("data", {}).get("things", [])
        return things

    @backoff.on_exception(
        backoff.expo,
        exception=(httpx.HTTPStatusError, httpx.TransportError),
        max_tries=MAX_RETRY_TRIES,
        max_time=MAX_RETRY_TIME,
        giveup=_giveup_on_status,
        on_backoff=_log_backoff,
    )
    def _fetch_metadata_with_retry(
        self,
        thread_id: str,
    ) -> dict[str, Any]:
        self._ensure_token()
        url = self.COMMENTS_URL.format(thread_id=thread_id)
        params = {"limit": "0", "raw_json": "1"}
        time.sleep(self._delay)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data[0]["data"]["children"][0]["data"]
