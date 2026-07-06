"""Download an arXiv paper's LaTeX source and recompile it at Kindle page size.

Based on Arxiv2Kindle by Soumik Rakshit (MIT,
https://github.com/soumik12345/Arxiv2Kindle).
"""
import gzip
import os
import re
import sys
import time
import shutil
import tarfile
import tempfile
import requests
import subprocess
from glob import glob
import lxml.html as html
from pathlib import Path

from zotero2kindle.tex import KindleTexTransformer, STY_FILE
from zotero2kindle.verify import verify_pdf

ARXIV_ID_RE = re.compile(r'((http|https)://.*?/)?(?P<id>\d{4}\.\d{4,5}(v\d{1,2})?)')
# environments whose font (and therefore width) the remediation ladder shrinks
SHRINKABLE_ENVS = ('tabular', 'longtable', 'algorithm')


def _remediation_level(size, tabcolsep, extra=()):
    # \everydisplay is appended at begin-document because packages like
    # mathtools overwrite the register when they load
    return ([f'\\AtBeginDocument{{\\everydisplay\\expandafter'
             f'{{\\the\\everydisplay\\{size}}}}}\n',
             f'\\setlength{{\\tabcolsep}}{{{tabcolsep}}}\n',
             '\\kindlefittables\n']
            + [f'\\AtBeginEnvironment{{{env}}}{{\\{size}}}\n'
               for env in SHRINKABLE_ENVS]
            + list(extra))


# progressively stronger layout tweaks, applied only when verification
# still finds content cut off at the page edge (wide formulas/tables)
REMEDIATION_LEVELS = [
    [],
    _remediation_level('small', '3pt'),
    _remediation_level('footnotesize', '2pt'),
    _remediation_level('scriptsize', '1pt', ['\\geometry{margin=0.12in}\n']),
]

# issue codes that make the pipeline retry with the next remediation level
RETRY_CODES = ('cut-content',)


class ConversionError(Exception):
    pass


def _get(url, timeout, attempts=3):
    """GET with retries; arXiv throttles bursts of e-print downloads."""
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            if attempt == attempts - 1:
                raise
            delay = 30 * (attempt + 1)
            print(f'download failed ({error}); retrying in {delay}s')
            time.sleep(delay)


