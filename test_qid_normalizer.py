"""Regression test runner for qid_normalizer — run directly or via pytest."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from qid_normalizer import (
    INTERNAL_NORMS, S99_DATE_PAT, build_strip_candidates, run_tests,
)


def test_e17347_false_positives():
    """The 7 reported false positives from E17347 must all resolve cleanly."""
    doc  = {'q11', 'q23', 'q40bis', 'r2'}
    live = {'q11', 'q23'}

    cases = [
        ('Q11x1',    'q11x1',    'q11',    'doc'),
        ('Q11x2',    'q11x2',    'q11',    'doc'),
        ('Q11x3',    'q11x3',    'q11',    'doc'),
        ('Q23bis',   'q23bis',   'q23',    'doc'),
        ('Q40bis_1', 'q40bis1',  'q40bis', 'doc'),
        ('R2x1',     'r2x1',     'r2',     'doc'),
        ('S99Datex1','s99datex1', None,    'intern'),
        ('S99Datex2','s99datex2', None,    'intern'),
        ('S99Datex3','s99datex3', None,    'intern'),
        ('S99Datex4','s99datex4', None,    'intern'),
        ('SumOfRows','sumofrows', None,    'intern'),
    ]

    for raw, xnorm, target, src in cases:
        if src == 'intern':
            assert (xnorm in INTERNAL_NORMS
                    or S99_DATE_PAT.match(xnorm)
                    or any(c in INTERNAL_NORMS for c in build_strip_candidates(raw, xnorm))), \
                f'{raw!r} not caught as internal'
        else:
            cands = build_strip_candidates(raw, xnorm)
            assert any(c == target and (c in doc or c in live) for c in cands), \
                f'{raw!r} candidates {cands} did not match {target!r}'


def test_no_false_negatives():
    """Real QIDs must not be swallowed by the normaliser."""
    real_qids = ['Q1', 'SPE', 'AGE', 'Q23bis', 'Q15bis', 'R2', 'Q11']
    for qid in real_qids:
        import re
        xnorm = re.sub(r'[^a-z0-9]', '', qid.lower())
        assert xnorm not in INTERNAL_NORMS, \
            f'Real QID {qid!r} incorrectly in INTERNAL_NORMS'
        assert not S99_DATE_PAT.match(xnorm), \
            f'Real QID {qid!r} incorrectly matched S99_DATE_PAT'


if __name__ == '__main__':
    ok = run_tests()
    # Also run the standalone tests above
    try:
        test_e17347_false_positives()
        print('test_e17347_false_positives  PASS')
    except AssertionError as e:
        print(f'test_e17347_false_positives  FAIL: {e}')
        ok = False

    try:
        test_no_false_negatives()
        print('test_no_false_negatives      PASS')
    except AssertionError as e:
        print(f'test_no_false_negatives      FAIL: {e}')
        ok = False

    sys.exit(0 if ok else 1)
