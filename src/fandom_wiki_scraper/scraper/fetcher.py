"""BaseFetcher ABC, FallbackFetcher, and create_fetcher() factory."""

from abc import ABC, abstractmethod
from typing import Literal

from loguru import logger

DEFAULT_BACKEND = "api"


class BaseFetcher(ABC):
    """Abstract base for all HTTP fetcher backends."""

    @property
    @abstractmethod
    def source_type(self) -> Literal["dom", "content"]:
        """Whether fetch_html returns a full DOM or scoped content HTML."""
        ...

    @abstractmethod
    def fetch_html(self, url: str) -> str: ...

    @abstractmethod
    def fetch_binary(self, url: str) -> bytes: ...

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL may be fetched. Default: permissive."""
        return True

    def __enter__(self) -> "BaseFetcher":
        return self

    def __exit__(self, *args: object) -> None:  # noqa: B027
        pass


class FallbackFetcher(BaseFetcher):
    """Tries backends in order: curl → api → playwright.

    On any transient failure, logs a warning and advances to the next
    backend. PermissionError (robots.txt) and FileNotFoundError (404) are
    re-raised immediately — no backend switch would help.

    Playwright is included in the chain but its browser is only started
    inside __enter__, so missing playwright packages are caught gracefully.
    """

    def __init__(self, delay: float = 1.5) -> None:
        self._delay = delay
        self._backends: list[BaseFetcher] = []
        self._active_source_type: Literal["dom", "content"] = "dom"

    @property
    def source_type(self) -> Literal["dom", "content"]:
        return self._active_source_type

    def __enter__(self) -> "FallbackFetcher":
        from fandom_wiki_scraper.scraper.fetcher_api import MediaWikiFetcher
        from fandom_wiki_scraper.scraper.fetcher_curl import CurlCffiFetcher

        candidates: list[BaseFetcher] = [
            MediaWikiFetcher(delay=self._delay),
            CurlCffiFetcher(delay=self._delay),
        ]
        for backend in candidates:
            try:
                backend.__enter__()
                self._backends.append(backend)
            except Exception as exc:
                logger.warning(
                    "Could not start backend {}: {} — skipping",
                    type(backend).__name__,
                    exc,
                )
        if not self._backends:
            raise RuntimeError("No fetcher backends could be initialised")
        return self

    def __exit__(self, *args: object) -> None:
        for backend in self._backends:
            backend.__exit__(*args)

    def fetch_html(self, url: str) -> str:
        for backend in self._backends:
            try:
                html = backend.fetch_html(url)
                self._active_source_type = backend.source_type
                return html
            except (PermissionError, FileNotFoundError):
                raise
            except Exception as exc:
                logger.warning(
                    "Backend {} failed for {} — trying next. error={}",
                    type(backend).__name__,
                    url,
                    exc,
                )
        raise RuntimeError(f"All backends exhausted for {url}")

    def fetch_binary(self, url: str) -> bytes:
        for backend in self._backends:
            try:
                return backend.fetch_binary(url)
            except Exception as exc:
                logger.warning(
                    "Backend {} failed binary {} — trying next. error={}",
                    type(backend).__name__,
                    url,
                    exc,
                )
        raise RuntimeError(f"All backends exhausted for binary {url}")

    def is_allowed(self, url: str) -> bool:
        return self._backends[0].is_allowed(url) if self._backends else True


def create_fetcher(backend: str, delay: float = 1.5) -> BaseFetcher:
    """Instantiate the requested backend fetcher.

    backend choices: "fallback" | "curl" | "api" | "playwright"
    """
    if backend == "fallback":
        return FallbackFetcher(delay=delay)
    elif backend == "curl":
        from fandom_wiki_scraper.scraper.fetcher_curl import CurlCffiFetcher

        return CurlCffiFetcher(delay=delay)
    elif backend == "api":
        from fandom_wiki_scraper.scraper.fetcher_api import MediaWikiFetcher

        return MediaWikiFetcher(delay=delay)
    else:
        raise ValueError(
            f"Unknown backend: {backend!r}. Valid choices: fallback, curl, api"
        )
