"""
xml_parser.py — Survey export format detection and parsing.

Public API:
    detect_format(file_path)     -> str   format key
    parse_export(file_path)      -> list[dict]   same schema as normalizer.py

Parsing strategy — hybrid Option B:
    CODE extracts every field it can; LLM (Gemini via normalizer.normalize_chunk)
    is called ONLY when a per-question fallback trigger fires:
      1. type == 'UNKNOWN'  (tag not in known type map)
      2. options empty AND question type expects choices
      3. routing has complex operators (AND / OR / SUM / function calls)
      4. text is empty or whitespace

    Per-run log: "Parsed N questions: M via code, K via LLM fallback"
    Per-question LLM log: which QID + which field triggered the call.

Output schema (identical to normalizer.py):
    {
        "qid":            str,        # as written in source
        "qid_normalized": str,        # uppercase, dots→X, no spaces
        "text":           str,
        "type":           str,        # UNIQUE|MULTIPLE|OPEN|MATRIX|GRID|NUMERIC|UNKNOWN
        "options":        list,       # [{"code": str|None, "text": str, "marker": str|None}]
        "routing":        str|None,
        "termination":    str|None,
        "is_matrix_group": bool,
    }
"""

import json
import logging
import os
import re
import time
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import resource as _resource
    def _mem_mb():
        return _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss / 1024
except ImportError:
    def _mem_mb():
        try:
            with open('/proc/self/status') as _f:
                for _line in _f:
                    if _line.startswith('VmRSS:'):
                        return int(_line.split()[1]) / 1024
        except Exception:
            pass
        return 0.0

log = logging.getLogger(__name__)

# ── Global timing accumulator for LLM calls ───────────────────────────────────
_llm_timing: dict = {"count": 0, "total_s": 0.0, "per_qid": []}
_llm_timing_lock = threading.Lock()

# ── Format keys ───────────────────────────────────────────────────────────────

FMT_CONFIRMIT_ZIP = "confirmit_zip"
FMT_CONFIRMIT_XML = "confirmit_xml"
FMT_DECIPHER      = "decipher"
FMT_QUALTRICS     = "qualtrics"
FMT_FORSTA        = "forsta"
FMT_GENERIC_XML   = "generic_xml"
FMT_UNKNOWN       = "unknown"

_SNIFF_BYTES = 2048

# ── LLM fallback config (mirrors normalizer.py) ───────────────────────────────

_LLM_MAX_RETRIES = 1
_LLM_RETRY_DELAY = 15  # seconds; multiplied by attempt number

# ── Type maps ─────────────────────────────────────────────────────────────────

TYPE_MAP_DECIPHER = {
    "radio":    "UNIQUE",
    "checkbox": "MULTIPLE",
    "number":   "NUMERIC",
    "text":     "OPEN",
    "textarea": "OPEN",
    "select":   "UNIQUE",
}

# Keyed by (QuestionType, Selector) — covers all common Qualtrics combos
TYPE_MAP_QUALTRICS = {
    ("MC",     "SAVR"):    "UNIQUE",
    ("MC",     "SAHR"):    "UNIQUE",
    ("MC",     "DL"):      "UNIQUE",
    ("MC",     "MAVR"):    "MULTIPLE",
    ("MC",     "MAHR"):    "MULTIPLE",
    ("MC",     "MSB"):     "MULTIPLE",
    ("TE",     "ML"):      "OPEN",
    ("TE",     "SL"):      "OPEN",
    ("TE",     "ESTB"):    "OPEN",
    ("Matrix", "Likert"):  "MATRIX",
    ("Matrix", "TE"):      "MATRIX",
    ("Matrix", "Profile"): "MATRIX",
    ("Slider", "HSLIDER"): "NUMERIC",
    ("Slider", "VSLIDER"): "NUMERIC",
    ("Slider", "STAR"):    "NUMERIC",
    ("DD",     "DL"):      "UNIQUE",
    ("DD",     "SACOL"):   "UNIQUE",
}

# Lowercase type attr values on Confirmit <Variable> elements
TYPE_MAP_CONFIRMIT = {
    "single":   "UNIQUE",
    "multiple": "MULTIPLE",
    "numeric":  "NUMERIC",
    "openend":  "OPEN",
    "grid":     "GRID",
    "loop":     "MATRIX",
    "info":     "OPEN",
}

# ── Confirmit Project (modern) format maps ────────────────────────────────────

# Side-channel metadata populated during parse_confirmit_xml(); read by parse_export_with_stats()
_parse_metadata: dict = {}

# Type map keyed by element tag (lowercase) — the tag IS the type in Project format
TYPE_MAP_CONFIRMIT_PROJECT: dict = {
    'single':      'UNIQUE',
    'multi':       'MULTIPLE',
    'numeric':     'NUMERIC',
    'open':        'OPEN',
    'info':        'OPEN',
    'grid':        'MATRIX',
    'gridmulti':   'MULTIPLE',
    'gridnumeric': 'NUMERIC',
    'grid3d':      'MATRIX',
    'loop':        'MATRIX',
}

# Question element tags that appear in the modern Confirmit Project XML tree
_CONFIRMIT_PROJECT_Q_TAGS: frozenset = frozenset([
    'Single', 'Multi', 'Numeric', 'Open', 'Info',
    'Grid', 'GridMulti', 'GridNumeric', 'Grid3D', 'Loop',
])

# Maps element tag (lowercase) to the child tag holding answer/row elements
_CONFIRMIT_PROJECT_ANSWERS_TAG: dict = {
    'single':      'SingleAnswers',
    'multi':       'MultiAnswers',
    'grid':        'GridAnswers',
    'gridmulti':   'GridAnswers',
    'gridnumeric': 'GridAnswers',
    'grid3d':      'GridAnswers',
}

# Visible survey QIDs: Q/R/S/P/SA/SC/D followed by digits + optional alphanumeric suffix
# Hidden/template variables (InLoop, HidLang, DacimaID …) don't match this pattern
_CONFIRMIT_VISIBLE_QID_RE = re.compile(
    r'^(Q|R|S|P|SA|SC|D)\d+[a-zA-Z0-9_]*$', re.IGNORECASE
)

# Types that should have a non-empty options list
_CHOICE_TYPES = {"UNIQUE", "MULTIPLE", "MATRIX", "GRID"}


# ── QID normalisation ─────────────────────────────────────────────────────────

def _normalize_qid(raw: str) -> str:
    """Uppercase, replace dots with X, strip spaces."""
    return re.sub(r"\.", "X", raw.strip()).upper()

