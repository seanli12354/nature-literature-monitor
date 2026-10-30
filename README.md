# Nature Literature Monitor

A weekly, DOI-deduplicated Nature-family literature database, using OpenAlex metadata and OpenAI abstract analysis.

## Setup (GitHub website)

1. Create a **private** GitHub repository named `nature-literature-monitor`.
2. Upload all files and folders from this project (including `.github/workflows/weekly.yml`). If uploading through GitHub web, ensure the `.github` folder is retained.
3. Create an OpenAlex API key using the current OpenAlex account/developer instructions: https://docs.openalex.org/ .
4. Create an OpenAI API key at https://platform.openai.com/api-keys and enable API billing. ChatGPT subscriptions do not include API usage.
5. Repository → Settings → Secrets and variables → Actions → New repository secret. Add `OPENALEX_API_KEY` and `OPENAI_API_KEY`. Never commit keys.
6. Repository → Actions → **Weekly Nature literature monitor** → Run workflow. For the first run, use `backfill=false` (last 30 days). If successful, optionally run `backfill=true` (from 2021; potentially expensive and incomplete due to per-query page cap).
7. Review `data/literature.csv`, `data/literature.xlsx`, `reports/latest.md`.
8. Every Monday at **09:00 Asia/Singapore** (01:00 UTC), GitHub Actions runs automatically. Scheduled jobs can occasionally be delayed.

## Configuration

Edit `config.json` to change journals, queries, start date, lookback days, OpenAI model, and per-run AI cap. The current source filter uses **primary publication source**, so articles whose primary venue is not one of the configured journals are excluded. OpenAlex search relevance is not the same as nature.com search.

`max_pages_per_query=5` means **at most 500 results per query**. If you backfill a large period, increase this or split historical periods; otherwise results can be incomplete. `max_ai_papers_per_run=35` caps AI costs; additional new articles remain marked pending, and the current script does **not** automatically analyze pending items in later runs. Increase cap or extend script if needed.

## Important limitations

- AI only reads **abstracts**, not paywalled full texts. A missing abstract is marked.
- DOI is the primary deduplication key; OpenAlex ID is the fallback. If a record initially lacks a DOI and later gains one, manual merging may be required.
- Journal IDs are resolved by exact journal names from OpenAlex. If a source name cannot be resolved uniquely, the run stops rather than silently using the wrong journal.
- GitHub Actions writes updates by committing to the default branch; allow Actions to write to the repository under Settings → Actions → General → Workflow permissions. Branch protection can prevent this; if so, adjust permissions or use a PR-based workflow.
- OpenAlex API terms, quotas, pricing, and required credentials can change. OpenAI API usage has separate charges.
- The report lists newly discovered papers; it is **not** a statistical synthesis of the entire database. Citations are database-supplied metadata, not independent verification.
- If OpenAlex returns more than 500 records for a query in the chosen window, results will be truncated; see page cap.

## Local run

```bash
python -m pip install -r requirements.txt
export OPENALEX_API_KEY='your_key'
export OPENAI_API_KEY='your_key'
python monitor.py
```

Windows PowerShell: `$env:OPENALEX_API_KEY="..."` and `$env:OPENAI_API_KEY="..."`.

For historical backfill: `BACKFILL=true python monitor.py` (PowerShell: `$env:BACKFILL="true"; python monitor.py`).
