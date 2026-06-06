"""
test_runner.py — Execute auto-generated QC test cases against a live survey.

Runs auto_runnable=True test cases using Playwright headless Chromium.
Platform-generic: works for Confirmit, Qualtrics, Decipher, Forsta, and any
survey platform that renders in a browser.

Input:
    test_cases   : list[dict]  from test_generator.py (auto_runnable=True only)
    survey_url   : str
    screenshot_dir: str        directory for .png screenshots
    max_tests    : int         default 20
    timeout_ms   : int         default 30 000
    xml_questions: list[dict]  optional — from xml_parser.py, used to build
                               question presence cache and improve locators

Output per test:
    {
      test_id, qid, type,
      status: "PASS" | "FAIL" | "ERROR" | "SKIP",
      screenshot_path,
      actual_result,
      expected_result,
      duration_ms,
      locator_strategy,    (which strategy found the question)
      locator_diagnostics  (list of LOCATOR_FAIL entries if any)
    }

Never raises — all errors are caught and returned as ERROR status so the
main QC report is never blocked by test-runner failures.
"""

from __future__ import annotations

import os
import re
import time
from difflib import SequenceMatcher
from typing import Optional


# ── Termination / thank-you page indicators (multilingual) ───────────────────
_TERM_TEXT = re.compile(
    r'\b('
    r'thank\s+you|thanks\s+for|merci\s+(de|pour|d\'avoir)|grazie\s+(per|della)|'
    r'gracias\s+(por|de\s+participar)|danke\s+(f[uü]r|sch[oö]n)|bedankt|'
    r'obrigado|obrigada|'
    r'survey\s+is\s+(complete|finished|over|done|closed)|'
    r'questionnaire\s+(is\s+)?(compl[eé]t|terminé|fini|beend|abgeschlossen)|'
    r'sondage\s+(est\s+)?terminé|inchiesta\s+complet|encuesta\s+terminad|'
    r'screen.?out|screened\s+out|quota.?full|quota\s+complet|'
    r'end\s+of\s+(the\s+)?(interview|survey|questionnaire)|'
    r'fin\s+de\s+(l[a\']|du)\s+(sondage|questionnaire|entretien)|'
    r'fine\s+(del|della)\s+(sondaggio|inchiesta)|'
    r'close\s+(this\s+)?(window|tab|browser)|ferme[rz]\s+(cet?te?\s+)?onglet'
    r')\b',
    re.IGNORECASE,
)

# Validation error indicators (multilingual)
_VALID_TEXT = re.compile(
    r'\b('
    r'required|please\s+(answer|select|enter|fill|choose)|'
    r'this\s+(question|field)\s+(is\s+)?required|must\s+(answer|select|enter)|'
    r'answer\s+required|field\s+required|'
    r'r[eé]ponse\s+(requise|obligatoire)|veuillez\s+(r[eé]pondre|choisir|s[eé]lectionner)|'
    r'obligatoire|obbligatorio|obrigatório|pflichtfeld|verplicht|'
    r'bitte\s+(beantworten|ausw[aä]hlen|eingeben)'
    r')\b',
    re.IGNORECASE,
)

# Next-button selectors — tried in order, first visible match wins
_NEXT_SELS = [
    # Confirmit
    '.cf-button-next', 'input.cf-button[type=submit]',
    'button.cf-button:not(.cf-button-back)',
    # Qualtrics
    '#NextButton', 'button#NextButton',
    # Decipher
    'input[value=">>"]', 'a:text-matches("^>>$")',
    # Forsta / generic
    '.q-btn-next', '[class*=next-btn]', '[class*=btn-next]',
    'button:text-matches("^(Next|Suivant|Weiter|Avanti|Siguiente|Volgende|>>)$", "i")',
    'input[type=submit][value*="Next" i]',
    'input[type=button][value*="Next" i]',
    'input[type=submit]', 'button[type=submit]',
    '[class*=next][class*=button]', '[class*=button][class*=next]',
]

# Test Navigator item selectors
_TN_SELS = [
    '.cf-tn-list-item', '.cf-tn-item',
    '[class*=tn-question]', '[class*=tn-item]',
    '.wix-tn-item', '[class*=test-navigator] li',
    '[class*=testnavigator] li',
]

