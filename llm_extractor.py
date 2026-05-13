"""
================================================================
  llm_extractor.py — Gemini AI-Powered Logic Extractor
================================================================
Yeh module kisi bhi LOGIC table ko le ke structured rules return karta hai.
Works with ANY language (English, French, Italian, Spanish, Hindi, etc.)
"""

import json
import re
import re
import google.generativeai as genai


EXTRACTION_PROMPT = """You are a survey QC logic analyzer. Extract ALL termination rules from this LOGIC table.

Host question ID: {host_qid}

LOGIC TABLE:
{content}

CRITICAL RULES:
1. Extract EVERY SINGLE code that leads to termination/close/thank and close
2. If multiple codes close (e.g., code 1, code 2, code 3 all close) — create SEPARATE rule for EACH code
3. Language can be ANY (English, French, Italian, Spanish, German, etc.) — "merci et fermer", "grazie e chiudi", "thanks and close", "thank and close" ALL mean terminate

FORMATS YOU MUST HANDLE (examples):
- "If code 1 : thanks and close" → terminate code 1
- "If code 1 : thanks and close If code 2 : thanks and close" → TWO rules: code 1 AND code 2
- "If code 2 NON then thanks and close" → terminate code 2
- "Thank and close if code 2 is selected" → terminate code 2
- "If code 1 Oui : continue If code 2 NON : thanks and close" → only code 2 terminates
- "If code 1 = QUALIFY" → action is "qualify" not terminate
- "Thank and close if code 1 is NOT selected AND..." → compound rule

Return ONLY valid JSON, no markdown, no explanation:
{{
  "rules": [
    {{
      "test_qid": "{host_qid}",
      "answer_code": "1",
      "action": "terminate",
      "complexity": "simple",
      "reason": "code 1 triggers thanks and close"
    }}
  ],
  "notes": ""
}}

IMPORTANT: If 3 codes all terminate, return 3 separate rule objects. Never combine multiple codes into one rule.
If no termination rules exist, return {{"rules": [], "notes": "no termination"}}.
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

        # Clean markdown fences + Gemini 2.5 thinking tags
        text = text.replace('```json', '').replace('```', '').strip()
        # Remove Gemini thinking/parameter tags that leak into response
        text = re.sub(r'<[^>]+>.*?</[^>]+>', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'<[^>]+/>', '', text).strip()

        # Extract JSON object/array if surrounded by extra text
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

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
