"""Convert leftover Tiki wiki tables in page markdown into standard markdown tables.

Tiki tables look like ``||cell|cell\\nrow|cell||``: ``||`` opens and closes the table,
``|`` separates cells and line breaks separate rows. The Tiki -> markdown migration left
~130 of them unconverted, and it doubled the line breaks, so in stored markdown rows are
usually separated by blank lines and a single line break inside a row is a line break
within a cell (Tiki ``%%%``).

``convert_tiki_tables`` returns the new markdown plus a report entry per table so a
person can review the result; tables it cannot convert confidently are left unchanged
and reported with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_COLUMNS = 12

# A markdown table separator row: | --- | :---: | ...
_MD_SEPARATOR_RE = re.compile(r'^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*\|[ \t|:-]*$')
_HEADING_PREFIX_RE = re.compile(r'^(#{1,6})[ \t]*$')
# "Mostly a number": 1.71 X, 37%, 6 ng, 30 mg, -2 mm, 0.7-1.2, < 0.7 ...
_NUMERIC_CELL_RE = re.compile(
    r'^[\s<>~≈±+\-–*]*\d[\d.,\s\-–]*\s*(%|x|ng|mg|mcg|iu|mm|g|kg|nmol/l|ng/ml)?\.?\s*$',
    re.IGNORECASE,
)


@dataclass
class TableReport:
    status: str                      # "converted" or "skipped"
    reason: str = ''
    original: str = ''
    converted: str = ''
    rows: int = 0
    columns: int = 0
    header_from_first_row: bool = True
    notes: List[str] = field(default_factory=list)


def _markdown_table_line_spans(md: str) -> List[Tuple[int, int]]:
    """Character spans of existing markdown tables (header + separator + rows)."""
    spans = []
    lines = md.split('\n')
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and '|' in lines[i] and _MD_SEPARATOR_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and '|' in lines[j] and lines[j].strip():
                j += 1
            spans.append((offsets[i], offsets[j - 1] + len(lines[j - 1])))
            i = j
            continue
        i += 1
    return spans


def _find_tiki_tables(md: str) -> List[Tuple[int, int]]:
    """(start, end) of each ``||...||`` Tiki table, skipping markdown tables (whose empty
    cells also produce ``||``)."""
    md_spans = _markdown_table_line_spans(md)

    def in_markdown_table(pos: int) -> bool:
        return any(start <= pos <= end for start, end in md_spans)

    tables = []
    pos = 0
    while True:
        start = md.find('||', pos)
        if start < 0:
            break
        if in_markdown_table(start):
            pos = start + 2
            continue
        end = md.find('||', start + 2)
        while end >= 0 and in_markdown_table(end):
            end = md.find('||', end + 2)
        if end < 0:
            break
        inner = md[start + 2:end]
        if '\n' in inner and '|' in inner:
            tables.append((start, end + 2))
            pos = end + 2
        else:
            pos = start + 2
    return tables


def _split_rows(inner: str) -> List[str]:
    inner = inner.strip('\n')
    if re.search(r'\n[ \t]*\n', inner):
        # Doubled line breaks separate rows; a single line break is a break within a cell.
        rows = re.split(r'\n[ \t]*\n+', inner)
        return [re.sub(r'[ \t]*\n[ \t]*', '<br>', row.strip()) for row in rows if row.strip()]
    return [row.strip() for row in inner.split('\n') if row.strip()]


def _clean_cell(cell: str) -> str:
    cell = cell.replace('\t', ' ').strip()
    cell = re.sub(r'\s{2,}', ' ', cell)
    return cell


def _looks_like_header(first: List[str], body: List[List[str]]) -> bool:
    if not first[0]:
        return True  # empty top-left corner: classic header row
    others = [c for c in first[1:] if c]
    if not others:
        return True
    numeric = sum(bool(_NUMERIC_CELL_RE.match(re.sub(r'<[^>]+>|\*\*', '', c))) for c in others)
    return numeric < len(others) / 2


def _md_table(rows: List[List[str]], header: Optional[List[str]]) -> str:
    columns = max(len(r) for r in rows + ([header] if header else []))
    pad = lambda r: r + [''] * (columns - len(r))
    lines = []
    head = pad(header) if header else [''] * columns
    lines.append('| ' + ' | '.join(head) + ' |')
    lines.append('|' + '|'.join([' --- '] * columns) + '|')
    for row in rows:
        lines.append('| ' + ' | '.join(pad(row)) + ' |')
    return '\n'.join(lines)


def _convert_one(inner: str) -> Tuple[Optional[str], TableReport]:
    report = TableReport(status='skipped', original=inner)
    raw_rows = _split_rows(inner)
    if len(raw_rows) < 2:
        report.reason = 'fewer than 2 rows'
        return None, report
    rows = [[_clean_cell(c) for c in row.split('|')] for row in raw_rows]
    widths = [len(r) for r in rows]
    report.rows, report.columns = len(rows), max(widths)
    if max(widths) < 2:
        report.reason = 'only one column'
        return None, report
    if max(widths) > MAX_COLUMNS:
        report.reason = f'{max(widths)} columns (probably not a table)'
        return None, report
    if any(re.search(r'\[[^\]]*$', c) or re.search(r'^[^\[]*\]\(', c) for r in rows for c in r):
        report.reason = 'a link is split across cells'
        return None, report
    if max(widths) - min(widths) > 1:
        report.notes.append(f'uneven rows ({min(widths)}-{max(widths)} cells), padded')
    elif max(widths) != min(widths):
        report.notes.append('one row shorter, padded')
    if any(re.match(r'^\d+\.\s', r[0]) for r in rows):
        report.notes.append('a cell starts with "1." (may have been "#" in Tiki)')

    if _looks_like_header(rows[0], rows[1:]):
        header, body = rows[0], rows[1:]
    else:
        header, body = None, rows
        report.header_from_first_row = False
        report.notes.append('first row looks like data, so an empty header row was added')
    report.status = 'converted'
    report.converted = _md_table(body, header)
    return report.converted, report


def convert_tiki_tables(md: str) -> Tuple[str, List[TableReport]]:
    """Return (markdown with confident Tiki tables converted, one report per table found)."""
    normalized = md.replace('\r\n', '\n')
    spans = _find_tiki_tables(normalized)
    if not spans:
        return md, []

    reports = []
    out = []
    pos = 0
    for start, end in spans:
        inner = normalized[start + 2:end - 2]
        table, report = _convert_one(inner)
        reports.append(report)
        if table is None:
            continue

        before = normalized[pos:start]
        # Text on the same line before "||" (e.g. "**Table 1** ||Age|...") stays as its own paragraph.
        line_start = before.rfind('\n') + 1
        prefix = before[line_start:]
        before = before[:line_start]
        heading = _HEADING_PREFIX_RE.match(prefix.strip()) if prefix.strip() else None
        if heading:
            report.notes.append('heading marker before the table was removed')
            prefix = ''
        out.append(before)
        if prefix.strip():
            out.append(prefix.rstrip() + '\n\n')
        elif before and not before.endswith('\n\n'):
            out.append('\n')
        out.append(table)

        # Text on the same line after the closing "||" becomes the next paragraph.
        rest_line_end = normalized.find('\n', end)
        rest_line_end = len(normalized) if rest_line_end < 0 else rest_line_end
        suffix = normalized[end:rest_line_end]
        if suffix.strip():
            out.append('\n\n' + suffix.strip())
        out.append('\n')
        if not normalized[rest_line_end:rest_line_end + 2].startswith('\n\n'):
            out.append('\n')
        pos = rest_line_end + 1 if rest_line_end < len(normalized) else rest_line_end

    out.append(normalized[pos:])
    converted = ''.join(out)
    if '\r\n' in md:
        converted = converted.replace('\n', '\r\n')
    return converted, reports


_WRAPPER_TAG_RE = re.compile(r'<(/?)(div|span|center|blockquote)\b([^>]*)>', re.IGNORECASE)


def _isolate_markdown_tables(md: str) -> str:
    """Make sure every markdown table has a blank line before and after it."""
    lines = md.split('\n')
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and '|' in lines[i] and _MD_SEPARATOR_RE.match(lines[i + 1]):
            if out and out[-1].strip():
                out.append('')
            j = i
            while j < len(lines) and '|' in lines[j] and lines[j].strip():
                out.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip():
                out.append('')
            i = j
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _render_tables_inside_html_blocks(md: str) -> str:
    """Replace markdown tables that sit inside HTML boxes (<div>, <span>, ...) with HTML tables.

    markdown2 leaves everything inside an HTML block unprocessed, so a table inside
    <div class="border">...</div> would otherwise show as raw text. Only the table is
    converted; the rest of the box is left exactly as it was.
    """
    import markdown2

    lines = md.split('\n')
    out: List[str] = []
    depth = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if depth > 0 and i + 1 < len(lines) and '|' in line and _MD_SEPARATOR_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and '|' in lines[j] and lines[j].strip():
                j += 1
            table_md = '\n'.join(lines[i:j])
            out.append(markdown2.markdown(table_md, extras=['tables']).strip())
            i = j
            continue
        for tag in _WRAPPER_TAG_RE.finditer(line):
            if tag.group(0).endswith('/>'):
                continue
            depth = max(0, depth - 1) if tag.group(1) else depth + 1
        out.append(line)
        i += 1
    return '\n'.join(out)


def prepare_tables_for_rendering(md: str) -> str:
    """Pre-process markdown so every table renders: convert leftover Tiki tables, give
    tables blank lines around them, and turn tables inside HTML boxes into HTML tables."""
    md, _ = convert_tiki_tables(md)
    md = md.replace('\r\n', '\n')
    md = _isolate_markdown_tables(md)
    return _render_tables_inside_html_blocks(md)