# ── Phase 2: Question container attribute selectors ───────────────────────────
_Q_ATTR_SELS = [
    '[data-questionid="{qid}"]',   # Strategy 1
    '[data-qid="{qid}"]',          # Strategy 2
    '[id="{qid}"]',                # Strategy 3
    '[name="{qid}"]',              # Strategy 4
]

# Generic question wrapper selectors (for strategies 5–7)
_Q_WRAPPER_SELS = [
    '.cf-question-body', '.cf-question',
    '[class*=question-container]', '[class*=QuestionContainer]',
    '[class*=question-body]', '[class*=questionBody]',
    '.q-row', '.q-question',
    '[data-question]', '[data-question-id]',
    'fieldset',
    '[class*=question][class*=row]',
    '[class*=survey-question]',
]

# ── Phase 3: Radio/checkbox selection waterfall ───────────────────────────────
_RADIO_STRATS = [
    # Strategy 1: input value attribute (most reliable)
    'input[type=radio][value="{code}"]',
    'input[type=checkbox][value="{code}"]',
    # Strategy 2: label containing input with matching value
    'label:has(input[value="{code}"])',
    # Strategy 3: ARIA role radio
    '[role=radio][value="{code}"]',
    # Strategy 4: data-code attribute (Confirmit, Forsta)
    '[data-code="{code}"]',
    'input[data-code="{code}"]',
    'label[data-code="{code}"]',
    # Strategy 5: data-answer-id (Decipher)
    '[data-answer-id="{code}"]',
    'input[data-answer-id="{code}"]',
    # Strategy 6: id contains _CODE or ends with CODE
    'input[type=radio][id*="_{code}"]',
    'input[type=radio][id$="{code}"]',
    # Platform-specific variants
    '.f-radio[data-code="{code}"]',           # Forsta
    '.radio-button[data-value="{code}"]',      # Decipher variant
    'li[data-code="{code}"] input',            # Confirmit list items
    'tr[data-code="{code}"] input',            # Confirmit table rows
]

MAX_TESTS   = 20
TIMEOUT_MS  = 30_000
BROWSER_UA  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def _parse_action(action: str) -> tuple[str, str]:
    """'select_code(2)' → ('select_code', '2')."""
    m = re.match(r'(\w+)\(([^)]*)\)', action.strip())
    if m:
        return m.group(1), m.group(2).strip().strip("\"'")
    return action.strip(), ""


def _body_text(page, chars: int = 3000) -> str:
    try:
        return (page.locator("body").inner_text(timeout=3_000) or "")[:chars]
    except Exception:
        return ""


def _screenshot(page, path: str) -> Optional[str]:
    try:
        page.screenshot(path=path, timeout=5_000, full_page=False)
        return path
    except Exception:
        return None


def _click_next(page, timeout: int = 10_000) -> bool:
    """Click the Next button. Returns True if a button was found and clicked."""
    for sel in _NEXT_SELS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=600):
                loc.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


def _is_terminated(page) -> bool:
    """True when the current page looks like a termination / thank-you screen."""
    body = _body_text(page)
    if _TERM_TEXT.search(body):
        return True
    try:
        inputs = page.locator(
            'input[type=radio]:visible, input[type=checkbox]:visible, '
            'input[type=text]:visible, input[type=number]:visible, '
            'textarea:visible, select:visible'
        ).count()
        has_next = any(
            page.locator(s).first.is_visible(timeout=300)
            for s in _NEXT_SELS[:4]
        )
        if inputs == 0 and not has_next:
            return True
    except Exception:
        pass
    return False


def _has_error(page) -> bool:
    """True when a validation error message is visible."""
    body = _body_text(page)
    if _VALID_TEXT.search(body):
        return True
    for sel in [
        '[class*=error]:visible', '[class*=invalid]:visible',
        '[role=alert]:visible', '.cf-error-message:visible',
        '.q-error-message:visible', '[class*=validation]:visible',
    ]:
        try:
            if page.locator(sel).filter(visible=True).count() > 0:
                return True
        except Exception:
            continue
    return False


# ── Phase 1: Locator diagnostics ─────────────────────────────────────────────

def _log_locator_fail(qid: str, selector: str, count: int, visible: int,
                       html_snippet: str = "", page_text_len: int = 0) -> str:
    """Phase 1: Emit a structured LOCATOR_FAIL diagnostic entry."""
    entry = (
        f"LOCATOR_FAIL: qid={qid} selector={selector!r} "
        f"count={count} visible={visible} text_len={page_text_len}"
    )
    if html_snippet:
        entry += f" html={html_snippet[:80]!r}"
    print(entry)
    return entry


