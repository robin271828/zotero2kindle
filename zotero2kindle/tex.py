"""Rewrites arXiv LaTeX source into a single-column, small-page layout.

The static LaTeX side of the conversion (image scaling and rotation, size
defaults) lives in arxiv2kindle.sty, which is copied next to the paper
source; this module handles the per-document rewriting.
"""
import re
from pathlib import Path

STY_FILE = Path(__file__).parent / 'arxiv2kindle.sty'

PT_PER = {'pt': 1.0, 'mm': 2.845, 'cm': 28.45, 'in': 72.27, 'em': 10.0, 'ex': 4.3}
P_COL_RE = re.compile(r'([pmb])\{\s*([\d.]+)\s*(pt|mm|cm|in|em|ex)\s*\}')


def _shrink_fixed_columns(colspec, max_pt=245.0):
    """Scale absolute p{...}/m{...}/b{...} column widths down when they sum
    to more than the Kindle text width; fonts can't fix fixed widths."""
    total = sum(float(n) * PT_PER[u] for _, n, u in P_COL_RE.findall(colspec))
    if total <= max_pt:
        return colspec
    factor = max_pt / total
    return P_COL_RE.sub(
        lambda m: f'{m.group(1)}{{{float(m.group(2)) * PT_PER[m.group(3)] * factor:.1f}pt}}',
        colspec)


