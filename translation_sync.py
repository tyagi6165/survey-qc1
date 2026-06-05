"""
translation_sync.py — Cross-language semantic verification for survey QC.

Detects when doc and live survey texts are in different languages and
verifies that meaning is preserved.  Uses the multilingual embedding model
already loaded by embeddings.py — no extra models needed.

The underlying model (paraphrase-multilingual-MiniLM-L12-v2) projects all
languages into a shared vector space, so "Quel âge avez-vous?" and "What is
your age?" produce similar vectors regardless of language.

Verdicts
--------
  TRANSLATION_MATCH     score ≥ 0.85 — same meaning; suppress false-positive
                                        text-mismatch issues
  TRANSLATION_UNCERTAIN 0.60 ≤ score < 0.85 — meaning drift; needs manual review
  TRANSLATION_MISMATCH  score < 0.60 — content differs; real bug

Public API
----------
  detect_language(text)                              -> str
  check_translation_sync(doc_questions,
                         live_questions,
                         text_match_issues=None)     -> (list[dict], list[dict])
"""

from __future__ import annotations
import re
from typing import Optional

# ── Thresholds (mirror embeddings.py constants) ───────────────────────────────
_MATCH     = 0.85   # TRANSLATION_MATCH
_UNCERTAIN = 0.60   # boundary between UNCERTAIN and MISMATCH

# ── Language detection ────────────────────────────────────────────────────────

try:
    from langdetect import detect as _ld_detect
    _LANGDETECT_OK = True
except ImportError:
    _LANGDETECT_OK = False

# Stopword sets for heuristic fallback — covers the five most common survey
# languages.  Intentionally small so the heuristic is fast and portable.
_STOPWORDS: dict[str, frozenset] = {
    'fr': frozenset({
        'le','la','les','des','est','et','en','ou','un','une','que','qui',
        'de','du','au','aux','ce','se','mais','pas','ne','pour','sur','par',
        'avec','dans','je','vous','nous','ils','elle',
    }),
    'de': frozenset({
        'der','die','das','und','ist','in','von','zu','den','mit','ein',
        'eine','auch','bei','an','auf','sich','oder','werden','sind','nicht',
        'sie','ich','wir','ihr','es','er',
    }),
    'it': frozenset({
        'il','lo','la','gli','le','un','una','di','da','del','della','che',
        'con','per','non','si','come','sono','questo','questa','ma','mi',
        'ci','ho','ha','sei','lui','lei',
    }),
    'es': frozenset({
        'el','la','los','las','de','en','y','es','que','por','para','con',
        'no','una','se','del','su','al','un','sus','lo','hay','esta','este',
    }),
    'en': frozenset({
        'the','and','is','are','was','were','of','in','to','for','a','an',
        'with','that','this','it','be','have','has','from','at','by','not',
        'you','we','they','do','did','will','i','me','my',
    }),
}

_WORD_RE = re.compile(
    r'\b[a-zàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿœß]+\b',
    re.IGNORECASE,
)


def _heuristic_lang(text: str) -> str:
    """
    Stopword-frequency fallback.  Returns a 2-letter code or 'unknown'.
    Requires at least 2 matching stopwords to commit to a language.
    """
    words = {w.lower() for w in _WORD_RE.findall(text[:300])}
    scores = {lang: len(words & sw) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.__getitem__)
    return best if scores[best] >= 2 else 'unknown'


def detect_language(text: str) -> str:
    """
    Return the 2-letter BCP-47 language code for text, or 'unknown'.

    Uses langdetect when available (50+ languages, probabilistic).
    Falls back to a stopword heuristic covering EN/FR/DE/IT/ES.
    Returns 'unknown' for texts shorter than 10 characters.
    """
    clean = (text or '').strip()
    if len(clean) < 10:
        return 'unknown'

    if _LANGDETECT_OK:
        try:
            return _ld_detect(clean[:400])
        except Exception:
            pass   # langdetect can raise LangDetectException on very short/garbled text

    return _heuristic_lang(clean)


