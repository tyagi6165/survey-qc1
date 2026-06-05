"""
export_validator.py — Data export schema validation.

Validates that a survey's data export (variable names, types, codes) matches
the survey definition. Catches export bugs before data collection begins.

Rules R031–R038 — new rule set, no overlap with rule_engine.py (R001–R008).

Rule IDs:
  R031  Variable in survey but MISSING from export         → HIGH
  R032  Variable in export but NOT in survey               → MEDIUM
  R033  Variable type mismatch (SINGLE vs MULTIPLE)        → HIGH
  R034  Answer code in survey missing from export columns  → HIGH
  R035  Export column code not in survey codes             → MEDIUM
  R036  Open-end field has no export variable              → HIGH
  R037  Piping variable used in text but absent from export → HIGH
  R038  Loop variable naming convention mismatch           → MEDIUM

Input:
  doc_questions  — dict from qc_engine.parse_document()
  export_input   — string: CSV header line, TSV, or whitespace-separated names
  xml_questions  — optional list from xml_parser.parse_export()

Output:
  {"issues": [...], "summary": {...}, "expected_schema": [...]}
"""

from __future__ import annotations

import re
import csv
import io
from dataclasses import dataclass, field
from typing import Optional


# ─── Export type vocabulary ───────────────────────────────────────────────────
# SINGLE_CODE   → one variable per question, stores numeric code
# MULTIPLE_CODE → one variable per answer code (0/1 per column)
# OPEN_TEXT     → one variable per question, stores text
# NUMERIC       → one variable per question, stores number
# DATE          → one variable per question, stores date string
# UNKNOWN       → type could not be determined

_EXPORT_TYPE_PATTERNS = [
    # Most specific first
    (re.compile(r'\bRANK\b',                              re.I), "NUMERIC"),
    (re.compile(r'\bPER\s+(?:ROW|LINE)\b|\bMATRIX\b',   re.I), "SINGLE_CODE"),  # grid row
    (re.compile(r'\bMULTIPL[EY]\b|\bMULTI\b|\b\bMA\b',  re.I), "MULTIPLE_CODE"),
    (re.compile(r'\bUNIQUE\b|\bSINGLE\b|\bCLOSED?\b|\bSA\b', re.I), "SINGLE_CODE"),
    (re.compile(r'\bOPEN\s+NUMERIC\b',                   re.I), "NUMERIC"),
    (re.compile(r'\bNUMERIC\b|\bNUMBER\b|\bINTEGER\b|\bFLOAT\b|\bSCALE\b', re.I), "NUMERIC"),
    (re.compile(r'\bOPEN\b|\bTEXT\b|\bVERBATIM\b|\b\bOE\b', re.I), "OPEN_TEXT"),
    (re.compile(r'\bDATE\b|\bDATETIME\b',                re.I), "DATE"),
]

# Canonical type names from canonical_model.py mapped to export types
_CANONICAL_TO_EXPORT = {
    "SINGLE":   "SINGLE_CODE",
    "MULTIPLE": "MULTIPLE_CODE",
    "OPEN":     "OPEN_TEXT",
    "NUMERIC":  "NUMERIC",
    "GRID":     "SINGLE_CODE",   # grid row = single code per row variable
    "RANK":     "NUMERIC",
    "UNKNOWN":  "UNKNOWN",
}

# Variable name suffixes that indicate multi-code or matrix columns
_SUFFIX_RE = re.compile(r'^(.+?)[\._x](\d+)$', re.IGNORECASE)

# Piping variable pattern: [VARNAME] in question text
_PIPING_VAR_RE = re.compile(r'\[([A-Z][A-Z0-9_]{1,30})\]')

# QID pattern used by the doc
_QID_NORM_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_.]*$')


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ExpectedVariable:
    """One variable we expect to find in the export, derived from the survey."""
    name: str                       # primary expected name, e.g. "Q14_3"
    alt_names: list[str] = field(default_factory=list)  # acceptable alternatives
    export_type: str = "UNKNOWN"    # SINGLE_CODE / MULTIPLE_CODE / OPEN_TEXT / NUMERIC / DATE
    source_qid: str = ""            # which survey question produced this
    code: Optional[str] = None      # for MULTIPLE_CODE: the specific code this var represents
    is_loop_var: bool = False
    is_piping_var: bool = False

    def all_names(self) -> list[str]:
        return [self.name] + self.alt_names


