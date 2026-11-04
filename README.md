# Battery circularity literature monitor

Standalone battery-circularity monitor. No hydropower topic or task is included.

## Run

```sh
pip install -r requirements.txt
python monitor.py
python sources_monitor.py
```

Edit `config.json` for journals, queries, keyword rules, research interests and
external sources. Set `OPENAI_API_KEY` for AI abstract analysis; optionally set
`OPENALEX_API_KEY`. For an initial historical collection, set `BACKFILL=true`
(or use Actions → Weekly battery circularity monitor → Run workflow → backfill=true).
The seed DOI list is intentionally empty pending trusted anchor papers.
Check first-run warnings for unresolved journal and preprint source names.

Outputs are `data/battery/` and `reports/battery/` so inherited water-related
outputs cannot enter the battery database. Existing root-level data and reports
are unused by this package. No topic environment variable is required.

The workflow runs battery literature and external-source discovery weekly and
commits only battery outputs. GitHub scheduled workflows run on the default
branch; a feature branch can be run manually. External searches use Google News
RSS and are not complete official archives.

`send_email.py` attaches battery reports only. Invoke separately with SMTP_HOST,
SMTP_USER and SMTP_PASSWORD; SMTP_PORT defaults to 465. The existing recipient is
xiaoli8@nus.edu.sg. The workflow does not send email automatically.

## Local open-access PDF downloads

```sh
python download_github_pdfs.py --dest literature2
```

Or run `START_BATTERY_PDF_DOWNLOAD.bat` on Windows after installing dependencies.
The downloader fetches battery spreadsheets from the `battery-circularity` branch,
ranks candidates and attempts the top 10 available open-access PDFs. Use
`--branch main` if installed on main, and `--limit` to adjust the candidate count.
Existing PDFs are retained; HTML responses are not saved as PDFs.

## Offline checks

```sh
python -m unittest discover -s tests -v
```

Upload the contents of this package to the repository root, retaining the
`.github/workflows/` directory. The package includes requirements.txt and can
also be used in a new repository. It does not include old output databases.
