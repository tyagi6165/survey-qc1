"""
================================================================
  qc_engine.py — v8.1 Complete QC Engine
================================================================
8 checks in Phase 1:
  1. Termination rules (AI-powered, batched)
  2. Missing words/keywords
  3. Question text match (doc vs live)
  4. Options text match
  5. Mandatory markers
  6. Piping markers (raw unresolved)
  7. Answer codes sequence
  8. Question order

Works with ANY language. URL required for checks 2-4.
"""

import re
import json
from difflib import SequenceMatcher
from docx import Document
from docx.oxml.ns import qn


# ============================================================
# CONSTANTS
# ============================================================
QID_PATTERN = re.compile(
    r'\[?\s*([RSQ]\d+(?:bis|ter|Info|info|Ex)?)\s*[\.\-–—\s\]]',
    re.IGNORECASE
)

PIPING_PATTERNS = [
    r'\[PIPE[^\]]*\]',
    r'\{\{[^}]+\}\}',
    r'<pipe[^>]*>',
    r'\[PIPED[^\]]*\]',
    r'\[Q\d+\.[A-Z]\]',
    r'\[Q\d+\]',
]
PIPING_RE = re.compile('|'.join(PIPING_PATTERNS), re.IGNORECASE)

MANDATORY_RE = re.compile(
    r'\*\s*$|\bmandatory\b|\bobligation\b|\bobbligatorio\b|\bobligatoire\b|\berforderlich\b',
    re.IGNORECASE
)

TERMINATION_KEYWORDS = [
    'close', 'terminate', 'thank', 'thanks', 'screen out',
    'grazie', 'chiudi', 'gracias', 'cierre', 'merci', 'fermer',
    'vielen dank', 'beenden', 'qualify', 'continue',
]

SKIP_PATTERNS = re.compile(
    r'^\s*(PROGRAMMING\s+TABLE|\[NEXT SCREEN\]|\[SAME SCREEN|---+|\|\s*---|\|\s*$)',
    re.IGNORECASE
)

STOPWORDS = {
    # English
    'the','and','or','but','is','are','was','were','be','been','have','has',
    'had','do','does','did','will','would','could','a','an','of','in','on',
    'at','to','for','with','by','from','this','that','these','those','it',
    'its','i','you','he','she','we','they','me','him','her','us','them',
    'my','your','his','their','our','all','any','some','not','so','if','as',
    # French
    'le','la','les','un','une','des','de','du','au','aux','et','ou','mais',
    'donc','car','ni','que','qui','quoi','dont','ou','je','tu','il','elle',
    'nous','vous','ils','elles','ce','se','sa','son','ses','mon','ton','ma',
    # Italian
    'il','lo','gli','un','una','di','da','del','della','dei','delle','degli',
    'al','alla','ai','alle','con','su','per','tra','fra','che','chi','come',
    'quando','dove','non','anche','ancora','sempre','molto','poco',
    # Spanish
    'el','los','las','una','unos','unas','del','al','con','por','para',
    'sin','sobre','entre','hacia','desde','hasta','durante','mediante',
}


# ============================================================
# DOCUMENT PARSER
# ============================================================
def _extract_table_options(question, rows):
    """Extract answer options from a doc table row list into a question dict."""
    for r in rows:
        if len(r) < 2:
            continue
        # Format 1: [code, text, marker?] — first cell is a purely numeric code
        if re.match(r'^\d+$', r[0]):
            code, text = r[0], r[1]
        # Format 2: [text, code] — exactly two cells, last is numeric, first is not
        elif len(r) == 2 and re.match(r'^\d+$', r[-1]) and not re.match(r'^\d+$', r[0]):
            code, text = r[-1], r[0]
        else:
            continue
        if text and code not in question["raw_codes"]:
            question["options"].append({"code": code, "text": text})
            question["raw_codes"].append(code)