# Public alias per spec
normalize_qid = _normalize_qid


# ── Routing complexity detection ──────────────────────────────────────────────

_COMPLEX_ROUTING_RE = re.compile(
    r"\b(AND|OR|NOT|SUM|MIN|MAX|COUNT|AVG|IF|CONTAINS|MATCHES)\b"
    r"|\w+\s*\([^)]*\)",   # function-call pattern: word(...)
    re.IGNORECASE,
)

def is_complex_routing(cond_str: str) -> bool:
    """True if routing has boolean operators or function calls — triggers LLM."""
    return bool(_COMPLEX_ROUTING_RE.search(cond_str)) if cond_str else False


# ── XML element utilities ─────────────────────────────────────────────────────

def _local_tag(el) -> str:
    """Return element's local tag name, stripping any namespace."""
    return el.tag.split("}")[-1].lower()


def _child_by_tag(parent, local: str):
    """First direct child whose local tag matches (case-insensitive). None if missing."""
    lc = local.lower()
    for ch in parent:
        if _local_tag(ch) == lc:
            return ch
    return None


def _strip_xml_decl(xml_text: str) -> str:
    """Remove BOM and <?xml … ?> declaration that can confuse ET with encoding."""
    text = xml_text.lstrip("﻿")
    return re.sub(r"^\s*<\?xml[^?]*\?>", "", text, count=1).strip()


# ── Confirmit-specific helpers ────────────────────────────────────────────────

def _confirmit_get_text(container_el) -> str:
    """
    Extract text from <Texts><Text language="…"> structure.
    Prefers ENG / EN; falls back to first available language.
    """
    texts_el = _child_by_tag(container_el, "texts")
    if texts_el is None:
        return ""
    candidates: dict = {}
    for el in texts_el:
        if _local_tag(el) == "text":
            lang = el.get("language", "").upper()
            val  = (el.text or "").strip()
            if val:
                candidates[lang] = val
    for pref in ("ENG", "EN", "ENGLISH", ""):
        if pref in candidates:
            return candidates[pref]
    return next(iter(candidates.values()), "")


# ── Confirmit Project (modern) text helpers ──────────────────────────────────

def _confirmit_project_get_text(question_el) -> str:
    """
    Extract question text from the modern Confirmit Project XML format.
    Structure: <FormTexts><FormText Language="N"><Text>…</Text></FormText></FormTexts>
    Strips HTML tags. Prefers English (2057/1033), falls back to any language.
    Uses itertext() so text nested inside HTML sub-elements (e.g. <b>…</b>) is captured.
    """
    form_texts_el = question_el.find('FormTexts')
    if form_texts_el is None:
        return ""
    texts: dict = {}
    for ft in form_texts_el:
        lang = ft.get('Language', '')
        text_el = ft.find('Text')
        if text_el is not None:
            raw = ''.join(text_el.itertext()).strip()
            clean = re.sub(r'<[^>]+>', '', raw).strip()
            if clean:
                texts[lang] = clean
    for pref in ('2057', '1033', 'ENG', 'EN', ''):
        if pref in texts:
            return texts[pref]
    return next(iter(texts.values()), '')


def _confirmit_project_get_answer_text(answer_el) -> str:
    """
    Extract answer/scale text from modern Confirmit Project XML.
    Answers: <Texts><Text Language="N">…</Text></Texts>
    Scales:  <FormTexts><FormText Language="N"><Text>…</Text></FormText></FormTexts>
    Strips HTML. Prefers English, falls back to any language.
    """
    texts_el = answer_el.find('Texts')
    if texts_el is not None:
        texts: dict = {}
        for text_el in texts_el:
            if text_el.tag.split('}')[-1].lower() == 'text':
                lang = text_el.get('Language', '')
                raw  = ''.join(text_el.itertext()).strip()
                val  = re.sub(r'<[^>]+>', '', raw).strip()
                if val:
                    texts[lang] = val
        for pref in ('2057', '1033', 'ENG', 'EN', ''):
            if pref in texts:
                return texts[pref]
        if texts:
            return next(iter(texts.values()))
    # Scale elements may use <FormTexts> instead
    return _confirmit_project_get_text(answer_el)


# ── Qualtrics-specific helpers ────────────────────────────────────────────────

def _qualtrics_logic_to_str(logic) -> str:
    """Flatten Qualtrics DisplayLogic dict to a readable routing string."""
    if not isinstance(logic, dict):
        return str(logic)

    parts: list = []
    connector = "AND"

    for key, val in logic.items():
        if key == "Type":
            connector = "OR" if str(val).lower() == "any" else "AND"
            continue
        if not isinstance(val, dict):
            continue
        for cond in val.values():
            if not isinstance(cond, dict):
                continue
            ltype = cond.get("LogicType", "")
            if ltype == "Question":
                parts.append(
                    f"{cond.get('QuestionID','')} "
                    f"{cond.get('Operator','=')} "
                    f"{cond.get('Value','')}"
                )
            elif ltype == "EmbeddedData":
                parts.append(
                    f"{cond.get('Description','')} "
                    f"{cond.get('Operator','=')} "
                    f"{cond.get('Value','')}"
                )

    return f" {connector} ".join(parts) if parts else json.dumps(logic)


# ── Lazy LLM model cache ──────────────────────────────────────────────────────

_cached_model = None

def _get_llm_model():
    """Lazy-init Gemini model from normalizer; cached for process lifetime."""
    global _cached_model
    if _cached_model is None:
        from normalizer import _make_model
        _cached_model = _make_model()
    return _cached_model


# ── Fallback trigger detection ────────────────────────────────────────────────

def _check_fallback_triggers(q: dict) -> list:
    """
    Return ordered, deduplicated list of field names that need LLM enrichment.
    Called after pure-code extraction; non-empty return means LLM call fires.
    """
    triggers: list = []
    if q["type"] == "UNKNOWN":
        triggers.append("type")
        if not q["options"]:        # grab options in the same LLM call
            triggers.append("options")
    if not (q["text"] or "").strip():
        triggers.append("text")
    if not q["options"] and q["type"] in _CHOICE_TYPES and "options" not in triggers:
        triggers.append("options")
    if q["routing"] and is_complex_routing(q["routing"]):
        triggers.append("routing")
    return list(dict.fromkeys(triggers))   # deduplicate, preserve order


# ── LLM field enrichment ──────────────────────────────────────────────────────

