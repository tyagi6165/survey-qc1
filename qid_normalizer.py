"""
qid_normalizer.py — Single source of truth for QID filtering & normalisation.

This module is imported by app.py, survey_graph.py, and any other module that
needs to decide whether a QID is real, internal, or a framework artefact.

Public API (filtering)
----------------------
should_skip_qid(qid)   → bool
    Master check.  True = exclude from all comparisons and reports.

is_valid_qid(qid)      → bool
    False for stop words, letter-only non-screeners, too long.

is_framework_page(qid) → bool
    True for Agreement, Invoice, Article, Display, qPassword, etc.

is_internal_id(qid)    → bool
    True for Confirmit/Forsta framework variables (S99Date, SumOfRows, …).

is_s99_date_pattern(qid) → bool
    True for S99Datex1-4, S99H, S99M, etc.

get_parent_qid(qid)    → str
    Collapse child QID to parent: S99Datex1→S99, Q11x1→Q11, Q40bis_1→Q40bis.

verify_extraction(qid, text) → (bool, str)
    Check extracted text actually belongs to *qid*.
    Returns (True, "VERIFIED"), (True, "UNVERIFIED"), or
    (False, "WRONG_CONTENT (X)").

Public API (normalisation)
--------------------------
INTERNAL_NORMS : frozenset[str]
    Alphanumeric lower-case norms of Confirmit/Forsta framework variables.

S99_DATE_PAT : re.Pattern
    Regex for S99-type date/time variants (S99Datex1, S99Timex2, S99Mx3, …).

FW_NORM_PAT : re.Pattern
    Regex for Confirmit normalised-prefix framework variable families.

SCREENER_QIDS : frozenset[str]
    Letter-only QIDs that are real survey screener questions, not words.

build_strip_candidates(raw_qid, xnorm) → list[str]
    Progressively-stripped normalised forms for fallback matching.

run_tests() → bool
    Run all built-in test cases.
"""
import re

# ---------------------------------------------------------------------------
# Internal norms (Confirmit/Forsta framework variables)
# ---------------------------------------------------------------------------
INTERNAL_NORMS: frozenset = frozenset({
    # Technical date/time input fields
    's99', 's99date', 's99h', 's99m', 's99s', 's99time',
    # Survey-level metadata
    'sumofrows', 'surveyloi', 'surveyid',
    # Scripting / display system variables
    'startupscript', 'translationstatuses', 'questiontriggers',
    'questionmaskpredicate', 'predicates', 'hasweights',
    # Image / layout metadata
    'answerimagedefault', 'answerimageover', 'answerimageselected',
    'answerimageheight', 'answerimagewidth',
    # Generic XML element names that leak through as QIDs
    'scale', 'texts', 'textsright', 'formtext',
    'expression', 'title', 'name', 'instruction',
    # Respondent tracking
    'respondentid', 'dataqualitycheck',
})

# Catches S99Datex1, S99Datex2, S99Timex3, S99H, S99M, S99S, etc.
S99_DATE_PAT: re.Pattern = re.compile(r'^s\d+(date|time|h|m|s)(x\d+)?$')

# Confirmit normalised-prefix framework variable families.
# Applied after doc/live QID matching so real survey QIDs are never filtered.
FW_NORM_PAT: re.Pattern = re.compile(
    r'^(bg[a-z]|lang[a-z]|list[a-z]|hid[a-z]|hsurvey|survey'
    r'|setting|interview|redirect|term\d|blocktocall'
    r'|punch|captures?[0-9]?|page[qi0-9]|call[a-z]|dial[a-z])'
)

