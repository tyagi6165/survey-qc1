"""
rule_engine.py — Deterministic survey QC rules.

Layer 1 of the 3-layer decision stack:
  L1  RuleEngine (this file)  — 100% deterministic, no AI, no randomness
  L2  Semantic engine         — embeddings.py (same model = same score)
  L3  AI Judge                — future Item #22, only for truly uncertain cases

Design guarantees:
  - Same input  →  same output, every time
  - No network calls, no model inference, no randomness
  - Audit-defensible: every issue carries a rule ID (R001–R008)
  - Works for any language, any platform, any survey type
"""

import re


# ─── Type normalisation table ─────────────────────────────────────────────────
# Maps platform-specific type names → canonical type labels.
# Add more aliases as new platforms are encountered.
_TYPE_MAP = {
    "UNIQUE":   {"SINGLE", "RADIO", "SINGLE_CHOICE", "SINGLECHOICE", "SA"},
    "MULTIPLE": {"MULTI", "CHECKBOX", "MULTIPLE_CHOICE", "MULTIPLECHOICE", "MA"},
    "NUMERIC":  {"NUMBER", "INTEGER", "FLOAT", "DECIMAL", "SCALE"},
    "OPEN":     {"TEXT", "OPEN_ENDED", "OPENENDED", "VERBATIM", "OE", "STRING"},
    "GRID":     {"MATRIX", "TABLE", "RATING", "LIKERT"},
    "RANK":     {"RANKING", "ORDER", "RANKED"},
}


def _canonical_type(raw: str) -> str:
    """Return canonical type label or the original string if unrecognised."""
    t = (raw or "").strip().upper()
    for canonical, aliases in _TYPE_MAP.items():
        if t == canonical or t in aliases:
            return canonical
    return t


# ─── Termination keyword patterns (multilingual) ──────────────────────────────
_TERM_PATTERNS = re.compile(
    r'\b('
    r'thank|thanks|thank\s+you|merci|grazie|gracias|danke|bedankt|obrigado|'
    r'close|fermer|chiudi|cerrar|schlie[ßs]en|'
    r'screen\s*out|screened\s*out|screenout|'
    r'terminate|terminer|terminare|terminar|beenden|'
    r'quota\s*full|quota\s+complet'
    r')\b',
    re.IGNORECASE,
)