def parse_document(doc_path):
    """
    Parse document → extract questions, options, logic tables.
    Returns: dict
    """
    doc = Document(doc_path)
    questions = {}
    logic_tables = []
    current_qid = None
    qid_order = []
    _pending_opts = None  # last option table seen, for PROG TABLE look-back

    # Walk body in order
    for child in doc.element.body.iterchildren():
        if child.tag == qn('w:p'):
            for para in doc.paragraphs:
                if para._element is child:
                    text = para.text.strip()
                    if not text or SKIP_PATTERNS.search(text):
                        break
                    m = QID_PATTERN.match(text)
                    if m:
                        qid = m.group(1)
                        current_qid = qid
                        if qid not in questions:
                            questions[qid] = {
                                "text": "", "options": [],
                                "is_mandatory": False, "has_piping": False,
                                "piping_found": [], "raw_codes": [],
                            }
                            qid_order.append(qid)
                        rest = text[m.end():].strip()
                        rest = re.sub(r'^[\-–—\s]+', '', rest).strip()
                        if rest:
                            questions[qid]["text"] += " " + rest
                    elif current_qid:
                        opt = re.match(r'^(\d+)[\.\)]\s+(.+)', text)
                        if opt:
                            questions[current_qid]["options"].append({
                                "code": opt.group(1),
                                "text": opt.group(2).strip()
                            })
                            questions[current_qid]["raw_codes"].append(opt.group(1))
                        else:
                            questions[current_qid]["text"] += " " + text
                            # Check mandatory
                            if MANDATORY_RE.search(text):
                                questions[current_qid]["is_mandatory"] = True
                            # Check piping
                            pipes = PIPING_RE.findall(text)
                            if pipes:
                                questions[current_qid]["has_piping"] = True
                                questions[current_qid]["piping_found"].extend(pipes)
                    break

        elif child.tag == qn('w:tbl'):
            for tbl in doc.tables:
                if tbl._element is child:
                    rows = []
                    for row in tbl.rows:
                        row_text = []
                        for cell in row.cells:
                            ct = ' '.join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                            if ct:
                                row_text.append(ct)
                        if row_text:
                            rows.append(row_text)

                    flat = ' | '.join(' / '.join(r) for r in rows)

                    # Is this a LOGIC table with termination?
                    has_logic = bool(re.search(r'\b(LOGIC|ROUTING|ROUTINE)\b', flat, re.I))
                    has_term = any(kw in flat.lower() for kw in TERMINATION_KEYWORDS)

                    if has_logic and has_term:
                        logic_tables.append({
                            'host_qid': current_qid,
                            'flat_text': flat,
                            'rows': rows
                        })
                        _pending_opts = None
                    elif rows:
                        first_cell = rows[0][0] if rows[0] else ''
                        is_prog = bool(re.match(r'PROGRAMM?ING\s+TABLE', first_cell, re.I))

                        if is_prog:
                            # Extract QID from second cell of first row
                            prog_qid = None
                            if len(rows[0]) >= 2:
                                m = re.match(
                                    r'([RSQ]\d+(?:bis|ter|Info|info|Ex)?)',
                                    rows[0][1].strip(), re.I
                                )
                                if m:
                                    prog_qid = m.group(1)
                            if prog_qid:
                                if prog_qid not in questions:
                                    questions[prog_qid] = {
                                        "text": "", "options": [],
                                        "is_mandatory": False, "has_piping": False,
                                        "piping_found": [], "raw_codes": [],
                                    }
                                    qid_order.append(prog_qid)
                                current_qid = prog_qid
                                # Check TYPE row for mandatory marker
                                for r in rows[1:5]:
                                    if r and r[0].strip().upper() == 'TYPE' and len(r) >= 2:
                                        if MANDATORY_RE.search(r[1]):
                                            questions[prog_qid]["is_mandatory"] = True
                                # Assign buffered options if this question has none yet
                                if _pending_opts and not questions[prog_qid]["options"]:
                                    _extract_table_options(questions[prog_qid], _pending_opts)
                            _pending_opts = None
                        else:
                            # Potential answer-option table
                            if current_qid and current_qid in questions:
                                # Only assign if the question has no options yet — prevents
                                # a following question's option table being appended here
                                # when current_qid hasn't advanced past its own PROG TABLE yet.
                                if not questions[current_qid]["options"]:
                                    _extract_table_options(questions[current_qid], rows)
                            # Always buffer for the next PROG TABLE (handles the case where
                            # the option table appears before the PROG TABLE that names the QID)
                            _pending_opts = rows
                    break

    # Clean up texts
    for qid in questions:
        questions[qid]["text"] = re.sub(r'\s+', ' ', questions[qid]["text"]).strip()

    return {
        "questions": questions,
        "qid_order": qid_order,
        "logic_tables": logic_tables,
        "stats": {
            "total_questions": len(questions),
            "with_options": sum(1 for q in questions.values() if q["options"]),
            "with_piping": sum(1 for q in questions.values() if q["has_piping"]),
            "with_mandatory": sum(1 for q in questions.values() if q["is_mandatory"]),
            "logic_tables": len(logic_tables),
        }
    }


