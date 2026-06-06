"""
range_validator.py — Detect numeric range constraints in survey specs and
validate they are correctly enforced.

Multilingual: English, French, German, Italian, Spanish.
Generic: any survey, any platform, no hardcoded variable names.

Range patterns detected
-----------------------
  Explicit MIN/MAX   MIN: 0 / MAX: 120 · min=1 max=1000 · Minimum: 18 Maximum: 99
  Dash / hyphen      Age 18-99 · Alter 18-120 · 2000-2024
  "X to Y"           1 to 500 patients · 18 to 99
  "between X and Y"  between 0 and 100 · entre 18 et 99 · zwischen 18 und 99
  "X à/a Y"          de 18 à 99 · de 1 à 500 · da 18 a 99
  Percentage         0-100% · 0 à 100%

Rules produced
--------------
  R041  RANGE_MIN_NOT_ENFORCED   — test: enter MIN-1 → must be blocked         (HIGH)
  R042  RANGE_MAX_NOT_ENFORCED   — test: enter MAX+1 → must be blocked         (HIGH)
  R043  RANGE_MISSING_VALIDATION — range in spec, numeric type, no prog. row   (HIGH)
  R044  RANGE_MISMATCH           — conflicting / ambiguous ranges in same Q    (MEDIUM)
  R045  RANGE_MANDATORY_BLANK    — mandatory numeric must reject blank entry    (HIGH)

Public API
----------
  extract_range(text)                  -> tuple[float, float] | None
  detect_doc_ranges(doc_questions)     -> dict[str, RangeInfo]
  validate_ranges(doc_questions,
                  live_questions=None) -> list[dict]
  generate_range_test_cases(doc_questions,
                             id_prefix="TC_RNG")  -> list[dict]
"""

from __future__ import annotations
import re
from typing import Optional

# ── Type indicator words (multilingual) ──────────────────────────────────────
# Used to confirm a question is numeric/range-bearing from text context alone.
_NUMERIC_CONTEXT_RE = re.compile(
    r'\b('
    # English
    r'age|year|years|patients?|units?|months?|days?|hours?|weeks?|'
    r'percent|percentage|number|quantity|amount|score|rate|count|'
    # French
    r'âge|ann[ée]e?|mois|jours?|patients?|pourcent|nombre|quantit[ée]|'
    r'montant|unit[ée]s?|score|taux|'
    # German
    r'alter|jahre?|monate?|wochen|tage?|patienten|prozent|anzahl|menge|'
    r'punkte?|bewertung|betrag|'
    # Italian
    r'et[àa]|anni|mesi|giorni|pazienti|percento|numero|quantit[àa]|'
    r'punteggio|'
    # Spanish
    r'edad|a[ñn]os?|meses|d[ií]as?|pacientes|porcentaje|n[uú]mero|'
    r'cantidad|puntaje'
    r')\b',
    re.IGNORECASE,
)

# ── Range extraction patterns (ordered most-specific first) ──────────────────

# Pattern 1: Explicit MIN/MAX keywords (all languages use MIN/MAX or Minimum/Maximum)
_P_MINMAX = re.compile(
    r'(?:min(?:imum)?|min\.?)\s*[=:\s]\s*(-?[\d.,]+)\s*'
    r'(?:[/,;]?\s*(?:max(?:imum)?|max\.?)\s*[=:\s]\s*(-?[\d.,]+))',
    re.IGNORECASE,
)

# Pattern 2: "Minimum X Maximum Y" as separate words (no separator)
_P_MINMAX_WORDS = re.compile(
    r'min(?:imum)?\s+(-?[\d.,]+)\s+max(?:imum)?\s+(-?[\d.,]+)',
    re.IGNORECASE,
)

# Pattern 3: "between X and Y" — EN/FR/DE/IT/ES
_P_BETWEEN = re.compile(
    r'(?:'
    r'between|'                 # English
    r'entre|'                   # French / Spanish
    r'zwischen|'                # German
    r'tra|fra'                  # Italian
    r')\s+(-?[\d.,]+)\s+'
    r'(?:and|et|und|und|e|y|à|a)\s+(-?[\d.,]+)',
    re.IGNORECASE,
)

# Pattern 4: "X to Y" — EN (also covers "de X à Y", "von X bis Y", "da X a Y")
_P_TO = re.compile(
    r'(-?[\d.,]+)\s+'
    r'(?:to|à|bis|a(?=\s))\s+'   # EN: "to" | FR: "à" | DE: "bis" | IT: "a"
    r'(-?[\d.,]+)',
    re.IGNORECASE,
)