# ---------------------------------------------------------------------------
# QID stop words — common words that look like QIDs when \d* allows 0 digits
# ---------------------------------------------------------------------------
QID_STOP_WORDS: frozenset = frozenset({
    # French
    'aide', 'entre', 'plus', 'non', 'oui', 'tous', 'dans',
    'pour', 'avec', 'sans', 'sous', 'quel', 'quelle',
    'veuillez', 'merci', 'autre', 'votre', 'cette',
    'moins', 'combien', 'depuis', 'parmi', 'selon',
    'quels', 'quelles', 'comment', 'chaque', 'aussi',
    'mais', 'donc', 'comme', 'tout', 'bien', 'encore',
    'jamais', 'toujours', 'avant', 'apres', 'chez',
    'vers', 'prix', 'note', 'type', 'code', 'page',
    'test', 'info', 'date', 'heure', 'temps',
    # English common words
    'the', 'and', 'for', 'not', 'all', 'any', 'but',
    'yes', 'ask', 'end', 'new', 'old', 'get', 'set',
    'show', 'hide', 'next', 'back', 'stop', 'start',
    'close', 'open', 'continue', 'thank', 'thanks',
    # German common words
    'und', 'oder', 'mit', 'von', 'bei', 'nach',
    'wenn', 'dann', 'also', 'aber', 'noch', 'mehr',
})

# Letter-only QIDs that are real screener/quota questions, not common words
SCREENER_QIDS: frozenset = frozenset({
    'SPE', 'AGE', 'GENDER', 'COUNTRY', 'REGION',
    'LANG', 'QUOTA', 'OCCUPATION', 'SPECIALTY',
})

# Framework/system pages that appear in the TN but are NOT real questions.
# Uses prefix match (re.match without $) so "Agreement_Email" is caught by
# the "agreement" prefix, "PreviewCompleteAgreement" by "preview", etc.
FRAMEWORK_PAGE_PAT: re.Pattern = re.compile(
    r'(?i)^(?:agreement|fee_invoice|invoice|email|password|preview|complete|'
    r'article\d*|display|facture|internal.?error|completion|'
    r'consent|preambule|avantages|prestation|dispvirement|'
    r'santedeclare|iban[a-g]?|gbic|'
    r'testinfo|qpassword|i\d{3,})'
)

_VALID_QID_PAT: re.Pattern = re.compile(
    r'^[A-Za-z]{1,8}\d+[a-zA-Z]*(?:_\d+|\.\d+)?$'
)

# ---------------------------------------------------------------------------
# Core normalisation
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Alphanumeric-only lower-case form — matches _xml_by_nqid key format."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


# ---------------------------------------------------------------------------
# Filtering functions
# ---------------------------------------------------------------------------
def is_valid_qid(candidate: str) -> bool:
    """Return True if candidate looks like a real survey QID, not a common word."""
    if not candidate:
        return False
    if len(candidate) > 15:
        return False
    if candidate.lower() in QID_STOP_WORDS:
        return False
    # Letter-only: must be an explicit screener/metadata QID
    if not any(c.isdigit() for c in candidate):
        return candidate.upper() in SCREENER_QIDS
    return bool(_VALID_QID_PAT.match(candidate))


def is_framework_page(qid: str) -> bool:
    """Return True if qid is a framework/system page, not a real survey question."""
    return bool(FRAMEWORK_PAGE_PAT.match(qid))


def is_internal_id(qid: str) -> bool:
    """Return True if qid is a Confirmit/Forsta internal framework variable."""
    n = _norm(qid)
    if n in INTERNAL_NORMS:
        return True
    if S99_DATE_PAT.match(n):
        return True
    if FW_NORM_PAT.match(n):
        return True
    # Confirmit camelCase internal vars: hS1, iM1, gQ11, Hid*
    if re.match(r'^[hig][A-Z]', qid) or re.match(r'^Hid[A-Z]', qid):
        return True
    # _hidden shadow copies
    if qid.endswith('_hidden'):
        return True
    return False


def is_s99_date_pattern(qid: str) -> bool:
    """Return True for S99Date, S99Datex1-4, S99H, S99M, S99S, etc."""
    return bool(S99_DATE_PAT.match(_norm(qid)))


