"""Send papers to a Kindle - single entry point (python -m zotero2kindle).

  arxiv <url>...    convert arXiv papers to Kindle-sized PDFs and send them
  zotero            send papers tagged in Zotero (arXiv items get recompiled)
  send <pdf>...     send existing PDFs as-is

Email configuration comes from .env (see README); already-sent Zotero items
are tracked in .sent_to_kindle.json.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click

from zotero2kindle import ROOT_DIR
from zotero2kindle.arxiv import Arxiv2KindleConverter
from zotero2kindle.mail import load_dotenv, send_pdfs
from zotero2kindle.verify import verify_pdf, render_report, has_failures
from zotero2kindle.zotero import zotero_get, find_arxiv_id, download_stored_pdf

STATE_FILE = ROOT_DIR / '.sent_to_kindle.json'
REPORTS_DIR = ROOT_DIR / 'reports'


def safe_filename(name):
    return re.sub(r'[/\\:*?"<>|]', '-', name).strip()[:150]


def report_issues(pdf, issues):
    """Print verification results plus a visual report; True when sendable."""
    report = render_report(pdf, issues, REPORTS_DIR / Path(pdf).stem)
    for issue in issues:
        print(f'  {issue}')
    if not issues:
        print('  all checks passed')
    print(f'  visual report: {report}')
    return not has_failures(issues)


def convert_arxiv(url, width=4, height=6, margin=0.2, landscape=False, font_size=10,
                  force=False):
    """Convert and verify one arXiv paper.

    Returns the path of a nicely named PDF that passed verification,
    otherwise None (conversion failed or the result would look broken
    on the device - see the printed report for which). With force=True
    a failed verification is reported but the PDF is still returned.
    """
    try:
        converter = Arxiv2KindleConverter(url, landscape, font_size)
        result = converter.execute_pipeline(width, height, margin)
    except Exception as error:
        print(f'Conversion crashed for {url}: {error}')
        return None
    if not result:
        return None
    pdf_file, arxiv_id, title, issues = result
    named = Path(pdf_file).with_name(safe_filename(f'{arxiv_id}_{title}') + '.pdf')
    shutil.move(pdf_file, named)
    print(f'Verifying: {named.name}')
    if not report_issues(named, issues):
        if not force:
            print('  verification FAILED - not sending this conversion '
                  '(check the report; --force sends anyway)')
            return None
        print('  verification FAILED - sending anyway (--force)')
    return named


@click.group()
def cli():
    """Convert papers and send them to your Kindle."""


@cli.command()
@click.argument('urls', nargs=-1, required=True)
@click.option('--width', '-w', default=4, help='Page width in inches')
@click.option('--height', '-h', default=6, help='Page height in inches')
@click.option('--margin', '-m', default=0.2, help='Margin in inches')
@click.option('--landscape', '-l', is_flag=True, help='Landscape output')
@click.option('--font-size', '-f', default=10, help='Base font size in pt')
@click.option('--send/--no-send', 'do_send', default=True,
              help='Email the result to the Kindle (default) or only convert')
@click.option('--force', is_flag=True,
              help='Keep/send PDFs even when verification fails')
def arxiv(urls, width, height, margin, landscape, font_size, do_send, force):
    """Convert arXiv papers to Kindle-sized PDFs and send them."""
    if not 0. < margin < 1.:
        raise click.BadParameter('margin must be between 0 and 1 inch')
    pdfs, failed = [], []
    for url in urls:
        pdf = convert_arxiv(url, width, height, margin, landscape, font_size, force)
        if pdf is None:
            failed.append(url)
            continue
        print(f'PDF File: {pdf}')
        pdfs.append(pdf)
    if do_send and pdfs:
        send_pdfs(pdfs)
    if failed:
        raise click.ClickException(
            'conversion or verification failed for:\n  ' + '\n  '.join(failed))


@cli.command()
@click.option('--tag', '-t', default=None,
              help='Zotero tag marking papers to send (default: ZOTERO_TAG from .env or "kindle")')
@click.option('--dry-run', is_flag=True, help='Only list what would be sent')
@click.option('--resend', is_flag=True, help='Also send items that were sent before')
def zotero(tag, dry_run, resend):
    """Send papers tagged in Zotero; arXiv papers get recompiled for the Kindle."""
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
            pdf = convert_arxiv(f'https://arxiv.org/abs/{arxiv_id}')
            if pdf is None:
                print(f'Conversion failed, falling back to stored PDF: {title}')
        if pdf is None:
            pdf = download_stored_pdf(item['key'], staging_dir)
            if pdf is not None:
                print(f'Checking stored PDF: {title}')
                if not report_issues(pdf, verify_pdf(pdf)):
                    print(f'  stored PDF would be hard to read on the Kindle - '
                          f'skipping: {title}\n'
                          f'  (use "kindle send" to send it anyway)')
                    continue
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


@cli.command()
@click.argument('pdf_paths', type=click.Path(exists=True), nargs=-1, required=True)
@click.option('--gmail', '-g', default=None, help='Gmail address to send from (default: GMAIL_ADDRESS from .env)')
@click.option('--kindle_mail', '-k', default=None, help='Send-to-Kindle address (default: KINDLE_EMAIL from .env)')
def send(pdf_paths, gmail, kindle_mail):
    """Send existing PDFs to the Kindle as-is."""
    send_pdfs(pdf_paths, gmail, kindle_mail)


@cli.command()
@click.argument('pdf_paths', type=click.Path(exists=True), nargs=-1, required=True)
@click.option('--open', '-o', 'do_open', is_flag=True,
              help='Open the visual report in the browser')
def check(pdf_paths, do_open):
    """Verify PDFs for Kindle-readability (layout, fonts, cut content, refs)."""
    failed = []
    for pdf in pdf_paths:
        print(f'Checking: {pdf}')
        ok = report_issues(pdf, verify_pdf(pdf))
        if not ok:
            failed.append(pdf)
        if do_open:
            subprocess.run(['open', REPORTS_DIR / Path(pdf).stem / 'report.html'])
    if failed:
        raise click.ClickException(f'{len(failed)} of {len(pdf_paths)} PDFs failed checks')


if __name__ == '__main__':
    cli()
