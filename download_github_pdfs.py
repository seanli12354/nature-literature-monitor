"""Download topic spreadsheets and available open-access PDFs from GitHub.

Usage: python download_github_pdfs.py battery --dest literature2
Existing files are kept. Publisher HTML is never saved as a PDF.
"""
import argparse
from io import BytesIO
from pathlib import Path
import re
import requests
from openpyxl import load_workbook

CORE_TERMS = {
    'battery': ['recycling', 'second life', 'second-life', 'repurpos', 'circular',
                'end-of-life', 'material flow', 'recovery rate', 'recycled content',
                'critical mineral', 'hydrometallurg', 'direct recycling', 'black mass',
                'life cycle', 'passport'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('topic', nargs='?', choices=CORE_TERMS, default='battery')
    parser.add_argument('--dest', type=Path, default=Path(__file__).resolve().parent / 'literature2')
    parser.add_argument('--branch', default='battery-circularity')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    dest = args.dest / args.topic
    dest.mkdir(parents=True, exist_ok=True)
    prefix = 'data/battery'
    session = requests.Session()
    rows = []
    for filename in ('literature.xlsx', 'external_sources.xlsx'):
        url = f'https://raw.githubusercontent.com/seanli12354/nature-literature-monitor/{args.branch}/{prefix}/{filename}'
        response = session.get(url, timeout=60)
        if response.status_code == 404:
            print(f'Not generated yet: {prefix}/{filename}')
            continue
        response.raise_for_status()
        book = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
        (dest / filename).write_bytes(response.content)
        values = iter(book.active.values)
        headers = next(values)
        rows.extend(dict(zip(headers, row)) for row in values)
        book.close()
    def score(row):
        text = ' '.join(str(v or '') for v in row.values()).casefold()
        return (row.get('relevance') == 'High', sum(term in text for term in CORE_TERMS[args.topic]),
                float(row.get('citations_per_year') or 0))
    rows.sort(key=score, reverse=True)
    seen = set()
    attempted = 0
    for row in rows:
        doi = str(row.get('doi') or '').strip()
        link = str(row.get('url') or '')
        key = doi or link
        if not key or key in seen:
            continue
        seen.add(key)
        if attempted >= max(0, args.limit):
            break
        attempted += 1
        title = str(row.get('title') or doi or 'paper')
        name = re.sub(r'[^\w .-]', '_', title)[:140].rstrip('. ') + '.pdf'
        path = dest / name
        if path.exists():
            continue
        try:
            candidates = [link] if '.pdf' in link.lower() else []
            if doi:
                doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
                response = session.get(f'https://api.openalex.org/works/https://doi.org/{doi}', timeout=45)
                response.raise_for_status()
                work = response.json()
                candidates += [loc['pdf_url'] for loc in work.get('locations', []) if loc.get('pdf_url')]
            saved = False
            for url in dict.fromkeys(candidates):
                try:
                    response = session.get(url, timeout=60)
                    response.raise_for_status()
                    if response.content.startswith(b'%PDF-'):
                        path.write_bytes(response.content)
                        saved = True
                        print(f'Saved: {name}')
                        break
                except requests.RequestException:
                    continue
            if not saved:
                print(f'No downloadable OA PDF: {title}')
        except (requests.RequestException, ValueError) as exc:
            print(f'Skipped {title}: {exc}')


if __name__ == '__main__':
    main()
