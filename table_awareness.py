"""
table_awareness.py — Survey table type classifier v1.0

Distinguishes programming/routing/quota tables from actual survey question
tables so that doc parsers can skip non-question content.

Works for any language (EN/FR/DE/IT/ES/NL) and any doc format (.docx, HTML,
plain text serialised as rows).

Public API:
    classify_table(rows)    -> str   one of TABLE_TYPES
    is_question_table(rows) -> bool
    TABLE_TYPES             frozenset of valid type-string constants
"""

import re
from typing import Sequence

# ── Public type constants ─────────────────────────────────────────────────────

TABLE_TYPES = frozenset({
    'ROUTING_TABLE',
    'PROGRAMMING_TABLE',
    'QUOTA_TABLE',
    'SURVEY_QUESTION',
    'UNKNOWN',
})

# ── Compiled patterns ─────────────────────────────────────────────────────────

# Routing: IF / CONDITION / THEN / GOTO / SKIP TO — any of these as a header cell
_ROUTING_HDR = re.compile(
    r'^\s*(?:IF|CONDITION|THEN|GOTO|GO\s+TO|SKIP\s+TO|TARGET\s+Q|ROUTE\s+TO'
    r'|SI|SINON|DANN|GEHE\s+ZU|ANDERS)\s*$',
    re.IGNORECASE,
)

# Routing: arrow syntax in body cells (→, ->, >>, ⇒)
_ROUTING_ARROW = re.compile(r'[→➔⇒]|->|>>|\bGO\s*TO\b|\bGOTO\b|\bSKIP\s+TO\b|\bROUTE\b', re.IGNORECASE)

# Routing: "IF <qid> <op> <val>" patterns anywhere in the table
_ROUTING_CONDITION = re.compile(
    r'\bIF\s+[A-Za-z]\w*\s*(?:=|!=|≠|<|>|≤|≥|<=|>=)',
    re.IGNORECASE,
)

# Programming table: explicit header keyword
_PROG_HEADER_KW = re.compile(
    r'PROG(?:RAMM?ING)?\s*TABLE|PROGRAMMING\s*NOTES?|PROG\s*NOTES?|SPEC(?:IFICATION)?S?\s*TABLE',
    re.IGNORECASE,
)

# Programming: Variable / Type / Label / Code as header columns
_PROG_HDR_CELL = re.compile(
    r'^\s*(?:VARIABLE|VAR(?:IABLE)?|TYPE|LABEL|CODE[SD]?|CODED'
    r'|MANDATORY|REQUIRED|RANGE|MIN|MAX|RANDOMIS|RANDOMIZ|DISPLAY'
    r'|SCREEN|ROUTING|ROUTINE|FORMAT|WIDTH|DECIMAL|OPEN\s+ENDED'
    r'|SINGLE|MULTIPLE|NUMERIC|TEXT|FILTER|LOOP'
    r'|VARIABLE\s*NAME|QUESTION\s*TYPE)\s*$',
    re.IGNORECASE,
)

# Programming: body-row keywords (first cell of a row is exactly this keyword)
_PROG_BODY_KW = re.compile(
    r'^\s*(?:TYPE|MANDATORY|RANGE|RANDOMIS|RANDOMIZ|CODED|OPEN\s+ENDED'
    r'|FILTER|LOOP|MIN\s*=|MAX\s*=|ALL\s+RESP(?:ONDENTS)?'
    r'|DISPLAY\s+LOGIC|SKIP\s+LOGIC|SCREEN\s+OUT)\s*$',
    re.IGNORECASE,
)

# Quota table: header keywords
_QUOTA_HDR = re.compile(
    r'^\s*(?:QUOTA|TARGET|OBJECTIVE|OBJECTIF|CIBLE|ZIEL|N\s*='
    r'|CELL|SEGMENT|STRATA|STRATUM|QUOTA\s*SIZE|NEEDED|REQUIRED\s+N)\s*$',
    re.IGNORECASE,
)

# Quota: body signals (N=<number>, quota size references)
_QUOTA_BODY = re.compile(
    r'N\s*=\s*\d+|\bQUOTA\b|\bTARGET\b|\bOBJECTIF\b|\bCIBLE\b',
    re.IGNORECASE,
)

# Survey question: QID header line (R/S/Q prefix)
_QID_LINE = re.compile(
    r'^\s*\[?\s*([RSQ]\d+(?:bis|ter|info|Info|Ex)?)\s*[\.\-–—\s\]]',
    re.IGNORECASE,
)

# Survey question: numeric option codes (01. / 1- / 2) / 99 ) etc.)
_OPTION_CODE = re.compile(r'^\s*\d{1,3}\s*[.\-–)]\s*\S')

# Survey question: question mark anywhere in the text
_QUESTION_MARK = re.compile(r'\?')


# ── Internal helpers ──────────────────────────────────────────────────────────

def _flat(rows: Sequence[Sequence[str]]) -> str:
    """Join all cell text into a single string for whole-table scanning."""
    return '\n'.join(
        ' | '.join(c.strip() for c in row if c.strip())
        for row in rows
        if any(c.strip() for c in row)
    )


