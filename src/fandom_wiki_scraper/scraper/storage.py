"""CSV-backed store for scraped Fandom Wiki pages."""

import csv
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from fandom_wiki_scraper.scraper.models import FandomWikiPage

CSV_COLUMNS = [
    "id",
    "url",
    "title",
    "scraped_at",
    "html_file",
    "md_file",
    "media_files",
    "linked_page_ids",
    "linked_urls",
]


class CsvStore:
    """Manages pages.csv and the on-disk directory structure.

    Keeps an in-memory dict[str, FandomWikiPage] keyed by normalised URL.
    All mutations are in-memory; save() is the only flush point and writes
    atomically (temp file + rename).
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._html_dir = base_dir / "pages_html"
        self._md_dir = base_dir / "pages_md"
        self._media_dir = base_dir / "media"
        self._csv_path = base_dir / "pages.csv"

        base_dir.mkdir(parents=True, exist_ok=True)
        self._html_dir.mkdir(exist_ok=True)
        self._md_dir.mkdir(exist_ok=True)
        self._media_dir.mkdir(exist_ok=True)

        self._pages: dict[str, FandomWikiPage] = self.load()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict[str, FandomWikiPage]:
        """Read pages.csv into memory. Returns {} if the file does not exist."""
        if not self._csv_path.exists():
            return {}

        pages: dict[str, FandomWikiPage] = {}
        with self._csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    page = self._deserialise_row(row)
                    pages[page.url] = page
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed CSV row url={} error={}",
                        row.get("url", "?"),
                        exc,
                    )
        return pages

    def save(self, pages: dict[str, FandomWikiPage]) -> None:
        """Atomically flush all pages to pages.csv."""
        tmp_path = self._csv_path.with_suffix(".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=CSV_COLUMNS,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for page in pages.values():
                writer.writerow(self._serialise_row(page))
        tmp_path.replace(self._csv_path)

    def register_url(self, url: str) -> FandomWikiPage:
        """Return the existing page for url, or create and register a stub.

        Does NOT flush to disk — caller must call save() when ready.
        Thread-safe: the check-then-insert is atomic under _lock.
        """
        with self._lock:
            if url in self._pages:
                return self._pages[url]
            page = FandomWikiPage(id=self._next_id_unsafe(), url=url)
            self._pages[url] = page
            return page

    def mark_scraped(self, page: FandomWikiPage) -> None:
        """Replace the in-memory entry with the fully-scraped page."""
        with self._lock:
            self._pages[page.url] = page

    def next_id(self) -> int:
        """Return max(existing ids) + 1, or 1 if the store is empty."""
        with self._lock:
            return self._next_id_unsafe()

    def _next_id_unsafe(self) -> int:
        """Compute next id without acquiring _lock — only call while holding it."""
        if not self._pages:
            return 1
        return max(p.id for p in self._pages.values()) + 1

    def get_by_url(self, url: str) -> FandomWikiPage | None:
        return self._pages.get(url)

    def get_by_id(self, page_id: int) -> FandomWikiPage | None:
        for page in self._pages.values():
            if page.id == page_id:
                return page
        return None

    def all_pages(self) -> list[FandomWikiPage]:
        return list(self._pages.values())

    def unscraped_pages(self) -> list[FandomWikiPage]:
        return [p for p in self._pages.values() if p.scraped_at is None]

    @property
    def pages(self) -> dict[str, FandomWikiPage]:
        return self._pages

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_row(page: FandomWikiPage) -> dict[str, str]:
        return {
            "id": str(page.id),
            "url": page.url,
            "title": page.title,
            "scraped_at": page.scraped_at.isoformat() if page.scraped_at else "",
            "html_file": page.html_file or "",
            "md_file": page.md_file or "",
            "media_files": json.dumps(page.media_files),
            "linked_page_ids": json.dumps(page.linked_page_ids),
            "linked_urls": json.dumps(page.linked_urls),
        }

    @staticmethod
    def _deserialise_row(row: dict[str, str]) -> FandomWikiPage:
        scraped_at: datetime | None = None
        if row.get("scraped_at"):
            scraped_at = datetime.fromisoformat(row["scraped_at"])
            if scraped_at.tzinfo is None:
                scraped_at = scraped_at.replace(tzinfo=UTC)

        # Support old CSVs that used "content_file" before the rename
        html_file = row.get("html_file") or row.get("content_file") or None

        return FandomWikiPage(
            id=int(row["id"]),
            url=row["url"],
            title=row.get("title", ""),
            scraped_at=scraped_at,
            html_file=html_file or None,
            md_file=row.get("md_file") or None,
            media_files=json.loads(row.get("media_files") or "[]"),
            linked_page_ids=json.loads(row.get("linked_page_ids") or "[]"),
            linked_urls=json.loads(row.get("linked_urls") or "[]"),
        )
