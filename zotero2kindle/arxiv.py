"""Download an arXiv paper's LaTeX source and recompile it at Kindle page size.

Based on Arxiv2Kindle by Soumik Rakshit (MIT,
https://github.com/soumik12345/Arxiv2Kindle).
"""
import os
import re
import sys
import wget
import shutil
import tarfile
import tempfile
import requests
import subprocess
from glob import glob
import lxml.html as html
from pathlib import Path

from zotero2kindle.tex import KindleTexTransformer, STY_FILE


class Arxiv2KindleConverter:

    def __init__(self, arxiv_url: str, is_landscape: bool, font_size: int = 10) -> None:
        self.arxiv_url = arxiv_url
        self.is_landscape = is_landscape
        self.font_size = font_size
        self.check_prerequisite()

    def check_prerequisite(self):
        result = subprocess.run(["pdflatex", "--version"], stdout=None, stderr=None)
        if result.returncode != 0:
            raise SystemError("no pdflatex found")
        if self.is_landscape:
            result = subprocess.run(["pdftk", "--version"], stdout=None, stderr=None)
            if result.returncode != 0:
                raise SystemError("no pdftk found (required for landscape mode)")

    def download_source(self):
        arxiv_id = re.match(r'((http|https)://.*?/)?(?P<id>\d{4}\.\d{4,5}(v\d{1,2})?)', self.arxiv_url).group('id')
        arxiv_abs = f'http://arxiv.org/abs/{arxiv_id}'
        arxiv_pgtitle = html.fromstring(
            requests.get(arxiv_abs).text.encode('utf8')).xpath('/html/head/title/text()')[0]
        arxiv_title = re.sub(r'\s+', ' ', re.sub(r'^\[[^]]+\]\s*', '', arxiv_pgtitle), re.DOTALL)
        # create temporary directory
        arxiv_dir = tempfile.mkdtemp(prefix='arxiv2kindle_')
        archive_url = f'http://arxiv.org/e-print/{arxiv_id}'
        # download tar.gz file and add file extension
        tar_filename = wget.download(
            archive_url, out=os.path.join(
                arxiv_dir, ''.join([arxiv_title, '.tar.gz'])))
        if not Path(tar_filename).exists():
            raise SystemError('Paper sources are not available')
        with tarfile.open(tar_filename) as f:
            f.extractall(arxiv_dir)
        return arxiv_dir, arxiv_id, arxiv_title

    def process_tex(self, arxiv_dir, geometric_settings):
        texfiles = glob(os.path.join(arxiv_dir, '*.tex'))
        for texfile in texfiles:
            with open(texfile, 'r') as f:
                src = f.readlines()
            if 'documentclass' in src[0]:
                print('correct file: ' + texfile)
                break
        transformer = KindleTexTransformer(geometric_settings, self.is_landscape, self.font_size)
        src = transformer.transform(src)
        shutil.copy(STY_FILE, Path(texfile).parent)
        os.rename(texfile, texfile + '.bak')
        with open(texfile, 'w') as f:
            f.writelines(src)

        def pdflatex():
            subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', texfile],
                stdout=sys.stderr, cwd=Path(texfile).parent
            )

        base = Path(texfile).with_suffix('')
        pdflatex()
        # papers shipping a .bib database instead of a precompiled .bbl need
        # bibtex/biber, otherwise citations render as question marks
        aux = base.with_suffix('.aux')
        if base.with_suffix('.bcf').exists():
            subprocess.run(['biber', base.name], stdout=sys.stderr, cwd=base.parent)
        elif aux.exists() and r'\bibdata' in aux.read_text(errors='ignore'):
            subprocess.run(['bibtex', base.name], stdout=sys.stderr, cwd=base.parent)
        # two more passes so citations and cross-references resolve
        pdflatex()
        pdflatex()
        return texfile[:-4] + '.pdf'

    def execute_pipeline(self, width: int, height: int, margin: float):
        arxiv_dir, arxiv_id, arxiv_title = self.download_source()
        print(f'\nArxiv Directory: {arxiv_dir}')
        print(f'Arxiv Title: {arxiv_title}')
        if self.is_landscape:
            width, height = height, width
        geometric_settings = dict(
            paperwidth=f'{width}in',
            paperheight=f'{height}in',
            margin=f'{margin}in'
        )
        try:
            pdf_file = self.process_tex(arxiv_dir, geometric_settings)
            return pdf_file, arxiv_id, arxiv_title
        except KeyError:
            print('Unable to create pdf file')
            shutil.rmtree(arxiv_dir, ignore_errors=True)