def _llm_enrich(qid: str, raw_block: str, q: dict, triggers: list) -> dict:
    """
    Call normalize_chunk on raw_block (one question's XML / JSON string).
    Merge ONLY the triggered fields back into q.
    qid / qid_normalized are always preserved from code-parsed values.
    Retries up to _LLM_MAX_RETRIES on empty result.
    """
    from normalizer import normalize_chunk
    model = _get_llm_model()

    # [TIMING] LLM call start
    t0_llm = time.time()
    log.info("[TIMING] LLM_CALL_START: QID=%s triggers=%s raw_block_len=%d",
             qid, triggers, len(raw_block))

    result_list: list = []
    for attempt in range(_LLM_MAX_RETRIES + 1):
        t0_attempt = time.time()
        result_list = normalize_chunk(raw_block, model)
        attempt_s = time.time() - t0_attempt
        log.info("[TIMING] LLM_ATTEMPT_%d QID=%s: %.2fs result_count=%d",
                 attempt, qid, attempt_s, len(result_list))
        if result_list:
            break
        if attempt < _LLM_MAX_RETRIES:
            delay = min(2 ** attempt, 10)   # exponential backoff: 1s, 2s, 4s … capped at 10s
            log.info("[TIMING] LLM_RETRY_SLEEP: QID=%s sleeping %.1fs", qid, delay)
            time.sleep(delay)

    llm_elapsed = time.time() - t0_llm
    with _llm_timing_lock:
        _llm_timing["count"] += 1
        _llm_timing["total_s"] += llm_elapsed
        _llm_timing["per_qid"].append({"qid": qid, "triggers": triggers, "elapsed_s": round(llm_elapsed, 2)})
    log.info("[TIMING] LLM_CALL_COMPLETE: QID=%s %.2fs (cumulative: %d calls, %.2fs total)",
             qid, llm_elapsed, _llm_timing["count"], _llm_timing["total_s"])

    if not result_list:
        log.warning("_llm_enrich: no LLM result for QID=%s — keeping code-parsed values", qid)
        return q

    llm_q = result_list[0]
    merged = dict(q)

    for field in triggers:
        val = llm_q.get(field)
        if field == "options":
            if isinstance(val, list) and val:
                merged["options"] = val
                log.info("  _llm_enrich: QID=%s field='options' enriched (%d opts)", qid, len(val))
        elif val is not None:
            merged[field] = val
            log.info("  _llm_enrich: QID=%s field='%s' enriched", qid, field)

    # Always preserve code-parsed identity fields
    merged["qid"]            = q["qid"]
    merged["qid_normalized"] = q["qid_normalized"]
    return merged


# ── Format detection ──────────────────────────────────────────────────────────

