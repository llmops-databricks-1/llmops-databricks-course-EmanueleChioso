# Databricks notebook source
# MAGIC %md
# MAGIC # Fandom Wiki Scraper
# MAGIC
# MAGIC ## Topics Covered:
# MAGIC - Scraper architecture and data model
# MAGIC - Scraping individual pages (seeded mode)
# MAGIC - BFS crawl from a start URL
# MAGIC - Inspecting the scraped dataset
# MAGIC - Backend options (API vs curl vs fallback)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Architecture Overview
# MAGIC
# MAGIC The scraper is built around three layers:
# MAGIC
# MAGIC ### Fetcher Backends
# MAGIC
# MAGIC | Backend | Implementation | Best For |
# MAGIC |---------|---------------|----------|
# MAGIC | `api` | `MediaWikiFetcher` — httpx + Fandom `api.php` | **Default** — fastest, structured output |
# MAGIC | `curl` | `CurlCffiFetcher` — curl_cffi, Chrome TLS fingerprint | Fallback when API is blocked |
# MAGIC | `fallback` | `FallbackFetcher` — tries `api` then `curl` | Resilient production use |
# MAGIC
# MAGIC ### Storage Layout
# MAGIC
# MAGIC ```
# MAGIC <data_dir>/
# MAGIC ├── pages.csv          # index of all known pages (scraped + stubs)
# MAGIC ├── pages_html/<id>.html   # cleaned article HTML (mw-parser-output)
# MAGIC ├── pages_md/<id>.md       # Markdown converted from content HTML
# MAGIC └── media/<id>/            # downloaded images and other media
# MAGIC ```
# MAGIC
# MAGIC ### Run Modes
# MAGIC
# MAGIC 1. **`scrape(*urls)`** — seeded mode, fetch exactly the given URLs
# MAGIC 2. **`crawl(start_url)`** — BFS from one URL, up to `max_pages` / `max_depth`
# MAGIC 3. **`resume()`** — continue from all known-but-unscraped stubs

# COMMAND ----------

import csv
from pathlib import Path

from loguru import logger

from fandom_wiki_scraper.scraper.models import FandomWikiPage
from fandom_wiki_scraper.scraper.scraper import FandomWikiScraper
from fandom_wiki_scraper.scraper.storage import CsvStore

DATA_DIR = "/tmp/fandom_wiki_demo"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Data Model: `FandomWikiPage`
# MAGIC
# MAGIC Every page — whether fully scraped or just a discovered stub — is a
# MAGIC frozen Pydantic model:
# MAGIC
# MAGIC | Field | Type | Description |
# MAGIC |-------|------|-------------|
# MAGIC | `id` | `int` | Auto-assigned sequential ID |
# MAGIC | `url` | `str` | Normalised canonical URL |
# MAGIC | `title` | `str` | Page title extracted from HTML |
# MAGIC | `scraped_at` | `datetime \| None` | `None` means stub (not yet fetched) |
# MAGIC | `html_file` | `str \| None` | Path to saved content HTML |
# MAGIC | `md_file` | `str \| None` | Path to saved Markdown |
# MAGIC | `media_files` | `list[str]` | Paths to downloaded media |
# MAGIC | `linked_page_ids` | `list[int]` | IDs of pages linked from this one |
# MAGIC | `linked_urls` | `list[str]` | Normalised URLs of outgoing links |

# COMMAND ----------

# Inspect a stub vs a scraped page — no network calls needed
stub = FandomWikiPage(id=1, url="https://onepiece.fandom.com/wiki/Monkey_D._Luffy")
logger.info("Stub page: id={}, url={}, scraped={}", stub.id, stub.url, stub.scraped_at)

from datetime import UTC, datetime

