# Fandom Wiki Scraper

Scrapes pages from any Fandom wiki — fetches HTML, extracts links and media, and saves everything to disk.

## Commands

### Scrape specific pages

```bash
uv run python -m fandom_wiki_scraper.scraper.cli scrape \
  https://onepiece.fandom.com/wiki/Chapter_1176 \
  https://onepiece.fandom.com/wiki/List_of_Locations
```

### BFS crawl from a start page

```bash
uv run python -m fandom_wiki_scraper.scraper.cli crawl \
  https://onepiece.fandom.com/wiki/One_Piece_Wiki \
  --max_pages=200 --max_depth=2
```

### Resume a previous crawl

```bash
uv run python -m fandom_wiki_scraper.scraper.cli resume \
  --max_pages=200 --max_depth=2
```

### Override output dir, delay, and HTTP backend

```bash
uv run python -m fandom_wiki_scraper.scraper.cli \
  --data_dir=data/my_wiki --delay=2.0 --backend=api \
  scrape https://onepiece.fandom.com/wiki/Chapter_1176
```

## Global flags

| Flag | Default | Description |
|------|---------|-------------|
| `--data_dir` | `data/fandom_wiki` | Output directory |
| `--delay` | `1.5` | Seconds between requests |
| `--backend` | `fallback` | HTTP backend: `api`, `curl`, `fallback` |
| `--workers` | `4` | Parallel download workers for media |

## Backends

- `api` — MediaWiki `api.php` (fastest, recommended)
- `curl` — `curl_cffi` with Chrome TLS fingerprint (fallback)
- `fallback` — tries `api` first, falls back to `curl`

## Output layout

```
data/fandom_wiki/
  pages.csv          # index of all scraped pages
  pages/<id>.html    # raw HTML per page
  media/<id>/        # downloaded images per page
```
