"""
consensus_engine.py — 3-Engine Consensus Scoring for SurveyQC.

Engines:
  1  Rule Engine   — deterministic rule violations (confidence + severity)
  2  Playwright    — live browser verification    (term_results + playwright_tests)
  3  AI Judge      — semantic review              (ai_verdict on issues)

Scoring matrix key: (rule_bug, pw_bug, ai_bug)
  True  = engine says BUG
  False = engine says PASS / not a bug (negative signal)
  None  = engine not available / no opinion

Issues are enriched IN-PLACE with 'consensus_score' and 'consensus_tier'.
No data is copied — caller's lists are mutated.
"""

from __future__ import annotations
from typing import Optional

# ── Tier labels ────────────────────────────────────────────────────────────────
TIER_CONFIRMED  = 'CONFIRMED_BUG'   # 95-99%
TIER_LIKELY     = 'LIKELY_BUG'      # 65-94%
TIER_REVIEW     = 'NEEDS_REVIEW'    # 35-64%
TIER_SUPPRESSED = 'SUPPRESSED'      # < 35%

# ── 3-Engine scoring matrix ────────────────────────────────────────────────────
_SCORES: dict[tuple, int] = {
    # All 3 agree
    (True,  True,  True ):  99,
    # Rule + one other
    (True,  False, True ):  85,
    (True,  True,  False):  80,
    (True,  None,  True ):  78,
    (True,  True,  None ):  75,
    # Rule only + negatives/unknowns
    (True,  None,  None ):  55,
    (True,  False, None ):  45,
    (True,  None,  False):  35,
    (True,  False, False):  20,
    # Two non-rule engines agree
    (False, True,  True ):  70,
    (None,  True,  True ):  70,
    # Single non-rule engine
    (False, None,  True ):  40,
    (None,  None,  True ):  40,
    (False, True,  None ):  50,
    (None,  True,  None ):  50,
    # Weak / contradictory
    (False, False, True ):  35,
    (None,  False, True ):  35,
    (False, False, None ):  15,
    (None,  False, None ):  15,
    (False, None,  False):  10,
    (None,  None,  False):  10,
    (False, False, False):   5,
    (None,  False, False):   5,
    (False, None,  None ):  10,
    # No signal at all — use existing confidence
    (None,  None,  None ):  -1,  # special: fall back to issue.confidence
}

# Health deductions per (tier, severity)
_DEDUCTIONS: dict[tuple, float] = {
    (TIER_CONFIRMED, 'HIGH'):   5.0,
    (TIER_CONFIRMED, 'MEDIUM'): 3.0,
    (TIER_CONFIRMED, 'LOW'):    1.5,
    (TIER_LIKELY,    'HIGH'):   2.0,
    (TIER_LIKELY,    'MEDIUM'): 2.0,
    (TIER_LIKELY,    'LOW'):    1.0,
    (TIER_REVIEW,    'HIGH'):   0.5,
    (TIER_REVIEW,    'MEDIUM'): 0.5,
    (TIER_REVIEW,    'LOW'):    0.5,
}

# Rule group → display category
_GROUP_CAT: dict[int, str] = {
    1: 'Routing', 2: 'Termination', 3: 'Routing',
    4: 'Piping',  5: 'Piping',      6: 'Variables',
    7: 'Routing', 8: 'Options',     9: 'Routing', 10: 'Routing',
}

