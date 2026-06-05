"""
test_generator.py — Auto-generate QC test cases from survey spec logic.

Parses routing/logic text from LOGIC tables, RANGE rows, TYPE rows, and
question definitions → produces a set of actionable test cases.

Test types:
  TERMINATION  — select a code/answer → expect termination page
  ROUTING      — select a code/answer → expect next question visible/hidden
  MANDATORY    — submit without answer → expect validation error
  RANGE        — enter boundary value → expect block or acceptance
  GRID         — render-check for matrix/grid questions (manual)

Languages handled: English, French, German, Italian, Spanish (generic patterns).

Output per test case:
  {
    test_id:        "TC001",
    qid:            "R0",
    type:           "TERMINATION",
    action:         "select_code(4)",
    expected:       "termination_page",
    condition_text: "If code 4 : thanks and close",
    priority:       "HIGH",
    auto_runnable:  True,
    source:         "logic_table",
    notes:          "",
  }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─── Termination keyword pattern (multilingual) ──────────────────────────────
_TERM_KW = re.compile(
    r'\b('
    r'thanks?\s+and\s+close|thank\s+and\s+close|thanks?\s*,?\s*and\s*close|'
    r'close|fermer|chiudi|cerrar|schlie[ßs]en|'
    r'screen\s*out|screened?\s*out|screenout|'
    r'terminate|terminer|terminare|terminar|beenden|'
    r'quota\s*full|quota\s+complet|'
    r'thank\s+you|merci|grazie|gracias|danke|bedankt|obrigado'
    r')\b',
    re.IGNORECASE,
)

# Condition keywords that mean "stay/continue"
_CONTINUE_KW = re.compile(
    r'\b(continue|go\s+to|aller|weiter|continua|continuar|siguiente|screen\s+\d+)\b',
    re.IGNORECASE,
)

# QID pattern — matches R0, S1bis, Q14, SPE, S2
_QID_RE = re.compile(r'\b([A-Za-z][A-Za-z0-9_.]*(?:bis|ter|info|ex)?)\b', re.IGNORECASE)


# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    test_id: str
    qid: str
    type: str                  # TERMINATION | ROUTING | MANDATORY | RANGE
    action: str                # e.g. "select_code(4)", "enter_value(0)"
    expected: str              # e.g. "termination_page", "validation_error"
    condition_text: str = ""   # raw condition snippet for traceability
    priority: str = "HIGH"
    auto_runnable: bool = True
    source: str = "logic_table"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _is_termination(text: str) -> bool:
    return bool(_TERM_KW.search(text))


def _is_continue(text: str) -> bool:
    return bool(_CONTINUE_KW.search(text))


def _get_qid_from_table(lt: dict) -> str:
    """Extract the primary QID for a logic table.

    Priority: PROGRAMMING TABLE row → host_qid field.
    """
    for row in lt.get("rows", []):
        if len(row) >= 2 and re.match(r'PROGRAMM?ING\s+TABLE', row[0], re.I):
            raw = row[1].strip().rstrip('.')
            # Clean up: take first token that looks like a QID
            m = re.match(r'([A-Za-z][A-Za-z0-9_.]*(?:bis|ter|info|ex)?)', raw, re.I)
            if m:
                return m.group(1)
    return lt.get("host_qid") or ""


def _get_row_text(lt: dict, label: str) -> str:
    """Return the text content of the first row matching `label`."""
    pat = re.compile(r'^\s*' + re.escape(label) + r'\s*$', re.I)
    for row in lt.get("rows", []):
        if row and pat.match(row[0]):
            return " ".join(row[1:]).strip()
    return ""


def _get_type_text(lt: dict) -> str:
    return _get_row_text(lt, "TYPE")


def _get_range_text(lt: dict) -> str:
    """Return RANGE row content, e.g. 'MIN: 0 / MAX: 60'."""
    for row in lt.get("rows", []):
        if row and re.match(r'RANGE', row[0], re.I):
            return " ".join(row[1:]).strip()
    return ""


def _get_logic_text(lt: dict) -> str:
    """
    Return the logic/condition text from the table.
    Prefers the LOGIC row (which has the actual conditions) over a ROUTING/
    ROUTINE row (which often just says "All Respondents").
    Falls back to flat_text when no structured LOGIC row exists.
    """
    logic_text = ""
    fallback_text = ""
    for row in lt.get("rows", []):
        if not row:
            continue
        if re.match(r'LOGIC\b', row[0], re.I) and len(row) > 1:
            logic_text = " ".join(row[1:]).strip()
        elif re.match(r'(ROUTING|ROUTINE)\b', row[0], re.I) and len(row) > 1:
            val = " ".join(row[1:]).strip()
            # Skip generic "All Respondents" routing rows
            if not re.match(r'^all\s+respondents?$', val, re.I):
                fallback_text = val
    return logic_text or fallback_text


# ─── Pattern parsers ─────────────────────────────────────────────────────────

def _parse_if_code_clauses(logic_text: str) -> list[dict]:
    # Parse: If code N [optional label in quotes] : ACTION
    #   or:  If code N [label] then ACTION
    # Handles real survey formats in EN/FR.
    results = []
    # Quote chars: ASCII " and curly variants as literals (avoid embedding them)
    _q = '""''"'
    clause_re = re.compile(
        r'(?:if|si)\s+code\s+(\d+)'
        + r'(?:\s*[' + re.escape(_q) + r'][^' + re.escape(_q) + r']{0,40}[' + re.escape(_q) + r'])?'
        + r'\s*(?:[:' + '–—' + r']|then)\s*'
        + r'([^.]+?)'
        + r'(?=(?:if|si)\s+code\s+\d+|\Z)',
        re.IGNORECASE | re.DOTALL,
    )
    for m in clause_re.finditer(logic_text):
        code = m.group(1)
        action_text = m.group(2).strip()
        results.append({
            "code": code,
            "action_text": action_text,
            "is_term": _is_termination(action_text),
            "is_continue": _is_continue(action_text),
        })
    return results


def _parse_inverted_term(logic_text: str) -> list[dict]:
    """
    Parse 'thanks and close if QID=N' (inverted format).
    Real examples:
      'thanks and close if R0=2'
      'THANKS, AND CLOSE IF S1=999'
    Returns list of {qid, code, condition_text}.
    """
    results = []
    pat = re.compile(
        r'\b(?:' + _TERM_KW.pattern + r')\b[^if]*if\s+(\w[\w.]*)\s*=\s*(\d+)',
        re.IGNORECASE,
    )
    for m in pat.finditer(logic_text):
        qid = m.group(m.lastindex - 1) if m.lastindex and m.lastindex > 1 else ""
        code = m.group(m.lastindex)
        # Simpler direct extract
        simple = re.search(
            r'(?:thanks|close|thank|merci|fermer|grazie|terminate)\b.*?if\s+(\w[\w.]*)\s*=\s*(\d+)',
            logic_text, re.I,
        )
        if simple:
            results.append({"qid": simple.group(1), "code": simple.group(2),
                             "condition_text": logic_text.strip()[:120]})
            break
    return results


def _parse_inequality_term(logic_text: str) -> list[dict]:
    """
    Parse 'IF QID ≠ N  close' or 'IF QID != N close'.
    Real example: 'IF SPE ≠ 22  Thank and close'
    Returns list of {qid, op, value, condition_text}.
    """
    results = []
    pat = re.compile(
        r'if\s+(\w+)\s*([≠!=<>]+)\s*(\d+)\s+(?:' + _TERM_KW.pattern.lstrip(r'\b(').rstrip(r')\b') + r')',
        re.IGNORECASE,
    )
    # Simplified: look for IF QID [operator] NUMBER then term keyword
    simple = re.compile(
        r'if\s+([A-Za-z]\w*)\s*([≠!=<>]+)\s*(\d+)',
        re.IGNORECASE,
    )
    for m in simple.finditer(logic_text):
        remainder = logic_text[m.end():].strip()
        if _is_termination(remainder[:60]):
            results.append({
                "qid": m.group(1),
                "op": m.group(2).strip(),
                "value": m.group(3),
                "condition_text": logic_text.strip()[:120],
            })
    return results


def _parse_range_term(logic_text: str) -> list[dict]:
    """
    Parse 'IF QID < N or QID > N  close'.
    Real example: 'IF S2 < 2 or S2 > 40    Thank and close'
    Returns {qid, low, high, condition_text}.
    """
    # Pattern: IF QID < N or QID > N
    pat = re.compile(
        r'if\s+(\w+)\s*<\s*(\d+)\s+(?:or|ou|oder|o)\s+\w+\s*>\s*(\d+)',
        re.IGNORECASE,
    )
    m = pat.search(logic_text)
    if m and _is_termination(logic_text[m.end():m.end() + 80]):
        return [{"qid": m.group(1), "low": m.group(2), "high": m.group(3),
                 "condition_text": logic_text.strip()[:120]}]

    # Pattern: IF QID > N or QID < N  (reversed)
    pat2 = re.compile(
        r'if\s+(\w+)\s*>\s*(\d+)\s+(?:or|ou|oder|o)\s+\w+\s*<\s*(\d+)',
        re.IGNORECASE,
    )
    m2 = pat2.search(logic_text)
    if m2 and _is_termination(logic_text[m2.end():m2.end() + 80]):
        return [{"qid": m2.group(1), "low": m2.group(3), "high": m2.group(2),
                 "condition_text": logic_text.strip()[:120]}]

    return []


def _parse_no_text_term(logic_text: str, qid: str) -> list[dict]:
    """
    Parse 'IF NO thank and close' / 'IF at least one NO close'.
    These use text answers rather than numeric codes.
    Returns list of {text_answer, condition_text}.
    """
    results = []
    pat = re.compile(
        r'if\s+(?:at\s+least\s+one\s+)?([a-z]+(?:\s+[a-z]+)?)\s+' +
        r'(?:' + _TERM_KW.pattern.lstrip(r'\b(').rstrip(r')\b') + r')',
        re.IGNORECASE,
    )
    m = pat.search(logic_text)
    if m:
        answer = m.group(1).strip().upper()
        # Only accept YES/NO type answers
        if re.match(r'^(NO|NON|NEIN|NO|OUI|YES|JA|SI)$', answer, re.I):
            results.append({
                "text_answer": answer,
                "condition_text": logic_text.strip()[:120],
            })
    return results


def _parse_range_row(range_text: str) -> Optional[tuple[float, float]]:
    """Parse 'MIN: 0 / MAX: 60' → (0.0, 60.0) or None."""
    pat = re.compile(r'MIN\s*[:\s]+(-?[\d.]+)\s*/\s*MAX\s*[:\s]+(-?[\d.]+)', re.I)
    m = pat.search(range_text)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return None


def _parse_routing_clauses(logic_text: str) -> list[dict]:
    """
    Parse 'If code N : go to screen X' routing clauses.
    Real example: 'If code 1 : Go to screen 2, then screen 3'
    Returns list of {code, target_text}.
    """
    results = []
    clause_re = re.compile(
        r'(?:if|si)\s+code\s+(\d+)(?:\s*["""][^""""]*["""])?\s*[:–—]\s*([^.]+?)(?=(?:if|si)\s+code|\Z)',
        re.IGNORECASE | re.DOTALL,
    )
    for m in clause_re.finditer(logic_text):
        code = m.group(1)
        action = m.group(2).strip()
        if _is_continue(action) and not _is_termination(action):
            # Extract target screen/QID
            screen_m = re.search(r'screen\s+(\d+)', action, re.I)
            skip_m = re.search(r'(?:skip|go|aller)\s+to\s+([A-Za-z0-9]+)', action, re.I)
            target = ""
            if screen_m:
                target = f"screen_{screen_m.group(1)}"
            elif skip_m:
                target = skip_m.group(1)
            results.append({
                "code": code,
                "target": target,
                "action_text": action[:80],
            })
    return results


