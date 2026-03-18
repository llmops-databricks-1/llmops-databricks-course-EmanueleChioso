"""CLI entry point for the Fandom Wiki scraper.

Usage:
    # Seeded mode — scrape specific pages
    uv run python -m fandom_wiki_scraper.scraper.cli scrape \\
        https://onepiece.fandom.com/wiki/Chapter_1176 \\
        https://onepiece.fandom.com/wiki/List_of_Locations

    # BFS crawl from a start page
    uv run python -m fandom_wiki_scraper.scraper.cli crawl \\
        https://onepiece.fandom.com/wiki/One_Piece_Wiki \\
        --max_pages=200 --max_depth=2

    # Override data directory, request delay, and HTTP backend
    # backend choices: fallback (default), curl, api, playwright
    uv run python -m fandom_wiki_scraper.scraper.cli \\
        --data_dir=data/my_wiki --delay=2.0 --backend=api \\
        scrape https://onepiece.fandom.com/wiki/Chapter_1176
"""

import fire

from fandom_wiki_scraper.scraper.scraper import (
    DEFAULT_DATA_DIR,
    DEFAULT_DELAY,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_WORKERS,
    FandomWikiScraper,
)


class _CliScraper:
    """Thin CLI wrapper around FandomWikiScraper.

    Returns None from all methods so fire does not dump the result objects.
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        delay: float = DEFAULT_DELAY,
        backend: str = "fallback",
        workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._scraper = FandomWikiScraper(
            data_dir=data_dir, delay=delay, backend=backend, workers=workers
        )

    def scrape(self, *urls: str) -> None:
        """Scrape the given URLs and save to disk."""
        with self._scraper:
            self._scraper.scrape(*urls)

    def crawl(
        self,
        start_url: str,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        """BFS-crawl from start_url up to max_pages pages."""
        with self._scraper:
            self._scraper.crawl(start_url, max_pages=max_pages, max_depth=max_depth)

    def resume(
        self,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        """Resume from the earliest known-but-unscraped page in the CSV."""
        with self._scraper:
            self._scraper.resume(max_pages=max_pages, max_depth=max_depth)


def main() -> None:
    fire.Fire(_CliScraper)


if __name__ == "__main__":
    main()
