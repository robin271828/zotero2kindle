"""Post-conversion checks for Kindle-ready PDFs.

Everything here is heuristic, but each check corresponds to a failure mode
seen in practice: layouts that stay two-column, formulas/tables/figures cut
off at the page edge, unreadably small text, mixed body font sizes, and
citations/references that render as "?" because the bibliography broke.

verify_pdf() returns a list of Issue objects; render_report() renders the
offending pages to PNGs next to an HTML report so problems can be checked
visually before anything is sent to the device.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import fitz

# content sticking out further than this (pt) loses more than a character
CUT_FAIL_PT = 6.0
CUT_WARN_PT = 1.5
# a footer a few pt over the bottom is noise; a float taller than the
# page loses real content
CUT_BOTTOM_PT = 12.0
TINY_PT = 6.0
TINY_SHARE = 0.3
MIN_PAGE_CHARS = 200  # ignore near-empty pages for font statistics
REF_RE = re.compile(r'\(\?+(?:,\s*\?+)*\)|\[\?+\]|(?<![\w?])\?\?(?![\w?])')


def _full_textpage(page):
    """Text extraction normally clips to the page; use a much larger clip so
    content sticking out beyond the page edge (= cut off on the device) is
    seen instead of silently dropped."""
    margin = 2000
    clip = fitz.Rect(-margin, -margin,
                     page.rect.width + margin, page.rect.height + margin)
    return page.get_textpage(clip=clip)


@dataclass
class Issue:
    severity: str  # 'fail' or 'warn'
    code: str
    message: str
    pages: list = field(default_factory=list)

    def __str__(self):
        pages = ''
        if self.pages:
            shown = ','.join(str(p) for p in self.pages[:8])
            more = f',… ({len(self.pages)} pages)' if len(self.pages) > 8 else ''
            pages = f' [page {shown}{more}]'
        return f'{self.severity.upper()}: {self.message}{pages}'


def _text_lines(page, textpage):
    """(x0, x1, nchars) per horizontal text line, skipping trivial fragments."""
    lines = []
    for block in page.get_text('dict', textpage=textpage)['blocks']:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            text = ''.join(s['text'] for s in line['spans']).strip()
            if len(text) >= 3 and abs(line['dir'][0]) > 0.9:
                x0, _, x1, _ = line['bbox']
                lines.append((x0, x1, len(text)))
    return lines


def _is_two_column(page, textpage):
    """True if the page shows two side-by-side stacks of text lines.

    Needs many lines on both sides so pages where text merely wraps around
    a figure (short caption stack next to a narrow paragraph) don't count.
    """
    w = page.rect.width
    lines = _text_lines(page, textpage)
    # substantial lines that live entirely in the left / right half
    left = [l for l in lines if l[0] < 0.25 * w and l[1] < 0.62 * w and l[1] - l[0] > 0.25 * w]
    right = [l for l in lines if 0.42 * w < l[0] < 0.62 * w and l[1] > 0.75 * w]
    return min(len(left), len(right)) >= 10


def _page_fonts(page, textpage):
    """Character-weighted font family and size counters for a page.

    Families are counted over body-sized text only, so pages dominated by
    figures (plot labels in their own fonts) don't read as a font change.
    """
    families, sizes = Counter(), Counter()
    for block in page.get_text('dict', textpage=textpage)['blocks']:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            for span in line['spans']:
                n = len(span['text'].strip())
                if not n:
                    continue
                sizes[round(span['size'])] += n
                if 8 <= span['size'] <= 13:
                    family = re.sub(r'^[A-Z]{6}\+', '', span['font']).split('-')[0]
                    families[family] += n
    return families, sizes


def _cut_amount(bbox, rect):
    """(horizontal, vertical) pt a bbox sticks out beyond the page."""
    x0, y0, x1, y1 = bbox
    return (max(0.0, x1 - rect.x1, rect.x0 - x0),
            max(0.0, y1 - rect.y1, rect.y0 - y0))


def _scan_page(page):
    """Collect the per-page facts all checks are built from."""
    textpage = _full_textpage(page)
    worst_h = worst_v = rotated = 0.0
    for block in page.get_text('dict', textpage=textpage)['blocks']:
        if block['type'] == 1:  # image
            h, v = _cut_amount(block['bbox'], page.rect)
            worst_h, worst_v = max(worst_h, h), max(worst_v, v)
            continue
        for line in block['lines']:
            for span in line['spans']:
                if not span['text'].strip():
                    continue
                h, v = _cut_amount(span['bbox'], page.rect)
                if abs(line['dir'][0]) > 0.9:
                    worst_h, worst_v = max(worst_h, h), max(worst_v, v)
                else:  # sideways tables, watermarks, sidebars
                    rotated = max(rotated, h, v)
    families, char_sizes = _page_fonts(page, textpage)
    return {
        'two_column': _is_two_column(page, textpage),
        'unresolved_refs': len(REF_RE.findall(page.get_text(textpage=textpage))),
        'cut_h': worst_h, 'cut_v': worst_v, 'cut_rotated': rotated,
        'families': families, 'char_sizes': char_sizes,
    }


def _check_page_sizes(doc, expect_size, issues):
    sizes = Counter((round(p.rect.width), round(p.rect.height)) for p in doc)
    if expect_size:
        ew, eh = expect_size
        bad = [i + 1 for i, p in enumerate(doc)
               if abs(p.rect.width - ew) > 3 or abs(p.rect.height - eh) > 3]
        if bad:
            issues.append(Issue('fail', 'page-size',
                                f'page size is not the expected {ew:.0f}x{eh:.0f}pt '
                                f'(found {dict(sizes)})', bad))
    elif len(sizes) > 1:
        issues.append(Issue('warn', 'page-size', f'mixed page sizes: {dict(sizes)}'))


def _ink_overflow(pdf_path, suspects, margin=120):
    """Render-confirm text-reported overflow beyond the right/bottom edge.

    Text extraction sees glyphs that never render - e.g. figure content the
    author cropped away with trim/clip. Widening the mediabox and looking
    for actual ink beyond the original page edge separates content that is
    really cut off from content that is hidden by design.

    Returns {pageno: (right_pt, bottom_pt)}.
    """
    if not suspects:
        return {}
    result = {}
    doc = fitz.open(pdf_path)
    for pageno in suspects:
        page = doc[pageno - 1]
        w, h = page.rect.width, page.rect.height
        page.set_mediabox(fitz.Rect(0, 0, w + margin, h + margin))
        def inked_depth(strip, rightwards):
            """Furthest inked pt into the strip, along x or y."""
            pix = page.get_pixmap(dpi=72, clip=strip)  # ~1px per pt
            samples, stride, n = pix.samples, pix.stride, pix.n
            worst = 0
            for y in range(pix.height):
                row = samples[y * stride:y * stride + pix.width * n]
                if all(c >= 245 for c in row):
                    continue
                if not rightwards:
                    worst = y + 1  # rows scan top-down; keep the deepest
                    continue
                for x in range(pix.width - 1, worst - 1, -1):
                    if any(c < 245 for c in row[x * n:x * n + 3]):
                        worst = x + 1
                        break
            return float(worst)

        result[pageno] = (
            inked_depth(fitz.Rect(w, 0, w + margin, h), rightwards=True),
            inked_depth(fitz.Rect(0, h, w + margin, h + margin), rightwards=False))
    doc.close()
    return result


def _check_cut_content(pdf_path, scans, issues):
    suspects = [p for p, s in scans.items()
                if max(s['cut_h'], s['cut_rotated']) >= CUT_WARN_PT
                or s['cut_v'] >= CUT_BOTTOM_PT]
    ink = _ink_overflow(pdf_path, suspects)
    real_h = {p: min(scans[p]['cut_h'], ink[p][0]) for p in ink}
    real_rot = {p: min(scans[p]['cut_rotated'], ink[p][0]) for p in ink}
    real_v = {p: min(scans[p]['cut_v'], ink[p][1]) for p in ink}

    fail = {p: round(v, 1) for p, v in real_h.items() if v >= CUT_FAIL_PT}
    warn = {p: round(v, 1) for p, v in real_h.items()
            if CUT_WARN_PT <= v < CUT_FAIL_PT}
    rotated = {p: round(v, 1) for p, v in real_rot.items()
               if v >= CUT_FAIL_PT and p not in fail}
    bottom = {p: round(v, 1) for p, v in real_v.items() if v >= CUT_BOTTOM_PT}
    if fail:
        issues.append(Issue('fail', 'cut-content',
                            'content cut off at the page edge '
                            f'(worst overhang per page, pt: {fail})', list(fail)))
    if warn:
        issues.append(Issue('warn', 'cut-content',
                            f'content slightly over the page edge (pt: {warn})',
                            list(warn)))
    if rotated:
        issues.append(Issue('warn', 'cut-rotated',
                            'sideways content (rotated tables/watermarks) extends '
                            f'beyond the page (pt: {rotated})', list(rotated)))
    if bottom:
        issues.append(Issue('warn', 'cut-bottom',
                            'content runs past the bottom edge, probably an '
                            f'unbreakable float taller than the page (pt: {bottom})',
                            list(bottom)))


def _check_fonts(scans, issues):
    font_of_page, size_of_page, tiny = {}, {}, []
    for pageno, s in scans.items():
        total = sum(s['char_sizes'].values())
        if total < MIN_PAGE_CHARS:
            continue
        if sum(s['families'].values()) >= MIN_PAGE_CHARS:
            font_of_page[pageno] = s['families'].most_common(1)[0][0]
        size_of_page[pageno] = s['char_sizes'].most_common(1)[0][0]
        small = sum(n for size, n in s['char_sizes'].items() if size < TINY_PT)
        if small > TINY_SHARE * total:
            tiny.append(pageno)

    if tiny:
        issues.append(Issue('warn', 'tiny-text',
                            f'mostly tiny text (<{TINY_PT:.0f}pt), unreadable on Kindle', tiny))
    main_fonts = Counter(font_of_page.values())
    if len(main_fonts) > 1:
        expected = main_fonts.most_common(1)[0][0]
        odd = [p for p, f in font_of_page.items() if f != expected]
        # a few odd pages are usually figures, code listings, or sans-serif
        # caption blocks; only a widespread change of the body font (a
        # broken font setup) should block sending
        severity = 'fail' if len(odd) > max(3, 0.25 * len(font_of_page)) else 'warn'
        issues.append(Issue(severity, 'font-mix',
                            f'dominant font changes between pages ({dict(main_fonts)})', odd))
    main_sizes = Counter(size_of_page.values())
    if main_sizes and max(main_sizes) - min(main_sizes) > 1:
        expected = main_sizes.most_common(1)[0][0]
        odd = [p for p, s in size_of_page.items() if abs(s - expected) > 1]
        issues.append(Issue('warn', 'size-mix',
                            f'dominant text size varies between pages ({dict(main_sizes)})', odd))


def verify_pdf(pdf_path, expect_size=None):
    """Run all checks on a PDF; expect_size is (width_pt, height_pt) or None."""
    doc = fitz.open(pdf_path)
    issues = []
    _check_page_sizes(doc, expect_size, issues)
    scans = {i + 1: _scan_page(page) for i, page in enumerate(doc)}

    twocol = [p for p, s in scans.items() if s['two_column']]
    if twocol:
        issues.append(Issue('fail', 'two-column',
                            'text is laid out in two columns', twocol))
    _check_cut_content(pdf_path, scans, issues)
    _check_fonts(scans, issues)
    refs = sum(s['unresolved_refs'] for s in scans.values())
    if refs:
        issues.append(Issue('fail' if refs >= 3 else 'warn', 'unresolved-refs',
                            f'{refs} unresolved citations/references rendered as "?"'))
    doc.close()
    return issues


def has_failures(issues):
    return any(i.severity == 'fail' for i in issues)


def render_report(pdf_path, issues, out_dir, max_pages=12, dpi=100):
    """Render flagged pages (plus the first two as a sample) into an HTML report.

    Returns the path of the written report.html.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)

    flagged = {}
    for issue in issues:
        for p in issue.pages:
            flagged.setdefault(p, []).append(issue.code)
    pages = sorted(flagged)[:max_pages]
    sample = [p for p in (1, 2) if p not in flagged and p <= doc.page_count]

    cells = []
    for pageno in sample + pages:
        png = out_dir / f'page{pageno:03d}.png'
        doc[pageno - 1].get_pixmap(dpi=dpi).save(png)
        label = ', '.join(flagged.get(pageno, ['sample']))
        cells.append(f'<figure><img src="{png.name}"><figcaption>'
                     f'p.{pageno} — {label}</figcaption></figure>')
    doc.close()

    items = ''.join(f'<li class="{i.severity}">{i}</li>' for i in issues) or \
        '<li class="ok">all checks passed</li>'
    (out_dir / 'report.html').write_text(f'''<!doctype html><meta charset="utf-8">
<title>{Path(pdf_path).name}</title>
<style>
 body {{ font: 14px -apple-system, sans-serif; margin: 1.5em; }}
 li.fail {{ color: #b00020; }} li.warn {{ color: #b26a00; }} li.ok {{ color: #1a7f37; }}
 .pages {{ display: flex; flex-wrap: wrap; gap: 12px; }}
 figure {{ margin: 0; }} img {{ border: 1px solid #999; width: 280px; }}
 figcaption {{ font-size: 12px; text-align: center; }}
</style>
<h2>{Path(pdf_path).name}</h2>
<ul>{items}</ul>
<div class="pages">{''.join(cells)}</div>
''')
    return out_dir / 'report.html'