# ── Phase 6: Debug screenshot on locator failure ──────────────────────────────

def _screenshot_on_fail(page, qid: str, ss_dir: str) -> Optional[str]:
    """Phase 6: Capture debug_fail_{QID}.png on locator failure."""
    try:
        safe_qid = re.sub(r'[^A-Za-z0-9_]', '_', qid)
        path = os.path.join(ss_dir, f"debug_fail_{safe_qid}.png")
        return _screenshot(page, path)
    except Exception:
        return None


# ── Phase 2: Multi-strategy question container finder ────────────────────────

def _find_question_container(
    page,
    qid: str,
    xml_text: str = "",
    diagnostics: Optional[list] = None,
) -> tuple:
    """
    Phase 2: Seven-strategy waterfall to locate a question's DOM container.

    1. [data-questionid="QID"]
    2. [data-qid="QID"]
    3. [id="QID"]
    4. [name="QID"]
    5. XML question text search (get_by_text, first 6 words)
    6. Navigator item text match
    7. Container fuzzy similarity match (≥80%)

    Returns (locator, strategy_name) or (None, None).
    """
    qid_upper = qid.upper()
    qid_lower = qid.lower()
    body_len  = len(_body_text(page, 500))

    def _fail(sel, cnt=0, vis=0):
        entry = _log_locator_fail(qid, sel, cnt, vis, "", body_len)
        if diagnostics is not None:
            diagnostics.append(entry)

    # ── Strategies 1–4: attribute selectors ──────────────────────────────────
    for tpl in _Q_ATTR_SELS:
        for variant in dict.fromkeys([qid, qid_upper, qid_lower]):
            sel = tpl.format(qid=variant)
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                if cnt > 0:
                    return loc.first, f"attr:{sel}"
                _fail(sel, 0, 0)
            except Exception:
                pass

    # ── Strategy 5: XML question text search ──────────────────────────────────
    if xml_text and len(xml_text.strip()) > 10:
        words = xml_text.strip().split()
        for n_words in (6, 3):
            if len(words) < n_words:
                continue
            phrase = ' '.join(words[:n_words])
            try:
                loc = page.get_by_text(phrase, exact=False)
                if loc.count() > 0 and loc.first.is_visible(timeout=400):
                    return loc.first, f"xml_text:{phrase[:35]}"
            except Exception:
                pass
            _fail(f'get_by_text("{phrase[:30]}")', 0, 0)

    # ── Strategy 6: Navigator item mapping ────────────────────────────────────
    for tn_sel in _TN_SELS:
        try:
            items = page.locator(tn_sel).all()
            for item in items:
                try:
                    txt = (item.inner_text(timeout=400) or "").upper()
                    if qid_upper in txt:
                        return item, f"navigator:{tn_sel}"
                except Exception:
                    continue
        except Exception:
            pass

    # ── Strategy 7: Container fuzzy match ─────────────────────────────────────
    for wrapper_sel in _Q_WRAPPER_SELS:
        try:
            wrappers = page.locator(wrapper_sel).all()
            for w in wrappers[:40]:
                try:
                    inner = (w.inner_text(timeout=300) or "")
                    if qid_upper in inner.upper():
                        return w, f"container_text:{wrapper_sel}"
                    if xml_text and len(xml_text) > 15 and len(inner) > 10:
                        ratio = SequenceMatcher(
                            None,
                            xml_text[:120].lower(),
                            inner[:300].lower()
                        ).ratio()
                        if ratio >= 0.80:
                            return w, f"fuzzy:{wrapper_sel}:{ratio:.2f}"
                except Exception:
                    continue
        except Exception:
            pass

    _fail("all_7_strategies_exhausted", 0, 0)
    return None, None


# ── Phase 3 & 4: Enhanced radio/checkbox selection (no positional fallback) ───

