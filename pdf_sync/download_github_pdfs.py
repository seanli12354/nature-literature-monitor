"""Sync public GitHub literature/external Excel files and download legally public PDFs.
Windows Python 3.9+; standard library only. No tokens or passwords required.

Outputs in DEST:
  Literature_PDFs/            all downloaded literature PDFs (one flat folder)
  External_PDFs/              all downloaded external-source PDFs (one flat folder)
  Top10_most_relevant/        copies of the 10 most relevant PDFs, rank-prefixed
  missing_pdfs.html           clickable list of everything not downloaded (open in browser)
  missing_pdfs.csv            same list for Excel (links are clickable HYPERLINK formulas)
  missing_dois.txt            DOIs only - paste into Zotero "Add Item by Identifier"
  literature_pdf_status.csv / external_pdf_status.csv   full status manifests
"""
from __future__ import annotations
import csv
import html
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

# Keep helper functions (xlsx reader, OpenAlex OA discovery, PDF validation)
# alongside this script: pdf_helpers.py
from pdf_helpers import read_xlsx, safe_name, openalex_work, candidates, download_pdf

# Usage: py -3 download_github_pdfs.py [topic]   (default: solar_cost)
TOPIC = sys.argv[1] if len(sys.argv) > 1 else 'solar_cost'
BASE = Path(r'C:\Users\xli9\OneDrive - McGill University\Desktop\manu_12_TIM_APEN')
DEST = BASE / f'literature_{TOPIC}'
REPO = 'seanli12354/nature-literature-monitor'
BRANCH = 'main'
FILES = (f'data/{TOPIC}/literature.xlsx', f'data/{TOPIC}/external_sources.xlsx')
USER_AGENT = 'AcademicLiteraturePDFSync/1.0 (public OA documents only)'
MAX_EXTERNAL_HTML = 2_000_000

# ---- New options -----------------------------------------------------------
# Library proxy prefix: clicking a proxied link opens the article with your
# institutional subscription (log in once with your McGill account).
# Set to '' to disable proxied links.
LIBRARY_PROXY = 'https://proxy.library.mcgill.ca/login?url='
TOP_N = 10                 # how many of the most relevant papers to open at the end
OPEN_TOP = True            # False = only build the Top10 folder, do not open anything
OPEN_MISSING_TOP_IN_BROWSER = False  # True = also open top-N links without a PDF in Chrome (many tabs at once can break the McGill login)
# Optional explicit path to WPS. Leave '' to auto-detect, falling back to the
# Windows default PDF viewer (set WPS as default PDF app if you want WPS always).
WPS_EXE = ''
# Links and missing_pdfs.html open in Chrome (where LibKey Nomad is installed).
# Leave '' to auto-detect Chrome; falls back to the default browser if Chrome is not found.
CHROME_EXE = ''
OPEN_MISSING_LIST = True   # open missing_pdfs.html in Chrome at the end of the run
# Words that mark a paper as close to the current manuscript (title + abstract).
CORE_TERMS_BY_TOPIC = {
    'solar_cost': ['cost decomposition', 'cost driver', 'drivers of cost', 'cost reduction', 'learning rate',
                   'learning curve', 'experience curve', "wright's law", 'balance of system', 'soft cost',
                   'module price', 'polysilicon', 'wafer', 'silver', 'efficiency', 'economies of scale',
                   'levelized', 'lcoe', 'cost of capital', 'wacc', 'input-output', 'value chain',
                   'tariff', 'supply chain', 'manufacturing cost', 'bottom-up'],
}
CORE_TERMS = CORE_TERMS_BY_TOPIC.get(TOPIC, [])
# -----------------------------------------------------------------------------

LIT_DIR = DEST / 'Literature_PDFs'
EXT_DIR = DEST / 'External_PDFs'
TOP_DIR = DEST / 'Top10_most_relevant'


