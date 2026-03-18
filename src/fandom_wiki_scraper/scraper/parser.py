"""Stateless HTML parser for Fandom Wiki pages."""

import urllib.parse

from bs4 import BeautifulSoup
from markdownify import ATX
from markdownify import markdownify as md

FANDOM_BASE = "https://onepiece.fandom.com"
WIKI_PATH_PREFIX = "/wiki/"

# Administrative namespaces to skip — Category: is intentionally NOT here
# because category pages are valid content targets.
SKIP_NAMESPACES = frozenset(
    [
        "Special:",
        "File:",
        "Help:",
        "Talk:",
        "User:",
        "User_talk:",
        "Template:",
        "Template_talk:",
        "MediaWiki:",
        "Module:",
    ]
)


class PageParser:
    """Stateless parser for Fandom Wiki HTML.

    All methods are static — no instance state needed.
    Scopes extraction to div.mw-parser-output to avoid nav/sidebar noise.
    """

    @staticmethod
    def extract_title(html: str, url: str = "") -> str:
        """Extract the page title from HTML.

        Priority: h1.page-header__title → #firstHeading → first h1
        Fallback: URL-decode the last /wiki/<segment>.
        """
        soup = BeautifulSoup(html, "lxml")

        for selector in (
            lambda s: s.find("h1", class_="page-header__title"),
            lambda s: s.find(id="firstHeading"),
            lambda s: s.find("h1"),
        ):
            tag = selector(soup)
            if tag:
                return tag.get_text(strip=True)

        if url and WIKI_PATH_PREFIX in url:
            slug = url.split(WIKI_PATH_PREFIX)[-1]
            return urllib.parse.unquote(slug).replace("_", " ")

        return ""

    @staticmethod
    def extract_internal_links(html: str) -> list[str]:
        """Return deduplicated absolute Fandom Wiki URLs found in the content.

        Scoped to div.mw-parser-output. Skips administrative namespaces.
        Category: pages are kept. Preserves discovery order.
        """
        soup = BeautifulSoup(html, "lxml")
        content = (
            soup.find("div", class_="mw-parser-output") or soup.find("main") or soup.body
        )
        if content is None:
            return []

        seen: dict[str, None] = {}
        for tag in content.find_all("a", href=True):
            href: str = tag["href"]
            if not href.startswith(WIKI_PATH_PREFIX):
                continue

            page_name = href[len(WIKI_PATH_PREFIX) :]
            if any(page_name.startswith(ns) for ns in SKIP_NAMESPACES):
                continue

            # Strip fragment
            if "#" in href:
                href = href[: href.index("#")]

            full_url = FANDOM_BASE + href
            seen[full_url] = None

        return list(seen)

    @staticmethod
    def extract_media_urls(html: str) -> list[str]:
        """Return deduplicated absolute media URLs from the content div.

        Prefers data-src over src (Fandom lazy-loads images).
        Strips Fandom's /revision/latest/... query suffix to get canonical URLs.
        Skips data: URIs.
        """
        soup = BeautifulSoup(html, "lxml")
        content = (
            soup.find("div", class_="mw-parser-output") or soup.find("main") or soup.body
        )
        if content is None:
            return []

        seen: dict[str, None] = {}
        for img in content.find_all("img"):
            src: str = img.get("data-src") or img.get("src") or ""
            if not src or src.startswith("data:") or not src.startswith("https://"):
                continue

            # Strip /revision/latest/... suffix → canonical image URL
            if "/revision/" in src:
                src = src.split("/revision/")[0]

            seen[src] = None

        return list(seen)

    @staticmethod
    def extract_content_html(dom_html: str) -> str:
        """Extract the article body from a full DOM HTML string.

        Returns the outer HTML of div.mw-parser-output. Falls back to the
        full <body> if the element is not found (e.g. non-standard pages).
        """
        soup = BeautifulSoup(dom_html, "lxml")
        content = soup.find("div", class_="mw-parser-output")
        if content:
            return str(content)
        return str(soup.body) if soup.body else dom_html

    @staticmethod
    def html_to_markdown(content_html: str) -> str:
        """Convert content HTML to clean Markdown.

        Removes script/style/nav/aside noise before conversion.
        Uses ATX-style headings (# H1, ## H2, …).
        """
        soup = BeautifulSoup(content_html, "lxml")
        for tag in soup.find_all(["script", "style", "nav", "aside"]):
            tag.decompose()
        return md(str(soup), heading_style=ATX)

    @staticmethod
    def normalise_url(url: str) -> str:
        """Return the canonical form of a URL (no fragment, no trailing slash)."""
        parsed = urllib.parse.urlparse(url)
        parsed = parsed._replace(fragment="")
        path = parsed.path.rstrip("/") or "/"
        parsed = parsed._replace(path=path)
        return urllib.parse.urlunparse(parsed)
