"""Send PDFs to a Kindle via Gmail SMTP.

Configuration is read from a .env file next to this script (see .env):
  GMAIL_ADDRESS       Gmail address to send from
  KINDLE_EMAIL        Send-to-Kindle address
  GMAIL_APP_PASSWORD  Gmail app password (https://myaccount.google.com/apppasswords)

If GMAIL_APP_PASSWORD is unset, the macOS Keychain item with service name
'arxiv2kindle' is tried next, then an interactive prompt:
  security add-generic-password -a <gmail address> -s arxiv2kindle -w
"""
import os
import subprocess
from getpass import getpass
from pathlib import Path

import click
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

KEYCHAIN_SERVICE = 'arxiv2kindle'
# Gmail rejects messages over 25MB; base64 encoding adds ~37%
MAX_BATCH_BYTES = 17 * 1024 * 1024
# Send-to-Kindle accepts at most 25 documents per email
MAX_BATCH_FILES = 25


def load_dotenv():
    env_file = Path(__file__).parent / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip())


def get_app_password(gmail):
    password = os.environ.get('GMAIL_APP_PASSWORD')
    if password:
        return password
    result = subprocess.run(
        ['security', 'find-generic-password', '-a', gmail, '-s', KEYCHAIN_SERVICE, '-w'],
        capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return getpass(prompt='Enter Gmail app password: ')


def batch_pdfs(pdf_paths):
    """Split PDFs into batches that fit in a single email."""
    batches, batch, batch_bytes = [], [], 0
    for path in pdf_paths:
        size = path.stat().st_size
        if batch and (batch_bytes + size > MAX_BATCH_BYTES or len(batch) >= MAX_BATCH_FILES):
            batches.append(batch)
            batch, batch_bytes = [], 0
        batch.append(path)
        batch_bytes += size
    if batch:
        batches.append(batch)
    return batches


def send_pdfs(pdf_paths, gmail=None, kindle_mail=None):
    """Email PDFs to a Kindle, batching them to respect size limits."""
    load_dotenv()
    gmail = gmail or os.environ.get('GMAIL_ADDRESS')
    kindle_mail = kindle_mail or os.environ.get('KINDLE_EMAIL')
    if not gmail or not kindle_mail:
        raise click.UsageError('Set GMAIL_ADDRESS and KINDLE_EMAIL in .env or pass -g/-k.')
    pdf_paths = [Path(p) for p in pdf_paths]
    password = get_app_password(gmail)

    for batch in batch_pdfs(pdf_paths):
        msg = MIMEMultipart()
        msg['From'] = gmail
        msg['To'] = kindle_mail
        msg['Subject'] = batch[0].stem if len(batch) == 1 else f'{len(batch)} papers'
        for path in batch:
            pdf_part = MIMEApplication(path.read_bytes(), _subtype='pdf')
            pdf_part.add_header('Content-Disposition', 'attachment', filename=path.name)
            msg.attach(pdf_part)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(gmail, password)
        server.sendmail(gmail, kindle_mail, msg.as_string())
        server.quit()
        for path in batch:
            print(f'Sent {path.name} to {kindle_mail}')

