"""Stateless parser for Reddit JSON data."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

from markdownify import ATX
from markdownify import markdownify as md

from fandom_wiki_scraper.reddit.models import RedditComment

TARGET_FLAIRS = frozenset({"Theory", "Analysis", "Discussion", "Big News"})

_THREAD_LINK_RE = re.compile(
    r"https?://(?:www\.)?reddit\.com/r/OnePiece/comments/([a-z0-9]+)",
    re.IGNORECASE,
)


class RedditParser:
    """Stateless parser for Reddit JSON responses.

    All methods are static — no instance state needed.
    """

    @staticmethod
    def parse_listing(data: dict[str, Any]) -> dict[str, Any]:
        """Extract normalised fields from a search result listing item."""
        created = datetime.fromtimestamp(
            data.get("created_utc", 0),
            tz=UTC,
        )
        permalink = data.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink else ""

        return {
            "reddit_id": data.get("id", ""),
            "url": url,
            "title": data.get("title", ""),
            "author": data.get("author", "[deleted]"),
            "flair": data.get("link_flair_text"),
            "score": data.get("score", 0),
            "upvote_ratio": data.get("upvote_ratio", 0.0),
            "num_comments": data.get("num_comments", 0),
            "num_awards": data.get("total_awards_received", 0),
            "created_utc": created,
        }

    @staticmethod
    def parse_thread_json(
        post_data: dict[str, Any],
        comments_data: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[RedditComment]]:
        """Parse a full thread response into post fields + comment tree."""
        post_fields = RedditParser.parse_listing(post_data)
        post_fields["selftext_html"] = post_data.get("selftext_html") or ""

        comments = [RedditParser._parse_comment(c) for c in comments_data]
        return post_fields, comments

    @staticmethod
    def render_thread_html(
        post: dict[str, Any],
        comments: list[RedditComment],
    ) -> str:
        """Render a self-contained HTML page for a thread."""
        title = html.escape(post.get("title", ""))
        author = html.escape(post.get("author", ""))
        flair = html.escape(post.get("flair") or "")
        score = post.get("score", 0)
        num_comments = post.get("num_comments", 0)
        created = post.get("created_utc", "")
        body_html = post.get("selftext_html") or ""

        flair_tag = f'<span class="flair">[{flair}]</span> ' if flair else ""
        meta = (
            f"<p>by u/{author} | {score} points | {num_comments} comments | {created}</p>"
        )

        comments_html = "\n".join(
            RedditParser._render_comment_html(c) for c in comments if not c.is_more
        )

        return (
            "<!DOCTYPE html>\n"
            "<html><head>"
            f"<title>{title}</title>"
            '<meta charset="utf-8">'
            "<style>"
            "body{font-family:sans-serif;max-width:800px;margin:auto;padding:1em}"
            ".comment{border-left:2px solid #ccc;padding-left:1em;margin:0.5em 0}"
            ".meta{color:#666;font-size:0.85em}"
            ".flair{background:#eee;padding:2px 6px;border-radius:3px}"
            "</style>"
            "</head><body>"
            f"<h1>{flair_tag}{title}</h1>"
            f'<div class="meta">{meta}</div>'
            f'<div class="post-body">{body_html}</div>'
            "<hr>"
            f'<div class="comments"><h2>Comments</h2>{comments_html}</div>'
            "</body></html>"
        )

    @staticmethod
    def thread_html_to_markdown(thread_html: str) -> str:
        """Convert rendered thread HTML to Markdown."""
        return md(thread_html, heading_style=ATX, strip=["style"])

    @staticmethod
    def extract_thread_links(
        post_body_html: str,
        comments: list[RedditComment],
    ) -> list[str]:
        """Find r/OnePiece thread URLs in post body + all comments."""
        seen: dict[str, None] = {}

        for match in _THREAD_LINK_RE.finditer(post_body_html):
            seen[_normalise_thread_url(match.group(0))] = None

        for comment in RedditParser._flatten_comments(comments):
            for match in _THREAD_LINK_RE.finditer(comment.body_html):
                seen[_normalise_thread_url(match.group(0))] = None

        return list(seen)

    @staticmethod
    def extract_thread_id(url: str) -> str | None:
        """Extract the Reddit thread ID from a URL."""
        match = _THREAD_LINK_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def matches_target_flair(flair: str | None) -> bool:
        """Check if flair matches one of the target flairs."""
        if not flair:
            return False
        return flair.strip() in TARGET_FLAIRS

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_comment(
        thing: dict[str, Any],
    ) -> RedditComment:
        """Recursively parse a comment 'thing' from Reddit JSON."""
        kind = thing.get("kind", "")
        data = thing.get("data", {})

        if kind == "more":
            return RedditComment(
                id=data.get("id", "more"),
                is_more=True,
                more_children_ids=data.get("children", []),
            )

        replies_raw = data.get("replies")
        replies: list[RedditComment] = []
        if isinstance(replies_raw, dict):
            children = replies_raw.get("data", {}).get("children", [])
            replies = [RedditParser._parse_comment(c) for c in children]

        return RedditComment(
            id=data.get("id", ""),
            author=data.get("author", "[deleted]"),
            body_html=data.get("body_html", ""),
            score=data.get("score", 0),
            depth=data.get("depth", 0),
            replies=replies,
        )

    @staticmethod
    def _render_comment_html(comment: RedditComment) -> str:
        """Render a single comment (with nested replies) to HTML."""
        author = html.escape(comment.author)
        replies_html = "\n".join(
            RedditParser._render_comment_html(r) for r in comment.replies if not r.is_more
        )
        return (
            f'<div class="comment" style="margin-left:{comment.depth}em">'
            f'<p class="meta">u/{author} | {comment.score} points</p>'
            f"<div>{comment.body_html}</div>"
            f"{replies_html}"
            "</div>"
        )

    @staticmethod
    def _flatten_comments(
        comments: list[RedditComment],
    ) -> list[RedditComment]:
        """Flatten a comment tree into a list."""
        result: list[RedditComment] = []
        for c in comments:
            result.append(c)
            if c.replies:
                result.extend(
                    RedditParser._flatten_comments(c.replies),
                )
        return result


def _normalise_thread_url(url: str) -> str:
    """Strip query/fragment and trailing path segments after thread ID."""
    match = _THREAD_LINK_RE.search(url)
    if match:
        thread_id = match.group(1)
        return f"https://www.reddit.com/r/OnePiece/comments/{thread_id}"
    return url