@dataclass
class ExportVariable:
    """One variable parsed from the actual export headers."""
    name: str
    inferred_type: str = "UNKNOWN"  # inferred from name pattern or sample data
    base_qid: str = ""              # stripped base: "Q14" from "Q14_3"
    suffix: Optional[str] = None   # "3" from "Q14_3"


# ─── Type normalizer ─────────────────────────────────────────────────────────

def _normalize_type(raw_type: str, is_numeric: bool = False) -> str:
    """Map a free-text doc type string to an export type label."""
    if is_numeric:
        return "NUMERIC"
    raw = (raw_type or "").strip()
    if not raw:
        return "UNKNOWN"
    # Try canonical mapping first (fast path)
    upper = raw.upper()
    if upper in _CANONICAL_TO_EXPORT:
        return _CANONICAL_TO_EXPORT[upper]
    # Fall through to pattern matching for messy strings
    for pattern, export_type in _EXPORT_TYPE_PATTERNS:
        if pattern.search(raw):
            return export_type
    return "UNKNOWN"


def _qid_upper(qid: str) -> str:
    """Normalise QID to uppercase for comparison."""
    return (qid or "").strip().upper().replace(".", "X")


# ─── Export CSV / header parser ───────────────────────────────────────────────

def parse_export_headers(export_input: str) -> list[ExportVariable]:
    """
    Parse export variable names from a CSV header line, TSV, or newline/
    space-separated list. Optionally uses the first data row for type inference.

    Returns a list of ExportVariable with inferred types and base QIDs.
    """
    if not export_input or not export_input.strip():
        return []

    text = export_input.strip()
    headers: list[str] = []

    # ── Detect separator and extract header names ──────────────────────────
    # Try CSV / TSV with the built-in reader
    for sep in (",", "\t", ";"):
        if sep in text.split("\n")[0]:
            try:
                reader = csv.reader(io.StringIO(text), delimiter=sep)
                rows = list(reader)
                if rows:
                    headers = [h.strip().strip('"').strip("'") for h in rows[0] if h.strip()]
                # If there's a second row of data, we could infer types — kept for future
                break
            except Exception:
                continue

    # Fallback: whitespace / newline separated
    if not headers:
        headers = [h.strip() for h in re.split(r'[\n\r,\t\s]+', text) if h.strip()]

    if not headers:
        return []

    # ── Build ExportVariable objects ─────────────────────────────────────
    result: list[ExportVariable] = []
    for name in headers:
        if not name or not _QID_NORM_RE.match(name):
            continue  # skip empty / obviously non-QID names like "9", "---"

        ev = ExportVariable(name=name)

        # Try to split base_qid + suffix (Q14_3, Q14x3, Q14.3)
        m = _SUFFIX_RE.match(name)
        if m:
            ev.base_qid = m.group(1).upper()
            ev.suffix = m.group(2)
            ev.inferred_type = "MULTIPLE_CODE"  # tentative; confirmed by survey type
        else:
            ev.base_qid = name.upper()
            ev.inferred_type = "UNKNOWN"

        result.append(ev)

    return result


# ─── Expected schema builder ──────────────────────────────────────────────────

