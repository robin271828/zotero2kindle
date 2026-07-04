# zotero2kindle

Send papers from your [Zotero](https://www.zotero.org) library to your Kindle.
arXiv papers are recompiled from their LaTeX source into a single-column,
Kindle-page-sized PDF; everything else falls back to the PDF stored in Zotero.
Papers are grouped on the Kindle by a `[Topic]` title prefix taken from their
Zotero collection.

## How it works

1. Tag papers in Zotero with `kindle` (or any tag you configure).
2. `pixi run kindle zotero` fetches the tagged items via Zotero's local API.
3. Items with an arXiv ID (detected from the URL, Extra, or DOI field) get
   their LaTeX source downloaded and recompiled at e-reader page size:
   single column, fonts kept readable, oversized figures scaled down or
   rotated to landscape pages. Non-arXiv items use their stored PDF.
4. All PDFs are emailed to your Send-to-Kindle address in as few messages as
   Gmail's size limits allow.
5. Sent items are recorded in `.sent_to_kindle.json`, so the next sync only
   sends new papers.

Papers can also be sent directly from an arXiv URL without going through
Zotero (`pixi run kindle arxiv <url>`).

## Requirements

- [pixi](https://pixi.sh) (`pixi install` once, in the repo)
- A LaTeX distribution providing `pdflatex` (e.g. MacTeX)
- Zotero 7, running, with Settings → Advanced →
  *"Allow other applications on this computer to communicate with Zotero"* enabled
- A Gmail account with an [app password](https://myaccount.google.com/apppasswords),
  and that address on your Kindle's
  [approved senders list](https://www.amazon.com/hz/mycd/myx#/home/settings/payment)

## Setup

Create a `.env` in the repo root (gitignored):

```
GMAIL_ADDRESS=you@gmail.com
KINDLE_EMAIL=you_XXXXXX@kindle.com
GMAIL_APP_PASSWORD=<16-char app password>
ZOTERO_TAG=kindle
```

If `GMAIL_APP_PASSWORD` is unset, the macOS Keychain (service `arxiv2kindle`)
is tried, then an interactive prompt.

## Usage

One CLI, three modes:

```
pixi run kindle zotero                        # send everything newly tagged in Zotero
pixi run kindle zotero --dry-run              # preview what would be sent
pixi run kindle zotero --resend               # include already-sent items
pixi run kindle zotero -t mytag               # use a different Zotero tag

pixi run kindle arxiv https://arxiv.org/abs/<id>   # convert + send directly
pixi run kindle arxiv <url> --no-send              # convert only
pixi run kindle arxiv <url> -w 4 -h 6 -m 0.2 -l    # page size / margin / landscape

pixi run kindle send <pdf> [<pdf> ...]        # email existing PDFs as-is
```

To inspect a converted PDF before sending:

```
pixi run preview <pdf>
```

## Notes

- Papers without LaTeX source on arXiv can't be recompiled; they're sent
  as-is.
- Kindle "Collections" can't be assigned by email, so grouping relies on the
  `[Topic]` title prefix; sort your Kindle library by title.
- The arXiv conversion pipeline builds on
  [Arxiv2Kindle](https://github.com/soumik12345/Arxiv2Kindle) by Soumik
  Rakshit (MIT).
