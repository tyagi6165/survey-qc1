"""
================================================================
  Survey QC Web App — v8.0
================================================================
Web-based survey QC tool. No terminal needed!

Run with:
    streamlit run app.py

Features:
- Upload .docx → AI extracts logic in ANY language
- Gemini-powered (free API)
- Browser-based UI (no terminal)
- Word report download
"""

import streamlit as st
import os
import sys
from pathlib import Path
import tempfile
from datetime import datetime
import json

# Import our modules
from doc_analyzer import analyze_document
from llm_extractor import configure_gemini, extract_all_logic_tables


# ============================================
# PAGE CONFIG
# ============================================
st.set_page_config(
    page_title="Survey QC Tool",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================
# CUSTOM CSS — Make it look professional
# ============================================
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1F4E79;
        margin-bottom: 0;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #666;
        margin-top: 0;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: #f0f7ff;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #1F4E79;
    }
    .success-badge {
        background: #d4edda;
        color: #155724;
        padding: 0.5rem 1rem;
        border-radius: 6px;
        display: inline-block;
        font-weight: 600;
    }
    .warning-badge {
        background: #fff3cd;
        color: #856404;
        padding: 0.5rem 1rem;
        border-radius: 6px;
        display: inline-block;
        font-weight: 600;
    }
    .terminate-rule {
        background: #fff5f5;
        border-left: 4px solid #c00000;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .simple-rule {
        background: #f0fff4;
        border-left: 4px solid #2d8a3e;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .compound-rule {
        background: #fff8e1;
        border-left: 4px solid #f57c00;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .stButton button {
        background: #1F4E79;
        color: white;
        font-weight: 600;
        padding: 0.5rem 2rem;
        border-radius: 6px;
        border: none;
    }
    .stButton button:hover {
        background: #2c6094;
    }