# Pattern 5: "de X à Y" / "de X a Y" — French / Italian / Spanish
_P_DE_A = re.compile(
    r'de\s+(-?[\d.,]+)\s+[àa]\s+(-?[\d.,]+)',
    re.IGNORECASE,
)

# Pattern 6: "X-Y" bare dash — only if context or explicit keyword present
# (guarded further by _NUMERIC_CONTEXT_RE or min/max keywords to reduce FP)
_P_DASH = re.compile(
    r'(?<!\w)(-?[\d.,]+)\s*[-–—]\s*(-?[\d.,]+)(?!\w)',
)

# Ordered list — first match that yields valid (lo < hi) wins
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ('minmax',   _P_MINMAX),
    ('minmax_w', _P_MINMAX_WORDS),
    ('between',  _P_BETWEEN),
    ('de_a',     _P_DE_A),
    ('to',       _P_TO),
    ('dash',     _P_DASH),
]


def _parse_num(s: str) -> Optional[float]:
    """Parse a number string that may use comma as decimal separator."""
    try:
        return float(s.replace(',', '.'))
    except (ValueError, TypeError):
        return None


def extract_range(text: str) -> Optional[tuple]:
    """
    Return the first valid (min, max) pair found in text, or None.

    For the bare-dash pattern an additional numeric-context guard is applied
    to avoid false positives on option-code ranges like "1-5 scale".
    Valid means: both values are finite numbers and lo < hi.
    """
    if not text:
        return None

    has_context = bool(
        _NUMERIC_CONTEXT_RE.search(text) or
        _P_MINMAX.search(text) or
        _P_MINMAX_WORDS.search(text)
    )

    for name, pat in _PATTERNS:
        for m in pat.finditer(text):
            lo = _parse_num(m.group(1))
            hi = _parse_num(m.group(2))
            if lo is None or hi is None:
                continue
            # Reject inverted or equal bounds
            if lo >= hi:
                continue
            # Bare-dash: require numeric context nearby OR both values are
            # 4-digit years (1900–2100) which are self-evidently year ranges.
            if name == 'dash':
                _is_year_range = (1900 <= lo <= 2100 and 1900 <= hi <= 2100)
                if not _is_year_range:
                    window_start = max(0, m.start() - 60)
                    window_end   = min(len(text), m.end() + 60)
                    window = text[window_start:window_end]
                    if not has_context and not _NUMERIC_CONTEXT_RE.search(window):
                        continue
            # Sanity: reject implausibly large ranges (> 1 billion)
            if (hi - lo) > 1_000_000_000:
                continue
            return (lo, hi)
    return None


def _fmt(v: float) -> str:
    """Format a float as int if it's whole, otherwise as float."""
    return str(int(v)) if v == int(v) else str(v)


# ── Range info container ──────────────────────────────────────────────────────

class RangeInfo:
    __slots__ = ('qid', 'min_val', 'max_val', 'pattern_text',
                 'is_numeric', 'is_mandatory', 'has_prog_range')

    def __init__(self, qid, lo, hi, snippet, is_numeric, is_mandatory,
                 has_prog_range=False):
        self.qid          = qid
        self.min_val      = lo
        self.max_val      = hi
        self.pattern_text = snippet
        self.is_numeric   = is_numeric
        self.is_mandatory = is_mandatory
        self.has_prog_range = has_prog_range   # True if a RANGE row also exists

    def to_dict(self) -> dict:
        return {
            'qid':          self.qid,
            'min_val':      self.min_val,
            'max_val':      self.max_val,
            'pattern_text': self.pattern_text,
            'is_numeric':   self.is_numeric,
            'is_mandatory': self.is_mandatory,
            'has_prog_range': self.has_prog_range,
        }