def github_download(path):
    url = 'https://raw.githubusercontent.com/' + REPO + '/' + BRANCH + '/' + path
    target = DEST / Path(path).name
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=65) as resp:
            data = resp.read(35 * 1024 * 1024 + 1)
            if len(data) > 35 * 1024 * 1024:
                raise RuntimeError('Excel exceeds 35 MB')
        if not data.startswith(b'PK\x03\x04'):
            raise RuntimeError('GitHub response is not an Excel file')
        import zipfile
        from io import BytesIO
        if not zipfile.is_zipfile(BytesIO(data)):
            raise RuntimeError('Invalid Excel ZIP format')
        tmp = target.with_suffix('.download')
        tmp.write_bytes(data)
        tmp.replace(target)
        print(f'[GitHub] Updated {target.name} ({len(data):,} bytes)')
        return target
    except urllib.error.HTTPError as exc:
        print(f'[GitHub] FAILED {path}: HTTP {exc.code} (check public repo, branch, path)', flush=True)
    except Exception as exc:
        print(f'[GitHub] FAILED {path}: {exc}', flush=True)
    if target.exists():
        print(f'[GitHub] Using cached local {target.name} (may be outdated)')
        return target
    return None


def read_manifest(path):
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return {r.get('key', ''): r for r in csv.DictReader(f)}


MANIFEST_COLS = ['key', 'title', 'source', 'date', 'url', 'doi', 'relevance', 'score',
                 'pdf_status', 'pdf_url', 'pdf_path', 'checked_at']


def save_manifest(path, records):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS, extrasaction='ignore')
        w.writeheader()
        w.writerows(records)
    temp.replace(path)


def record_base(key, title, source, date, url, **extra):
    rec = dict(key=key, title=title, source=source, date=date, url=url,
               doi='', relevance='', score='', pdf_status='', pdf_url='', pdf_path='',
               checked_at=datetime.now().isoformat(timespec='seconds'))
    rec.update(extra)
    return rec


def clean_doi(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or '').strip(), flags=re.I).lower()


def relevance_score(row):
    """Higher = more relevant to the current manuscript.
    AI relevance dominates; manuscript keywords and seed-citation links break ties;
    citations only as a weak last tie-breaker (capped so new papers are not inflated)."""
    rel = {'high': 100, 'medium': 40, 'low': 0}.get(str(row.get('relevance') or '').strip().lower(), 20)
    text = (str(row.get('title') or '') + ' ' + str(row.get('abstract') or '')).lower()
    hits = sum(1 for t in CORE_TERMS if t in text)
    seed = 15 if str(row.get('seed') or '').strip() else 0
    try:
        cites = min(float(row.get('citations') or 0), 100) / 10
    except ValueError:
        cites = 0
    return round(rel + 6 * min(hits, 8) + seed + cites, 1)


def move_into_flat(path, folder):
    """Move a PDF saved by the old year/journal layout into the flat folder."""
    if path.parent == folder:
        return path
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    if dest.exists():
        return dest
    try:
        shutil.move(str(path), str(dest))
        return dest
    except OSError:
        return path


def pdf_name(title, identity):
    return safe_name(title, 90) + '__' + safe_name(identity, 30) + '.pdf'