def _select_code_enhanced(
    page,
    code: str,
    container=None,
    qid: str = "",
    diagnostics: Optional[list] = None,
) -> tuple[bool, str]:
    """
    Phase 3 & 4: Select radio/checkbox by answer code using full strategy waterfall.

    Matches by value, data-code, data-answer-id, id suffix, and label text.
    NEVER uses positional index (nth-child) as a selection strategy.
    Returns (success, strategy_description).
    """
    raw = re.sub(r'^[!=<>≤≥≠]+', '', code.strip()).strip()
    if not raw:
        return False, "empty_code"

    scope = container if container is not None else page

    # Strategies 1–8: attribute-based matching
    for tpl in _RADIO_STRATS:
        sel = tpl.format(code=raw)
        try:
            loc = scope.locator(sel).first
            if loc.is_visible(timeout=400):
                loc.click(timeout=5_000, force=True)
                strategy = f"code_attr:{sel}"
                print(f"LOCATOR_OK: qid={qid} Selected code {raw!r} via {strategy}")
                return True, strategy
        except Exception:
            pass

    # Strategy 9: visible label text exactly or closely matching raw code
    for label_tpl in [
        'label:text-is("{code}")',
        'span:text-is("{code}")',
        'td:text-is("{code}")',
        '[class*=label]:text-is("{code}")',
    ]:
        sel = label_tpl.format(code=raw)
        try:
            loc = scope.locator(sel).first
            if loc.is_visible(timeout=300):
                loc.click(timeout=5_000, force=True)
                strategy = f"label_text:{sel}"
                print(f"LOCATOR_OK: qid={qid} Selected code {raw!r} via {strategy}")
                return True, strategy
        except Exception:
            pass

    entry = _log_locator_fail(qid, f"select_code({raw})", 0, 0, "", 0)
    if diagnostics is not None:
        diagnostics.append(entry)
    return False, "not_found"


