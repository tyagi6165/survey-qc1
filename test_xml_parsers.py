"""
test_xml_parsers.py — Validate Phase 2 XML parsers.

Usage:
    cd /var/www/surveyqc
    python3 test_xml_parsers.py

For each sample file:
  1. Calls parse_export(path) — auto-detects format
  2. Prints total questions + type breakdown
  3. Saves JSON to /tmp/<name>_normalized.json
  4. Validates schema completeness
  5. Prints first 2 questions as JSON
  6. Reports PASS / FAIL

No GEMINI_API_KEY required if all sample questions are cleanly parseable by code
(i.e. 0 LLM fallbacks expected on the provided clean samples).
"""

import json
import logging
import os
import sys
from collections import Counter

# ── Configure logging so parser INFO/WARNING messages appear ─────────────────
logging.basicConfig(
    level=logging.INFO,
    format="  [%(levelname)s] %(name)s: %(message)s",
)

sys.path.insert(0, "/var/www/surveyqc")
from xml_parser import parse_export, detect_format

# ── Schema constants ──────────────────────────────────────────────────────────
REQUIRED_KEYS   = {"qid", "qid_normalized", "text", "type", "options",
                   "routing", "termination", "is_matrix_group"}
VALID_TYPES     = {"UNIQUE", "MULTIPLE", "OPEN", "MATRIX", "GRID", "NUMERIC", "UNKNOWN"}
OPTION_KEYS     = {"code", "text", "marker"}


# ── Schema validator ──────────────────────────────────────────────────────────
def validate_schema(questions: list) -> list:
    """Return list of error strings. Empty list = PASS."""
    errors = []

    if not isinstance(questions, list):
        return [f"Result is {type(questions).__name__}, expected list"]

    for i, q in enumerate(questions):
        prefix = f"Q[{i}] qid={q.get('qid','?')!r}"

        missing = REQUIRED_KEYS - set(q.keys())
        if missing:
            errors.append(f"{prefix}: missing keys {sorted(missing)}")

        q_type = q.get("type")
        if q_type not in VALID_TYPES:
            errors.append(f"{prefix}: invalid type {q_type!r}")

        opts = q.get("options")
        if not isinstance(opts, list):
            errors.append(f"{prefix}: options is {type(opts).__name__}, expected list")
        else:
            for j, opt in enumerate(opts):
                missing_opt = OPTION_KEYS - set(opt.keys())
                if missing_opt:
                    errors.append(f"{prefix} option[{j}]: missing keys {sorted(missing_opt)}")

        if not isinstance(q.get("is_matrix_group"), bool):
            errors.append(f"{prefix}: is_matrix_group is not bool")

    return errors


# ── Per-sample runner ─────────────────────────────────────────────────────────
def run_sample(label: str, path: str) -> bool:
    """Run one sample. Returns True on PASS."""
    SEP = "─" * 60
    print(f"\n{SEP}")
    print(f"  Sample : {label}")
    print(f"  File   : {path}")

    if not os.path.exists(path):
        print(f"  ERROR  : file not found — {path}")
        print(f"  Result : FAIL\n{SEP}")
        return False

    fmt = detect_format(path)
    print(f"  Format : {fmt}")

    try:
        questions = parse_export(path)
    except Exception as exc:
        print(f"  ERROR  : parse_export raised {type(exc).__name__}: {exc}")
        print(f"  Result : FAIL\n{SEP}")
        return False

    total = len(questions)
    print(f"  Total  : {total} questions")

    if total == 0:
        print("  Result : FAIL  (empty result)")
        print(SEP)
        return False

    # Type breakdown
    type_counts = Counter(q.get("type", "?") for q in questions)
    print(f"  Types  : {dict(type_counts)}")

    # LLM fallback count (inferred from log — just show 0 expectation note)
    print("  LLM    : see log above (expected 0 for clean sample files)")

    # Save full JSON
    out_path = f"/tmp/{label.replace(' ', '_')}_normalized.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(questions, fh, ensure_ascii=False, indent=2)
    print(f"  Saved  : {out_path}")

    # Schema validation
    errors = validate_schema(questions)
    if errors:
        print(f"  Schema : FAIL ({len(errors)} error(s))")
        for e in errors[:5]:
            print(f"    • {e}")
        print(f"  Result : FAIL\n{SEP}")
        return False

    print(f"  Schema : OK — all {total} questions pass schema check")

    # Print first 2 questions
    print("\n  ── First 2 questions ──────────────────────────────────")
    for q in questions[:2]:
        print(json.dumps(q, ensure_ascii=False, indent=4))

    print(f"\n  Result : PASS\n{SEP}")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    samples = [
        ("Decipher",   "/tmp/sample_decipher.xml"),
        ("Qualtrics",  "/tmp/sample_qualtrics.qsf"),
        ("Confirmit",  "/tmp/sample_confirmit.zip"),
    ]

    print("\n" + "═" * 60)
    print("  XML Parser Phase 2 — Test Suite")
    print("═" * 60)

    results = {}
    for label, path in samples:
        results[label] = run_sample(label, path)

    print("\n" + "═" * 60)
    print("  SUMMARY")
    print("═" * 60)
    all_pass = True
    for label, passed in results.items():
        status = "PASS ✓" if passed else "FAIL ✗"
        print(f"  {label:<12} {status}")
        if not passed:
            all_pass = False

    print("═" * 60)
    print(f"  Overall : {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print("═" * 60 + "\n")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
