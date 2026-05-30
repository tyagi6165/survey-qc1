import re

# Matches QID patterns: 1-6 uppercase letters, 1+ digits, optional suffix
# Suffix handles: bis, ter, plain letters (a/b/c), letter+digit (x1, x2)
_QID_RE = re.compile(
    r'\b([A-Za-z]{1,6}\d+(?:bis|ter|[a-z]{1,3}\d*)?)\b',
    re.IGNORECASE
)


def parse_qid_list(text: str) -> list:
    """
    Extract QIDs from arbitrary text input.

    Handles comma/newline-separated lists and natural-language sentences.
    Returns normalized (uppercased) QIDs, deduped, order-preserving.
    Falls back to LLM extraction if regex finds nothing.
    """
    if not text or not text.strip():
        return []

    matches = _QID_RE.findall(text)
    if matches:
        seen = set()
        result = []
        for m in matches:
            norm = m.upper()
            if norm not in seen:
                seen.add(norm)
                result.append(norm)
        return result

    # LLM fallback: ask Gemini to extract QIDs from natural language
    return _llm_extract_qids(text)


def _llm_extract_qids(text: str) -> list:
    """
    LLM fallback: called only when regex finds 0 matches.
    Uses Gemini to extract QIDs from natural-language feedback.
    Returns [] gracefully on any error.
    """
    try:
        import os, sys
        sys.path.insert(0, '/var/www/surveyqc')
        from app import api_store  # reuse existing key store
        api_key = api_store.get('gemini', {}).get('key', '')
        if not api_key:
            return []

        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        prompt = (
            "Extract all survey question IDs (QIDs) from the text below. "
            "QIDs look like Q1, Q15bis, P3, R2x1, S1bis, SA1, MA2, etc. — "
            "a prefix of letters followed by a number, with an optional suffix.\n"
            "Return ONLY a JSON array of strings, e.g. [\"Q1\",\"Q15BIS\",\"P3\"]. "
            "Uppercase all IDs. If none found, return [].\n\n"
            f"TEXT:\n{text[:2000]}"
        )
        response = model.generate_content(prompt)
        raw = response.text.strip()
        raw = re.sub(r'```json|```', '', raw).strip()

        import json
        extracted = json.loads(raw)
        if isinstance(extracted, list):
            return [str(q).upper() for q in extracted if q]
    except Exception:
        pass
    return []