def _enter_value(page, value: str) -> bool:
    """Clear and type `value` into the first visible numeric/text input."""
    for sel in [
        "input[type=number]:visible",
        "input[type=text]:visible",
        "textarea:visible",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.triple_click(timeout=3_000)
                loc.fill(str(value), timeout=3_000)
                return True
        except Exception:
            continue
    return False


def _select_answer_text(page, text: str) -> bool:
    """Click a radio/option whose visible label matches `text`."""
    for sel in [
        f'input[type=radio][aria-label*="{text}" i]',
        f'label:has-text("{text}")',
        f'span:has-text("{text}")',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click(timeout=5_000)
                return True
        except Exception:
            continue
    return False


def _execute_action(
    page,
    action_type: str,
    action_val: str,
    container=None,
    qid: str = "",
    diagnostics: Optional[list] = None,
) -> tuple[bool, str]:
    """Execute a test action. Returns (success, strategy_used)."""
    if action_type == "select_code":
        return _select_code_enhanced(
            page, action_val, container=container, qid=qid, diagnostics=diagnostics)
    if action_type == "enter_value":
        return _enter_value(page, action_val), "enter_value"
    if action_type == "select_answer_text":
        return _select_answer_text(page, action_val), "select_answer_text"
    if action_type == "submit_without_answer":
        return True, "no_action_required"
    return False, f"unknown_action:{action_type}"


# ── Phase 5: Question presence cache (pre-crawl) ─────────────────────────────

def _build_presence_cache(
    page,
    survey_url: str,
    qids: list,
    xml_text_map: dict,
    timeout: int = 30_000,
) -> dict:
    """
    Phase 5: Walk the survey and record which QIDs are present in the DOM.

    Returns {qid: {"found": bool, "strategy": str|None, "page": int|None}}.
    This cache is shared across all tests to avoid redundant DOM searches.
    """
    cache: dict = {}
    if not qids:
        return cache

    try:
        page.goto(survey_url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        page_num = 0

        while page_num < 60:
            page_num += 1
            body_upper = _body_text(page, 8000).upper()

            for qid in qids:
                if qid in cache:
                    continue
                qid_upper = qid.upper()

                # Fast body-text check
                if qid_upper in body_upper:
                    cache[qid] = {"found": True, "strategy": "body_text", "page": page_num}
                    continue

                # DOM attribute check (strategies 1–4 only; skip fuzzy for speed)
                for tpl in _Q_ATTR_SELS:
                    found = False
                    for variant in dict.fromkeys([qid, qid_upper, qid.lower()]):
                        sel = tpl.format(qid=variant)
                        try:
                            if page.locator(sel).count() > 0:
                                cache[qid] = {
                                    "found": True, "strategy": f"attr:{sel}",
                                    "page": page_num}
                                found = True
                                break
                        except Exception:
                            pass
                    if found:
                        break

            if all(qid in cache for qid in qids):
                break

            if _is_terminated(page):
                break
            if not _click_next(page, timeout=5_000):
                break
            page.wait_for_timeout(1_000)

        for qid in qids:
            if qid not in cache:
                cache[qid] = {"found": False, "strategy": None, "page": None}

        found_n = sum(1 for v in cache.values() if v["found"])
        print(f"[PRESENCE_CACHE] {found_n}/{len(qids)} QIDs found in {page_num} page(s)")

    except Exception as e:
        print(f"[PRESENCE_CACHE] Pre-crawl error: {e}")
        for qid in qids:
            if qid not in cache:
                cache[qid] = {"found": False, "strategy": None, "page": None}

    return cache


# ── Navigation ────────────────────────────────────────────────────────────────

def _navigate_to_qid(
    page,
    qid: str,
    timeout: int,
    xml_text: str = "",
    presence_cache: Optional[dict] = None,
    diagnostics: Optional[list] = None,
) -> tuple[bool, Optional[object]]:
    """
    Navigate to the page containing `qid` and return (found, container_locator).

    Combines page-level navigation (TN click, Next-walk) with DOM container
    discovery (Phase 2 waterfall).
    """
    qid_upper = qid.upper()

    # Phase 5: log cache result upfront
    if presence_cache is not None and qid in presence_cache:
        info = presence_cache[qid]
        if not info.get("found"):
            msg = (f"PRESENCE_CACHE: qid={qid} not located during pre-crawl "
                   f"— may be conditional or not in main flow")
            print(msg)
            if diagnostics is not None:
                diagnostics.append(msg)

    # ── Strategy 1: Test Navigator ────────────────────────────────────────────
    for tn_sel in _TN_SELS:
        try:
            if page.locator(tn_sel).count() > 0:
                items = page.locator(tn_sel).all()
                for item in items:
                    try:
                        txt = (item.inner_text(timeout=500) or "").upper()
                        if qid_upper in txt:
                            item.click(timeout=5_000, force=True)
                            page.wait_for_timeout(1_200)
                            container, strat = _find_question_container(
                                page, qid, xml_text, diagnostics)
                            return True, container
                    except Exception:
                        continue
        except Exception:
            pass

    # ── Strategy 2: Body text scan (already on correct page) ─────────────────
    if qid_upper in _body_text(page).upper():
        container, _ = _find_question_container(page, qid, xml_text, diagnostics)
        return True, container

    # ── Strategy 3: DOM attribute search on current page ─────────────────────
    container, strat = _find_question_container(page, qid, xml_text, diagnostics)
    if container is not None:
        return True, container

    # ── Strategy 4: Walk forward (up to 40 Next clicks) ──────────────────────
    for _ in range(40):
        if _is_terminated(page):
            return False, None
        if not _click_next(page, timeout=5_000):
            return False, None
        page.wait_for_timeout(1_000)

        if qid_upper in _body_text(page).upper():
            container, _ = _find_question_container(page, qid, xml_text, diagnostics)
            return True, container

        container, strat = _find_question_container(page, qid, xml_text, diagnostics)
        if container is not None:
            return True, container

    return False, None


# ── Phase 7: Improved failure result ─────────────────────────────────────────

def _locator_failure_result(
    tc: dict,
    qid: str,
    xml_known: bool,
    diagnostics: list,
    ss_path: Optional[str],
    t0: float,
) -> dict:
    """
    Phase 7: Return a LOCATOR FAILURE result with context instead of bare "Could not find X".
    """
    if xml_known:
        actual = (
            f"LOCATOR FAILURE — question {qid} exists in XML export but "
            "could not be located in the DOM. "
            "Confidence: NEEDS REVIEW — not necessarily a survey bug. "
            "Possible causes: conditional display, different page flow, "
            "or DOM attributes not matching known patterns."
        )
    else:
        actual = (
            f"LOCATOR FAILURE — question {qid} could not be located in the DOM. "
            "QID also absent from XML export. "
            "Confidence: NEEDS REVIEW"
        )
    return {
        "test_id":           tc["test_id"],
        "qid":               qid,
        "type":              tc.get("type", ""),
        "status":            "ERROR",
        "screenshot_path":   ss_path,
        "actual_result":     actual,
        "expected_result":   tc.get("expected", ""),
        "duration_ms":       _ms(t0),
        "notes":             tc.get("notes", ""),
        "locator_strategy":  "all_strategies_failed",
        "locator_diagnostics": diagnostics,
    }


# ── Per-type runners ──────────────────────────────────────────────────────────

def _make_result(tc: dict) -> dict:
    return {
        "test_id":           tc["test_id"],
        "qid":               tc.get("qid", "?"),
        "type":              tc.get("type", ""),
        "status":            "ERROR",
        "screenshot_path":   None,
        "actual_result":     "",
        "expected_result":   tc.get("expected", ""),
        "duration_ms":       0,
        "notes":             tc.get("notes", ""),
        "locator_strategy":  "",
        "locator_diagnostics": [],
    }


def _run_termination(
    page, tc: dict, url: str, ss_dir: str, timeout: int,
    xml_text_map: Optional[dict] = None,
    presence_cache: Optional[dict] = None,
    xml_qid_set: Optional[set] = None,
) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))
    qid = tc["qid"]
    diag: list = []
    xml_text = (xml_text_map or {}).get(qid.upper(), "")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        found, container = _navigate_to_qid(
            page, qid, timeout, xml_text=xml_text,
            presence_cache=presence_cache, diagnostics=diag)

        if not found:
            ss = _screenshot_on_fail(page, qid, ss_dir)
            xml_known = bool(xml_qid_set and qid.upper() in xml_qid_set)
            return _locator_failure_result(tc, qid, xml_known, diag, ss, t0)

        action_ok, action_strat = _execute_action(
            page, action_type, action_val,
            container=container, qid=qid, diagnostics=diag)

        if not action_ok:
            ss = _screenshot_on_fail(page, qid, ss_dir)
            res["actual_result"] = (
                f"LOCATOR FAILURE — could not select code {action_val!r} "
                f"for question {qid}. Tried all {len(_RADIO_STRATS) + 4} strategies. "
                "Confidence: NEEDS REVIEW"
            )
            res["screenshot_path"] = ss
            res["locator_strategy"] = action_strat
            res["locator_diagnostics"] = diag
            res["duration_ms"] = _ms(t0)
            return res

        res["locator_strategy"] = action_strat
        page.wait_for_timeout(400)
        _click_next(page, timeout=timeout)
        page.wait_for_timeout(2_000)

        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}.png"))

        if _is_terminated(page):
            res["status"] = "PASS"
            res["actual_result"] = "termination_page"
        else:
            res["status"] = "FAIL"
            res["actual_result"] = "survey_continues_unexpectedly"

    except Exception as e:
        res["actual_result"] = str(e)[:200]
        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}_err.png"))

    res["locator_diagnostics"] = diag
    res["duration_ms"] = _ms(t0)
    return res