def get_parent_qid(qid: str) -> str:
    """Collapse child QID to its logical parent.

    Examples:
        S99Datex1 → S99
        Q11x1     → Q11
        Q40bis_1  → Q40bis
        Q23bis    → Q23bis  (bis is a question variant, not a child)
        R2x1      → R2
        D3A       → D3      (single-letter A/B/C sub-question suffix)
        Q10B      → Q10
    """
    r = qid.strip()
    # S99Date/Time/H/M/S variants → S99.
    # Require the suffix to start directly after a digit so that the terminal
    # letters of 'bis' (s after i) and 'ter' (r after e) are NOT matched.
    r = re.sub(r'(?<=\d)(date|time|h|m|s)(x?\d+)?$', '', r, flags=re.I)
    # xN suffix: Q11x1 → Q11
    r = re.sub(r'[xX]\d+$', '', r)
    # _N suffix: Q40bis_1 → Q40bis
    r = re.sub(r'_\d+$', '', r)
    # Single letter suffix directly after a digit: D3A→D3, Q10B→Q10.
    # Lookbehind ensures the letter is glued to the digit part, not part of
    # a multi-char suffix (bis/ter/info/ex all have >1 letter before the end).
    r = re.sub(r'(?<=[0-9])[A-Za-z]$', '', r)
    return r if r else qid


def should_skip_qid(qid: str) -> bool:
    """Master check — should this QID be excluded from comparisons and reports?

    Combines all filtering rules into a single call used by every phase.
    """
    if not qid:
        return True
    if not is_valid_qid(qid):
        return True    # stop word, no digits, too long
    if is_framework_page(qid):
        return True    # Agreement, Invoice, Password, Article pages
    if is_internal_id(qid):
        return True    # SumOfRows, S99Date, Confirmit scripting vars
    return False


def verify_extraction(qid: str, extracted_text: str) -> tuple:
    """Check that extracted_text actually belongs to qid, not another question.

    Returns:
        (True,  'VERIFIED')             — qid found in text
        (True,  'UNVERIFIED')           — cannot confirm but no evidence of wrong content
        (True,  'EMPTY')                — no text to check
        (False, 'WRONG_CONTENT (X)')    — text contains a different QID's marker
    """
    if not extracted_text:
        return True, 'EMPTY'
    if qid.lower() in extracted_text.lower():
        return True, 'VERIFIED'
    other = re.search(r'\[question\s+id:\s*([^\]\s]+)\]', extracted_text, re.IGNORECASE)
    if other and other.group(1).lower() != qid.lower():
        return False, f'WRONG_CONTENT ({other.group(1)})'
    return True, 'UNVERIFIED'


# ---------------------------------------------------------------------------
# Strip candidates (used by XML comparison loop)
# ---------------------------------------------------------------------------
def build_strip_candidates(raw_qid: str, xnorm: str) -> list:
    """Return progressively-stripped normalised QID forms for fallback matching.

    Stripping stages applied in order; each distinct result is kept:
      1. xN suffix    Q11x1    → Q11
      2. _N suffix    Q40bis_1 → Q40bis
      3. _BIS/_TER   Q23_BIS  → Q23bis  (de-underscore + lower-case)
      4. bis/ter      Q23bis   → Q23     (parent-QID fallback)

    Candidates equal to xnorm are excluded — they are already checked upstream.
    """
    seen: set = set()
    out: list = []

    def _add(s: str) -> None:
        n = _norm(s)
        if n and n != xnorm and n not in seen:
            seen.add(n)
            out.append(n)

    r = raw_qid.strip()
    r = re.sub(r'[xX]\d+$', '', r)                                          # Q11x1  → Q11
    _add(r)
    r = re.sub(r'_\d+$', '', r)                                             # Q40bis_1 → Q40bis
    _add(r)
    r = re.sub(r'_(bis|ter)$', lambda m: m.group(1).lower(), r, flags=re.I) # Q23_BIS → Q23bis
    _add(r)
    r = re.sub(r'(bis|ter)$', '', r, flags=re.I)                            # Q23bis  → Q23
    _add(r)
    return out


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
_CANDIDATE_CASES = [
    ('Q11x1',     'q11x1',    ['q11'],           'xN strip → Q11'),
    ('Q11x2',     'q11x2',    ['q11'],           'xN strip → Q11'),
    ('Q11x3',     'q11x3',    ['q11'],           'xN strip → Q11'),
    ('R2x1',      'r2x1',     ['r2'],            'xN strip → R2'),
    ('Q15bisx1',  'q15bisx1', ['q15bis', 'q15'], 'xN then bis strip'),
    ('Q40bis_1',  'q40bis1',  ['q40bis', 'q40'], '_N → Q40bis, bis → Q40'),
    ('Q40bis_2',  'q40bis2',  ['q40bis', 'q40'], '_N variant'),
    ('Q23bis',    'q23bis',   ['q23'],            'bis strip → Q23'),
    ('Q23BIS',    'q23bis',   ['q23'],            'uppercase BIS'),
    ('Q23_BIS',   'q23bis',   ['q23'],            '_BIS de-underscored then stripped'),
    ('Q15ter',    'q15ter',   ['q15'],            'ter strip → Q15'),
    ('S99Datex1', 's99datex1', ['s99date'],       's99date via candidate'),
    ('S99Datex2', 's99datex2', ['s99date'],       's99date via candidate'),
    ('S99Datex3', 's99datex3', ['s99date'],       's99date via candidate'),
    ('S99Datex4', 's99datex4', ['s99date'],       's99date via candidate'),
    ('SPE',       'spe',       [],                'all-letter QID'),
    ('Q1',        'q1',        [],                'simple QID'),
    ('Q15bis',    'q15bis',    ['q15'],           'bis-only strip'),
]

