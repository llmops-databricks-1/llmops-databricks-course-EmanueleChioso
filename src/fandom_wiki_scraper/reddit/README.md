# Reddit Scraper (r/OnePiece)

Scrapes threads from r/OnePiece using the Reddit OAuth2 API.
Targets flairs: Theory, Analysis, Discussion, Big News.

## Prerequisites

Reddit now requires OAuth2 for API access. Register a free **script** app:

1. Go to <https://www.reddit.com/prefs/apps>
2. Click **"create another app…"** → type **script**
3. Set redirect URI to `http://localhost:8080` (unused, just required)
4. Note the **client ID** (under the app name) and **secret**

Export the credentials before running any command:

```bash
export REDDIT_CLIENT_ID=your_client_id
export REDDIT_CLIENT_SECRET=your_secret
```

## Commands

### Weekly discovery and scrape

Searches multiple time ranges and scrapes all matching threads:

```bash
uv run python -m fandom_wiki_scraper.reddit weekly
```

Limit to specific time ranges:

```bash
uv run python -m fandom_wiki_scraper.reddit weekly --time_ranges=week,month
```

Available ranges: `hour`, `day`, `week`, `month`, `year`, `all`.

### Refresh non-archived threads

Re-checks existing threads and re-scrapes those with significant score or comment changes:

```bash
uv run python -m fandom_wiki_scraper.reddit refresh
```

### Scrape specific thread URLs

```bash
uv run python -m fandom_wiki_scraper.reddit scrape \
  https://www.reddit.com/r/OnePiece/comments/abc123/some_title/
```

### Override output dir and delay

```bash
uv run python -m fandom_wiki_scraper.reddit \
  --data_dir=data/custom --delay=3.0 weekly
```

## Global flags

| Flag | Default | Description |
|------|---------|-------------|
| `--data_dir` | `data/reddit` | Output directory |
| `--delay` | `6.0` | Seconds between requests |

## Output layout

```
data/reddit/
  threads.csv         # index of all scraped threads
  threads_html/       # raw HTML per thread
  threads_md/         # markdown conversion per thread
```
