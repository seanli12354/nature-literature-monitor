
import csv
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

DATA.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)

CSV_PATH = DATA / "external_sources.csv"
XLSX_PATH = DATA / "external_sources.xlsx"
REPORT_PATH = REPORTS / "external_latest.md"

LOOKBACK_DAYS = 30

# Source-specific searches through Google News RSS.
# These are discovery searches, not complete official archives.
SOURCES = {
    "IEA": "iea.org",
    "IHA": "hydropower.org",
    "IRENA": "irena.org",
    "IPCC": "ipcc.ch",
    "WMO": "wmo.int",
    "DOE": "energy.gov",
    "AGU": "agu.org",
    "EGU": "egu.eu",
    "NOAA": "noaa.gov",
    "EIA": "eia.gov",
}

TOPICS = {
    "Hydropower": [
        "hydropower", "hydroelectric", "hydroelectricity",
        "hydropower plant", "pumped hydro"
    ],
    "Water resources": [
        "water resources", "water scarcity", "water security",
        "reservoir", "river basin", "water management"
    ],
    "Climate and drought": [
        "drought", "flood", "heatwave",
        "climate risk", "climate change", "extreme weather"
    ],
    "Energy systems": [
        "power grid", "electricity", "renewable energy",
        "energy transition", "energy storage",
        "power system", "grid reliability"
    ],
    "Water-energy nexus": [
        "water energy", "water-energy", "energy water",
        "hydro solar", "hydro wind"
    ],
}

EXCLUDED_TERMS = [
            "search -",
            "events -",
            "animation",
            "section 24",
            "production incentive",
            "discover hydropower",
            "session",
            "shark skin",
            "job opening",
            "job vacancy",
            "phd position",
            "postdoctoral position",
            "career opportunity",
            "how it works",
            "what is hydropower",
            "internship",
            "jobs -",
            "basics",
            "types of hydropower",
            "benefits of hydropower",
            "how hydropower works",
            "voices of hydropower",
            "mythbusters",
            "hydropower office",
            "hydrokinetic office",
            "incentive program",
            "hydroelectric incentives",
            "e-library",
            "training manual"
        ]

FIELDS = [
    "id", "date", "source", "category",
    "title", "summary", "url",
    "topics", "collected_at"
]


def build_rss_url(domain):
    query = (
        f"site:{domain} "
        "(hydropower OR hydroelectric OR \"pumped storage\" OR reservoir OR drought) "
        "(electricity OR power OR grid OR outage OR shortage)"
    )
    return (
        "https://news.google.com/rss/search?"
        + urlencode({
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en"
        })
    )


def title_key(source, title):
    return (source, re.sub(r"[^a-z0-9]", "", title.lower()))


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def classify(text):
    text = text.lower()
    matches = []

    for topic, keywords in TOPICS.items():
        if any(word in text for word in keywords):
            matches.append(topic)

    return "; ".join(matches)


def fetch_source(name, domain, cutoff):
    response = requests.get(
        build_rss_url(domain),
        timeout=30,
        headers={
            "User-Agent": "NatureLiteratureMonitor/1.0"
        }
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    results = []

    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        url = (item.findtext("link") or "").strip()
        summary = clean(item.findtext("description"))
        published = parse_date(item.findtext("pubDate"))

        if not title or not url or not published:
            continue

        if any(
            term in title.lower()
            for term in EXCLUDED_TERMS
        ):
            continue

        
        if published < cutoff:
            continue

        topics = classify(title + " " + summary)

        if not topics:
            continue

        identifier = hashlib.sha256(
            (name + "|" + url).encode("utf-8")
        ).hexdigest()[:20]

        results.append({
            "id": identifier,
            "date": published.date().isoformat(),
            "source": name,
            "category": "External information",
            "title": title,
            "summary": summary[:1000],
            "url": url,
            "topics": topics,
            "collected_at": datetime.now(
                timezone.utc
            ).date().isoformat()
        })

    return results


def load_existing():
    if not CSV_PATH.exists():
        return {}

    with CSV_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        return {
            row["id"]: row
            for row in csv.DictReader(f)
        }


def save_csv(rows):
    with CSV_PATH.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def save_excel(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "External Sources"

    ws.append(FIELDS)

    for row in rows:
        ws.append([row.get(k, "") for k in FIELDS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for cell in ws[1]:
        cell.font = Font(
            color="FFFFFF", bold=True
        )
        cell.fill = PatternFill(
            "solid", fgColor="17365D"
        )
        cell.alignment = Alignment(
            wrap_text=True
        )

    widths = [24, 16, 16, 24, 70, 90, 65, 45, 18]

    for i, width in enumerate(widths, start=1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[
            get_column_letter(i)
        ].width = width

    wb.save(XLSX_PATH)


def save_report(new_rows, errors):
    today = datetime.now(timezone.utc).date()

    lines = [
        "# Weekly external information monitor",
        "",
        f"Run date: {today.isoformat()}",
        f"New records: {len(new_rows)}",
        "",
    ]

    for row in new_rows:
        lines.extend([
            f"## {row['title']}",
            "",
            f"- Date: {row['date']}",
            f"- Source: {row['source']}",
            f"- Topics: {row['topics']}",
            f"- Link: {row['url']}",
            "",
            row["summary"] or "No summary available.",
            "",
        ])

    if errors:
        lines.extend([
            "## Source retrieval warnings",
            ""
        ])
        lines.extend(f"- {error}" for error in errors)

    REPORT_PATH.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def main():
    cutoff = datetime.now(
        timezone.utc
    ) - timedelta(days=LOOKBACK_DAYS)

    existing = {}
    seen = set()
    for row in sorted(load_existing().values(), key=lambda x: x["date"]):
        key = title_key(row["source"], row["title"])
        if key in seen or any(term in row["title"].lower() for term in EXCLUDED_TERMS):
            continue  # also clean records stored by earlier versions
        seen.add(key)
        existing[row["id"]] = row
    new_rows = []
    errors = []

    for name, domain in SOURCES.items():
        try:
            found = fetch_source(name, domain, cutoff)

            count = 0

            seen_titles = {title_key(r["source"], r["title"]) for r in existing.values()}
            for row in found:
                tkey = title_key(row["source"], row["title"])
                if tkey in seen_titles:
                    continue  # Google News often lists one page under several URLs
                seen_titles.add(tkey)
                if row["id"] not in existing:
                    existing[row["id"]] = row
                    new_rows.append(row)
                    count += 1

            print(f"{name}: {count} new records")

        except Exception as exc:
            message = f"{name}: {str(exc)[:200]}"
            errors.append(message)
            print("WARNING:", message)

    rows = sorted(
        existing.values(),
        key=lambda x: (x["date"], x["title"]),
        reverse=True
    )

    new_rows.sort(
        key=lambda x: x["date"],
        reverse=True
    )

    save_csv(rows)
    save_excel(rows)
    save_report(new_rows, errors)

    print(
        f"Done: {len(rows)} total, "
        f"{len(new_rows)} new, "
        f"{len(errors)} source warnings"
    )


if __name__ == "__main__":
    main()
