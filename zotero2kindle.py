"""Send papers tagged in Zotero to your Kindle.

Queries the Zotero 7 local API (Zotero must be running and 'Allow other
applications on this computer to communicate with Zotero' enabled under
Settings -> Advanced). Items carrying the configured tag (default: 'kindle',
override with ZOTERO_TAG in .env or --tag) are collected; arXiv papers are
recompiled to a Kindle-sized single-column PDF, anything else falls back to
the PDF stored in Zotero. The item's first Zotero collection becomes a
'[Topic] ' prefix on the document title, and everything is emailed in
batches.

Already-sent items are recorded in .sent_to_kindle.json and skipped unless
--resend is given.
"""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import click
import requests

from arxiv2kindle import Arxiv2KindleConverter
from send_to_kindle import load_dotenv, send_pdfs

ZOTERO_API = 'http://localhost:23119/api/users/0'
STATE_FILE = Path(__file__).parent / '.sent_to_kindle.json'
ARXIV_RE = re.compile(r'(?:arxiv\.org/(?:abs|pdf)/|arxiv[:.])(\d{4}\.\d{4,5})', re.I)


def zotero_get(path, **params):
    try:
        response = requests.get(f'{ZOTERO_API}{path}', params=params, timeout=30)
    except requests.ConnectionError:
        raise click.ClickException('Cannot reach Zotero - is it running?')
    if not response.ok:
        raise click.ClickException(
            f'Zotero API error on {path}: {response.status_code} {response.text.strip()}\n'
            'Enable Settings -> Advanced -> "Allow other applications on this '
            'computer to communicate with Zotero".')
    return response


def find_arxiv_id(data):
    for field in ('url', 'extra', 'DOI'):
        m = ARXIV_RE.search(str(data.get(field, '')))
        if m:
            return m.group(1)
    return None


def download_stored_pdf(item_key, dest_dir):
    for child in zotero_get(f'/items/{item_key}/children').json():
        data = child['data']
        if data.get('itemType') == 'attachment' and data.get('contentType') == 'application/pdf':
            pdf = dest_dir / f"{data['key']}.pdf"
            pdf.write_bytes(zotero_get(f"/items/{data['key']}/file").content)
            return pdf
    return None


def safe_filename(name):
    return re.sub(r'[/\\:*?"<>|]', '-', name).strip()[:150]


@click.command()
@click.option('--tag', '-t', default=None, help='Zotero tag marking papers to send (default: ZOTERO_TAG from .env or "kindle")')
@click.option('--dry-run', is_flag=True, help='Only list what would be sent')
@click.option('--resend', is_flag=True, help='Also send items that were sent before')
def main(tag, dry_run, resend):
    load_dotenv()
    tag = tag or os.environ.get('ZOTERO_TAG', 'kindle')
    items = zotero_get('/items', tag=tag, itemType='-attachment', limit=100).json()
    if not items:
        print(f'No Zotero items tagged "{tag}".')
        return
    collections = {c['key']: c['data']['name']
                   for c in zotero_get('/collections', limit=100).json()}
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    staging_dir = Path(tempfile.mkdtemp(prefix='zotero2kindle_'))
    to_send, sent_now = [], {}
    for item in items:
        data = item['data']
        title = data.get('title') or item['key']
        if item['key'] in state and not resend:
            print(f'Skipping (already sent): {title}')
            continue
        topic = collections.get((data.get('collections') or [None])[0])
        arxiv_id = find_arxiv_id(data)
        source = f'arxiv:{arxiv_id}' if arxiv_id else 'stored PDF'
        filename = safe_filename(f'[{topic}] {title}' if topic else title) + '.pdf'
        if dry_run:
            print(f'Would send: {filename}  ({source})')
            continue

        print(f'Preparing: {filename}  ({source})')
        pdf = None
        if arxiv_id:
            converter = Arxiv2KindleConverter(f'https://arxiv.org/abs/{arxiv_id}', False)
            result = converter.execute_pipeline(width=4, height=6, margin=0.2)
            if result:
                pdf = Path(result[0])
            else:
                print(f'Conversion failed, falling back to stored PDF: {title}')
        if pdf is None:
            pdf = download_stored_pdf(item['key'], staging_dir)
        if pdf is None:
            print(f'No PDF available, skipping: {title}')
            continue
        to_send.append(shutil.copy(pdf, staging_dir / filename))
        sent_now[item['key']] = title

    if dry_run or not to_send:
        return
    send_pdfs(to_send)
    state.update(sent_now)
    STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == '__main__':
    main()
