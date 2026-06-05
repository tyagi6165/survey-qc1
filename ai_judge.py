"""
ai_judge.py — Layer 3 of the 3-layer QC decision stack.

Architecture:
  L1  rule_engine.py   — deterministic rules, confidence 85-95  → FINAL (AI cannot override)
  L2  embeddings.py    — semantic similarity                     → FINAL when score ≥ 0.85
  L3  ai_judge.py      — reviews UNCERTAIN issues only           ← this file

Contract:
  - AI Judge NEVER overrides a rule engine FAIL with confidence ≥ CERTAINTY_THRESHOLD (85).
    Rules are king; AI is an advisor on borderline cases.
  - If the Gemini API is unavailable or errors out, issues are returned unchanged.
    The report is never blocked by AI unavailability.
  - Batch size: max 10 issues per API call (token budget ≤ 2000 tokens/call).
  - Output badges: ai_verdict, ai_confidence, ai_explanation on each reviewed issue.
    Issues marked FALSE_POSITIVE are demoted to INFO severity + ai_dismissed=True.
"""

import re
import json

# Issues with confidence >= this are treated as definitive — AI cannot override
CERTAINTY_THRESHOLD = 85

# Check types that are always definitive facts regardless of confidence field
_ALWAYS_DEFINITIVE = {
    "QUESTION_MISSING",           # QID absent from live — pure fact
    "QUESTION_MISSING_IN_LIVE",   # same check, existing engine label
    "PIPING_NOT_RESOLVED",        # raw piping visible in live — pure fact
    "CODE_SEQUENCE_ERROR",        # integer arithmetic — pure math
    "ALL_OPTIONS_MISSING",        # zero live options — pure fact
    "ANSWER_CODES_MISSING",       # specific codes missing — pure fact
}

# Check types that are inherently uncertain and always sent to AI Judge
_ALWAYS_UNCERTAIN = {
    "TEXT_POSSIBLE_MISMATCH",
    "TEXT_MISMATCH",
    "MISSING_WORDS",
    "MANDATORY_MARKER_MISSING",
    "MANDATORY_CHECK",
    "OPTIONS_MISSING",
    "OPTIONS_COUNT_MISMATCH",
    "QUESTION_EXTRA",
    "QID_NAMING_VARIANT",
    "TERMINATION_UNVERIFIED",
    "QUESTION_ORDER_MISMATCH",
}


def _is_uncertain(issue: dict) -> bool:
    """
    Return True if this issue should be sent to AI Judge.

    Logic (in priority order):
    1. Always-definitive check types → False
    2. Confidence ≥ CERTAINTY_THRESHOLD → False  (rule is definitive)
    3. Always-uncertain check types   → True
    4. No confidence field            → True  (legacy check, assume uncertain)
    5. Confidence < CERTAINTY_THRESHOLD → True
    """
    check = issue.get("check", "")

    if check in _ALWAYS_DEFINITIVE:
        return False

    conf = issue.get("confidence")

    if conf is not None and conf >= CERTAINTY_THRESHOLD:
        return False

    if check in _ALWAYS_UNCERTAIN:
        return True

    # No confidence field → uncertain by default
    if conf is None:
        return True

    return conf < CERTAINTY_THRESHOLD


def _build_issue_context(issue: dict) -> str:
    """Build a compact context string for one issue (fits inside a batched prompt)."""
    parts = []

    check   = issue.get("check", "UNKNOWN")
    qid     = issue.get("qid", "?")
    details = issue.get("details", "")
    doc_snip  = (issue.get("doc_snippet") or "")[:120].strip()
    live_snip = (issue.get("live_snippet") or "")[:120].strip()
    sem_score = issue.get("similarity_score")
    rule      = issue.get("rule", "")
    conf      = issue.get("confidence")

    parts.append(f"Type: {check} | QID: {qid}")
    if rule:
        parts.append(f"Rule: {rule}")
    if conf is not None:
        parts.append(f"Rule confidence: {conf}%")
    if details:
        parts.append(f"Finding: {details[:150]}")
    if doc_snip:
        parts.append(f'Spec text: "{doc_snip}"')
    if live_snip:
        parts.append(f'Live text: "{live_snip}"')
    if sem_score is not None:
        parts.append(f"Semantic similarity: {sem_score:.0%}")

    return "\n    ".join(parts)


def _build_batch_prompt(batch: list[dict]) -> str:
    """
    Build a single Gemini prompt reviewing up to 10 issues.
    Stays well under 2000 tokens.
    """
    items_text = ""
    for i, issue in enumerate(batch):
        ctx = _build_issue_context(issue)
        items_text += f"\n[{i}] {ctx}\n"

    return f"""You are a survey QC expert reviewing potential issues in a pharmaceutical/healthcare survey.

The survey spec and live survey may be in different languages (French, English, German, etc.). Cross-lingual equivalents are NOT bugs.

For each issue below, decide:
- CONFIRMED_BUG: Real problem that needs a fix
- FALSE_POSITIVE: Not actually a bug (translation, synonym, minor formatting)
- NEEDS_MANUAL: Too ambiguous to decide without seeing the full survey context

Issues to review:
{items_text}
Return ONLY valid JSON, no markdown:
{{"reviews":[{{"idx":0,"verdict":"CONFIRMED_BUG","confidence":85,"explanation":"one sentence"}}]}}"""


