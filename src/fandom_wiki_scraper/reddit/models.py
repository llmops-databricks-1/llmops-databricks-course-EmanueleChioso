"""Pydantic models for the Reddit r/OnePiece scraper."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RedditThread(BaseModel):
    """Represents one scraped or discovered Reddit thread.

    A thread with scraped_at=None is a stub — discovered via listing or
    link but not yet fully fetched (no comments / rendered HTML).

    html_file / md_file hold relative paths to rendered files on disk.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    reddit_id: str
    url: str
    title: str = ""
    author: str = ""
    flair: str | None = None
    score: int = 0
    upvote_ratio: float = 0.0
    num_comments: int = 0
    num_awards: int = 0
    created_utc: datetime | None = None
    scraped_at: datetime | None = None
    html_file: str | None = None
    md_file: str | None = None
    linked_thread_ids: list[int] = Field(default_factory=list)
    linked_thread_urls: list[str] = Field(default_factory=list)
    discovery_source: str = "manual"
    from_link: bool = False

    @field_validator("url", mode="before")
    @classmethod
    def normalise_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme == "" and parsed.netloc == "":
            v = f"https://www.reddit.com{v}" if v.startswith("/") else v
            parsed = urlparse(v)
        if parsed.netloc and "reddit.com" in parsed.netloc:
            parsed = parsed._replace(scheme="https", netloc="www.reddit.com")
        cleaned = parsed._replace(fragment="")
        path = cleaned.path.rstrip("/") or "/"
        cleaned = cleaned._replace(path=path)
        return urlunparse(cleaned)

    @field_validator("scraped_at", mode="after")
    @classmethod
    def require_timezone(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("scraped_at must be timezone-aware")
        return v

    @field_validator("created_utc", mode="after")
    @classmethod
    def require_timezone_created(
        cls,
        v: datetime | None,
    ) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("created_utc must be timezone-aware")
        return v


class RedditComment(BaseModel):
    """Transient model for a single Reddit comment (not persisted)."""

    model_config = ConfigDict(frozen=True)

    id: str
    author: str = "[deleted]"
    body_html: str = ""
    score: int = 0
    depth: int = 0
    replies: list[RedditComment] = Field(default_factory=list)
    is_more: bool = False
    more_children_ids: list[str] = Field(default_factory=list)