</style>
""", unsafe_allow_html=True)


# ============================================
# HEADER
# ============================================
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown('<h1 class="main-header">📊 Survey QC Tool</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="sub-header">AI-powered survey quality check — works with ANY language</p>',
        unsafe_allow_html=True
    )
with col2:
    st.markdown("**v8.0** — Powered by Gemini AI")


# ============================================
# SIDEBAR — API Key & Settings
# ============================================
with st.sidebar:
    st.header("🔑 Setup")

    # Try to load saved key from session state
    if 'gemini_api_key' not in st.session_state:
        st.session_state.gemini_api_key = ""

    api_key = st.text_input(
        "Gemini API Key",
        value=st.session_state.gemini_api_key,
        type="password",
        help="Get free key from: https://aistudio.google.com/app/apikey",
        placeholder="AIzaSy..."
    )

    if api_key:
        st.session_state.gemini_api_key = api_key
        st.success("✅ API Key set")

    st.markdown("---")

    st.header("⚙️ Settings")
    fuzzy_threshold = st.slider(
        "Match strictness",
        min_value=0.4, max_value=0.95, value=0.65, step=0.05,
        help="Higher = stricter text matching"
    )

    test_mode = st.radio(
        "Test mode",
        ["Document only (fast)", "Document + Live URL (full)"],
        help="Pehle 'Document only' try karo"
    )

    st.markdown("---")

    st.header("ℹ️ Help")
    with st.expander("📖 How to use"):
        st.markdown("""
        1. **Get free Gemini API key** from [aistudio.google.com](https://aistudio.google.com/app/apikey)
        2. **Paste API key** in sidebar
        3. **Upload .docx** screener document
        4. (Optional) Add survey URL for live testing
        5. Click **"Run QC Analysis"**
        6. Download your report
        """)

    with st.expander("🌍 Supported languages"):
        st.markdown("""
        - ✅ English
        - ✅ French (Français)
        - ✅ Italian (Italiano)
        - ✅ Spanish (Español)
        - ✅ German (Deutsch)
        - ✅ Hindi (हिन्दी)
        - ✅ Urdu (اردو)
        - ✅ Aur 80+ aur languages

        AI automatically detect karta hai!
        """)


# ============================================
# MAIN AREA
# ============================================

# Check if API key is set
if not st.session_state.gemini_api_key:
    st.warning("⚠️ Pehle sidebar mein **Gemini API Key** paste karo!")
    st.info("""
    **API key kahan se lo (free, 1 minute):**
    1. Click: https://aistudio.google.com/app/apikey
    2. "Create API key in new project" click karo
    3. Key copy karke sidebar mein paste karo
    """)
    st.stop()


# ============================================
# UPLOAD SECTION
# ============================================
st.header("📤 Upload & Configure")

col1, col2 = st.columns(2)

with col1:
    uploaded_file = st.file_uploader(
        "Screener Document (.docx)",
        type=['docx'],
        help="Kisi bhi language ka screener doc upload karo"
    )

with col2:
    survey_url = st.text_input(
        "Survey URL (optional)",
        placeholder="https://questionnaire.example.com/...",
        help="Live link testing ke liye"
    )
    country = st.text_input(
        "Country (if needed)",
        placeholder="Italy, France, Spain, etc.",
        help="Live link mein country select karne ke liye"
    )


# ============================================
# RUN BUTTON
# ============================================
st.markdown("---")

if uploaded_file is None:
    st.info("👆 Pehle screener document upload karo")
    st.stop()

# Show file info
file_size_kb = len(uploaded_file.getvalue()) / 1024
st.success(f"✅ **{uploaded_file.name}** uploaded ({file_size_kb:.1f} KB)")

if st.button("🚀 Run QC Analysis", type="primary", use_container_width=True):

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        # ============================================
        # PHASE 1: Document Analysis
        # ============================================
        st.markdown("### 📄 Phase 1: Document Parsing")

        with st.spinner("Document parse kar raha hoon..."):
            try:
                analysis = analyze_document(tmp_path)
            except Exception as e:
                st.error(f"❌ Document parse nahi hua: {e}")
                st.stop()

        # Show parsing results
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📋 Questions", analysis['stats']['total_questions'])
        with col2:
            st.metric("📊 LOGIC Tables", analysis['stats']['logic_tables_found'])
        with col3:
            st.metric("✏️ With Options", analysis['stats']['questions_with_options'])

        if analysis['stats']['logic_tables_found'] == 0:
            st.warning("⚠️ Document mein LOGIC tables nahi mile. Kya ye sahi screener doc hai?")
            st.stop()

        # ============================================
        # PHASE 2: AI Logic Extraction
        # ============================================
        st.markdown("---")
        st.markdown("### 🤖 Phase 2: AI Logic Extraction")

        # Configure Gemini
        try:
            configure_gemini(st.session_state.gemini_api_key)
        except Exception as e:
            st.error(f"❌ Gemini setup fail: {e}")
            st.stop()

        # Progress bar for table processing
        n_tables = len(analysis['logic_tables'])
        progress_bar = st.progress(0, text=f"AI processing 0/{n_tables} tables...")

        with st.spinner(f"AI {n_tables} LOGIC tables analyze kar raha hai..."):
            all_rules, summary = extract_all_logic_tables(analysis['logic_tables'])
            progress_bar.progress(1.0, text=f"✅ {n_tables}/{n_tables} tables done")

        # Show errors if any
        if summary['errors']:
            with st.expander(f"⚠️ {len(summary['errors'])} tables had errors", expanded=False):
                for err in summary['errors']:
                    st.error(f"Table {err['table_idx']} ({err['host_qid']}): {err['error']}")

        # ============================================
        # PHASE 3: Results Display
        # ============================================
        st.markdown("---")
        st.markdown("### 📊 Phase 3: Results")

        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Rules", summary['total_rules'])
        with col2:
            st.metric("🛑 Terminate", summary['terminate_rules'])
        with col3:
            st.metric("✅ Simple", summary['simple_terminate'], help="Auto-testable")
        with col4:
            st.metric("⚠️ Compound", summary['compound_terminate'], help="Manual review needed")

        # Tabs for different views
        tab1, tab2, tab3 = st.tabs([
            "🛑 Termination Rules",
            "📋 All Questions",
            "📊 Raw Data"
        ])

        with tab1:
            terminate_rules = [r for r in all_rules if r.get('action') == 'terminate']
            if not terminate_rules:
                st.info("Koi termination rule nahi mila")
            else:
                # Group by complexity
                simple_rules = [r for r in terminate_rules if r.get('complexity') == 'simple']
                compound_rules = [r for r in terminate_rules if r.get('complexity') == 'compound']

                if simple_rules:
                    st.markdown(f"#### ✅ Simple Termination Rules ({len(simple_rules)})")
                    st.caption("Ye rules tool auto-test kar sakta hai")
                    for rule in simple_rules:
                        st.markdown(
                            f'<div class="simple-rule">'
                            f'<strong>🛑 {rule.get("test_qid")} = code {rule.get("answer_code")}</strong><br>'
                            f'<small>{rule.get("reason", "")}</small>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

                if compound_rules:
                    st.markdown(f"#### ⚠️ Compound Logic Rules ({len(compound_rules)})")
                    st.caption("In rules ko manual review chahiye (cross-question logic)")
                    for rule in compound_rules:
                        st.markdown(
                            f'<div class="compound-rule">'
                            f'<strong>🛑 {rule.get("test_qid")} = code {rule.get("answer_code")}</strong><br>'
                            f'<small>{rule.get("reason", "")}</small>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

        with tab2:
            st.markdown(f"#### All Questions ({len(analysis['questions'])})")
            for qid, qdata in analysis['questions'].items():
                with st.expander(f"**{qid}** — {qdata['text'][:80]}..."):
                    st.write(f"**Full text:** {qdata['text']}")
                    if qdata['options']:
                        st.write(f"**Options ({len(qdata['options'])}):**")
                        for opt in qdata['options']:
                            st.write(f"  - `{opt['code']}` → {opt['text']}")
                    if qdata.get('is_mandatory'):
                        st.write("✅ Mandatory")

        with tab3:
            st.markdown("#### Extracted Rules (JSON)")
            st.json(all_rules)

        # ============================================
        # PHASE 4: Download
        # ============================================
        st.markdown("---")
        st.markdown("### 📥 Download Results")

        # Create downloadable JSON
        result_data = {
            "generated_at": datetime.now().isoformat(),
            "document": uploaded_file.name,
            "summary": summary,
            "rules": all_rules,
            "questions": {
                qid: {
                    "text": q["text"],
                    "options_count": len(q["options"]),
                    "is_mandatory": q.get("is_mandatory", False)
                }
                for qid, q in analysis['questions'].items()
            }
        }

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "📄 Download Full Report (JSON)",
                data=json.dumps(result_data, indent=2, ensure_ascii=False),
                file_name=f"qc_report_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime="application/json",
                use_container_width=True
            )

        with col2:
            # Create a simple text summary
            summary_text = f"""SURVEY QC REPORT
Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}
Document: {uploaded_file.name}

