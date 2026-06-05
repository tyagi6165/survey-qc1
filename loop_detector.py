"""
loop_detector.py — Detect loop/repeat blocks and parent-child QID hierarchies.

Siblings in a loop block are marked LOOP_SIBLING so QC checks run only once
per group (on the first sibling) and never flag the rest as duplicates.

Supported patterns (generic — any prefix letter, any language, any platform):
  1. Numeric x-suffix   Q30x1, Q30x2, Q30x3  → parent=Q30,  index=1,2,3
  2. Alpha suffix        D3A,  D3B,  D3C      → parent=D3,   index=A,B,C
  3. Underscore suffix   Q30_1, Q30_2         → parent=Q30,  index=1,2
  4. bis/ter suffix      Q4bis, Q4ter         → parent=Q4,   variant=bis/ter
  5. new suffix          D4new, xD5new        → parent=D4/D5 variant=new

Public API
----------
  get_parent_qid(qid)                -> str | None
  get_loop_index(qid)                -> str | None
  get_loop_siblings(qid, all_qids)   -> list[str]   (excludes qid itself)
  is_loop_member(qid, all_qids)      -> bool
  build_loop_map(all_qids)           -> dict[str, list[str]]
  get_loop_skip_set(all_qids)        -> dict[str, str]  {skip_qid: first_sibling}
"""

import re
from typing import Optional

# ── Patterns (most specific first to avoid mis-matches) ───────────────────────

# Pattern 4: bis/ter — must come before alpha so the trailing 's'/'r' of bis/ter
# isn't mistaken for a single-letter alpha suffix.
_RE_BISTER = re.compile(r'^(.*\d)(bis|ter)$', re.IGNORECASE)

# Pattern 5: new suffix — optional leading 'x' that some platforms prepend.
# xD5new → parent=D5, D4new → parent=D4.
_RE_NEW = re.compile(r'^x?([A-Za-z]+\d+(?:\.\d+)?)(new)$', re.IGNORECASE)

# Pattern 1: numeric x-suffix: Q30x1, R5x2, S1x10.
_RE_XNUM = re.compile(r'^([A-Za-z]+\d+(?:\.\d+)?)x(\d+)$', re.IGNORECASE)

# Pattern 3: underscore-numeric suffix: Q30_1, D3_2.
_RE_UNDER = re.compile(r'^([A-Za-z]+\d+(?:\.\d+)?)_(\d+)$')

# Pattern 2: single uppercase alpha suffix directly after the last digit.
# Require uppercase only to reduce false positives on camelCase QID names.
# 'x' is excluded from the suffix character class — it belongs to pattern 1.
_RE_ALPHA = re.compile(r'^([A-Za-z]+\d+(?:\.\d+)?)([A-WYZ])$')

# Ordered list: first match wins.
_PATTERNS = [
    ('bister', _RE_BISTER),
    ('new',    _RE_NEW),
    ('xnum',   _RE_XNUM),
    ('under',  _RE_UNDER),
    ('alpha',  _RE_ALPHA),
]


# ── Core parse ────────────────────────────────────────────────────────────────

def _parse_qid(qid: str):
    """
    Try every pattern against qid in priority order.
    Returns (parent, index_or_variant, pattern_name) or (None, None, None).
    """
    for name, pat in _PATTERNS:
        m = pat.match(qid)
        if m:
            return m.group(1), m.group(2), name
    return None, None, None


# ── Public API ────────────────────────────────────────────────────────────────

def get_parent_qid(qid: str) -> Optional[str]:
    """Return the loop-parent stem, or None if qid is not a recognised loop child."""
    parent, _, _ = _parse_qid(qid)
    return parent


def get_loop_index(qid: str) -> Optional[str]:
    """Return the loop index / variant string (e.g. '1', 'A', 'bis'), or None."""
    _, idx, _ = _parse_qid(qid)
    return idx


def _sort_key(qid: str):
    """Numeric indices sort as ints; alpha/text sorts as uppercase strings."""
    idx = get_loop_index(qid) or ''
    try:
        return (0, int(idx), '')
    except ValueError:
        return (1, 0, idx.upper())


def build_loop_map(all_qids) -> dict:
    """
    Build {parent_qid: [child1, child2, ...]} for every loop group
    that contains at least 2 children.

    Siblings within each group are sorted: numeric indices ascending,
    then alpha/text ascending.

    Args:
        all_qids: iterable of QID strings (original, not normalised).

    Returns:
        dict — empty if no loops found.
    """
    groups: dict = {}
    for qid in all_qids:
        parent, _, _ = _parse_qid(qid)
        if parent is not None:
            groups.setdefault(parent, []).append(qid)

    return {
        parent: sorted(children, key=_sort_key)
        for parent, children in groups.items()
        if len(children) >= 2
    }


def get_loop_siblings(qid: str, all_qids) -> list:
    """
    Return all loop siblings of qid (same parent, same pattern) excluding
    qid itself.  Returns [] if qid is not part of any loop group.
    """
    parent, _, _ = _parse_qid(qid)
    if parent is None:
        return []
    loop_map = build_loop_map(all_qids)
    return [s for s in loop_map.get(parent, []) if s != qid]


def is_loop_member(qid: str, all_qids) -> bool:
    """True if qid belongs to a loop group (≥ 2 siblings share the same parent)."""
    parent, _, _ = _parse_qid(qid)
    if parent is None:
        return False
    return parent in build_loop_map(all_qids)


def get_loop_skip_set(all_qids) -> dict:
    """
    Return {skip_qid: first_sibling_qid} for every loop sibling that
    should be skipped — i.e. every sibling except the first in each group.

    The first sibling is always compared; the rest are suppressed so the
    QC engine never raises duplicate issues for loop iterations.

    Args:
        all_qids: iterable of QID strings.

    Returns:
        dict — empty if no loops detected.
    """
    skip: dict = {}
    for parent, children in build_loop_map(all_qids).items():
        first = children[0]
        for child in children[1:]:
            skip[child] = first
    return skip