# ============================================================
# CHECK 1: TERMINATION RULES (AI-powered, BATCHED)
# ============================================================
def check_termination(logic_tables, gemini_model):
    """
    Extract ALL termination rules using ONE batched AI call.
    Avoids quota issues by sending all tables at once.
    """
    if not logic_tables:
        return [], {"status": "no_logic_tables"}

    # Build batched prompt
    tables_text = ""
    for i, t in enumerate(logic_tables, 1):
        tables_text += f"\n--- TABLE {i} (QID: {t['host_qid']}) ---\n{t['flat_text'][:500]}\n"

    prompt = f"""You are a survey QC expert. Extract ALL termination rules from these {len(logic_tables)} LOGIC tables.

{tables_text}

RULES:
- Language can be ANY (English, French, Italian, Spanish, etc.)
- "thanks and close", "thank and close", "merci et fermer", "grazie e chiudi" = TERMINATE
- If multiple codes close in ONE table, create SEPARATE rule for EACH code
- "If code 1: continue, If code 2: close" → only code 2 terminates
- Compound = involves NOT/AND/OR or cross-question reference

Return ONLY this JSON (no markdown):
{{
  "rules": [
    {{
      "table_num": 1,
      "test_qid": "R0",
      "answer_code": "1",
      "action": "terminate",
      "complexity": "simple",
      "reason": "code 1 → thanks and close"
    }}
  ]
}}"""

    try:
        response = gemini_model.generate_content(prompt)
        text = response.text.strip()
        # Clean any markdown or thinking tags
        text = re.sub(r'```json|```', '', text).strip()
        text = re.sub(r'<[^>]+>.*?</[^>]+>', '', text, flags=re.DOTALL).strip()
        # Extract JSON
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            rules = data.get("rules", [])
            return rules, {"status": "ok", "total": len(rules)}
        return [], {"status": "json_parse_error", "raw": text[:200]}
    except Exception as e:
        return [], {"status": "error", "message": str(e)}


# ============================================================
# CHECK 2: MISSING WORDS (Doc vs Live)
# ============================================================
def check_missing_words(doc_questions, live_questions):
    """
    For each question, find words in doc that are missing from live.
    """
    issues = []

    for qid, doc_q in doc_questions.items():
        if qid not in live_questions:
            continue

        doc_words = _tokenize(doc_q["text"])
        live_words = set(_tokenize(live_questions[qid].get("text", "")))

        missing = [w for w in doc_words if w not in live_words]
        # Deduplicate
        missing = list(dict.fromkeys(missing))

        if missing:
            issues.append({
                "qid": qid,
                "check": "MISSING_WORDS",
                "severity": "HIGH" if len(missing) >= 3 else "MEDIUM",
                "details": f"Words in doc but not in live: {missing[:10]}",
                "doc_snippet": doc_q["text"][:100],
                "live_snippet": live_questions[qid].get("text", "")[:100],
            })

    return issues