class RuleEngine:
    """
    100% deterministic survey QC rules.
    No AI. No randomness. Same input = same output always.
    Audit-defensible for pharma/healthcare clients.
    """

    # ── R001 ─────────────────────────────────────────────────────────────────
    def check_question_missing(self, doc_q, live_q):
        """
        Rule R001: Question present in spec but absent from live = MISSING.
        Deterministic — no ambiguity.
        """
        if doc_q is not None and live_q is None:
            return {
                "result": "FAIL",
                "type": "QUESTION_MISSING",
                "confidence": 95,
                "reason": "Question exists in spec but absent from live survey",
                "rule": "R001",
            }
        return {"result": "PASS", "rule": "R001"}

    # ── R002 ─────────────────────────────────────────────────────────────────
    def check_question_extra(self, doc_q, live_q):
        """
        Rule R002: Question in live but absent from spec = EXTRA.
        Reported as WARN (may be a framework/intro page, not always a bug).
        """
        if live_q is not None and doc_q is None:
            return {
                "result": "WARN",
                "type": "QUESTION_EXTRA",
                "confidence": 70,
                "reason": "Question present in live survey but not found in spec",
                "rule": "R002",
            }
        return {"result": "PASS", "rule": "R002"}

    # ── R003 ─────────────────────────────────────────────────────────────────
    def check_question_type(self, doc_q, live_q):
        """
        Rule R003: Question type (UNIQUE / MULTIPLE / NUMERIC / OPEN / GRID)
        must match between spec and live.  UNIQUE vs MULTIPLE is a hard bug.
        SKIP when either side has no type data (live crawlers rarely expose type).
        """
        if not doc_q or not live_q:
            return {"result": "SKIP", "rule": "R003"}

        doc_type = _canonical_type(doc_q.get("question_type", ""))
        live_type = _canonical_type(live_q.get("question_type", ""))

        if not doc_type or not live_type:
            return {"result": "SKIP", "rule": "R003"}

        if doc_type != live_type:
            return {
                "result": "FAIL",
                "type": "QUESTION_TYPE_MISMATCH",
                "confidence": 90,
                "reason": f"Spec type: {doc_type}, Live type: {live_type}",
                "rule": "R003",
            }
        return {"result": "PASS", "rule": "R003"}

    # ── R004 ─────────────────────────────────────────────────────────────────
    def check_mandatory(self, doc_q, live_q):
        """
        Rule R004: If spec marks a question mandatory, the live survey must
        show the mandatory marker (* or equivalent).
        Field names: doc uses 'is_mandatory', live uses 'has_mandatory_marker'.
        """
        if not doc_q or not live_q:
            return {"result": "SKIP", "rule": "R004"}

        # Support both field name conventions
        doc_mand = (
            doc_q.get("is_mandatory") or
            doc_q.get("mandatory", False)
        )
        live_mand = (
            live_q.get("has_mandatory_marker") or
            live_q.get("mandatory", False)
        )

        if doc_mand and not live_mand:
            return {
                "result": "FAIL",
                "type": "MANDATORY_MISSING",
                "confidence": 88,
                "reason": "Question marked mandatory in spec but no * marker in live",
                "rule": "R004",
            }
        return {"result": "PASS", "rule": "R004"}

    # ── R005 ─────────────────────────────────────────────────────────────────
    def check_option_count(self, doc_q, live_q):
        """
        Rule R005: Option count must not drop between spec and live.
        Zero live options when spec has options = definite bug.
        Fewer live options = likely missing options.
        More live options = SKIP (extra options are not flagged here).
        """
        if not doc_q or not live_q:
            return {"result": "SKIP", "rule": "R005"}

        doc_opts = doc_q.get("options", [])
        live_opts = live_q.get("options", [])

        if not doc_opts:
            return {"result": "SKIP", "rule": "R005"}

        doc_count = len(doc_opts)
        live_count = len(live_opts)

        if live_count == 0:
            return {
                "result": "FAIL",
                "type": "ALL_OPTIONS_MISSING",
                "confidence": 95,
                "reason": f"Spec has {doc_count} option(s), live has 0",
                "rule": "R005",
            }

        if doc_count > live_count:
            missing = doc_count - live_count
            return {
                "result": "FAIL",
                "type": "OPTIONS_COUNT_MISMATCH",
                "confidence": 85,
                "reason": (
                    f"{missing} option(s) missing from live "
                    f"(spec: {doc_count}, live: {live_count})"
                ),
                "rule": "R005",
            }

        return {"result": "PASS", "rule": "R005"}

    # ── R006 ─────────────────────────────────────────────────────────────────
    def check_option_codes(self, doc_q, live_q):
        """
        Rule R006: Answer codes in spec must all be present in live.
        SKIP when live options have no code field (many crawlers don't expose codes).
        Wrong codes = data collection error — patients coded in wrong bucket.
        """
        if not doc_q or not live_q:
            return {"result": "SKIP", "rule": "R006"}

        doc_codes = {
            str(o.get("code", "")).strip()
            for o in doc_q.get("options", [])
            if o.get("code") is not None and str(o.get("code", "")).strip()
        }
        live_codes = {
            str(o.get("code", "")).strip()
            for o in live_q.get("options", [])
            if o.get("code") is not None and str(o.get("code", "")).strip()
        }

        if not doc_codes:
            return {"result": "SKIP", "rule": "R006"}

        # Live crawler doesn't expose codes → can't compare
        if not live_codes:
            return {"result": "SKIP", "rule": "R006"}

        missing_codes = doc_codes - live_codes
        if missing_codes:
            return {
                "result": "FAIL",
                "type": "ANSWER_CODES_MISSING",
                "confidence": 90,
                "reason": f"Answer code(s) in spec but missing in live: {sorted(missing_codes)}",
                "rule": "R006",
            }

        return {"result": "PASS", "rule": "R006"}

    # ── R007 ─────────────────────────────────────────────────────────────────
    def check_termination(self, doc_term, live_tested):
        """
        Rule R007: Deterministic termination check — looks for termination
        keywords in the LOGIC table flat text rather than asking AI.
        doc_term: dict with 'flat_text' from a LOGIC table
        live_tested: True if the rule was verified in live, False/None otherwise
        """
        if not doc_term:
            return {"result": "SKIP", "rule": "R007"}

        flat = doc_term.get("flat_text", "")
        has_term_keyword = bool(_TERM_PATTERNS.search(flat))

        if has_term_keyword and not live_tested:
            return {
                "result": "WARN",
                "type": "TERMINATION_UNVERIFIED",
                "confidence": 75,
                "reason": "Termination keywords found in LOGIC table — verify in live survey",
                "rule": "R007",
            }

        return {"result": "PASS", "rule": "R007"}

    # ── R008 ─────────────────────────────────────────────────────────────────
    def check_question_order(self, doc_order, live_order):
        """
        Rule R008: Relative question order must be preserved between spec and live.
        Checks all consecutive pairs in the doc ordering that also appear in live.
        """
        if not doc_order or not live_order:
            return {"result": "SKIP", "rule": "R008"}

        common = [q for q in doc_order if q in live_order]
        if len(common) < 3:
            return {"result": "SKIP", "rule": "R008"}

        live_pos = {q: i for i, q in enumerate(live_order)}

        out_of_order = []
        for i in range(len(common) - 1):
            q1, q2 = common[i], common[i + 1]
            if live_pos[q1] > live_pos[q2]:
                out_of_order.append((q1, q2))

        if out_of_order:
            pairs_str = ", ".join(f"{a}→{b}" for a, b in out_of_order[:3])
            return {
                "result": "WARN",
                "type": "QUESTION_ORDER_MISMATCH",
                "confidence": 75,
                "reason": (
                    f"{len(out_of_order)} ordering violation(s): {pairs_str}"
                ),
                "rule": "R008",
            }

        return {"result": "PASS", "rule": "R008"}

    # ── Per-pair runner ───────────────────────────────────────────────────────
    def run_all_checks(self, doc_q, live_q, xml_q=None):
        """
        Run all per-question deterministic checks (R001–R006) on a QID pair.
        R007 and R008 operate at survey level — call them via their methods directly.
        Returns list of {result, type, confidence, reason, rule} dicts for FAIL/WARN only.
        """
        issues = []
        for check in (
            self.check_question_missing,
            self.check_question_extra,
            self.check_question_type,
            self.check_mandatory,
            self.check_option_count,
            self.check_option_codes,
        ):
            result = check(doc_q, live_q)
            if result.get("result") in ("FAIL", "WARN"):
                issues.append(result)
        return issues
