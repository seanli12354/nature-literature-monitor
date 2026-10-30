"""Weekly, DOI-deduplicated OpenAlex literature monitor.

Two collection modes, both restricted to the configured journals:
  * keyword queries  - OpenAlex title_and_abstract.search with boolean strings
  * seed citations   - papers that cite the anchor papers listed in config.json

Every record gets a cheap keyword relevance check (title + abstract). Only records
that pass are sent to the AI, which extracts stated gaps and future work rather than
just the main finding. OPENALEX_API_KEY is optional; OPENAI_API_KEY is needed for AI.
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
AI_FIELDS = ["research_question", "main_finding", "stated_gap_or_future_work", "real_world_hook",
             "generalizability", "direction_hint", "theme", "relevance"]
FIELDS = ["key", "doi", "openalex_id", "title", "journal", "published", "type", "authors", "citations",
          "citations_per_year", "abstract", "url", "query", "seed", "prefilter",
          *AI_FIELDS, "evidence_status", "analyzed_at"]
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NatureLiteratureMonitor/1.1 (academic metadata research)"})


def request_json(url, params=None, headers=None, payload=None):
    for attempt in range(6):
        try:
            if payload is None:
                r = SESSION.get(url, params=params, headers=headers, timeout=45)
            else:
                r = SESSION.post(url, json=payload, headers=headers, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt * 2, 60))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"API did not respond after retries: {url}")


def openalex(endpoint, params):
    params = dict(params)
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if CONFIG.get("contact_email"):
        params["mailto"] = CONFIG["contact_email"]
    return request_json(f"https://api.openalex.org/{endpoint}", params)


def normalize_doi(value):
    value = (value or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", value)


def decode_abstract(index):
    if not index:
        return ""
    words = [(position, word) for word, positions in index.items() for position in positions]
    return " ".join(word for _, word in sorted(words))


def source_name(work):
    return (((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "").strip()


def resolve_journals():
    """Resolve exact source names; stop rather than guess."""
    ids = {}
    for journal in CONFIG["journals"]:
        data = openalex("sources", {"search": journal, "per_page": 25})
        matches = [s for s in data.get("results", []) if s.get("display_name", "").casefold() == journal.casefold()]
        if not matches:
            print(f"WARNING: no OpenAlex source named exactly {journal!r}; skipped", file=sys.stderr)
            continue
        # Several sources can share a name (e.g. "Science"); keep the most cited one.
        best = max(matches, key=lambda s: s.get("cited_by_count", 0))
        ids[journal] = best["id"].split("/")[-1]
    return ids


def resolve_seeds():
    seeds = {}
    for doi in CONFIG.get("seed_dois", []):
        work = openalex(f"works/https://doi.org/{normalize_doi(doi)}", {})
        seeds[work["id"].split("/")[-1]] = work.get("display_name", doi)
    return seeds


def prefilter(title, abstract):
    """Keep a record only if it has a domain term AND (if configured) an energy term."""
    text = f"{title} {abstract}".lower()
    exclude = [w.lower() for w in CONFIG.get("exclude_if_any", [])]
    if exclude and any(w in text for w in exclude):
        return "excluded"
    for group in ("must_include_any", "must_also_include_any"):
        words = [w.lower() for w in CONFIG.get(group, [])]
        if words and not any(w in text for w in words):
            return "off-topic"
    return "pass"


def citations_per_year(cited, published):
    try:
        years = max((date.today() - date.fromisoformat(published)).days / 365.25, 0.5)
    except ValueError:
        return ""
    return round(cited / years, 1)


def record(work, journal, label, seed=""):
    doi = normalize_doi(work.get("doi"))
    oa_id = work["id"].split("/")[-1]
    abstract = decode_abstract(work.get("abstract_inverted_index"))
    published = work.get("publication_date", "") or ""
    cited = work.get("cited_by_count", 0) or 0
    return {
        "key": "doi:" + doi if doi else "oa:" + oa_id, "doi": doi, "openalex_id": oa_id,
        "title": work.get("display_name", "") or "", "journal": journal, "published": published,
        "type": work.get("type", ""),
        "authors": "; ".join(a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])[:15]),
        "citations": cited, "citations_per_year": citations_per_year(cited, published),
        "abstract": abstract, "url": "https://doi.org/" + doi if doi else work["id"],
        "query": label, "seed": seed, "prefilter": prefilter(work.get("display_name", "") or "", abstract),
    }


def merge(results, rec, field, value):
    if not value:
        return
    if rec["key"] not in results:
        results[rec["key"]] = rec
    elif value not in results[rec["key"]][field].split("; "):
        results[rec["key"]][field] = "; ".join(x for x in (results[rec["key"]][field], value) if x)


def paged(filter_str, label, sources, results, field, value, any_journal=False):
    journals = {j.casefold() for j in sources}
    cursor, n = "*", 0
    for _ in range(CONFIG["max_pages_per_query"]):
        payload = openalex("works", {"filter": filter_str, "per_page": 100, "cursor": cursor})
        works = payload.get("results", [])
        for w in works:
            journal = source_name(w)
            if any_journal or journal.casefold() in journals:
                seed_mode = field == "seed"
                merge(results, record(w, journal, "" if seed_mode else value, value if seed_mode else ""), field, value)
        n += len(works)
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not works or not cursor:
            break
        time.sleep(0.2)
    print(f"{label}: {n} works", flush=True)


def collect(sources, seeds, from_date, to_date):
    results = {}
    source_ids = "|".join(sources.values())
    base = f"primary_location.source.id:{source_ids},from_publication_date:{from_date},to_publication_date:{to_date}"
    for query in CONFIG["queries"]:
        if "," in query:
            raise RuntimeError(f"Commas are not allowed inside an OpenAlex filter value: {query!r}")
        paged(f"{base},title_and_abstract.search:{query}", query, sources, results, "query", query)
    seed_from = CONFIG.get("seed_from_date", from_date)
    for seed_id, title in seeds.items():
        any_journal = bool(CONFIG.get("seed_any_journal", False))
        seed_base = (f"from_publication_date:{seed_from},to_publication_date:{to_date}" if any_journal else
                     f"primary_location.source.id:{source_ids},from_publication_date:{seed_from},to_publication_date:{to_date}")
        paged(f"{seed_base},cites:{seed_id}", f"cites {title[:60]}", sources, results, "seed", title[:80], any_journal)
    return results


SYSTEM_PROMPT = (
    "You read scholarly ABSTRACTS only and help a researcher find research directions. "
    "Return a JSON object with these keys, each a short string: research_question, main_finding, "
    "stated_gap_or_future_work (gaps, limitations or next steps the abstract states or clearly implies; "
    "'None stated' if absent), real_world_hook (a concrete event, policy, plan or decision the work is tied to; "
    "'None stated' if absent), generalizability (Local case / Regional / Global or transferable mechanism), "
    "direction_hint (one sentence on an open question this suggests for the researcher's interests), theme, "
    "relevance (High, Medium or Low for the researcher's interests). Never invent numbers, mechanisms or "
    "full-text details; write 'Not established from abstract' where necessary. Do not force a connection: "
    "if the paper is not about the researcher's interests, set relevance to Low and direction_hint to "
    "'Not relevant'. High means it directly studies one of the interests; Medium means it offers a method, "
    "dataset or framing the researcher could directly reuse. Researcher's interests: "
)


def analyze(row):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY")
    title_only = not row.get("abstract")
    response = request_json(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload={
            "model": CONFIG["ai_model"], "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + CONFIG.get("research_interests", "")},
                {"role": "user", "content": json.dumps(
                    {**{k: row.get(k, "") for k in ("title", "journal", "type", "abstract", "doi")},
                     **({"note": "No abstract available: judge relevance from the title only and write "
                                 "'Not established from title' for findings and gaps."} if title_only else {})},
                    ensure_ascii=False)},
            ],
        },
    )
    parsed = json.loads(response["choices"][0]["message"]["content"])
    return {**{f: str(parsed.get(f, "")) for f in AI_FIELDS},
            "evidence_status": "Title only (no abstract in OpenAlex)" if title_only else "Abstract only",
            "analyzed_at": datetime.now(timezone.utc).date().isoformat()}


def load_rows():
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        rows = {row["key"]: row for row in csv.DictReader(f)}
    for row in rows.values():
        # Re-screen every stored record so rule changes also apply to old rows.
        row["prefilter"] = prefilter(row.get("title", ""), row.get("abstract", ""))
        if row["prefilter"] != "pass" and not row.get("analyzed_at"):
            row["evidence_status"] = f"Skipped by keyword prefilter ({row['prefilter']})"
        for f in ("query", "seed", "type", "citations_per_year"):
            row.setdefault(f, "")
        if not row.get("citations_per_year"):
            row["citations_per_year"] = citations_per_year(float(row.get("citations") or 0), row.get("published", ""))
    return rows


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
    numeric = {"citations", "citations_per_year"}
    for row in rows:
        ws.append([row.get(k, "") if k in numeric else str(row.get(k, "")) for k in FIELDS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.alignment = Alignment(wrap_text=True)
    wide = {"title": 60, "abstract": 75, "authors": 45, "stated_gap_or_future_work": 60,
            "direction_hint": 60, "main_finding": 55, "research_question": 50, "real_world_hook": 45}
    for i, name in enumerate(FIELDS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = wide.get(name, 18)
    wb.save(DATA / "literature.xlsx")


def write_report(rows, from_date, to_date, failures, n_new):
    today = date.today().isoformat()
    rank = {"High": 0, "Medium": 1, "Low": 2}
    shown = [r for r in rows if r.get("prefilter") == "pass" and r.get("relevance") in ("High", "Medium")]
    shown.sort(key=lambda r: (rank.get(r.get("relevance"), 3), -float(r.get("citations_per_year") or 0)))
    lines = [f"# Literature monitor — {today}", "",
             f"**Window:** {from_date} to {to_date}  |  **New this run:** {n_new}  |  "
             f"**High/Medium relevance in database:** {len(shown)}", "",
             "Based on OpenAlex metadata and abstracts only, not full-text review.", ""]
    for r in shown:
        lines += [f"## {r['title']}",
                  f"- **Journal/date/type:** {r['journal']} / {r['published']} / {r.get('type', '')}",
                  f"- **DOI:** {r['url']}  |  **Citations/yr:** {r.get('citations_per_year', '')}",
                  f"- **Found via:** {r.get('query') or ''}{' | cites ' + r['seed'] if r.get('seed') else ''}",
                  f"- **Relevance:** {r.get('relevance')}  |  **Scope:** {r.get('generalizability', '')}",
                  f"- **Stated gap / future work:** {r.get('stated_gap_or_future_work', '')}",
                  f"- **Real-world hook:** {r.get('real_world_hook', '')}",
                  f"- **Direction hint:** {r.get('direction_hint', '')}", ""]
    if failures:
        lines += ["## AI analysis failures", *[f"- {x}" for x in failures], ""]
    text = "\n".join(lines)
    (REPORTS / f"weekly_{today}.md").write_text(text, encoding="utf-8")
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")


def main():
    today = date.today()
    backfill = os.environ.get("BACKFILL", "false").lower() == "true"
    from_date = CONFIG["start_date"] if backfill else (today - timedelta(days=int(CONFIG["lookback_days"]))).isoformat()
    to_date = today.isoformat()
    sources = resolve_journals()
    seeds = resolve_seeds()
    existing = load_rows()
    found = collect(sources, seeds, from_date, to_date)

    n_new = 0
    for key, row in found.items():
        if key in existing:
            keep = {f: existing[key].get(f, "") for f in (*AI_FIELDS, "evidence_status", "analyzed_at")}
            for f in ("query", "seed"):
                merged = {x for x in (existing[key].get(f, "") + "; " + row[f]).split("; ") if x}
                row[f] = "; ".join(sorted(merged))
            existing[key] = {**row, **keep}
        else:
            existing[key] = row
            n_new += 1

    # AI only for on-topic, unanalyzed records; most-cited-per-year first, then newest.
    for row in existing.values():
        if row.get("prefilter") != "pass" and not row.get("analyzed_at"):
            row["evidence_status"] = f"Skipped by keyword prefilter ({row.get('prefilter')})"
    pending = [r for r in existing.values()
               if r.get("prefilter") == "pass" and not r.get("analyzed_at")]
    pending.sort(key=lambda r: (float(r.get("citations_per_year") or 0), r.get("published", "")), reverse=True)
    budget = int(CONFIG["max_ai_papers_per_run"])
    failures = []
    for row in pending[:budget]:
        try:
            row.update(analyze(row))
        except Exception as exc:  # keep metadata even if the AI call fails
            failures.append(f"{row['openalex_id']}: {str(exc)[:180]}")
    for row in pending[budget:]:
        row["evidence_status"] = "Pending AI analysis (run cap; will be retried next run)"
    print(f"AI analyzed: {min(budget, len(pending)) - len(failures)}, remaining: {max(0, len(pending) - budget)}")

    all_rows = sorted(existing.values(), key=lambda r: (r.get("published", ""), r.get("title", "")), reverse=True)
    write_csv(all_rows)
    write_excel(all_rows)
    write_report(all_rows, from_date, to_date, failures, n_new)
    passed = sum(r.get("prefilter") == "pass" for r in all_rows)
    print(f"Done: {len(all_rows)} total ({passed} pass prefilter), {n_new} new, {len(failures)} AI failures")
    if failures:
        print("Warning: some AI analyses failed; metadata was retained", file=sys.stderr)


if __name__ == "__main__":
    main()