# ============================================================
# CHECK 3: QUESTION TEXT MATCH
# ============================================================
def check_question_text(doc_questions, live_questions, threshold=0.65):
    """
    Fuzzy match question text between doc and live.
    """
    issues = []

    for qid, doc_q in doc_questions.items():
        if qid not in live_questions:
            issues.append({
                "qid": qid,
                "check": "QUESTION_MISSING_IN_LIVE",
                "severity": "HIGH",
                "details": f"Question {qid} found in doc but NOT in live survey",
            })
            continue

        doc_text = doc_q["text"]
        live_text = live_questions[qid].get("text", "")

        if not doc_text or len(doc_text) < 10:
            continue

        ratio = SequenceMatcher(None,
            _normalize(doc_text)[:300],
            _normalize(live_text)[:300]
        ).ratio()

        if ratio < threshold:
            issues.append({
                "qid": qid,
                "check": "TEXT_MISMATCH",
                "severity": "HIGH" if ratio < 0.4 else "MEDIUM",
                "details": f"Match: {int(ratio*100)}% (threshold: {int(threshold*100)}%)",
                "doc_snippet": doc_text[:120],
                "live_snippet": live_text[:120],
            })

    return issues


# ============================================================
# CHECK 4: OPTIONS MATCH
# ============================================================
def check_options(doc_questions, live_questions):
    """
    Check if all answer options from doc appear in live survey.
    """
    issues = []

    for qid, doc_q in doc_questions.items():
        doc_opts = doc_q.get("options", [])
        if not doc_opts:
            continue
        if qid not in live_questions:
            continue

        live_opts = live_questions[qid].get("options", [])
        live_texts = [_normalize(o.get("text", "")) for o in live_opts]
        live_combined = " | ".join(live_texts)

        missing_opts = []
        for opt in doc_opts:
            opt_norm = _normalize(opt["text"])
            if len(opt_norm) < 3:
                continue
            # Check fuzzy match against any live option
            found = any(
                SequenceMatcher(None, opt_norm, lt).ratio() > 0.70
                for lt in live_texts
            )
            if not found and opt_norm not in live_combined:
                missing_opts.append(f"[{opt['code']}] {opt['text'][:50]}")

        if missing_opts:
            issues.append({
                "qid": qid,
                "check": "OPTIONS_MISSING",
                "severity": "HIGH",
                "details": f"Options in doc but not in live: {missing_opts[:5]}",
            })

    return issues


# ============================================================
# CHECK 5: MANDATORY MARKERS
# ============================================================
def check_mandatory(doc_questions, live_questions=None):
    """
    Check mandatory questions have * marker in live.
    If no live data, report mandatory questions found in doc.
    """
    issues = []

    for qid, doc_q in doc_questions.items():
        if not doc_q.get("is_mandatory"):
            continue

        if live_questions is None:
            # Doc-only mode: just report what's mandatory
            issues.append({
                "qid": qid,
                "check": "MANDATORY_CHECK",
                "severity": "INFO",
                "details": f"Marked mandatory in doc — verify * marker in live survey",
            })
        else:
            live_q = live_questions.get(qid, {})
            if not live_q.get("has_mandatory_marker"):
                issues.append({
                    "qid": qid,
                    "check": "MANDATORY_MARKER_MISSING",
                    "severity": "MEDIUM",
                    "details": "Doc says mandatory but live survey has no * marker",
                })

    return issues


# ============================================================
# CHECK 6: PIPING MARKERS
# ============================================================
def check_piping(doc_questions, live_questions=None):
    """
    Check for raw unresolved piping markers in live survey.
    Doc piping = expected. Live piping = BUG.
    """
    issues = []

    # Check doc for piping expectations
    doc_piping_qids = {
        qid for qid, q in doc_questions.items()
        if q.get("has_piping")
    }

    if live_questions:
        for qid, live_q in live_questions.items():
            if live_q.get("has_raw_piping"):
                raw = live_q.get("raw_piping_found", [])
                issues.append({
                    "qid": qid,
                    "check": "PIPING_NOT_RESOLVED",
                    "severity": "HIGH",
                    "details": f"Raw piping markers visible in live survey: {raw[:5]}",
                })
    else:
        # Doc-only: check if doc has piping expectations
        for qid in doc_piping_qids:
            pipes = doc_questions[qid].get("piping_found", [])
            issues.append({
                "qid": qid,
                "check": "PIPING_EXPECTED",
                "severity": "INFO",
                "details": f"Doc has piping markers {pipes[:3]} — verify resolved in live",
            })

    return issues


