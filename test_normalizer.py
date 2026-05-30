"""
test_normalizer.py — Standalone test for normalizer.py

Usage:
    cd /var/www/surveyqc
    python test_normalizer.py

Requires GEMINI_API_KEY in environment (or .env file in project root).
"""

import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Load .env if present (GEMINI_API_KEY)
_env = Path('/var/www/surveyqc/.env')
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s  %(message)s',
)

from normalizer import (
    extract_raw_text,
    chunk_document,
    normalize_document_from_path,
    _unique_qids_in_text,
    _QID_LINE_RE,
)

DOC_PATH = (
    '/var/www/surveyqc/uploads/a97d5f58/'
    'E17347_-_Questionnaire_de_programmation_QUANTI__QUALI_2.docx'
)
OUTPUT_PATH = '/tmp/e17347_normalized.json'


def main():
    print('=' * 60)
    print('  SurveyQC Normalizer — E17347 test run')
    print('=' * 60)

    # ── Step 1: extract text ─────────────────────────────────────
    print('\n[1] Extracting raw text from doc...')
    raw_text = extract_raw_text(DOC_PATH)
    raw_lines = raw_text.splitlines()
    print(f'    {len(raw_lines)} lines extracted')

    # ── Step 2: chunk ────────────────────────────────────────────
    print('\n[2] Chunking document...')
    chunks = chunk_document(raw_text)
    print(f'    {len(chunks)} QID chunks found')

    # ── Step 3: count raw QID markers ────────────────────────────
    expected_qids = _unique_qids_in_text(raw_text)
    print(f'\n[3] Raw QID markers in doc: {len(expected_qids)}')
    print(f'    {sorted(expected_qids)}')

    # ── Step 4: normalize ─────────────────────────────────────────
    print(f'\n[4] Normalizing via Gemini (batches of 12)...')
    questions = normalize_document_from_path(DOC_PATH)

    # ── Step 5: summary ───────────────────────────────────────────
    print('\n' + '=' * 60)
    print('  RESULTS SUMMARY')
    print('=' * 60)
    print(f'  Total questions returned : {len(questions)}')
    print(f'  Expected from raw doc    : {len(expected_qids)}')

    returned_qids = {
        (q.get('qid_normalized') or q.get('qid') or '').upper()
        for q in questions
    }
    matched = expected_qids & returned_qids
    missing = expected_qids - returned_qids
    extra   = returned_qids - expected_qids

    print(f'  Matched QIDs             : {len(matched)}')
    if missing:
        print(f'  Missing from output      : {sorted(missing)}')
    if extra:
        print(f'  Extra (not in raw doc)   : {sorted(extra)}')

    # Type breakdown
    type_counts = Counter(q.get('type', 'UNKNOWN') for q in questions)
    print('\n  Type breakdown:')
    for t, n in sorted(type_counts.items()):
        print(f'    {t:<12} {n}')

    # Termination rules found
    with_term = [q for q in questions if q.get('termination')]
    print(f'\n  Questions with termination : {len(with_term)}')
    for q in with_term:
        print(f'    {q["qid"]:10} {q["termination"][:70]}')

    # Routing rules found
    with_routing = [q for q in questions if q.get('routing')]
    print(f'\n  Questions with routing     : {len(with_routing)}')

    # Matrix groups
    matrix_items = [q for q in questions if q.get('is_matrix_group')]
    print(f'  Matrix group items         : {len(matrix_items)}')

    # Sample — show first 3 questions in full
    print('\n  First 3 normalized questions:')
    for q in questions[:3]:
        print()
        print(json.dumps(q, ensure_ascii=False, indent=4))

    # ── Step 6: save full JSON ────────────────────────────────────
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    print(f'\n[6] Full JSON saved to {OUTPUT_PATH}')
    print(f'    ({os.path.getsize(OUTPUT_PATH):,} bytes)')
    print()


if __name__ == '__main__':
    if not os.environ.get('GEMINI_API_KEY'):
        print('ERROR: GEMINI_API_KEY not set. Export it before running.')
        sys.exit(1)
    main()