def _run_mandatory(
    page, tc: dict, url: str, ss_dir: str, timeout: int,
    xml_text_map: Optional[dict] = None,
    presence_cache: Optional[dict] = None,
    xml_qid_set: Optional[set] = None,
) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    qid = tc["qid"]
    diag: list = []
    xml_text = (xml_text_map or {}).get(qid.upper(), "")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        found, container = _navigate_to_qid(
            page, qid, timeout, xml_text=xml_text,
            presence_cache=presence_cache, diagnostics=diag)

        if not found:
            ss = _screenshot_on_fail(page, qid, ss_dir)
            xml_known = bool(xml_qid_set and qid.upper() in xml_qid_set)
            return _locator_failure_result(tc, qid, xml_known, diag, ss, t0)

        res["locator_strategy"] = "navigated"
        _click_next(page, timeout=timeout)
        page.wait_for_timeout(1_500)

        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}.png"))

        if _has_error(page):
            res["status"] = "PASS"
            res["actual_result"] = "validation_error_shown"
        else:
            res["status"] = "FAIL"
            res["actual_result"] = "no_validation_error_blank_submission_accepted"

    except Exception as e:
        res["actual_result"] = str(e)[:200]
        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}_err.png"))

    res["locator_diagnostics"] = diag
    res["duration_ms"] = _ms(t0)
    return res