def download_literature(path):
    rows = read_xlsx(path)
    for r in rows:
        r['_score'] = relevance_score(r)
    # Most relevant first, so the important PDFs are fetched even if the run is interrupted.
    rows.sort(key=lambda r: -r['_score'])
    print(f'\n[Literature] {len(rows)} records')
    manifest = DEST / 'literature_pdf_status.csv'
    old = read_manifest(manifest)
    results = []
    success = 0
    LIT_DIR.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows, 1):
        title = (row.get('title') or '').strip()
        if not title:
            continue
        key = str(row.get('key') or row.get('doi') or row.get('openalex_id') or title)
        published = str(row.get('published') or '')
        source = str(row.get('journal') or row.get('source_platform') or 'UnknownSource')
        identity = str(row.get('openalex_id') or row.get('doi') or i).rsplit('/', 1)[-1]
        target = LIT_DIR / pdf_name(title, identity)
        rec = record_base(key, title, source, published, row.get('url') or row.get('doi') or '',
                          doi=clean_doi(row.get('doi')), relevance=row.get('relevance', ''),
                          score=row['_score'])
        prev = old.get(key, {})
        prev_path = Path(prev['pdf_path']) if prev.get('pdf_path') else None
        if target.is_file() or (prev_path and prev_path.is_file()):
            have = target if target.is_file() else move_into_flat(prev_path, LIT_DIR)
            rec.update(pdf_status='Already downloaded', pdf_url=prev.get('pdf_url', ''), pdf_path=str(have))
        else:
            try:
                work = openalex_work(row)
                links = list(candidates(work)) if work else []
                if not links:
                    rec['pdf_status'] = 'No public OA PDF found'
                else:
                    failures = []
                    for link in links:
                        ok, message = download_pdf(link, target)
                        if ok:
                            rec.update(pdf_status='Downloaded', pdf_url=link, pdf_path=str(target))
                            success += 1
                            break
                        failures.append(message)
                    if not rec['pdf_status']:
                        rec['pdf_status'] = 'Download failed: ' + '; '.join(failures[:2])
            except Exception as exc:
                rec['pdf_status'] = 'Lookup failed: ' + str(exc)[:160]
            time.sleep(0.15)
        results.append(rec)
        print(f'  [{i}/{len(rows)}] {rec["pdf_status"]}: {title[:65]}', flush=True)
        save_manifest(manifest, results)
    print(f'[Literature] New PDFs: {success}; status: {manifest.name}')
    return results


def public_pdf_candidates(article_url):
    """Try a public direct PDF or an explicit citation_pdf_url meta tag.
    No authenticated sessions, no bypassing paywalls, no arbitrary website crawling.
    """
    if not article_url or not article_url.startswith(('https://', 'http://')):
        return []
    req = urllib.request.Request(article_url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/pdf'})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            final = resp.geturl()
            ctype = (resp.headers.get('Content-Type') or '').lower()
            if 'application/pdf' in ctype or urllib.parse.urlsplit(final).path.lower().endswith('.pdf'):
                return [final]
            if 'text/html' not in ctype:
                return []
            raw = resp.read(MAX_EXTERNAL_HTML + 1)
            if len(raw) > MAX_EXTERNAL_HTML:
                return []
            page = raw.decode('utf-8', errors='replace')
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    links = []
    # Standard scholarly metadata, including publisher and institutional repository pages.
    for tag in re.findall(r'<meta\b[^>]*>', page, flags=re.I):
        attrs = {k.lower(): html.unescape(v) for k, v in re.findall(
            r'''([\w:-]+)\s*=\s*["']([^"']*)["']''', tag)}
        if attrs.get('name', '').lower() in ('citation_pdf_url', 'dc.identifier.pdf'):
            link = urllib.parse.urljoin(final, attrs.get('content', ''))
            if link.startswith(('https://', 'http://')):
                links.append(link)
    # Only direct arXiv abstract pages get a deterministic PDF URL.
    m = re.match(r'https?://arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)', final)
    if m:
        links.append('https://arxiv.org/pdf/' + m.group(1))
    return list(dict.fromkeys(links))


def download_external(path):
    rows = read_xlsx(path)
    print(f'\n[External] {len(rows)} records')
    manifest = DEST / 'external_pdf_status.csv'
    old = read_manifest(manifest)
    results = []
    success = 0
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows, 1):
        title = (row.get('title') or '').strip()
        url = (row.get('url') or '').strip()
        key = str(row.get('id') or url or title)
        source = str(row.get('source') or 'External')
        published = str(row.get('date') or '')
        target = EXT_DIR / pdf_name(title, key[-20:])
        rec = record_base(key, title, source, published, url)
        prev = old.get(key, {})
        prev_path = Path(prev['pdf_path']) if prev.get('pdf_path') else None
        if target.is_file() or (prev_path and prev_path.is_file()):
            have = target if target.is_file() else move_into_flat(prev_path, EXT_DIR)
            rec.update(pdf_status='Already downloaded', pdf_url=prev.get('pdf_url', ''), pdf_path=str(have))
        else:
            links = public_pdf_candidates(url)
            if not links:
                rec['pdf_status'] = 'No direct public PDF found'
            else:
                failures = []
                for link in links:
                    ok, message = download_pdf(link, target)
                    if ok:
                        rec.update(pdf_status='Downloaded', pdf_url=link, pdf_path=str(target))
                        success += 1
                        break
                    failures.append(message)
                if not rec['pdf_status']:
                    rec['pdf_status'] = 'Download failed: ' + '; '.join(failures[:2])
            time.sleep(0.15)
        results.append(rec)
        print(f'  [{i}/{len(rows)}] {rec["pdf_status"]}: {title[:65]}')
        save_manifest(manifest, results)
    print(f'[External] New PDFs: {success}; status: {manifest.name}')
    return results


