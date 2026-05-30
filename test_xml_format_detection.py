"""
test_xml_format_detection.py — Tests for xml_parser.detect_format().

Creates small dummy export files in /tmp/, runs detect_format() on each,
and prints PASS/FAIL per case including "extension lying" scenarios.
"""

import json
import os
import shutil
import sys
import zipfile

# Allow running from any working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xml_parser import detect_format, FMT_QUALTRICS, FMT_DECIPHER, FMT_CONFIRMIT_XML, FMT_CONFIRMIT_ZIP, FMT_FORSTA, FMT_GENERIC_XML, FMT_UNKNOWN

TMP = "/tmp/surveyqc_format_tests"
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []

def check(label, file_path, expected_fmt):
    actual = detect_format(file_path)
    ok = actual == expected_fmt
    status = PASS if ok else FAIL
    print(f"  [{status}]  {label}")
    if not ok:
        print(f"           expected={expected_fmt!r}  got={actual!r}")
    results.append(ok)

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── Setup ─────────────────────────────────────────────────────────────────────

os.makedirs(TMP, exist_ok=True)

# ── Create dummy files ────────────────────────────────────────────────────────

# 1. Qualtrics QSF (JSON)
qsf_content = json.dumps({
    "SurveyEntry": {
        "SurveyID": "SV_test123",
        "SurveyName": "Test Survey",
        "SurveyStatus": "Active"
    },
    "SurveyElements": [
        {
            "SurveyID": "SV_test123",
            "Element": "SQ",
            "PrimaryAttribute": "QID1",
            "Payload": {
                "QuestionText": "What is your age?",
                "QuestionType": "MC",
                "Choices": {"1": {"Display": "Under 18"}, "2": {"Display": "18-34"}}
            }
        }
    ]
})
qsf_path = os.path.join(TMP, "dummy_qualtrics.qsf")
with open(qsf_path, "w", encoding="utf-8") as f:
    f.write(qsf_content)

# 2. Decipher XML
decipher_xml = """<?xml version="1.0" encoding="UTF-8"?>
<survey
  xmlns="http://www.decipherinc.com/ns/survey"
  name="TestSurvey" id="selfserve/1234/testsurvey">
  <radio label="Q1" optional="0" cond="1">
    <title>Which of the following best describes you?</title>
    <row label="1">Employee</row>
    <row label="2">Manager</row>
    <row label="3">Director</row>
  </radio>
  <checkbox label="Q2" cond="Q1.r1">
    <title>Which departments?</title>
    <row label="1">Finance</row>
    <row label="2">HR</row>
  </checkbox>
  <number label="Q3" size="3" cond="1">
    <title>How many years of experience?</title>
  </number>
</survey>"""
decipher_path = os.path.join(TMP, "dummy_decipher.xml")
with open(decipher_path, "w", encoding="utf-8") as f:
    f.write(decipher_xml)

# 3. Confirmit raw XML
confirmit_xml = """<?xml version="1.0" encoding="UTF-8"?>
<survey xmlns:confirmit="http://www.confirmit.com/schema/survey">
  <Variables>
    <Variable type="Single" confirmit:code="Q1" confirmit:version="1">
      <Text>What is your primary job role?</Text>
      <Values>
        <Value code="1"><Text>Analyst</Text></Value>
        <Value code="2"><Text>Manager</Text></Value>
        <Value code="3"><Text>Director</Text></Value>
      </Values>
    </Variable>
    <Variable type="Multi" confirmit:code="Q2">
      <Text>Which tools do you use?</Text>
      <Values>
        <Value code="1"><Text>Excel</Text></Value>
        <Value code="2"><Text>Python</Text></Value>
      </Values>
    </Variable>
  </Variables>
</survey>"""
confirmit_xml_path = os.path.join(TMP, "dummy_confirmit.xml")
with open(confirmit_xml_path, "w", encoding="utf-8") as f:
    f.write(confirmit_xml)

# 4. Confirmit ZIP (wraps the same XML)
confirmit_zip_path = os.path.join(TMP, "dummy_confirmit.zip")
with zipfile.ZipFile(confirmit_zip_path, "w") as zf:
    zf.writestr("SurveyDefinition.xml", confirmit_xml)

# 5. Forsta / Dimensions XML
forsta_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ddf:survey xmlns:ddf="http://www.forsta.com/ddf" id="SURVEY_001">
  <block label="screener">
    <question ident="S1" type="single">
      <text>What is your age?</text>
      <values>
        <value code="1">18-24</value>
        <value code="2">25-34</value>
        <value code="3">35+</value>
      </values>
    </question>
  </block>
