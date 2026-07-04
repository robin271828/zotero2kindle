# zotero2kindle

Send papers from [Zotero](https://www.zotero.org) (or straight from arXiv) to
your Kindle. arXiv papers are recompiled from LaTeX source into single-column,
Kindle-page-sized PDFs; other papers are sent as stored. The Zotero collection
name becomes a `[Topic]` title prefix for grouping on the device.

## Requirements

- [pixi](https://pixi.sh) (`pixi install` once) and `pdflatex` (e.g. MacTeX)
- Zotero 7, running, with Settings → Advanced →
  *"Allow other applications on this computer to communicate with Zotero"*
- Gmail [app password](https://myaccount.google.com/apppasswords); the address
  must be an approved Send-to-Kindle sender

## Setup

`.env` in the repo root (gitignored):

```
GMAIL_ADDRESS=you@gmail.com
KINDLE_EMAIL=you_XXXXXX@kindle.com
GMAIL_APP_PASSWORD=<app password>
ZOTERO_TAG=kindle
```

## Usage

```
pixi run kindle zotero                             # send papers tagged 'kindle' in Zotero
pixi run kindle zotero --dry-run                   # preview; --resend, -t <tag>
pixi run kindle arxiv https://arxiv.org/abs/<id>   # convert + send directly (--no-send: convert only)
pixi run kindle send <pdf> ...                     # email PDFs as-is
pixi run preview <pdf>                             # open a PDF before sending
```

Already-sent Zotero items are tracked in `.sent_to_kindle.json`.