# ---- Links for papers that could not be downloaded --------------------------

def publisher_pdf_link(doi):
    """Publisher's own PDF URL pattern for common publishers (works when you have access,
    e.g. on campus / VPN / through the library proxy). Returns '' when no stable pattern."""
    if not doi:
        return ''
    d = doi
    if d.startswith('10.1038/'):
        return 'https://www.nature.com/articles/' + d.split('/', 1)[1] + '.pdf'
    if d.startswith(('10.1029/', '10.1002/', '10.1111/')):
        return 'https://onlinelibrary.wiley.com/doi/pdfdirect/' + d
    if d.startswith('10.1126/'):
        return 'https://www.science.org/doi/pdf/' + d
    if d.startswith('10.1073/'):
        return 'https://www.pnas.org/doi/pdf/' + d
    if d.startswith('10.1021/'):
        return 'https://pubs.acs.org/doi/pdf/' + d
    if d.startswith('10.1080/'):
        return 'https://www.tandfonline.com/doi/pdf/' + d
    if d.startswith('10.1061/'):
        return 'https://ascelibrary.org/doi/pdf/' + d
    if d.startswith('10.1088/'):
        return 'https://iopscience.iop.org/article/' + d + '/pdf'
    if d.startswith('10.1007/'):
        return 'https://link.springer.com/content/pdf/' + d + '.pdf'
    if d.startswith('10.1016/'):
        # Elsevier has no stable PDF URL; the DOI page has a one-click "View PDF" button.
        return ''
    return ''


def proxied(url):
    if not url or not LIBRARY_PROXY:
        return ''
    return LIBRARY_PROXY + url


def access_links(rec):
    doi = rec.get('doi', '')
    doi_url = 'https://doi.org/' + doi if doi else rec.get('url', '')
    pdf = publisher_pdf_link(doi)
    scholar = 'https://scholar.google.com/scholar?q=' + urllib.parse.quote('"' + rec.get('title', '') + '"')
    return {
        'pdf_direct': pdf,
        'pdf_via_proxy': proxied(pdf),
        'article_via_proxy': proxied(doi_url),
        'article': doi_url,
        'google_scholar': scholar,
    }


def best_link(rec):
    links = access_links(rec)
    return (links['pdf_via_proxy'] or links['article_via_proxy'] or links['pdf_direct']
            or links['article'] or links['google_scholar'])


