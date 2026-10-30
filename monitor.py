"""Weekly, DOI-deduplicated OpenAlex literature monitor.
Requires OPENALEX_API_KEY and OPENAI_API_KEY in environment.
"""
import csv
import json
import os
import re
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
DATA.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)
CSV_PATH = DATA / "literature.csv"
FIELDS = ["key", "doi", "openalex_id", "title", "journal", "published", "authors", "citations",
          "abstract", "url", "query", "research_question", "main_finding", "novelty",
          "broader_significance", "theme", "relevance", "evidence_status", "analyzed_at"]
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NatureLiteratureMonitor/1.0 (academic metadata research)"})


def request_json(url, params=None, headers=None, payload=None):
    for attempt in range(5):
        try:
            if payload is None:
                r = SESSION.get(url, params=params, headers=headers, timeout=45)
            else:
                r = SESSION.post(url, json=payload, headers=headers, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt * 2, 30))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("API did not respond after retries")


def openalex(endpoint, params):
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    if not api_key:
        raise RuntimeError("Set OPENALEX_API_KEY in GitHub Actions secrets")
    return request_json(f"https://api.openalex.org/{endpoint}", {**params, "api_key": api_key})


def normalize_doi(value):
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    return value


def decode_abstract(index):
    if not index:
        return ""
    words = [(position, word) for word, positions in index.items() for position in positions]
    return " ".join(word for _, word in sorted(words))


def source_name(work):
    return (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip()


def resolve_journals():
    """Resolve exact source names, never trust a fuzzy title search alone."""
    ids = {}
    for journal in CONFIG["journals"]:
        data = openalex("sources", {"search": journal, "per_page": 25})
        matches = [s for s in data.get("results", []) if s.get("display_name", "").casefold() == journal.casefold()]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one OpenAlex source for {journal!r}; got {len(matches)}")
        ids[journal] = matches[0]["id"].split("/")[-1]
    return ids


def collect(sources, from_date, to_date):
    results = {}
    source_ids = "|".join(sources.values())
    for query in CONFIG["queries"]:
        for page in range(1, CONFIG["max_pages_per_query"] + 1):
            payload = openalex("works", {
                "search": query,
                "filter": (
    f"primary_location.source.id:{source_ids},"
    f"from_publication_date:{from_date},"
    f"to_publication_date:{to_date},"
    f"type:article|review|editorial|letter"
),
                "per_page": 100,
                "page": page,
            })
            works = payload.get("results", [])
            for w in works:
                journal = source_name(w)
                if journal.casefold() not in {j.casefold() for j in sources}:
                    continue
                doi = normalize_doi(w.get("doi"))
                oa_id = w["id"].split("/")[-1]
                key = "doi:" + doi if doi else "oa:" + oa_id
                if key not in results:
                    results[key] = {
                        "key": key, "doi": doi, "openalex_id": oa_id,
                        "title": w.get("display_name", ""), "journal": journal,
                        "published": w.get("publication_date", ""),
                        "authors": "; ".join(a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])[:15]),
                        "citations": w.get("cited_by_count", 0),
                        "abstract": decode_abstract(w.get("abstract_inverted_index")),
                        "url": "https://doi.org/" + doi if doi else w["id"],
                        "query": query,
                    }
                elif query not in results[key]["query"].split("; "):
                    results[key]["query"] += "; " + query
            print(f"{query}: page {page}, {len(works)} works", flush=True)
            if len(works) < 100:
                break
            time.sleep(0.15)
    return results


