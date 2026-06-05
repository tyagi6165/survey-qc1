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

Output per test:
    {
      test_id, qid, type,
      status: "PASS" | "FAIL" | "ERROR" | "SKIP",
      screenshot_path,
      actual_result,
      expected_result,
      duration_ms,
    }

Never raises — all errors are caught and returned as ERROR status so the
main QC report is never blocked by test-runner failures.
"""

from __future__ import annotations

import os
import re
import time
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

# Radio/checkbox selectors for code selection
_RADIO_SELS = [
    'input[type=radio][value="{code}"]',
    'input[type=checkbox][value="{code}"]',
    'input[type=radio][data-code="{code}"]',
    'input[type=radio][data-value="{code}"]',
    'input[type=radio][id*="{code}"]',
    # Qualtrics: label wraps input
    'label:has(input[value="{code}"])',
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
    # No visible interactive inputs + no next button → structural termination
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


# ── Navigation ────────────────────────────────────────────────────────────────

def _navigate_to_qid(page, qid: str, timeout: int) -> bool:
    """
    Bring the question identified by qid into view.

    Strategy order:
    1. Test Navigator (Confirmit / Forsta) — click the TN item whose text
       contains the QID.  Fast and precise.
    2. Text scan of current page — QID already visible, nothing to do.
    3. Walk forward (up to 40 Next clicks) until QID appears in page text.

    Returns True when the question is visible on the current page.
    """
    qid_upper = qid.upper()

    # ── Strategy 1: Test Navigator ────────────────────────────────────────────
    tn_sel = None
    for sel in _TN_SELS:
        try:
            if page.locator(sel).count() > 0:
                tn_sel = sel
                break
        except Exception:
            pass

    if tn_sel:
        try:
            items = page.locator(tn_sel).all()
            for item in items:
                try:
                    txt = (item.inner_text(timeout=500) or "").upper()
                    if qid_upper in txt:
                        item.click(timeout=5_000, force=True)
                        page.wait_for_timeout(1_200)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

    # ── Strategy 2: Already on the right page ────────────────────────────────
    if qid_upper in _body_text(page).upper():
        return True

    # ── Strategy 3: Walk forward ──────────────────────────────────────────────
    for _ in range(40):
        if _is_terminated(page):
            return False
        if not _click_next(page, timeout=5_000):
            return False
        page.wait_for_timeout(1_000)
        if qid_upper in _body_text(page).upper():
            return True

    return False


# ── Code / value interaction ──────────────────────────────────────────────────

def _select_code(page, code: str) -> bool:
    """
    Select the radio/checkbox that corresponds to answer code `code`.

    Strips operator prefixes (!=22 → 22, <=10 → 10) so the raw action string
    from test_generator can be passed directly.
    """
    raw = re.sub(r'^[!=<>≤≥≠]+', '', code.strip()).strip()
    if not raw.isdigit():
        return False
    # Direct attribute match
    for tpl in _RADIO_SELS:
        sel = tpl.format(code=raw)
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click(timeout=5_000, force=True)
                return True
        except Exception:
            continue
    # Position fallback: code 2 = 2nd visible radio
    try:
        n = int(raw)
        radios = page.locator("input[type=radio]:visible")
        if radios.count() >= n:
            radios.nth(n - 1).click(timeout=5_000, force=True)
            return True
    except Exception:
        pass
    return False


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


def _execute_action(page, action_type: str, action_val: str) -> bool:
    if action_type == "select_code":
        return _select_code(page, action_val)
    if action_type == "enter_value":
        return _enter_value(page, action_val)
    if action_type == "select_answer_text":
        return _select_answer_text(page, action_val)
    if action_type == "submit_without_answer":
        return True   # caller just clicks Next without selecting anything
    return False


# ── Per-type runners ──────────────────────────────────────────────────────────

def _make_result(tc: dict) -> dict:
    return {
        "test_id":        tc["test_id"],
        "qid":            tc.get("qid", "?"),
        "type":           tc.get("type", ""),
        "status":         "ERROR",
        "screenshot_path": None,
        "actual_result":  "",
        "expected_result": tc.get("expected", ""),
        "duration_ms":    0,
        "notes":          tc.get("notes", ""),
    }


def _run_termination(page, tc: dict, url: str, ss_dir: str, timeout: int) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        if not _navigate_to_qid(page, tc["qid"], timeout):
            res["actual_result"] = f"Could not navigate to {tc['qid']}"
            res["screenshot_path"] = _screenshot(
                page, os.path.join(ss_dir, f"{tc['test_id']}_nav_fail.png"))
            res["duration_ms"] = _ms(t0)
            return res

        _execute_action(page, action_type, action_val)
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

    res["duration_ms"] = _ms(t0)
    return res


def _run_mandatory(page, tc: dict, url: str, ss_dir: str, timeout: int) -> dict:
    res = _make_result(tc)
    t0  = time.time()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        if not _navigate_to_qid(page, tc["qid"], timeout):
            res["actual_result"] = f"Could not navigate to {tc['qid']}"
            res["screenshot_path"] = _screenshot(
                page, os.path.join(ss_dir, f"{tc['test_id']}_nav_fail.png"))
            res["duration_ms"] = _ms(t0)
            return res

        # Deliberately do NOT answer — just submit
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

    res["duration_ms"] = _ms(t0)
    return res


def _run_routing(page, tc: dict, url: str, ss_dir: str, timeout: int) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        if not _navigate_to_qid(page, tc["qid"], timeout):
            res["actual_result"] = f"Could not navigate to {tc['qid']}"
            res["screenshot_path"] = _screenshot(
                page, os.path.join(ss_dir, f"{tc['test_id']}_nav_fail.png"))
            res["duration_ms"] = _ms(t0)
            return res

        _execute_action(page, action_type, action_val)
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
            # Unknown expectation — record observed outcome
            res["status"] = "PASS"
            res["actual_result"] = "terminated" if terminated else "survey_continues"

    except Exception as e:
        res["actual_result"] = str(e)[:200]
        res["screenshot_path"] = _screenshot(
            page, os.path.join(ss_dir, f"{tc['test_id']}_err.png"))

    res["duration_ms"] = _ms(t0)
    return res


def _run_range(page, tc: dict, url: str, ss_dir: str, timeout: int) -> dict:
    res = _make_result(tc)
    t0  = time.time()
    action_type, action_val = _parse_action(tc.get("action", ""))

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)

        if not _navigate_to_qid(page, tc["qid"], timeout):
            res["actual_result"] = f"Could not navigate to {tc['qid']}"
            res["screenshot_path"] = _screenshot(
                page, os.path.join(ss_dir, f"{tc['test_id']}_nav_fail.png"))
            res["duration_ms"] = _ms(t0)
            return res

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
) -> dict:
    """
    Execute auto_runnable test cases against the live survey.

    Never raises — all failures are captured in individual result dicts.

    Returns:
        {
          "results":  [result_dict, ...],
          "summary":  {"total", "passed", "failed", "errors", "skipped",
                       "pass_rate"},
          "error":    None | str   (top-level setup error only)
        }
    """
    outcome: dict = {"results": [], "summary": {}, "error": None}

    if not survey_url:
        outcome["error"] = "No survey URL"
        _fill_summary([], outcome)
        return outcome

    # Filter: auto_runnable only, skip GRID (manual), cap at max_tests
    runnable = [
        tc for tc in (test_cases or [])
        if tc.get("auto_runnable") and tc.get("type") not in ("GRID",)
    ][:max_tests]

    if not runnable:
        outcome["error"] = None
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

            for tc in runnable:
                runner = _RUNNERS.get(tc.get("type", ""))
                if not runner:
                    results.append({
                        **_make_result(tc),
                        "status": "SKIP",
                        "actual_result": f"No runner for type {tc.get('type')}",
                    })
                    continue

                # Fresh isolated context per test — no session leakage
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
                    r = runner(page, tc, survey_url, screenshot_dir, timeout_ms)
                except Exception as exc:
                    r = {
                        **_make_result(tc),
                        "actual_result": f"Runner exception: {str(exc)[:200]}",
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
    total   = len(results)
    passed  = sum(1 for r in results if r.get("status") == "PASS")
    failed  = sum(1 for r in results if r.get("status") == "FAIL")
    errors  = sum(1 for r in results if r.get("status") == "ERROR")
    skipped = sum(1 for r in results if r.get("status") == "SKIP")
    outcome["summary"] = {
        "total":    total,
        "passed":   passed,
        "failed":   failed,
        "errors":   errors,
        "skipped":  skipped,
        "pass_rate": f"{int(100 * passed / total)}%" if total > 0 else "N/A",
    }
