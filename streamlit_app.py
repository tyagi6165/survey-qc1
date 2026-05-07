import streamlit as st
import tempfile
from docx import Document
import re

st.set_page_config(page_title="Survey QC Tool", page_icon="🛡️")
st.title("🛡️ Survey QC Tool")
st.write("Confirmit surveys ki QC automate karo")

uploaded_doc = st.file_uploader("📄 Upload .docx", type=['docx'])
survey_url = st.text_input("🔗 Survey URL")
country = st.selectbox("🌍 Country", ["Italy", "France", "Spain", "Germany", "UK"])

if st.button("🚀 Run QC", type="primary"):
    if uploaded_doc and survey_url:
        with st.spinner("Analyzing..."):
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(uploaded_doc.getbuffer())
                doc = Document(tmp.name)
            
            qids = set()
            for para in doc.paragraphs:
                m = re.match(r'^\s*\[?\s*([RSQ]\d+)', para.text)
                if m: qids.add(m.group(1))
            
            st.success(f"✅ {len(qids)} questions found!")
            col1, col2, col3 = st.columns(3)
            col1.metric("Questions", len(qids))
            col2.metric("Country", country)
            col3.metric("Status", "Ready")
            st.write("Question IDs:", ", ".join(sorted(qids)))
    else:
        st.error("❌ Doc + URL dono daalo!")

st.caption("Built by Tushar | v1.0")
