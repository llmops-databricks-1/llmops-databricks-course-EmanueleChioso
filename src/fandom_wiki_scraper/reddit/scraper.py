"""RedditScraper — orchestrates weekly discovery, smart refresh, and scraping."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from fandom_wiki_scraper.reddit.fetcher import RedditFetcher
from fandom_wiki_scraper.reddit.models import RedditComment, RedditThread
from fandom_wiki_scraper.reddit.parser import RedditParser
from fandom_wiki_scraper.reddit.storage import CsvStore

DEFAULT_DATA_DIR = "data/reddit_onepiece"
DEFAULT_DELAY = 6.0

SCORE_CHANGE_PCT = 0.20
SCORE_CHANGE_ABS = 50
COMMENT_CHANGE_PCT = 0.20
COMMENT_CHANGE_ABS = 10
ARCHIVE_AGE_DAYS = 180


class RedditScraper:
    """Scrape r/OnePiece threads to disk with a CSV index.

    For each thread, two files are written:
    - threads_html/<id>.html — rendered self-contained HTML
    - threads_md/<id>.md    — Markdown conversion

    Usage as a context manager:

        with RedditScraper() as scraper:
            scraper.weekly()
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        delay: float = DEFAULT_DELAY,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._store = CsvStore(self._data_dir)
        self._fetcher = RedditFetcher(delay=delay)
        self._parser = RedditParser()

    def __enter__(self) -> "RedditScraper":
        self._fetcher.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._fetcher.__exit__(*args)

    # ------------------------------------------------------------------
    # Public run modes
    # ------------------------------------------------------------------

    def weekly(
        self,
        time_ranges: tuple[str, ...] = ("week", "month", "year"),
    ) -> list[RedditThread]:
        """Main job: discover, filter, scrape, chase links.

        1. Paginate search for each time range, deduplicate
        2. Register all with listing engagement fields
        3. Filter by needs_rescrape()
        4. Scrape qualifying threads
        5. Extract and register linked threads
        6. Chase links 1 level deep
        7. Save CSV
        """
        discovered = self._discover_threads(time_ranges)
        logger.info(
            "Discovered {} unique threads across time ranges: {}.",
            len(discovered),
            ", ".join(time_ranges),
        )

        registered: list[RedditThread] = []
        for fields in discovered.values():
            thread = self._store.register_thread(
                reddit_id=fields["reddit_id"],
                url=fields["url"],
                title=fields.get("title", ""),
                author=fields.get("author", ""),
                flair=fields.get("flair"),
                score=fields.get("score", 0),
                upvote_ratio=fields.get("upvote_ratio", 0.0),
                num_comments=fields.get("num_comments", 0),
                num_awards=fields.get("num_awards", 0),
                created_utc=fields.get("created_utc"),
                discovery_source=fields.get(
                    "discovery_source",
                    "search:week",
                ),
            )
            registered.append(thread)

        to_scrape = [
            t
            for t in registered
            if self.needs_rescrape(t, discovered.get(t.reddit_id, {}))
        ]
        logger.info(
            "{}/{} threads qualify for scraping.",
            len(to_scrape),
            len(registered),
        )

        scraped: list[RedditThread] = []
        for thread in to_scrape:
            result = self._scrape_one(thread.reddit_id)
            if result is not None and result.scraped_at is not None:
                scraped.append(result)

        self._chase_links(scraped)
        self._store.save()
        logger.info(
            "Weekly done. {}/{} threads scraped.",
            len(scraped),
            len(to_scrape),
        )
        return scraped

    def refresh(self) -> list[RedditThread]:
        """Re-check non-archived threads and re-scrape changed ones."""
        now = datetime.now(UTC)
        archive_cutoff = now - timedelta(days=ARCHIVE_AGE_DAYS)
        candidates = [
            t
            for t in self._store.all_threads()
            if t.scraped_at is not None
            and t.created_utc is not None
            and t.created_utc > archive_cutoff
        ]
        logger.info(
            "Refresh: checking {} non-archived threads.",
            len(candidates),
        )

        scraped: list[RedditThread] = []
        for thread in candidates:
            try:
                meta = self._fetcher.fetch_thread_metadata(
                    thread.reddit_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch metadata for {}: {}",
                    thread.reddit_id,
                    exc,
                )
                continue

            fresh = self._parser.parse_listing(meta)
            if self.needs_rescrape(thread, fresh):
                result = self._scrape_one(thread.reddit_id)
                if result is not None and result.scraped_at is not None:
                    scraped.append(result)

        self._store.save()
        logger.info(
            "Refresh done. {}/{} threads re-scraped.",
            len(scraped),
            len(candidates),
        )
        return scraped

    def scrape(self, *urls: str) -> list[RedditThread]:
        """Manually scrape specific thread URLs."""
        logger.info("Scraping {} URL(s).", len(urls))
        scraped: list[RedditThread] = []

        for url in urls:
            thread_id = self._parser.extract_thread_id(url)
            if not thread_id:
                logger.warning("Cannot extract thread ID from: {}", url)
                continue

            self._store.register_thread(
                reddit_id=thread_id,
                url=url,
                discovery_source="manual",
            )
            result = self._scrape_one(thread_id)
            if result is not None and result.scraped_at is not None:
                scraped.append(result)

        self._store.save()
        logger.info(
            "Done. {}/{} URL(s) scraped.",
            len(scraped),
            len(urls),
        )
        return scraped

    # ------------------------------------------------------------------
    # Smart update logic
    # ------------------------------------------------------------------

    @staticmethod
    def needs_rescrape(
        stored: RedditThread,
        fresh: dict[str, Any],
    ) -> bool:
        """Decide whether a thread needs re-scraping.

        Rules (in order):
        1. Archived (>6 months) -> never
        2. Never scraped -> always
        3. Score delta exceeds threshold -> yes
        4. Comment delta exceeds threshold -> yes
        5. Otherwise -> no
        """
        now = datetime.now(UTC)
        if (
            stored.created_utc is not None
            and (now - stored.created_utc).days > ARCHIVE_AGE_DAYS
        ):
            return False

        if stored.scraped_at is None:
            return True

        fresh_score = fresh.get("score", stored.score)
        score_delta = abs(fresh_score - stored.score)
        score_threshold = min(
            abs(stored.score * SCORE_CHANGE_PCT),
            SCORE_CHANGE_ABS,
        )
        if score_delta >= max(score_threshold, 1):
            return True

        fresh_comments = fresh.get(
            "num_comments",
            stored.num_comments,
        )
        comment_delta = abs(fresh_comments - stored.num_comments)
        comment_threshold = min(
            abs(stored.num_comments * COMMENT_CHANGE_PCT),
            COMMENT_CHANGE_ABS,
        )
        return comment_delta >= max(comment_threshold, 1)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _scrape_one(self, thread_id: str) -> RedditThread | None:
        """Fetch, parse, render, and persist a single thread."""
        logger.info("Fetching thread: {}", thread_id)
        try:
            post_data, comments_data = self._fetcher.fetch_thread(
                thread_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch thread {}: {}",
                thread_id,
                exc,
            )
            return None

        post_fields, comments = self._parser.parse_thread_json(
            post_data,
            comments_data,
        )

        comments = self._expand_more_comments(
            thread_id,
            post_data,
            comments,
        )

        thread_html = self._parser.render_thread_html(
            post_fields,
            comments,
        )
        thread_md = self._parser.thread_html_to_markdown(thread_html)

        existing = self._store.get_by_reddit_id(thread_id)
        local_id = existing.id if existing else 1

        html_file = self._save_html(local_id, thread_html)
        md_file = self._save_md(local_id, thread_md)

        linked_urls = self._parser.extract_thread_links(
            post_fields.get("selftext_html", ""),
            comments,
        )
        self_url = f"https://www.reddit.com/r/OnePiece/comments/{thread_id}"
        linked_urls = [u for u in linked_urls if u != self_url]

        linked_ids = self._register_linked_threads(
            linked_urls,
            thread_id,
        )

        update_fields: dict[str, Any] = {
            **post_fields,
            "scraped_at": datetime.now(UTC),
            "html_file": html_file,
            "md_file": md_file,
            "linked_thread_ids": linked_ids,
            "linked_thread_urls": linked_urls,
        }
        update_fields.pop("selftext_html", None)

        updated = RedditThread(
            id=local_id,
            **update_fields,
        )
        self._store.mark_scraped(updated)
        logger.info(
            "Scraped '{}' — {} comments, {} linked threads | {}",
            updated.title,
            updated.num_comments,
            len(linked_urls),
            thread_id,
        )
        return updated

    def _expand_more_comments(
        self,
        thread_id: str,
        post_data: dict[str, Any],
        comments: list[RedditComment],
    ) -> list[RedditComment]:
        """Expand 'more' stubs in the comment tree (1 level)."""
        link_id = f"t3_{thread_id}"
        expanded: list[RedditComment] = []

        for comment in comments:
            if comment.is_more and comment.more_children_ids:
                try:
                    things = self._fetcher.fetch_more_children(
                        link_id,
                        comment.more_children_ids[:100],
                    )
                    for thing in things:
                        expanded.append(
                            self._parser._parse_comment(thing),
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to expand more children for {}: {}",
                        thread_id,
                        exc,
                    )
            else:
                expanded.append(comment)

        return expanded

    def _discover_threads(
        self,
        time_ranges: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        """Paginate search across time ranges, deduplicate by reddit_id."""
        all_threads: dict[str, dict[str, Any]] = {}

        for time_range in time_ranges:
            after: str | None = None
            page_num = 0
            while True:
                listings, next_after = self._fetcher.search_threads(
                    time_range=time_range,
                    after=after,
                )
                if not listings:
                    break

                for item in listings:
                    fields = self._parser.parse_listing(item)
                    rid = fields["reddit_id"]
                    fields["discovery_source"] = f"search:{time_range}"
                    if rid not in all_threads:
                        all_threads[rid] = fields

                page_num += 1
                logger.debug(
                    "Search t={} page={} got {} results.",
                    time_range,
                    page_num,
                    len(listings),
                )

                if not next_after:
                    break
                after = next_after

        return all_threads

    def _chase_links(
        self,
        newly_scraped: list[RedditThread],
    ) -> None:
        """Scrape linked threads 1 level deep (no recursion)."""
        stubs_to_chase: list[str] = []
        for thread in newly_scraped:
            for url in thread.linked_thread_urls:
                tid = self._parser.extract_thread_id(url)
                if not tid:
                    continue
                existing = self._store.get_by_reddit_id(tid)
                if existing is None or (
                    existing.scraped_at is None and not existing.from_link
                ):
                    stubs_to_chase.append(tid)

        if not stubs_to_chase:
            return

        seen: set[str] = set()
        unique: list[str] = []
        for tid in stubs_to_chase:
            if tid not in seen:
                seen.add(tid)
                unique.append(tid)

        logger.info("Chasing {} linked threads.", len(unique))
        for tid in unique:
            self._scrape_one(tid)

    def _register_linked_threads(
        self,
        linked_urls: list[str],
        source_thread_id: str,
    ) -> list[int]:
        """Register linked threads as stubs and return their local IDs."""
        ids: list[int] = []
        for url in linked_urls:
            tid = self._parser.extract_thread_id(url)
            if not tid or tid == source_thread_id:
                continue
            stub = self._store.register_thread(
                reddit_id=tid,
                url=url,
                discovery_source="link",
                from_link=True,
            )
            ids.append(stub.id)
        return ids

    def _save_html(self, thread_id: int, content: str) -> str:
        dest = self._data_dir / "threads_html" / f"{thread_id}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return str(dest)

    def _save_md(self, thread_id: int, content: str) -> str:
        dest = self._data_dir / "threads_md" / f"{thread_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return str(dest)