# ============================================================
# CHECK 7: ANSWER CODES SEQUENCE
# ============================================================
def check_answer_codes(doc_questions):
    """
    Verify answer codes are sequential (1,2,3,4...) without gaps or duplicates.
    """
    issues = []

    for qid, q in doc_questions.items():
        codes = q.get("raw_codes", [])
        if len(codes) < 2:
            continue

        try:
            int_codes = [int(c) for c in codes]
        except ValueError:
            continue

        # Check sequential
        expected = list(range(int_codes[0], int_codes[0] + len(int_codes)))
        if int_codes != expected:
            # Find gaps or duplicates
            gaps = [e for e in expected if e not in int_codes]
            dupes = [c for c in int_codes if int_codes.count(c) > 1]

            detail = []
            if gaps:
                detail.append(f"Missing codes: {gaps}")
            if dupes:
                detail.append(f"Duplicate codes: {list(set(dupes))}")

            if detail:
                issues.append({
                    "qid": qid,
                    "check": "CODE_SEQUENCE_ERROR",
                    "severity": "MEDIUM",
                    "details": " | ".join(detail),
                    "found_codes": int_codes,
                })

    return issues


# ============================================================
# CHECK 8: QUESTION ORDER
# ============================================================
def check_question_order(doc_qid_order, live_questions=None):
    """
    Verify questions appear in same order in doc and live.
    """
    issues = []

    if live_questions is None:
        # Doc-only: check doc order is logical (R before S, numbered sequence)
        r_questions = [q for q in doc_qid_order if q.startswith('R')]
        s_questions = [q for q in doc_qid_order if q.startswith('S')]

        # Extract numbers
        def get_num(qid):
            m = re.search(r'\d+', qid)
            return int(m.group()) if m else 0

        r_nums = [get_num(q) for q in r_questions]
        s_nums = [get_num(q) for q in s_questions]

        # Check R questions are in order
        for i in range(1, len(r_nums)):
            if r_nums[i] < r_nums[i-1]:
                issues.append({
                    "qid": r_questions[i],
                    "check": "ORDER_ISSUE",
                    "severity": "MEDIUM",
                    "details": f"{r_questions[i]} appears after {r_questions[i-1]} — unexpected order",
                })

        # Check S questions are in order
        for i in range(1, len(s_nums)):
            if s_nums[i] < s_nums[i-1]:
                issues.append({
                    "qid": s_questions[i],
                    "check": "ORDER_ISSUE",
                    "severity": "MEDIUM",
                    "details": f"{s_questions[i]} appears after {s_questions[i-1]} — unexpected order",
                })
    else:
        # Compare doc order vs live order
        live_order = list(live_questions.keys())
        doc_in_live = [q for q in doc_qid_order if q in live_questions]

        for i, qid in enumerate(doc_in_live):
            if qid in live_order:
                live_pos = live_order.index(qid)
                doc_pos = i
                # Check if relative order is maintained
                if i > 0:
                    prev_qid = doc_in_live[i-1]
                    if prev_qid in live_order:
                        prev_live_pos = live_order.index(prev_qid)
                        if live_pos < prev_live_pos:
                            issues.append({
                                "qid": qid,
                                "check": "ORDER_MISMATCH",
                                "severity": "MEDIUM",
                                "details": f"Doc order: {prev_qid}→{qid}, but live order is reversed",
                            })

    return issues