def write_missing(lit, ext):
    missing = [r for r in lit if not r.get('pdf_path')]
    missing.sort(key=lambda r: -float(r.get('score') or 0))
    missing_ext = [r for r in ext if not r.get('pdf_path')]
    link_cols = ['pdf_via_proxy', 'article_via_proxy', 'pdf_direct', 'article', 'google_scholar']

    # Excel-friendly CSV: each link is a clickable HYPERLINK formula.
    def hl(url, label):
        if not url:
            return ''
        if len(url) > 250:
            return url
        return '=HYPERLINK("' + url.replace('"', '%22') + '","' + label + '")'

    with (DEST / 'missing_pdfs.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['rank', 'relevance', 'score', 'date', 'source', 'title', 'doi', *link_cols, 'status'])
        for i, r in enumerate(missing, 1):
            L = access_links(r)
            w.writerow([i, r.get('relevance', ''), r.get('score', ''), r.get('date', '')[:10],
                        r.get('source', ''), r.get('title', ''), r.get('doi', ''),
                        *[hl(L[c], c) for c in link_cols], r.get('pdf_status', '')])
        for r in missing_ext:
            w.writerow(['ext', '', '', r.get('date', '')[:10], r.get('source', ''), r.get('title', ''), '',
                        '', '', '', hl(r.get('url', ''), 'article'), '', r.get('pdf_status', '')])

    # DOI list for Zotero: Add Item by Identifier (magic wand) -> paste -> Find Available PDF.
    (DEST / 'missing_dois.txt').write_text(
        '\n'.join(r['doi'] for r in missing if r.get('doi')) + '\n', encoding='utf-8')

    # Clickable HTML page (fastest to work through: click, save PDF, next).
    def a(url, label):
        return f'<a href="{html.escape(url)}" target="_blank">{label}</a>' if url else ''

    rows_html = []
    for i, r in enumerate(missing, 1):
        L = access_links(r)
        buttons = ' · '.join(x for x in (
            a(L['pdf_via_proxy'], 'PDF (proxy)'), a(L['article_via_proxy'], 'Article (proxy)'),
            a(L['pdf_direct'], 'PDF'), a(L['article'], 'DOI'), a(L['google_scholar'], 'Scholar')) if x)
        rows_html.append(
            f'<tr class="{html.escape(str(r.get("relevance", "")).lower())}"><td>{i}</td>'
            f'<td>{html.escape(str(r.get("relevance", "")))}</td><td>{html.escape(r.get("date", "")[:4])}</td>'
            f'<td>{html.escape(r.get("source", ""))}</td><td>{html.escape(r.get("title", ""))}</td>'
            f'<td class="links">{buttons}</td></tr>')
    for r in missing_ext:
        rows_html.append(
            f'<tr><td>ext</td><td></td><td>{html.escape(r.get("date", "")[:4])}</td>'
            f'<td>{html.escape(r.get("source", ""))}</td><td>{html.escape(r.get("title", ""))}</td>'
            f'<td class="links">{a(r.get("url", ""), "Open")}</td></tr>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Missing PDFs</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:20px;font-size:14px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}}
tr.high td:nth-child(2){{color:#b00;font-weight:bold}}td.links a{{white-space:nowrap}}
a:visited{{color:#888}}</style></head><body>
<h2>PDFs not downloaded ({len(missing)} papers, {len(missing_ext)} external) — {datetime.now():%Y-%m-%d %H:%M}</h2>
<p>Sorted by relevance to the manuscript. "proxy" links open through the library with your subscription
(log in once). Visited links turn grey, so you can see what you have already handled.
This list is rebuilt on every run from what the script itself downloaded; papers you save by hand stay listed
(the grey visited links show what you already handled).</p>
<table><tr><th>#</th><th>Relevance</th><th>Year</th><th>Source</th><th>Title</th><th>Links</th></tr>
{''.join(rows_html)}</table></body></html>"""
    (DEST / 'missing_pdfs.html').write_text(page, encoding='utf-8')
    print(f'\n[Missing] {len(missing)} literature + {len(missing_ext)} external without PDF -> '
          f'missing_pdfs.html / missing_pdfs.csv / missing_dois.txt')


# ---- Top-N most relevant ------------------------------------------------------

def find_wps():
    if WPS_EXE and Path(WPS_EXE).is_file():
        return WPS_EXE
    roots = [os.environ.get('LOCALAPPDATA', ''), os.environ.get('ProgramFiles', ''),
             os.environ.get('ProgramFiles(x86)', '')]
    for root in roots:
        if not root:
            continue
        base = Path(root) / 'Kingsoft' / 'WPS Office'
        if not base.is_dir():
            continue
        for name in ('ksolaunch.exe',):
            hits = sorted(base.rglob(name))
            if hits:
                return str(hits[0])
    return ''


def find_chrome():
    if CHROME_EXE and Path(CHROME_EXE).is_file():
        return CHROME_EXE
    for root in (os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', ''),
                 os.environ.get('LOCALAPPDATA', '')):
        if root:
            exe = Path(root) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'
            if exe.is_file():
                return str(exe)
    return ''


def open_url(url):
    chrome = find_chrome()
    try:
        if chrome:
            subprocess.Popen([chrome, url])
        else:
            webbrowser.open(url)
    except Exception as exc:
        print('   could not open', url, exc)


def open_file(path, wps):
    try:
        if wps:
            subprocess.Popen([wps, str(path)])
        elif hasattr(os, 'startfile'):
            os.startfile(str(path))  # Windows default PDF viewer
        else:
            webbrowser.open(Path(path).as_uri())
    except Exception as exc:
        print('   could not open', path, exc)


def top_n(lit):
    ranked = sorted(lit, key=lambda r: -float(r.get('score') or 0))[:TOP_N]
    if not ranked:
        return
    TOP_DIR.mkdir(parents=True, exist_ok=True)
    for old in TOP_DIR.glob('*.pdf'):
        try:
            old.unlink()
        except OSError:
            pass
    lines = []
    wps = find_wps() if OPEN_TOP else ''
    print(f'\n[Top {TOP_N}] most relevant papers' + (f' (opening with {wps})' if wps else ''))
    for i, r in enumerate(ranked, 1):
        p = Path(r['pdf_path']) if r.get('pdf_path') else None
        has_pdf = bool(p and p.is_file())
        lines.append(f'{i:02d}. [{r.get("relevance", "")}, score {r.get("score")}] {r.get("title", "")}'
                     f'\n    {r.get("source", "")} {r.get("date", "")[:10]}  '
                     + (str(p) if has_pdf else 'NO PDF -> ' + best_link(r)))
        print('  ' + lines[-1].replace('\n', '\n  '))
        if has_pdf:
            copy = TOP_DIR / f'{i:02d}_{p.name}'
            try:
                shutil.copy2(p, copy)
            except OSError:
                copy = p
            if OPEN_TOP:
                open_file(copy, wps)
                time.sleep(0.8)
        elif OPEN_TOP and OPEN_MISSING_TOP_IN_BROWSER:
            open_url(best_link(r))
            time.sleep(0.5)
    (TOP_DIR / 'top10_list.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def fetch_topic_file(i):
    return github_download(FILES[i])


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    print('Local folder:', DEST)
    print('Public GitHub repo:', REPO)
    print('Topic:', TOPIC)
    literature = fetch_topic_file(0)
    external = fetch_topic_file(1)
    if not literature and not external:
        print('ERROR: Neither Excel is available. Make sure the repository is public and Actions committed data/.')
        return 1
    lit = download_literature(literature) if literature else []
    ext = download_external(external) if external else []
    write_missing(lit, ext)
    top_n(lit)
    if OPEN_MISSING_LIST and (DEST / 'missing_pdfs.html').is_file():
        open_url((DEST / 'missing_pdfs.html').resolve().as_uri())
    print('\nDone. Open missing_pdfs.html for one-click links to the papers that were not downloaded.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