def build_expected_schema(
    doc_questions: dict,
    xml_questions: Optional[list] = None,
) -> list[ExpectedVariable]:
    """
    Generate the list of export variables we expect to see, based on the
    survey definition.  Works from doc_questions dict (always available)
    and optionally from xml_questions for richer type/code information.

    Variable naming rules:
      SINGLE_CODE / OPEN_TEXT / NUMERIC → one variable = QID
      MULTIPLE_CODE                     → one variable per code: QID_code
      GRID (SINGLE_CODE per row)        → inferred as SINGLE_CODE; one var = QID
                                          (row variables only if options present)
    """
    # Build XML index for type/option enrichment
    xml_index: dict[str, dict] = {}
    if xml_questions:
        from canonical_model import _norm_qid
        for xq in xml_questions:
            nqid = (xq.get("qid_normalized") or _norm_qid(xq.get("qid", ""))).upper()
            xml_index[nqid] = xq

    expected: list[ExpectedVariable] = []
    piping_vars_seen: set[str] = set()

    for qid, q in doc_questions.items():
        qid_up = _qid_upper(qid)
        raw_type = q.get("question_type", "")
        is_numeric = bool(q.get("is_numeric"))
        exp_type = _normalize_type(raw_type, is_numeric=is_numeric)

        # Enrich from XML if available
        xq = xml_index.get(qid_up)
        if xq:
            xml_type_raw = xq.get("type", "")
            xml_exp_type = _CANONICAL_TO_EXPORT.get(xml_type_raw.upper(), "UNKNOWN")
            if xml_exp_type != "UNKNOWN":
                exp_type = xml_exp_type  # XML is more reliable

        options = q.get("options", []) or []
        codes = [str(o.get("code", "")).strip() for o in options if o.get("code")]
        codes = [c for c in codes if c]  # remove empty

        # ── Detect loop QID (Q30x1, Q30x2 → base=Q30) ────────────────────
        loop_m = re.match(r'^([A-Za-z]+\d+)[Xx](\d+)$', qid)
        if loop_m:
            base = loop_m.group(1).upper()
            iteration = loop_m.group(2)
            # Primary: QID_N (Q30_1); alt: QIDxN (Q30x1), QID_00N (Q30_001)
            primary = f"{base}_{iteration}"
            alts = [qid.upper(), f"{base}_{iteration.zfill(3)}"]
            ev = ExpectedVariable(
                name=primary,
                alt_names=alts,
                export_type=exp_type if exp_type != "UNKNOWN" else "SINGLE_CODE",
                source_qid=qid,
                is_loop_var=True,
            )
            expected.append(ev)
            # Still collect piping vars from loop questions before continuing
            text = q.get("text", "") or ""
            for pipes in q.get("piping_found", []):
                pm = _PIPING_VAR_RE.search(pipes)
                if pm:
                    pvar = pm.group(1).upper()
                    if pvar not in piping_vars_seen:
                        piping_vars_seen.add(pvar)
                        expected.append(ExpectedVariable(
                            name=pvar, alt_names=[], export_type="SINGLE_CODE",
                            source_qid=qid, is_piping_var=True,
                        ))
            for pm in _PIPING_VAR_RE.finditer(text):
                pvar = pm.group(1).upper()
                if pvar not in piping_vars_seen:
                    piping_vars_seen.add(pvar)
                    expected.append(ExpectedVariable(
                        name=pvar, alt_names=[], export_type="SINGLE_CODE",
                        source_qid=qid, is_piping_var=True,
                    ))
            continue

        if exp_type == "MULTIPLE_CODE":
            if codes:
                # One column per answer code: QID_1, QID_2, ...
                for code in codes:
                    primary = f"{qid_up}_{code}"
                    alts = [f"{qid_up}_{code.zfill(2)}", f"{qid_up}_{code.zfill(3)}",
                            f"{qid_up}x{code}"]
                    expected.append(ExpectedVariable(
                        name=primary,
                        alt_names=alts,
                        export_type="MULTIPLE_CODE",
                        source_qid=qid,
                        code=code,
                    ))
            else:
                # No codes in spec — expect at least QID_1 (flag if totally absent)
                primary = f"{qid_up}_1"
                alts = [qid_up]
                expected.append(ExpectedVariable(
                    name=primary,
                    alt_names=alts,
                    export_type="MULTIPLE_CODE",
                    source_qid=qid,
                    code=None,
                ))

        elif exp_type == "OPEN_TEXT":
            # One text variable per question
            expected.append(ExpectedVariable(
                name=qid_up,
                alt_names=[f"{qid_up}_TEXT", f"{qid_up}_VERBATIM", f"{qid_up}_OE"],
                export_type="OPEN_TEXT",
                source_qid=qid,
            ))

        else:
            # SINGLE_CODE, NUMERIC, DATE, UNKNOWN → one variable
            expected.append(ExpectedVariable(
                name=qid_up,
                alt_names=[],
                export_type=exp_type if exp_type != "UNKNOWN" else "SINGLE_CODE",
                source_qid=qid,
            ))

        # ── Collect piping variables ───────────────────────────────────────
        text = q.get("text", "") or ""
        for pipes in q.get("piping_found", []):
            m = _PIPING_VAR_RE.search(pipes)
            if m:
                pvar = m.group(1).upper()
                if pvar not in piping_vars_seen:
                    piping_vars_seen.add(pvar)
                    expected.append(ExpectedVariable(
                        name=pvar,
                        alt_names=[],
                        export_type="SINGLE_CODE",
                        source_qid=qid,
                        is_piping_var=True,
                    ))
        # Also scan question text directly for [VARNAME] patterns
        for m in _PIPING_VAR_RE.finditer(text):
            pvar = m.group(1).upper()
            if pvar not in piping_vars_seen:
                piping_vars_seen.add(pvar)
                expected.append(ExpectedVariable(
                    name=pvar,
                    alt_names=[],
                    export_type="SINGLE_CODE",
                    source_qid=qid,
                    is_piping_var=True,
                ))

    return expected