_INTERNAL_EXACT_CASES = [
    ('SumOfRows',          True,  'exact internal'),
    ('Survey_LOI',         True,  'surveyloi'),
    ('SurveyID',           True,  'surveyid'),
    ('S99Date',            True,  's99date direct'),
    ('S99H',               True,  's99h direct'),
    ('StartupScript',      True,  'startupscript'),
    ('QuestionTriggers',   True,  'questiontriggers'),
    ('Q1',                 False, 'real QID — must not be internal'),
    ('SPE',                False, 'screener — must not be internal'),
    ('Q23bis',             False, 'real bis question'),
]

_S99_PAT_CASES = [
    ('s99datex1', True,  'S99Datex1 pattern'),
    ('s99datex4', True,  'S99Datex4 pattern'),
    ('s99timex2', True,  'S99Timex2 pattern'),
    ('s99mx1',    True,  'S99Mx1 pattern'),
    ('s99h',      True,  'S99H bare'),
    ('s99m',      True,  'S99M bare'),
    ('q11x1',     False, 'not an S99 field'),
    ('q23bis',    False, 'not an S99 field'),
]

_REGRESSION_CASES = [
    ('Q11x1',    'q11x1',     'q11',     'doc',    'Q11x1 → parent Q11 in doc'),
    ('Q11x2',    'q11x2',     'q11',     'doc',    'Q11x2 → parent Q11 in doc'),
    ('Q11x3',    'q11x3',     'q11',     'doc',    'Q11x3 → parent Q11 in doc'),
    ('Q23bis',   'q23bis',    'q23',     'doc',    'Q23bis → parent Q23 in doc'),
    ('Q40bis_1', 'q40bis1',   'q40bis',  'doc',    'Q40bis_1 → Q40bis in doc'),
    ('R2x1',     'r2x1',      'r2',      'doc',    'R2x1 → parent R2 in doc'),
    ('S99Datex1','s99datex1', None,      'intern', 'S99Datex1 → internal (pattern)'),
    ('S99Datex2','s99datex2', None,      'intern', 'S99Datex2 → internal (pattern)'),
    ('S99Datex3','s99datex3', None,      'intern', 'S99Datex3 → internal (pattern)'),
    ('S99Datex4','s99datex4', None,      'intern', 'S99Datex4 → internal (pattern)'),
    ('SumOfRows','sumofrows', None,      'intern', 'SumOfRows → exact internal'),
]

_MOCK_DOC  = {'q11', 'q23', 'q40bis', 'r2', 'q15bis'}
_MOCK_LIVE = {'q11', 'q23', 'r2'}

