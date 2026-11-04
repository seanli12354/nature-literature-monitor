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


## Multiple research topics

`topics/hydro/config.json` and `topics/battery/config.json` hold the journal lists,
queries, research interests, keyword rules and external discovery sources.
`TOPIC` defaults to `hydro`; an unknown topic fails instead of using hydro settings.
Rules are OR-ed together and keyword groups within each rule are AND-ed.

```sh
TOPIC=hydro python monitor.py
TOPIC=hydro python sources_monitor.py
TOPIC=battery BACKFILL=true python monitor.py
TOPIC=battery python sources_monitor.py
```

Hydro retains `data/` and `reports/`, including its existing history. Battery uses
`data/battery/` and `reports/battery/`. The weekly workflow runs both topics;
manual dispatch with `backfill=true` collects their configured historical windows.
Scheduled GitHub Actions run from the default branch; this feature branch must be
merged before its revised schedule becomes active. No backfill is run by committing.

The battery seed DOI list is intentionally empty until trusted anchor papers are
selected. Keyword collection still works. Check first-run warnings for unresolved
OpenAlex journal or preprint source names; exact journal names are required.
External sources are Google News RSS discovery results, not complete agency archives.

`send_email.py` builds both topics' reports with topic-prefixed attachment names.
Set `TOPICS="battery"` to select only battery. The workflow does not invoke email;
SMTP credentials and an explicit invocation are required as before.

For local downloads, install `requirements.txt`, then run:

```sh
python download_github_pdfs.py battery --dest literature2
python download_github_pdfs.py hydro --dest literature2
```

Windows launchers are provided for each topic. This new standalone downloader
fetches the two topic spreadsheets from branch `battery-circularity`, ranks records
using relevance, topic keywords and citations per year, and attempts the top 10
open-access PDFs via OpenAlex locations. It keeps existing PDFs, validates PDF
headers and reports inaccessible papers. Use `--branch main` after merging, or
`--limit` to adjust the number of candidates. Existing PC-only downloader customizations
are not present in this repository and are not overwritten.

Offline checks (no API calls or real email): `python -m unittest discover -s tests -v`.