# ─── Issue builder ────────────────────────────────────────────────────────────

def _issue(qid: str, variable_name: str, rule_id: str, severity: str,
           evidence: str, confidence: int) -> dict:
    return {
        "qid":           qid,
        "variable_name": variable_name,
        "check":         rule_id,
        "type":          rule_id,          # app.py compatibility
        "severity":      severity,
        "details":       evidence,
        "evidence":      evidence,
        "confidence":    confidence,
        "rule":          rule_id,
    }


# ─── Validation checks ────────────────────────────────────────────────────────

def _run_checks(
    expected: list[ExpectedVariable],
    actual: list[ExportVariable],
) -> list[dict]:
    issues = []

    # Build lookup structures
    actual_names_upper: set[str] = {ev.name.upper() for ev in actual}
    actual_by_base: dict[str, list[ExportVariable]] = {}
    for ev in actual:
        actual_by_base.setdefault(ev.base_qid.upper(), []).append(ev)

    expected_sources: set[str] = {
        _qid_upper(e.source_qid) for e in expected if not e.is_piping_var
    }
    expected_names_upper: set[str] = set()
    for e in expected:
        expected_names_upper.update(n.upper() for n in e.all_names())

    # ── Collect piping vars separately ────────────────────────────────────
    expected_piping: list[ExpectedVariable] = [e for e in expected if e.is_piping_var]
    expected_non_piping: list[ExpectedVariable] = [e for e in expected if not e.is_piping_var]

    # ── Group expected by source_qid for per-question logic ───────────────
    by_qid: dict[str, list[ExpectedVariable]] = {}
    for e in expected_non_piping:
        qid_up = _qid_upper(e.source_qid)
        by_qid.setdefault(qid_up, []).append(e)

    # ── R031 / R033 / R034 / R036: per expected variable ──────────────────
    for qid_up, evars in by_qid.items():
        first = evars[0]
        qtype = first.export_type

        # Check whether ANY of this question's expected variables appear in export
        any_found = any(
            n.upper() in actual_names_upper
            for e in evars for n in e.all_names()
        )

        if not any_found:
            # R036: open-end variable defined but no export column
            if qtype == "OPEN_TEXT":
                issues.append(_issue(
                    qid=first.source_qid,
                    variable_name=first.name,
                    rule_id="R036",
                    severity="HIGH",
                    evidence=(
                        f"Open-end question {first.source_qid} has no export column "
                        f"(expected: {first.name})"
                    ),
                    confidence=85,
                ))
            else:
                # R031: variable missing entirely from export
                issues.append(_issue(
                    qid=first.source_qid,
                    variable_name=first.name,
                    rule_id="R031",
                    severity="HIGH",
                    evidence=(
                        f"Survey variable {first.source_qid} not found in export "
                        f"(expected: {first.name}, type: {qtype})"
                    ),
                    confidence=88,
                ))
            continue

        # Check for type mismatch (R033)
        # Export has multi-code columns but survey says SINGLE, or vice versa
        actual_cols_for_qid = actual_by_base.get(qid_up, [])
        has_suffixed_cols = bool(actual_cols_for_qid and
                                 any(ev.suffix for ev in actual_cols_for_qid))

        if qtype == "SINGLE_CODE" and has_suffixed_cols:
            suffix_names = [ev.name for ev in actual_cols_for_qid if ev.suffix][:5]
            issues.append(_issue(
                qid=first.source_qid,
                variable_name=first.name,
                rule_id="R033",
                severity="HIGH",
                evidence=(
                    f"{first.source_qid} is SINGLE_CODE in spec but export has "
                    f"multiple columns: {suffix_names}"
                ),
                confidence=80,
            ))

        elif qtype == "MULTIPLE_CODE" and not has_suffixed_cols:
            # Survey says MULTIPLE but export only has bare QID column
            bare_only = (
                qid_up in actual_names_upper and
                not any(ev.suffix for ev in actual_by_base.get(qid_up, []))
            )
            if bare_only:
                issues.append(_issue(
                    qid=first.source_qid,
                    variable_name=first.name,
                    rule_id="R033",
                    severity="HIGH",
                    evidence=(
                        f"{first.source_qid} is MULTIPLE_CODE in spec but export has "
                        f"only a single column '{qid_up}' (expected one per code)"
                    ),
                    confidence=78,
                ))

        # R034 / R035: code-level checks (only for MULTIPLE_CODE)
        if qtype == "MULTIPLE_CODE":
            expected_codes = {e.code for e in evars if e.code}
            actual_codes_for_qid: set[str] = set()
            for ev in actual_by_base.get(qid_up, []):
                if ev.suffix:
                    actual_codes_for_qid.add(ev.suffix.lstrip("0") or "0")

            if expected_codes and actual_codes_for_qid:
                # R034: codes in spec but not in export
                missing = expected_codes - actual_codes_for_qid
                if missing:
                    issues.append(_issue(
                        qid=first.source_qid,
                        variable_name=first.name,
                        rule_id="R034",
                        severity="HIGH",
                        evidence=(
                            f"Answer code(s) defined in spec but missing as export columns "
                            f"for {first.source_qid}: {sorted(missing, key=lambda x: int(x) if x.isdigit() else 0)}"
                        ),
                        confidence=85,
                    ))
                # R035: codes in export but not in spec
                extra = actual_codes_for_qid - expected_codes
                if extra:
                    issues.append(_issue(
                        qid=first.source_qid,
                        variable_name=first.name,
                        rule_id="R035",
                        severity="MEDIUM",
                        evidence=(
                            f"Export has extra code column(s) not in spec "
                            f"for {first.source_qid}: {sorted(extra, key=lambda x: int(x) if x.isdigit() else 0)}"
                        ),
                        confidence=75,
                    ))

    # ── R032: Export variables not matched to any survey question ──────────
    for ev in actual:
        name_up = ev.name.upper()
        base_up = ev.base_qid.upper()

        # Skip system columns that are never in the survey definition
        if name_up in {"RESPONDENT_ID", "RESP_ID", "ID", "RECORD", "SERIALNUMBER",
                        "INTERVIEW_ID", "INTERVIEWID", "START_TIME", "END_TIME",
                        "DURATION", "STATUS", "COMPLETES", "WEIGHT", "COUNTRY",
                        "LANGUAGE", "LANG", "MODE", "PANEL"}:
            continue

        # Is this variable explained by any expected name or by being a suffix of a known QID?
        matched = (
            name_up in expected_names_upper
            or base_up in expected_sources
            or name_up in {_qid_upper(e.source_qid) for e in expected}
        )

        if not matched:
            issues.append(_issue(
                qid=base_up,
                variable_name=ev.name,
                rule_id="R032",
                severity="MEDIUM",
                evidence=(
                    f"Export column '{ev.name}' has no matching question in survey spec "
                    f"(may be a system variable, derived variable, or wrong name)"
                ),
                confidence=72,
            ))

    # ── R037: Piping variables used in text but absent from export ─────────
    for ep in expected_piping:
        if ep.name.upper() not in actual_names_upper:
            issues.append(_issue(
                qid=ep.source_qid,
                variable_name=ep.name,
                rule_id="R037",
                severity="HIGH",
                evidence=(
                    f"Piping variable [{ep.name}] used in question {ep.source_qid} "
                    f"but no column '{ep.name}' found in export"
                ),
                confidence=82,
            ))

    # ── R038: Loop variable naming convention inconsistency ────────────────
    # Detect when survey uses QIDxN but export uses QID_N or vice versa
    survey_loop_bases: dict[str, list[ExpectedVariable]] = {}
    for e in expected_non_piping:
        if e.is_loop_var:
            base_m = re.match(r'^([A-Za-z]+\d+)[_x]?\d+$', e.source_qid, re.I)
            if base_m:
                base = base_m.group(1).upper()
                survey_loop_bases.setdefault(base, []).append(e)

    for base, loop_evars in survey_loop_bases.items():
        # Survey uses xN notation (Q30x1, Q30x2)
        # Export should use base_N or base_N — check what's actually there
        export_for_base = actual_by_base.get(base, [])
        if not export_for_base:
            continue  # covered by R031

        # Check separator consistency
        survey_seps = set()
        for ev in loop_evars:
            m = re.search(r'[_x](\d+)$', ev.source_qid, re.I)
            if m:
                sep = ev.source_qid[m.start()].lower()
                survey_seps.add(sep)

        export_seps = set()
        for ev in export_for_base:
            if ev.suffix:
                col_sep_m = re.search(r'([_x])\d+$', ev.name, re.I)
                if col_sep_m:
                    export_seps.add(col_sep_m.group(1).lower())

        if survey_seps and export_seps and survey_seps != export_seps:
            issues.append(_issue(
                qid=base,
                variable_name=f"{base}_N",
                rule_id="R038",
                severity="MEDIUM",
                evidence=(
                    f"Loop {base}: survey uses '{list(survey_seps)[0]}' separator "
                    f"(e.g. {loop_evars[0].source_qid}) but export uses "
                    f"'{list(export_seps)[0]}' separator "
                    f"(e.g. {export_for_base[0].name})"
                ),
                confidence=80,
            ))

    return issues