def _parse_gemini_response(text: str, batch_size: int) -> list[dict]:
    """
    Parse Gemini JSON response. Returns list of verdict dicts indexed by 'idx'.
    Falls back to empty list on any parse error.
    """
    try:
        text = text.strip()
        text = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        reviews = data.get("reviews", [])
        # Validate structure
        out = []
        for r in reviews:
            idx = r.get("idx")
            verdict = r.get("verdict", "NEEDS_MANUAL")
            if not isinstance(idx, int) or idx < 0 or idx >= batch_size:
                continue
            if verdict not in ("CONFIRMED_BUG", "FALSE_POSITIVE", "NEEDS_MANUAL"):
                verdict = "NEEDS_MANUAL"
            out.append({
                "idx": idx,
                "verdict": verdict,
                "confidence": min(100, max(0, int(r.get("confidence", 50)))),
                "explanation": str(r.get("explanation", ""))[:200],
            })
        return out
    except Exception:
        return []


class AIJudge:
    """
    Layer 3: reviews uncertain issues via Gemini.
    Accepts the gemini_model object (same instance used in qc_engine).
    Pass model=None to create a no-op judge that returns issues unchanged.
    """

    def __init__(self, gemini_model):
        self.model = gemini_model

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini and return raw text. Raises on failure."""
        response = self.model.generate_content(prompt)
        return response.text.strip()

    def _review_batch(self, batch: list[dict]) -> dict[int, dict]:
        """
        Send one batch to Gemini. Returns {local_idx: verdict_dict}.
        On any error returns empty dict (issues stay unchanged).
        """
        if not self.model or not batch:
            return {}
        try:
            prompt = _build_batch_prompt(batch)
            raw = self._call_gemini(prompt)
            reviews = _parse_gemini_response(raw, len(batch))
            return {r["idx"]: r for r in reviews}
        except Exception:
            return {}

    def review_all(
        self,
        all_issues: list[dict],
        doc_questions: dict | None = None,
        live_questions: dict | None = None,
    ) -> tuple[list[dict], dict]:
        """
        Review all uncertain issues in all_issues.
        Mutates issues in-place (adds ai_verdict, ai_confidence, ai_explanation fields).
        Returns (updated_all_issues, stats_dict).

        Stats dict contains:
          reviewed   — how many issues were sent to AI
          confirmed  — how many AI said CONFIRMED_BUG
          dismissed  — how many AI said FALSE_POSITIVE
          manual     — how many AI said NEEDS_MANUAL
          skipped    — how many issues had confidence ≥ threshold (not sent to AI)
          api_errors — how many batch calls failed
        """
        stats = {
            "reviewed": 0,
            "confirmed": 0,
            "dismissed": 0,
            "manual": 0,
            "skipped": 0,
            "api_errors": 0,
        }

        if not self.model:
            return all_issues, stats

        # Identify uncertain issues (keep their original list indices)
        uncertain_indices = [
            i for i, iss in enumerate(all_issues) if _is_uncertain(iss)
        ]
        stats["skipped"] = len(all_issues) - len(uncertain_indices)

        if not uncertain_indices:
            return all_issues, stats

        # Batch into groups of ≤ 10
        BATCH_SIZE = 10
        for batch_start in range(0, len(uncertain_indices), BATCH_SIZE):
            chunk_indices = uncertain_indices[batch_start : batch_start + BATCH_SIZE]
            batch = [all_issues[i] for i in chunk_indices]

            verdicts = self._review_batch(batch)

            if not verdicts:
                stats["api_errors"] += 1
                # Leave this batch untouched — never block the report
                continue

            stats["reviewed"] += len(verdicts)

            for local_idx, vdict in verdicts.items():
                issue_idx = chunk_indices[local_idx]
                issue = all_issues[issue_idx]

                issue["ai_verdict"]      = vdict["verdict"]
                issue["ai_confidence"]   = vdict["confidence"]
                issue["ai_explanation"]  = vdict["explanation"]

                if vdict["verdict"] == "CONFIRMED_BUG":
                    issue["ai_verified"] = True
                    stats["confirmed"] += 1

                elif vdict["verdict"] == "FALSE_POSITIVE":
                    # Demote severity — keep in list for audit trail
                    issue["severity"]     = "INFO"
                    issue["ai_dismissed"] = True
                    stats["dismissed"] += 1

                elif vdict["verdict"] == "NEEDS_MANUAL":
                    issue["ai_needs_manual"] = True
                    stats["manual"] += 1

        return all_issues, stats


# ─── Convenience: build a judge from the app's Gemini factory ────────────────
def make_judge(gemini_model) -> AIJudge:
    """Create an AIJudge. Passing None returns a no-op judge (safe default)."""
    return AIJudge(gemini_model)