def _header_cells(rows: Sequence[Sequence[str]], max_rows: int = 3) -> list:
    """Return non-empty cell strings from the first max_rows rows."""
    out = []
    for row in rows[:max_rows]:
        for cell in row:
            t = cell.strip()
            if t:
                out.append(t)
    return out


def _count_matches(pattern: re.Pattern, cells: list) -> int:
    return sum(1 for c in cells if pattern.search(c))


# ── Public API ────────────────────────────────────────────────────────────────

def classify_table(rows: Sequence[Sequence[str]]) -> str:
    """
    Classify a table into one of TABLE_TYPES.

    Args:
        rows: list of rows; each row is a list of cell strings (stripped text).

    Returns:
        'ROUTING_TABLE'     — IF/CONDITION/THEN/GOTO column layout, or
                              body contains arrow/goto/condition patterns.
        'PROGRAMMING_TABLE' — PROG TABLE header or Variable/Type/Label/Code
                              column layout; holds spec metadata, not Q&A.
        'QUOTA_TABLE'       — Quota/Target/N= structure.
        'SURVEY_QUESTION'   — Contains a QID header and/or coded answer options.
        'UNKNOWN'           — Cannot determine; treat as question table (safe default).
    """
    if not rows:
        return 'UNKNOWN'

    full_text = _flat(rows)
    headers = _header_cells(rows)

    # ── 1. ROUTING TABLE ─────────────────────────────────────────────────────
    # Two or more header cells are routing keywords
    routing_hdr_hits = _count_matches(_ROUTING_HDR, headers)
    if routing_hdr_hits >= 2:
        return 'ROUTING_TABLE'

    # Arrows or GOTO appear at least twice in the full table text
    arrow_hits = len(_ROUTING_ARROW.findall(full_text))
    if arrow_hits >= 2:
        return 'ROUTING_TABLE'

    # "IF Q=value" conditional pattern appears at least twice
    cond_hits = len(_ROUTING_CONDITION.findall(full_text))
    if cond_hits >= 2:
        return 'ROUTING_TABLE'

    # Single GOTO/arrow in a table that also has "IF" or "CONDITION" in a header
    if arrow_hits >= 1 and routing_hdr_hits >= 1:
        return 'ROUTING_TABLE'

    # ── 2. PROGRAMMING TABLE ─────────────────────────────────────────────────
    # Explicit PROG TABLE / PROGRAMMING NOTES header anywhere in the table
    if _PROG_HEADER_KW.search(full_text):
        return 'PROGRAMMING_TABLE'

    # Two or more header cells are programming spec keywords
    prog_hdr_hits = _count_matches(_PROG_HDR_CELL, headers)
    if prog_hdr_hits >= 2:
        return 'PROGRAMMING_TABLE'

    # Two or more body rows start with a programming keyword in their first cell
    prog_body_hits = sum(
        1 for row in rows[1:]
        if row and _PROG_BODY_KW.match(row[0].strip())
    )
    if prog_body_hits >= 2:
        return 'PROGRAMMING_TABLE'

    # One programming header keyword + one body keyword = strong signal
    if prog_hdr_hits >= 1 and prog_body_hits >= 1:
        return 'PROGRAMMING_TABLE'

    # ── 3. QUOTA TABLE ───────────────────────────────────────────────────────
    quota_hdr_hits = _count_matches(_QUOTA_HDR, headers)
    if quota_hdr_hits >= 1 and _QUOTA_BODY.search(full_text):
        return 'QUOTA_TABLE'

    # Header is "QUOTA" or "TARGET" alone (single-column quota list)
    if quota_hdr_hits >= 2:
        return 'QUOTA_TABLE'

    # ── 4. SURVEY QUESTION ───────────────────────────────────────────────────
    has_qid = bool(_QID_LINE.search(full_text))
    option_count = sum(
        1 for row in rows
        for cell in row
        if _OPTION_CODE.match(cell.strip())
    )
    has_question_mark = bool(_QUESTION_MARK.search(full_text))

    # QID present + at least 2 coded options → unambiguous survey content
    if has_qid and option_count >= 2:
        return 'SURVEY_QUESTION'

    # QID + question mark (no numeric codes needed — open-ended or grid)
    if has_qid and has_question_mark:
        return 'SURVEY_QUESTION'

    # Enough coded options with no routing/prog signals already ruled out
    if option_count >= 3:
        return 'SURVEY_QUESTION'

    # QID alone (single-cell table or numeric-entry question)
    if has_qid:
        return 'SURVEY_QUESTION'

    # ── 5. UNKNOWN (safe default — keep, don't skip) ──────────────────────────
    return 'UNKNOWN'


def is_question_table(rows: Sequence[Sequence[str]]) -> bool:
    """
    Return True if this table should be included in survey parsing.
    Returns False for ROUTING_TABLE, PROGRAMMING_TABLE, QUOTA_TABLE.
    UNKNOWN is kept (safe default).
    """
    return classify_table(rows) in ('SURVEY_QUESTION', 'UNKNOWN')