scraped = FandomWikiPage(
    id=1,
    url="https://onepiece.fandom.com/wiki/Monkey_D._Luffy",
    title="Monkey D. Luffy",
    scraped_at=datetime.now(UTC),
    html_file="data/fandom_wiki/pages_html/1.html",
    md_file="data/fandom_wiki/pages_md/1.md",
    media_files=["data/fandom_wiki/media/1/0_Luffy.png"],
    linked_page_ids=[2, 3],
    linked_urls=[
        "https://onepiece.fandom.com/wiki/Straw_Hat_Pirates",
        "https://onepiece.fandom.com/wiki/Gomu_Gomu_no_Mi",
    ],
)
logger.info("Scraped page: title='{}', links={}, media={}", scraped.title, len(scraped.linked_urls), len(scraped.media_files))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Seeded Mode: Scrape Specific Pages
# MAGIC
# MAGIC Use `scraper.scrape(*urls)` when you know exactly which pages you want.
# MAGIC Already-scraped pages are skipped (idempotent). All discovered outgoing
# MAGIC links are registered as stubs so they can be fetched later.

# COMMAND ----------

SEED_URLS = [
    "https://onepiece.fandom.com/wiki/Chapter_1",
    "https://onepiece.fandom.com/wiki/Chapter_2",
    "https://onepiece.fandom.com/wiki/Chapter_3",
]

with FandomWikiScraper(data_dir=DATA_DIR, delay=1.0, backend="api") as scraper:
    pages = scraper.scrape(*SEED_URLS)