# ============================================================
# MASTER RUN — ALL 8 CHECKS
# ============================================================
def run_all_checks(doc_data, live_questions=None, gemini_model=None, threshold=0.65):
    """
    Run all 8 QC checks. Returns structured results.

    Args:
        doc_data: output from parse_document()
        live_questions: dict of live survey data (optional, from crawler)
        gemini_model: Gemini model instance (for termination check)
        threshold: fuzzy match threshold

    Returns:
        dict with all check results + summary
    """
    results = {
        "termination": {"rules": [], "meta": {}},
        "missing_words": [],
        "text_match": [],
        "options_match": [],
        "mandatory": [],
        "piping": [],
        "answer_codes": [],
        "question_order": [],
    }

    doc_questions = doc_data["questions"]
    qid_order = doc_data["qid_order"]
    logic_tables = doc_data["logic_tables"]

    # CHECK 1: Termination (AI, batched)
    if gemini_model and logic_tables:
        rules, meta = check_termination(logic_tables, gemini_model)
        results["termination"] = {"rules": rules, "meta": meta}

    # CHECK 2: Missing words (needs live)
    if live_questions:
        results["missing_words"] = check_missing_words(doc_questions, live_questions)

    # CHECK 3: Text match (needs live)
    if live_questions:
        results["text_match"] = check_question_text(doc_questions, live_questions, threshold)

    # CHECK 4: Options match (needs live)
    if live_questions:
        results["options_match"] = check_options(doc_questions, live_questions)

    # CHECK 5: Mandatory (doc-only or with live)
    results["mandatory"] = check_mandatory(doc_questions, live_questions)

    # CHECK 6: Piping (doc-only or with live)
    results["piping"] = check_piping(doc_questions, live_questions)

    # CHECK 7: Answer codes (doc-only)
    results["answer_codes"] = check_answer_codes(doc_questions)

    # CHECK 8: Question order (doc-only or with live)
    results["question_order"] = check_question_order(qid_order, live_questions)

    # SUMMARY
    all_issues = (
        results["missing_words"] +
        results["text_match"] +
        results["options_match"] +
        results["mandatory"] +
        results["piping"] +
        results["answer_codes"] +
        results["question_order"]
    )

    term_rules = results["termination"]["rules"]
    terminate_rules = [r for r in term_rules if r.get("action") == "terminate"]
    simple_rules = [r for r in terminate_rules if r.get("complexity") == "simple"]
    compound_rules = [r for r in terminate_rules if r.get("complexity") == "compound"]

    sev = {"HIGH": 0, "MEDIUM": 0, "INFO": 0}
    for issue in all_issues:
        s = issue.get("severity", "INFO")
        sev[s] = sev.get(s, 0) + 1

    results["summary"] = {
        "total_issues": len(all_issues),
        "severity": sev,
        "termination_rules_found": len(term_rules),
        "terminate_simple": len(simple_rules),
        "terminate_compound": len(compound_rules),
        "checks_run": sum([
            1 if gemini_model else 0,  # termination
            1 if live_questions else 0,  # missing words
            1 if live_questions else 0,  # text match
            1 if live_questions else 0,  # options
            1,  # mandatory
            1,  # piping
            1,  # codes
            1,  # order
        ]),
        "mode": "full" if live_questions else "doc_only",
    }

    # Verdict
    if sev["HIGH"] == 0 and len(compound_rules) == 0:
        results["summary"]["verdict"] = "PASS"
        results["summary"]["verdict_msg"] = "✅ No critical issues found"
    elif sev["HIGH"] > 0:
        results["summary"]["verdict"] = "FAIL"
        results["summary"]["verdict_msg"] = f"❌ {sev['HIGH']} HIGH severity issues found"
    else:
        results["summary"]["verdict"] = "REVIEW"
        results["summary"]["verdict_msg"] = f"⚠️ {sev['MEDIUM']} issues need review"

    return results


# ============================================================
# HELPERS
# ============================================================
def _normalize(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text).strip().lower()
    text = re.sub(r"[''`\"\"']", "'", text)
    return text


def _tokenize(text):
    if not text:
        return []
    text = re.sub(r'\[[^\]]{1,30}\]|\{\{[^}]+\}\}|<[^>]+>|https?://\S+', ' ', text)
    text = text.lower()
    words = re.findall(r"[a-zàèéìòùáíóúüâêîôûñçäöüßœæ']+", text)
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]