def _run_routing(
    page, tc: dict, url: str, ss_dir: str, timeout: int,
    xml_text_map: Optional[dict] = None,
    presence_cache: Optional[dict] = None,
    xml_qid_set: Optional[set] = None,
) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))
    qid = tc["qid"]
    diag: list = []
    xml_text = (xml_text_map or {}).get(qid.upper(), "")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        found, container = _navigate_to_qid(
            page, qid, timeout, xml_text=xml_text,
            presence_cache=presence_cache, diagnostics=diag)

        if not found:
            ss = _screenshot_on_fail(page, qid, ss_dir)
            xml_known = bool(xml_qid_set and qid.upper() in xml_qid_set)
            return _locator_failure_result(tc, qid, xml_known, diag, ss, t0)

        action_ok, action_strat = _execute_action(
            page, action_type, action_val,
            container=container, qid=qid, diagnostics=diag)
        res["locator_strategy"] = action_strat

        page.wait_for_timeout(400)
        _click_next(page, timeout=timeout)
        page.wait_for_timeout(2_000)

        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}.png"))

        terminated = _is_terminated(page)
        expected   = tc.get("expected", "")

        if "continues" in expected or "navigate_to" in expected:
            if not terminated:
                res["status"] = "PASS"
                res["actual_result"] = "survey_continues"
            else:
                res["status"] = "FAIL"
                res["actual_result"] = "unexpected_termination"
        elif "termination" in expected:
            if terminated:
                res["status"] = "PASS"
                res["actual_result"] = "termination_page"
            else:
                res["status"] = "FAIL"
                res["actual_result"] = "survey_continues_unexpectedly"
        else:
            res["status"] = "PASS"
            res["actual_result"] = "terminated" if terminated else "survey_continues"

    except Exception as e:
        res["actual_result"] = str(e)[:200]
        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}_err.png"))

    res["locator_diagnostics"] = diag
    res["duration_ms"] = _ms(t0)
    return res


def _run_range(
    page, tc: dict, url: str, ss_dir: str, timeout: int,
    xml_text_map: Optional[dict] = None,
    presence_cache: Optional[dict] = None,
    xml_qid_set: Optional[set] = None,
) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))
    qid = tc["qid"]
    diag: list = []
    xml_text = (xml_text_map or {}).get(qid.upper(), "")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        found, container = _navigate_to_qid(
            page, qid, timeout, xml_text=xml_text,
            presence_cache=presence_cache, diagnostics=diag)

        if not found:
            ss = _screenshot_on_fail(page, qid, ss_dir)
            xml_known = bool(xml_qid_set and qid.upper() in xml_qid_set)
            return _locator_failure_result(tc, qid, xml_known, diag, ss, t0)

        res["locator_strategy"] = "navigated"

        if action_type == "enter_value":
            _enter_value(page, action_val)
            page.wait_for_timeout(300)

        _click_next(page, timeout=timeout)
        page.wait_for_timeout(1_500)

        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}.png"))

        expected = tc.get("expected", "")
        error_shown = _has_error(page)

        if expected == "validation_block":
            res["status"] = "PASS" if error_shown     else "FAIL"
            res["actual_result"] = (
                "out_of_range_blocked" if error_shown
                else "out_of_range_accepted_unexpectedly"
            )
        elif expected == "accept_value":
            res["status"] = "PASS" if not error_shown else "FAIL"
            res["actual_result"] = (
                "boundary_value_accepted" if not error_shown
                else "boundary_value_blocked_unexpectedly"
            )
        else:
            res["status"] = "PASS"
            res["actual_result"] = "error_shown" if error_shown else "no_error"

    except Exception as e:
        res["actual_result"] = str(e)[:200]
        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}_err.png"))

    res["locator_diagnostics"] = diag
    res["duration_ms"] = _ms(t0)
    return res


# ── Main entry point ──────────────────────────────────────────────────────────

_RUNNERS = {
    "TERMINATION": _run_termination,
    "MANDATORY":   _run_mandatory,
    "ROUTING":     _run_routing,
    "RANGE":       _run_range,
}