def detect_doc_ranges(doc_questions: dict, xml_questions: list | None = None) -> dict:
    """
    Scan every doc question for range constraints.

    Returns {qid: RangeInfo} for all questions where a range was found.
    """
    _NUMERIC_TYPES = {
        'NUMERIC', 'NUMBER', 'INTEGER', 'FLOAT', 'DECIMAL', 'SCALE',
        'OPEN',    # open-ended text fields are often numeric in surveys
    }
    results: dict = {}

    # Build XML numeric type lookup for cross-reference
    _xml_numeric: set = set()
    if xml_questions:
        for _xq in xml_questions:
            if (_xq.get('type') or '').upper() in ('NUMERIC', 'NUMBER', 'INTEGER', 'FLOAT'):
                _norm = re.sub(r'[^a-z0-9]', '', (_xq.get('qid_normalized') or _xq.get('qid', '')).lower())
                if _norm:
                    _xml_numeric.add(_norm)

    _YEAR_BUCKET_RE = re.compile(
        r'\d{1,2}\s*[-–]\s*\d{1,2}\s*(years?|ans?|ann[e\xe9]es?)',
        re.IGNORECASE,
    )

    for qid, q in doc_questions.items():
        text       = (q.get('text') or '').strip()
        qtype      = (q.get('question_type') or '').upper()
        is_numeric = any(nt in qtype for nt in _NUMERIC_TYPES) or bool(q.get('is_numeric'))
        # Cross-reference XML type: if XML says numeric, trust it
        _qnorm = re.sub(r'[^a-z0-9]', '', qid.lower())
        if _qnorm in _xml_numeric:
            is_numeric = True
        is_mand    = bool(q.get('is_mandatory') or q.get('mandatory'))
        has_options = bool(q.get('options'))

        # Early exit: 3+ answer options whose text contains year/an/année bucket
        # patterns (e.g. "2-9 ans", "10-19 years") → skip ALL range detection.
        # These are categorical age buckets, not numeric input bounds.
        if has_options and not is_numeric:
            _opt_texts = [o.get('text', '') for o in (q.get('options') or [])]
            if (len(_opt_texts) >= 3
                    and sum(1 for t in _opt_texts if _YEAR_BUCKET_RE.search(t)) >= 2):
                continue

        # Scan question text only — option labels are categorical answer buckets,
        # not numeric input constraints, and trigger false R041/R042 positives.
        all_text = text

        # For questions with answer options that are NOT explicitly numeric,
        # only use strong explicit MIN/MAX keyword patterns — not bare dash.
        # This prevents "2-9 years / 10-19 years" answer bucket lists from
        # being misidentified as numeric input constraints.
        if has_options and not is_numeric:
            bounds = None
            for pat_name, pat in _PATTERNS:
                if pat_name == 'dash':
                    continue  # skip bare-dash for categorical questions
                m = pat.search(all_text)
                if m:
                    lo = _parse_num(m.group(1))
                    hi = _parse_num(m.group(2))
                    if lo is not None and hi is not None and lo < hi:
                        # Extra guard: if multiple different ranges detected in
                        # the same text, they are almost certainly answer buckets
                        all_matches = [(lo, hi)]
                        for _, pat2 in _PATTERNS:
                            for m2 in pat2.finditer(all_text):
                                if m2.start() == m.start():
                                    continue
                                lo2 = _parse_num(m2.group(1))
                                hi2 = _parse_num(m2.group(2))
                                if lo2 is not None and hi2 is not None and lo2 < hi2:
                                    all_matches.append((lo2, hi2))
                        if len(all_matches) >= 2:
                            bounds = None  # multiple ranges = categorical buckets
                        else:
                            bounds = (lo, hi)
                        break
        else:
            bounds = extract_range(all_text)

        if bounds is None:
            # Fall back: is the question text numeric-context heavy?
            # If so, flag as numeric even without explicit range for R045.
            if is_numeric and is_mand:
                results[qid] = RangeInfo(qid, None, None, '', is_numeric, is_mand)
            continue

        lo, hi = bounds
        # Extract snippet around the match for evidence
        m = None
        for _, pat in _PATTERNS:
            m = pat.search(all_text)
            if m:
                break
        snippet = all_text[max(0, m.start()-20):m.end()+20].strip() if m else all_text[:60]

        results[qid] = RangeInfo(qid, lo, hi, snippet, is_numeric, is_mand)

    return results


def _make_range_issue(qid: str, rule: str, severity: str,
                      evidence: str, confidence: int,
                      min_val=None, max_val=None) -> dict:
    check_labels = {
        'R041': 'RANGE_MIN_NOT_ENFORCED',
        'R042': 'RANGE_MAX_NOT_ENFORCED',
        'R043': 'RANGE_MISSING_VALIDATION',
        'R044': 'RANGE_MISMATCH',
        'R045': 'RANGE_MANDATORY_BLANK',
    }
    check = check_labels.get(rule, rule)
    return {
        'qid':        qid,
        'min_val':    min_val,
        'max_val':    max_val,
        'rule':       rule,
        'check':      check,
        'type':       check.replace('_', ' '),
        'severity':   severity,
        'evidence':   evidence,
        'details':    evidence,
        'confidence': confidence,
        'is_range_issue': True,
    }


