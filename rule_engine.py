"""
rule_engine.py — Comprehensive deterministic survey QC rule engine.

Layer 1 of the 3-layer decision stack:
  L1  RuleEngine (this file)  — 100% deterministic, no AI, no randomness
  L2  Semantic engine         — embeddings.py
  L3  AI Judge                — ai_judge.py, only for genuinely uncertain cases

10 Rule Groups:
  G1  ROUTING ENGINE
  G2  TERMINATION ENGINE
  G3  MANDATORY ENGINE
  G4  PIPING ENGINE
  G5  LOOP ENGINE
  G6  VARIABLE ENGINE
  G7  QUESTION TYPE ENGINE
  G8  OPTION/CODE ENGINE
  G9  SURVEY GRAPH ENGINE
  G10 EXPORT ENGINE

Public entry point:
    run_rule_engine(doc_questions, xml_questions=None) -> dict
    {results: list[dict], summary: dict, duration_ms: int}

Design guarantees:
  - Same input → same output, every time
  - No network calls, no model inference, no randomness
  - Never raises exceptions — all errors caught per-group
  - Uses sets for O(1) QID lookup; indexes built once
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)

# ── Optional dependency imports with fallbacks ────────────────────────────────

try:
    from piping_validator import extract_pipe_vars
    _HAVE_PIPING = True
except ImportError:
    log.warning("piping_validator not available — G4 will be limited")
    _HAVE_PIPING = False
    def extract_pipe_vars(text: str) -> list:  # type: ignore
        # Minimal fallback: detect [QID] and {VAR} patterns
        hits = []
        for m in re.finditer(r'\[([A-Za-z][A-Za-z0-9_]{0,30})\]|\{([A-Za-z][A-Za-z0-9_]{0,30})\}', text):
            raw = m.group(0)
            var = m.group(1) or m.group(2)
            hits.append({'raw': raw, 'platform_hint': 'generic', 'var_name': var})
        return hits

try:
    from loop_detector import build_loop_map
    _HAVE_LOOP = True
except ImportError:
    log.warning("loop_detector not available — G5 will be limited")
    _HAVE_LOOP = False
    def build_loop_map(all_qids) -> dict:  # type: ignore
        return {}

try:
    from survey_graph import build_xml_graph, build_doc_graph, compare_graphs
    import networkx as nx
    _HAVE_GRAPH = True
except ImportError:
    log.warning("survey_graph/networkx not available — G9 will use fallback")
    _HAVE_GRAPH = False
    build_xml_graph = None  # type: ignore
    build_doc_graph = None  # type: ignore
    compare_graphs = None  # type: ignore
    nx = None  # type: ignore

try:
    from qid_normalizer import should_skip_qid, normalize_qid
    _HAVE_NORM = True
except ImportError:
    log.warning("qid_normalizer not available — using basic QID filter")
    _HAVE_NORM = False
    def should_skip_qid(qid: str) -> bool:  # type: ignore
        return not bool(re.match(r'^[A-Za-z]{1,8}\d', qid or ''))
    def normalize_qid(qid: str) -> str:  # type: ignore
        return re.sub(r'[^A-Z0-9]', '', str(qid or '').upper())


# ── RuleResult dataclass ──────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule_id: str
    rule_group: int
    rule_group_name: str
    qid: str
    severity: str          # HIGH | MEDIUM | LOW | INFO
    confidence: int        # 0-100
    issue_type: str
    evidence: str
    recommendation: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d['rule_type'] = d['issue_type']   # canonical output alias
        return d


# ── Internal helpers ──────────────────────────────────────────────────────────

# Regex to extract QID-like tokens from routing/condition strings
_QID_REF_PAT = re.compile(
    r'\b([A-Z]{1,8}\d+(?:[A-Z]\d*|bis|ter|_\d+|x\d+|\.\d+)?'
    r'|SPE|BMO\d*|SCREEN)\b',
    re.IGNORECASE,
)

# Regex to extract numeric codes from termination strings
_CODE_PAT = re.compile(r'\b(\d+)\b')

# Regex to detect QID-in-pipe-bracket [Q1], [S2], [R3x1] etc.
_PIPE_QID_PAT = re.compile(r'^\[([A-Za-z][A-Za-z0-9_]{0,20}\d[A-Za-z0-9_]*)\]$')

# Type canonicalisation maps
_DOC_TYPE_CANON = {
    'UNIQUE': 'SINGLE', 'SINGLE': 'SINGLE', 'CLOSE': 'SINGLE',
    'CLOSED': 'SINGLE', 'SA': 'SINGLE', 'RADIO': 'SINGLE',
    'MULTIPLE': 'MULTIPLE', 'MULTI': 'MULTIPLE', 'MA': 'MULTIPLE',
    'CHECKBOX': 'MULTIPLE',
    'OPEN': 'OPEN', 'VERBATIM': 'OPEN', 'OE': 'OPEN', 'TEXT': 'OPEN',
    'OPEN_ENDED': 'OPEN', 'STRING': 'OPEN',
    'NUMERIC': 'NUMERIC', 'NUMBER': 'NUMERIC', 'INTEGER': 'NUMERIC',
    'FLOAT': 'NUMERIC', 'SCALE': 'NUMERIC',
    'GRID': 'GRID', 'MATRIX': 'GRID', 'TABLE': 'GRID',
    'RANK': 'RANK', 'RANKING': 'RANK',
}

_XML_TYPE_CANON = {
    'UNIQUE': 'SINGLE', 'SINGLE': 'SINGLE',
    'MULTIPLE': 'MULTIPLE',
    'OPEN': 'OPEN',
    'NUMERIC': 'NUMERIC',
    'MATRIX': 'GRID', 'GRID': 'GRID',
    'RANK': 'RANK',
    'UNKNOWN': 'UNKNOWN',
}

# Invalid QID characters for export
_INVALID_QID_CHARS_PAT = re.compile(r'[\s\-\.\/\\@#$%^&*()+={}\[\]|;:\'",<>?]')

# Mandatory text markers in multiple languages
_MANDATORY_TEXT_PAT = re.compile(
    r'\*\s*$'
    r'|\bmandatory\b'
    r'|\bobligation\b'
    r'|\bobbligatorio\b'
    r'|\bobligatoire\b'
    r'|\berforderlich\b'
    r'|\bobligatorio\b'
    r'|\bobligatorisk\b',
    re.IGNORECASE | re.MULTILINE,
)


def _canon_doc_type(raw: str) -> str:
    return _DOC_TYPE_CANON.get((raw or '').strip().upper(), 'UNKNOWN')


def _canon_xml_type(raw: str) -> str:
    return _XML_TYPE_CANON.get((raw or '').strip().upper(), 'UNKNOWN')


def _is_numeric_doc_question(q: dict) -> bool:
    raw = (q or {}).get('question_type') or ''
    return bool((q or {}).get('is_numeric') or _canon_doc_type(raw) == 'NUMERIC')


def _xml_has_categorical_options(q: dict) -> bool:
    opts = (q or {}).get('options') or []
    return any(str(o.get('text', '')).strip() for o in opts if isinstance(o, dict))


def _is_static_info_or_legal_text(text: str) -> bool:
    blob = (text or '').lower()
    return any(k in blob for k in (
        'traitement des donnees a caractere personnel',
        'traitement des données à caractère personnel',
        'loi bertrand', 'privacy', 'legal', 'consent', 'static info',
        'information page',
    ))


def _has_strong_termination_signal(text: str) -> bool:
    blob = (text or '').lower()
    if _is_static_info_or_legal_text(blob):
        return False
    return any(k in blob for k in (
        'terminate', 'termination', 'screenout', 'screen out', 'disqualify',
        'complete interview', 'end interview', 'stop interview',
        'goto end', 'go to end', 'thanks', 'status set', 'set status',
        'complete/respondent', 'end/thanks', 'hard terminate',
        'screenedset', 'setinterviewend', 'screenedredirect',
    ))


def _qid_term_aliases(qid: str) -> Set[str]:
    q = normalize_qid(qid)
    out = {q}
    if q.endswith('BIS'):
        out.add(q[:-3])
    else:
        out.add(q + 'BIS')
    out.add(re.sub(r'(?<=\d)[A-Z]$', '', q))
    # Grid children used by Confirmit for parent rows/columns.
    if re.match(r'^[A-Z]{1,8}\d+$', q):
        for suffix in ('A', 'B', 'C'):
            out.add(q + suffix)
    return {x for x in out if x}


def _termination_refers_to_qid(text: str, qid: str) -> bool:
    if not text or not qid:
        return False
    refs = {normalize_qid(r) for r in re.findall(
        r'\b(?:f|qf|ReturnNum|gridGetRowsByScale|EqualTo)\s*\(\s*[\'"]([A-Za-z]{1,8}\d+[A-Za-z]*(?:_[0-9]+)?)',
        text,
        re.I,
    )}
    return bool(refs & _qid_term_aliases(qid))


def _xml_term_records_for_qid(qid: str, xml_index: dict) -> List[dict]:
    records: List[dict] = []
    aliases = _qid_term_aliases(qid)
    for xqid, xml_q in xml_index.items():
        term_str = (xml_q.get('termination') or '').strip()
        if _is_static_info_or_legal_text(term_str):
            term_str = ''
        evs = xml_q.get('termination_evidence') or []
        xaliases = _qid_term_aliases(xqid)
        relevant = bool(aliases & xaliases)
        if not relevant and term_str:
            relevant = _termination_refers_to_qid(term_str, qid)
        if not relevant:
            for ev in evs:
                if _termination_refers_to_qid(str(ev.get('evidence_text') or ev.get('xml_condition') or ''), qid):
                    relevant = True
                    break
        if not relevant:
            continue
        if term_str and (_has_strong_termination_signal(term_str) or int(xml_q.get('termination_confidence') or 0) >= 80):
            records.append({
                'xml_qid': xqid,
                'condition': term_str,
                'source_node': xml_q.get('termination_source_node', ''),
                'evidence_text': term_str,
                'confidence': int(xml_q.get('termination_confidence') or 75),
            })
        for ev in evs:
            etxt = str(ev.get('evidence_text') or ev.get('xml_condition') or '')
            if _is_static_info_or_legal_text(etxt):
                continue
            if not _has_strong_termination_signal(etxt) and int(ev.get('confidence') or 0) < 80:
                continue
            records.append({
                'xml_qid': xqid,
                'condition': str(ev.get('xml_condition') or etxt),
                'source_node': str(ev.get('xml_source_node') or xml_q.get('termination_source_node') or ''),
                'evidence_text': etxt,
                'confidence': int(ev.get('confidence') or xml_q.get('termination_confidence') or 75),
            })
    dedup = []
    seen = set()
    for rec in records:
        key = (rec.get('xml_qid'), rec.get('condition')[:160], rec.get('source_node'))
        if key not in seen:
            seen.add(key)
            dedup.append(rec)
    return dedup


def _is_compound_termination_row(row: dict) -> bool:
    raw_blob = ' '.join(row.get('doc_raw') or []).upper()
    xml_blob = (row.get('xml_condition') or '').upper()
    return any(k in raw_blob or k in xml_blob for k in (
        ' AND ', ' OR ', 'OTHERWISE', 'TOTAL', 'LESS THAN', 'GREATER THAN',
        '<', '>', '<=', '>=', 'ALL PRODUCT', 'ALL PRODUCTS',
        'GRIDGETROWSBYSCALE', 'RETURNNUM',
    ))


def _extract_qid_refs(text: str) -> List[str]:
    """Extract all QID-like references from a routing/condition string."""
    if not text:
        return []
    return [normalize_qid(m.group(0)) for m in _QID_REF_PAT.finditer(text)]


def _norm_qid(qid: str) -> str:
    """Canonical QID key shared with DOC/XML/live matching."""
    return normalize_qid(qid)


def _make_result(
    rule_id: str,
    rule_group: int,
    rule_group_name: str,
    qid: str,
    severity: str,
    confidence: int,
    issue_type: str,
    evidence: str,
    recommendation: str = '',
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        rule_group=rule_group,
        rule_group_name=rule_group_name,
        qid=qid,
        severity=severity,
        confidence=confidence,
        issue_type=issue_type,
        evidence=evidence,
        recommendation=recommendation,
    )


# ── Index builders ────────────────────────────────────────────────────────────

def _build_doc_index(doc_questions: dict) -> dict:
    """Return {normalized_qid: question_dict} for O(1) doc lookups."""
    return {normalize_qid(k): v for k, v in (doc_questions or {}).items() if normalize_qid(k)}


def _build_xml_index(xml_questions: list) -> dict:
    """Return {normalized_qid: question_dict} for O(1) xml lookups."""
    idx: dict = {}
    for q in (xml_questions or []):
        qid = normalize_qid(q.get('qid') or q.get('qid_normalized') or '')
        if qid:
            idx[qid] = q
    return idx


def _build_all_qids(doc_index: dict, xml_index: dict) -> Set[str]:
    """Union of all upper-case QIDs from both sources."""
    return set(doc_index.keys()) | set(xml_index.keys())


# ─────────────────────────────────────────────────────────────────────────────
# TERMINATION MATRIX BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_termination_matrix(doc_index: dict, xml_index: dict) -> List[dict]:
    """
    Build a structured Termination Matrix from doc + xml sources.

    Each row represents one QID that has termination in at least one source:
    {
        qid:            str
        doc_codes:      list[str]   — codes that trigger termination per doc
        doc_raw:        list[str]   — raw rule text(s) from spec doc
        xml_condition:  str|None    — raw termination string from XML
        xml_codes:      list[str]   — codes extracted from xml_condition
        doc_xml_match:  bool|None   — True when doc codes ⊆ xml codes and vice versa
        missing_in_xml: list[str]   — doc codes absent from xml termination
        extra_in_xml:   list[str]   — xml codes not in doc
        status:         str         — MATCH | MISSING_IN_XML | XML_NOT_PARSEABLE |
                                      NEEDS_REVIEW | COMPOUND_MANUAL_REVIEW
    }
    """
    matrix: Dict[str, dict] = {}

    # Gather doc-side termination
    for qid, doc_q in doc_index.items():
        rules = doc_q.get('termination_rules') or []
        if not rules:
            continue
        row = {
            'qid': qid,
            'doc_codes': [],
            'doc_raw': [],
            'xml_condition': None,
            'xml_codes': [],
            'doc_xml_match': None,
            'missing_in_xml': [],
            'extra_in_xml': [],
            'status': 'XML_NOT_PARSEABLE' if xml_index else 'NEEDS_REVIEW',
            'root_cause': 'XML_PARSER' if xml_index else 'REVIEW_REQUIRED',
            'recommendation': 'Verify termination logic in XML or review script-based routing.',
        }
        for rule in rules:
            codes = rule.get('answer_codes') or []
            row['doc_codes'].extend([str(c) for c in codes])
            raw = (rule.get('raw') or '').strip()
            if raw:
                row['doc_raw'].append(raw[:120])
        matrix[qid] = row

    # Attach deep/global XML termination evidence to DOC rows, including
    # aliases such as S1 -> S1bis and grid children such as S4 -> S4A/S4B.
    for qid, row in list(matrix.items()):
        records = _xml_term_records_for_qid(qid, xml_index)
        if not records:
            continue
        doc_code_set = set(row.get('doc_codes') or [])
        def _rec_score(rec):
            rec_codes = set(_CODE_PAT.findall((rec.get('condition') or '') + ' ' + (rec.get('evidence_text') or '')))
            score = int(rec.get('confidence') or 0)
            if doc_code_set and doc_code_set <= rec_codes:
                score += 100
            if 'hard terminate' in ((rec.get('condition') or '') + ' ' + (rec.get('evidence_text') or '')).lower():
                score += 10
            return score
        best = max(records, key=_rec_score)
        condition = best.get('condition') or best.get('evidence_text') or ''
        row['xml_condition'] = condition[:300]
        row['xml_source_node'] = best.get('source_node', '')
        row['evidence_text'] = best.get('evidence_text', '')[:500]
        row['confidence'] = best.get('confidence', 0)
        row['xml_codes'] = sorted(set(_CODE_PAT.findall(condition + ' ' + row.get('evidence_text', ''))))
        doc_set = set(row['doc_codes'])
        xml_set = set(row['xml_codes'])
        row['missing_in_xml'] = sorted(doc_set - xml_set)
        row['extra_in_xml'] = sorted(xml_set - doc_set)
        if _is_compound_termination_row(row):
            row['status'] = 'COMPOUND_MANUAL_REVIEW'
            row['root_cause'] = 'REVIEW_REQUIRED'
            row['recommendation'] = 'XML termination evidence found, but condition is compound/grid/numeric and needs manual review.'
        elif doc_set and not row['missing_in_xml']:
            row['status'] = 'MATCH'
            row['doc_xml_match'] = True
            row['root_cause'] = 'REVIEW_REQUIRED'
            row['recommendation'] = 'DOC termination code is represented in XML termination logic.'
        else:
            row['status'] = 'NEEDS_REVIEW'
            row['root_cause'] = 'REVIEW_REQUIRED'
            row['recommendation'] = 'XML termination evidence found but code mapping is unclear.'

    # Gather xml-side termination
    for qid, xml_q in xml_index.items():
        term_str = (xml_q.get('termination') or '').strip()
        if _is_static_info_or_legal_text(term_str):
            term_str = ''
        if term_str and not _has_strong_termination_signal(term_str):
            term_str = ''
        if not term_str:
            continue
        xml_codes = sorted(set(_CODE_PAT.findall(term_str)))
        if qid in matrix:
            row = matrix[qid]
            if row.get('xml_condition') and row.get('status') in ('MATCH', 'COMPOUND_MANUAL_REVIEW', 'NEEDS_REVIEW'):
                continue
            row['xml_condition'] = term_str[:200]
            row['xml_codes'] = xml_codes
            doc_set = set(row['doc_codes'])
            xml_set = set(xml_codes)
            row['missing_in_xml'] = sorted(doc_set - xml_set)
            row['extra_in_xml'] = sorted(xml_set - doc_set)
            match = (not row['missing_in_xml'] and not row['extra_in_xml'])
            row['doc_xml_match'] = match
            if match:
                row['status'] = 'MATCH'
                row['root_cause'] = 'REVIEW_REQUIRED'
                row['recommendation'] = 'DOC termination condition is represented in XML.'
            else:
                if any(c in ('?', '') for c in row['doc_codes']) or any(k in ' '.join(row['doc_raw']).upper() for k in (' AND ', ' OR ', 'OTHERWISE', 'TOTAL')):
                    row['status'] = 'COMPOUND_MANUAL_REVIEW'
                    row['root_cause'] = 'REVIEW_REQUIRED'
                    row['recommendation'] = 'Compound termination logic requires manual review against XML script/routing.'
                else:
                    row['status'] = 'NEEDS_REVIEW'
                    row['root_cause'] = 'REVIEW_REQUIRED'
                    row['recommendation'] = 'Termination evidence exists but does not clearly map to the DOC code.'
        else:
            if any(_qid_term_aliases(qid) & _qid_term_aliases(doc_qid) for doc_qid in matrix.keys()):
                continue
            matrix[qid] = {
                'qid': qid,
                'doc_codes': [],
                'doc_raw': [],
                'xml_condition': term_str[:200],
                'xml_codes': xml_codes,
                'doc_xml_match': None,
                'missing_in_xml': [],
                'extra_in_xml': [],
                'status': 'NEEDS_REVIEW',
                'root_cause': 'REVIEW_REQUIRED',
                'recommendation': 'XML contains termination evidence not found in uploaded DOC scope; confirm intent.',
            }

    for row in matrix.values():
        if row.get('status') in ('MISSING_IN_XML', 'XML_NOT_PARSEABLE'):
            raw_blob = ' '.join(row.get('doc_raw') or []).upper()
            if any(k in raw_blob for k in (' AND ', ' OR ', 'OTHERWISE', 'TOTAL', 'LESS THAN', 'GREATER THAN')):
                row['status'] = 'COMPOUND_MANUAL_REVIEW'
                row['root_cause'] = 'REVIEW_REQUIRED'
                row['recommendation'] = 'Compound termination logic needs manual review; not a live failure.'
            elif row.get('xml_condition') and not row.get('xml_codes'):
                row['status'] = 'XML_NOT_PARSEABLE'
                row['root_cause'] = 'XML_PARSER'
                row['recommendation'] = 'XML logic appears script-based/complex; review source script.'

    _order = {'MISSING_IN_XML': 0, 'XML_NOT_PARSEABLE': 1, 'COMPOUND_MANUAL_REVIEW': 2, 'NEEDS_REVIEW': 3, 'MATCH': 4}
    return sorted(matrix.values(), key=lambda r: (_order.get(r['status'], 9), r['qid']))


# ─────────────────────────────────────────────────────────────────────────────
# G1 — ROUTING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g1_routing(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G1: Routing destination validation, broken references, circular routing."""
    results: List[RuleResult] = []
    GROUP = 1
    NAME = 'ROUTING ENGINE'
    PREFIX = 'G1'

    # Build xml routing map: {qid: routing_string}
    xml_routing: Dict[str, str] = {}
    for qid, q in xml_index.items():
        rt = q.get('routing') or ''
        if rt.strip():
            xml_routing[qid] = rt.strip()

    # Build simple adjacency for circular detection: {qid: set of referenced qids}
    adjacency: Dict[str, Set[str]] = {}
    for qid, rt in xml_routing.items():
        refs = {r.upper() for r in _extract_qid_refs(rt) if r.upper() in all_qids}
        if refs:
            adjacency[qid] = refs

    # R1.1 — INVALID_ROUTING_DESTINATION: xml routing references unknown QID
    for qid, rt in xml_routing.items():
        refs = _extract_qid_refs(rt)
        for ref in refs:
            rref = ref.upper()
            if rref not in all_qids and not should_skip_qid(ref):
                results.append(_make_result(
                    f'{PREFIX}01', GROUP, NAME, qid,
                    'HIGH', 88, 'INVALID_ROUTING_DESTINATION',
                    f'Routing string "{rt[:80]}" references QID "{ref}" which is not in the survey.',
                    f'Verify QID "{ref}" exists or correct the routing expression.',
                ))

    # R1.2 — BROKEN_ROUTING_REFERENCE: doc termination rule references non-existent test_qid
    for qid, doc_q in doc_index.items():
        term_rules = doc_q.get('termination_rules') or []
        for rule in term_rules:
            test_qid = (rule.get('test_qid') or '').upper()
            if test_qid and test_qid != qid and test_qid not in all_qids:
                if not should_skip_qid(test_qid):
                    results.append(_make_result(
                        f'{PREFIX}02', GROUP, NAME, qid,
                        'MEDIUM', 78, 'BROKEN_ROUTING_REFERENCE',
                        f'Doc termination rule at {qid} references test_qid "{test_qid}" '
                        f'which does not exist in the survey.',
                        f'Confirm "{test_qid}" is a valid QID or update the termination rule.',
                    ))

    # R1.3 — CIRCULAR_ROUTING: Q1 routes to Q2 which routes back to Q1
    # Simple cycle detection: if qid A refs B, and B refs A
    visited_pairs: Set[tuple] = set()
    for qid_a, refs_a in adjacency.items():
        for qid_b in refs_a:
            if qid_b in adjacency and qid_a in adjacency[qid_b]:
                pair = tuple(sorted([qid_a, qid_b]))
                if pair not in visited_pairs:
                    visited_pairs.add(pair)
                    results.append(_make_result(
                        f'{PREFIX}03', GROUP, NAME, qid_a,
                        'MEDIUM', 72, 'CIRCULAR_ROUTING',
                        f'Potential circular routing: {qid_a} → {qid_b} → {qid_a}.',
                        'Review routing conditions to ensure no infinite loops.',
                    ))

    # R1.4 — doc has termination_rules with test_qid routing but xml has no routing field
    for qid, doc_q in doc_index.items():
        term_rules = doc_q.get('termination_rules') or []
        for rule in term_rules:
            test_qid = (rule.get('test_qid') or '').upper()
            if test_qid and test_qid in xml_index:
                xml_q = xml_index[test_qid]
                if not (xml_q.get('routing') or '').strip():
                    results.append(_make_result(
                        f'{PREFIX}04', GROUP, NAME, qid,
                        'MEDIUM', 65, 'ROUTING_DOC_XML_MISMATCH',
                        f'Doc has termination rule at {qid} referencing {test_qid}, '
                        f'but XML question {test_qid} has no routing field.',
                        f'Check if routing/display condition should be set on {test_qid} in the XML.',
                    ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G2 — TERMINATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g2_termination(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G2: Termination alignment between doc and xml."""
    results: List[RuleResult] = []
    GROUP = 2
    NAME = 'TERMINATION ENGINE'
    PREFIX = 'G2'

    # Build sets of QIDs with termination in each source
    doc_term_qids: Set[str] = set()
    for qid, doc_q in doc_index.items():
        if doc_q.get('termination_rules'):
            doc_term_qids.add(qid)

    xml_term_qids: Set[str] = set()
    xml_term_map: Dict[str, str] = {}
    for qid, xml_q in xml_index.items():
        term_str = (xml_q.get('termination') or '').strip()
        if _is_static_info_or_legal_text(term_str) or not _has_strong_termination_signal(term_str):
            term_str = ''
        if term_str:
            xml_term_qids.add(qid)
            xml_term_map[qid] = term_str

    # R2.1 — TERMINATION_MISSING_IN_XML: doc has termination but xml doesn't
    for qid in sorted(doc_term_qids):
        if qid in xml_index and qid not in xml_term_qids:
            rules = doc_index[qid].get('termination_rules') or []
            codes = [str(c) for r in rules for c in (r.get('answer_codes') or [])]
            raw_blob = ' '.join((r.get('raw') or '') for r in rules).upper()
            is_compound = any(k in raw_blob for k in (' AND ', ' OR ', 'OTHERWISE', 'TOTAL', 'LESS THAN', 'GREATER THAN'))
            results.append(_make_result(
                f'{PREFIX}01', GROUP, NAME, qid,
                'LOW' if is_compound else 'INFO', 45, 'TERMINATION_XML_NOT_PARSEABLE',
                f'Doc defines termination at {qid} (codes: {codes or "unknown"}) '
                f'but XML termination evidence was not strongly parsed for this question.',
                'Review XML script/routing source; do not treat as missing unless parser proves absence.',
            ))

    # R2.2 — TERMINATION_EXTRA_IN_XML: xml has termination but doc doesn't
    for qid in sorted(xml_term_qids):
        if qid in doc_index and qid not in doc_term_qids:
            results.append(_make_result(
                f'{PREFIX}02', GROUP, NAME, qid,
                'MEDIUM', 72, 'TERMINATION_EXTRA_IN_XML',
                f'XML has termination at {qid} ("{xml_term_map[qid][:60]}") '
                f'but doc has no termination_rules for this question.',
                'Confirm this termination is intentional or remove from XML.',
            ))

    # R2.3 — CODE_MISMATCH_IN_TERMINATION: doc answer codes not in xml term string
    for qid in sorted(doc_term_qids & xml_term_qids):
        doc_q = doc_index[qid]
        term_str = xml_term_map.get(qid, '')
        xml_code_strings = set(_CODE_PAT.findall(term_str))
        rules = doc_q.get('termination_rules') or []
        for rule in rules:
            for code in (rule.get('answer_codes') or []):
                code_s = str(code)
                if code_s not in xml_code_strings:
                    results.append(_make_result(
                        f'{PREFIX}03', GROUP, NAME, qid,
                        'HIGH', 75, 'CODE_MISMATCH_IN_TERMINATION',
                        f'Doc says terminate when {qid}={code_s}, '
                        f'but code "{code_s}" not found in XML termination '
                        f'string: "{term_str[:80]}".',
                        f'Ensure XML termination includes code {code_s} or update doc.',
                    ))

    # R2.4 — Cross-question termination: test_qid must exist
    for qid, doc_q in doc_index.items():
        for rule in (doc_q.get('termination_rules') or []):
            test_qid = (rule.get('test_qid') or '').upper()
            if test_qid and test_qid != qid and test_qid not in all_qids:
                if not should_skip_qid(test_qid):
                    results.append(_make_result(
                        f'{PREFIX}04', GROUP, NAME, qid,
                        'HIGH', 82, 'TERMINATION_CROSS_QID_MISSING',
                        f'Termination rule at {qid} tests question "{test_qid}" '
                        f'which does not exist in the survey.',
                        f'Verify "{test_qid}" exists or correct the termination rule.',
                    ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G3 — MANDATORY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g3_mandatory(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G3: Mandatory question alignment."""
    results: List[RuleResult] = []
    GROUP = 3
    NAME = 'MANDATORY ENGINE'
    PREFIX = 'G3'

    mandatory_qids = [
        qid for qid, doc_q in doc_index.items()
        if doc_q.get('is_mandatory')
    ]
    mandatory_set = set(mandatory_qids)

    total_doc = len(doc_index)

    # R3.1 — MANDATORY_TYPE_INCONSISTENCY
    # doc.is_mandatory=True but xml.type is OPEN/NUMERIC yet question also has options
    for qid in mandatory_qids:
        if qid in xml_index:
            doc_q = doc_index[qid]
            xml_q = xml_index[qid]
            xml_type_raw = (xml_q.get('type') or '').upper()
            xml_canon = _canon_xml_type(xml_type_raw)
            doc_opts = doc_q.get('options') or []
            xml_opts = xml_q.get('options') or []
            # If xml says OPEN/NUMERIC but both sides have options → type inconsistency
            if xml_canon in ('OPEN', 'NUMERIC') and (doc_opts or xml_opts):
                results.append(_make_result(
                    f'{PREFIX}01', GROUP, NAME, qid,
                    'LOW', 60, 'MANDATORY_TYPE_INCONSISTENCY',
                    f'Question {qid} is mandatory in doc but XML type is '
                    f'"{xml_type_raw}" (open/numeric) while options are present.',
                    'Verify question type — mandatory open-end with options may be misconfigured.',
                ))

    # R3.2 — MANDATORY_QUESTION_MISSING_IN_XML
    for qid in mandatory_qids:
        if qid not in xml_index:
            results.append(_make_result(
                f'{PREFIX}02', GROUP, NAME, qid,
                'HIGH', 82, 'MANDATORY_QUESTION_MISSING_IN_XML',
                f'Mandatory question {qid} is defined in doc but absent from XML.',
                f'Add question {qid} to the XML export or verify import.',
            ))

    # R3.3 — EXCESSIVE_MANDATORY_QUESTIONS
    if total_doc > 0:
        pct = len(mandatory_set) / total_doc
        if pct > 0.70:
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, 'SURVEY',
                'LOW', 55, 'EXCESSIVE_MANDATORY_QUESTIONS',
                f'{len(mandatory_set)} of {total_doc} questions are mandatory '
                f'({pct:.0%}). More than 70% mandatory is unusual.',
                'Review mandatory flags — respondent experience may suffer.',
            ))

    # R3.4 — Note mandatory questions that have no xml counterpart to verify against
    mandatory_no_xml = [q for q in mandatory_qids if q not in xml_index]
    if mandatory_no_xml:
        # Already reported per-qid as R3.2 above; provide a summary note
        if len(mandatory_no_xml) > 1:
            results.append(_make_result(
                f'{PREFIX}04', GROUP, NAME, 'SURVEY',
                'INFO', 70, 'MANDATORY_VERIFICATION_INCOMPLETE',
                f'{len(mandatory_no_xml)} mandatory question(s) have no XML counterpart '
                f'and cannot be verified in XML: {mandatory_no_xml[:5]}.',
                'Ensure XML export is complete before running mandatory checks.',
            ))

    # R3.5 — HIDDEN_MANDATORY: text contains mandatory language but is_mandatory=False
    for qid, doc_q in doc_index.items():
        if doc_q.get('is_mandatory'):
            continue
        text = doc_q.get('text') or ''
        opts_text = ' '.join(
            str(o.get('text', '')) for o in (doc_q.get('options') or [])
        )
        full_text = text + ' ' + opts_text
        if _MANDATORY_TEXT_PAT.search(full_text):
            results.append(_make_result(
                f'{PREFIX}05', GROUP, NAME, qid,
                'MEDIUM', 65, 'HIDDEN_MANDATORY',
                f'Question {qid} is not flagged mandatory but its text contains '
                f'a mandatory language marker. This may be a doc annotation error.',
                'Confirm whether this question should be mandatory and set is_mandatory accordingly.',
            ))

    # R3.6 — MANDATORY_IN_XML_NOT_IN_DOC: XML has a forced/required attribute but doc doesn't flag mandatory
    for qid, xml_q in xml_index.items():
        xml_opts = xml_q.get('options') or []
        # Some platforms mark required via a "required" marker on options or a field in the question
        _xml_markers = [str(o.get('marker', '')).lower() for o in xml_opts]
        has_xml_required = any('required' in m or 'mandatory' in m or 'obligatoire' in m
                               for m in _xml_markers if m)
        if has_xml_required and qid in doc_index:
            doc_q = doc_index[qid]
            if not doc_q.get('is_mandatory'):
                results.append(_make_result(
                    f'{PREFIX}06', GROUP, NAME, qid,
                    'MEDIUM', 68, 'MANDATORY_IN_XML_NOT_IN_DOC',
                    f'XML question {qid} has a mandatory/required marker but doc '
                    f'does not flag this question as mandatory.',
                    'Align mandatory flags between spec document and XML export.',
                ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G4 — PIPING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g4_piping(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G4: Piping variable validation."""
    results: List[RuleResult] = []
    GROUP = 4
    NAME = 'PIPING ENGINE'
    PREFIX = 'G4'

    all_qids_upper = {q.upper() for q in all_qids}

    for qid, doc_q in doc_index.items():
        has_piping_flag = doc_q.get('has_piping', False)
        text = doc_q.get('text') or ''
        pipe_vars = extract_pipe_vars(text)

        if not has_piping_flag and not pipe_vars:
            continue

        # R4.1 — PIPING_SOURCE_MISSING: pipe references a QID not in survey
        for pv in pipe_vars:
            raw = pv.get('raw', '')
            var_name = pv.get('var_name', '')

            # Check if it looks like a QID reference
            src_qid = None
            m = _PIPE_QID_PAT.match(raw)
            if m:
                src_qid = m.group(1).upper()
            elif re.match(r'^[RSQrsq]\d', var_name):
                src_qid = var_name.upper()

            if src_qid and src_qid not in all_qids_upper:
                if not should_skip_qid(src_qid):
                    results.append(_make_result(
                        f'{PREFIX}01', GROUP, NAME, qid,
                        'HIGH', 85, 'PIPING_SOURCE_MISSING',
                        f'Question {qid} pipes from "{raw}" but source QID "{src_qid}" '
                        f'does not exist in the survey.',
                        f'Add question "{src_qid}" to the survey or fix the pipe reference.',
                    ))

        # R4.2 — PIPING_NOT_IN_XML: doc has piping patterns but xml question text is blank
        if qid in xml_index:
            xml_q = xml_index[qid]
            xml_text = (xml_q.get('text') or '').strip()
            if pipe_vars and not xml_text:
                results.append(_make_result(
                    f'{PREFIX}02', GROUP, NAME, qid,
                    'MEDIUM', 65, 'PIPING_NOT_IN_XML',
                    f'Question {qid} has piping patterns in doc '
                    f'({[pv["raw"] for pv in pipe_vars[:3]]}) but XML question text is blank.',
                    'Verify XML question text is correctly populated with piping markers.',
                ))

        # R4.3 — PIPING_TARGET_UNDEFINED: has_piping flag but no pipe vars found
        if has_piping_flag and not pipe_vars:
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, qid,
                'HIGH', 78, 'PIPING_TARGET_UNDEFINED',
                f'Question {qid} is flagged as has_piping=True but no pipe '
                f'variable patterns were found in the question text.',
                'Verify piping variables are correctly formatted in the question text.',
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G5 — LOOP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g5_loops(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G5: Loop/repeat block validation."""
    results: List[RuleResult] = []
    GROUP = 5
    NAME = 'LOOP ENGINE'
    PREFIX = 'G5'

    doc_qids = list(doc_index.keys())
    xml_qids = list(xml_index.keys())

    doc_loop_map = build_loop_map(doc_qids)
    xml_loop_map = build_loop_map(xml_qids)

    xml_loop_map_upper = {k.upper(): v for k, v in xml_loop_map.items()}

    # R5.1 — LOOP_MISSING_IN_XML: loop group in doc not found in xml
    for parent, children in doc_loop_map.items():
        parent_up = parent.upper()
        if parent_up not in xml_loop_map_upper:
            results.append(_make_result(
                f'{PREFIX}01', GROUP, NAME, parent,
                'MEDIUM', 70, 'LOOP_MISSING_IN_XML',
                f'Loop group with parent "{parent}" has {len(children)} '
                f'siblings in doc ({children[:4]}) but was not found in XML.',
                f'Ensure loop variables {children[:4]} are present in XML export.',
            ))

    # R5.2 — LOOP_COUNT_MISMATCH: doc loop has N siblings, xml has significantly fewer
    for parent, doc_children in doc_loop_map.items():
        parent_up = parent.upper()
        if parent_up in xml_loop_map_upper:
            xml_children = xml_loop_map_upper[parent_up]
            doc_count = len(doc_children)
            xml_count = len(xml_children)
            if doc_count > xml_count + 1:
                results.append(_make_result(
                    f'{PREFIX}02', GROUP, NAME, parent,
                    'MEDIUM', 72, 'LOOP_COUNT_MISMATCH',
                    f'Loop "{parent}": doc has {doc_count} iterations '
                    f'but XML has only {xml_count}.',
                    'Verify all loop iterations are exported in the XML.',
                ))

    # R5.3 — LOOP_EXTRA_IN_XML: xml loop has more siblings than doc
    for parent, xml_children in xml_loop_map.items():
        parent_up = parent.upper()
        doc_children_upper = {k.upper(): v for k, v in doc_loop_map.items()}
        if parent_up in doc_children_upper:
            doc_count = len(doc_children_upper[parent_up])
            xml_count = len(xml_children)
            if xml_count > doc_count + 1:
                results.append(_make_result(
                    f'{PREFIX}03', GROUP, NAME, parent,
                    'LOW', 65, 'LOOP_EXTRA_IN_XML',
                    f'Loop "{parent}": XML has {xml_count} iterations '
                    f'but doc has only {doc_count}.',
                    'Check whether XML has extra loop iterations not in the spec.',
                ))

    # R5.4 — LOOP_BROKEN: parent QID exists but no loop children match
    all_qids_upper = {q.upper() for q in all_qids}
    for parent, children in doc_loop_map.items():
        parent_up = parent.upper()
        # parent itself must exist somewhere
        if parent_up in all_qids_upper:
            # Check none of the children exist in xml
            xml_children_found = [
                c for c in children if c.upper() in {q.upper() for q in xml_qids}
            ]
            if not xml_children_found:
                results.append(_make_result(
                    f'{PREFIX}04', GROUP, NAME, parent,
                    'HIGH', 78, 'LOOP_BROKEN',
                    f'Loop parent "{parent}" exists but none of its '
                    f'{len(children)} children {children[:4]} are present in XML.',
                    'Verify loop block export — all iterations should appear in XML.',
                ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G6 — VARIABLE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g6_variables(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
    xml_questions: list,
) -> List[RuleResult]:
    """G6: Variable/QID existence, duplicates, and option code duplicates."""
    results: List[RuleResult] = []
    GROUP = 6
    NAME = 'VARIABLE ENGINE'
    PREFIX = 'G6'

    all_qids_upper = {q.upper() for q in all_qids}

    # R6.1 — UNDEFINED_VARIABLE_IN_ROUTING: routing references non-existent QID
    for qid, xml_q in xml_index.items():
        rt = xml_q.get('routing') or ''
        if not rt.strip():
            continue
        for ref in _extract_qid_refs(rt):
            ref_up = ref.upper()
            if ref_up not in all_qids_upper and not should_skip_qid(ref):
                results.append(_make_result(
                    f'{PREFIX}01', GROUP, NAME, qid,
                    'HIGH', 80, 'UNDEFINED_VARIABLE_IN_ROUTING',
                    f'XML routing for {qid} references "{ref}" '
                    f'which is not defined in the survey.',
                    f'Add question "{ref}" to the survey or fix the routing expression.',
                ))

    # R6.2 — DUPLICATE_QID: same qid_normalized appears twice in xml_questions
    nqid_seen: Dict[str, List[str]] = {}
    for q in (xml_questions or []):
        nqid = (q.get('qid_normalized') or '').lower().strip()
        raw_qid = q.get('qid') or ''
        if nqid:
            nqid_seen.setdefault(nqid, []).append(raw_qid)

    for nqid, qid_list in nqid_seen.items():
        if len(qid_list) > 1:
            results.append(_make_result(
                f'{PREFIX}02', GROUP, NAME, qid_list[0],
                'HIGH', 92, 'DUPLICATE_QID',
                f'Normalized QID "{nqid}" appears {len(qid_list)} times in XML: '
                f'{qid_list[:5]}.',
                'Remove duplicate questions or ensure unique QID naming.',
            ))

    # R6.3 — DUPLICATE_OPTION_CODE in xml (same code appears twice in one question)
    for qid, xml_q in xml_index.items():
        opts = xml_q.get('options') or []
        code_list = [str(o.get('code', '')).strip() for o in opts if o.get('code') is not None]
        code_list = [c for c in code_list if c]
        seen_codes: Set[str] = set()
        dup_codes: Set[str] = set()
        for c in code_list:
            if c in seen_codes:
                dup_codes.add(c)
            seen_codes.add(c)
        if dup_codes:
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, qid,
                'HIGH', 88, 'DUPLICATE_OPTION_CODE',
                f'XML question {qid} has duplicate answer codes: {sorted(dup_codes)}.',
                'Ensure each answer option has a unique code within the question.',
            ))

    # R6.4 — DUPLICATE_OPTION_TEXT in doc (same option text in multiple positions)
    for qid, doc_q in doc_index.items():
        opts = doc_q.get('options') or []
        texts = [str(o.get('text', '')).strip().lower() for o in opts if o.get('text')]
        seen_texts: Set[str] = set()
        dup_texts: List[str] = []
        for t in texts:
            if t and t in seen_texts:
                dup_texts.append(t)
            seen_texts.add(t)
        if dup_texts:
            display = [t[:30] for t in dup_texts[:3]]
            results.append(_make_result(
                f'{PREFIX}04', GROUP, NAME, qid,
                'MEDIUM', 75, 'DUPLICATE_OPTION_TEXT',
                f'Doc question {qid} has duplicate option text(s): {display}.',
                'Check for copy-paste errors in answer options.',
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G7 — QUESTION TYPE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g7_types(
    doc_index: dict,
    xml_index: dict,
) -> List[RuleResult]:
    """G7: Question type cross-validation between doc and xml."""
    results: List[RuleResult] = []
    GROUP = 7
    NAME = 'QUESTION TYPE ENGINE'
    PREFIX = 'G7'

    for qid in sorted(set(doc_index.keys()) & set(xml_index.keys())):
        doc_q = doc_index[qid]
        xml_q = xml_index[qid]

        doc_type_raw = doc_q.get('question_type') or ''
        is_numeric = bool(doc_q.get('is_numeric'))
        if is_numeric and not doc_type_raw:
            doc_type_raw = 'NUMERIC'
        doc_canon = _canon_doc_type(doc_type_raw)

        xml_type_raw = xml_q.get('type') or ''
        xml_canon = _canon_xml_type(xml_type_raw)

        # Skip if either side is UNKNOWN
        if doc_canon == 'UNKNOWN' or xml_canon == 'UNKNOWN':
            continue

        if doc_canon == xml_canon:
            continue

        if doc_canon == 'NUMERIC' and xml_canon in ('MULTIPLE', 'GRID', 'SINGLE'):
            severity = 'INFO'
            confidence = 45
            issue_type = 'GRID_NUMERIC_REPRESENTATION'
            if xml_canon in ('SINGLE', 'MULTIPLE') and _xml_has_categorical_options(xml_q):
                severity = 'LOW'
                confidence = 55
            results.append(_make_result(
                f'{PREFIX}04', GROUP, NAME, qid,
                severity, confidence, issue_type,
                f'Numeric DOC question {qid} is represented in XML as {xml_canon}. '
                f'This is usually a grid/cell export representation and needs validation/range review, not a closed-question bug.',
                'Compare numeric validation/range and grid metadata rather than requiring answer options.',
            ))
            continue

        # R7.1 — SINGLE_MULTI_MISMATCH: SINGLE vs MULTIPLE is a critical data bug
        if (doc_canon == 'SINGLE' and xml_canon == 'MULTIPLE') or \
           (doc_canon == 'MULTIPLE' and xml_canon == 'SINGLE'):
            results.append(_make_result(
                f'{PREFIX}01', GROUP, NAME, qid,
                'HIGH', 90, 'SINGLE_MULTI_MISMATCH',
                f'Critical type mismatch at {qid}: doc={doc_canon} vs xml={xml_canon}. '
                f'Single/multi confusion causes data collection errors.',
                f'Align question type — doc says "{doc_type_raw}", XML says "{xml_type_raw}".',
            ))

        # R7.2 — TYPE_INCOMPATIBLE: OPEN vs closed or GRID vs OPEN
        elif (doc_canon in ('OPEN', 'NUMERIC') and xml_canon in ('SINGLE', 'MULTIPLE', 'GRID')) or \
             (doc_canon in ('SINGLE', 'MULTIPLE', 'GRID') and xml_canon in ('OPEN', 'NUMERIC')):
            results.append(_make_result(
                f'{PREFIX}02', GROUP, NAME, qid,
                'HIGH', 82, 'TYPE_INCOMPATIBLE',
                f'Incompatible type change at {qid}: doc={doc_canon} vs xml={xml_canon}. '
                f'Cannot map open-ended to closed or vice versa.',
                f'Correct question type — doc says "{doc_type_raw}", XML says "{xml_type_raw}".',
            ))

        # R7.3 — TYPE_MISMATCH_DOC_XML: any other mismatch
        else:
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, qid,
                'HIGH', 85, 'TYPE_MISMATCH_DOC_XML',
                f'Type mismatch at {qid}: doc canonical="{doc_canon}" '
                f'(raw: "{doc_type_raw}") vs xml canonical="{xml_canon}" '
                f'(raw: "{xml_type_raw}").',
                f'Verify and align question type between spec and XML.',
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G8 — OPTION/CODE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g8_options(
    doc_index: dict,
    xml_index: dict,
) -> List[RuleResult]:
    """G8: Answer option and code validation."""
    results: List[RuleResult] = []
    GROUP = 8
    NAME = 'OPTION/CODE ENGINE'
    PREFIX = 'G8'

    # Per-question cross-checks for matched questions
    for qid in sorted(set(doc_index.keys()) & set(xml_index.keys())):
        doc_q = doc_index[qid]
        xml_q = xml_index[qid]
        if _is_numeric_doc_question(doc_q):
            continue

        doc_opts = doc_q.get('options') or []
        xml_opts = xml_q.get('options') or []

        doc_codes = {
            str(o.get('code', '')).strip()
            for o in doc_opts
            if o.get('code') is not None and str(o.get('code', '')).strip()
        }
        xml_codes = {
            str(o.get('code', '')).strip()
            for o in xml_opts
            if o.get('code') is not None and str(o.get('code', '')).strip()
        }

        doc_count = len(doc_opts)
        xml_count = len(xml_opts)

        # R8.1 — ANSWER_CODE_MISSING_IN_XML
        if doc_codes and xml_codes:
            missing = sorted(doc_codes - xml_codes, key=lambda x: (len(x), x))
            if missing:
                results.append(_make_result(
                    f'{PREFIX}01', GROUP, NAME, qid,
                    'HIGH', 88, 'ANSWER_CODE_MISSING_IN_XML',
                    f'Question {qid}: answer code(s) {missing[:8]} are in doc '
                    f'but missing from XML options.',
                    'Add missing answer codes to XML or correct doc specification.',
                ))

        # R8.2 — ANSWER_CODE_EXTRA_IN_XML
        if doc_codes and xml_codes:
            extra = sorted(xml_codes - doc_codes, key=lambda x: (len(x), x))
            if extra:
                results.append(_make_result(
                    f'{PREFIX}02', GROUP, NAME, qid,
                    'MEDIUM', 72, 'ANSWER_CODE_EXTRA_IN_XML',
                    f'Question {qid}: answer code(s) {extra[:8]} are in XML '
                    f'but not in doc specification.',
                    'Verify extra codes are intentional — may be framework codes.',
                ))

        # R8.3 — OPTIONS_FEWER_IN_XML
        if doc_count > 0 and xml_count > 0:
            if doc_count > xml_count + 1:
                missing_n = doc_count - xml_count
                results.append(_make_result(
                    f'{PREFIX}03', GROUP, NAME, qid,
                    'HIGH', 82, 'OPTIONS_FEWER_IN_XML',
                    f'Question {qid}: doc has {doc_count} options but XML has '
                    f'only {xml_count} ({missing_n} fewer).',
                    'Add missing answer options to XML export.',
                ))

            # R8.4 — OPTIONS_MORE_IN_XML
            elif xml_count > doc_count + 1:
                extra_n = xml_count - doc_count
                results.append(_make_result(
                    f'{PREFIX}04', GROUP, NAME, qid,
                    'MEDIUM', 68, 'OPTIONS_MORE_IN_XML',
                    f'Question {qid}: XML has {xml_count} options but doc has '
                    f'only {doc_count} ({extra_n} more).',
                    'Check for extra/test options in XML not in the spec.',
                ))

    # R8.5 — DUPLICATE_CODE_IN_DOC: same code appears twice in one doc question
    for qid, doc_q in doc_index.items():
        opts = doc_q.get('options') or []
        code_list = [str(o.get('code', '')).strip() for o in opts if o.get('code') is not None]
        code_list = [c for c in code_list if c]
        seen: Set[str] = set()
        dups: Set[str] = set()
        for c in code_list:
            if c in seen:
                dups.add(c)
            seen.add(c)
        if dups:
            results.append(_make_result(
                f'{PREFIX}05', GROUP, NAME, qid,
                'HIGH', 92, 'DUPLICATE_CODE_IN_DOC',
                f'Doc question {qid} has duplicate option codes: {sorted(dups)}.',
                'Fix duplicate codes in the specification document.',
            ))

    # R8.6 — DUPLICATE_CODE_IN_XML: same code appears twice in one xml question
    for qid, xml_q in xml_index.items():
        opts = xml_q.get('options') or []
        code_list = [str(o.get('code', '')).strip() for o in opts if o.get('code') is not None]
        code_list = [c for c in code_list if c]
        seen = set()
        dups = set()
        for c in code_list:
            if c in seen:
                dups.add(c)
            seen.add(c)
        if dups:
            results.append(_make_result(
                f'{PREFIX}06', GROUP, NAME, qid,
                'HIGH', 92, 'DUPLICATE_CODE_IN_XML',
                f'XML question {qid} has duplicate option codes: {sorted(dups)}.',
                'Fix duplicate codes in the XML export configuration.',
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G9 — SURVEY GRAPH ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g9_graph(
    doc_questions: dict,
    xml_questions: list,
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G9: Graph-based routing analysis — cycles, orphans, coverage."""
    results: List[RuleResult] = []
    GROUP = 9
    NAME = 'SURVEY GRAPH ENGINE'
    PREFIX = 'G9'

    # ── Pure adjacency fallback (always runs) ─────────────────────────────────
    # Build simple adjacency from routing strings without networkx
    simple_adj: Dict[str, Set[str]] = {}
    for qid, xml_q in xml_index.items():
        rt = xml_q.get('routing') or ''
        refs = {r.upper() for r in _extract_qid_refs(rt)}
        if refs:
            simple_adj[qid] = refs

    # R9.F1 — SELF_REFERENCE: question routes to itself
    for qid, refs in simple_adj.items():
        if qid in refs:
            results.append(_make_result(
                f'{PREFIX}F1', GROUP, NAME, qid,
                'HIGH', 88, 'SELF_REFERENCE',
                f'Question {qid} references itself in its routing condition.',
                'Remove self-reference from routing — this creates an infinite loop.',
            ))

    # ── NetworkX-based analysis ───────────────────────────────────────────────
    if not _HAVE_GRAPH:
        # Emit an info note that graph analysis was skipped
        results.append(_make_result(
            f'{PREFIX}00', GROUP, NAME, 'SURVEY',
            'INFO', 0, 'GRAPH_ANALYSIS_SKIPPED',
            'networkx/survey_graph not available — advanced graph analysis skipped.',
            'Install networkx for full cycle and orphan detection.',
        ))
        return results

    try:
        xml_graph = build_xml_graph(xml_questions)
    except Exception as exc:
        log.warning("G9: build_xml_graph failed: %s", exc)
        results.append(_make_result(
            f'{PREFIX}00', GROUP, NAME, 'SURVEY',
            'INFO', 0, 'GRAPH_BUILD_FAILED',
            f'Could not build XML survey graph: {exc}',
            'Check XML question data for malformed routing strings.',
        ))
        return results

    try:
        doc_graph = build_doc_graph(doc_questions)
    except Exception as exc:
        log.warning("G9: build_doc_graph failed: %s", exc)
        doc_graph = None

    g = xml_graph.graph

    # R9.1 — CIRCULAR_ROUTING: cycle in the xml graph
    try:
        cycle = nx.find_cycle(g)
        cycle_nodes = list({u for u, v in cycle} | {v for u, v in cycle})
        # Filter to question nodes only
        q_nodes_in_cycle = [
            n for n in cycle_nodes
            if g.nodes[n].get('node_type') == 'question'
        ]
        if q_nodes_in_cycle:
            results.append(_make_result(
                f'{PREFIX}01', GROUP, NAME, q_nodes_in_cycle[0],
                'HIGH', 85, 'CIRCULAR_ROUTING',
                f'Cycle detected in XML survey graph involving: '
                f'{q_nodes_in_cycle[:6]}.',
                'Break the routing cycle — at least one conditional path must exit the loop.',
            ))
    except Exception:
        # nx.find_cycle raises when no cycle
        pass

    # R9.2 — ORPHAN_QUESTION: question nodes with in_degree==0 except START
    try:
        q_set = set(xml_graph.question_nodes())
        for node in g.nodes():
            if node not in q_set:
                continue
            if g.in_degree(node) == 0:
                results.append(_make_result(
                    f'{PREFIX}02', GROUP, NAME, node,
                    'MEDIUM', 70, 'ORPHAN_QUESTION',
                    f'Question {node} has no incoming edges — it may be unreachable.',
                    'Check if this question is intentionally standalone or missing a routing link.',
                ))
    except Exception as exc:
        log.warning("G9 orphan check failed: %s", exc)

    # R9.3 — NO_TERMINATION_PATH: zero termination nodes
    try:
        term_nodes = xml_graph.termination_nodes()
        if len(term_nodes) == 0:
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, 'SURVEY',
                'HIGH', 80, 'NO_TERMINATION_PATH',
                'The XML survey graph has no termination (screen-out) nodes.',
                'Verify termination logic exists in the XML — screen-outs may be missing.',
            ))
    except Exception as exc:
        log.warning("G9 termination check failed: %s", exc)

    # R9.4 — compare doc_graph vs xml_graph
    if doc_graph is not None:
        try:
            flow_issues = compare_graphs(doc_graph, xml_graph)
            for fi in flow_issues:
                # Map FlowIssue severity to our rule severity
                sev_map = {'HIGH': 'HIGH', 'MEDIUM': 'MEDIUM', 'LOW': 'LOW'}
                sev = sev_map.get(fi.severity, 'MEDIUM')
                results.append(_make_result(
                    f'{PREFIX}04', GROUP, NAME, fi.qid,
                    sev, 82, f'GRAPH_{fi.issue_type}',
                    fi.details,
                    'Review routing alignment between doc specification and XML export.',
                ))
        except Exception as exc:
            log.warning("G9 compare_graphs failed: %s", exc)

    # R9.5 — UNREACHABLE_QUESTION: question has no path from START
    try:
        reachable = set(nx.descendants(g, 'START')) | {'START'}
        q_nodes_set = set(xml_graph.question_nodes())
        unreachable_qs = q_nodes_set - reachable
        for node in sorted(unreachable_qs):
            results.append(_make_result(
                f'{PREFIX}05', GROUP, NAME, node,
                'HIGH', 80, 'UNREACHABLE_QUESTION',
                f'Question {node} cannot be reached from the survey start. '
                f'It has no incoming path through any routing.',
                f'Check whether {node} should be conditional on another question '
                f'or if it was accidentally disconnected from the survey flow.',
            ))
    except Exception as exc:
        log.warning("G9 unreachable check failed: %s", exc)

    # R9.6 — DEAD_END: question with outgoing edges only to TERMINATE/COMPLETE
    #         and no conditional branches — means some respondents always hit this dead end
    try:
        for node in sorted(xml_graph.question_nodes()):
            out_edges = list(g.out_edges(node, data=True))
            if not out_edges:
                # No outgoing edges at all (except if it's the last question)
                if node != xml_graph.question_flow_order()[-1:]:
                    results.append(_make_result(
                        f'{PREFIX}06', GROUP, NAME, node,
                        'MEDIUM', 72, 'DEAD_END_QUESTION',
                        f'Question {node} has no outgoing routing edges — '
                        f'survey flow stops here for all respondents.',
                        f'Verify {node} is intentionally the last question or add next-question routing.',
                    ))
    except Exception as exc:
        log.warning("G9 dead-end check failed: %s", exc)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# G10 — EXPORT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_g10_export(
    doc_index: dict,
    xml_index: dict,
    all_qids: Set[str],
) -> List[RuleResult]:
    """G10: Export readiness and data integrity checks."""
    results: List[RuleResult] = []
    GROUP = 10
    NAME = 'EXPORT ENGINE'
    PREFIX = 'G10'

    # R10.1 — MULTIPLE_CHOICE_NO_CODES: MULTIPLE question has no option codes
    for qid, doc_q in doc_index.items():
        q_type = doc_q.get('question_type') or ''
        canon = _canon_doc_type(q_type)
        if canon == 'MULTIPLE':
            opts = doc_q.get('options') or []
            has_codes = any(o.get('code') is not None for o in opts)
            if opts and not has_codes:
                results.append(_make_result(
                    f'{PREFIX}01', GROUP, NAME, qid,
                    'MEDIUM', 68, 'MULTIPLE_CHOICE_NO_CODES',
                    f'Multi-select question {qid} has {len(opts)} options but '
                    f'none have answer codes.',
                    'Add numeric codes to all answer options for proper data export.',
                ))

    # R10.2 — LOOP_TYPE_INCONSISTENCY: loop siblings have mixed types
    doc_qids = list(doc_index.keys())
    doc_loop_map = build_loop_map(doc_qids)
    for parent, children in doc_loop_map.items():
        child_types: List[str] = []
        for child in children:
            child_up = child.upper()
            if child_up in doc_index:
                raw_type = doc_index[child_up].get('question_type') or ''
                canon = _canon_doc_type(raw_type)
                if canon != 'UNKNOWN':
                    child_types.append(canon)
        unique_types = set(child_types)
        if len(unique_types) > 1:
            results.append(_make_result(
                f'{PREFIX}02', GROUP, NAME, parent,
                'MEDIUM', 72, 'LOOP_TYPE_INCONSISTENCY',
                f'Loop group "{parent}" has siblings with mixed question types: '
                f'{sorted(unique_types)}. Expected all siblings to have same type.',
                'Standardise question types within a loop block for consistent export.',
            ))

    # R10.3 — INVALID_QID_FOR_EXPORT: QIDs with characters that cause export issues
    for qid in sorted(all_qids):
        if _INVALID_QID_CHARS_PAT.search(qid):
            bad_chars = sorted(set(_INVALID_QID_CHARS_PAT.findall(qid)))
            results.append(_make_result(
                f'{PREFIX}03', GROUP, NAME, qid,
                'MEDIUM', 75, 'INVALID_QID_FOR_EXPORT',
                f'QID "{qid}" contains characters that may cause export/data issues: '
                f'{bad_chars}.',
                'Rename the QID to use only alphanumeric characters and underscores.',
            ))

    # R10.4 — OPEN_END_WITH_PIPING: open-end questions that use piping
    for qid, doc_q in doc_index.items():
        q_type = doc_q.get('question_type') or ''
        canon = _canon_doc_type(q_type)
        if canon in ('OPEN', 'NUMERIC') and doc_q.get('has_piping'):
            results.append(_make_result(
                f'{PREFIX}04', GROUP, NAME, qid,
                'LOW', 55, 'OPEN_END_WITH_PIPING',
                f'Open-end question {qid} uses piping. Export variable naming '
                f'may need special attention.',
                'Verify the piped variable name will export correctly in all formats.',
            ))

    # R10.5 — XML_QUESTION_NO_TYPE: XML question has UNKNOWN type
    for qid, xml_q in xml_index.items():
        xml_type_raw = (xml_q.get('type') or '').strip().upper()
        if not xml_type_raw or xml_type_raw == 'UNKNOWN':
            results.append(_make_result(
                f'{PREFIX}05', GROUP, NAME, qid,
                'MEDIUM', 70, 'XML_QUESTION_NO_TYPE',
                f'XML question {qid} has no question type or type=UNKNOWN. '
                f'Untyped questions may export incorrectly.',
                f'Assign a valid question type ({", ".join(["SINGLE","MULTIPLE","OPEN","NUMERIC","GRID","RANK"])}) in the XML.',
            ))

    # R10.6 — DOC_XML_BOTH_EMPTY_OPTIONS: closed question with no options in either source
    for qid in sorted(set(doc_index.keys()) & set(xml_index.keys())):
        doc_q = doc_index[qid]
        xml_q = xml_index[qid]
        if _is_numeric_doc_question(doc_q):
            continue
        doc_type = _canon_doc_type(doc_q.get('question_type') or '')
        xml_type = _canon_xml_type(xml_q.get('type') or '')
        if doc_type in ('SINGLE', 'MULTIPLE') or xml_type in ('SINGLE', 'MULTIPLE'):
            doc_opts = doc_q.get('options') or []
            xml_opts = xml_q.get('options') or []
            if not doc_opts and not xml_opts:
                results.append(_make_result(
                    f'{PREFIX}06', GROUP, NAME, qid,
                    'HIGH', 85, 'CLOSED_QUESTION_NO_OPTIONS',
                    f'Closed question {qid} (type={doc_type or xml_type}) '
                    f'has no answer options in either doc or XML.',
                    'Add answer options to this question in both the spec document and XML.',
                ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

GROUP_NAMES = {
    1: 'ROUTING ENGINE',
    2: 'TERMINATION ENGINE',
    3: 'MANDATORY ENGINE',
    4: 'PIPING ENGINE',
    5: 'LOOP ENGINE',
    6: 'VARIABLE ENGINE',
    7: 'QUESTION TYPE ENGINE',
    8: 'OPTION/CODE ENGINE',
    9: 'SURVEY GRAPH ENGINE',
    10: 'EXPORT ENGINE',
}


def run_rule_engine(
    doc_questions: Optional[dict] = None,
    xml_questions: Optional[list] = None,
) -> dict:
    """
    Run all 10 rule groups against doc_questions and xml_questions.

    Args:
        doc_questions: {qid: {text, options:[{code,text}], question_type,
                               is_mandatory, has_piping,
                               termination_rules:[{test_qid,answer_codes,raw,source}],
                               is_numeric}}
        xml_questions: [{qid, qid_normalized, text, type, options:[{code,text,marker}],
                          routing, termination, is_matrix_group}]

    Returns:
        {
          results: list[dict],   # each is RuleResult.to_dict()
          summary: {
              total: int, high: int, medium: int, low: int, info: int,
              by_group: {1: n, 2: n, ...}
          },
          duration_ms: int
        }
    """
    t0 = time.time()

    doc_questions = doc_questions or {}
    xml_questions = xml_questions or []

    all_results: List[RuleResult] = []

    # Build shared indexes once
    doc_index = _build_doc_index(doc_questions)
    xml_index = _build_xml_index(xml_questions)
    all_qids = _build_all_qids(doc_index, xml_index)

    # Build termination matrix (separate from rule results)
    termination_matrix: List[dict] = []
    try:
        termination_matrix = build_termination_matrix(doc_index, xml_index)
    except Exception as _tm_exc:
        log.warning("termination_matrix build failed: %s", _tm_exc)

    print(f"[rule_engine] Starting: {len(doc_index)} doc QIDs, "
          f"{len(xml_index)} xml QIDs, {len(all_qids)} total unique QIDs")

    # Run each group with full exception isolation
    group_runners = [
        (1,  lambda: _run_g1_routing(doc_index, xml_index, all_qids)),
        (2,  lambda: _run_g2_termination(doc_index, xml_index, all_qids)),
        (3,  lambda: _run_g3_mandatory(doc_index, xml_index, all_qids)),
        (4,  lambda: _run_g4_piping(doc_index, xml_index, all_qids)),
        (5,  lambda: _run_g5_loops(doc_index, xml_index, all_qids)),
        (6,  lambda: _run_g6_variables(doc_index, xml_index, all_qids, xml_questions)),
        (7,  lambda: _run_g7_types(doc_index, xml_index)),
        (8,  lambda: _run_g8_options(doc_index, xml_index)),
        (9,  lambda: _run_g9_graph(doc_questions, xml_questions, doc_index, xml_index, all_qids)),
        (10, lambda: _run_g10_export(doc_index, xml_index, all_qids)),
    ]

    by_group: Dict[int, int] = {g: 0 for g in range(1, 11)}

    for group_num, runner in group_runners:
        try:
            t_group = time.time()
            group_results = runner()
            elapsed_ms = int((time.time() - t_group) * 1000)
            all_results.extend(group_results)
            by_group[group_num] = len(group_results)
            print(f"[rule_engine] G{group_num} ({GROUP_NAMES[group_num]}): "
                  f"{len(group_results)} issue(s) in {elapsed_ms}ms")
        except Exception as exc:
            log.error("rule_engine G%d crashed: %s", group_num, exc, exc_info=True)
            print(f"[rule_engine] G{group_num} ERROR: {exc}")
            # Emit a meta-issue so caller knows something went wrong
            all_results.append(_make_result(
                f'G{group_num}ERR', group_num, GROUP_NAMES[group_num], 'SURVEY',
                'INFO', 0, 'RULE_GROUP_ERROR',
                f'Rule group {group_num} ({GROUP_NAMES[group_num]}) failed: {exc}',
                'Check rule_engine logs for details.',
            ))

    # Build summary
    sev_counts = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0, 'INFO': 0}
    for r in all_results:
        key = r.severity if r.severity in sev_counts else 'INFO'
        sev_counts[key] += 1

    duration_ms = int((time.time() - t0) * 1000)
    total = len(all_results)

    print(f"[rule_engine] Complete: {total} issues "
          f"(HIGH={sev_counts['HIGH']}, MEDIUM={sev_counts['MEDIUM']}, "
          f"LOW={sev_counts['LOW']}, INFO={sev_counts['INFO']}) "
          f"in {duration_ms}ms")

    return {
        'results': [r.to_dict() for r in all_results],
        'summary': {
            'total': total,
            'high': sev_counts['HIGH'],
            'medium': sev_counts['MEDIUM'],
            'low': sev_counts['LOW'],
            'info': sev_counts['INFO'],
            'by_group': by_group,
        },
        'duration_ms': duration_ms,
        'termination_matrix': termination_matrix,
    }


# ── Backwards-compatible RuleEngine class (shim) ─────────────────────────────
# Preserves compatibility with any code that imports the old-style class.

class RuleEngine:
    """
    Legacy per-question rule runner shim.
    For new code, use run_rule_engine() directly.
    """

    def run_all_checks(self, doc_q, live_q, xml_q=None):
        """
        Minimal compatibility shim — runs basic per-question checks.
        Returns list of {result, type, confidence, reason, rule} for FAIL/WARN.
        """
        issues = []
        if doc_q is None and live_q is not None:
            issues.append({
                'result': 'WARN', 'type': 'QUESTION_EXTRA',
                'confidence': 70,
                'reason': 'Question present in live but not in spec',
                'rule': 'R002',
            })
        if doc_q is not None and live_q is None:
            issues.append({
                'result': 'FAIL', 'type': 'QUESTION_MISSING',
                'confidence': 95,
                'reason': 'Question exists in spec but absent from live survey',
                'rule': 'R001',
            })
        return issues
