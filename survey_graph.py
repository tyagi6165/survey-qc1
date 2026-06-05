"""
survey_graph.py — Survey flow directed graph.

Builds a nx.DiGraph from CanonicalQuestion lists (canonical_model.py).
Works for any source (doc, xml, live).  Graph comparison detects routing bugs.

Node types
----------
  question    — a survey question
  START       — synthetic entry point
  COMPLETE    — synthetic successful-completion endpoint
  TERMINATE   — screen-out / close  (one per triggering question)

Edge types
----------
  sequential  — default next question (always traversed)
  conditional — question is only shown when the labelled condition is true
  termination — leads to a TERMINATE node

Typical usage (standalone — no app.py changes in this step)
------------------------------------------------------------
    from xml_parser import parse_export
    from canonical_model import xml_to_canonical
    from survey_graph import build_graph, compare_graphs

    canon = xml_to_canonical(parse_export("survey.xml"))
    g = build_graph(canon)
    print(g.summary())
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import networkx as nx

from canonical_model import CanonicalQuestion
from qid_normalizer import should_skip_qid, get_parent_qid

log = logging.getLogger(__name__)


# ── Routing condition helpers ─────────────────────────────────────────────────

# Matches QIDs like Q1, S4, P3bis, Q12_3, Q1.2, SPE, BMO1, R2x2 …
_QID_PAT = re.compile(
    r'\b([A-Z]{1,8}\d+(?:[A-Z]\d*|bis|ter|_\d+|X\d+|\.\d+)?'
    r'|SPE|BMO\d*|SCREEN)\b',
    re.IGNORECASE,
)
_ALWAYS_PAT = re.compile(
    r'^\s*(ALL\s+RESPONDENTS?|ALWAYS|TOUS(\s+LES\s+R[EÉ]PONDANTS?)?)\s*$',
    re.IGNORECASE,
)
# Trailing close-out phrases to strip from termination strings
_CLOSE_SUFFIX = re.compile(
    r'\s+(Thank(\s+and)?\s+close|close\s+survey|terminate|screen\s*[_\-]?out'
    r'|end|APLUSA\b.*)',
    re.IGNORECASE,
)


def _is_trivial(routing: Optional[str]) -> bool:
    """True when the routing means 'always show' — no real condition."""
    if not routing:
        return True
    s = routing.strip()
    if _ALWAYS_PAT.match(s):
        return True
    # A bare digit or single punctuation is not a meaningful condition
    if re.match(r'^\d+$', s):
        return True
    # If there is no QID reference and no comparison operator, treat as trivial
    has_qid = bool(_QID_PAT.search(s))
    has_op  = bool(re.search(r'[<>=!≠≥≤]|\bAND\b|\bOR\b', s, re.IGNORECASE))
    return not (has_qid or has_op)


def _normalize_condition(raw: str) -> str:
    """
    Light normalisation of a display/skip condition string.
      'IF Q4 ≠ 1'          → 'Q4 != 1'
      'Si Q8 différent …'  → 'Si Q8 différent …'  (kept as-is if too complex)
    """
    s = raw.strip()
    # Strip leading 'IF '
    s = re.sub(r'^IF\s+', '', s, flags=re.IGNORECASE).strip()
    # Unicode operators
    s = s.replace('≠', '!=').replace('≥', '>=').replace('≤', '<=').replace('=', '=')
    # Collapse excessive whitespace
    s = re.sub(r'  +', ' ', s)
    return s


def _extract_term_condition(term_str: str) -> str:
    """
    Pull the testable condition from a raw termination string.

    'IF SPE ≠ 22 Thank and close'            → 'SPE != 22'
    'IF S2 < 2 or S2 > 40 Thank and close'  → 'S2 < 2 or S2 > 40'
    'IF SUM OF S4_1 + S4_2 + S4_3 < 5 …'   → 'SUM OF S4_1 + S4_2 + S4_3 < 5'
    """
    s = re.sub(r'^IF\s+', '', term_str.strip(), flags=re.IGNORECASE)
    s = _CLOSE_SUFFIX.sub('', s).strip()
    return _normalize_condition(s)


def referenced_qids(condition: str) -> List[str]:
    """Extract all QID tokens referenced inside a condition string."""
    return [m.group(1).upper() for m in _QID_PAT.finditer(condition)]


# ── Core graph class ──────────────────────────────────────────────────────────

class SurveyFlowGraph:
    """
    Directed graph representing a survey's question flow.

    Nodes: question QIDs + "START" + "COMPLETE" + "TERMINATE_{qid}"
    Edges: carry 'edge_type' ("sequential"|"conditional"|"termination")
           and 'condition' (the routing expression string, or None).
    """

    def __init__(self, source: str = ""):
        self.source = source
        self.graph: nx.DiGraph = nx.DiGraph()
        # Always-present synthetic nodes
        self.graph.add_node("START",    node_type="start",    label="START")
        self.graph.add_node("COMPLETE", node_type="complete", label="COMPLETE")

    # ── Mutation ─────────────────────────────────────────────────────────────

    def add_question(self, qid: str, **attrs) -> None:
        attrs.setdefault("node_type", "question")
        attrs.setdefault("label", qid)
        self.graph.add_node(qid, **attrs)

    def add_route(
        self,
        from_qid: str,
        to_qid: str,
        condition: Optional[str] = None,
        edge_type: str = "sequential",
    ) -> None:
        self.graph.add_edge(from_qid, to_qid, condition=condition, edge_type=edge_type)

    def add_termination(self, from_qid: str, condition: str) -> None:
        term_node = f"TERMINATE_{from_qid}"
        label = f"TERMINATE ({condition[:50]})" if condition else f"TERMINATE_{from_qid}"
        if not self.graph.has_node(term_node):
            self.graph.add_node(term_node, node_type="termination", label=label)
        self.graph.add_edge(from_qid, term_node, condition=condition, edge_type="termination")

    # ── Query ─────────────────────────────────────────────────────────────────

    def question_nodes(self) -> List[str]:
        return [
            n for n, d in self.graph.nodes(data=True)
            if d.get("node_type") == "question"
        ]

    def termination_nodes(self) -> List[str]:
        return [
            n for n, d in self.graph.nodes(data=True)
            if d.get("node_type") == "termination"
        ]

    def conditional_questions(self) -> List[str]:
        """QIDs whose *incoming* edge is labelled 'conditional'."""
        return [
            v for u, v, d in self.graph.edges(data=True)
            if d.get("edge_type") == "conditional"
        ]

    def flow_order(self) -> List[str]:
        """
        Topological sort of ALL nodes.
        Falls back to insertion order when the graph has cycles (shouldn't
        happen in a well-formed survey, but defensive).
        """
        try:
            return list(nx.topological_sort(self.graph))
        except nx.NetworkXUnfeasible:
            log.warning("SurveyFlowGraph(%s): cycle detected — using insertion order", self.source)
            return list(self.graph.nodes())

    def question_flow_order(self) -> List[str]:
        """Like flow_order() but filtered to question nodes only."""
        q_set = set(self.question_nodes())
        return [n for n in self.flow_order() if n in q_set]

    def paths_to_termination(self) -> Dict[str, List[List[str]]]:
        """
        For each TERMINATE_{qid} node, return all simple paths from START.
        Expensive on large surveys — for diagnostic use only.
        """
        result: Dict[str, List[List[str]]] = {}
        for t in self.termination_nodes():
            try:
                result[t] = list(nx.all_simple_paths(self.graph, "START", t, cutoff=60))
            except (nx.NetworkXError, nx.NodeNotFound):
                result[t] = []
        return result

    def summary(self) -> Dict[str, Any]:
        q_nodes   = self.question_nodes()
        term_nodes = self.termination_nodes()
        cond_qs   = self.conditional_questions()
        return {
            "source":                self.source,
            "questions":             len(q_nodes),
            "edges":                 self.graph.number_of_edges(),
            "termination_points":    len(term_nodes),
            "termination_at":        [n.removeprefix("TERMINATE_") for n in term_nodes],
            "conditional_questions": len(cond_qs),
            "conditional_qids":      sorted(cond_qs),
            "flow_order_first_20":   self.question_flow_order()[:20],
        }


# ── Graph builders ────────────────────────────────────────────────────────────

def build_graph(questions: List[CanonicalQuestion]) -> SurveyFlowGraph:
    """
    Build a SurveyFlowGraph from ANY list of CanonicalQuestion objects.

    The list order defines the sequential flow (topological order of the
    original source).  Routing conditions become edge labels; termination
    rules become TERMINATE branches.

    Works identically for doc, xml, and live sources.
    """
    if not questions:
        return SurveyFlowGraph(source="empty")

    source = questions[0].source
    g = SurveyFlowGraph(source=source)
    prev_qid = "START"

    for q in questions:
        g.add_question(
            q.qid,
            text=q.text[:120] if q.text else "",
            q_type=q.type,
            source=q.source,
            confidence=q.confidence,
            is_mandatory=q.is_mandatory,
        )

        # Determine incoming-edge type: conditional vs sequential
        condition: Optional[str] = None
        edge_type = "sequential"
        if q.routing and q.routing.show_if:
            raw = q.routing.show_if
            if not _is_trivial(raw):
                condition  = _normalize_condition(raw)
                edge_type  = "conditional"

        g.add_route(prev_qid, q.qid, condition=condition, edge_type=edge_type)
        prev_qid = q.qid

        # Termination branches
        for rule in q.termination_rules:
            raw = rule.raw or ""
            if raw:
                cond_str = _extract_term_condition(raw)
            else:
                # Doc-style rule — build condition from answer_codes
                codes = ", ".join(rule.answer_codes) if rule.answer_codes else "?"
                cond_str = f"{q.qid} = {codes}"
            g.add_termination(q.qid, cond_str or rule.source or "unknown")

    g.add_route(prev_qid, "COMPLETE", edge_type="sequential")
    return g


# ── Graph comparison ──────────────────────────────────────────────────────────

@dataclass
class FlowIssue:
    qid: str
    issue_type: str
    severity: str          # HIGH | MEDIUM | LOW
    details: str

    def to_dict(self) -> dict:
        return {
            "qid":      self.qid,
            "type":     self.issue_type,
            "severity": self.severity,
            "details":  self.details,
        }


def compare_graphs(
    ref: SurveyFlowGraph,
    other: SurveyFlowGraph,
) -> List[FlowIssue]:
    """
    Compare *ref* (spec: XML or doc) against *other* (live or another spec).

    Returns a list of FlowIssue covering:
      1. Questions in ref but absent from other (HIGH)
      2. Extra questions in other not in ref (LOW)
      3. Termination points present in ref but missing from other (HIGH)
      4. Termination points in other not in ref (MEDIUM)
      5. Conditional questions in ref with no condition in other (MEDIUM)
    """
    issues: List[FlowIssue] = []
    ref_qs   = set(ref.question_nodes())
    other_qs = set(other.question_nodes())

    # 1. Missing questions
    for qid in sorted(ref_qs - other_qs):
        # Skip internal/framework/stop-word QIDs — same pipeline as PHASE 3
        if should_skip_qid(qid):
            continue
        # Parent-child collapse: S99Datex1 → parent S99; Q11x1 → parent Q11
        parent = get_parent_qid(qid)
        if parent != qid and parent in other_qs:
            continue  # child sub-field whose parent exists in live
        issues.append(FlowIssue(
            qid=qid,
            issue_type="MISSING_IN_OTHER",
            severity="HIGH",
            details=f"In {ref.source} spec but absent from {other.source}",
        ))

    # 2. Extra questions
    for qid in sorted(other_qs - ref_qs):
        if should_skip_qid(qid):
            continue
        issues.append(FlowIssue(
            qid=qid,
            issue_type="EXTRA_IN_OTHER",
            severity="LOW",
            details=f"In {other.source} but not in {ref.source} spec",
        ))

    # 3. & 4. Termination mismatches
    ref_term_at   = {n.removeprefix("TERMINATE_") for n in ref.termination_nodes()}
    other_term_at = {n.removeprefix("TERMINATE_") for n in other.termination_nodes()}

    for qid in sorted(ref_term_at - other_term_at):
        # Try to find the condition from ref
        edge_data = ref.graph.get_edge_data(qid, f"TERMINATE_{qid}") or {}
        cond = edge_data.get("condition", "")
        issues.append(FlowIssue(
            qid=qid,
            issue_type="TERMINATION_MISSING",
            severity="HIGH",
            details=f"Termination at {qid} ({cond}) defined in {ref.source} but not in {other.source}",
        ))
    for qid in sorted(other_term_at - ref_term_at):
        issues.append(FlowIssue(
            qid=qid,
            issue_type="TERMINATION_EXTRA",
            severity="MEDIUM",
            details=f"Termination at {qid} present in {other.source} but not in {ref.source} spec",
        ))

    # 5. Routing-condition coverage
    ref_cond_qs   = set(ref.conditional_questions())
    other_cond_qs = set(other.conditional_questions())
    for qid in sorted(ref_cond_qs - other_cond_qs):
        if qid not in other_qs:
            continue  # already reported as MISSING; don't double-report
        # Retrieve the condition string from ref
        cond_str = ""
        for u, v, d in ref.graph.in_edges(qid, data=True):
            if d.get("edge_type") == "conditional":
                cond_str = d.get("condition") or ""
                break
        issues.append(FlowIssue(
            qid=qid,
            issue_type="ROUTING_UNCHECKED",
            severity="MEDIUM",
            details=f"{qid} has routing '{cond_str}' in {ref.source} but no condition in {other.source}",
        ))

    return issues


# ── Convenience: build from raw source dicts ──────────────────────────────────

def build_doc_graph(doc_questions: dict) -> SurveyFlowGraph:
    """Build graph directly from app.py's doc questions dict."""
    from canonical_model import doc_to_canonical
    return build_graph(doc_to_canonical(doc_questions))


def build_xml_graph(xml_questions: list) -> SurveyFlowGraph:
    """Build graph directly from xml_parser.parse_export() list."""
    from canonical_model import xml_to_canonical
    return build_graph(xml_to_canonical(xml_questions))


def build_live_graph(live_data: dict) -> SurveyFlowGraph:
    """Build graph directly from app.py's live_data dict."""
    from canonical_model import live_to_canonical
    return build_graph(live_to_canonical(live_data))
