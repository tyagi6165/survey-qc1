"""
================================================================
  Survey QC Tool — v8.1
  Streamlit Web App
================================================================
8 Phase 1 checks:
  1. Termination rules (AI, batched)
  2. Missing words
  3. Question text match
  4. Options match
  5. Mandatory markers
  6. Piping markers
  7. Answer codes
  8. Question order
"""

import streamlit as st
import os
import json
import tempfile
from datetime import datetime
from docx import Document as DocxDocument

import google.generativeai as genai
from qc_engine import parse_document, run_all_checks

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="Survey QC Tool v8.1",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.main-title { font-size:2.2rem; font-weight:700; color:#1F4E79; }
.sub-title { color:#666; margin-top:-10px; margin-bottom:20px; }
.check-pass { background:#d4edda; border-left:4px solid #28a745;
              padding:8px 12px; border-radius:4px; margin:4px 0; }
.check-fail-high { background:#f8d7da; border-left:4px solid #dc3545;
                   padding:8px 12px; border-radius:4px; margin:4px 0; }
.check-fail-med  { background:#fff3cd; border-left:4px solid #ffc107;
                   padding:8px 12px; border-radius:4px; margin:4px 0; }
.check-info { background:#d1ecf1; border-left:4px solid #17a2b8;
              padding:8px 12px; border-radius:4px; margin:4px 0; }
.verdict-pass { background:#d4edda; color:#155724; padding:16px;
                border-radius:8px; text-align:center; font-size:1.3rem; font-weight:700; }
.verdict-fail { background:#f8d7da; color:#721c24; padding:16px;
                border-radius:8px; text-align:center; font-size:1.3rem; font-weight:700; }
.verdict-review { background:#fff3cd; color:#856404; padding:16px;
                  border-radius:8px; text-align:center; font-size:1.3rem; font-weight:700; }
.metric-box { background:#f0f7ff; padding:12px; border-radius:8px;
              border-left:4px solid #1F4E79; margin:4px; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### 🔑 API Setup")

    # Load from Streamlit secrets first
    if 'api_key' not in st.session_state:
        try:
            st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            st.session_state.api_key = ""

    if st.session_state.api_key:
        st.success("✅ API Key ready")
        override = st.text_input("Change API key", type="password",
                                  placeholder="Paste new key to override...",
                                  label_visibility="collapsed")
        if override:
            st.session_state.api_key = override
            st.success("✅ Updated")
    else:
        key_input = st.text_input("Gemini API Key", type="password",
                                   placeholder="AIzaSy...",
                                   help="Free key: aistudio.google.com/app/apikey")
        if key_input:
            st.session_state.api_key = key_input
            st.success("✅ Key set")

    st.markdown("---")
    st.markdown("### ⚙️ Settings")

    threshold = st.slider("Text match threshold", 0.4, 0.95, 0.65, 0.05,
                          help="Higher = stricter matching")

    st.markdown("---")
    st.markdown("### ℹ️ Phase 1 Checks")
    st.markdown("""
    1. 🛑 Termination rules
    2. 📝 Missing words
    3. 🔤 Question text
    4. 📋 Options match
    5. ⭐ Mandatory markers
    6. 🔗 Piping markers
    7. 🔢 Answer codes
    8. 📊 Question order
    """)


# ============================================================
# HEADER
# ============================================================
st.markdown('<p class="main-title">📊 Survey QC Tool</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-title">v8.1 — AI-powered · Any language · 8 checks</p>',
            unsafe_allow_html=True)

# API key check
if not st.session_state.get('api_key'):
    st.warning("⚠️ Sidebar mein Gemini API Key paste karo pehle!")
    st.info("Free key: https://aistudio.google.com/app/apikey — No credit card needed")
    st.stop()


# ============================================================
# INPUT SECTION
# ============================================================
st.markdown("## 📤 Upload & Configure")

col1, col2 = st.columns([1, 1])

with col1:
    st.markdown("**📄 Screener Document (.docx)**")
    uploaded = st.file_uploader("", type=["docx"], label_visibility="collapsed")
    if uploaded:
        st.success(f"✅ {uploaded.name} ({len(uploaded.getvalue())//1024} KB)")

with col2:
    st.markdown("**🔗 Survey URL**")
    survey_url = st.text_input("",
        placeholder="https://questionnaire.example.com/...",
        label_visibility="collapsed",
        help="Live survey link for full QC"
    )

    country = st.text_input("🌍 Country (if needed)",
        placeholder="Italy, France, Spain...",
        help="Country to select on survey landing page"
    )

    if survey_url:
        if not survey_url.startswith(("http://", "https://")):
            st.error("❌ URL http:// ya https:// se start hona chahiye")
            survey_url = None
        else:
            st.success("✅ URL set")


# ============================================================
# RUN BUTTON
# ============================================================
st.markdown("---")

if not uploaded:
    st.info("👆 Pehle screener .docx upload karo")
    st.stop()

if not survey_url:
    st.warning("⚠️ Survey URL daalo — live QC ke liye zaroori hai")
    # Allow doc-only mode with confirmation
    doc_only = st.checkbox("Abhi sirf Document analysis karo (URL baad mein)")
    if not doc_only:
        st.stop()
else:
    doc_only = False

run_btn = st.button("🚀 Run QC Analysis", type="primary", use_container_width=True)

if not run_btn:
    st.stop()


# ============================================================
# RUN QC
# ============================================================

# Setup Gemini
try:
    genai.configure(api_key=st.session_state.api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
except Exception as e:
    st.error(f"❌ Gemini setup failed: {e}")
    st.stop()

# Save uploaded file
with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
    tmp.write(uploaded.getvalue())
    tmp_path = tmp.name

try:
    # ── PHASE 1: Parse Document ──────────────────────────────
    st.markdown("---")
    st.markdown("### 📄 Phase 1: Document Parsing")

    with st.spinner("Document parse ho raha hai..."):
        doc_data = parse_document(tmp_path)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions", doc_data['stats']['total_questions'])
    c2.metric("LOGIC Tables", doc_data['stats']['logic_tables'])
    c3.metric("With Options", doc_data['stats']['with_options'])
    c4.metric("With Piping", doc_data['stats']['with_piping'])

    if doc_data['stats']['total_questions'] == 0:
        st.error("❌ Document mein koi question nahi mila — sahi .docx hai?")
        st.stop()

    st.success(f"✅ {doc_data['stats']['total_questions']} questions parsed successfully")

    # ── PHASE 2: Run All 8 Checks ────────────────────────────
    st.markdown("---")
    st.markdown("### 🔍 Phase 2: Running 8 QC Checks")

    progress = st.progress(0)
    status_text = st.empty()

    # Checks 1-8 progress updates
    status_text.text("⏳ Check 1/8: Extracting termination rules (AI)...")
    progress.progress(10)

    results = run_all_checks(
        doc_data=doc_data,
        live_questions=None,  # Crawler v8.2 mein add hoga
        gemini_model=model,
        threshold=threshold
    )

    progress.progress(100)
    status_text.text("✅ All checks complete!")

    # ── PHASE 3: Results ─────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Phase 3: Results")

    summary = results["summary"]

    # Verdict
    if summary["verdict"] == "PASS":
        st.markdown(f'<div class="verdict-pass">{summary["verdict_msg"]}</div>',
                    unsafe_allow_html=True)
    elif summary["verdict"] == "FAIL":
        st.markdown(f'<div class="verdict-fail">{summary["verdict_msg"]}</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="verdict-review">{summary["verdict_msg"]}</div>',
                    unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Summary metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("🛑 Terminate Rules", summary["termination_rules_found"])
    m2.metric("✅ Auto-testable", summary["terminate_simple"])
    m3.metric("⚠️ Compound", summary["terminate_compound"])
    m4.metric("❌ HIGH Issues", summary["severity"]["HIGH"])
    m5.metric("⚠️ MEDIUM Issues", summary["severity"]["MEDIUM"])

    st.markdown("---")

    # ── 8 CHECK TABS ─────────────────────────────────────────
    tabs = st.tabs([
        "🛑 Termination",
        "📝 Words",
        "🔤 Text Match",
        "📋 Options",
        "⭐ Mandatory",
        "🔗 Piping",
        "🔢 Codes",
        "📊 Order"
    ])

    # TAB 1: Termination
    with tabs[0]:
        st.markdown("#### 🛑 Check 1: Termination Rules")
        term_rules = results["termination"]["rules"]
        meta = results["termination"]["meta"]

        if meta.get("status") == "error":
            st.error(f"AI Error: {meta.get('message')}")
        elif not term_rules:
            st.info("Koi termination rules nahi mile")
        else:
            terminate = [r for r in term_rules if r.get("action") == "terminate"]
            qualify = [r for r in term_rules if r.get("action") == "qualify"]
            compound = [r for r in terminate if r.get("complexity") == "compound"]
            simple = [r for r in terminate if r.get("complexity") == "simple"]

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Rules", len(term_rules))
            c2.metric("Simple (auto-test)", len(simple))
            c3.metric("Compound (manual)", len(compound))

            if simple:
                st.markdown(f"**✅ Simple Rules ({len(simple)}) — Auto-testable:**")
                for r in simple:
                    st.markdown(
                        f'<div class="check-pass">🛑 <b>{r.get("test_qid")}</b> = '
                        f'code <b>{r.get("answer_code")}</b> → TERMINATE | '
                        f'{r.get("reason","")[:80]}</div>',
                        unsafe_allow_html=True
                    )

            if compound:
                st.markdown(f"**⚠️ Compound Rules ({len(compound)}) — Manual Review:**")
                for r in compound:
                    st.markdown(
                        f'<div class="check-fail-med">⚠️ <b>{r.get("test_qid")}</b> — '
                        f'{r.get("reason","")[:100]}</div>',
                        unsafe_allow_html=True
                    )

            if qualify:
                st.markdown(f"**ℹ️ Qualify Rules ({len(qualify)}):**")
                for r in qualify:
                    st.markdown(
                        f'<div class="check-info">✅ <b>{r.get("test_qid")}</b> = '
                        f'code <b>{r.get("answer_code")}</b> → QUALIFY</div>',
                        unsafe_allow_html=True
                    )

    # TAB 2-4: Need live URL
    for tab_idx, (tab, key, name) in enumerate(zip(
        tabs[1:4],
        ["missing_words", "text_match", "options_match"],
        ["Missing Words", "Text Match", "Options Match"]
    )):
        with tab:
            issues = results[key]
            st.markdown(f"#### Check {tab_idx+2}: {name}")
            if not survey_url or doc_only:
                st.warning("⚠️ Live URL required for this check — abhi doc-only mode mein ho")
                st.info("URL add karke dobara run karo — live survey se compare hoga")
            elif not issues:
                st.markdown('<div class="check-pass">✅ No issues found</div>',
                            unsafe_allow_html=True)
            else:
                for issue in issues:
                    sev = issue.get("severity", "INFO")
                    css = "check-fail-high" if sev == "HIGH" else "check-fail-med"
                    st.markdown(
                        f'<div class="{css}">❌ <b>{issue["qid"]}</b>: '
                        f'{issue["details"][:150]}</div>',
                        unsafe_allow_html=True
                    )

    # TAB 5: Mandatory
    with tabs[4]:
        st.markdown("#### ⭐ Check 5: Mandatory Markers")
        issues = results["mandatory"]
        if not issues:
            st.markdown('<div class="check-pass">✅ No mandatory marker issues</div>',
                        unsafe_allow_html=True)
        else:
            for issue in issues:
                sev = issue.get("severity", "INFO")
                css = ("check-fail-med" if sev == "MEDIUM"
                       else "check-info" if sev == "INFO"
                       else "check-fail-high")
                icon = "⭐" if sev == "INFO" else "❌"
                st.markdown(
                    f'<div class="{css}">{icon} <b>{issue["qid"]}</b>: '
                    f'{issue["details"]}</div>',
                    unsafe_allow_html=True
                )

    # TAB 6: Piping
    with tabs[5]:
        st.markdown("#### 🔗 Check 6: Piping Markers")
        issues = results["piping"]
        if not issues:
            st.markdown('<div class="check-pass">✅ No raw piping markers found</div>',
                        unsafe_allow_html=True)
        else:
            for issue in issues:
                sev = issue.get("severity", "INFO")
                css = "check-fail-high" if sev == "HIGH" else "check-info"
                icon = "❌" if sev == "HIGH" else "ℹ️"
                st.markdown(
                    f'<div class="{css}">{icon} <b>{issue["qid"]}</b>: '
                    f'{issue["details"][:150]}</div>',
                    unsafe_allow_html=True
                )

    # TAB 7: Answer Codes
    with tabs[6]:
        st.markdown("#### 🔢 Check 7: Answer Code Sequence")
        issues = results["answer_codes"]
        if not issues:
            st.markdown('<div class="check-pass">✅ All answer codes sequential — no gaps</div>',
                        unsafe_allow_html=True)
        else:
            for issue in issues:
                st.markdown(
                    f'<div class="check-fail-med">⚠️ <b>{issue["qid"]}</b>: '
                    f'{issue["details"]}</div>',
                    unsafe_allow_html=True
                )

    # TAB 8: Question Order
    with tabs[7]:
        st.markdown("#### 📊 Check 8: Question Order")
        issues = results["question_order"]
        if not issues:
            st.markdown('<div class="check-pass">✅ Question order is correct</div>',
                        unsafe_allow_html=True)
        else:
            for issue in issues:
                st.markdown(
                    f'<div class="check-fail-med">⚠️ <b>{issue["qid"]}</b>: '
                    f'{issue["details"]}</div>',
                    unsafe_allow_html=True
                )

    # ── DOWNLOAD ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📥 Download Report")

    term_rules = results["termination"]["rules"]
    terminate_simple = [r for r in term_rules
                        if r.get("action") == "terminate" and r.get("complexity") == "simple"]
    terminate_compound = [r for r in term_rules
                          if r.get("action") == "terminate" and r.get("complexity") == "compound"]

    report_txt = f"""SURVEY QC REPORT v8.1
Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}
Document:  {uploaded.name}
URL:       {survey_url or 'Not provided (doc-only mode)'}
Mode:      {'Document only' if doc_only else 'Full QC'}
Verdict:   {summary['verdict']} — {summary['verdict_msg']}

{'='*60}
SUMMARY
{'='*60}
Questions parsed:        {doc_data['stats']['total_questions']}
LOGIC tables found:      {doc_data['stats']['logic_tables']}
Termination rules:       {len(term_rules)}
  Simple (auto-test):    {len(terminate_simple)}
  Compound (manual):     {len(terminate_compound)}
Total issues found:      {summary['total_issues']}
  HIGH severity:         {summary['severity']['HIGH']}
  MEDIUM severity:       {summary['severity']['MEDIUM']}
  INFO:                  {summary['severity']['INFO']}

{'='*60}
CHECK 1: TERMINATION RULES
{'='*60}"""

    for r in term_rules:
        action = r.get('action', '').upper()
        complexity = r.get('complexity', '')
        icon = "TERMINATE" if action == "TERMINATE" else "QUALIFY"
        report_txt += f"\n[{icon}][{complexity.upper()}] {r.get('test_qid')} = code {r.get('answer_code')}"
        report_txt += f"\n  Reason: {r.get('reason', '')}\n"

    for check_name, check_key in [
        ("CHECK 5: MANDATORY MARKERS", "mandatory"),
        ("CHECK 6: PIPING MARKERS", "piping"),
        ("CHECK 7: ANSWER CODES", "answer_codes"),
        ("CHECK 8: QUESTION ORDER", "question_order"),
    ]:
        issues = results[check_key]
        report_txt += f"\n{'='*60}\n{check_name}\n{'='*60}\n"
        if not issues:
            report_txt += "✅ PASS — No issues found\n"
        else:
            for issue in issues:
                report_txt += f"[{issue.get('severity','?')}] {issue['qid']}: {issue['details']}\n"

    report_txt += f"\n{'='*60}\n— End of Report — Survey QC Tool v8.1\n"

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "📄 Download Report (TXT)",
            data=report_txt,
            file_name=f"QC_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
            mime="text/plain",
            use_container_width=True
        )
    with col2:
        st.download_button(
            "📊 Download Raw Data (JSON)",
            data=json.dumps(results, indent=2, ensure_ascii=False),
            file_name=f"QC_Data_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            use_container_width=True
        )

finally:
    try:
        os.unlink(tmp_path)
    except Exception:
        pass

st.markdown("---")
st.caption("Survey QC Tool v8.1 · Streamlit + Gemini AI · Any language · © 2026")
