"""CSV-backed store for scraped Reddit threads."""

import csv
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from fandom_wiki_scraper.reddit.models import RedditThread

CSV_COLUMNS = [
    "id",
    "reddit_id",
    "url",
    "title",
    "author",
    "flair",
    "score",
    "upvote_ratio",
    "num_comments",
    "num_awards",
    "created_utc",
    "scraped_at",
    "html_file",
    "md_file",
    "linked_thread_ids",
    "linked_thread_urls",
    "discovery_source",
    "from_link",
]


class CsvStore:
    """Manages threads.csv and the on-disk directory structure.

    In-memory dict[str, RedditThread] keyed by reddit_id.
    All mutations are in-memory; save() flushes atomically.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._html_dir = base_dir / "threads_html"
        self._md_dir = base_dir / "threads_md"
        self._csv_path = base_dir / "threads.csv"

        base_dir.mkdir(parents=True, exist_ok=True)
        self._html_dir.mkdir(exist_ok=True)
        self._md_dir.mkdir(exist_ok=True)

        self._threads: dict[str, RedditThread] = self.load()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict[str, RedditThread]:
        """Read threads.csv into memory."""
        if not self._csv_path.exists():
            return {}

        threads: dict[str, RedditThread] = {}
        with self._csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    thread = self._deserialise_row(row)
                    threads[thread.reddit_id] = thread
                except Exception as exc:
                    logger.warning(
                        "Skipping malformed CSV row reddit_id={} error={}",
                        row.get("reddit_id", "?"),
                        exc,
                    )
        return threads

    def save(self) -> None:
        """Atomically flush all threads to threads.csv."""
        tmp_path = self._csv_path.with_suffix(".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=CSV_COLUMNS,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for thread in self._threads.values():
                writer.writerow(self._serialise_row(thread))
        tmp_path.replace(self._csv_path)

    def register_thread(
        self,
        reddit_id: str,
        url: str,
        **listing_fields: object,
    ) -> RedditThread:
        """Return existing thread or create a stub.

        listing_fields are merged into the stub (title, score, etc.).
        If the thread already exists, listing engagement fields are
        updated (score, upvote_ratio, num_comments, num_awards) but
        scraped_at and file paths are preserved.
        """
        with self._lock:
            existing = self._threads.get(reddit_id)
            if existing is not None:
                engagement = {
                    k: v
                    for k, v in listing_fields.items()
                    if k
                    in {
                        "score",
                        "upvote_ratio",
                        "num_comments",
                        "num_awards",
                    }
                    and v is not None
                }
                if engagement:
                    updated = existing.model_copy(update=engagement)
                    self._threads[reddit_id] = updated
                    return updated
                return existing

            thread = RedditThread(
                id=self._next_id_unsafe(),
                reddit_id=reddit_id,
                url=url,
                **listing_fields,  # type: ignore[arg-type]
            )
            self._threads[reddit_id] = thread
            return thread

    def mark_scraped(self, thread: RedditThread) -> None:
        """Replace the in-memory entry with the fully-scraped thread."""
        with self._lock:
            self._threads[thread.reddit_id] = thread

    def get_by_reddit_id(self, reddit_id: str) -> RedditThread | None:
        return self._threads.get(reddit_id)

    def get_by_id(self, thread_id: int) -> RedditThread | None:
        for t in self._threads.values():
            if t.id == thread_id:
                return t
        return None

    def all_threads(self) -> list[RedditThread]:
        return list(self._threads.values())

    def unscraped_threads(self) -> list[RedditThread]:
        return [t for t in self._threads.values() if t.scraped_at is None]

    @property
    def threads(self) -> dict[str, RedditThread]:
        return self._threads

    # ------------------------------------------------------------------
    # ID helpers
    # ------------------------------------------------------------------

    def _next_id_unsafe(self) -> int:
        if not self._threads:
            return 1
        return max(t.id for t in self._threads.values()) + 1

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_row(thread: RedditThread) -> dict[str, str]:
        return {
            "id": str(thread.id),
            "reddit_id": thread.reddit_id,
            "url": thread.url,
            "title": thread.title,
            "author": thread.author,
            "flair": thread.flair or "",
            "score": str(thread.score),
            "upvote_ratio": str(thread.upvote_ratio),
            "num_comments": str(thread.num_comments),
            "num_awards": str(thread.num_awards),
            "created_utc": (thread.created_utc.isoformat() if thread.created_utc else ""),
            "scraped_at": (thread.scraped_at.isoformat() if thread.scraped_at else ""),
            "html_file": thread.html_file or "",
            "md_file": thread.md_file or "",
            "linked_thread_ids": json.dumps(thread.linked_thread_ids),
            "linked_thread_urls": json.dumps(thread.linked_thread_urls),
            "discovery_source": thread.discovery_source,
            "from_link": str(thread.from_link),
        }

    @staticmethod
    def _deserialise_row(row: dict[str, str]) -> RedditThread:
        created_utc: datetime | None = None
        if row.get("created_utc"):
            created_utc = datetime.fromisoformat(row["created_utc"])
            if created_utc.tzinfo is None:
                created_utc = created_utc.replace(tzinfo=UTC)

        scraped_at: datetime | None = None
        if row.get("scraped_at"):
            scraped_at = datetime.fromisoformat(row["scraped_at"])
            if scraped_at.tzinfo is None:
                scraped_at = scraped_at.replace(tzinfo=UTC)

        return RedditThread(
            id=int(row["id"]),
            reddit_id=row["reddit_id"],
            url=row["url"],
            title=row.get("title", ""),
            author=row.get("author", ""),
            flair=row.get("flair") or None,
            score=int(row.get("score", 0)),
            upvote_ratio=float(row.get("upvote_ratio", 0.0)),
            num_comments=int(row.get("num_comments", 0)),
            num_awards=int(row.get("num_awards", 0)),
            created_utc=created_utc,
            scraped_at=scraped_at,
            html_file=row.get("html_file") or None,
            md_file=row.get("md_file") or None,
            linked_thread_ids=json.loads(
                row.get("linked_thread_ids") or "[]",
            ),
            linked_thread_urls=json.loads(
                row.get("linked_thread_urls") or "[]",
            ),
            discovery_source=row.get("discovery_source", "manual"),
            from_link=row.get("from_link", "False").lower() == "true",
        )