# ─── Main TestGenerator class ─────────────────────────────────────────────────

class TestGenerator:
    """
    Parse survey spec → produce actionable test cases.
    Handles English, French, German, Italian, Spanish routing text.
    """

    def __init__(self):
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"TC{self._counter:03d}"

    def _tc(self, **kwargs) -> TestCase:
        return TestCase(test_id=self._next_id(), **kwargs)

    # ── Termination from logic table ─────────────────────────────────────────

    def _tests_from_if_code(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate tests from 'If code N : close/continue' patterns."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        clauses = _parse_if_code_clauses(logic)
        tests = []
        for c in clauses:
            if c["is_term"]:
                tests.append(self._tc(
                    qid=qid,
                    type="TERMINATION",
                    action=f"select_code({c['code']})",
                    expected="termination_page",
                    condition_text=f"code {c['code']}: {c['action_text'][:60]}",
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                ))
            elif c["is_continue"]:
                # Routing test: selecting this code keeps survey alive
                tests.append(self._tc(
                    qid=qid,
                    type="ROUTING",
                    action=f"select_code({c['code']})",
                    expected="survey_continues",
                    condition_text=f"code {c['code']}: {c['action_text'][:60]}",
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                ))
        return tests

    def _tests_from_inverted(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate tests from 'close if QID=N' (inverted format)."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        matches = _parse_inverted_term(logic)
        tests = []
        for m in matches:
            tqid = m["qid"] if m["qid"] else qid
            tests.append(self._tc(
                qid=tqid,
                type="TERMINATION",
                action=f"select_code({m['code']})",
                expected="termination_page",
                condition_text=m["condition_text"],
                priority="HIGH",
                auto_runnable=True,
                source="logic_table",
            ))
        return tests

    def _tests_from_inequality(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate tests from 'IF QID ≠ N close', 'IF QID <= N close', etc."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        matches = _parse_inequality_term(logic)
        tests = []
        for m in matches:
            tqid = m["qid"] if m["qid"] else qid
            op = m["op"].strip()
            val = m["value"]
            val_i = int(val)

            # ≠ / != → selecting val continues; anything else terminates
            if op in ("≠", "!="):
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"select_code(!={val})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=False,
                    source="logic_table",
                    notes=f"Select any code that is NOT {val}",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="ROUTING",
                    action=f"select_code({val})",
                    expected="survey_continues",
                    condition_text=m["condition_text"],
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Code {val} should NOT terminate",
                ))

            # = → this specific code terminates
            elif op == "=":
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"select_code({val})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                ))

            # < → value below threshold terminates; value at threshold continues
            elif op == "<":
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i - 1})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i - 1} is below threshold {val}",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="ROUTING",
                    action=f"enter_value({val_i})",
                    expected="survey_continues",
                    condition_text=m["condition_text"],
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val} is at threshold — should continue",
                ))

            # <= → value at or below threshold terminates; value above continues
            elif op == "<=":
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val} is at threshold — should terminate",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i - 1})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i - 1} is below threshold {val}",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="ROUTING",
                    action=f"enter_value({val_i + 1})",
                    expected="survey_continues",
                    condition_text=m["condition_text"],
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i + 1} exceeds threshold — should continue",
                ))

            # > → value above threshold terminates; value at threshold continues
            elif op == ">":
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i + 1})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i + 1} exceeds threshold {val}",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="ROUTING",
                    action=f"enter_value({val_i})",
                    expected="survey_continues",
                    condition_text=m["condition_text"],
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val} is at threshold — should continue",
                ))

            # >= → value at or above threshold terminates; value below continues
            elif op == ">=":
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val} is at threshold — should terminate",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="TERMINATION",
                    action=f"enter_value({val_i + 1})",
                    expected="termination_page",
                    condition_text=m["condition_text"],
                    priority="HIGH",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i + 1} exceeds threshold {val}",
                ))
                tests.append(self._tc(
                    qid=tqid,
                    type="ROUTING",
                    action=f"enter_value({val_i - 1})",
                    expected="survey_continues",
                    condition_text=m["condition_text"],
                    priority="MEDIUM",
                    auto_runnable=True,
                    source="logic_table",
                    notes=f"Value {val_i - 1} is below threshold — should continue",
                ))

        return tests

    def _tests_from_range_condition(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate tests from 'IF QID < N or QID > N close'."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        matches = _parse_range_term(logic)
        tests = []
        for m in matches:
            tqid = m["qid"] if m["qid"] else qid
            low = int(m["low"])
            high = int(m["high"])
            # Below minimum → termination
            tests.append(self._tc(
                qid=tqid,
                type="TERMINATION",
                action=f"enter_value({low - 1})",
                expected="termination_page",
                condition_text=m["condition_text"],
                priority="HIGH",
                auto_runnable=True,
                source="logic_table",
                notes=f"Value {low - 1} is below threshold {low}",
            ))
            # Above maximum → termination
            tests.append(self._tc(
                qid=tqid,
                type="TERMINATION",
                action=f"enter_value({high + 1})",
                expected="termination_page",
                condition_text=m["condition_text"],
                priority="HIGH",
                auto_runnable=True,
                source="logic_table",
                notes=f"Value {high + 1} is above threshold {high}",
            ))
            # Boundary valid → survey continues
            tests.append(self._tc(
                qid=tqid,
                type="ROUTING",
                action=f"enter_value({low})",
                expected="survey_continues",
                condition_text=m["condition_text"],
                priority="MEDIUM",
                auto_runnable=True,
                source="logic_table",
                notes=f"Value {low} is exactly at lower boundary — should continue",
            ))
            tests.append(self._tc(
                qid=tqid,
                type="ROUTING",
                action=f"enter_value({high})",
                expected="survey_continues",
                condition_text=m["condition_text"],
                priority="MEDIUM",
                auto_runnable=True,
                source="logic_table",
                notes=f"Value {high} is exactly at upper boundary — should continue",
            ))
        return tests

    def _tests_from_no_text(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate tests from 'IF NO close'."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        matches = _parse_no_text_term(logic, qid)
        tests = []
        for m in matches:
            answer = m["text_answer"]
            tests.append(self._tc(
                qid=qid,
                type="TERMINATION",
                action=f"select_answer_text(\"{answer}\")",
                expected="termination_page",
                condition_text=m["condition_text"],
                priority="HIGH",
                auto_runnable=True,
                source="logic_table",
                notes=f"Select the '{answer}' option",
            ))
        return tests

    def _tests_from_routing(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate routing tests from 'If code N : go to screen X'."""
        logic = _get_logic_text(lt)
        if not logic:
            return []
        routing = _parse_routing_clauses(logic)
        tests = []
        for r in routing:
            target = r["target"] or "next_screen"
            tests.append(self._tc(
                qid=qid,
                type="ROUTING",
                action=f"select_code({r['code']})",
                expected=f"navigate_to({target})",
                condition_text=r["action_text"],
                priority="MEDIUM",
                auto_runnable=False,  # screen numbers don't map directly to QIDs
                source="logic_table",
                notes=f"Verify {target} is reached after selecting code {r['code']}",
            ))
        return tests

    # ── RANGE row tests ───────────────────────────────────────────────────────

    def _tests_from_range_row(self, lt: dict, qid: str) -> list[TestCase]:
        """Generate boundary tests from RANGE row (MIN/MAX)."""
        range_text = _get_range_text(lt)
        if not range_text:
            return []
        bounds = _parse_range_row(range_text)
        if not bounds:
            return []
        lo, hi = bounds
        tests = []
        # Below minimum
        below = lo - 1
        tests.append(self._tc(
            qid=qid,
            type="RANGE",
            action=f"enter_value({int(below) if below == int(below) else below})",
            expected="validation_block",
            condition_text=f"RANGE {range_text}",
            priority="HIGH",
            auto_runnable=True,
            source="range_row",
            notes=f"Value below MIN={lo} should be blocked",
        ))
        # Above maximum
        above = hi + 1
        tests.append(self._tc(
            qid=qid,
            type="RANGE",
            action=f"enter_value({int(above) if above == int(above) else above})",
            expected="validation_block",
            condition_text=f"RANGE {range_text}",
            priority="HIGH",
            auto_runnable=True,
            source="range_row",
            notes=f"Value above MAX={hi} should be blocked",
        ))
        # Valid values at boundaries
        tests.append(self._tc(
            qid=qid,
            type="RANGE",
            action=f"enter_value({int(lo) if lo == int(lo) else lo})",
            expected="accept_value",
            condition_text=f"RANGE {range_text}",
            priority="MEDIUM",
            auto_runnable=True,
            source="range_row",
            notes=f"MIN value {lo} should be accepted",
        ))
        tests.append(self._tc(
            qid=qid,
            type="RANGE",
            action=f"enter_value({int(hi) if hi == int(hi) else hi})",
            expected="accept_value",
            condition_text=f"RANGE {range_text}",
            priority="MEDIUM",
            auto_runnable=True,
            source="range_row",
            notes=f"MAX value {hi} should be accepted",
        ))
        return tests

    # ── Mandatory tests ───────────────────────────────────────────────────────

    def _tests_from_mandatory(self, qid: str, type_text: str) -> list[TestCase]:
        """Generate mandatory test: submit without answer → block."""
        return [self._tc(
            qid=qid,
            type="MANDATORY",
            action="submit_without_answer()",
            expected="validation_error",
            condition_text=f"TYPE: {type_text}",
            priority="HIGH",
            auto_runnable=True,
            source="type_row",
            notes="Question is mandatory — blank submission must be blocked",
        )]

    # ── Per-table dispatcher ──────────────────────────────────────────────────

    def _tests_from_table(self, lt: dict) -> list[TestCase]:
        """Dispatch all parsers against one logic table."""
        qid = _get_qid_from_table(lt)
        if not qid:
            return []

        logic = _get_logic_text(lt)
        type_text = _get_type_text(lt)
        tests: list[TestCase] = []

        # ── Mandatory from TYPE row ─────────────────────────────────────────
        if re.search(r'\bmandatory\b|\bobligato\w*\b|\bobligatoire\b|\b\*\b', type_text, re.I):
            tests.extend(self._tests_from_mandatory(qid, type_text))

        if not logic:
            # Still try range row even without logic text
            tests.extend(self._tests_from_range_row(lt, qid))
            return tests

        # Determine which parsers to try based on logic text content
        has_if_code = bool(re.search(r'(?:if|si)\s+code\s+\d+', logic, re.I))
        has_inverted = bool(re.search(r'(?:thanks?|close|terminate|merci|fermer)\b.*?if\s+\w', logic, re.I))
        has_inequality = bool(re.search(r'if\s+\w+\s*[≠!=<>]', logic, re.I))
        has_range_cond = bool(re.search(r'if\s+\w+\s*<\s*\d+\s+(?:or|ou|oder)', logic, re.I) or
                              re.search(r'if\s+\w+\s*>\s*\d+\s+(?:or|ou|oder)', logic, re.I))
        has_no_text = bool(re.search(r'if\s+(?:at\s+least\s+one\s+)?(?:no|non|nein)\b', logic, re.I))

        if has_if_code:
            tests.extend(self._tests_from_if_code(lt, qid))
            # if_code parser covers routing too, don't double-add
        else:
            if has_inverted:
                tests.extend(self._tests_from_inverted(lt, qid))
            if has_range_cond:
                tests.extend(self._tests_from_range_condition(lt, qid))
            # Run inequality parser independently — a table can have both a range
            # condition (IF X < 2 or X > 40) AND an inequality (IF SPE ≠ 22).
            if has_inequality:
                tests.extend(self._tests_from_inequality(lt, qid))
            if has_no_text:
                tests.extend(self._tests_from_no_text(lt, qid))

        # RANGE row tests are always attempted (independent of logic text)
        tests.extend(self._tests_from_range_row(lt, qid))

        # Deduplicate (same qid + action combination)
        seen: set[tuple] = set()
        unique: list[TestCase] = []
        for t in tests:
            key = (t.qid, t.action, t.expected)
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique

    # ── Mandatory / GRID tests from doc questions ─────────────────────────────

    def _tests_from_questions(self, questions: dict) -> list[TestCase]:
        """Generate mandatory and GRID render tests for questions not covered by logic tables."""
        tests = []
        for qid, q in questions.items():
            if q.get("is_mandatory"):
                tests.append(self._tc(
                    qid=qid,
                    type="MANDATORY",
                    action="submit_without_answer()",
                    expected="validation_error",
                    condition_text="is_mandatory=True (from doc)",
                    priority="HIGH",
                    auto_runnable=True,
                    source="question_definition",
                    notes="Question marked mandatory — blank submission must be blocked",
                ))

            # GRID / MATRIX questions: verify the grid renders completely.
            # The grid itself doesn't have routing logic, so the logic-table
            # parsers never produce a test for it.  Generate a render-check test
            # so testers know to manually verify all rows × columns appear.
            qt = (q.get("question_type") or "").upper()
            if qt in ("GRID", "MATRIX") or "MATRIX" in qt:
                opts = q.get("options", [])
                row_count = len(opts) if opts else "?"
                tests.append(self._tc(
                    qid=qid,
                    type="GRID",
                    action="render_check()",
                    expected="all_rows_and_columns_visible",
                    condition_text=f"question_type={qt} ({row_count} rows in doc)",
                    priority="MEDIUM",
                    auto_runnable=False,
                    source="question_definition",
                    notes=(
                        f"Verify all {row_count} matrix row(s) and all column headers "
                        "render correctly. Check radio/checkbox exists for every cell."
                    ),
                ))
        return tests

    # ── Top-level entry point ─────────────────────────────────────────────────

    def generate(self, doc_data: dict) -> list[dict]:
        """
        Generate all test cases from doc_data (output of parse_document()).
        Returns list of test case dicts, ordered by test_id.
        """
        self._counter = 0
        all_tests: list[TestCase] = []
        logic_covered_qids: set[str] = set()

        logic_tables = doc_data.get("logic_tables", [])
        questions = doc_data.get("questions", {})

        # ── [TERM DEBUG] checkpoint 4b: test_generator received ───────────────
        _dbg_q_rules = sum(len(q.get("termination_rules", [])) for q in questions.values())
        print(f"[TERM DEBUG] test_generator received {len(logic_tables)} logic_tables, {_dbg_q_rules} termination_rules across {len(questions)} questions")
        for _dbg_lt in logic_tables[:5]:
            _dbg_logic = _dbg_lt.get("flat_text", "")[:80]
            print(f"[TERM DEBUG]   lt host={_dbg_lt.get('host_qid')!r}: {_dbg_logic!r}")
        # ─────────────────────────────────────────────────────────────────────

        # ── From logic tables ─────────────────────────────────────────────────
        seen_global: set[tuple] = set()
        for lt in logic_tables:
            tc_list = self._tests_from_table(lt)
            for tc in tc_list:
                key = (tc.qid, tc.action, tc.expected)
                if key not in seen_global:
                    seen_global.add(key)
                    all_tests.append(tc)
                    logic_covered_qids.add(tc.qid)

        # ── From question definitions (mandatory not covered by logic tables) ──
        for tc in self._tests_from_questions(questions):
            key = (tc.qid, tc.action, tc.expected)
            if tc.qid not in logic_covered_qids and key not in seen_global:
                seen_global.add(key)
                all_tests.append(tc)

        # Re-number sequentially after deduplication
        for i, tc in enumerate(all_tests, 1):
            tc.test_id = f"TC{i:03d}"

        return [tc.to_dict() for tc in all_tests]


# ─── Convenience summary ─────────────────────────────────────────────────────

def summarise(test_cases: list[dict]) -> dict:
    """Return a summary dict for logging/report."""
    total = len(test_cases)
    auto = sum(1 for tc in test_cases if tc.get("auto_runnable"))
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for tc in test_cases:
        by_type[tc.get("type", "UNKNOWN")] = by_type.get(tc.get("type", "UNKNOWN"), 0) + 1
        by_priority[tc.get("priority", "MEDIUM")] = by_priority.get(tc.get("priority", "MEDIUM"), 0) + 1
    return {
        "total": total,
        "auto_runnable": auto,
        "manual_only": total - auto,
        "by_type": by_type,
        "by_priority": by_priority,
    }


# ─── Module-level convenience function ───────────────────────────────────────

def generate_test_cases(doc_data: dict) -> tuple[list[dict], dict]:
    """Generate test cases + summary from parse_document() output."""
    gen = TestGenerator()
    cases = gen.generate(doc_data)
    summary = summarise(cases)
    return cases, summary