# ── Public validation ─────────────────────────────────────────────────────────

def validate_ranges(
    doc_questions: dict,
    live_questions: Optional[dict] = None,
    xml_questions: list | None = None,
) -> list:
    """
    Run R041–R045 checks.  live_questions is accepted for future live-input
    comparison but most checks are doc-derived; Playwright test cases (R041/R042)
    confirm enforcement at runtime.

    R041/R042 (boundary tests) are ONLY generated when the question is confirmed
    numeric — prevents categorical answer buckets like "2-9 years / 10-19 years"
    from generating spurious range validation tests.

    Returns list of issue dicts, each containing:
        qid, min_val, max_val, rule, check, type, severity, evidence,
        details, confidence, is_range_issue.
    """
    issues: list = []
    range_map = detect_doc_ranges(doc_questions, xml_questions=xml_questions)

    # Track QIDs that have ranges so we can check for conflicting detections
    _range_counts: dict = {}

    for qid, ri in range_map.items():
        if ri.min_val is None and ri.max_val is None:
            # Only a mandatory numeric with no explicit range — R045 only
            if ri.is_mandatory and ri.is_numeric:
                issues.append(_make_range_issue(
                    qid, 'R045', 'HIGH',
                    f'Numeric mandatory question {qid} — blank entry must be blocked '
                    f'(generate Playwright test)',
                    80,
                ))
            continue

        lo, hi = ri.min_val, ri.max_val

        # R041 / R042 — Boundary tests ONLY for confirmed numeric questions.
        # Categorical answer buckets (e.g. "2-9 years", "10-19 years") must
        # never generate boundary validation tests — they are not numeric inputs.
        if ri.is_numeric:
            # R041 — Min boundary
            issues.append(_make_range_issue(
                qid, 'R041', 'HIGH',
                f'Range {_fmt(lo)}–{_fmt(hi)} detected in spec '
                f'("{ri.pattern_text[:60]}") — '
                f'verify live blocks values < {_fmt(lo)}',
                75, lo, hi,
            ))

            # R042 — Max boundary
            issues.append(_make_range_issue(
                qid, 'R042', 'HIGH',
                f'Range {_fmt(lo)}–{_fmt(hi)} detected in spec — '
                f'verify live blocks values > {_fmt(hi)}',
                75, lo, hi,
            ))

            # R045 — Mandatory numeric with range
            if ri.is_mandatory:
                issues.append(_make_range_issue(
                    qid, 'R045', 'HIGH',
                    f'Mandatory numeric {qid} (range {_fmt(lo)}–{_fmt(hi)}) — '
                    f'blank entry must be blocked',
                    80, lo, hi,
                ))
        else:
            # R043 — Range detected in spec but question not confirmed numeric.
            # Flag as MEDIUM for manual review — may be a categorical answer list
            # or a question that needs a NUMERIC type assignment.
            if not ri.has_prog_range:
                issues.append(_make_range_issue(
                    qid, 'R043', 'MEDIUM',
                    f'Range {_fmt(lo)}–{_fmt(hi)} found in doc text '
                    f'but question type not marked NUMERIC — '
                    f'verify if this is a numeric input constraint or categorical options',
                    55, lo, hi,
                ))

        _range_counts[qid] = _range_counts.get(qid, 0) + 1

    # R044 — Count distinct ranges per QID; flag if we found ambiguous bounds
    # (two different patterns in the same question yielded different ranges)
    for qid, q in doc_questions.items():
        text = (q.get('text') or '').strip()
        all_text = text + ' ' + ' '.join(
            o.get('text', '') for o in q.get('options', [])
        )
        # Collect ALL matches across all patterns to detect conflicts
        found_ranges: list = []
        for _, pat in _PATTERNS:
            for m in pat.finditer(all_text):
                lo_c = _parse_num(m.group(1))
                hi_c = _parse_num(m.group(2))
                if lo_c is not None and hi_c is not None and lo_c < hi_c:
                    # Check if this bound pair is materially different from others
                    new = (lo_c, hi_c)
                    if not any(abs(e[0] - lo_c) < 0.5 and abs(e[1] - hi_c) < 0.5
                               for e in found_ranges):
                        found_ranges.append(new)
        if len(found_ranges) >= 2:
            desc = ', '.join(f'{_fmt(a)}–{_fmt(b)}' for a, b in found_ranges[:3])
            issues.append(_make_range_issue(
                qid, 'R044', 'MEDIUM',
                f'Multiple conflicting ranges detected in {qid}: {desc} — '
                f'verify which is the intended constraint',
                65,
                min(r[0] for r in found_ranges),
                max(r[1] for r in found_ranges),
            ))

    return issues