</ddf:survey>"""
forsta_path = os.path.join(TMP, "dummy_forsta.xml")
with open(forsta_path, "w", encoding="utf-8") as f:
    f.write(forsta_xml)

# 6. Generic XML (has <survey but no known markers)
generic_xml = """<?xml version="1.0" encoding="UTF-8"?>
<survey id="unknown_platform">
  <question id="Q1">
    <text>How satisfied are you?</text>
    <option value="1">Very satisfied</option>
    <option value="2">Satisfied</option>
  </question>
</survey>"""
generic_path = os.path.join(TMP, "dummy_generic.xml")
with open(generic_path, "w", encoding="utf-8") as f:
    f.write(generic_xml)

# 7. Random / unknown content
random_path = os.path.join(TMP, "dummy_random.txt")
with open(random_path, "w", encoding="utf-8") as f:
    f.write("This is just some random text.\nNot a survey file at all.\n")

# 8. Empty file
empty_path = os.path.join(TMP, "dummy_empty.xml")
with open(empty_path, "w") as f:
    pass


# ── Tests: canonical files ────────────────────────────────────────────────────

section("Canonical files (correct extension)")
check("Qualtrics QSF (.qsf)",           qsf_path,           FMT_QUALTRICS)
check("Decipher XML (.xml)",            decipher_path,      FMT_DECIPHER)
check("Confirmit raw XML (.xml)",       confirmit_xml_path, FMT_CONFIRMIT_XML)
check("Confirmit export ZIP (.zip)",    confirmit_zip_path, FMT_CONFIRMIT_ZIP)
check("Forsta/Dimensions XML (.xml)",   forsta_path,        FMT_FORSTA)
check("Generic XML survey (.xml)",      generic_path,       FMT_GENERIC_XML)
check("Random text (.txt)",             random_path,        FMT_UNKNOWN)
check("Empty file (.xml)",              empty_path,         FMT_UNKNOWN)


# ── Tests: extension lying ────────────────────────────────────────────────────

section("Extension lying — content wins")

# Qualtrics .qsf renamed to .xml
qsf_as_xml = os.path.join(TMP, "qualtrics_renamed.xml")
shutil.copy(qsf_path, qsf_as_xml)
check("Qualtrics QSF saved as .xml",    qsf_as_xml,         FMT_QUALTRICS)

# Decipher .xml renamed to .txt
decipher_as_txt = os.path.join(TMP, "decipher_renamed.txt")
shutil.copy(decipher_path, decipher_as_txt)
check("Decipher XML saved as .txt",     decipher_as_txt,    FMT_DECIPHER)

# Confirmit XML renamed to .qsf
confirmit_as_qsf = os.path.join(TMP, "confirmit_renamed.qsf")
shutil.copy(confirmit_xml_path, confirmit_as_qsf)
check("Confirmit XML saved as .qsf",    confirmit_as_qsf,   FMT_CONFIRMIT_XML)

# Forsta XML renamed to .json
forsta_as_json = os.path.join(TMP, "forsta_renamed.json")
shutil.copy(forsta_path, forsta_as_json)
check("Forsta XML saved as .json",      forsta_as_json,     FMT_FORSTA)

# Confirmit ZIP renamed to .xml  (still has PK magic bytes)
zip_as_xml = os.path.join(TMP, "confirmit_zip_renamed.xml")
shutil.copy(confirmit_zip_path, zip_as_xml)
check("Confirmit ZIP saved as .xml",    zip_as_xml,         FMT_CONFIRMIT_ZIP)


# ── Tests: edge cases ────────────────────────────────────────────────────────

section("Edge cases")

# Password-protected zip — we can't easily create one with stdlib,
# so test that a binary non-zip file with PK-like content is handled
corrupt_zip = os.path.join(TMP, "corrupt.zip")
with open(corrupt_zip, "wb") as f:
    f.write(b"PK\x03\x04" + b"\x00" * 20 + b"garbage data that is not a real zip")

# zipfile.is_zipfile will return False for this
# so detect_format should fall through to text sniffing → unknown
check("Corrupt file with PK header",    corrupt_zip,        FMT_UNKNOWN)

# File with BOM (common in Windows exports)
bom_qsf = os.path.join(TMP, "bom_qualtrics.qsf")
with open(bom_qsf, "wb") as f:
    f.write(b"\xef\xbb\xbf")   # UTF-8 BOM
    f.write(qsf_content.encode("utf-8"))
check("Qualtrics QSF with UTF-8 BOM",   bom_qsf,           FMT_QUALTRICS)


# ── Summary ───────────────────────────────────────────────────────────────────

total  = len(results)
passed = sum(results)
failed = total - passed
print(f"\n{'═'*60}")
print(f"  Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
else:
    print("  — all tests passed")
print(f"{'═'*60}\n")

# Cleanup
shutil.rmtree(TMP, ignore_errors=True)

sys.exit(0 if failed == 0 else 1)
