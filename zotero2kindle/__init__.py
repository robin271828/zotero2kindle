from pathlib import Path

# repo root: where .env and .sent_to_kindle.json live
ROOT_DIR = Path(__file__).resolve().parent.parent

__all__ = ['ROOT_DIR', 'Arxiv2KindleConverter', 'verify_pdf', 'render_report',
           'has_failures', 'send_pdfs']


def __getattr__(name):
    """Lazy re-exports of the public API, keeping package import light."""
    if name == 'Arxiv2KindleConverter':
        from zotero2kindle.arxiv import Arxiv2KindleConverter
        return Arxiv2KindleConverter
    if name in ('verify_pdf', 'render_report', 'has_failures'):
        from zotero2kindle import verify
        return getattr(verify, name)
    if name == 'send_pdfs':
        from zotero2kindle.mail import send_pdfs
        return send_pdfs
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