def detect_format(file_path: str) -> str:
    """
    Detect survey export format from file CONTENT, not extension.

    Priority order:
      1. ZIP binary signature → confirmit_zip
      2. JSON with Qualtrics top-level keys → qualtrics
      3. XML with Decipher markers (cond= + question-type tags) → decipher
      4. XML with Confirmit markers (namespace / Variables) → confirmit_xml
      5. XML with Forsta/Dimensions markers → forsta
      6. Any XML containing <survey → generic_xml
      7. Nothing matched → unknown
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Export file not found: {file_path}")

    if os.path.getsize(file_path) == 0:
        log.warning("detect_format: file is empty — %s", file_path)
        return FMT_UNKNOWN

    # --- 1. ZIP: check magic bytes (PK\x03\x04) --------------------------------
    try:
        with open(file_path, "rb") as fh:
            magic = fh.read(4)
        if magic[:2] == b"PK":
            if zipfile.is_zipfile(file_path):
                return FMT_CONFIRMIT_ZIP
    except OSError as exc:
        log.warning("detect_format: cannot read file — %s", exc)
        return FMT_UNKNOWN

    # --- 2–6. Text-based sniff -------------------------------------------------
    try:
        with open(file_path, "rb") as fh:
            raw_bytes = fh.read(_SNIFF_BYTES)
        # Decode as UTF-8; strip BOM (﻿) that Windows tools often prepend
        snippet = raw_bytes.decode("utf-8", errors="replace").lstrip("﻿").lower()
    except OSError as exc:
        log.warning("detect_format: cannot read file — %s", exc)
        return FMT_UNKNOWN

    # 2. Qualtrics QSF — JSON starting with { containing exclusive keys
    stripped = snippet.lstrip()
    if stripped.startswith("{") and (
        '"surveyentry"' in snippet or '"surveyelements"' in snippet
    ):
        return FMT_QUALTRICS

    # 3. Decipher XML — <survey with HTML-like question tags + cond= routing
    if "<survey" in snippet and (
        "cond=" in snippet or "cond =" in snippet
    ) and any(tag in snippet for tag in ("<radio", "<checkbox", "<number", "<text", "<select")):
        return FMT_DECIPHER

    # 4. Confirmit raw XML — namespace or structural markers
    if "<survey" in snippet and any(
        marker in snippet
        for marker in ("confirmit:code", "<variables>", "xmlns:confirmit", "<surveydef")
    ):
        return FMT_CONFIRMIT_XML

    # 5. Forsta / Dimensions XML — namespace prefixes or block-first structure.
    #    Root element may be <ddf:survey or <dimensions:survey, so we don't
    #    require a bare "<survey" prefix — the namespace markers are sufficient.
    if any(
        marker in snippet
        for marker in ("ddf:", "dimensions:", "xmlns:ddf", "xmlns:dimensions")
    ) or ("<survey" in snippet and "<block " in snippet):
        return FMT_FORSTA

    # 6. Generic XML survey — has <survey but no known markers
    if "<survey" in snippet:
        return FMT_GENERIC_XML

    return FMT_UNKNOWN


# ── Main entry point ──────────────────────────────────────────────────────────

def parse_export(file_path: str) -> list:
    """
    Auto-detect format and parse survey export into normalizer schema.
    Raises ValueError for unknown or unreadable formats.
    Returns [] (with a log warning) on parse errors — never crashes.
    """
    t0_export = time.time()
    fmt = detect_format(file_path)
    log.info("parse_export: detected format '%s' for %s", fmt, os.path.basename(file_path))

    dispatch = {
        FMT_CONFIRMIT_ZIP: _load_confirmit_zip,
        FMT_CONFIRMIT_XML: _load_xml_text_and(parse_confirmit_xml),
        FMT_DECIPHER:      _load_xml_text_and(parse_decipher_xml),
        FMT_QUALTRICS:     _load_text_and(parse_qualtrics_qsf),
        FMT_FORSTA:        _load_xml_text_and(parse_forsta_xml),
        FMT_GENERIC_XML:   _load_xml_text_and(parse_generic_xml),
    }

    if fmt == FMT_UNKNOWN:
        raise ValueError(
            "Unrecognised survey export format. "
            "Expected: Confirmit datamap (.zip or .xml), "
            "Decipher XML, Qualtrics QSF (.qsf/.json), or Forsta XML."
        )

    result = dispatch[fmt](file_path)
    log.info("[TIMING] PARSE_EXPORT_TOTAL: %.2fs format=%s questions=%d",
             time.time() - t0_export, fmt, len(result))
    return result


def parse_export_with_stats(file_path: str) -> tuple:
    """
    Like parse_export() but also returns a metadata dict.

    Returns:
        (questions: list[dict], metadata: dict)

    metadata keys (all optional — present only when the format supplies them):
        'hidden_count': int  — hidden/template variables skipped (Confirmit Project format)
    """
    t0_total = time.time()
    mem_start = _mem_mb()
    log.info("[TIMING] PARSE_EXPORT_WITH_STATS_START: file=%s mem=%.1f MB",
             os.path.basename(file_path), mem_start)
    _parse_metadata.clear()
    questions = parse_export(file_path)

    # [TIMING] XML comparison preparation (building by-QID index)
    t0_index = time.time()
    _qid_index = {q["qid_normalized"]: q for q in questions}
    log.info("[TIMING] XML_COMPARISON_PREPARATION: %.2fs indexed %d questions by qid_normalized",
             time.time() - t0_index, len(_qid_index))

    total_elapsed = time.time() - t0_total
    mem_end = _mem_mb()
    log.info(
        "[TIMING] PARSE_EXPORT_WITH_STATS_COMPLETE: total=%.2fs questions=%d "
        "llm_calls=%d llm_total=%.2fs mem=%.1f MB (delta=+%.1f MB)",
        total_elapsed, len(questions),
        _llm_timing.get("count", 0), _llm_timing.get("total_s", 0.0),
        mem_end, mem_end - mem_start,
    )
    log.info("[TIMING] VARIABLES_EXTRACTED: %d total questions returned", len(questions))
    return questions, dict(_parse_metadata)


# ── File loaders (wrap parsers with safe I/O) ─────────────────────────────────

def _load_xml_text_and(parser_fn):
    """Return a loader that reads file as UTF-8 text then calls parser_fn."""
    def loader(file_path):
        t0_load = time.time()
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024) if os.path.exists(file_path) else 0
        mem_before = _mem_mb()
        log.info("[TIMING] XML_FILE_LOAD_START: %s (%.2f MB) mem=%.1f MB",
                 os.path.basename(file_path), file_size_mb, mem_before)
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            log.warning("parse_export: cannot read %s — %s", file_path, exc)
            return []
        mem_after = _mem_mb()
        log.info("[TIMING] XML_FILE_LOAD_COMPLETE: %.2fs text_len=%d chars mem=%.1f MB (delta=+%.1f MB)",
                 time.time() - t0_load, len(text), mem_after, mem_after - mem_before)
        return parser_fn(text)
    return loader


def _load_text_and(parser_fn):
    """Return a loader that reads file as UTF-8 text then calls parser_fn."""
    def loader(file_path):
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            log.warning("parse_export: cannot read %s — %s", file_path, exc)
            return []
        return parser_fn(text)
    return loader


def _load_confirmit_zip(file_path: str) -> list:
    """Unzip Confirmit export, find the survey definition XML, parse it."""
    # [TIMING] XML file load start
    t0_load = time.time()
    zip_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    mem_before = _mem_mb()
    log.info("[TIMING] XML_FILE_LOAD_START: %s (%.2f MB, mem=%.1f MB)",
             os.path.basename(file_path), zip_size_mb, mem_before)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            xml_members = [n for n in zf.namelist() if n.lower().endswith(".xml")]

            if not xml_members:
                raise ValueError("No XML file found inside zip.")

            # Prefer the file whose name suggests it's the main survey definition
            preferred = next(
                (n for n in xml_members
                 if any(kw in n.lower() for kw in ("definition", "survey", "export"))),
                None,
            )
            target = preferred or max(xml_members, key=lambda n: zf.getinfo(n).file_size)
            xml_size_bytes = zf.getinfo(target).file_size
            log.info("[TIMING] XML_FILE_LOAD_TARGET: %s (uncompressed %.2f MB)",
                     target, xml_size_bytes / (1024 * 1024))

            with tempfile.TemporaryDirectory() as tmp:
                extracted = zf.extract(target, path=tmp)
                with open(extracted, "r", encoding="utf-8", errors="replace") as fh:
                    xml_text = fh.read()

    except zipfile.BadZipFile:
        raise ValueError("File appears to be corrupted or is not a valid zip archive.")
    except RuntimeError as exc:
        if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
            raise ValueError("Zip file is password-protected — export without a password.")
        raise ValueError(f"Could not open zip: {exc}") from exc

    load_elapsed = time.time() - t0_load
    mem_after = _mem_mb()
    log.info("[TIMING] XML_FILE_LOAD_COMPLETE: %.2fs xml_text_len=%d chars mem=%.1f MB (delta=+%.1f MB)",
             load_elapsed, len(xml_text), mem_after, mem_after - mem_before)

    return parse_confirmit_xml(xml_text)


# ── Parser 1 — Confirmit XML ──────────────────────────────────────────────────

def parse_confirmit_xml(xml_text: str) -> list:
    """
    Parse a Confirmit survey XML file.

    Auto-detects which of two Confirmit XML formats the file uses:

    • Modern 'Project' format (root = <Project>, typed elements like <Single>/<Multi>):
      routes to _parse_confirmit_project_xml(); filters hidden/template variables.
      Populates _parse_metadata['hidden_count'] with the number of skipped variables.

    • Classic format (root = <survey>, structure <Variables><Variable name="" type="">):
      parsed inline.
    """
    # Reset LLM timing accumulator for this parse run
    _llm_timing["count"] = 0
    _llm_timing["total_s"] = 0.0
    _llm_timing["per_qid"] = []

    t0_total = time.time()
    mem_before_parse = _mem_mb()
    log.info("[TIMING] XML_PARSE_START: xml_text_len=%d chars mem=%.1f MB",
             len(xml_text), mem_before_parse)

    # [TIMING] ET.fromstring parse
    t0_et = time.time()
    try:
        root = ET.fromstring(_strip_xml_decl(xml_text))
    except ET.ParseError as exc:
        log.warning("parse_confirmit_xml: XML parse error — %s", exc)
        return []
    log.info("[TIMING] XML_PARSE_COMPLETE: ET.fromstring took %.2fs", time.time() - t0_et)

    # ── Modern Confirmit Project format ─────────────────────────────────────
    if _local_tag(root) == "project":
        log.info("parse_confirmit_xml: modern Project format detected")
        visible, hidden_count = _parse_confirmit_project_xml(root)
        _parse_metadata['hidden_count'] = hidden_count
        total_elapsed = time.time() - t0_total
        mem_after_parse = _mem_mb()
        log.info(
            "[TIMING] PARSE_CONFIRMIT_XML_COMPLETE: %d visible, %d hidden, total=%.2fs "
            "llm_calls=%d llm_total=%.2fs mem=%.1f MB (delta=+%.1f MB)",
            len(visible), hidden_count, total_elapsed,
            _llm_timing["count"], _llm_timing["total_s"],
            mem_after_parse, mem_after_parse - mem_before_parse,
        )
        log.info(
            "parse_confirmit_xml: %d visible questions, %d hidden/template skipped",
            len(visible), hidden_count,
        )
        return visible

    # ── Classic format: <Variables><Variable> ───────────────────────────────
    t0_var = time.time()
    log.info("[TIMING] VARIABLE_EXTRACTION_START")
    variables_el = None
    for el in root.iter():
        if _local_tag(el) == "variables":
            variables_el = el
            break

    if variables_el is None:
        log.warning("parse_confirmit_xml: no <Variables> element found and root is not <Project>")
        return []

    var_elements = [el for el in variables_el if _local_tag(el) == "variable"]
    log.info("[TIMING] VARIABLE_EXTRACTION_COMPLETE: %.2fs found %d <Variable> elements",
             time.time() - t0_var, len(var_elements))

    if not var_elements:
        log.warning("parse_confirmit_xml: no <Variable> children found")
        return []

    results   = []
    llm_count = 0

    t0_questions = time.time()
    log.info("[TIMING] QUESTION_EXTRACTION_START: %d variables", len(var_elements))

    for var_el in var_elements:
        qid      = var_el.get("name", f"VAR{len(results) + 1}")
        raw_type = var_el.get("type", "").lower()
        q_type   = TYPE_MAP_CONFIRMIT.get(raw_type, "UNKNOWN")
        text     = _confirmit_get_text(var_el)

        t0_opts = time.time()
        options = []
        answers_el = _child_by_tag(var_el, "answers")
        if answers_el is not None:
            for ans_el in answers_el:
                if _local_tag(ans_el) == "answer":
                    code     = ans_el.get("code", None)
                    opt_text = _confirmit_get_text(ans_el)
                    options.append(make_option(code, opt_text))
        opts_elapsed = time.time() - t0_opts
        if opts_elapsed > 1.0:
            log.info("[TIMING] OPTION_EXTRACTION_SLOW: QID=%s %.2fs %d options", qid, opts_elapsed, len(options))

        raw_block = "# Confirmit Variable\n" + ET.tostring(var_el, encoding="unicode")

        q = make_question(qid, text, q_type, options)
        triggers = _check_fallback_triggers(q)

        if triggers:
            llm_count += 1
            log.info("parse_confirmit_xml: LLM fallback QID=%s fields=%s", qid, triggers)
            q = _llm_enrich(qid, raw_block, q, triggers)

        results.append(q)

    log.info("[TIMING] QUESTION_EXTRACTION_COMPLETE: %.2fs extracted %d questions",
             time.time() - t0_questions, len(results))
    total_elapsed = time.time() - t0_total
    log.info(
        "[TIMING] PARSE_CONFIRMIT_XML_COMPLETE: total=%.2fs llm_calls=%d llm_total=%.2fs",
        total_elapsed, _llm_timing["count"], _llm_timing["total_s"],
    )
    log.info(
        "parse_confirmit_xml: Parsed %d questions: %d via code, %d via LLM fallback",
        len(results), len(results) - llm_count, llm_count,
    )
    return results


def _parse_confirmit_project_xml(root) -> tuple:
    """
    Parse a modern Confirmit Project XML tree (root element = <Project>).

    Question elements are typed tags (<Single>, <Multi>, <Grid>, etc.) that appear
    anywhere in the tree. The QID lives in a <Name> child element (not an attribute).

    Filters:
      - VariableType="Hidden" or "Background"  → hidden/template, skip
      - QID doesn't match ^(Q|R|S|P|SA|SC|D)\\d+  → programming helper, skip

    Returns:
        (visible_questions: list[dict], hidden_count: int)
    """
    t0_project = time.time()
    mem_before = _mem_mb()
    log.info("[TIMING] QUESTION_EXTRACTION_START (project format) mem=%.1f MB", mem_before)

    # ── FIX 1 & 2: Build parent map and Grid3D inheritance caches ────────────
    # parent_map lets us walk from any element up to a Grid3D ancestor.
    # grid3d_options_cache: Grid3D element id → list[option dict]
    #   Options come from the Scales of any Grid/GridMulti/GridNumeric children
    #   inside the Grid3D's <Nodes>.  These are shared by all Multi/Grid column
    #   questions that live in the same Grid3D but have empty own answers.
    # grid3d_text_cache: Grid3D element id → question text string
    #   Used as a fallback when a Grid3D sub-question has empty FormTexts.

    t0_prep = time.time()
    parent_map: dict = {child: parent for parent in root.iter() for child in parent}

    grid3d_options_cache: dict = {}
    grid3d_text_cache: dict    = {}

    for el in root.iter():
        if el.tag.split('}')[-1] != 'Grid3D':
            continue
        eid = id(el)
        # Text from the Grid3D itself
        grid3d_text_cache[eid] = _confirmit_project_get_text(el)
        # Options: collect Scales from direct Grid/GridMulti/GridNumeric children
        # inside <Nodes> (the immediate child container).
        options: list = []
        seen_precodes: set = set()
        nodes_el = el.find('Nodes')
        if nodes_el is not None:
            for ch in nodes_el:
                ctag = ch.tag.split('}')[-1]
                if ctag.lower() not in ('grid', 'gridmulti', 'gridnumeric'):
                    continue
                scales_el = ch.find('Scales')
                if scales_el is None:
                    continue
                for scale in scales_el:
                    if scale.tag.split('}')[-1] != 'Scale':
                        continue
                    precode  = scale.get('Precode') or scale.get('Code')
                    opt_text = _confirmit_project_get_answer_text(scale)
                    if opt_text and precode not in seen_precodes:
                        options.append(make_option(precode, opt_text))
                        seen_precodes.add(precode)
        grid3d_options_cache[eid] = options

    log.info("[TIMING] PARENT_MAP_AND_GRID3D_CACHE_BUILT: %.2fs parent_map=%d entries grid3d=%d",
             time.time() - t0_prep, len(parent_map), len(grid3d_options_cache))

    def _find_grid3d_parent(el) -> object:
        """Walk parent_map upward; return the first Grid3D ancestor or None."""
        cur = el
        for _ in range(10):
            par = parent_map.get(cur)
            if par is None:
                return None
            if par.tag.split('}')[-1] == 'Grid3D':
                return par
            cur = par
        return None

    # ── Candidate element collection ─────────────────────────────────────────
    t0_iter = time.time()
    all_candidates = [(el, el.tag.split('}')[-1]) for el in root.iter()
                      if el.tag.split('}')[-1] in _CONFIRMIT_PROJECT_Q_TAGS]
    log.info("[TIMING] TREE_ITER_COMPLETE: %.2fs found %d candidate question elements",
             time.time() - t0_iter, len(all_candidates))

    visible   = []
    hidden_ct = 0

    # Timing accumulators for sub-steps
    t_text_total     = 0.0
    t_options_total  = 0.0
    t_routing_total  = 0.0
    t_tostring_total = 0.0
    loop_count       = 0
    inherited_opts   = 0
    inherited_text   = 0

    # Collect questions needing LLM; enriched concurrently after the main loop.
    # Each entry: (list_index, qid, raw_block, q, triggers)
    pending_llm: list = []

    for el, tag in all_candidates:
        # QID from <Name> child element text
        name_el = el.find('Name')
        if name_el is None or not (name_el.text or '').strip():
            continue
        qid = name_el.text.strip()

        # Skip hidden / background / non-survey variables
        vtype = el.get('VariableType', '')
        if vtype in ('Hidden', 'Background') or not _CONFIRMIT_VISIBLE_QID_RE.match(qid):
            hidden_ct += 1
            continue

        # Type from element tag
        q_type  = TYPE_MAP_CONFIRMIT_PROJECT.get(tag.lower(), 'UNKNOWN')

        # [TIMING] Question text extraction
        t0_text = time.time()
        text    = _confirmit_project_get_text(el)
        t_text_total += time.time() - t0_text

        # [TIMING] Answer options extraction
        t0_opts = time.time()
        options = []
        tag_low = tag.lower()
        ans_tag = _CONFIRMIT_PROJECT_ANSWERS_TAG.get(tag_low)
        if ans_tag:
            ans_el = el.find(ans_tag)
            if ans_el is not None:
                for ans in ans_el:
                    atag = ans.tag.split('}')[-1]
                    if atag in ('Answer', 'GridAnswer', 'HeaderAnswer'):
                        precode  = ans.get('Precode') or ans.get('Code')
                        opt_text = _confirmit_project_get_answer_text(ans)
                        if opt_text:
                            options.append(make_option(precode, opt_text))
        # Grid/Scale types: also try own <Scales> for column headers
        if not options and tag_low in ('grid', 'gridmulti', 'gridnumeric', 'grid3d'):
            scales_el = el.find('Scales')
            if scales_el is not None:
                for scale in scales_el:
                    if scale.tag.split('}')[-1] == 'Scale':
                        precode  = scale.get('Precode') or scale.get('Code')
                        opt_text = _confirmit_project_get_answer_text(scale)
                        if opt_text:
                            options.append(make_option(precode, opt_text))
        opts_elapsed = time.time() - t0_opts
        t_options_total += opts_elapsed
        if opts_elapsed > 0.5:
            log.info("[TIMING] OPTION_EXTRACTION_SLOW: QID=%s tag=%s %.2fs %d options",
                     qid, tag, opts_elapsed, len(options))

        # ── FIX 1: inherit options from Grid3D parent when own options are empty ──
        if not options and q_type in _CHOICE_TYPES:
            g3d = _find_grid3d_parent(el)
            if g3d is not None:
                inh = grid3d_options_cache.get(id(g3d), [])
                if inh:
                    options = inh
                    inherited_opts += 1
                    log.info("[TIMING] OPTIONS_INHERITED: QID=%s from Grid3D parent (%d options)",
                             qid, len(options))

        # [TIMING] Routing extraction
        t0_routing = time.time()
        routing = None
        qmp = el.find('QuestionMaskPredicate')
        if qmp is not None and qmp.text and qmp.text.strip():
            routing = qmp.text.strip()
        if not routing:
            preds_el = el.find('Predicates')
            if preds_el is not None:
                exprs = [
                    expr.text.strip()
                    for expr in preds_el.iter()
                    if expr.tag.split('}')[-1] == 'Expression'
                    and expr.text and expr.text.strip()
                ]
                if exprs:
                    routing = '; '.join(exprs)
        t_routing_total += time.time() - t0_routing

        # ── FIX 2: inherit text from Grid3D parent when own text is empty ────────
        if not text:
            g3d = _find_grid3d_parent(el)
            if g3d is not None:
                inh_text = grid3d_text_cache.get(id(g3d), '')
                if inh_text:
                    text = inh_text
                    inherited_text += 1
                    log.info("[TIMING] TEXT_INHERITED: QID=%s from Grid3D parent", qid)

        # [TIMING] ET.tostring serialisation (used as LLM raw_block input)
        t0_tostring = time.time()
        raw_block = f"# Confirmit Project XML: {tag}\n" + ET.tostring(el, encoding='unicode')
        tostring_elapsed = time.time() - t0_tostring
        t_tostring_total += tostring_elapsed
        if tostring_elapsed > 0.5:
            log.info("[TIMING] TOSTRING_SLOW: QID=%s %.2fs raw_block_len=%d",
                     qid, tostring_elapsed, len(raw_block))

        # Track Loop elements separately
        if tag_low == 'loop':
            loop_count += 1
            log.info("[TIMING] LOOP_EXTRACTION: QID=%s routing=%s options=%d",
                     qid, routing[:60] if routing else None, len(options))

        q        = make_question(qid, text, q_type, options, routing)
        triggers = _check_fallback_triggers(q)

        if triggers:
            # ── FIX 3: defer LLM calls; run them concurrently after this loop ──
            log.info("[TIMING] AI_FALLBACK_QUEUED: QID=%s tag=%s triggers=%s text_len=%d options=%d",
                     qid, tag, triggers, len(text), len(options))
            pending_llm.append((len(visible), qid, raw_block, q, triggers))

        visible.append(q)

    # ── FIX 3: run all deferred LLM calls concurrently ───────────────────────
    llm_count = len(pending_llm)
    if pending_llm:
        t0_llm_phase = time.time()
        max_workers  = min(llm_count, 8)   # cap concurrency; Gemini handles it well
        log.info("[TIMING] CONCURRENT_LLM_START: %d calls, max_workers=%d",
                 llm_count, max_workers)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_llm_enrich, qid, raw_block, q, triggers): (idx, qid)
                for idx, qid, raw_block, q, triggers in pending_llm
            }
            for future in as_completed(future_to_idx):
                idx, qid = future_to_idx[future]
                try:
                    visible[idx] = future.result()
                except Exception as exc:
                    log.warning("[TIMING] CONCURRENT_LLM_ERROR: QID=%s %s", qid, exc)

        log.info("[TIMING] CONCURRENT_LLM_COMPLETE: %.2fs for %d calls (wall time)",
                 time.time() - t0_llm_phase, llm_count)

    total_project = time.time() - t0_project
    mem_after = _mem_mb()
    log.info("[TIMING] QUESTION_EXTRACTION_COMPLETE: %.2fs extracted %d questions",
             total_project, len(visible))
    log.info(
        "[TIMING] PROJECT_PARSE_BREAKDOWN: total=%.2fs "
        "prep=%.2fs text=%.2fs options=%.2fs routing=%.2fs tostring=%.2fs "
        "llm_total=%.2fs (%d calls, inherited_opts=%d inherited_text=%d) mem_delta=+%.1f MB",
        total_project,
        time.time() - t0_prep,
        t_text_total, t_options_total, t_routing_total, t_tostring_total,
        _llm_timing["total_s"], llm_count, inherited_opts, inherited_text,
        mem_after - mem_before,
    )
    log.info(
        "[TIMING] NORMALIZATION_NOTE: %d questions extracted, %d via code, %d via LLM",
        len(visible), len(visible) - llm_count, llm_count,
    )
    log.info("[TIMING] LOOP_EXTRACTION_COMPLETE: %d loop elements processed", loop_count)

    if _llm_timing["per_qid"]:
        slowest = sorted(_llm_timing["per_qid"], key=lambda x: x["elapsed_s"], reverse=True)[:5]
        log.info("[TIMING] SLOWEST_LLM_CALLS: %s", slowest)

    log.info(
        "_parse_confirmit_project_xml: %d visible, %d hidden/template, %d via LLM",
        len(visible), hidden_ct, llm_count,
    )
    return visible, hidden_ct


# ── Parser 2 — Decipher / Forsta Plus XML ─────────────────────────────────────

def _iter_decipher_questions(parent, in_block: bool = False):
    """
    Yield (element, in_block) for question and <term> elements recursively.
    Questions inside a <block> get in_block=True → is_matrix_group=True.
    """
    for el in parent:
        tag = _local_tag(el)
        if tag == "block":
            yield from _iter_decipher_questions(el, in_block=True)
        elif tag in TYPE_MAP_DECIPHER or tag == "term":
            yield el, in_block


def parse_decipher_xml(xml_text: str) -> list:
    """
    Parse a Decipher (Forsta Plus) survey XML file.
    Structure: <survey>
                 <radio label="Q1" cond="…"><title>…</title><row label="r1">…</row>…</radio>
                 <term cond="Q1.r1" />
                 <checkbox|number|text|textarea|select label="…">…</checkbox>
                 <block label="…" cond="…">…nested questions…</block>
               </survey>

    <term> elements are attached to the nearest preceding question as termination.
    Questions inside <block> with cond= get is_matrix_group=True.
    """
    try:
        root = ET.fromstring(_strip_xml_decl(xml_text))
    except ET.ParseError as exc:
        log.warning("parse_decipher_xml: XML parse error — %s", exc)
        return []

    results  = []
    llm_count = 0
    last_q   = None   # mutable dict ref for termination attachment

    for el, in_block in _iter_decipher_questions(root):
        tag = _local_tag(el)

        # ── <term> — attach to preceding question ───────────────────────────
        if tag == "term":
            cond = (el.get("cond") or "").strip()
            if cond and last_q is not None:
                if last_q["termination"]:
                    last_q["termination"] += f"; {cond}"
                else:
                    last_q["termination"] = cond
            continue

        # ── question element ────────────────────────────────────────────────
        qid    = el.get("label", f"Q{len(results) + 1}")
        cond   = (el.get("cond") or "").strip()
        q_type = TYPE_MAP_DECIPHER.get(tag, "UNKNOWN")

        title_el = _child_by_tag(el, "title")
        text     = (title_el.text or "").strip() if title_el is not None else ""

        options = []
        for row in el.findall("row"):
            code      = row.get("label")
            opt_text  = (row.text or "").strip()
            exclusive = row.get("exclusive", "0") == "1"
            options.append(make_option(code, opt_text, "ne" if exclusive else None))

        routing = cond or None

        raw_block = f"# Decipher XML element: {tag}\n" + ET.tostring(el, encoding="unicode")

        q = make_question(qid, text, q_type, options, routing,
                          is_matrix_group=in_block)
        triggers = _check_fallback_triggers(q)

        if triggers:
            llm_count += 1
            log.info("parse_decipher_xml: LLM fallback QID=%s fields=%s", qid, triggers)
            q = _llm_enrich(qid, raw_block, q, triggers)

        results.append(q)
        last_q = q

    log.info(
        "parse_decipher_xml: Parsed %d questions: %d via code, %d via LLM fallback",
        len(results), len(results) - llm_count, llm_count,
    )
    return results


# ── Parser 3 — Qualtrics QSF (JSON) ──────────────────────────────────────────

def parse_qualtrics_qsf(qsf_text: str) -> list:
    """
    Parse a Qualtrics QSF file (JSON despite the extension).
    Filters SurveyElements where Element == "SQ"; all others (BL, RS …) ignored.
    Type determined from (QuestionType, Selector) pair.
    DisplayLogic flattened to a routing string; complex → LLM fallback.
    """
    try:
        data = json.loads(qsf_text)
    except json.JSONDecodeError as exc:
        log.warning("parse_qualtrics_qsf: JSON parse error — %s", exc)
        return []

    elements   = data.get("SurveyElements", [])
    sq_payloads = [
        e["Payload"] for e in elements
        if e.get("Element") == "SQ" and isinstance(e.get("Payload"), dict)
    ]

    if not sq_payloads:
        log.warning("parse_qualtrics_qsf: no SQ elements found")
        return []

    results   = []
    llm_count = 0

    for payload in sq_payloads:
        qid = payload.get("QuestionID", f"QID{len(results) + 1}")

        # Strip HTML tags from question text
        raw_text = payload.get("QuestionText", "") or ""
        text     = re.sub(r"<[^>]+>", "", raw_text).strip()

        qtype_raw = payload.get("QuestionType", "")
        selector  = payload.get("Selector",     "")
        q_type    = TYPE_MAP_QUALTRICS.get((qtype_raw, selector), "UNKNOWN")
        if q_type == "UNKNOWN" and qtype_raw:
            # Try with None selector as fallback
            q_type = TYPE_MAP_QUALTRICS.get((qtype_raw, None), "UNKNOWN")

        # Options from Choices; for Matrix prefer Answers (scale values)
        if qtype_raw in ("Matrix", "Grid"):
            raw_choices = payload.get("Answers", {}) or {}
        else:
            raw_choices = payload.get("Choices", {}) or {}

        options = []
        for code, choice in sorted(raw_choices.items(), key=lambda x: x[0]):
            opt_text = choice.get("Display", "") if isinstance(choice, dict) else str(choice)
            options.append(make_option(code, opt_text))

        # Routing from DisplayLogic
        display_logic = payload.get("DisplayLogic")
        routing       = _qualtrics_logic_to_str(display_logic) if display_logic else None

        raw_block = "# Qualtrics SQ element\n" + json.dumps(payload, ensure_ascii=False, indent=2)

        q = make_question(qid, text, q_type, options, routing)
        triggers = _check_fallback_triggers(q)

        if triggers:
            llm_count += 1
            log.info("parse_qualtrics_qsf: LLM fallback QID=%s fields=%s", qid, triggers)
            q = _llm_enrich(qid, raw_block, q, triggers)

        results.append(q)

    log.info(
        "parse_qualtrics_qsf: Parsed %d questions: %d via code, %d via LLM fallback",
        len(results), len(results) - llm_count, llm_count,
    )
    return results


# ── Parser 4 — Forsta XML (router) ───────────────────────────────────────────

def parse_forsta_xml(xml_text: str) -> list:
    """
    Forsta uses Decipher XML format (Forsta Plus).
    If Confirmit-style XML is detected instead (post-acquisition merge),
    route to parse_confirmit_xml.
    """
    sniff = xml_text.lower()
    if any(m in sniff for m in ("<variables>", "confirmit:code", "<surveydef")):
        log.info("parse_forsta_xml: Confirmit-style XML detected — routing to parse_confirmit_xml")
        return parse_confirmit_xml(xml_text)
    log.info("parse_forsta_xml: routing to parse_decipher_xml")
    return parse_decipher_xml(xml_text)


# ── Parser 5 — Generic XML fallback ──────────────────────────────────────────

_GENERIC_Q_TAGS = {"question", "item", "variable", "radio", "checkbox", "q", "element"}

def parse_generic_xml(xml_text: str) -> list:
    """
    Fallback for unknown XML survey formats.
    Searches for any element whose tag looks question-like.
    Always triggers LLM enrichment (type and options are always unknown).
    Logs a warning about low-confidence output.
    """
    log.warning("parse_generic_xml: unknown XML format — attempting best-effort extraction")

    try:
        root = ET.fromstring(xml_text.lstrip("﻿"))
    except ET.ParseError as exc:
        log.warning("parse_generic_xml: XML parse error — %s", exc)
        return []

    candidates = [el for el in root.iter() if _local_tag(el) in _GENERIC_Q_TAGS]

    if not candidates:
        log.warning("parse_generic_xml: no known question tags — passing full XML to LLM")
        from normalizer import normalize_chunk
        full_text = "# Unknown survey XML\n" + ET.tostring(root, encoding="unicode")
        results = normalize_chunk(full_text, _get_llm_model())
        log.warning(
            "parse_generic_xml: LLM returned %d questions from full-XML call", len(results)
        )
        return results

    results   = []
    llm_count = 0

    for el in candidates:
        tag = _local_tag(el)
        qid = (
            el.get("label") or el.get("name") or
            el.get("id")    or f"Q{len(results) + 1}"
        )
        # Collect text content from direct children only (avoid deep duplication)
        text_parts = []
        for child in el:
            if child.text and child.text.strip():
                text_parts.append(child.text.strip())
        text = " ".join(text_parts[:3])

        raw_block = f"# Unknown XML element: {tag}\n" + ET.tostring(el, encoding="unicode")

        q = make_question(qid, text, "UNKNOWN")
        # Type is always unknown in generic mode; always call LLM
        triggers = ["type", "options"] if not text else ["type", "options", "routing"]
        llm_count += 1
        log.info("parse_generic_xml: LLM fallback QID=%s fields=%s", qid, triggers)
        q = _llm_enrich(qid, raw_block, q, triggers)
        results.append(q)

    log.warning(
        "parse_generic_xml: Parsed %d questions (low confidence): %d via code, %d via LLM",
        len(results), len(results) - llm_count, llm_count,
    )
    return results


# ── Schema helpers (used by all parsers above) ────────────────────────────────

def make_question(
    qid: str,
    text: str,
    q_type: str = "UNKNOWN",
    options: list = None,
    routing: str = None,
    termination: str = None,
    is_matrix_group: bool = False,
) -> dict:
    """
    Build a question dict matching the normalizer.py output schema.
    Call this from every format-specific parser so the shape is guaranteed.
    NUMERIC added to valid_types in Phase 2.
    """
    valid_types = {"UNIQUE", "MULTIPLE", "OPEN", "MATRIX", "GRID", "NUMERIC", "UNKNOWN"}
    if q_type not in valid_types:
        q_type = "UNKNOWN"

    return {
        "qid":             qid,
        "qid_normalized":  _normalize_qid(qid),
        "text":            text,
        "type":            q_type,
        "options":         options if options is not None else [],
        "routing":         routing or None,
        "termination":     termination or None,
        "is_matrix_group": bool(is_matrix_group),
    }


def make_option(code=None, text="", marker=None) -> dict:
    """Build an option dict matching the normalizer.py options schema."""
    return {
        "code":   str(code) if code is not None else None,
        "text":   text,
        "marker": marker or None,
    }