def run_playwright_tests(
    test_cases: list,
    survey_url: str,
    screenshot_dir: str,
    max_tests: int = MAX_TESTS,
    timeout_ms: int = TIMEOUT_MS,
    xml_questions: Optional[list] = None,
) -> dict:
    """
    Execute auto_runnable test cases against the live survey.

    Phases implemented:
      Phase 1 — Locator diagnostics logged to stdout + returned in result dicts
      Phase 2 — 7-strategy question container waterfall in _navigate_to_qid
      Phase 3 — Radio button waterfall in _select_code_enhanced
      Phase 4 — Value-based code matching only (no positional index)
      Phase 5 — Presence cache built via pre-crawl before tests run
      Phase 6 — debug_fail_{QID}.png screenshots on locator failure
      Phase 7 — LOCATOR FAILURE messages instead of bare "Could not find X"

    Never raises — all failures are captured in individual result dicts.

    Returns:
        {
          "results":  [result_dict, ...],
          "summary":  {"total", "passed", "failed", "errors", "skipped",
                       "locator_failures", "pass_rate"},
          "error":    None | str
        }
    """
    outcome: dict = {"results": [], "summary": {}, "error": None}

    if not survey_url:
        outcome["error"] = "No survey URL"
        _fill_summary([], outcome)
        return outcome

    runnable = [
        tc for tc in (test_cases or [])
        if tc.get("auto_runnable") and tc.get("type") not in ("GRID",)
    ][:max_tests]

    if not runnable:
        _fill_summary([], outcome)
        return outcome

    try:
        os.makedirs(screenshot_dir, exist_ok=True)
    except Exception as e:
        outcome["error"] = f"Cannot create screenshot dir: {e}"
        _fill_summary([], outcome)
        return outcome

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        outcome["error"] = (
            "Playwright not installed. "
            "Run: pip install playwright && playwright install chromium"
        )
        _fill_summary([], outcome)
        return outcome

    # Build XML lookup maps for Phase 5 + Phase 2 text search
    xml_text_map: dict = {}
    xml_qid_set: set   = set()
    if xml_questions:
        for xq in xml_questions:
            raw_qid = xq.get("qid") or xq.get("qid_normalized") or ""
            if raw_qid:
                key = raw_qid.upper()
                xml_text_map[key] = xq.get("text", "")
                xml_qid_set.add(key)

    results: list = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                slow_mo=100,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            # ── Phase 5: Presence cache via pre-crawl ─────────────────────────
            presence_cache: dict = {}
            unique_qids = list(dict.fromkeys(tc["qid"] for tc in runnable))
            if unique_qids:
                print(f"[PRESENCE_CACHE] Pre-crawling {survey_url[:60]} "
                      f"for {len(unique_qids)} QID(s)…")
                cache_ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=BROWSER_UA,
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                cache_page = cache_ctx.new_page()
                cache_page.set_default_timeout(timeout_ms)
                cache_page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                )
                try:
                    presence_cache = _build_presence_cache(
                        cache_page, survey_url, unique_qids,
                        xml_text_map, timeout=timeout_ms)
                except Exception as ce:
                    print(f"[PRESENCE_CACHE] Pre-crawl failed (non-fatal): {ce}")
                finally:
                    try:
                        cache_ctx.close()
                    except Exception:
                        pass

            # ── Run tests (fresh context per test) ────────────────────────────
            for tc in runnable:
                runner = _RUNNERS.get(tc.get("type", ""))
                if not runner:
                    results.append({
                        **_make_result(tc),
                        "status": "SKIP",
                        "actual_result": f"No runner for type {tc.get('type')}",
                    })
                    continue

                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=BROWSER_UA,
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                )

                try:
                    r = runner(
                        page, tc, survey_url, screenshot_dir, timeout_ms,
                        xml_text_map=xml_text_map,
                        presence_cache=presence_cache,
                        xml_qid_set=xml_qid_set,
                    )
                except Exception as exc:
                    r = {
                        **_make_result(tc),
                        "actual_result": f"Runner exception: {str(exc)[:200]}",
                        "locator_diagnostics": [],
                    }
                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass

                results.append(r)

            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        outcome["error"] = f"Playwright engine error: {str(e)[:300]}"

    outcome["results"] = results
    _fill_summary(results, outcome)
    return outcome


def _fill_summary(results: list, outcome: dict) -> None:
    total    = len(results)
    passed   = sum(1 for r in results if r.get("status") == "PASS")
    failed   = sum(1 for r in results if r.get("status") == "FAIL")
    errors   = sum(1 for r in results if r.get("status") == "ERROR")
    skipped  = sum(1 for r in results if r.get("status") == "SKIP")
    loc_fail = sum(
        1 for r in results
        if "LOCATOR FAILURE" in (r.get("actual_result") or "")
    )
    outcome["summary"] = {
        "total":            total,
        "passed":           passed,
        "failed":           failed,
        "errors":           errors,
        "skipped":          skipped,
        "locator_failures": loc_fail,
        "pass_rate": f"{int(100 * passed / total)}%" if total > 0 else "N/A",
    }