class KindleTexTransformer:

    def __init__(self, geometric_settings, is_landscape=False, font_size=10,
                 extra_preamble=()):
        self.geometric_settings = geometric_settings
        self.is_landscape = is_landscape
        self.font_size = font_size
        self.extra_preamble = list(extra_preamble)

    def transform(self, src):
        """Take the paper's source as a list of lines, return the rewritten lines."""
        # filter comments/newlines for easier debugging:
        src = [line for line in src if line[0] != '%' and len(line.strip()) > 0]
        src[0] = self._clean_documentclass(src[0])
        begindocs = [i for i, line in enumerate(src) if line.startswith(r'\begin{document}')]
        assert len(begindocs) == 1
        # force a single column, since classes like IEEEtran are two-column
        # by default without any documentclass option to strip; \sloppy
        # keeps justified text from overflowing the narrow page
        src[begindocs[0]] = src[begindocs[0]].replace(
            r'\begin{document}',
            '\\begin{document}\n'
            # the preamble handed the untouched class layout back so
            # \AtBeginDocument style-police checks pass; now that every
            # begin-document hook has run (including classes re-applying
            # their own geometry), put the Kindle layout back in force;
            # \onecolumn then rebuilds the galley from it
            '\\kindlerestorelayout{kindle}\n'
            # classes like acmart re-set their own page style at begin
            # document; their headers/footers are laid out for the original
            # paper size and land off-page here
            '\\pagestyle{empty}\\thispagestyle{empty}\n'
            '\\onecolumn\n\\sloppy\\emergencystretch=3em\n', 1)
        src[begindocs[0]:begindocs[0]] = self._preamble_lines()
        transformed = []
        author_depth = 0  # brace depth while inside a multi-line \author{...}
        for line in src:
            if author_depth > 0:
                author_depth += line.count('{') - line.count('}')
                line = self._transform_author_line(line)
            elif r'\author' in line:
                after = line.split(r'\author', 1)[1]
                author_depth = after.count('{') - after.count('}')
                line = self._transform_author_line(line)
            transformed.append(self._transform_line(line))
        return transformed

    def transform_body(self, src):
        """Rewrite a secondary source file (pulled in via \\input/\\include).

        Only the line-level rewrites apply; the preamble injection happens in
        the main file.
        """
        return [self._transform_line(line) for line in src
                if line[0] != '%' and len(line.strip()) > 0]

    def _clean_documentclass(self, line):
        # strip font size, column count, and paper size from the class options:
        line = re.sub(r'\b\d+pt\b', '', line)
        line = re.sub(r'\b\w+column\b', '', line)
        line = re.sub(r'\b\w+paper\b', '', line)
        line = re.sub(r'(?<=\[),', '', line)  # remove extraneous starting commas
        line = re.sub(r',(?=[\],])', '', line)  # remove extraneous middle/ending commas
        if '{acmart}' in line:
            # ACM footers/sidebars are letter-size page furniture that lands
            # off-page here; nonacm drops them
            if '[' in line:
                line = line.replace('[', '[nonacm,', 1)
            else:
                line = line.replace(r'\documentclass', r'\documentclass[nonacm]', 1)
        return line

    def _preamble_lines(self):
        geometry = ','.join(f'{k}={v}' for k, v in self.geometric_settings.items())
        lines = [
            '\\usepackage{arxiv2kindle}\n',
            '\\kindlesnapshotlayout{class}\n',
            '\\pagestyle{empty}\n',
            '\\usepackage{times}\n',
            # one base font size regardless of what the class sets
            f'\\usepackage[fontsize={self.font_size}pt]{{fontsize}}\n',
            # classes like neurips load geometry themselves; a plain
            # \usepackage with options would hit an option clash and get
            # ignored, so load it bare and override with reset
            '\\makeatletter\\@ifpackageloaded{geometry}{}{\\RequirePackage{geometry}}\\makeatother\n',
            f'\\geometry{{reset,{geometry}}}\n',
        ]
        # remediation lines go before the kindle snapshot so a \geometry
        # override in them is captured too
        lines += self.extra_preamble
        lines += [
            '\\kindlesnapshotlayout{kindle}\n',
            # hand the untouched class layout back so \AtBeginDocument
            # layout-police checks (ICML, AISTATS, ...) pass; the Kindle
            # layout is re-applied right after \begin{document}
            '\\kindlerestorelayout{class}\n',
            # conference classes (ICML, ...) typeset their title block via
            # \twocolumn[...], which would silently re-enable two-column
            # layout after our \onecolumn; make it typeset only its argument
            '\\renewcommand\\twocolumn[1][]{#1}\n',
            # \maketitle installs a first-page style whose headers/footers
            # are laid out for the original paper size and land off-page
            '\\apptocmd{\\maketitle}{\\thispagestyle{empty}}{}{}\n',
            # acmart prints folios (page numbers) even with nonacm
            '\\makeatletter\\@ifclassloaded{acmart}'
            '{\\settopmatter{printfolios=false}}{}\\makeatother\n',
        ]
        if self.is_landscape:
            lines.append('\\usepackage{pdflscape}\n')
        return lines

    def _transform_author_line(self, line):
        # \\ is illegal inside box commands like \centerline; leave such
        # author lines alone
        if re.search(r'\\(centerline|hbox|mbox|makebox)\b', line):
            return line
        # authors are often glued together with ties (~~) that can never
        # wrap on a narrow page; title blocks are usually tabulars, so
        # turn the separators into row breaks
        line = re.sub(r'\s*~~+\s*', r'\\\\ ', line)
        # comma-separated author lists after superscripts get one
        # title-block row per author
        return re.sub(r'(\$[^$]*\$)\s*,\s*', r'\1,\\\\ ', line)

    def _transform_line(self, line):
        # column-spanning floats become regular floats in a single column
        line = line.replace('{figure*}', '{figure}').replace('{table*}', '{table}')
        # wrapping text around a float is hopeless on a 3.6in wide page: the
        # float overprints the text; make wrap floats regular floats
        line = re.sub(r'\\begin\{wrap(figure|table)\}(\[[^\]]*\])?(\{[^{}]*\}){1,2}',
                      r'\\begin{\1}', line)
        line = re.sub(r'\\end\{wrap(figure|table)\}', r'\\end{\1}', line)
        # URLs shown via \texttt can never break; \nolinkurl renders the
        # same but breaks at punctuation (UrlBreaks in arxiv2kindle.sty)
        line = re.sub(r'(\\href\{[^{}]*\})\{\\texttt\{([^{}]*)\}\}',
                      r'\1{\\nolinkurl{\2}}', line)
        # tabular* stretches inter-column space to a target width but
        # cannot shrink content below it; as plain tabular the table takes
        # its natural width and the auto-fit machinery can scale it
        line = re.sub(r'\\begin\{tabular\*\}\s*\{[^{}]*\}', r'\\begin{tabular}', line)
        line = line.replace(r'\end{tabular*}', r'\end{tabular}')
        # tables with fixed-width columns wider than the page
        line = re.sub(r'(\\begin\{(?:tabular|longtable|supertabular)\}(?:\[[^\]]*\])?)'
                      r'\{((?:[^{}]|\{[^{}]*\})*)\}',
                      lambda m: m.group(1) + '{' + _shrink_fixed_columns(m.group(2)) + '}',
                      line)
        # \put overlays with negative offsets annotate a figure at its
        # original letter-paper coordinates; on the scaled-down image they
        # land in the wrong place or off the page
        if re.match(r'\s*\\put\(\s*-', line):
            return '%\n'  # a blank line would insert a paragraph break
        # drop negative vertical space used to compress the original
        # two-column layout; it makes text run into floats here
        line = re.sub(r'\\vspace\*?\{\s*-[^{}]*\}', '', line)
        # rescale images sized relative to the original column/page so they
        # keep their aspect ratio and never exceed the page; \linewidth (not
        # \textwidth) so images inside minipages/subfigures stay inside them
        m = re.search(r'\\includegraphics\[width=([.\d]*)\\(line|text|column)width\s*\]', line)
        if m:
            mul = m.group(1) or '1'
            line = re.sub(
                r'\\includegraphics\[width=([.\d]*)\\(line|text|column)width\s*\]',
                r'\\includegraphics[width={mul}\\linewidth,height={mul}\\textheight,keepaspectratio]'.format(mul=mul),
                line
            )
        return line
