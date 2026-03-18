"""CLI entry point for the Reddit r/OnePiece scraper.

Usage:
    # Weekly discovery + scrape
    uv run python -m fandom_wiki_scraper.reddit weekly

    # Limit time ranges
    uv run python -m fandom_wiki_scraper.reddit weekly --time_ranges=week,month

    # Refresh non-archived threads
    uv run python -m fandom_wiki_scraper.reddit refresh

    # Scrape specific thread URLs
    uv run python -m fandom_wiki_scraper.reddit scrape \\
        https://www.reddit.com/r/OnePiece/comments/abc123/some_title/

    # Override data directory and delay
    uv run python -m fandom_wiki_scraper.reddit \\
        --data_dir=data/custom --delay=3.0 weekly
"""

import fire

from fandom_wiki_scraper.reddit.scraper import (
    DEFAULT_DATA_DIR,
    DEFAULT_DELAY,
    RedditScraper,
)


class _CliReddit:
    """Thin CLI wrapper around RedditScraper.

    Returns None from all methods so fire does not dump result objects.
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        delay: float = DEFAULT_DELAY,
    ) -> None:
        self._scraper = RedditScraper(
            data_dir=data_dir,
            delay=delay,
        )

    def weekly(
        self,
        time_ranges: str = "week,month,year",
    ) -> None:
        """Discover and scrape threads from search results."""
        ranges = tuple(t.strip() for t in time_ranges.split(","))
        with self._scraper:
            self._scraper.weekly(time_ranges=ranges)

    def refresh(self) -> None:
        """Re-check and re-scrape threads with significant changes."""
        with self._scraper:
            self._scraper.refresh()

    def scrape(self, *urls: str) -> None:
        """Scrape specific thread URLs."""
        with self._scraper:
            self._scraper.scrape(*urls)


def main() -> None:
    fire.Fire(_CliReddit)


if __name__ == "__main__":
    main()
