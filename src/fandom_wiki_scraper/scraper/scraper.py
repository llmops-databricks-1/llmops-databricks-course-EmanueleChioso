"""FandomWikiScraper — orchestrates seeded and BFS-crawl scraping sessions."""

import queue
import threading
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from fandom_wiki_scraper.scraper.fetcher import BaseFetcher, create_fetcher
from fandom_wiki_scraper.scraper.models import FandomWikiPage
from fandom_wiki_scraper.scraper.parser import PageParser
from fandom_wiki_scraper.scraper.storage import CsvStore

FANDOM_DOMAIN = "onepiece.fandom.com"
WIKI_PATH_PREFIX = "/wiki/"

DEFAULT_DATA_DIR = "data/fandom_wiki"
DEFAULT_DELAY = 1.5
DEFAULT_MAX_PAGES = 500
DEFAULT_MAX_DEPTH = 3
DEFAULT_WORKERS = 1


class FandomWikiScraper:
    """Scrape Fandom Wiki pages to disk with a CSV index.

    For each page, two files are written:
    - pages_html/<id>.html — cleaned content HTML (mw-parser-output body)
    - pages_md/<id>.md    — Markdown converted from the content HTML

    Usage as a context manager ensures the HTTP client is closed cleanly:

        with FandomWikiScraper() as scraper:
            scraper.scrape(["https://onepiece.fandom.com/wiki/Chapter_1176"])

    Or call scrape()/crawl() directly — the client is closed when the
    process ends regardless.
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        delay: float = DEFAULT_DELAY,
        backend: str = "fallback",  # "fallback" | "curl" | "api" | "playwright"
        workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._store = CsvStore(self._data_dir)
        self._fetcher = create_fetcher(backend, delay=delay)
        self._parser = PageParser()
        self._backend = backend
        self._delay = delay
        self._workers = workers

    def __enter__(self) -> "FandomWikiScraper":
        self._fetcher.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._fetcher.__exit__(*args)

    # ------------------------------------------------------------------
    # Public run modes
    # ------------------------------------------------------------------

    def scrape(self, *urls: str) -> list[FandomWikiPage]:
        """Seeded mode: scrape exactly the given URLs.

        Already-scraped pages are skipped (idempotent). All discovered
        internal links are registered as stubs in the CSV so they can be
        scraped later. The CSV is saved once after all URLs are processed.

        Returns the list of FandomWikiPage objects scraped in this call.
        """
        scraped: list[FandomWikiPage] = []
        pages = self._store.pages
        logger.info("Scraping {} URL(s) with {} worker(s).", len(urls), self._workers)

        if self._workers == 1:
            for raw_url in urls:
                url = PageParser.normalise_url(raw_url)
                if not self._is_internal(url):
                    logger.warning("Skipping non-internal URL: {}", url)
                    continue

                page = self._store.register_url(url)
                if page.scraped_at is not None:
                    logger.info("Already scraped, skipping: {}", url)
                    continue

                updated = self._scrape_one(page, pages)
                if updated.scraped_at is not None:
                    scraped.append(updated)
        else:

            def _worker(raw_url: str) -> FandomWikiPage | None:
                url = PageParser.normalise_url(raw_url)
                if not self._is_internal(url):
                    logger.warning("Skipping non-internal URL: {}", url)
                    return None
                page = self._store.register_url(url)
                if page.scraped_at is not None:
                    logger.info("Already scraped, skipping: {}", url)
                    return None
                fetcher = create_fetcher(self._backend, delay=self._delay)
                with fetcher:
                    return self._scrape_one(page, pages, fetcher=fetcher)

            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                for result in pool.map(_worker, urls):
                    if result is not None and result.scraped_at is not None:
                        scraped.append(result)

        self._store.save(pages)
        logger.info("Done. {}/{} URL(s) scraped.", len(scraped), len(urls))
        return scraped

    def crawl(
        self,
        start_url: str,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> list[FandomWikiPage]:
        """BFS crawl from start_url.

        Discovers and scrapes internal wiki links up to max_depth levels
        deep, stopping after max_pages pages have been scraped this call.
        The CSV is saved once at the end.

        Returns the list of FandomWikiPage objects scraped in this call.
        """
        pages = self._store.pages
        start = PageParser.normalise_url(start_url)
        if not self._is_internal(start):
            raise ValueError(f"start_url is not an internal wiki URL: {start_url}")

        scraped: list[FandomWikiPage] = []
        logger.info(
            "Starting BFS crawl from {} (max_pages={}, max_depth={}, workers={}).",
            start,
            max_pages,
            max_depth,
            self._workers,
        )

        if self._workers == 1:
            visited: set[str] = {u for u, p in pages.items() if p.scraped_at is not None}
            bfs_queue: deque[tuple[str, int]] = deque()
            bfs_queue.append((start, 0))

            while bfs_queue and len(scraped) < max_pages:
                url, depth = bfs_queue.popleft()
                if url in visited:
                    continue
                visited.add(url)

                page = self._store.register_url(url)
                if page.scraped_at is not None:
                    continue

                updated = self._scrape_one(page, pages)
                if updated.scraped_at is not None:
                    scraped.append(updated)

                    if depth < max_depth:
                        for linked_url in updated.linked_urls:
                            if linked_url not in visited:
                                bfs_queue.append((linked_url, depth + 1))
        else:
            visited_par: set[str] = {
                u for u, p in pages.items() if p.scraped_at is not None
            }
            visited_lock = threading.Lock()
            scraped_lock = threading.Lock()
            stop_event = threading.Event()
            work_queue: queue.Queue[tuple[str, int]] = queue.Queue()
            work_queue.put((start, 0))

            def _crawl_worker() -> None:
                fetcher = create_fetcher(self._backend, delay=self._delay)
                with fetcher:
                    while not stop_event.is_set():
                        try:
                            url, depth = work_queue.get(timeout=1.0)
                        except queue.Empty:
                            continue
                        try:
                            with visited_lock:
                                if url in visited_par:
                                    continue
                                visited_par.add(url)

                            with scraped_lock:
                                if len(scraped) >= max_pages:
                                    continue

                            page = self._store.register_url(url)
                            if page.scraped_at is not None:
                                continue

                            updated = self._scrape_one(page, pages, fetcher=fetcher)
                            if updated.scraped_at is not None:
                                with scraped_lock:
                                    scraped.append(updated)

                                if depth < max_depth:
                                    for linked_url in updated.linked_urls:
                                        with visited_lock:
                                            if linked_url not in visited_par:
                                                work_queue.put((linked_url, depth + 1))
                        finally:
                            work_queue.task_done()

            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                for _ in range(self._workers):
                    pool.submit(_crawl_worker)
                work_queue.join()
                stop_event.set()

        self._store.save(pages)
        stop_reason = (
            "max_pages limit reached" if len(scraped) >= max_pages else "queue exhausted"
        )
        logger.info("Done. {} page(s) scraped — {}.", len(scraped), stop_reason)
        return scraped

    def resume(
        self,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> list[FandomWikiPage]:
        """Resume scraping all known-but-unscraped pages.

        Seeds the BFS queue with every stub (scraped_at=None) in the CSV,
        ordered by id. If a page fails to fetch, the next stub in the queue
        is tried. New links discovered from successfully scraped pages are
        also followed up to max_depth.
        """
        pages = self._store.pages
        unscraped = sorted(self._store.unscraped_pages(), key=lambda p: p.id)
        if not unscraped:
            logger.info("No unscraped pages found — nothing to resume.")
            return []

        logger.info(
            "Resuming with {} unscraped stub(s) as seeds (max_pages={}, max_depth={}).",
            len(unscraped),
            max_pages,
            max_depth,
        )

        scraped: list[FandomWikiPage] = []
        visited: set[str] = {u for u, p in pages.items() if p.scraped_at is not None}
        bfs_queue: deque[tuple[str, int]] = deque((p.url, 0) for p in unscraped)

        while bfs_queue and len(scraped) < max_pages:
            url, depth = bfs_queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            page = self._store.register_url(url)
            if page.scraped_at is not None:
                continue

            updated = self._scrape_one(page, pages)
            if updated.scraped_at is not None:
                scraped.append(updated)

                if depth < max_depth:
                    for linked_url in updated.linked_urls:
                        if linked_url not in visited:
                            bfs_queue.append((linked_url, depth + 1))

        self._store.save(pages)
        stop_reason = (
            "max_pages limit reached" if len(scraped) >= max_pages else "queue exhausted"
        )
        logger.info("Done. {} page(s) scraped — {}.", len(scraped), stop_reason)
        return scraped

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _scrape_one(
        self,
        page: FandomWikiPage,
        pages: dict[str, FandomWikiPage],
        fetcher: BaseFetcher | None = None,
    ) -> FandomWikiPage:
        """Fetch, parse, and persist a single page. Returns updated page."""
        f = fetcher if fetcher is not None else self._fetcher
        logger.info("Fetching: {}", page.url)
        try:
            html = f.fetch_html(page.url)
        except PermissionError as exc:
            logger.warning("Blocked by robots.txt: {} — {}", page.url, exc)
            return page
        except FileNotFoundError as exc:
            logger.warning("404 for {}: {}", page.url, exc)
            return page
        except Exception as exc:
            logger.warning("Failed to fetch {}: {}", page.url, exc)
            return page

        # Extract title/links/media from the original HTML (may be full DOM or content).
        # PageParser scopes internally to mw-parser-output so both work.
        title = self._parser.extract_title(html, page.url)
        internal_links = self._parser.extract_internal_links(html)
        media_urls = self._parser.extract_media_urls(html)

        # Derive clean content HTML: strip down to article body if we have a full DOM.
        if f.source_type == "dom":
            content_html = self._parser.extract_content_html(html)
        else:
            content_html = html

        markdown = self._parser.html_to_markdown(content_html)

        html_file = self._save_html_page(page.id, content_html)
        md_file = self._save_md_page(page.id, markdown)
        media_files = self._download_media(page.id, media_urls, fetcher=f)
        linked_page_ids, linked_urls = self._resolve_links(internal_links, pages)

        updated = FandomWikiPage(
            id=page.id,
            url=page.url,
            title=title,
            scraped_at=datetime.now(UTC),
            html_file=html_file,
            md_file=md_file,
            media_files=media_files,
            linked_page_ids=linked_page_ids,
            linked_urls=linked_urls,
        )
        self._store.mark_scraped(updated)
        logger.info(
            "Scraped '{}' — {} links, {} media files | {}",
            title,
            len(internal_links),
            len(media_files),
            page.url,
        )
        return updated

    def _save_html_page(self, page_id: int, content_html: str) -> str:
        """Write content HTML to pages_html/<id>.html and return the path."""
        dest = self._data_dir / "pages_html" / f"{page_id}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content_html, encoding="utf-8")
        return str(dest)

    def _save_md_page(self, page_id: int, markdown: str) -> str:
        """Write Markdown to pages_md/<id>.md and return the path."""
        dest = self._data_dir / "pages_md" / f"{page_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(markdown, encoding="utf-8")
        return str(dest)

    def _download_media(
        self,
        page_id: int,
        media_urls: list[str],
        fetcher: BaseFetcher | None = None,
    ) -> list[str]:
        """Download media files into media/<page_id>/ and return relative paths."""
        f = fetcher if fetcher is not None else self._fetcher
        media_dir = self._data_dir / "media" / str(page_id)
        media_dir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []
        for i, url in enumerate(media_urls):
            filename = self._media_filename(url, i)
            dest = media_dir / filename
            if dest.exists():
                saved.append(str(dest))
                continue
            try:
                data = f.fetch_binary(url)
                dest.write_bytes(data)
                saved.append(str(dest))
            except Exception as exc:
                logger.warning("Failed to download media {}: {}", url, exc)

        return saved

    def _resolve_links(
        self,
        discovered_urls: list[str],
        pages: dict[str, FandomWikiPage],
    ) -> tuple[list[int], list[str]]:
        """Register any new URLs as stubs and return (ids, urls)."""
        ids: list[int] = []
        urls: list[str] = []
        for url in discovered_urls:
            norm = PageParser.normalise_url(url)
            stub = self._store.register_url(norm)
            ids.append(stub.id)
            urls.append(norm)
        return ids, urls

    def _is_internal(self, url: str) -> bool:
        """Return True if url is a wiki content page on onepiece.fandom.com."""
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc == FANDOM_DOMAIN and parsed.path.startswith(WIKI_PATH_PREFIX)

    @staticmethod
    def _media_filename(url: str, index: int) -> str:
        """Derive a safe filename from a media URL."""
        path = urllib.parse.urlparse(url).path
        name = path.rstrip("/").split("/")[-1] or f"media_{index}"
        # Prefix with index to avoid collisions between different base URLs
        # that happen to share the same filename segment
        return f"{index}_{name}"
