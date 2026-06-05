"""
piping_validator.py — Validate piping variables in survey questions.

Detects piping markers in doc question text and verifies they resolve
correctly in the live survey.  Works for any platform, any language —
never hardcodes variable names.

Piping patterns detected
------------------------
  Square brackets   [SPECIALTY], [BRAND], [Q1], [S1_CODE]
  Confirmit pipe    [pipe:specialty]
  Curly braces      {specialty}, {brand}
  Double curly      {{specialty}}
  Pipe chars        |SPECIALTY|
  Decipher          ${pipe.specialty}, ${resp.specialty}
  Qualtrics         ${q://QID1/ChoiceGroup/SelectedChoices}

Four checks
-----------
  CHECK1  PIPING_NOT_RESOLVED   — literal marker still visible in live  (HIGH)
  CHECK2  PIPING_BLANK          — gap / double-space where value should be (HIGH)
  CHECK3  PIPING_SOURCE_MISSING — source QID referenced by pipe not found (HIGH)
  CHECK4  PIPING_FORMAT_MISMATCH— format inconsistent with detected platform (MEDIUM)

Public API
----------
  extract_pipe_vars(text)                             -> list[dict]
  validate_piping(doc_questions, live_questions,
                  platform='generic', all_qids=None) -> list[dict]
"""

import re
from typing import Optional

# ── Per-platform pipe-format signatures ───────────────────────────────────────

# (platform_hint, compiled_regex) — ordered most-specific first
_PIPE_SPECS = [
    # Qualtrics  ${q://...}
    ('qualtrics', re.compile(r'\$\{q://[^}]+\}', re.IGNORECASE)),
    # Decipher   ${pipe.xxx} / ${resp.xxx}
    ('decipher',  re.compile(r'\$\{(?:pipe|resp)\.[^}]+\}', re.IGNORECASE)),
    # Generic    ${xxx}  (other Decipher / mustache variants)
    ('decipher',  re.compile(r'\$\{[^}]{1,60}\}', re.IGNORECASE)),
    # Double-curly  {{xxx}}
    ('generic',   re.compile(r'\{\{[^}]{1,60}\}\}', re.IGNORECASE)),
    # Confirmit  [pipe:xxx]
    ('confirmit', re.compile(r'\[pipe:[^\]]{1,60}\]', re.IGNORECASE)),
    # QID-based bracket  [Q1], [S1_CODE], [R2.A]
    ('generic',   re.compile(r'\[[RSQrsq]\d+[^\]]{0,30}\]')),
    # Uppercase-identifier bracket  [SPECIALTY], [BRAND_NAME]
    ('confirmit', re.compile(r'\[[A-Z][A-Z0-9_]{1,40}\]')),
    # Pipe-char delimited  |VARIABLE|
    ('generic',   re.compile(r'\|[A-Z][A-Z0-9_]{1,40}\|')),
    # Single curly  {variable}  (lower or mixed)
    ('generic',   re.compile(r'\{[a-zA-Z][a-zA-Z0-9_.]{0,40}\}', re.IGNORECASE)),
]

# Expected format hint per platform key
_PLATFORM_FORMAT: dict = {
    'confirmit': 'confirmit',
    'decipher':  'decipher',
    'qualtrics': 'qualtrics',
    'forsta':    'confirmit',   # Forsta (formerly Confirmit) uses same bracket style
}

# Source-QID extractors (Check 3)
_SRC_QID_BRACKET   = re.compile(r'^\[([RSQrsq]\d+)', re.IGNORECASE)
_SRC_QID_QUALTRICS = re.compile(r'\$\{q://([A-Z0-9_]+)/', re.IGNORECASE)

# Blank-gap patterns in live text (Check 2) — language-agnostic punctuation tells
_BLANK_GAP_RE = re.compile(
    r'(?:'
    r'\s{2,}'            # two+ consecutive spaces
    r'|,\s*,'            # double comma: "Hello ,,"
    r'|\s,[\s!?]'        # space-comma-space: "Hello , how"
    r'|\s\.\s'           # space-period-space (gap where word was)
    r'|(?:dear|hello|bonjour|ciao|hola|salut|guten\s+tag)\s*[,.]'  # greeting+missing name
    r')',
    re.IGNORECASE,
)