def analyze(row):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY in GitHub Actions secrets")
    if not row.get("abstract"):
        return {"evidence_status": "No abstract; AI analysis skipped"}
    prompt = {
        "title": row["title"], "journal": row["journal"],
        "abstract": row["abstract"], "doi": row["doi"]
    }
    response = request_json(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload={
            "model": CONFIG["ai_model"], "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Analyze scholarly ABSTRACTS only. Return a JSON object with keys research_question, main_finding, novelty, broader_significance, theme, relevance. Each value must be a short string. relevance must be High, Medium, or Low. Never invent numbers, mechanisms, claims, or full-text details; use 'Not established from abstract' where necessary. Prioritize hydropower, renewable integration, energy-system planning, energy storage, land-use impacts, and critical minerals. Broader significance should be evidence-grounded."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}
            ],
        },
    )
    content = response["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    fields = ("research_question", "main_finding", "novelty", "broader_significance", "theme", "relevance")
    return {**{f: str(parsed.get(f, "")) for f in fields},
            "evidence_status": "Abstract only", "analyzed_at": datetime.now(timezone.utc).isoformat()}


def load_rows():
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        return {row["key"]: row for row in csv.DictReader(f)}


def write_csv(rows):
    tmp = CSV_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(CSV_PATH)



def write_excel(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Literature"

    ws.append(FIELDS)

    for row in rows:
        ws.append([str(row.get(k, "")) for k in FIELDS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.alignment = Alignment(wrap_text=True)

    widths = {
        "A": 24, "B": 28, "C": 24,
        "D": 60, "E": 30, "F": 16,
        "G": 50, "H": 14, "I": 75,
        "J": 40, "K": 40, "L": 55,
        "M": 55, "N": 55, "O": 55,
        "P": 18, "Q": 18, "R": 35,
        "S": 25
    }

    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    wb.save(DATA / "literature.xlsx")



def write_report(new_rows, from_date, to_date, failures):
    today = date.today().isoformat()
    lines = [f"# Nature literature monitor — {today}", "",
             f"**Publication window checked:** {from_date} to {to_date}",
             f"**Newly discovered papers:** {len(new_rows)}", "",
             "This report is based on OpenAlex metadata and abstracts, not full-text review.", ""]
    for r in sorted(new_rows, key=lambda x: (x.get("relevance") != "High", x.get("published", "")), reverse=False):
        lines += [f"## {r['title']}",
                  f"- **Journal/date:** {r['journal']} / {r['published']}",
                  f"- **DOI:** {r['url']}",
                  f"- **Relevance:** {r.get('relevance') or 'Not analyzed'}",
                  f"- **Research question:** {r.get('research_question') or 'Not analyzed'}",
                  f"- **Main finding:** {r.get('main_finding') or 'Not analyzed'}",
                  f"- **Significance:** {r.get('broader_significance') or 'Not analyzed'}", ""]
    if failures:
        lines += ["## AI analysis failures", *[f"- {x}" for x in failures], ""]
    (REPORTS / f"weekly_{today}.md").write_text("\n".join(lines), encoding="utf-8")
    (REPORTS / "latest.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    today = date.today()
    backfill = os.environ.get("BACKFILL", "false").lower() == "true"
    from_date = CONFIG["start_date"] if backfill else (today - timedelta(days=int(CONFIG["lookback_days"]))).isoformat()
    to_date = today.isoformat()
    sources = resolve_journals()
    existing = load_rows()
    found = collect(sources, from_date, to_date)
    fresh = []
    for key, row in found.items():
        if key in existing:
            # Preserve existing AI annotations; refresh metadata and citations.
            existing[key].update({k: v for k, v in row.items() if k not in ("query",)})
        else:
            existing[key] = row
            fresh.append(row)
    fresh.sort(key=lambda r: (r.get("published", ""), r.get("title", "")), reverse=True)
    failures = []
    budget = int(CONFIG["max_ai_papers_per_run"])
    for row in fresh[:budget]:
        try:
            row.update(analyze(row))
        except Exception as exc:
            failures.append(f"{row['openalex_id']}: {str(exc)[:180]}")
    for row in fresh[budget:]:
        row["evidence_status"] = "Pending AI analysis (run cap)"
    all_rows = sorted(existing.values(), key=lambda r: (r.get("published", ""), r.get("title", "")), reverse=True)
    write_csv(all_rows)
    write_excel(all_rows)
    write_report(fresh, from_date, to_date, failures)
    print(f"Done: {len(all_rows)} total, {len(fresh)} new, {len(failures)} AI failures")
    if failures:
        print("Warning: some AI analyses failed; raw metadata was retained", file=sys.stderr)


if __name__ == "__main__":
    main()