logger.info("Scraped {} page(s).", len(pages))
for page in pages:
    logger.info(
        "  [{:>3}] {} — {} links, {} media",
        page.id,
        page.title,
        len(page.linked_urls),
        len(page.media_files),
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. BFS Crawl Mode
# MAGIC
# MAGIC Use `scraper.crawl(start_url, max_pages, max_depth)` to discover pages
# MAGIC automatically via breadth-first search.
# MAGIC
# MAGIC | Parameter | Default | Description |
# MAGIC |-----------|---------|-------------|
# MAGIC | `max_pages` | 500 | Stop after this many pages scraped this call |
# MAGIC | `max_depth` | 3 | Maximum link-hop depth from the start URL |
# MAGIC | `delay` | 1.5 s | Polite delay between requests |
# MAGIC
# MAGIC **Tip**: Start small (`max_pages=5`) to verify the setup before running at scale.

# COMMAND ----------

START_URL = "https://onepiece.fandom.com/wiki/Chapter_1"
MAX_PAGES = 5
MAX_DEPTH = 1

with FandomWikiScraper(data_dir=DATA_DIR, delay=1.0, backend="api") as scraper:
    crawled_pages = scraper.crawl(START_URL, max_pages=MAX_PAGES, max_depth=MAX_DEPTH)

logger.info("Crawl complete — {} page(s) scraped.", len(crawled_pages))
for page in crawled_pages:
    logger.info("  [{:>3}] {}", page.id, page.title or page.url)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Inspecting the Dataset
# MAGIC
# MAGIC The `CsvStore` gives you programmatic access to `pages.csv` without
# MAGIC running another scrape. Useful for analysis and quality checks.

# COMMAND ----------

store = CsvStore(Path(DATA_DIR))
all_pages = store.all_pages()
scraped_pages = [p for p in all_pages if p.scraped_at is not None]
stub_pages = store.unscraped_pages()

logger.info("Total known pages : {}", len(all_pages))
logger.info("  Scraped          : {}", len(scraped_pages))
logger.info("  Stubs (pending)  : {}", len(stub_pages))

# COMMAND ----------

# Show top-10 most-linked pages (by incoming link count)
from collections import Counter

all_linked_ids = [lid for p in scraped_pages for lid in p.linked_page_ids]
id_to_title = {p.id: p.title or p.url for p in all_pages}
top_linked = Counter(all_linked_ids).most_common(10)

logger.info("Top 10 most-linked pages:")
for rank, (page_id, count) in enumerate(top_linked, start=1):
    logger.info("  {:>2}. {} (id={}, {} incoming links)", rank, id_to_title.get(page_id, "?"), page_id, count)

# COMMAND ----------

# Sample: read the Markdown content of the first scraped page
if scraped_pages:
    sample = scraped_pages[0]
    if sample.md_file and Path(sample.md_file).exists():
        md_text = Path(sample.md_file).read_text(encoding="utf-8")
        preview = md_text[:500].replace("\n", " ")
        logger.info("Markdown preview for '{}' ({} chars total):", sample.title, len(md_text))
        logger.info("  {}", preview)
    else:
        logger.warning("No Markdown file found for page id={}", sample.id)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Backend Options
# MAGIC
# MAGIC ### API Backend (`backend="api"`)
# MAGIC
# MAGIC Uses the Fandom/MediaWiki `api.php` endpoint directly.
# MAGIC Returns structured content HTML — no full-page DOM parsing needed.
# MAGIC
# MAGIC **Pros:** Fastest end-to-end, no Cloudflare friction, structured output
# MAGIC **Cons:** Requires the wiki to expose its API (almost all Fandom wikis do)
# MAGIC
# MAGIC ### curl_cffi Backend (`backend="curl"`)
# MAGIC
# MAGIC Fetches the full page HTML with a Chrome TLS fingerprint to bypass
# MAGIC Cloudflare bot-detection.
# MAGIC
# MAGIC **Pros:** Works on any page, handles Cloudflare challenges
# MAGIC **Cons:** Slightly slower, needs full DOM parsing
# MAGIC
# MAGIC ### Fallback Backend (`backend="fallback"`)
# MAGIC
# MAGIC Tries `api` first; on any transient failure automatically retries with `curl`.
# MAGIC `PermissionError` (robots.txt) and `FileNotFoundError` (404) are re-raised
# MAGIC immediately without switching.
# MAGIC
# MAGIC **Recommended for production jobs.**

# COMMAND ----------

# Cost model: estimate requests at different delay settings
PAGES_TARGET = 1000
DELAY_OPTIONS = [0.5, 1.0, 1.5, 2.0]

logger.info("Estimated crawl time for {} pages:", PAGES_TARGET)
logger.info("{:<12} {:>15} {:>20}", "Delay (s)", "Total time (min)", "Total time (hours)")
logger.info("-" * 50)
for delay in DELAY_OPTIONS:
    total_seconds = PAGES_TARGET * delay
    logger.info(
        "{:<12} {:>15.1f} {:>20.2f}",
        delay,
        total_seconds / 60,
        total_seconds / 3600,
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Resume Scraping
# MAGIC
# MAGIC If a crawl is interrupted or you want to fill in stubs discovered during
# MAGIC earlier scrapes, use `scraper.resume()`. It seeds the BFS queue with every
# MAGIC unscraped stub in `pages.csv` ordered by ID.

# COMMAND ----------

with FandomWikiScraper(data_dir=DATA_DIR, delay=1.0, backend="api") as scraper:
    resumed = scraper.resume(max_pages=10, max_depth=1)

logger.info("Resume scraped {} additional page(s).", len(resumed))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Summary
# MAGIC
# MAGIC | Mode | Method | Best For |
# MAGIC |------|--------|----------|
# MAGIC | **Seeded** | `scrape(*urls)` | Known page lists, targeted extraction |
# MAGIC | **BFS Crawl** | `crawl(start_url)` | Discovery, building full wiki graphs |
# MAGIC | **Resume** | `resume()` | Continuing interrupted jobs, filling stubs |
# MAGIC
# MAGIC ### Choosing a Backend
# MAGIC
# MAGIC - **`api`** — use for new scrapes; fastest and most reliable
# MAGIC - **`curl`** — use when Fandom API is unreachable or rate-limited
# MAGIC - **`fallback`** — use for long-running Databricks jobs where resilience matters
# MAGIC
# MAGIC ### Scaling Tips
# MAGIC
# MAGIC - Keep `delay >= 1.0` to respect Fandom's rate limits
# MAGIC - Use `workers > 1` only with `backend="fallback"` (each worker owns its own HTTP client)
# MAGIC - Store data under `/dbfs/` on Databricks for persistence across job runs