# should_skip_qid() test cases (from E17402 false-positive analysis)
_SKIP_CASES = [
    # internal / S99 variants
    ('S99Date',          True,  'S99Date → internal'),
    ('S99Datex1',        True,  'S99Datex1 → S99 pattern'),
    ('S99H',             True,  'S99H → S99 pattern'),
    ('SumOfRows',        True,  'SumOfRows → exact internal'),
    # framework pages
    ('Agreement_Email',  True,  'Agreement_Email → framework prefix'),
    ('qPassword',        True,  'qPassword → framework prefix'),
    ('FACTURE_DHONORAIRES', True, 'facture prefix → framework'),
    ('Article1',         True,  'Article1 → framework prefix'),
    ('DisplayResult',    True,  'DisplayResult → framework prefix'),
    # French stop words (no digits)
    ('Aide',             True,  'Aide → stop word'),
    ('Entre',            True,  'Entre → stop word'),
    ('Plus',             True,  'Plus → stop word'),
    ('Non',              True,  'Non → stop word'),
    ('Veuillez',         True,  'Veuillez → stop word'),
    # real QIDs — must NOT be skipped
    ('Q1',               False, 'Q1 → real QID'),
    ('S3a',              False, 'S3a → real QID'),
    ('R0',               False, 'R0 → real QID'),
    ('SPE',              False, 'SPE → screener'),
    ('Q11bis',           False, 'Q11bis → real QID'),
    ('S5a',              False, 'S5a → real QID'),
    ('P3b',              False, 'P3b → real QID'),
]


def run_tests() -> bool:
    failed = []

    # 1. Candidate-building
    for raw, xnorm, expected, desc in _CANDIDATE_CASES:
        got = build_strip_candidates(raw, xnorm)
        if got != expected:
            failed.append(
                f'  FAIL candidates [{desc}]:\n'
                f'       build_strip_candidates({raw!r}, {xnorm!r})\n'
                f'       expected {expected}\n'
                f'       got      {got}'
            )

    # 2. INTERNAL_NORMS exact membership
    for raw, expected, desc in _INTERNAL_EXACT_CASES:
        n = _norm(raw)
        got = n in INTERNAL_NORMS
        if got != expected:
            failed.append(
                f'  FAIL internal [{desc}]: {raw!r} → norm={n!r}, '
                f'expected={expected}, got={got}'
            )

    # 3. S99_DATE_PAT regex
    for xnorm, expected, desc in _S99_PAT_CASES:
        got = bool(S99_DATE_PAT.match(xnorm))
        if got != expected:
            failed.append(
                f'  FAIL S99_DATE_PAT [{desc}]: {xnorm!r}, '
                f'expected={expected}, got={got}'
            )

    # 4. Regression — simulate the full skip decision in the comparison loop
    for raw, xnorm, match_target, src, desc in _REGRESSION_CASES:
        if src == 'intern':
            exact_ok = xnorm in INTERNAL_NORMS
            pat_ok   = bool(S99_DATE_PAT.match(xnorm))
            cand_ok  = any(c in INTERNAL_NORMS or S99_DATE_PAT.match(c)
                           for c in build_strip_candidates(raw, xnorm))
            ok = exact_ok or pat_ok or cand_ok
        else:
            cands = build_strip_candidates(raw, xnorm)
            ok = any(c == match_target
                     and (c in _MOCK_DOC or c in _MOCK_LIVE)
                     for c in cands)
        if not ok:
            failed.append(
                f'  FAIL regression [{desc}]:\n'
                f'       {raw!r} → xnorm={xnorm!r}, '
                f'candidates={build_strip_candidates(raw, xnorm)}\n'
                f'       expected to resolve via {src}'
                + (f' matching {match_target!r}' if match_target else '')
            )

    # 5. should_skip_qid() — E17402 false-positive cases
    for qid, expected, desc in _SKIP_CASES:
        got = should_skip_qid(qid)
        if got != expected:
            failed.append(
                f'  FAIL should_skip_qid [{desc}]: {qid!r} → '
                f'expected={expected}, got={got}'
            )

    total = (len(_CANDIDATE_CASES) + len(_INTERNAL_EXACT_CASES)
             + len(_S99_PAT_CASES) + len(_REGRESSION_CASES) + len(_SKIP_CASES))
    if failed:
        print(f'FAILED {len(failed)}/{total}:')
        for f in failed:
            print(f)
        return False
    print(f'All {total} tests pass ✓')
    return True


if __name__ == '__main__':
    import sys
    sys.exit(0 if run_tests() else 1)