class Arxiv2KindleConverter:

    def __init__(self, arxiv_url: str, is_landscape: bool, font_size: int = 10) -> None:
        self.arxiv_url = arxiv_url
        self.is_landscape = is_landscape
        self.font_size = font_size
        self.check_prerequisite()

    def check_prerequisite(self):
        for tool, needed in (('pdflatex', True), ('pdftk', self.is_landscape)):
            if needed and shutil.which(tool) is None:
                raise ConversionError(f'no {tool} found on PATH')

    def download_source(self):
        """Fetch the e-print into a fresh temp dir; returns (dir, id, title).

        arXiv serves either a tar archive, a gzipped single .tex file, or
        (for papers submitted as PDF) a bare PDF, which has no source to
        recompile.
        """
        match = ARXIV_ID_RE.match(self.arxiv_url)
        if not match:
            raise ConversionError(f'cannot parse an arXiv id out of {self.arxiv_url!r}')
        arxiv_id = match.group('id')
        page = _get(f'https://arxiv.org/abs/{arxiv_id}', timeout=60)
        pgtitle = html.fromstring(page.text.encode('utf8')).xpath('/html/head/title/text()')[0]
        arxiv_title = re.sub(r'\s+', ' ', re.sub(r'^\[[^]]+\]\s*', '', pgtitle))

        arxiv_dir = tempfile.mkdtemp(prefix='arxiv2kindle_')
        eprint = _get(f'https://arxiv.org/e-print/{arxiv_id}', timeout=300)
        archive = Path(arxiv_dir) / 'source'
        archive.write_bytes(eprint.content)
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as f:
                f.extractall(arxiv_dir, filter='data')
        elif eprint.content[:2] == b'\x1f\x8b':  # gzipped single source file
            (Path(arxiv_dir) / 'main.tex').write_bytes(gzip.decompress(eprint.content))
        elif eprint.content[:5] == b'%PDF-':
            raise ConversionError(f'{arxiv_id} was submitted as PDF; no LaTeX source')
        else:
            raise ConversionError(f'unrecognized e-print format for {arxiv_id}')
        return arxiv_dir, arxiv_id, arxiv_title

    def find_main_texfile(self, arxiv_dir):
        candidates = []
        for texfile in glob(os.path.join(arxiv_dir, '**', '*.tex'), recursive=True):
            text = Path(texfile).read_text(errors='ignore')
            if r'\documentclass' in text:
                candidates.append((r'\begin{document}' in text, len(text), texfile))
        if not candidates:
            raise ConversionError('no .tex file with \\documentclass found')
        return max(candidates)[2]

    def process_tex(self, arxiv_dir, geometric_settings, level=None):
        """(Re)write the sources and compile; safe to call repeatedly.

        Originals are kept as .bak files so every call rewrites from a
        pristine copy - that is what makes the remediation retries work.
        level is a REMEDIATION_LEVELS entry (extra preamble lines) or None.
        """
        transformer = KindleTexTransformer(
            geometric_settings, self.is_landscape, self.font_size,
            extra_preamble=level or ())
        mainfile = self.find_main_texfile(arxiv_dir)
        print('main file: ' + mainfile)
        shutil.copy(STY_FILE, Path(mainfile).parent)
        for texfile in glob(os.path.join(arxiv_dir, '**', '*.tex'), recursive=True):
            bak = Path(texfile + '.bak')
            if not bak.exists():
                shutil.copy(texfile, bak)
            with open(bak) as f:
                src = f.readlines()
            src = (transformer.transform(src) if texfile == mainfile
                   else transformer.transform_body(src))
            with open(texfile, 'w') as f:
                f.writelines(src)

        def pdflatex():
            subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', mainfile],
                stdout=sys.stderr, cwd=Path(mainfile).parent
            )

        base = Path(mainfile).with_suffix('')
        pdflatex()
        # papers shipping a .bib database need bibtex/biber, otherwise
        # citations render as question marks; papers shipping only a
        # precompiled .bbl must NOT run bibtex - it would overwrite the
        # good .bbl with an empty one and break every citation
        aux = base.with_suffix('.aux')
        has_bib = bool(glob(os.path.join(arxiv_dir, '**', '*.bib'), recursive=True))
        if has_bib and base.with_suffix('.bcf').exists():
            subprocess.run(['biber', base.name], stdout=sys.stderr, cwd=base.parent)
        elif has_bib and aux.exists() and r'\bibdata' in aux.read_text(errors='ignore'):
            subprocess.run(['bibtex', base.name], stdout=sys.stderr, cwd=base.parent)
        # two more passes so citations and cross-references resolve
        pdflatex()
        pdflatex()
        pdf_file = base.with_suffix('.pdf')
        if not pdf_file.exists():
            log = base.with_suffix('.log')
            tail = log.read_text(errors='ignore')[-2000:] if log.exists() else ''
            raise ConversionError(f'pdflatex produced no PDF for {mainfile}\n{tail}')
        return str(pdf_file)

    def execute_pipeline(self, width: int, height: int, margin: float):
        """Convert; returns (pdf_file, arxiv_id, title, issues) or None.

        Compiles, verifies the result, and recompiles with progressively
        stronger layout tweaks while verification still finds content cut
        off at the page edge.
        """
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
        expect_size = (width * 72, height * 72)
        try:
            pdf_file = issues = None
            for levelno, level in enumerate(REMEDIATION_LEVELS):
                try:
                    attempt = self.process_tex(arxiv_dir, geometric_settings, level)
                except ConversionError as error:
                    if pdf_file is None:
                        raise
                    print(f'remediation level {levelno} failed to compile, '
                          f'keeping the previous result ({error})')
                    break
                pdf_file, issues = attempt, verify_pdf(attempt, expect_size)
                if not any(i.code in RETRY_CODES and i.severity == 'fail'
                           for i in issues):
                    break
                if levelno < len(REMEDIATION_LEVELS) - 1:
                    print('content cut off or side-by-side layout; retrying '
                          'with stronger layout remediation')
            return pdf_file, arxiv_id, arxiv_title, issues
        except Exception as error:
            print(f'Unable to create pdf file: {error}\n'
                  f'sources kept for inspection in {arxiv_dir}')
