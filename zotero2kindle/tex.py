"""Rewrites arXiv LaTeX source into a single-column, small-page layout.

The static LaTeX side of the conversion (image scaling and rotation, size
defaults) lives in arxiv2kindle.sty, which is copied next to the paper
source; this module handles the per-document rewriting.
"""
import re
from pathlib import Path

STY_FILE = Path(__file__).parent / 'arxiv2kindle.sty'


class KindleTexTransformer:

    def __init__(self, geometric_settings, is_landscape=False, font_size=10):
        self.geometric_settings = geometric_settings
        self.is_landscape = is_landscape
        self.font_size = font_size

    def transform(self, src):
        """Take the paper's source as a list of lines, return the rewritten lines."""
        # filter comments/newlines for easier debugging:
        src = [line for line in src if line[0] != '%' and len(line.strip()) > 0]
        src[0] = self._clean_documentclass(src[0])
        begindocs = [i for i, line in enumerate(src) if line.startswith(r'\begin{document}')]
        assert len(begindocs) == 1
        # \newgeometry re-wins against classes (e.g. neurips) that re-apply
        # their own geometry in \AtBeginDocument, which runs after the
        # preamble; force a single column, since classes like IEEEtran are
        # two-column by default without any documentclass option to strip;
        # \sloppy keeps justified text from overflowing the narrow page
        margin = self.geometric_settings.get('margin', '0.2in')
        src[begindocs[0]] = src[begindocs[0]].replace(
            r'\begin{document}',
            '\\begin{document}\n'
            f'\\newgeometry{{margin={margin}}}\n'
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

    def _clean_documentclass(self, line):
        # strip font size, column count, and paper size from the class options:
        line = re.sub(r'\b\d+pt\b', '', line)
        line = re.sub(r'\b\w+column\b', '', line)
        line = re.sub(r'\b\w+paper\b', '', line)
        line = re.sub(r'(?<=\[),', '', line)  # remove extraneous starting commas
        line = re.sub(r',(?=[\],])', '', line)  # remove extraneous middle/ending commas
        return line

    def _preamble_lines(self):
        geometry = ','.join(f'{k}={v}' for k, v in self.geometric_settings.items())
        lines = [
            '\\usepackage{arxiv2kindle}\n',
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
        if self.is_landscape:
            lines.append('\\usepackage{pdflscape}\n')
        return lines

    def _transform_author_line(self, line):
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
        # drop negative vertical space used to compress the original
        # two-column layout; it makes text run into floats here
        line = re.sub(r'\\vspace\*?\{\s*-[^{}]*\}', '', line)
        # rescale images sized relative to the original column/page so they
        # keep their aspect ratio and never exceed the page
        m = re.search(r'\\includegraphics\[width=([.\d]*)\\(line|text|column)width\s*\]', line)
        if m:
            mul = m.group(1) or '1'
            line = re.sub(
                r'\\includegraphics\[width=([.\d]*)\\(line|text|column)width\s*\]',
                r'\\includegraphics[width={mul}\\textwidth,height={mul}\\textheight,keepaspectratio]'.format(mul=mul),
                line
            )
        return line
