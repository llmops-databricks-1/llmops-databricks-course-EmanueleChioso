"""Pydantic models for the Fandom Wiki scraper."""

from datetime import datetime
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FandomWikiPage(BaseModel):
    """Represents one scraped or discovered Fandom Wiki page.

    A page with scraped_at=None is a stub — it has been discovered via a
    link from another page but has not yet been fetched.

    html_file holds a relative path to the saved content HTML file on disk
    (e.g. "data/fandom_wiki/pages_html/42.html").
    md_file holds a relative path to the saved Markdown file on disk
    (e.g. "data/fandom_wiki/pages_md/42.md").
    Neither field stores HTML/Markdown content directly.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    url: str
    title: str = ""
    scraped_at: datetime | None = None
    html_file: str | None = None
    md_file: str | None = None
    media_files: list[str] = Field(default_factory=list)
    linked_page_ids: list[int] = Field(default_factory=list)
    linked_urls: list[str] = Field(default_factory=list)

    @field_validator("url", mode="before")
    @classmethod
    def normalise_url(cls, v: str) -> str:
        parsed = urlparse(v)
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
