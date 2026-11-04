"""Download legally open-access PDFs for rows in literature.xlsx / literature.csv.
Python 3.9+, standard library only. Run on your Windows PC.
"""
from __future__ import annotations
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

DEST = Path(r'C:\Users\xli9\OneDrive - McGill University\Desktop\manu_12_TIM_APEN\literature2')
USER_AGENT = 'WaterEnergyLiteraturePDF/1.0 (personal academic OA access)'
NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}


def read_xlsx(path):
    """Read first worksheet using only Python's standard library."""
    with zipfile.ZipFile(path) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('x:si', NS):
                strings.append(''.join(t.text or '' for t in si.findall('.//x:t', NS)))
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        sheet = wb.find('x:sheets/x:sheet', NS)
        rel_id = sheet.attrib.get('{' + NS['r'] + '}id')
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next((r.attrib['Target'] for r in rels if r.attrib['Id'] == rel_id), 'worksheets/sheet1.xml')
        if target.startswith('/'):
            sheet_path = target.lstrip('/')
        elif target.startswith('xl/'):
            sheet_path = target
        else:
            sheet_path = 'xl/' + target
        root = ET.fromstring(z.read(sheet_path))
        output = []
        for row in root.findall('.//x:sheetData/x:row', NS):
            values = {}
            for cell in row.findall('x:c', NS):
                col = re.match(r'[A-Z]+', cell.attrib['r']).group()
                idx = 0
                for char in col:
                    idx = idx * 26 + ord(char) - 64
                kind = cell.attrib.get('t')
                v = cell.find('x:v', NS)
                if kind == 'inlineStr':
                    val = ''.join(t.text or '' for t in cell.findall('.//x:t', NS))
                elif v is None:
                    val = ''
                elif kind == 's':
                    val = strings[int(v.text)]
                else:
                    val = v.text or ''
                values[idx-1] = val
            if values:
                output.append([values.get(i, '') for i in range(max(values) + 1)])
        if not output:
            return []
        header = [str(x).strip().lower() for x in output[0]]
        return [dict(zip(header, row + [''] * (len(header) - len(row)))) for row in output[1:]]


def load_rows(path):
    if path.suffix.lower() == '.xlsx':
        return read_xlsx(path)
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == 2:
                raise RuntimeError(str(exc)) from exc
            time.sleep(2 ** attempt)


def safe_name(text, maxlen=95):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text or 'Unknown')).strip(' .')
    return (text[:maxlen].rstrip(' .') or 'Unknown')


def openalex_work(row):
    oa = str(row.get('openalex_id') or '').strip()
    doi = str(row.get('doi') or '').strip()
    if oa:
        work_id = oa.rsplit('/', 1)[-1]
        if re.fullmatch(r'W\d+', work_id):
            return get_json('https://api.openalex.org/works/' + work_id)
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi, flags=re.I)
    if doi:
        return get_json('https://api.openalex.org/works/https://doi.org/' + urllib.parse.quote(doi, safe='/'))
    return None


def candidates(work):
    """OpenAlex provides OA locations; do not scrape publisher paywalls."""
    locations = [work.get('best_oa_location'), work.get('primary_location')]
    locations += work.get('locations') or []
    seen = set()
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        if not (loc.get('is_oa') or loc.get('landing_page_url', '').startswith('https://arxiv.org/')):
            continue
        url = loc.get('pdf_url')
        if not url or not url.startswith('https://') or url in seen:
            continue
        seen.add(url)
        yield url
    # OpenAlex may not have the direct PDF for arXiv, even if an arXiv DOI is available.
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        landing = loc.get('landing_page_url') or ''
        match = re.search(r'https://arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)', landing)
        if match:
            url = 'https://arxiv.org/pdf/' + match.group(1)
            if url not in seen:
                seen.add(url)
                yield url