# Check type → display category
_CHECK_CAT: dict[str, str] = {
    'TEXT_MISMATCH': 'Routing',           'TEXT_POSSIBLE_MISMATCH': 'Routing',
    'MISSING_WORDS': 'Routing',           'QUESTION_MISSING': 'Routing',
    'QUESTION_MISSING_IN_LIVE': 'Routing','QUESTION_ORDER_MISMATCH': 'Routing',
    'MANDATORY_MARKER_MISSING': 'Routing','MANDATORY_CHECK': 'Routing',
    'TERMINATION_UNVERIFIED': 'Termination','TERMINATION_MISMATCH': 'Termination',
    'PIPING_NOT_RESOLVED': 'Piping',      'PIPING_IN_DOC_NOT_IN_XML': 'Piping',
    'OPTIONS_MISSING': 'Options',         'OPTIONS_COUNT_MISMATCH': 'Options',
    'ALL_OPTIONS_MISSING': 'Options',     'CODE_SEQUENCE_ERROR': 'Options',
    'ANSWER_CODES_MISSING': 'Options',    'OPTION_TEXT_MISMATCH': 'Options',
    'VARIABLE_MISSING': 'Variables',      'VARIABLE_MISMATCH': 'Variables',
}

CATEGORIES = ('Routing', 'Termination', 'Piping', 'Options', 'Variables')


# ── Signal extractors ──────────────────────────────────────────────────────────

def _rule_sig(issue: dict) -> Optional[bool]:
    if issue.get('ai_dismissed'):
        return None
    sev  = issue.get('severity', 'INFO')
    conf = issue.get('confidence') or 0
    if sev in ('HIGH', 'MEDIUM') or conf >= 55:
        return True
    if sev == 'LOW' and conf >= 40:
        return True
    return None


def _ai_sig(issue: dict) -> Optional[bool]:
    v = issue.get('ai_verdict', '')
    if v == 'CONFIRMED_BUG':  return True
    if v == 'FALSE_POSITIVE': return False
    return None


def _pw_sig(issue: dict, pw_map: dict) -> Optional[bool]:
    qid = (issue.get('qid') or '').upper().strip()
    st  = pw_map.get(qid)
    if st in ('FAIL', 'FAIL_TERM'): return True
    if st == 'PASS':                return False
    return None


# ── Playwright map builder ─────────────────────────────────────────────────────

def _build_pw_map(term_results: list, pw_tests: dict) -> dict:
    """Build {normalized_qid: 'PASS'|'FAIL'|'FAIL_TERM'} from all live evidence."""
    m: dict = {}
    for r in (term_results or []):
        qid = (r.get('test_qid') or '').upper().strip()
        if not qid:
            continue
        if not r.get('passed') and not r.get('needs_review'):
            m[qid] = 'FAIL_TERM'
        elif r.get('passed') and qid not in m:
            m[qid] = 'PASS'
    for r in ((pw_tests or {}).get('results', [])):
        qid = (r.get('qid') or '').upper().strip()
        if not qid:
            continue
        st = r.get('status', '')
        if st == 'FAIL' and m.get(qid) not in ('FAIL', 'FAIL_TERM'):
            m[qid] = 'FAIL'
        elif st == 'PASS' and qid not in m:
            m[qid] = 'PASS'
    return m


# ── Feedback adjustment ────────────────────────────────────────────────────────

def _feedback_adj(issue: dict, fb_map: dict, platform: str) -> int:
    itype    = issue.get('check') or issue.get('issue_type') or issue.get('type', '')
    verdicts = fb_map.get((itype, platform), [])
    if not verdicts:
        return 0
    fp   = sum(1 for v in verdicts if v == 'FALSE_POSITIVE')
    conf = sum(1 for v in verdicts if v == 'CONFIRMED')
    return max(-50, min(20, conf * 10 - fp * 15))


# ── Per-issue scoring ──────────────────────────────────────────────────────────

def _score_issue(issue: dict, pw_map: dict, fb_map: dict, platform: str) -> int:
    if issue.get('ai_dismissed'):
        return 15

    rs  = _rule_sig(issue)
    ps  = _pw_sig(issue, pw_map)
    as_ = _ai_sig(issue)

    base = _SCORES.get((rs, ps, as_), -1)
    if base == -1:
        # No signal — preserve existing confidence (capped at 65)
        base = min(65, max(20, issue.get('confidence') or 40))

    adj   = _feedback_adj(issue, fb_map, platform)
    score = max(0, min(99, base + adj))

    # Translation uncertainty: similarity in the "probably same" range → reduce
    sim = issue.get('similarity_score')
    if sim is not None and 0.70 <= float(sim) <= 0.85 and score < 65:
        score = max(0, score - 15)

    return score