# ── Core extraction ────────────────────────────────────────────────────────────

def extract_pipe_vars(text: str) -> list:
    """
    Extract all piping variable occurrences from text.

    Returns list of dicts:
        {raw, platform_hint, var_name}
    where `raw` is the full matched token, `platform_hint` is one of
    confirmit/decipher/qualtrics/generic, and `var_name` is the clean
    identifier (stripped of delimiters).

    Deduplicates by raw token AND by character span so that shorter
    patterns don't double-report substrings of longer already-matched
    tokens (e.g. {pipe.x} must not re-fire inside ${pipe.x}).
    """
    # Collect (start, end, hint, raw) across all patterns
    candidates: list = []
    seen_raw: set = set()
    for hint, pat in _PIPE_SPECS:
        for m in pat.finditer(text):
            raw = m.group(0)
            if raw not in seen_raw:
                seen_raw.add(raw)
                candidates.append((m.start(), m.end(), hint, raw))

    # Remove any candidate whose span is fully contained within a longer accepted span
    accepted: list = []
    for start, end, hint, raw in candidates:
        contained = any(
            (as_ <= start and ae_ >= end and (as_ < start or ae_ > end))
            for as_, ae_, *_ in accepted
        )
        if not contained:
            # Also check against remaining candidates with larger spans
            shadowed = any(
                (os <= start and oe >= end and (os < start or oe > end))
                for os, oe, *_ in candidates
                if (os, oe) != (start, end)
            )
            if not shadowed:
                accepted.append((start, end, hint, raw))

    results = []
    for _, _, hint, raw in accepted:
        var_name = re.sub(r'^[\[\{\$\|]+|[\]\}\|]+$', '', raw).strip()
        var_name = re.sub(r'^(?:pipe:|resp\.|q://)(.+?)(?:/.*)?$', r'\1', var_name,
                          flags=re.IGNORECASE)
        results.append({'raw': raw, 'platform_hint': hint, 'var_name': var_name})
    return results


def _detect_doc_platform(doc_questions: dict) -> str:
    """
    Infer dominant piping platform from all doc question texts combined.
    Returns the most-used platform_hint, or 'generic'.
    """
    counts: dict = {}
    for q in doc_questions.values():
        for pv in extract_pipe_vars(q.get('text', '') + ' '.join(
                o.get('text', '') for o in q.get('options', []))):
            h = pv['platform_hint']
            counts[h] = counts.get(h, 0) + 1
    if not counts:
        return 'generic'
    return max(counts, key=counts.__getitem__)


def _make_issue(qid: str, pipe_var: dict, rule: str, severity: str,
                evidence: str, confidence: int) -> dict:
    check_map = {
        'CHECK1': 'PIPING_NOT_RESOLVED',
        'CHECK2': 'PIPING_BLANK',
        'CHECK3': 'PIPING_SOURCE_MISSING',
        'CHECK4': 'PIPING_FORMAT_MISMATCH',
    }
    check = check_map.get(rule, rule)
    return {
        'qid':          qid,
        'pipe_variable': pipe_var.get('raw', ''),
        'rule':         rule,
        'check':        check,
        'type':         check.replace('_', ' '),   # app.py compat
        'severity':     severity,
        'evidence':     evidence,
        'details':      evidence,                  # qc_engine compat
        'confidence':   confidence,
        'is_piping_issue': True,
    }


# ── Four checks ───────────────────────────────────────────────────────────────

def _check1_not_resolved(qid: str, pipe_vars: list, live_text: str) -> list:
    """CHECK1 — literal marker still present in live text."""
    issues = []
    for pv in pipe_vars:
        raw = pv['raw']
        if raw.lower() in live_text.lower():
            issues.append(_make_issue(
                qid, pv, 'CHECK1', 'HIGH',
                f'Found literal {raw!r} in live question text — piping not resolved',
                90,
            ))
    return issues