# ── Test case generation ──────────────────────────────────────────────────────

def generate_range_test_cases(
    doc_questions: dict,
    id_prefix: str = 'TC_RNG',
    xml_questions: list | None = None,
) -> list:
    """
    Generate RANGE-type Playwright test cases for all questions with
    detected bounds.  Five test cases per question:
      1. enter MIN-1  → validation_block        (R041)
      2. enter MIN    → accept_value            (R041)
      3. enter MAX+1  → validation_block        (R042)
      4. enter MAX    → accept_value            (R042)
      5. enter 0      → block_or_accept         (contextual)

    Also generates MANDATORY blank test (R045) for mandatory numeric
    questions with or without explicit range.

    Returns list of dicts compatible with test_generator.TestCase.to_dict().
    """
    range_map = detect_doc_ranges(doc_questions, xml_questions=xml_questions)
    counter   = [0]

    def _next_id() -> str:
        counter[0] += 1
        return f'{id_prefix}_{counter[0]:03d}'

    def _tc(qid, action, expected, condition_text, notes,
            priority='HIGH', rule='R041') -> dict:
        return {
            'test_id':        _next_id(),
            'qid':            qid,
            'type':           'RANGE',
            'action':         action,
            'expected':       expected,
            'condition_text': condition_text,
            'priority':       priority,
            'auto_runnable':  True,
            'source':         'range_validator',
            'notes':          notes,
            'rule':           rule,
        }

    tests: list = []

    for qid, ri in range_map.items():
        # Only generate auto-runnable boundary tests for confirmed numeric questions.
        # Categorical questions (answer buckets) must not generate Playwright tests.
        if not ri.is_numeric:
            continue
        lo, hi = ri.min_val, ri.max_val
        cond = f'Range {_fmt(lo) if lo is not None else "?"}'
        cond += f'–{_fmt(hi) if hi is not None else "?"}'
        cond += f' detected in: "{ri.pattern_text[:50]}"'

        if lo is not None and hi is not None:
            below = lo - 1
            above = hi + 1

            tests.append(_tc(qid,
                f'enter_value({_fmt(below)})',
                'validation_block', cond,
                f'Value {_fmt(below)} is below MIN={_fmt(lo)} — must be blocked',
                rule='R041'))

            tests.append(_tc(qid,
                f'enter_value({_fmt(lo)})',
                'accept_value', cond,
                f'MIN value {_fmt(lo)} — must be accepted',
                priority='MEDIUM', rule='R041'))

            tests.append(_tc(qid,
                f'enter_value({_fmt(above)})',
                'validation_block', cond,
                f'Value {_fmt(above)} is above MAX={_fmt(hi)} — must be blocked',
                rule='R042'))

            tests.append(_tc(qid,
                f'enter_value({_fmt(hi)})',
                'accept_value', cond,
                f'MAX value {_fmt(hi)} — must be accepted',
                priority='MEDIUM', rule='R042'))

            # Zero boundary — context-dependent check
            if lo > 0:
                tests.append(_tc(qid,
                    'enter_value(0)',
                    'validation_block', cond,
                    f'0 is below MIN={_fmt(lo)} — should be blocked',
                    priority='MEDIUM', rule='R041'))

        # R045 — mandatory blank test
        if ri.is_mandatory:
            tests.append({
                'test_id':        _next_id(),
                'qid':            qid,
                'type':           'MANDATORY',
                'action':         'submit_without_answer()',
                'expected':       'validation_error',
                'condition_text': f'Mandatory numeric {qid}',
                'priority':       'HIGH',
                'auto_runnable':  True,
                'source':         'range_validator',
                'notes':          'Mandatory numeric — blank entry must be blocked',
                'rule':           'R045',
            })

    # Deduplicate by (qid, action, expected)
    seen: set = set()
    unique: list = []
    for t in tests:
        key = (t['qid'], t['action'], t['expected'])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique
