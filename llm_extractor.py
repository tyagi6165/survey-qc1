"""
================================================================
  llm_extractor.py — Gemini AI-Powered Logic Extractor
================================================================
Yeh module kisi bhi LOGIC table ko le ke structured rules return karta hai.
Works with ANY language (English, French, Italian, Spanish, Hindi, etc.)
"""

import json
import re
import google.generativeai as genai


EXTRACTION_PROMPT = """You are a survey QC logic analyzer. Below is one LOGIC/ROUTING table from a market research survey screener document. The host question ID is: {host_qid}

The text may be in ANY language (English, French, Italian, Spanish, German, Hindi, Urdu, etc.) and use any format style.

LOGIC TABLE CONTENT:
{content}

Your task: Extract ALL termination rules (rules that close/end the survey for the respondent). For each rule, determine:
1. test_qid: which question's answer triggers the termination (usually the host question)
2. answer_code: which answer code triggers it (e.g., "1", "2")
3. action: "terminate" | "continue" | "qualify"
4. complexity: "simple" if it's a direct "if code X = close" rule, OR "compound" if it involves NOT/AND/OR/cross-question references
5. reason: brief description of what triggers termination

Return ONLY valid JSON in this exact format (no markdown, no code fences):
{{
  "rules": [
    {{
      "test_qid": "...",
      "answer_code": "...",
      "action": "terminate",
      "complexity": "simple",
      "reason": "..."
    }}
  ],
  "notes": "any important observations"
}}

If there are no termination rules (e.g., LOGIC just says "Continue"), return {{"rules": [], "notes": "no termination logic"}}.
"""


def configure_gemini(api_key):
    """Set up Gemini with API key."""
    genai.configure(api_key=api_key)


def extract_logic_with_ai(table_content, host_qid):
    """
    Use Gemini AI to extract termination rules from a LOGIC table.
    Returns: dict with 'rules' list and 'notes' string.
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = EXTRACTION_PROMPT.format(
            host_qid=host_qid or 'unknown',
            content=table_content
        )

        response = model.generate_content(prompt)
        text = response.text.strip()

        # Clean any markdown code fences
        text = text.replace('```json', '').replace('```', '').strip()

        # Parse JSON
        result = json.loads(text)
        return result, None  # (data, error)

    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}. Raw: {text[:200]}"
    except Exception as e:
        return None, f"API error: {str(e)}"


def extract_all_logic_tables(logic_tables):
    """
    Extract rules from all LOGIC tables.

    Args:
        logic_tables: list of dicts with 'host_qid' and 'flat_text'

    Returns:
        all_rules: list of extracted rules
        summary: stats dict
    """
    all_rules = []
    errors = []

    for i, table in enumerate(logic_tables, 1):
        result, error = extract_logic_with_ai(
            table['flat_text'],
            table.get('host_qid')
        )

        if error:
            errors.append({
                'table_idx': i,
                'host_qid': table.get('host_qid'),
                'error': error
            })
            continue

        for rule in result.get('rules', []):
            rule['source_table_idx'] = i
            rule['host_qid'] = table.get('host_qid')
            all_rules.append(rule)

    # Summary stats
    terminate_rules = [r for r in all_rules if r.get('action') == 'terminate']
    simple_term = [r for r in terminate_rules if r.get('complexity') == 'simple']
    compound_term = [r for r in terminate_rules if r.get('complexity') == 'compound']

    summary = {
        'tables_processed': len(logic_tables),
        'tables_with_errors': len(errors),
        'total_rules': len(all_rules),
        'terminate_rules': len(terminate_rules),
        'simple_terminate': len(simple_term),
        'compound_terminate': len(compound_term),
        'other_rules': len(all_rules) - len(terminate_rules),
        'errors': errors
    }

    return all_rules, summary