def _check2_blank(qid: str, pipe_vars: list, live_text: str) -> list:
    """CHECK2 — gap / blank where piped value should appear."""
    if not pipe_vars:
        return []
    m = _BLANK_GAP_RE.search(live_text)
    if m:
        return [_make_issue(
            qid, pipe_vars[0], 'CHECK2', 'HIGH',
            f'Possible blank piping gap near {m.group(0)!r} in live text — value may be empty',
            70,
        )]
    return []


def _check3_source_missing(qid: str, pipe_vars: list, all_qids: set) -> list:
    """CHECK3 — source QID referenced in pipe does not exist in survey."""
    if not all_qids:
        return []
    issues = []
    for pv in pipe_vars:
        raw = pv['raw']
        src_qid = None
        m = _SRC_QID_BRACKET.match(raw)
        if m:
            src_qid = m.group(1).upper()
        else:
            m2 = _SRC_QID_QUALTRICS.search(raw)
            if m2:
                src_qid = m2.group(1).upper()
        if src_qid and src_qid not in {q.upper() for q in all_qids}:
            issues.append(_make_issue(
                qid, pv, 'CHECK3', 'HIGH',
                f'Pipe source {src_qid!r} (from {raw!r}) not found in survey QID list',
                85,
            ))
    return issues


def _check4_format_mismatch(qid: str, pipe_vars: list,
                             expected_hint: str) -> list:
    """CHECK4 — piping format inconsistent with detected platform."""
    if expected_hint == 'generic':
        return []
    issues = []
    for pv in pipe_vars:
        hint = pv['platform_hint']
        if hint == 'generic':
            continue  # generic markers accepted on any platform
        if hint != expected_hint:
            issues.append(_make_issue(
                qid, pv, 'CHECK4', 'MEDIUM',
                f'Pipe format {pv["raw"]!r} looks like {hint!r} platform '
                f'but survey detected as {expected_hint!r}',
                65,
            ))
    return issues


# ── Public API ────────────────────────────────────────────────────────────────

def validate_piping(
    doc_questions: dict,
    live_questions: Optional[dict] = None,
    platform: str = 'generic',
    all_qids: Optional[set] = None,
) -> list:
    """
    Run all four piping checks across every question in doc_questions.

    Args:
        doc_questions:  {qid: {"text": str, "options": [...], ...}}
                        from parse_document() or app.py questions dict.
        live_questions: {qid: {"text": str, ...}}  — if None, only CHECK3/4 run.
        platform:       platform key from detect_platform() — used for CHECK4.
                        Use 'generic' to skip CHECK4.
        all_qids:       full set of QIDs in the survey (doc + live) for CHECK3.
                        If None, CHECK3 is skipped.

    Returns:
        List of issue dicts, one per piping problem found.
        Each dict contains: qid, pipe_variable, rule, check, type,
        severity, evidence, details, confidence, is_piping_issue.
    """
    issues: list = []

    # Determine which format to expect (CHECK4)
    plat_key  = platform.lower().replace(' ', '').replace('-', '')
    expected_hint = _PLATFORM_FORMAT.get(plat_key, 'generic')
    if expected_hint == 'generic':
        # Fall back to inferring from doc text if platform is not mapped
        inferred = _detect_doc_platform(doc_questions)
        if inferred != 'generic':
            expected_hint = inferred

    _all_qids_upper = {q.upper() for q in all_qids} if all_qids else set()

    for qid, doc_q in doc_questions.items():
        doc_text = doc_q.get('text', '')
        if not doc_text:
            continue

        pipe_vars = extract_pipe_vars(doc_text)
        if not pipe_vars:
            continue   # no piping in this question — nothing to check

        live_text = ''
        if live_questions:
            live_q = live_questions.get(qid, {})
            live_text = live_q.get('text', '')

        # CHECK1 + CHECK2 require live text
        if live_text:
            issues.extend(_check1_not_resolved(qid, pipe_vars, live_text))
            issues.extend(_check2_blank(qid, pipe_vars, live_text))

        # CHECK3 — source QID existence (doc-only is fine)
        if _all_qids_upper:
            issues.extend(_check3_source_missing(qid, pipe_vars, _all_qids_upper))

        # CHECK4 — format mismatch (doc-only is fine)
        issues.extend(_check4_format_mismatch(qid, pipe_vars, expected_hint))

    return issues
