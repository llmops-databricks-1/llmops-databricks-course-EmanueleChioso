# justfile — fandom_wiki_scraper project
# Requires: just (https://just.systems), uv

# ── defaults ──────────────────────────────────────────────────────────────────

wiki_data_dir  := "data/fandom_wiki"
reddit_data_dir := "data/reddit_onepiece"
wiki_delay     := "1.5"
reddit_delay   := "6.0"
max_pages      := "500"
max_depth      := "3"
backend        := "api"

# ── help ──────────────────────────────────────────────────────────────────────

[private]
default:
    @just --list

# ── setup ─────────────────────────────────────────────────────────────────────

# Install all dev dependencies
install:
    uv sync --extra dev

# ── databricks auth ───────────────────────────────────────────────────────────

# Authenticate personal workspace (emanuele.chioso@gmail.com)
databricks-login-personal:
    databricks auth login \
        --host https://dbc-412b921d-cb05.cloud.databricks.com \
        --profile personal

# Authenticate cauchy workspace
databricks-login-cauchy:
    databricks auth login \
        --host https://dbc-b1b2f91a-d102.cloud.databricks.com \
        --profile cauchy

# ── quality ───────────────────────────────────────────────────────────────────

# Run ruff linter + formatter via pre-commit
lint:
    uv run pre-commit run --all-files

# Run the test suite
test:
    uv run pytest

# Lint then test
check: lint test

# ── fandom wiki scraper ───────────────────────────────────────────────────────

# Scrape one or more specific wiki pages
# Usage: just wiki-scrape url=https://onepiece.fandom.com/wiki/Monkey_D._Luffy
wiki-scrape url:
    uv run python -m fandom_wiki_scraper.scraper.cli \
        --data_dir={{ wiki_data_dir }} \
        --delay={{ wiki_delay }} \
        --backend={{ backend }} \
        scrape {{ url }}

# BFS-crawl from a wiki start page
# Usage: just wiki-crawl url=https://onepiece.fandom.com/wiki/One_Piece_Wiki
wiki-crawl url:
    uv run python -m fandom_wiki_scraper.scraper.cli \
        --data_dir={{ wiki_data_dir }} \
        --delay={{ wiki_delay }} \
        --backend={{ backend }} \
        crawl {{ url }} \
        --max_pages={{ max_pages }} \
        --max_depth={{ max_depth }}

# Resume a previous wiki crawl from the last unscraped page
wiki-resume:
    uv run python -m fandom_wiki_scraper.scraper.cli \
        --data_dir={{ wiki_data_dir }} \
        --delay={{ wiki_delay }} \
        --backend={{ backend }} \
        resume \
        --max_pages={{ max_pages }} \
        --max_depth={{ max_depth }}

# ── reddit scraper ────────────────────────────────────────────────────────────

# Discover and scrape weekly/monthly/yearly top threads
reddit-weekly time_ranges="week,month,year":
    uv run python -m fandom_wiki_scraper.reddit \
        --data_dir={{ reddit_data_dir }} \
        --delay={{ reddit_delay }} \
        weekly --time_ranges={{ time_ranges }}

# Re-scrape threads with significant score/comment changes
reddit-refresh:
    uv run python -m fandom_wiki_scraper.reddit \
        --data_dir={{ reddit_data_dir }} \
        --delay={{ reddit_delay }} \
        refresh

# Scrape a specific Reddit thread URL
# Usage: just reddit-scrape url=https://www.reddit.com/r/OnePiece/comments/abc123/title/
reddit-scrape url:
    uv run python -m fandom_wiki_scraper.reddit \
        --data_dir={{ reddit_data_dir }} \
        --delay={{ reddit_delay }} \
        scrape {{ url }}