========================================
SUMMARY
========================================
- Questions found: {analysis['stats']['total_questions']}
- LOGIC tables: {analysis['stats']['logic_tables_found']}
- Total rules extracted: {summary['total_rules']}
- Termination rules: {summary['terminate_rules']}
  - Simple (auto-testable): {summary['simple_terminate']}
  - Compound (need review): {summary['compound_terminate']}

========================================
TERMINATION RULES
========================================
"""
            for rule in terminate_rules:
                summary_text += f"\n[{rule.get('complexity', '').upper()}] {rule.get('test_qid')} = code {rule.get('answer_code')}"
                summary_text += f"\n  Reason: {rule.get('reason', '')}\n"

            st.download_button(
                "📝 Download Summary (TXT)",
                data=summary_text,
                file_name=f"qc_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                mime="text/plain",
                use_container_width=True
            )

        # Success message
        st.markdown("---")
        st.success(f"""
        ### 🎉 QC Analysis Complete!

        - **{analysis['stats']['total_questions']}** questions parsed
        - **{summary['terminate_rules']}** termination rules extracted by AI
        - **{summary['simple_terminate']}** rules ready for auto-testing
        - Document language detected and handled automatically
        """)

    finally:
        # Cleanup temp file
        try:
            os.unlink(tmp_path)
        except:
            pass


# ============================================
# FOOTER
# ============================================
st.markdown("---")
st.caption("Built with Streamlit + Gemini AI | v8.0 | © 2026")
