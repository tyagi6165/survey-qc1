"""
canonical_model.py — Unified survey question representation.

Every source (Doc, XML, Live) converts into CanonicalQuestion before
comparison logic runs. Converters live here; comparison logic is a
separate future migration.

Formats understood by the converters
-------------------------------------
Doc   : dict  {qid: {"text", "options", "is_mandatory", "has_piping",
                      "termination_rules", "is_numeric", "question_type"}}
XML   : list  [{"qid", "qid_normalized", "text", "type", "options",
                 "routing", "termination", "is_matrix_group"}]
Live  : dict  {qid: {"text", "options", "has_mandatory_marker",
                      "has_raw_piping", "raw_piping_found",
                      "has_inputs", "status"}}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── QID normalisation ────────────────────────────────────────────────────────

def _norm_qid(raw: str) -> str:
    """Uppercase, replace dots with X, strip surrounding whitespace."""
    return re.sub(r"\.", "X", raw.strip()).upper()


# ── Type normalisation ───────────────────────────────────────────────────────

# Canonical type vocabulary
TYPES = {"SINGLE", "MULTIPLE", "OPEN", "NUMERIC", "GRID", "RANK", "UNKNOWN"}

# xml_parser.py uses "UNIQUE"/"MATRIX" — map to canonical names
_XML_TYPE = {
    "UNIQUE":   "SINGLE",
    "SINGLE":   "SINGLE",
    "MULTIPLE": "MULTIPLE",
    "OPEN":     "OPEN",
    "NUMERIC":  "NUMERIC",
    "MATRIX":   "GRID",
    "GRID":     "GRID",
    "RANK":     "RANK",
    "UNKNOWN":  "UNKNOWN",
}

# Doc question_type strings are already close to canonical;
# is_numeric=True overrides blank question_type
_DOC_TYPE = {
    "SINGLE":   "SINGLE",
    "MULTIPLE": "MULTIPLE",
    "OPEN":     "OPEN",
    "NUMERIC":  "NUMERIC",
    "RANK":     "RANK",
    "GRID":     "GRID",
    "MATRIX":   "GRID",
    # variants seen in the wild
    "CLOSE":    "SINGLE",
    "CLOSED":   "SINGLE",
    "UNIQUE":   "SINGLE",
}


def _resolve_type(raw: str, is_numeric: bool = False) -> str:
    if is_numeric:
        return "NUMERIC"
    mapped = _DOC_TYPE.get(raw.strip().upper(), "UNKNOWN")
    return mapped if mapped in TYPES else "UNKNOWN"


# ── Sub-dataclasses ──────────────────────────────────────────────────────────

@dataclass
class CanonicalOption:
    text: str
    code: Optional[str] = None

    def to_dict(self) -> dict:
        return {"code": self.code, "text": self.text}


@dataclass
class CanonicalTermRule:
    """One termination rule extracted from the spec doc."""
    test_qid: str
    answer_codes: List[str] = field(default_factory=list)
    raw: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "test_qid": self.test_qid,
            "answer_codes": self.answer_codes,
            "raw": self.raw,
            "source": self.source,
        }


@dataclass
class CanonicalRouting:
    """Display condition / skip logic for a question."""
    show_if: Optional[str] = None   # e.g. "Q4 != 1"
    skip_to: Optional[str] = None   # target QID when condition met

    def to_dict(self) -> dict:
        return {"show_if": self.show_if, "skip_to": self.skip_to}


@dataclass
class CanonicalValidation:
    """Numeric or cross-question validation rule."""
    rule: Optional[str] = None       # e.g. "Q13 <= Q12"
    error_msg: Optional[str] = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "error_msg": self.error_msg}


# ── Main dataclass ────────────────────────────────────────────────────────────

@dataclass
class CanonicalQuestion:
    qid: str
    qid_normalized: str

    # Content
    text: str = ""
    type: str = "UNKNOWN"
    options: List[CanonicalOption] = field(default_factory=list)

    # Logic fields
    routing: Optional[CanonicalRouting] = None
    validation: Optional[CanonicalValidation] = None
    termination_rules: List[CanonicalTermRule] = field(default_factory=list)

    # Metadata
    source: str = ""        # "doc" | "xml" | "live"
    confidence: int = 0     # 0-100: how reliably the question was parsed
    is_mandatory: bool = False
    has_piping: bool = False
    language: str = ""

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "qid_normalized": self.qid_normalized,
            "text": self.text,
            "type": self.type,
            "options": [o.to_dict() for o in self.options],
            "routing": self.routing.to_dict() if self.routing else None,
            "validation": self.validation.to_dict() if self.validation else None,
            "termination_rules": [r.to_dict() for r in self.termination_rules],
            "source": self.source,
            "confidence": self.confidence,
            "is_mandatory": self.is_mandatory,
            "has_piping": self.has_piping,
            "language": self.language,
        }


# ── Confidence heuristic ─────────────────────────────────────────────────────

def _doc_confidence(text: str, opts: list, q_type: str) -> int:
    """Estimate parse confidence for a doc question (0-100)."""
    if not text.strip():
        return 25
    open_type = q_type in ("OPEN", "NUMERIC", "RANK")
    has_opts = bool(opts)
    if open_type:
        # Open questions legitimately have no options
        return 90 if len(text.strip()) > 10 else 70
    if has_opts and len(text.strip()) > 10:
        return 95
    if has_opts:
        return 80
    if len(text.strip()) > 20:
        # Text present but no options for a closed question → partial parse
        return 60
    return 40


def _xml_confidence(text: str, q_type: str, opts: list) -> int:
    """Estimate parse confidence for an XML question."""
    if not text.strip() and q_type == "UNKNOWN":
        return 30
    open_type = q_type in ("OPEN", "NUMERIC", "RANK")
    if open_type:
        return 90
    if text.strip() and opts:
        return 95
    if text.strip():
        return 75
    return 50


def _live_confidence(text: str, status: str) -> int:
    """Estimate parse confidence for a live-crawled question."""
    if status != "OK":
        return 20
    if not text.strip():
        return 35
    return 90 if len(text.strip()) > 20 else 70


# ── Converters ───────────────────────────────────────────────────────────────

def doc_to_canonical(doc_questions: dict) -> List[CanonicalQuestion]:
    """
    Convert the doc parser output to a list of CanonicalQuestion.

    doc_questions: dict keyed by QID, each value is:
        {"text", "options", "is_mandatory", "has_piping",
         "termination_rules", "is_numeric", "question_type"}
    """
    result = []
    for qid, v in doc_questions.items():
        q_type = _resolve_type(
            v.get("question_type", ""),
            is_numeric=bool(v.get("is_numeric"))
        )

        opts = [
            CanonicalOption(
                text=o.get("text", ""),
                code=o.get("code") or None,
            )
            for o in (v.get("options") or [])
            if o.get("text", "").strip()
        ]

        term_rules = [
            CanonicalTermRule(
                test_qid=r.get("test_qid", qid),
                answer_codes=r.get("answer_codes", []),
                raw=r.get("raw", ""),
                source=r.get("source", ""),
            )
            for r in (v.get("termination_rules") or [])
        ]

        text = (v.get("text") or "").strip()
        confidence = _doc_confidence(text, opts, q_type)

        result.append(CanonicalQuestion(
            qid=qid,
            qid_normalized=_norm_qid(qid),
            text=text,
            type=q_type,
            options=opts,
            termination_rules=term_rules,
            source="doc",
            confidence=confidence,
            is_mandatory=bool(v.get("is_mandatory")),
            has_piping=bool(v.get("has_piping")),
        ))

    return result


def xml_to_canonical(xml_questions: list) -> List[CanonicalQuestion]:
    """
    Convert xml_parser.py output list to CanonicalQuestion list.

    Each element:
        {"qid", "qid_normalized", "text", "type", "options",
         "routing", "termination", "is_matrix_group"}
    """
    result = []
    for v in xml_questions:
        qid = v.get("qid", "")
        if not qid:
            continue

        q_type = _XML_TYPE.get((v.get("type") or "UNKNOWN").upper(), "UNKNOWN")

        opts = [
            CanonicalOption(
                text=o.get("text", ""),
                code=o.get("code") or None,
            )
            for o in (v.get("options") or [])
            if o.get("text", "").strip()
        ]

        routing_str = v.get("routing") or None
        routing = CanonicalRouting(show_if=routing_str) if routing_str else None

        # XML termination is a single string, not a parsed rule list
        term_raw = v.get("termination") or ""
        term_rules = (
            [CanonicalTermRule(test_qid=qid, raw=term_raw, source="xml")]
            if term_raw.strip() else []
        )

        text = (v.get("text") or "").strip()
        confidence = _xml_confidence(text, q_type, opts)

        result.append(CanonicalQuestion(
            qid=qid,
            qid_normalized=v.get("qid_normalized") or _norm_qid(qid),
            text=text,
            type=q_type,
            options=opts,
            routing=routing,
            termination_rules=term_rules,
            source="xml",
            confidence=confidence,
        ))

    return result


def live_to_canonical(live_data: dict) -> List[CanonicalQuestion]:
    """
    Convert the live crawler output to CanonicalQuestion list.

    live_data: dict keyed by QID, each value is:
        {"text", "options", "has_mandatory_marker", "has_raw_piping",
         "raw_piping_found", "has_inputs", "status"}
    """
    result = []
    for qid, v in live_data.items():
        status = v.get("status", "OK")

        opts = [
            CanonicalOption(text=o.get("text", ""))
            for o in (v.get("options") or [])
            if o.get("text", "").strip()
        ]

        text = (v.get("text") or "").strip()
        confidence = _live_confidence(text, status)

        result.append(CanonicalQuestion(
            qid=qid,
            qid_normalized=_norm_qid(qid),
            text=text,
            type="UNKNOWN",   # live crawler doesn't know question type
            options=opts,
            source="live",
            confidence=confidence,
            is_mandatory=bool(v.get("has_mandatory_marker")),
            has_piping=bool(v.get("has_raw_piping")),
        ))

    return result


# ── Lookup helpers ────────────────────────────────────────────────────────────

def index_by_qid(questions: List[CanonicalQuestion]) -> dict:
    """Return {qid_normalized: CanonicalQuestion} for fast lookup."""
    return {q.qid_normalized: q for q in questions}