def download_pdf(url, target, max_bytes=60 * 1024 * 1024):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/pdf'})
    tmp = target.with_suffix('.part')
    try:
        with urllib.request.urlopen(req, timeout=50) as response, tmp.open('wb') as f:
            first = response.read(8192)
            if not first.lstrip().startswith(b'%PDF-'):
                return False, 'Not a PDF (HTML/login/blocked)'
            f.write(first)
            count = len(first)
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > max_bytes:
                    return False, 'PDF exceeds 60 MB limit'
                f.write(chunk)
        tmp.replace(target)
        return True, 'Downloaded'
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, str(exc)[:180]
    finally:
        if tmp.exists():
            tmp.unlink()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    # Optional argument: python download_pdfs.py "C:\\path\\literature.xlsx"
    if len(sys.argv) > 1:
        source = Path(sys.argv[1])
    else:
        source = next((DEST / name for name in ('literature.xlsx', 'literature.csv') if (DEST / name).exists()), None)
    if not source or not source.exists():
        print('ERROR: Put literature.xlsx in this folder first:', DEST)
        input('Press Enter to exit...')
        return 1
    rows = load_rows(source)
    manifest = DEST / 'pdf_download_status.csv'
    previous = {}
    if manifest.exists():
        with manifest.open(encoding='utf-8-sig', newline='') as f:
            previous = {r.get('key', ''): r for r in csv.DictReader(f)}
    status = []
    count = 0
    for i, row in enumerate(rows, 1):
        title = str(row.get('title') or '').strip()
        if not title:
            continue
        key = str(row.get('key') or row.get('doi') or row.get('openalex_id') or title)
        year = str(row.get('published') or '')[:4]
        if not re.fullmatch(r'\d{4}', year):
            year = 'UnknownYear'
        journal = safe_name(row.get('journal') or row.get('source_platform') or 'UnknownSource', 55)
        folder = DEST / year / journal
        folder.mkdir(parents=True, exist_ok=True)
        identity = safe_name(str(row.get('openalex_id') or row.get('doi') or i).rsplit('/', 1)[-1], 30)
        target = folder / (safe_name(title, 90) + '__' + identity + '.pdf')
        result = {'key': key, 'title': title, 'doi': row.get('doi', ''),
                  'journal': row.get('journal', ''), 'published': row.get('published', ''),
                  'pdf_status': '', 'pdf_url': '', 'pdf_path': '', 'checked_at': datetime.now().isoformat(timespec='seconds')}
        old = previous.get(key, {})
        old_path = Path(old.get('pdf_path') or '') if old.get('pdf_path') else None
        if target.exists() or (old_path and old_path.is_file()):
            result.update(pdf_status='Already downloaded', pdf_url=old.get('pdf_url', ''),
                          pdf_path=str(target if target.exists() else old_path))
        else:
            try:
                work = openalex_work(row)
                urls = list(candidates(work)) if work else []
                if not urls:
                    result['pdf_status'] = 'No openly accessible PDF found'
                else:
                    failures = []
                    for url in urls:
                        ok, message = download_pdf(url, target)
                        if ok:
                            result.update(pdf_status='Downloaded', pdf_url=url, pdf_path=str(target))
                            count += 1
                            break
                        failures.append(message)
                    if not result['pdf_status']:
                        result['pdf_status'] = 'Download failed: ' + '; '.join(failures[:2])
            except Exception as exc:
                result['pdf_status'] = 'Lookup failed: ' + str(exc)[:180]
        status.append(result)
        print(f'[{i}/{len(rows)}] {result["pdf_status"]}: {title[:70]}', flush=True)
        if result['pdf_status'] not in ('Already downloaded',):
            time.sleep(0.25)
        # Save progress after every row so interruption does not lose the manifest.
        with manifest.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(result))
            writer.writeheader()
            writer.writerows(status)
    print(f'Finished. New PDFs: {count}. Status file: {manifest}')
    input('Press Enter to close...')
    return 0


if __name__ == '__main__':
    sys.exit(main())