def _tier(score: int) -> str:
    if score >= 95: return TIER_CONFIRMED
    if score >= 65: return TIER_LIKELY
    if score >= 35: return TIER_REVIEW
    return TIER_SUPPRESSED


def _category(issue: dict) -> str:
    rg = issue.get('rule_group')
    if rg:
        return _GROUP_CAT.get(int(rg), 'Routing')
    chk = issue.get('check') or issue.get('issue_type') or issue.get('type', '')
    return _CHECK_CAT.get(chk, 'Routing')


def _suppress_reason(issue: dict, score: int) -> str:
    if issue.get('ai_dismissed'):
        return 'AI Judge: False Positive'
    sim = issue.get('similarity_score')
    if sim is not None and 0.70 <= float(sim) <= 0.85:
        return f'Translation uncertainty ({float(sim):.0%} similarity)'
    return f'Low confidence ({score}%)'


# ── Main entry point ───────────────────────────────────────────────────────────

def run_consensus(
    issues: list,
    rule_findings: list,
    term_results: list,
    playwright_tests: dict,
    feedback_history: list,
    platform: str = '',
) -> dict:
    """
    Enrich all issues and rule_findings in-place with:
      - consensus_score (int 0-99)
      - consensus_tier  (CONFIRMED_BUG | LIKELY_BUG | NEEDS_REVIEW | SUPPRESSED)

    Returns a summary dict (does NOT copy issues):
      health_score       int 0-100
      health_by_category {category: score}
      confirmed_count    int
      likely_count       int
      review_count       int
      suppressed_count   int
      suppressed_reasons {reason: count}
    """
    pw_map = _build_pw_map(term_results, playwright_tests)

    # Build feedback lookup: {(issue_type, platform): [verdicts]}
    fb_map: dict = {}
    for fb in (feedback_history or []):
        k = (fb.get('issue_type', ''), fb.get('platform', ''))
        fb_map.setdefault(k, []).append(fb.get('user_verdict', ''))

    # Enrich both lists in-place
    all_items = list(issues) + list(rule_findings)
    for iss in all_items:
        sc = _score_issue(iss, pw_map, fb_map, platform)
        iss['consensus_score'] = sc
        iss['consensus_tier']  = _tier(sc)

    # Tally counts and compute health
    health            = 100.0
    cat_deductions: dict[str, float] = {c: 0.0 for c in CATEGORIES}
    confirmed_count = likely_count = review_count = suppressed_count = 0
    suppress_reasons: dict[str, int] = {}

    for iss in all_items:
        t   = iss['consensus_tier']
        sev = iss.get('severity', 'MEDIUM')

        if t == TIER_SUPPRESSED:
            suppressed_count += 1
            reason = _suppress_reason(iss, iss['consensus_score'])
            suppress_reasons[reason] = suppress_reasons.get(reason, 0) + 1
            continue

        if   t == TIER_CONFIRMED: confirmed_count += 1
        elif t == TIER_LIKELY:    likely_count    += 1
        elif t == TIER_REVIEW:    review_count    += 1

        ded = _DEDUCTIONS.get((t, sev), 0.0)
        health -= ded

        cat = _category(iss)
        if cat in cat_deductions:
            cat_deductions[cat] += ded

    health = max(0, round(health))

    # Per-category: amplify deductions so individual categories degrade faster
    by_cat = {c: max(0, round(100 - cat_deductions[c] * 5)) for c in CATEGORIES}

    return {
        'health_score':       health,
        'health_by_category': by_cat,
        'confirmed_count':    confirmed_count,
        'likely_count':       likely_count,
        'review_count':       review_count,
        'suppressed_count':   suppressed_count,
        'suppressed_reasons': suppress_reasons,
    }
