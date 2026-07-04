"""Thin client for the Zotero 7 local API.

Zotero must be running with 'Allow other applications on this computer to
communicate with Zotero' enabled under Settings -> Advanced.
"""
import re

import click
import requests

ZOTERO_API = 'http://localhost:23119/api/users/0'
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
