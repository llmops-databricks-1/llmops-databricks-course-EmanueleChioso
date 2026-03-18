# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# LLMOps Course on Databricks — Project Guidelines

## Development Environment

This project uses `uv` for dependency management and running tools.
Python **3.12** is required (matches Databricks Serverless Environment 4).

Initial setup:
```bash
uv sync --extra dev
```

### Running Commands

**ALWAYS use `uv run` prefix for all Python tools:**

```bash
# Linting and formatting (via pre-commit / ruff)
just lint

# Run all tests
just test
```

CI installs `--extra ci` (no `databricks-connect`/`ipykernel`). Local dev uses `--extra dev`.

## Architecture

### Package layout

Source lives under `src/fandom_wiki_scraper/`. The distribution name is `llmops-databricks-course-EmanueleChioso`; the importable module is `fandom_wiki_scraper`. The version is read from `version.txt` at build time — bump that file to release a new version.

A `project_config.yml` at the repo root is bundled as package data and is the intended place for project-level configuration (workspace names, catalog, etc.).

### Databricks Asset Bundles

`databricks.yml` defines the bundle. The `dev` target workspace host is a placeholder (`https://<your-databricks-workspace>`) — replace it with the actual workspace URL before deploying.

Each notebook gets a matching job definition in `resources/<name>_job.yml`. Jobs run notebooks inside **Serverless Environment 4** with the project wheel pre-installed from `dist/*.whl`. Three base parameters are available in every notebook at runtime:
- `env` — bundle target name (e.g. `dev`)
- `git_sha` — passed via `--var git_sha=<sha>` at deploy time
- `run_id` — filled by Databricks at job run time

## Dependency Management

### Pinning Rules

**Regular dependencies** (`[project] dependencies`): pin to exact version.
```toml
"pydantic==2.11.7"
"databricks-sdk==0.85.0"
```

**Optional / dev dependencies**: use `>=X.Y.Z,<NEXT_MAJOR`.
```toml
"pytest>=8.3.4,<9"
"pre-commit>=4.1.0,<5"
```

### Packages That Must Always Be Optional

Never put these in `[project] dependencies`:
- `databricks-connect` → `dev` extra
- `ipykernel` → `dev` extra
- `pytest`, `pre-commit` → `ci` extra

### Updating Dependencies

Use the `/fix-deps` skill to look up the latest PyPI versions and update `pyproject.toml` automatically.

After any dependency changes, validate the environment resolves:
```bash
uv sync --extra dev
```

## Linting

Ruff is configured in `pyproject.toml` (line length 90, rules: F, E, W, B, I, UP, SIM, ERA, C, ANN). It runs automatically via pre-commit. Ruff auto-fixes are enabled (`--fix`); the hook fails if fixes were applied so you can review them.

## Skills

Custom slash commands are defined in `.claude/commands/`. Use them to automate common workflows:

| Skill | Command | Description |
|-------|---------|-------------|
| Fix dependencies | `/fix-deps` | Look up latest PyPI versions and update `pyproject.toml` |
| Run notebook | `/run-notebook <path>` | Deploy and run a notebook on Databricks via Asset Bundles |
| Ship | `/ship` | Commit all changes with a structured message and push (blocks on `main`) |

### `/run-notebook`

Deploys the project wheel and runs a notebook as a Databricks job.

```bash
/run-notebook notebooks/hello_world.py
```

What it does:
1. Derives a job resource key from the notebook filename (e.g. `hello_world_job`)
2. Ensures `resources/` exists and is included in `databricks.yml`
3. Creates `resources/<key>.yml` if it doesn't exist, with `env`, `git_sha`, and `run_id` base parameters
4. Runs `databricks bundle deploy` then `databricks bundle run <key>`

## Notebook File Format

All Python files in `notebooks/` must be formatted as Databricks notebooks:

- **First line**: `# Databricks notebook source`
- **Cell separator**: `# COMMAND ----------` between logical sections

This enables running them interactively in both VS Code (via the Jupyter extension) and Databricks.

```python
# Databricks notebook source
"""
Example description.
"""

import os

# COMMAND ----------

print("Hello, world!")
```

**NEVER** use `#!/usr/bin/env python` shebangs in notebook files.