# ─── Top-level function ───────────────────────────────────────────────────────

def run_export_validation(
    doc_questions: dict,
    export_input: str,
    xml_questions: Optional[list] = None,
) -> dict:
    """
    Full export schema validation.

    Args:
        doc_questions:  dict from qc_engine.parse_document()["questions"]
        export_input:   CSV header line, TSV, or whitespace-separated variable names
        xml_questions:  optional list from xml_parser.parse_export() for enrichment

    Returns:
        {
          "issues":          list[dict],   # R031–R038 issues
          "expected_schema": list[dict],   # what we expected in the export
          "actual_schema":   list[dict],   # what we found in the export
          "summary": {
            "expected_vars": int,
            "actual_vars":   int,
            "issues_found":  int,
            "high":          int,
            "medium":        int,
          }
        }
    """
    expected = build_expected_schema(doc_questions, xml_questions=xml_questions)
    actual   = parse_export_headers(export_input)
    issues   = _run_checks(expected, actual)

    high   = sum(1 for i in issues if i["severity"] == "HIGH")
    medium = sum(1 for i in issues if i["severity"] == "MEDIUM")

    return {
        "issues": issues,
        "expected_schema": [
            {
                "name":        e.name,
                "alt_names":   e.alt_names,
                "export_type": e.export_type,
                "source_qid":  e.source_qid,
                "is_loop":     e.is_loop_var,
                "is_piping":   e.is_piping_var,
            }
            for e in expected
        ],
        "actual_schema": [
            {
                "name":          ev.name,
                "base_qid":      ev.base_qid,
                "inferred_type": ev.inferred_type,
                "suffix":        ev.suffix,
            }
            for ev in actual
        ],
        "summary": {
            "expected_vars": len(expected),
            "actual_vars":   len(actual),
            "issues_found":  len(issues),
            "high":          high,
            "medium":        medium,
        },
    }