# ── Semantic similarity (lazy import to avoid circular dep) ───────────────────

def _sem_sim(a: str, b: str) -> float:
    try:
        from embeddings import semantic_similarity
        return semantic_similarity(a, b)
    except Exception:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a[:300].lower(), b[:300].lower()).ratio()


# ── Issue builder ─────────────────────────────────────────────────────────────

def _make_issue(qid: str, doc_lang: str, live_lang: str, score: float,
                verdict: str, doc_snippet: str, live_snippet: str) -> dict:
    sev = 'HIGH' if verdict == 'TRANSLATION_MISMATCH' else 'MEDIUM'
    conf = 90 if verdict == 'TRANSLATION_MISMATCH' else 65
    detail = (
        f'Doc ({doc_lang.upper()}) vs Live ({live_lang.upper()}): '
        f'semantic score {score:.0%} — {verdict.replace("_", " ").title()}'
    )
    evidence = (
        f'Doc ({doc_lang}): "{doc_snippet[:80]}"  ·  '
        f'Live ({live_lang}): "{live_snippet[:80]}"  ·  '
        f'Score: {score:.0%}'
    )
    return {
        'qid':                qid,
        'doc_lang':           doc_lang,
        'live_lang':          live_lang,
        'score':              round(score, 3),
        'verdict':            verdict,
        'check':              verdict,
        'type':               verdict.replace('_', ' '),
        'severity':           sev,
        'details':            detail,
        'evidence':           evidence,
        'doc_snippet':        doc_snippet[:120],
        'live_snippet':       live_snippet[:120],
        'confidence':         conf,
        'is_translation_issue': True,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def check_translation_sync(
    doc_questions: dict,
    live_questions: dict,
    text_match_issues: Optional[list] = None,
) -> tuple:
    """
    For every doc/live pair where the detected languages differ, compute
    semantic similarity and classify the result.

    Args:
        doc_questions:      {qid: {"text": str, ...}}
        live_questions:     {qid: {"text": str, ...}}
        text_match_issues:  existing TEXT_MISMATCH / TEXT_POSSIBLE_MISMATCH
                            issues from check_question_text().  Issues for QIDs
                            that score TRANSLATION_MATCH are removed — they are
                            valid translations, not programming bugs.

    Returns:
        (translation_issues, filtered_text_issues)

        translation_issues:    TRANSLATION_UNCERTAIN + TRANSLATION_MISMATCH dicts.
        filtered_text_issues:  text_match_issues with TRANSLATION_MATCH false
                               positives removed.
    """
    translation_issues: list = []
    matched_qids: set  = set()   # QIDs confirmed as valid translations

    for qid, doc_q in doc_questions.items():
        if qid not in live_questions:
            continue

        doc_text  = (doc_q.get('text') or '').strip()
        live_text = (live_questions[qid].get('text') or '').strip()

        # Skip blanks and very short texts — no meaningful comparison possible
        if len(doc_text) < 10 or len(live_text) < 10:
            continue

        doc_lang  = detect_language(doc_text)
        live_lang = detect_language(live_text)

        # Only process pairs where the languages are confidently different
        if (doc_lang == live_lang
                or doc_lang  == 'unknown'
                or live_lang == 'unknown'):
            continue

        try:
            score = _sem_sim(doc_text[:500], live_text[:500])
        except Exception:
            continue

        if score >= _MATCH:
            # Valid translation — flag QID so the false-positive is removed
            matched_qids.add(qid)
            continue

        verdict = (
            'TRANSLATION_UNCERTAIN'
            if score >= _UNCERTAIN
            else 'TRANSLATION_MISMATCH'
        )
        translation_issues.append(
            _make_issue(qid, doc_lang, live_lang, score, verdict,
                        doc_text[:120], live_text[:120])
        )

    # Filter existing text-match issues: remove ones resolved as translations
    if text_match_issues is not None and matched_qids:
        filtered = [
            iss for iss in text_match_issues
            if iss.get('qid') not in matched_qids
        ]
    else:
        filtered = list(text_match_issues or [])

    return translation_issues, filtered
