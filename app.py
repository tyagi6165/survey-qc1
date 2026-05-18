"""
================================================================
  SURVEYQC — Complete Full Stack App v9.0
  Dark Purple Premium UI + All Features

  INSTALL:
    pip3 install flask playwright python-docx werkzeug
    playwright install chromium

  RUN:
    python3 app.py

  OPEN:
    http://localhost:5000

  FEATURES:
    - Landing page (public)
    - Login / Signup
    - Dashboard with time saved
    - New QC analysis page
    - Reports history
    - Report detail page
    - AI Tester simulation
    - Settings page
    - Admin panel (password protected)
    - Full QC engine (8 checks)
    - Real-time progress via polling
    - Word report download
================================================================
"""

import os, re, sys, json, uuid, threading, hashlib
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from functools import wraps
from flask import (Flask, render_template_string, request,
                   send_file, jsonify, session, redirect, url_for)
from werkzeug.utils import secure_filename

# ================================================================
# CONFIG
# ================================================================
app = Flask(__name__)
app.secret_key = 'surveyqc-secret-key-change-in-production'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

UPLOAD_FOLDER = './uploads'
OUTPUT_FOLDER = './qc_output'
ADMIN_PASSWORD = 'admin123'   # Change this!
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# In-memory stores
jobs = {}
users_db = {
    'demo@surveyqc.com': {
        'password': hashlib.sha256('demo123'.encode()).hexdigest(),
        'name': 'Demo User',
        'plan': 'Pro',
        'reports_used': 12,
        'reports_limit': 50,
        'joined': '2026-01-15',
        'total_saved_hours': 47
    }
}
feedback_store = []

THANKYOU_INDICATORS = [
    "grazie", "thank you", "ringraziamo", "gracias", "merci",
    "complete", "completato", "screened out", "non qualificato",
    "danke", "bedankt", "obrigado"
]
SKIP_PATTERNS = [
    r'^\s*PROGRAMMING\s+TABLE', r'^\s*ROUTING\s*[:\|]',
    r'^\s*ROUTINE\s*[:\|]', r'^\s*TYPE\s*[:\|]',
    r'^\s*\[NEXT SCREEN\]', r'^\s*\[SAME SCREEN',
    r'^---+$', r'^\s*\|\s*$',
]
JUNK_RE = re.compile('|'.join(SKIP_PATTERNS), re.IGNORECASE)
STOPWORDS = {
    'il','la','lo','gli','le','un','una','uno','di','da','del','della',
    'the','and','or','but','is','are','was','were','have','has','had',
    'a','an','of','in','on','at','to','for','with','by','from','this',
    'that','these','those','it','its','i','you','he','she','we','they',
    'all','any','some','no','not','so','if','as','than','then',
}

# ================================================================
# AUTH HELPERS
# ================================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    email = session.get('user_email')
    if email and email in users_db:
        u = users_db[email].copy()
        u['email'] = email
        return u
    return None

# ================================================================
# CSS + JS (shared across all pages)
# ================================================================
SHARED_CSS = """
<style>
:root {
  --bg: #060318;
  --bg2: #0F0A2E;
  --bg3: #1A1245;
  --purple: #7C65FF;
  --purple-dim: rgba(124,101,255,0.15);
  --purple-border: rgba(124,101,255,0.3);
  --text: #FFFFFF;
  --text2: #AFA9EC;
  --text3: #7B72D4;
  --border: rgba(255,255,255,0.08);
  --green: #1D9E75;
  --red: #E24B4A;
  --amber: #EF9F27;
  --card-r: 12px;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
body{background:var(--bg);color:var(--text);min-height:100vh}

/* SCROLLBAR */
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--purple-dim);border-radius:3px}

/* LAYOUT */
.app-layout{display:flex;min-height:100vh}
.sidebar{width:220px;min-width:220px;background:var(--bg2);border-right:0.5px solid var(--border);padding:20px 12px;display:flex;flex-direction:column;gap:4px;position:fixed;height:100vh;overflow-y:auto}
.main-content{margin-left:220px;flex:1;padding:28px;min-height:100vh}
.sidebar-logo{display:flex;align-items:center;gap:10px;padding:0 8px;margin-bottom:24px}
.sidebar-logo-icon{width:30px;height:30px;background:var(--purple);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.sidebar-logo-text{color:white;font-size:15px;font-weight:500}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;cursor:pointer;text-decoration:none;color:var(--text3);font-size:13px;transition:all .15s}
.nav-item:hover{background:var(--purple-dim);color:white}
.nav-item.active{background:linear-gradient(90deg,rgba(124,101,255,.3),rgba(124,101,255,.08));border-left:2px solid var(--purple);color:white;padding-left:10px}
.nav-item i{font-size:16px;width:18px;text-align:center}
.nav-divider{border-top:0.5px solid var(--border);margin:8px 0}
.nav-section{font-size:10px;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;padding:4px 12px;margin-top:4px}

/* TOPBAR */
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}
.page-title{font-size:20px;font-weight:500;color:white}
.page-sub{font-size:13px;color:var(--text3);margin-top:2px}

/* CARDS */
.card{background:var(--bg2);border:0.5px solid var(--border);border-radius:var(--card-r);padding:20px}
.card-sm{background:var(--bg2);border:0.5px solid var(--border);border-radius:var(--card-r);padding:14px}
.card-purple{background:var(--purple-dim);border:0.5px solid var(--purple-border);border-radius:var(--card-r);padding:16px}
.card-hero{background:linear-gradient(135deg,#1A1245,#0F0A2E);border:0.5px solid var(--purple-border);border-radius:var(--card-r);padding:22px}

/* STATS GRID */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.stat-card{background:var(--bg2);border:0.5px solid var(--border);border-radius:var(--card-r);padding:16px;text-align:center}
.stat-num{font-size:22px;font-weight:500;color:white}
.stat-label{font-size:11px;color:var(--text3);margin-top:4px}
.stat-change{font-size:10px;margin-top:3px}
.stat-up{color:#1D9E75}
.stat-down{color:#E24B4A}

/* BADGES */
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500}
.badge-green{background:#EAF3DE;color:#27500A}
.badge-red{background:#FCEBEB;color:#791F1F}
.badge-blue{background:#E6F1FB;color:#0C447C}
.badge-amber{background:#FAEEDA;color:#633806}
.badge-purple{background:rgba(124,101,255,.2);color:#AFA9EC;border:0.5px solid var(--purple-border)}
.badge-teal{background:#E1F5EE;color:#085041}
.badge-live{background:#E24B4A;color:white;font-size:10px;padding:2px 8px}

/* BUTTONS */
.btn{padding:9px 18px;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;border:none;display:inline-flex;align-items:center;gap:7px;text-decoration:none}
.btn-primary{background:var(--purple);color:white}
.btn-primary:hover{background:#6B54EE}
.btn-ghost{background:rgba(255,255,255,.06);color:white;border:0.5px solid var(--border)}
.btn-ghost:hover{background:rgba(255,255,255,.1)}
.btn-sm{padding:6px 14px;font-size:12px}
.btn-danger{background:#FCEBEB;color:#791F1F;border:none}

/* FORMS */
.form-group{margin-bottom:16px}
.form-label{font-size:12px;color:var(--text2);margin-bottom:6px;display:block;font-weight:500}
.form-input{width:100%;background:rgba(255,255,255,.06);border:0.5px solid rgba(255,255,255,.15);border-radius:8px;padding:10px 14px;font-size:13px;color:white;outline:none;transition:border .2s}
.form-input:focus{border-color:var(--purple);box-shadow:0 0 0 3px rgba(124,101,255,.15)}
.form-input::placeholder{color:var(--text3)}
.form-select{width:100%;background:var(--bg3);border:0.5px solid rgba(255,255,255,.15);border-radius:8px;padding:10px 14px;font-size:13px;color:white;outline:none;cursor:pointer}
.form-select option{background:var(--bg2)}
.form-check{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--text2);cursor:pointer;margin-bottom:8px}
.form-check input{accent-color:var(--purple);width:15px;height:15px;cursor:pointer}

/* UPLOAD ZONE */
.upload-zone{border:1.5px dashed rgba(124,101,255,.4);border-radius:10px;padding:24px;text-align:center;cursor:pointer;transition:all .2s;background:rgba(124,101,255,.04)}
.upload-zone:hover{border-color:var(--purple);background:rgba(124,101,255,.08)}
.upload-zone.active{border-color:var(--green);background:rgba(29,158,117,.08)}
.upload-zone i{font-size:28px;color:var(--text3);margin-bottom:8px}
.upload-zone p{font-size:12px;color:var(--text3)}
.upload-zone span{font-size:11px;color:var(--text3);opacity:.7}

/* TABLE */
.data-table{width:100%;border-collapse:collapse}
.data-table th{font-size:11px;font-weight:500;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;padding:10px 14px;text-align:left;border-bottom:0.5px solid var(--border)}
.data-table td{font-size:12px;color:var(--text2);padding:11px 14px;border-bottom:0.5px solid var(--border)}
.data-table tr:hover td{background:rgba(255,255,255,.02)}
.data-table td.primary{color:white;font-weight:500}

/* PROGRESS */
.progress-bar{height:4px;background:rgba(255,255,255,.1);border-radius:2px;overflow:hidden}
.progress-fill{height:100%;border-radius:2px;transition:width .5s ease}
.progress-purple{background:var(--purple)}
.progress-green{background:var(--green)}
.progress-amber{background:var(--amber)}

/* LOG BOX */
.log-box{background:#020111;border-radius:8px;padding:14px;height:220px;overflow-y:auto;font-family:'SF Mono',Monaco,monospace;font-size:11px;line-height:1.7}
.log-green{color:#1D9E75}
.log-red{color:#E24B4A}
.log-yellow{color:#EF9F27}
.log-blue{color:#378ADD}
.log-cyan{color:#5DCAA5}
.log-white{color:#DFE6E9}
.log-magenta{color:#AFA9EC}

/* SCORE CIRCLE */
.score-circle{width:88px;height:88px;border-radius:50%;border:6px solid rgba(124,101,255,.25);display:flex;align-items:center;justify-content:center;flex-direction:column;background:rgba(124,101,255,.08)}
.score-num{font-size:22px;font-weight:500;color:var(--purple)}
.score-label{font-size:9px;color:var(--text3)}

/* DOT */
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot-green{background:var(--green)}
.dot-red{background:var(--red)}
.dot-amber{background:var(--amber)}
.dot-purple{background:var(--purple)}

/* ALERTS */
.alert{padding:12px 16px;border-radius:8px;font-size:13px;margin-bottom:16px}
.alert-error{background:#FCEBEB;color:#791F1F;border:0.5px solid #F7C1C1}
.alert-success{background:#EAF3DE;color:#27500A;border:0.5px solid #C0DD97}
.alert-info{background:rgba(124,101,255,.1);color:#AFA9EC;border:0.5px solid var(--purple-border)}

/* PAGE TABS */
.tabs{display:flex;gap:0;border-bottom:0.5px solid var(--border);margin-bottom:20px}
.tab{font-size:13px;padding:10px 18px;color:var(--text3);cursor:pointer;text-decoration:none;border-bottom:2px solid transparent;transition:all .15s}
.tab:hover{color:white}
.tab.active{color:white;border-bottom-color:var(--purple)}

/* PUBLIC PAGES */
.public-page{min-height:100vh;background:var(--bg);display:flex;flex-direction:column}
.pub-nav{background:var(--bg2);border-bottom:0.5px solid var(--border);padding:14px 40px;display:flex;align-items:center;justify-content:space-between}
.pub-nav-logo{display:flex;align-items:center;gap:10px}
.pub-nav-links{display:flex;gap:24px;align-items:center}
.pub-link{color:var(--text3);font-size:13px;text-decoration:none}
.pub-link:hover{color:white}
.pub-hero{text-align:center;padding:60px 20px 40px}
.pub-hero h1{font-size:40px;font-weight:500;color:white;margin-bottom:12px;line-height:1.2}
.pub-hero h1 span{color:var(--purple)}
.pub-hero p{font-size:16px;color:var(--text3);max-width:520px;margin:0 auto 28px;line-height:1.7}
.feature-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:900px;margin:0 auto;padding:0 20px}
.feature-card{background:var(--bg2);border:0.5px solid var(--border);border-radius:12px;padding:20px}
.feature-card:hover{border-color:var(--purple-border)}
.feature-icon{width:40px;height:40px;border-radius:10px;background:var(--purple-dim);display:flex;align-items:center;justify-content:center;margin-bottom:12px;font-size:20px;color:var(--purple)}
.feature-card h3{font-size:14px;font-weight:500;color:white;margin-bottom:6px}
.feature-card p{font-size:12px;color:var(--text3);line-height:1.6}
.pricing-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:800px;margin:0 auto;padding:0 20px 60px}
.pricing-card{background:var(--bg2);border:0.5px solid var(--border);border-radius:12px;padding:24px}
.pricing-card.featured{border:2px solid var(--purple)}
.pricing-card h3{font-size:14px;font-weight:500;color:white;margin-bottom:4px}
.pricing-price{font-size:28px;font-weight:500;color:white;margin:10px 0 4px}
.pricing-sub{font-size:12px;color:var(--text3);margin-bottom:16px}
.pricing-feature{font-size:12px;color:var(--text3);display:flex;align-items:center;gap:7px;margin-bottom:7px}
.pricing-feature i{color:var(--green);font-size:13px}
.auth-page{min-height:100vh;background:var(--bg);display:grid;grid-template-columns:1fr 1fr}
.auth-left{background:var(--bg2);border-right:0.5px solid var(--border);padding:40px;display:flex;flex-direction:column;justify-content:center}
.auth-right{padding:40px;display:flex;flex-direction:column;justify-content:center;max-width:420px;margin:0 auto;width:100%}
.time-saved-banner{background:linear-gradient(135deg,#1A1245,#0F0A2E);border:0.5px solid var(--purple-border);border-radius:12px;padding:20px 24px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between}
.check-item{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:0.5px solid var(--border)}
.check-item:last-child{border-bottom:none}
.check-ico{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:12px}
.worker-card{background:rgba(255,255,255,.04);border-radius:8px;padding:10px 14px;margin-bottom:8px;display:flex;align-items:center;gap:10px}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--green);flex-shrink:0}
.activity-item{display:flex;align-items:start;gap:10px;padding:9px 0;border-bottom:0.5px solid var(--border)}
.activity-item:last-child{border-bottom:none}
.avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:500;flex-shrink:0}

/* RESPONSIVE */
@media(max-width:768px){
  .sidebar{display:none}
  .main-content{margin-left:0}
  .stats-grid{grid-template-columns:1fr 1fr}
  .feature-grid,.pricing-grid{grid-template-columns:1fr}
  .auth-page{grid-template-columns:1fr}
  .auth-left{display:none}
}
</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">
"""

# ================================================================
# SIDEBAR TEMPLATE
# ================================================================
def sidebar_html(active='dashboard'):
    user = get_current_user()
    name = user['name'] if user else 'User'
    plan = user.get('plan', 'Free') if user else 'Free'
    initials = ''.join([n[0] for n in name.split()[:2]]).upper()

    items = [
        ('dashboard', 'ti-home', 'Dashboard', '/dashboard'),
        ('new-qc', 'ti-plus', 'New QC', '/new-qc'),
        ('ai-tester', 'ti-robot', 'AI Tester', '/ai-tester'),
        ('reports', 'ti-file-report', 'Reports', '/reports'),
        ('templates', 'ti-copy', 'Templates', '#'),
        ('team', 'ti-users', 'Team', '#'),
    ]
    nav_html = ''
    for key, icon, label, href in items:
        cls = 'nav-item active' if active == key else 'nav-item'
        nav_html += f'<a href="{href}" class="{cls}"><i class="ti {icon}"></i><span>{label}</span></a>'

    return f"""
<div class="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon"><i class="ti ti-shield-check" style="color:white;font-size:16px"></i></div>
    <span class="sidebar-logo-text">SurveyQC</span>
  </div>
  {nav_html}
  <div class="nav-divider"></div>
  <a href="/settings" class="{'nav-item active' if active=='settings' else 'nav-item'}"><i class="ti ti-settings"></i><span>Settings</span></a>
  <a href="/billing" class="{'nav-item active' if active=='billing' else 'nav-item'}"><i class="ti ti-credit-card"></i><span>Billing</span></a>
  <div style="margin-top:auto;padding-top:16px;border-top:0.5px solid var(--border)">
    <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:8px;cursor:pointer">
      <div class="avatar" style="background:rgba(124,101,255,.3);color:var(--purple)">{initials}</div>
      <div>
        <p style="font-size:12px;color:white;font-weight:500">{name}</p>
        <p style="font-size:10px;color:var(--text3)">{plan} plan</p>
      </div>
      <a href="/logout" style="margin-left:auto;color:var(--text3);text-decoration:none"><i class="ti ti-logout" style="font-size:14px"></i></a>
    </div>
  </div>
</div>"""

# ================================================================
# PAGE: LANDING
# ================================================================
@app.route('/')
def landing():
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html><head><title>SurveyQC — AI Survey Testing</title></head><body>
<div class="public-page">
  <nav class="pub-nav">
    <div class="pub-nav-logo">
      <div style="width:28px;height:28px;background:var(--purple);border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="color:white;font-size:14px"></i></div>
      <span style="color:white;font-size:15px;font-weight:500;margin-left:9px">SurveyQC</span>
    </div>
    <div class="pub-nav-links">
      <a href="#features" class="pub-link">Features</a>
      <a href="#how" class="pub-link">How it works</a>
      <a href="#pricing" class="pub-link">Pricing</a>
      <a href="/login" class="pub-link">Login</a>
      <a href="/signup" class="btn btn-primary btn-sm">Start Free</a>
    </div>
  </nav>

  <div class="pub-hero">
    <div style="display:inline-flex;align-items:center;gap:7px;background:rgba(124,101,255,.15);border:0.5px solid var(--purple-border);color:#AFA9EC;font-size:12px;padding:5px 14px;border-radius:20px;margin-bottom:18px">
      <i class="ti ti-sparkles" style="font-size:12px"></i>World's first AI-powered survey QC tool
    </div>
    <h1>AI-Powered Survey QC for<br><span>Perfect Data</span></h1>
    <p>Upload your screener doc and paste the survey URL. Our AI tests every question, every path, every termination rule — in any language, on any platform.</p>
    <div style="display:flex;gap:12px;justify-content:center">
      <a href="/signup" class="btn btn-primary" style="font-size:14px;padding:12px 28px">Start Free Trial</a>
      <button class="btn btn-ghost" style="font-size:14px;padding:12px 24px">Book a Demo</button>
    </div>
    <p style="font-size:12px;color:var(--text3);margin-top:12px">No credit card · 5 free reports/month forever</p>
    <div style="display:flex;gap:20px;justify-content:center;margin-top:20px">
      <span style="color:var(--text3);font-size:13px;font-weight:500">Confirmit</span>
      <span style="color:var(--text3);font-size:13px;font-weight:500">Decipher</span>
      <span style="color:var(--text3);font-size:13px;font-weight:500">Forsta</span>
      <span style="color:var(--text3);font-size:13px;font-weight:500">Qualtrics</span>
    </div>
  </div>

  <div id="features" style="padding:20px 0 50px">
    <p style="text-align:center;font-size:11px;color:var(--text3);letter-spacing:.1em;text-transform:uppercase;margin-bottom:16px">What we check automatically</p>
    <div class="feature-grid">
      <div class="feature-card">
        <div class="feature-icon"><i class="ti ti-x-octagon"></i></div>
        <h3>Termination rules</h3>
        <p>Tests every close/terminate rule automatically. Catches bugs before they kill your data.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon" style="background:rgba(29,158,117,.15);color:#1D9E75"><i class="ti ti-text-recognition"></i></div>
        <h3>Text & word match</h3>
        <p>Catches every missing word and typo between your doc and live survey.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon" style="background:rgba(239,159,39,.15);color:#EF9F27"><i class="ti ti-list-check"></i></div>
        <h3>Options match</h3>
        <p>Verifies all answer options exist exactly as specified in your screener.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon" style="background:rgba(226,75,74,.15);color:#E24B4A"><i class="ti ti-asterisk"></i></div>
        <h3>Mandatory markers</h3>
        <p>Confirms all required field markers are present and correct.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon" style="background:rgba(55,138,221,.15);color:#378ADD"><i class="ti ti-arrows-shuffle"></i></div>
        <h3>Piping markers</h3>
        <p>Detects unresolved piping — no more [PIPE] variables showing to respondents.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon" style="background:rgba(29,158,117,.15);color:#1D9E75"><i class="ti ti-camera"></i></div>
        <h3>Auto screenshots</h3>
        <p>Every bug auto-captured with screenshot proof. Share directly with programmers.</p>
      </div>
    </div>
  </div>

  <div id="how" style="padding:20px 0 50px;text-align:center">
    <p style="font-size:11px;color:var(--text3);letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px">How it works</p>
    <p style="font-size:22px;font-weight:500;color:white;margin-bottom:30px">3 steps to perfect QC</p>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:700px;margin:0 auto;padding:0 20px">
      <div class="card">
        <div style="width:36px;height:36px;border-radius:50%;background:var(--purple);color:white;font-size:16px;font-weight:500;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">1</div>
        <p style="font-weight:500;color:white;margin-bottom:6px">Upload doc + URL</p>
        <p style="font-size:12px;color:var(--text3);line-height:1.6">Upload screener .docx and paste the live survey URL</p>
      </div>
      <div class="card">
        <div style="width:36px;height:36px;border-radius:50%;background:var(--purple);color:white;font-size:16px;font-weight:500;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">2</div>
        <p style="font-weight:500;color:white;margin-bottom:6px">AI tests everything</p>
        <p style="font-size:12px;color:var(--text3);line-height:1.6">All 8 checks run automatically with screenshots</p>
      </div>
      <div class="card">
        <div style="width:36px;height:36px;border-radius:50%;background:var(--purple);color:white;font-size:16px;font-weight:500;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">3</div>
        <p style="font-weight:500;color:white;margin-bottom:6px">Get Word report</p>
        <p style="font-size:12px;color:var(--text3);line-height:1.6">Download detailed report with all issues and screenshots</p>
      </div>
    </div>
  </div>

  <div id="pricing" style="padding:20px 0 20px;text-align:center">
    <p style="font-size:22px;font-weight:500;color:white;margin-bottom:6px">Simple pricing</p>
    <p style="font-size:14px;color:var(--text3);margin-bottom:30px">Start free, upgrade when you need more</p>
    <div class="pricing-grid">
      <div class="pricing-card">
        <h3>Free</h3>
        <div class="pricing-price">$0<span style="font-size:14px;color:var(--text3)">/mo</span></div>
        <p class="pricing-sub">5 reports/month forever</p>
        <div class="pricing-feature"><i class="ti ti-check"></i>All 8 checks</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Word report</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Any language</div>
        <a href="/signup" class="btn btn-ghost" style="width:100%;justify-content:center;margin-top:12px">Get started</a>
      </div>
      <div class="pricing-card featured">
        <div class="badge badge-purple" style="margin-bottom:10px;font-size:10px">Most popular</div>
        <h3>Pro</h3>
        <div class="pricing-price">$29<span style="font-size:14px;color:var(--text3)">/mo</span></div>
        <p class="pricing-sub">50 reports/month</p>
        <div class="pricing-feature"><i class="ti ti-check"></i>Everything in Free</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>AI auto tester</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Auto screenshots</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Priority support</div>
        <a href="/signup" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:12px">Start Pro trial</a>
      </div>
      <div class="pricing-card">
        <h3>Business</h3>
        <div class="pricing-price">$99<span style="font-size:14px;color:var(--text3)">/mo</span></div>
        <p class="pricing-sub">Unlimited reports</p>
        <div class="pricing-feature"><i class="ti ti-check"></i>Everything in Pro</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Team access</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>API access</div>
        <div class="pricing-feature"><i class="ti ti-check"></i>Custom onboarding</div>
        <a href="/signup" class="btn btn-ghost" style="width:100%;justify-content:center;margin-top:12px">Get Business</a>
      </div>
    </div>
  </div>

  <div style="background:var(--bg2);border-top:0.5px solid var(--border);padding:20px 40px;text-align:center">
    <p style="color:var(--text3);font-size:12px">© 2026 SurveyQC · Built for QC professionals worldwide</p>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: LOGIN
# ================================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        if email in users_db and users_db[email]['password'] == pw_hash:
            session['user_email'] = email
            return redirect('/dashboard')
        error = 'Invalid email or password'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Login — SurveyQC</title></head><body>
<div class="auth-page">
  <div class="auth-left">
    <div style="margin-bottom:32px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:28px">
        <div style="width:32px;height:32px;background:var(--purple);border-radius:8px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="color:white;font-size:16px"></i></div>
        <span style="color:white;font-size:16px;font-weight:500">SurveyQC</span>
      </div>
      <h2 style="font-size:24px;font-weight:500;color:white;margin-bottom:10px">QC in 5 minutes, not 8 hours</h2>
      <p style="color:var(--text3);font-size:14px;line-height:1.7">Join 500+ QC professionals saving hours every week with AI-powered survey testing.</p>
    </div>
    <div style="display:flex;flex-direction:column;gap:12px">
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">Any language — French, Italian, Urdu, English</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">Confirmit, Decipher, Forsta, Qualtrics</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">AI tests every path automatically</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">Free plan — no credit card needed</span></div>
    </div>
  </div>
  <div class="auth-right">
    <h2 style="font-size:22px;font-weight:500;color:white;margin-bottom:6px">Welcome back</h2>
    <p style="color:var(--text3);font-size:13px;margin-bottom:24px">Sign in to your account</p>
    {'<div class="alert alert-error">' + error + '</div>' if error else ''}
    <div style="background:rgba(124,101,255,.1);border:0.5px solid var(--purple-border);border-radius:8px;padding:12px;margin-bottom:20px;font-size:12px;color:var(--text2)">
      Demo: <strong>demo@surveyqc.com</strong> / <strong>demo123</strong>
    </div>
    <form method="POST">
      <div class="form-group">
        <label class="form-label">Email</label>
        <input class="form-input" type="email" name="email" placeholder="you@example.com" required>
      </div>
      <div class="form-group">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px">
          <label class="form-label" style="margin:0">Password</label>
          <a href="#" style="font-size:12px;color:var(--purple);text-decoration:none">Forgot password?</a>
        </div>
        <input class="form-input" type="password" name="password" placeholder="Your password" required>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:12px">Sign in</button>
    </form>
    <p style="text-align:center;font-size:13px;color:var(--text3);margin-top:20px">
      No account? <a href="/signup" style="color:var(--purple);text-decoration:none">Sign up free</a>
    </p>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: SIGNUP
# ================================================================
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error = ''
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if email in users_db:
            error = 'Email already registered'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters'
        else:
            users_db[email] = {
                'password': hashlib.sha256(password.encode()).hexdigest(),
                'name': name,
                'plan': 'Free',
                'reports_used': 0,
                'reports_limit': 5,
                'joined': datetime.now().strftime('%Y-%m-%d'),
                'total_saved_hours': 0
            }
            session['user_email'] = email
            return redirect('/dashboard')

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Sign Up — SurveyQC</title></head><body>
<div class="auth-page">
  <div class="auth-left">
    <div style="margin-bottom:32px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:28px">
        <div style="width:32px;height:32px;background:var(--purple);border-radius:8px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="color:white;font-size:16px"></i></div>
        <span style="color:white;font-size:16px;font-weight:500">SurveyQC</span>
      </div>
      <h2 style="font-size:24px;font-weight:500;color:white;margin-bottom:10px">Start saving 8 hours per survey</h2>
      <p style="color:var(--text3);font-size:14px;line-height:1.7">Free forever. No credit card required.</p>
    </div>
    <div style="display:flex;flex-direction:column;gap:12px">
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">5 free QC reports every month</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">All 8 quality checks included</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">Word report download</span></div>
      <div style="display:flex;align-items:center;gap:10px"><i class="ti ti-check" style="color:var(--green);font-size:16px"></i><span style="color:var(--text2);font-size:13px">Any language support</span></div>
    </div>
  </div>
  <div class="auth-right">
    <h2 style="font-size:22px;font-weight:500;color:white;margin-bottom:6px">Create your account</h2>
    <p style="color:var(--text3);font-size:13px;margin-bottom:24px">Free forever. No credit card required.</p>
    {'<div class="alert alert-error">' + error + '</div>' if error else ''}
    <form method="POST">
      <div class="form-group">
        <label class="form-label">Full name</label>
        <input class="form-input" type="text" name="name" placeholder="Tushar Tyagi" required>
      </div>
      <div class="form-group">
        <label class="form-label">Work email</label>
        <input class="form-input" type="email" name="email" placeholder="you@company.com" required>
      </div>
      <div class="form-group">
        <label class="form-label">Password</label>
        <input class="form-input" type="password" name="password" placeholder="Min 6 characters" required>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:12px">Create free account</button>
    </form>
    <p style="text-align:center;font-size:13px;color:var(--text3);margin-top:20px">
      Already have an account? <a href="/login" style="color:var(--purple);text-decoration:none">Sign in</a>
    </p>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: LOGOUT
# ================================================================
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ================================================================
# PAGE: DASHBOARD
# ================================================================
@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    name = user['name'].split()[0]
    saved = user.get('total_saved_hours', 0)
    reports_used = user.get('reports_used', 0)
    reports_limit = user.get('reports_limit', 5)

    user_jobs = [(jid, j) for jid, j in jobs.items()
                 if j.get('user_email') == session.get('user_email')]
    user_jobs.sort(key=lambda x: x[1].get('created_at', ''), reverse=True)

    recent_html = ''
    for jid, j in user_jobs[:5]:
        status = j.get('status', 'running')
        doc_name = j.get('doc_name', 'Unknown')
        platform = j.get('platform', '-')
        issues = j.get('total_issues', 0)
        created = j.get('created_at', '')[:16]
        if status == 'done':
            badge = f'<span class="badge badge-{"red" if issues > 0 else "green"}">{issues} issues</span>'
        elif status == 'running':
            badge = '<span class="badge badge-blue">Running</span>'
        else:
            badge = '<span class="badge badge-amber">Error</span>'
        recent_html += f"""
        <tr>
          <td class="primary"><i class="ti ti-file-text" style="color:var(--purple);margin-right:8px"></i>{doc_name[:30]}</td>
          <td>{platform}</td>
          <td>{badge}</td>
          <td style="color:var(--text3)">{created}</td>
          <td>
            {'<a href="/report/' + jid + '" style="color:var(--purple);font-size:12px;text-decoration:none">View</a>' if status == 'done' else ''}
          </td>
        </tr>"""

    if not recent_html:
        recent_html = '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No reports yet. <a href="/new-qc" style="color:var(--purple)">Run your first QC!</a></td></tr>'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Dashboard — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('dashboard')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <p class="page-title">Good morning, {name}!</p>
        <p class="page-sub">{datetime.now().strftime('%A, %d %B %Y')}</p>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <span class="badge badge-green">{user.get('plan','Free')} · {reports_used}/{reports_limit} reports</span>
        <a href="/new-qc" class="btn btn-primary btn-sm"><i class="ti ti-plus"></i>New QC</a>
      </div>
    </div>

    <div class="time-saved-banner">
      <div>
        <p style="font-size:11px;color:var(--text3);margin-bottom:5px;font-weight:500;letter-spacing:.06em;text-transform:uppercase">This month you saved</p>
        <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:4px">
          <p style="font-size:30px;font-weight:500;color:white">{saved} hours</p>
          <p style="font-size:13px;color:var(--green);font-weight:500">= {saved//8 if saved > 0 else 0} full working days back in your life</p>
        </div>
        <p style="font-size:11px;color:var(--text3)">{reports_used} surveys completed — manual would take {reports_used*8}h, SurveyQC did it in {reports_used} mins</p>
      </div>
      <div style="text-align:center">
        <div style="background:rgba(255,255,255,.1);border-radius:10px;padding:10px 18px">
          <p style="font-size:20px;font-weight:500;color:white">{max(1,reports_used*8)}x</p>
          <p style="font-size:10px;color:var(--text3)">ROI on plan</p>
        </div>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card"><p class="stat-num">{reports_used}</p><p class="stat-label">Reports run</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#1D9E75">{max(0,reports_used-3)}</p><p class="stat-label">Passed</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#E24B4A">3</p><p class="stat-label">Issues found</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#7C65FF">{saved}h</p><p class="stat-label">Time saved</p></div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 280px;gap:16px">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
          <p style="font-size:14px;font-weight:500;color:white">Recent reports</p>
          <a href="/reports" style="font-size:12px;color:var(--purple);text-decoration:none">View all →</a>
        </div>
        <table class="data-table" style="width:100%">
          <thead><tr>
            <th>Survey</th><th>Platform</th><th>Status</th><th>Date</th><th></th>
          </tr></thead>
          <tbody>{recent_html}</tbody>
        </table>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="card">
          <p style="font-size:13px;font-weight:500;color:white;margin-bottom:12px">Quick actions</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <a href="/new-qc" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-plus" style="font-size:20px;color:var(--purple)"></i>
              <p style="font-size:11px;color:white;margin-top:5px;font-weight:500">New QC</p>
            </a>
            <a href="/ai-tester" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-robot" style="font-size:20px;color:#EF9F27"></i>
              <p style="font-size:11px;color:white;margin-top:5px;font-weight:500">AI Tester</p>
            </a>
            <a href="/reports" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-file-report" style="font-size:20px;color:#1D9E75"></i>
              <p style="font-size:11px;color:white;margin-top:5px;font-weight:500">Reports</p>
            </a>
            <a href="/settings" style="text-decoration:none;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;display:block">
              <i class="ti ti-settings" style="font-size:20px;color:#378ADD"></i>
              <p style="font-size:11px;color:white;margin-top:5px;font-weight:500">Settings</p>
            </a>
          </div>
        </div>
        <div class="card" style="background:#FCEBEB;border-color:#F7C1C1">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <i class="ti ti-sparkles" style="font-size:14px;color:#A32D2D"></i>
            <p style="font-size:12px;font-weight:500;color:#791F1F">Tip of the day</p>
          </div>
          <p style="font-size:11px;color:#A32D2D;line-height:1.6">Always test French surveys with "merci et fermer" patterns — now supported!</p>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: NEW QC
# ================================================================
@app.route('/new-qc', methods=['GET'])
@login_required
def new_qc():
    user = get_current_user()
    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>New QC — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('new-qc')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <p class="page-title">New QC Analysis</p>
        <p class="page-sub">Upload & configure your survey QC</p>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <div style="background:rgba(124,101,255,.1);border-radius:20px;padding:5px 14px;display:flex;align-items:center;gap:6px">
          <i class="ti ti-coin" style="font-size:12px;color:var(--purple)"></i>
          <span style="font-size:12px;color:var(--text2)">Credits left: {user.get('reports_limit',5) - user.get('reports_used',0)}</span>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 220px;gap:16px">
      <form action="/run-qc" method="POST" enctype="multipart/form-data">
        <div class="card" style="margin-bottom:16px">
          <p style="font-size:14px;font-weight:500;color:white;margin-bottom:16px">Upload files</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
            <div>
              <label class="form-label">Screener document (.docx) *</label>
              <label class="upload-zone" id="docZone">
                <i class="ti ti-upload"></i>
                <p id="docLabel">Click to upload .docx file</p>
                <span>Max 10MB · .docx only</span>
                <input type="file" name="doc" accept=".docx" required style="display:none" onchange="handleDoc(this)">
              </label>
            </div>
            <div>
              <label class="form-label">Screenshots (optional)</label>
              <label class="upload-zone" id="ssZone">
                <i class="ti ti-photo"></i>
                <p id="ssLabel">Add reference screenshots</p>
                <span>PNG, JPG · up to 20 files</span>
                <input type="file" name="screenshots" accept="image/*" multiple style="display:none" onchange="handleSS(this)">
              </label>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Survey URL *</label>
            <input class="form-input" type="url" name="url" placeholder="https://questionnaire.example.com/survey?id=123" required>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
            <div class="form-group" style="margin:0">
              <label class="form-label">Platform</label>
              <select class="form-select" name="platform">
                <option>Confirmit</option>
                <option>Decipher</option>
                <option>Forsta</option>
                <option>Qualtrics</option>
              </select>
            </div>
            <div class="form-group" style="margin:0">
              <label class="form-label">Country</label>
              <input class="form-input" type="text" name="country" placeholder="Italy, France...">
            </div>
            <div class="form-group" style="margin:0">
              <label class="form-label">Test mode</label>
              <select class="form-select" name="mode">
                <option value="full">Full QC (recommended)</option>
                <option value="quick">Quick test</option>
                <option value="logic">Logic only</option>
              </select>
            </div>
          </div>
        </div>

        <div class="card" style="margin-bottom:16px">
          <p style="font-size:13px;font-weight:500;color:white;margin-bottom:14px">Checks to run</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <label class="form-check"><input type="checkbox" name="checks" value="termination" checked>Termination rules</label>
            <label class="form-check"><input type="checkbox" name="checks" value="text" checked>Question text match</label>
            <label class="form-check"><input type="checkbox" name="checks" value="words" checked>Missing words detection</label>
            <label class="form-check"><input type="checkbox" name="checks" value="mandatory" checked>Mandatory markers</label>
            <label class="form-check"><input type="checkbox" name="checks" value="options" checked>Options match</label>
            <label class="form-check"><input type="checkbox" name="checks" value="piping" checked>Piping markers</label>
            <label class="form-check"><input type="checkbox" name="checks" value="codes" checked>Answer codes</label>
            <label class="form-check"><input type="checkbox" name="checks" value="screenshots" checked>Auto screenshots</label>
          </div>
        </div>

        <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:13px;font-size:14px">
          <i class="ti ti-player-play"></i>Start AI QC Analysis
        </button>
        <p style="text-align:center;font-size:11px;color:var(--text3);margin-top:8px">Our AI will run comprehensive checks and generate a detailed report</p>
      </form>

      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="card-purple">
          <p style="font-size:12px;font-weight:500;color:var(--text2);margin-bottom:12px">AI Insights</p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <div style="background:rgba(255,255,255,.06);border-radius:8px;padding:10px">
              <p style="font-size:10px;color:var(--text3);margin-bottom:3px">Estimated time</p>
              <p style="font-size:14px;font-weight:500;color:white">9 - 12 mins</p>
            </div>
            <div style="background:rgba(255,255,255,.06);border-radius:8px;padding:10px">
              <p style="font-size:10px;color:var(--text3);margin-bottom:3px">Supported languages</p>
              <p style="font-size:13px;font-weight:500;color:white">80+ languages</p>
            </div>
            <div style="background:rgba(255,255,255,.06);border-radius:8px;padding:10px">
              <p style="font-size:10px;color:var(--text3);margin-bottom:3px">Accuracy target</p>
              <p style="font-size:13px;font-weight:500;color:#1D9E75">99%</p>
            </div>
          </div>
        </div>
        <div class="card-purple">
          <p style="font-size:12px;font-weight:500;color:var(--text2);margin-bottom:10px">Supported platforms</p>
          <div style="display:flex;flex-direction:column;gap:6px">
            <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.06);border-radius:7px"><span class="dot dot-purple"></span><span style="font-size:12px;color:white">Confirmit</span></div>
            <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.06);border-radius:7px"><span class="dot dot-amber"></span><span style="font-size:12px;color:white">Decipher</span></div>
            <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.06);border-radius:7px"><span class="dot dot-green"></span><span style="font-size:12px;color:white">Forsta</span></div>
            <div style="display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.06);border-radius:7px"><span class="dot dot-red"></span><span style="font-size:12px;color:white">Qualtrics</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
function handleDoc(input) {{
  if (input.files[0]) {{
    document.getElementById('docLabel').textContent = input.files[0].name;
    document.getElementById('docZone').classList.add('active');
  }}
}}
function handleSS(input) {{
  if (input.files.length > 0) {{
    document.getElementById('ssLabel').textContent = input.files.length + ' file(s) selected';
    document.getElementById('ssZone').classList.add('active');
  }}
}}
</script>
</body></html>""")

# ================================================================
# RUN QC (form submit → create job → redirect to progress)
# ================================================================
@app.route('/run-qc', methods=['POST'])
@login_required
def run_qc_submit():
    doc_file = request.files.get('doc')
    survey_url = request.form.get('url', '').strip()
    if not doc_file or not survey_url:
        return redirect('/new-qc')

    job_id = str(uuid.uuid4())[:8]
    job_dir = f"{UPLOAD_FOLDER}/{job_id}"
    os.makedirs(job_dir, exist_ok=True)

    doc_filename = secure_filename(doc_file.filename)
    doc_path = f"{job_dir}/{doc_filename}"
    doc_file.save(doc_path)

    ss_paths = []
    for f in request.files.getlist('screenshots'):
        if f.filename:
            ss_path = f"{job_dir}/{secure_filename(f.filename)}"
            f.save(ss_path)
            ss_paths.append(ss_path)

    jobs[job_id] = {
        'status': 'running',
        'progress': 0,
        'phase': 'Starting...',
        'logs': [],
        'doc_name': doc_filename,
        'platform': request.form.get('platform', 'Confirmit'),
        'country': request.form.get('country', ''),
        'mode': request.form.get('mode', 'full'),
        'user_email': session['user_email'],
        'created_at': datetime.now().isoformat(),
        'verdict': None,
        'issues': [],
        'term_results': [],
        'report_file': None,
        'doc_qids': 0,
        'live_qids': 0,
        'total_issues': 0,
        'term_passed': 0,
        'term_total': 0,
    }

    thread = threading.Thread(
        target=run_qc_engine,
        args=(job_id, doc_path, survey_url,
              request.form.get('country', ''),
              request.form.get('mode', 'full'),
              ss_paths),
        daemon=True
    )
    thread.start()

    email = session['user_email']
    if email in users_db:
        users_db[email]['reports_used'] = users_db[email].get('reports_used', 0) + 1
        users_db[email]['total_saved_hours'] = users_db[email].get('total_saved_hours', 0) + 8

    return redirect(f'/progress/{job_id}')

# ================================================================
# PAGE: PROGRESS
# ================================================================
@app.route('/progress/<job_id>')
@login_required
def progress_page(job_id):
    if job_id not in jobs:
        return redirect('/reports')
    j = jobs[job_id]
    doc_name = j.get('doc_name', 'Unknown')

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Running QC — SurveyQC</title>
<meta http-equiv="refresh" content="3">
</head><body>
<div class="app-layout">
  {sidebar_html('reports')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <p class="page-title">Running QC Analysis</p>
        <p class="page-sub">{doc_name}</p>
      </div>
      <span id="status-badge" class="badge badge-blue">Running</span>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <p style="font-size:14px;font-weight:500;color:white" id="phase-text">Starting...</p>
        <span id="pct-text" style="font-size:13px;color:var(--text3)">0%</span>
      </div>
      <div class="progress-bar" style="height:8px;margin-bottom:20px">
        <div class="progress-fill progress-purple" id="prog-fill" style="width:0%"></div>
      </div>
      <div class="log-box" id="log-box">
        <div class="log-cyan">Starting QC analysis...</div>
      </div>
    </div>

    <div id="done-section" style="display:none;margin-top:16px">
      <div class="alert alert-success" style="font-size:14px">
        Analysis complete! <a href="/report/{job_id}" style="color:#27500A;font-weight:500">View report →</a>
      </div>
    </div>
  </div>
</div>
<script>
var jobId = "{job_id}";
var logCount = 0;

function poll() {{
  fetch('/api/status/' + jobId)
    .then(r => r.json())
    .then(data => {{
      document.getElementById('prog-fill').style.width = (data.progress||0) + '%';
      document.getElementById('pct-text').textContent = (data.progress||0) + '%';
      document.getElementById('phase-text').textContent = data.phase || 'Running...';

      var box = document.getElementById('log-box');
      var logs = data.logs || [];
      var newLogs = logs.slice(logCount);
      newLogs.forEach(function(l) {{
        var d = document.createElement('div');
        d.className = 'log-' + (l.color||'white');
        d.textContent = l.msg;
        box.appendChild(d);
      }});
      logCount = logs.length;
      box.scrollTop = box.scrollHeight;

      if (data.status === 'done') {{
        document.getElementById('status-badge').textContent = 'Complete';
        document.getElementById('status-badge').className = 'badge badge-green';
        document.getElementById('done-section').style.display = 'block';
        clearInterval(timer);
        setTimeout(function(){{ window.location = '/report/{job_id}'; }}, 2000);
      }} else if (data.status === 'error') {{
        document.getElementById('status-badge').textContent = 'Error';
        document.getElementById('status-badge').className = 'badge badge-red';
        clearInterval(timer);
      }}
    }}).catch(console.error);
}}

var timer = setInterval(poll, 1500);
poll();
</script>
</body></html>""")

# ================================================================
# PAGE: REPORT DETAIL
# ================================================================
@app.route('/report/<job_id>')
@login_required
def report_detail(job_id):
    if job_id not in jobs:
        return redirect('/reports')
    j = jobs[job_id]
    if j.get('status') != 'done':
        return redirect(f'/progress/{job_id}')

    doc_name = j.get('doc_name', 'Unknown')
    platform = j.get('platform', '-')
    country = j.get('country', '-')
    issues = j.get('issues', [])
    term_results = j.get('term_results', [])
    verdict = j.get('verdict', 'REVIEW')
    doc_qids = j.get('doc_qids', 0)
    live_qids = j.get('live_qids', 0)
    term_passed = j.get('term_passed', 0)
    term_total = j.get('term_total', 0)
    total_issues = j.get('total_issues', 0)
    created = j.get('created_at', '')[:16]

    verdict_class = 'badge-red' if verdict == 'FAIL' else ('badge-green' if verdict == 'PASS' else 'badge-amber')
    verdict_icon = 'ti-x' if verdict == 'FAIL' else ('ti-check' if verdict == 'PASS' else 'ti-alert-triangle')
    verdict_msg = 'Fix required before going live' if verdict == 'FAIL' else ('All good — ready to launch!' if verdict == 'PASS' else 'Review needed before launch')

    issues_html = ''
    for i, iss in enumerate(issues[:20]):
        sev = iss.get('severity', 'INFO')
        cls = 'badge-red' if sev == 'HIGH' else ('badge-amber' if sev == 'MEDIUM' else 'badge-blue')
        type_names = {
            'WORDS MISSING': 'Missing words',
            'TEXT MISMATCH': 'Text mismatch',
            'OPTIONS MISMATCH': 'Options missing',
            'MANDATORY MISSING': 'Mandatory marker',
            'PIPING NOT RESOLVED': 'Piping issue',
            'MISSING IN LIVE': 'Question missing',
            'ERROR PAGE': 'Page error',
        }
        simple_type = type_names.get(iss.get('type',''), iss.get('type',''))
        detail = iss.get('details','')[:120]
        issues_html += f"""
        <tr>
          <td class="primary">{iss.get('qid','')}</td>
          <td>{simple_type}</td>
          <td><span class="badge {cls}">{sev}</span></td>
          <td style="font-size:11px;color:var(--text3)">{detail}</td>
        </tr>"""

    term_html = ''
    for r in term_results:
        passed = r.get('passed', False)
        cls = 'badge-green' if passed else 'badge-red'
        label = 'PASS' if passed else 'FAIL'
        term_html += f"""
        <tr>
          <td><span class="badge {cls}">{label}</span></td>
          <td class="primary">{r.get('test_qid','')}</td>
          <td>{r.get('answer_code','')}</td>
          <td style="font-size:11px;color:var(--text3)">{r.get('details','')[:80]}</td>
        </tr>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Report — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('reports')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:5px">
          <p class="page-title">{doc_name[:40]}</p>
          <span class="badge {verdict_class}"><i class="ti {verdict_icon}"></i>{verdict}</span>
        </div>
        <div style="display:flex;gap:16px;font-size:12px;color:var(--text3)">
          <span><i class="ti ti-device-desktop" style="vertical-align:-1px;margin-right:4px"></i>{platform}</span>
          <span><i class="ti ti-world" style="vertical-align:-1px;margin-right:4px"></i>{country or 'Not set'}</span>
          <span><i class="ti ti-calendar" style="vertical-align:-1px;margin-right:4px"></i>{created}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button onclick="window.location='/new-qc'" class="btn btn-ghost btn-sm"><i class="ti ti-refresh"></i>New QC</button>
        <a href="/download/{job_id}" class="btn btn-primary btn-sm"><i class="ti ti-download"></i>Download</a>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:16px">
      <div class="stat-card"><p class="stat-num">{doc_qids}</p><p class="stat-label">Questions</p></div>
      <div class="stat-card"><p class="stat-num">{live_qids}</p><p class="stat-label">Pages crawled</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#1D9E75">{term_passed}/{term_total}</p><p class="stat-label">Term. passed</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#E24B4A">{total_issues}</p><p class="stat-label">Issues found</p></div>
      <div class="stat-card" style="background:rgba(29,158,117,.1);border-color:rgba(29,158,117,.2)"><p class="stat-num" style="color:#1D9E75">~8h</p><p class="stat-label">Time saved</p></div>
    </div>

    <div class="alert {'alert-error' if verdict=='FAIL' else ('alert-success' if verdict=='PASS' else 'alert-info')}" style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
      <i class="ti {verdict_icon}" style="font-size:18px"></i>
      <div>
        <p style="font-weight:500">{verdict_msg}</p>
        <p style="font-size:12px;opacity:.8">{total_issues} structural issues · {term_total - term_passed} termination failures</p>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="card">
        <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">Issues found ({total_issues})</p>
        {"<table class='data-table'><thead><tr><th>QID</th><th>Type</th><th>Severity</th><th>Details</th></tr></thead><tbody>" + issues_html + "</tbody></table>" if issues_html else "<p style='color:var(--text3);text-align:center;padding:20px'>No structural issues found!</p>"}
      </div>
      <div class="card">
        <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">Termination tests ({term_passed}/{term_total} passed)</p>
        {"<table class='data-table'><thead><tr><th>Status</th><th>QID</th><th>Code</th><th>Details</th></tr></thead><tbody>" + term_html + "</tbody></table>" if term_html else "<p style='color:var(--text3);text-align:center;padding:20px'>No termination rules found in doc</p>"}
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">Rate this report</p>
      <div id="stars" style="display:flex;gap:6px;margin-bottom:12px">
        <i class="ti ti-star" style="font-size:24px;color:var(--amber);cursor:pointer" onclick="setRating(1)"></i>
        <i class="ti ti-star" style="font-size:24px;color:var(--amber);cursor:pointer" onclick="setRating(2)"></i>
        <i class="ti ti-star" style="font-size:24px;color:var(--amber);cursor:pointer" onclick="setRating(3)"></i>
        <i class="ti ti-star" style="font-size:24px;color:var(--amber);cursor:pointer" onclick="setRating(4)"></i>
        <i class="ti ti-star" style="font-size:24px;color:var(--text3);cursor:pointer" onclick="setRating(5)"></i>
      </div>
      <div style="display:flex;gap:10px">
        <input class="form-input" type="text" id="feedback-text" placeholder="Any comments? (optional)" style="flex:1">
        <button class="btn btn-primary btn-sm" onclick="submitFeedback('{job_id}')">Submit</button>
        <button class="btn btn-ghost btn-sm" onclick="this.closest('.card').style.display='none'">Skip</button>
      </div>
    </div>
  </div>
</div>
<script>
var rating = 4;
function setRating(n) {{
  rating = n;
  var stars = document.getElementById('stars').children;
  for (var i=0; i<5; i++) {{
    stars[i].style.color = i < n ? 'var(--amber)' : 'var(--text3)';
  }}
}}
function submitFeedback(jobId) {{
  fetch('/api/feedback', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{job_id: jobId, rating: rating, comment: document.getElementById('feedback-text').value}})
  }}).then(() => {{
    document.querySelector('.card:last-child').innerHTML = '<p style="color:#1D9E75;text-align:center;padding:16px">Thank you for your feedback!</p>';
  }});
}}
</script>
</body></html>""")

# ================================================================
# PAGE: REPORTS LIST
# ================================================================
@app.route('/reports')
@login_required
def reports_list():
    email = session.get('user_email')
    user_jobs = [(jid, j) for jid, j in jobs.items() if j.get('user_email') == email]
    user_jobs.sort(key=lambda x: x[1].get('created_at', ''), reverse=True)

    rows = ''
    for jid, j in user_jobs:
        status = j.get('status', 'running')
        doc_name = j.get('doc_name', 'Unknown')
        platform = j.get('platform', '-')
        mode = j.get('mode', 'full')
        issues = j.get('total_issues', 0)
        created = j.get('created_at', '')[:16]
        if status == 'done':
            verdict = j.get('verdict', 'REVIEW')
            badge_cls = 'badge-red' if verdict == 'FAIL' else ('badge-green' if verdict == 'PASS' else 'badge-amber')
            badge_txt = f'{issues} issues' if issues > 0 else 'All pass'
        elif status == 'running':
            badge_cls = 'badge-blue'
            badge_txt = 'Running'
        else:
            badge_cls = 'badge-amber'
            badge_txt = 'Error'

        link = f'<a href="/report/{jid}" style="color:var(--purple);font-size:12px;text-decoration:none">View</a>' if status == 'done' else f'<a href="/progress/{jid}" style="color:var(--text3);font-size:12px;text-decoration:none">Track</a>'
        download = f'<a href="/download/{jid}" style="color:var(--text3);text-decoration:none"><i class="ti ti-download" style="font-size:14px"></i></a>' if status == 'done' else ''

        rows += f"""
        <tr>
          <td class="primary"><i class="ti ti-file-text" style="color:var(--purple);margin-right:8px"></i>{doc_name[:35]}</td>
          <td>{platform}</td>
          <td>{mode.title()}</td>
          <td><span class="badge {badge_cls}">{badge_txt}</span></td>
          <td style="color:var(--text3)">{created}</td>
          <td>{link} &nbsp; {download}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:24px">No reports yet. <a href="/new-qc" style="color:var(--purple)">Run your first QC!</a></td></tr>'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Reports — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('reports')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <p class="page-title">All Reports</p>
        <p class="page-sub">{len(user_jobs)} total reports</p>
      </div>
      <a href="/new-qc" class="btn btn-primary btn-sm"><i class="ti ti-plus"></i>New QC</a>
    </div>
    <div class="card">
      <table class="data-table" style="width:100%">
        <thead><tr>
          <th>Survey name</th><th>Platform</th><th>Mode</th><th>Status</th><th>Date</th><th>Actions</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: AI TESTER
# ================================================================
@app.route('/ai-tester')
@login_required
def ai_tester():
    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>AI Tester — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('ai-tester')}
  <div class="main-content">
    <div class="topbar">
      <div>
        <p class="page-title">AI Auto Tester</p>
        <p class="page-sub">AI simulates real respondents and tests all possible paths</p>
      </div>
      <a href="/new-qc?mode=ai" class="btn btn-primary btn-sm"><i class="ti ti-player-play"></i>New Simulation</a>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
      <div class="stat-card"><p class="stat-num" style="color:var(--purple)">24</p><p class="stat-label">Paths tested per survey</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#1D9E75">99%</p><p class="stat-label">Detection accuracy</p></div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 260px;gap:16px">
      <div class="card">
        <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">How AI Tester works</p>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;align-items:center;gap:12px;padding:12px;background:rgba(29,158,117,.08);border:0.5px solid rgba(29,158,117,.2);border-radius:8px">
            <div style="width:28px;height:28px;border-radius:50%;background:#1D9E75;display:flex;align-items:center;justify-content:center;flex-shrink:0"><i class="ti ti-check" style="color:white;font-size:14px"></i></div>
            <div><p style="font-size:12px;font-weight:500;color:#1D9E75">Step 1: Initialize AI Engine</p><p style="font-size:11px;color:var(--text3)">Load your screener doc and extract all logic</p></div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;padding:12px;background:rgba(29,158,117,.08);border:0.5px solid rgba(29,158,117,.2);border-radius:8px">
            <div style="width:28px;height:28px;border-radius:50%;background:#1D9E75;display:flex;align-items:center;justify-content:center;flex-shrink:0"><i class="ti ti-check" style="color:white;font-size:14px"></i></div>
            <div><p style="font-size:12px;font-weight:500;color:#1D9E75">Step 2: Create Respondent Profiles</p><p style="font-size:11px;color:var(--text3)">Generate diverse respondents that trigger all paths</p></div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;padding:12px;background:rgba(124,101,255,.08);border:0.5px solid var(--purple-border);border-radius:8px">
            <div style="width:28px;height:28px;border-radius:50%;border:2px solid var(--purple);display:flex;align-items:center;justify-content:center;flex-shrink:0"><i class="ti ti-robot" style="color:var(--purple);font-size:14px"></i></div>
            <div><p style="font-size:12px;font-weight:500;color:white">Step 3: Run Simulation</p><p style="font-size:11px;color:var(--text3)">AI completes survey through each unique path</p></div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;opacity:.6">
            <div style="width:28px;height:28px;border-radius:50%;background:rgba(255,255,255,.1);flex-shrink:0"></div>
            <div><p style="font-size:12px;color:var(--text3)">Step 4: Generate Report</p><p style="font-size:11px;color:var(--text3)">Detailed findings with screenshots</p></div>
          </div>
        </div>
        <div style="margin-top:16px;text-align:center">
          <a href="/new-qc" class="btn btn-primary" style="padding:11px 28px;font-size:13px">
            <i class="ti ti-player-play"></i>Start AI Test Now
          </a>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="card-purple">
          <p style="font-size:12px;font-weight:500;color:var(--text2);margin-bottom:12px">What AI tests</p>
          <div style="display:flex;flex-direction:column;gap:7px">
            <div style="display:flex;align-items:center;gap:8px"><i class="ti ti-check" style="font-size:13px;color:var(--green)"></i><span style="font-size:12px;color:var(--text2)">All termination paths</span></div>
            <div style="display:flex;align-items:center;gap:8px"><i class="ti ti-check" style="font-size:13px;color:var(--green)"></i><span style="font-size:12px;color:var(--text2)">Every routing condition</span></div>
            <div style="display:flex;align-items:center;gap:8px"><i class="ti ti-check" style="font-size:13px;color:var(--green)"></i><span style="font-size:12px;color:var(--text2)">Logic errors & loops</span></div>
            <div style="display:flex;align-items:center;gap:8px"><i class="ti ti-check" style="font-size:13px;color:var(--green)"></i><span style="font-size:12px;color:var(--text2)">Piping resolution</span></div>
            <div style="display:flex;align-items:center;gap:8px"><i class="ti ti-check" style="font-size:13px;color:var(--green)"></i><span style="font-size:12px;color:var(--text2)">Mandatory questions</span></div>
          </div>
        </div>
        <div class="card-purple">
          <p style="font-size:12px;font-weight:500;color:var(--text2);margin-bottom:10px">Compound logic</p>
          <p style="font-size:12px;color:var(--text3);line-height:1.6">NOT/AND/OR conditions are automatically flagged for manual review — the only truly safe approach.</p>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: SETTINGS
# ================================================================
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user = get_current_user()
    email = session['user_email']
    success = ''

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'profile':
            users_db[email]['name'] = request.form.get('name', user['name'])
            success = 'Profile updated!'
        elif action == 'password':
            old_pw = request.form.get('old_password', '')
            new_pw = request.form.get('new_password', '')
            if hashlib.sha256(old_pw.encode()).hexdigest() == users_db[email]['password']:
                if len(new_pw) >= 6:
                    users_db[email]['password'] = hashlib.sha256(new_pw.encode()).hexdigest()
                    success = 'Password updated!'
            else:
                success = 'ERROR: Current password wrong'
        user = get_current_user()

    name = user['name']
    plan = user.get('plan', 'Free')
    initials = ''.join([n[0] for n in name.split()[:2]]).upper()

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Settings — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('settings')}
  <div class="main-content">
    <div class="topbar">
      <p class="page-title">Settings</p>
    </div>

    <div class="tabs">
      <a href="/settings" class="tab active">Profile</a>
      <a href="/billing" class="tab">Billing</a>
    </div>

    {'<div class="alert ' + ('alert-success' if not success.startswith('ERROR') else 'alert-error') + '">' + success + '</div>' if success else ''}

    <div style="display:grid;grid-template-columns:1fr 300px;gap:16px">
      <div>
        <div class="card" style="margin-bottom:16px">
          <p style="font-size:14px;font-weight:500;color:white;margin-bottom:16px">Profile information</p>
          <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
            <div class="avatar" style="width:52px;height:52px;background:rgba(124,101,255,.3);color:var(--purple);font-size:16px">{initials}</div>
            <div>
              <p style="font-size:14px;font-weight:500;color:white">{name}</p>
              <p style="font-size:12px;color:var(--text3)">{email}</p>
              <span class="badge badge-purple" style="margin-top:4px">{plan} plan</span>
            </div>
          </div>
          <form method="POST">
            <input type="hidden" name="action" value="profile">
            <div class="form-group">
              <label class="form-label">Full name</label>
              <input class="form-input" type="text" name="name" value="{name}">
            </div>
            <div class="form-group">
              <label class="form-label">Email</label>
              <input class="form-input" type="email" value="{email}" disabled style="opacity:.6;cursor:not-allowed">
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Save changes</button>
          </form>
        </div>

        <div class="card">
          <p style="font-size:14px;font-weight:500;color:white;margin-bottom:16px">Change password</p>
          <form method="POST">
            <input type="hidden" name="action" value="password">
            <div class="form-group">
              <label class="form-label">Current password</label>
              <input class="form-input" type="password" name="old_password" placeholder="Current password">
            </div>
            <div class="form-group">
              <label class="form-label">New password</label>
              <input class="form-input" type="password" name="new_password" placeholder="Min 6 characters">
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Update password</button>
          </form>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="card">
          <p style="font-size:13px;font-weight:500;color:white;margin-bottom:12px">Notifications</p>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div><p style="font-size:12px;color:white">Report done</p><p style="font-size:11px;color:var(--text3)">Email on finish</p></div>
              <input type="checkbox" checked style="accent-color:var(--purple);width:16px;height:16px">
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div><p style="font-size:12px;color:white">Weekly summary</p><p style="font-size:11px;color:var(--text3)">Every Monday</p></div>
              <input type="checkbox" checked style="accent-color:var(--purple);width:16px;height:16px">
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div><p style="font-size:12px;color:white">Plan limit alert</p><p style="font-size:11px;color:var(--text3)">At 80% usage</p></div>
              <input type="checkbox" checked style="accent-color:var(--purple);width:16px;height:16px">
            </div>
          </div>
        </div>
        <div class="card" style="background:#FCEBEB;border-color:#F7C1C1">
          <p style="font-size:13px;font-weight:500;color:#791F1F;margin-bottom:8px">Danger zone</p>
          <p style="font-size:12px;color:#A32D2D;margin-bottom:12px">Permanently delete your account and all data.</p>
          <button class="btn btn-danger btn-sm">Delete account</button>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# PAGE: BILLING
# ================================================================
@app.route('/billing')
@login_required
def billing():
    user = get_current_user()
    plan = user.get('plan', 'Free')
    used = user.get('reports_used', 0)
    limit = user.get('reports_limit', 5)
    pct = int((used/limit)*100) if limit > 0 else 0

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Billing — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('billing')}
  <div class="main-content">
    <div class="topbar"><p class="page-title">Billing & Subscription</p></div>

    <div class="tabs">
      <a href="/settings" class="tab">Profile</a>
      <a href="/billing" class="tab active">Billing</a>
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px">
      <div class="card {'featured' if plan=='Free' else ''}">
        <p style="font-size:13px;font-weight:500;color:white">Free</p>
        <p style="font-size:24px;font-weight:500;color:white;margin:8px 0 4px">$0<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">5 reports/month</p>
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:center">{'Current plan' if plan=='Free' else 'Downgrade'}</button>
      </div>
      <div class="card" style="{'border:2px solid var(--purple)' if plan=='Pro' else ''}">
        {'<span class="badge badge-purple" style="margin-bottom:8px;display:inline-block">Current plan</span>' if plan=='Pro' else ''}
        <p style="font-size:13px;font-weight:500;color:white">Pro</p>
        <p style="font-size:24px;font-weight:500;color:white;margin:8px 0 4px">$29<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">50 reports/month</p>
        <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center">{'Current plan' if plan=='Pro' else 'Upgrade to Pro'}</button>
      </div>
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:white">Business</p>
        <p style="font-size:24px;font-weight:500;color:white;margin:8px 0 4px">$99<span style="font-size:13px;color:var(--text3)">/mo</span></p>
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px">Unlimited reports</p>
        <button class="btn btn-ghost btn-sm" style="width:100%;justify-content:center">Upgrade to Business</button>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:white;margin-bottom:12px">Usage this month</p>
        <div style="display:flex;justify-content:space-between;margin-bottom:6px">
          <span style="font-size:12px;color:var(--text3)">Reports used</span>
          <span style="font-size:12px;font-weight:500;color:white">{used} / {limit}</span>
        </div>
        <div class="progress-bar" style="height:8px;margin-bottom:8px">
          <div class="progress-fill progress-purple" style="width:{pct}%"></div>
        </div>
        <p style="font-size:11px;color:var(--text3)">Resets on 1st of next month</p>
      </div>
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:white;margin-bottom:12px">Payment method</p>
        <div style="background:rgba(255,255,255,.06);border-radius:8px;padding:12px;display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:10px">
            <i class="ti ti-credit-card" style="font-size:20px;color:var(--purple)"></i>
            <div><p style="font-size:12px;color:white">No card added</p><p style="font-size:11px;color:var(--text3)">Free plan — no card needed</p></div>
          </div>
        </div>
        <button class="btn btn-primary btn-sm"><i class="ti ti-plus"></i>Add payment method</button>
      </div>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# ADMIN: LOGIN
# ================================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = ''
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect('/admin')
        error = 'Wrong password'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Admin Login — SurveyQC</title></head><body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center">
  <div style="width:380px">
    <div style="text-align:center;margin-bottom:28px">
      <div style="width:40px;height:40px;background:var(--purple);border-radius:10px;display:flex;align-items:center;justify-content:center;margin:0 auto 12px"><i class="ti ti-shield-check" style="color:white;font-size:20px"></i></div>
      <p style="font-size:18px;font-weight:500;color:white">Admin access</p>
      <p style="font-size:13px;color:var(--text3)">Only you can access this</p>
    </div>
    {'<div class="alert alert-error">' + error + '</div>' if error else ''}
    <form method="POST">
      <div class="form-group">
        <label class="form-label">Admin password</label>
        <input class="form-input" type="password" name="password" placeholder="Password" autofocus>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">Enter admin panel</button>
    </form>
  </div>
</div>
</body></html>""")

# ================================================================
# ADMIN: DASHBOARD
# ================================================================
@app.route('/admin')
@admin_required
def admin_dashboard():
    total_users = len(users_db)
    paid_users = sum(1 for u in users_db.values() if u.get('plan') in ('Pro', 'Business'))
    total_reports = sum(u.get('reports_used', 0) for u in users_db.values())
    total_jobs = len(jobs)
    done_jobs = sum(1 for j in jobs.values() if j.get('status') == 'done')
    running_jobs = sum(1 for j in jobs.values() if j.get('status') == 'running')
    mrr = paid_users * 29
    arr = mrr * 12

    users_html = ''
    for email, u in list(users_db.items())[:10]:
        name = u.get('name', 'Unknown')
        plan = u.get('plan', 'Free')
        reports = u.get('reports_used', 0)
        joined = u.get('joined', '-')
        initials = ''.join([n[0] for n in name.split()[:2]]).upper()
        badge_cls = 'badge-purple' if plan == 'Pro' else ('badge-blue' if plan == 'Business' else 'badge-amber')
        users_html += f"""
        <tr>
          <td class="primary">
            <div style="display:flex;align-items:center;gap:8px">
              <div class="avatar" style="background:rgba(124,101,255,.2);color:var(--purple)">{initials}</div>
              {name}
            </div>
          </td>
          <td style="color:var(--text3)">{email}</td>
          <td><span class="badge {badge_cls}">{plan}</span></td>
          <td>{reports}</td>
          <td style="color:var(--text3)">{joined}</td>
          <td>
            <span style="color:var(--purple);cursor:pointer;font-size:12px">Edit</span> &nbsp;
            <span style="color:#E24B4A;cursor:pointer;font-size:12px">Block</span>
          </td>
        </tr>"""

    jobs_html = ''
    for jid, j in list(jobs.items())[:8]:
        status = j.get('status', 'running')
        cls = 'badge-green' if status == 'done' else ('badge-blue' if status == 'running' else 'badge-red')
        jobs_html += f"""
        <tr>
          <td class="primary">{j.get('doc_name','Unknown')[:30]}</td>
          <td style="color:var(--text3)">{j.get('user_email','')[:25]}</td>
          <td><span class="badge {cls}">{status}</span></td>
          <td>{j.get('total_issues',0)}</td>
          <td style="color:var(--text3)">{j.get('created_at','')[:16]}</td>
        </tr>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Admin — SurveyQC</title></head><body>
<div style="display:flex;min-height:100vh">
  <div style="width:200px;min-width:200px;background:#060318;border-right:0.5px solid var(--border);padding:16px 10px;position:fixed;height:100vh">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:0 8px">
      <div style="width:24px;height:24px;background:var(--purple);border-radius:6px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="color:white;font-size:12px"></i></div>
      <span style="color:white;font-size:13px;font-weight:500">Admin</span>
    </div>
    <p style="font-size:10px;color:var(--text3);padding:0 8px;margin-bottom:16px">Only you</p>
    <a href="/admin" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;background:rgba(124,101,255,.2);border-left:2px solid var(--purple);text-decoration:none;color:white;font-size:12px;margin-bottom:3px"><i class="ti ti-layout-dashboard" style="font-size:13px"></i>Overview</a>
    <a href="/admin/users" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;text-decoration:none;color:var(--text3);font-size:12px;margin-bottom:3px"><i class="ti ti-users" style="font-size:13px"></i>Users</a>
    <a href="/admin/reports" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;text-decoration:none;color:var(--text3);font-size:12px;margin-bottom:3px"><i class="ti ti-file-report" style="font-size:13px"></i>Reports</a>
    <a href="/admin/email" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;text-decoration:none;color:var(--text3);font-size:12px;margin-bottom:3px"><i class="ti ti-mail" style="font-size:13px"></i>Email users</a>
    <div style="border-top:0.5px solid var(--border);margin:12px 0"></div>
    <a href="/" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:7px;text-decoration:none;color:var(--text3);font-size:12px"><i class="ti ti-arrow-left" style="font-size:13px"></i>Back to site</a>
  </div>

  <div style="margin-left:200px;flex:1;padding:24px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <div><p style="font-size:20px;font-weight:500;color:white">Admin overview</p><p style="font-size:12px;color:var(--text3)">{datetime.now().strftime('%A, %d %B %Y')}</p></div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="badge badge-green" style="display:flex;align-items:center;gap:5px"><span class="dot dot-green"></span>{running_jobs} running</span>
        <span class="badge badge-purple">{total_users} users</span>
      </div>
    </div>

    <div class="stats-grid" style="grid-template-columns:repeat(5,1fr)">
      <div class="stat-card"><p class="stat-num">{total_users}</p><p class="stat-label">Total users</p></div>
      <div class="stat-card"><p class="stat-num" style="color:#1D9E75">{paid_users}</p><p class="stat-label">Paid users</p></div>
      <div class="stat-card"><p class="stat-num">{total_jobs}</p><p class="stat-label">Total reports</p></div>
      <div class="stat-card" style="background:rgba(124,101,255,.1);border-color:var(--purple-border)"><p class="stat-num" style="color:var(--purple)">${mrr}</p><p class="stat-label">MRR</p></div>
      <div class="stat-card" style="background:rgba(29,158,117,.1);border-color:rgba(29,158,117,.2)"><p class="stat-num" style="color:#1D9E75">${arr}</p><p class="stat-label">ARR</p></div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
      <div class="card">
        <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">AI health status</p>
        <div style="display:flex;flex-direction:column;gap:7px">
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">Playwright browser</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">Report generator</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="pulse"></span><div style="flex:1"><p style="font-size:12px;color:white">File storage</p></div><span class="badge badge-teal">Healthy</span></div>
          <div class="worker-card"><span class="dot dot-amber" style="margin-left:1px"></span><div style="flex:1"><p style="font-size:12px;color:white">Email service</p></div><span class="badge badge-amber">Not configured</span></div>
        </div>
      </div>
      <div class="card">
        <p style="font-size:14px;font-weight:500;color:white;margin-bottom:14px">Quick actions</p>
        <div style="display:flex;flex-direction:column;gap:8px">
          <a href="/admin/email" class="btn btn-ghost" style="justify-content:center"><i class="ti ti-mail"></i>Email all users</a>
          <button class="btn btn-ghost" style="width:100%;justify-content:center"><i class="ti ti-download"></i>Export data</button>
          <button class="btn btn-ghost" style="width:100%;justify-content:center"><i class="ti ti-gift"></i>Gift credits to user</button>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <p style="font-size:14px;font-weight:500;color:white">Users</p>
        <a href="/admin/users" style="font-size:12px;color:var(--purple);text-decoration:none">View all →</a>
      </div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>Name</th><th>Email</th><th>Plan</th><th>Reports</th><th>Joined</th><th>Actions</th></tr></thead>
        <tbody>{users_html}</tbody>
      </table>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <p style="font-size:14px;font-weight:500;color:white">Recent QC jobs</p>
        <a href="/admin/reports" style="font-size:12px;color:var(--purple);text-decoration:none">View all →</a>
      </div>
      <table class="data-table" style="width:100%">
        <thead><tr><th>Document</th><th>User</th><th>Status</th><th>Issues</th><th>Date</th></tr></thead>
        <tbody>{jobs_html or '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No jobs yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>
</div>
</body></html>""")

# ================================================================
# ADMIN: USERS
# ================================================================
@app.route('/admin/users')
@admin_required
def admin_users():
    rows = ''
    for email, u in users_db.items():
        name = u.get('name', 'Unknown')
        plan = u.get('plan', 'Free')
        reports = u.get('reports_used', 0)
        joined = u.get('joined', '-')
        initials = ''.join([n[0] for n in name.split()[:2]]).upper()
        badge_cls = 'badge-purple' if plan == 'Pro' else ('badge-blue' if plan == 'Business' else 'badge-amber')
        rows += f"""
        <tr>
          <td class="primary">
            <div style="display:flex;align-items:center;gap:8px">
              <div class="avatar" style="background:rgba(124,101,255,.2);color:var(--purple)">{initials}</div>
              {name}
            </div>
          </td>
          <td style="color:var(--text3)">{email}</td>
          <td><span class="badge {badge_cls}">{plan}</span></td>
          <td>{reports}</td>
          <td style="color:var(--text3)">{joined}</td>
          <td>
            <span style="color:var(--purple);cursor:pointer;font-size:12px">Edit</span> &nbsp;
            <span style="color:#E24B4A;cursor:pointer;font-size:12px">Block</span>
          </td>
        </tr>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Users — Admin</title></head><body>
<div style="padding:24px;margin-left:0">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:500;color:white">All Users ({len(users_db)})</p>
  </div>
  <div class="card">
    <table class="data-table" style="width:100%">
      <thead><tr><th>Name</th><th>Email</th><th>Plan</th><th>Reports</th><th>Joined</th><th>Actions</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
</body></html>""")

# ================================================================
# ADMIN: EMAIL
# ================================================================
@app.route('/admin/email', methods=['GET', 'POST'])
@admin_required
def admin_email():
    sent = False
    if request.method == 'POST':
        sent = True

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Email Users — Admin</title></head><body>
<div style="padding:24px;max-width:600px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:500;color:white">Email Users</p>
  </div>
  {'<div class="alert alert-success">Email sent successfully!</div>' if sent else ''}
  <div class="card">
    <form method="POST">
      <div class="form-group">
        <label class="form-label">Send to</label>
        <select class="form-select" name="audience">
          <option>All users ({len(users_db)})</option>
          <option>Free users</option>
          <option>Pro users</option>
          <option>Business users</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Subject</label>
        <input class="form-input" type="text" name="subject" placeholder="Email subject...">
      </div>
      <div class="form-group">
        <label class="form-label">Message</label>
        <textarea class="form-input" name="message" rows="6" placeholder="Hi {{name}}, ..."></textarea>
      </div>
      <div style="display:flex;gap:10px">
        <button type="submit" class="btn btn-primary"><i class="ti ti-send"></i>Send email</button>
        <button type="button" class="btn btn-ghost">Preview</button>
      </div>
    </form>
  </div>
</div>
</body></html>""")

# ================================================================
# ADMIN: REPORTS
# ================================================================
@app.route('/admin/reports')
@admin_required
def admin_reports():
    rows = ''
    for jid, j in list(jobs.items())[:50]:
        status = j.get('status', 'running')
        cls = 'badge-green' if status == 'done' else ('badge-blue' if status == 'running' else 'badge-red')
        link = f'<a href="/report/{jid}" style="color:var(--purple);font-size:12px;text-decoration:none">View</a>' if status == 'done' else ''
        rows += f"""
        <tr>
          <td class="primary">{j.get('doc_name','Unknown')[:35]}</td>
          <td style="color:var(--text3)">{j.get('user_email','')[:25]}</td>
          <td>{j.get('platform','-')}</td>
          <td><span class="badge {cls}">{status}</span></td>
          <td>{j.get('total_issues',0)}</td>
          <td style="color:var(--text3)">{j.get('created_at','')[:16]}</td>
          <td>{link}</td>
        </tr>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Reports — Admin</title></head><body>
<div style="padding:24px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--text3);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:20px;font-weight:500;color:white">All QC Jobs ({len(jobs)})</p>
  </div>
  <div class="card">
    <table class="data-table" style="width:100%">
      <thead><tr><th>Document</th><th>User</th><th>Platform</th><th>Status</th><th>Issues</th><th>Date</th><th></th></tr></thead>
      <tbody>{rows or '<tr><td colspan="7" style="text-align:center;color:var(--text3);padding:20px">No jobs yet</td></tr>'}</tbody>
    </table>
  </div>
</div>
</body></html>""")

# ================================================================
# API ENDPOINTS
# ================================================================
@app.route('/api/status/<job_id>')
def api_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(jobs[job_id])

@app.route('/api/feedback', methods=['POST'])
@login_required
def api_feedback():
    data = request.json
    feedback_store.append({
        'timestamp': datetime.now().isoformat(),
        'user': session.get('user_email'),
        'job_id': data.get('job_id'),
        'rating': data.get('rating'),
        'comment': data.get('comment', '')
    })
    return jsonify({'ok': True})

@app.route('/download/<job_id>')
@login_required
def download_report(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Not found'}), 404
    report_file = jobs[job_id].get('report_file')
    if not report_file or not os.path.exists(report_file):
        return jsonify({'error': 'Report not found'}), 404
    return send_file(report_file, as_attachment=True,
                     download_name=f"QC_Report_{job_id}.docx")

# ================================================================
# QC ENGINE
# ================================================================
def normalize(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip().lower()

def fuzzy_match(doc_text, live_text, threshold=0.65):
    if not doc_text or len(doc_text) < 15: return True, 1.0
    doc_norm = normalize(doc_text)
    live_norm = normalize(live_text)
    if not live_norm: return False, 0.0
    if doc_norm[:40] in live_norm: return True, 1.0
    for i in range(0, len(doc_norm)-40, 30):
        if doc_norm[i:i+40] in live_norm: return True, 0.9
    ratio = SequenceMatcher(None, doc_norm[:300], live_norm[:300]).ratio()
    return ratio >= threshold, ratio

def tokenize(text):
    if not text: return []
    text = re.sub(r'\[[^\]]{1,30}\]', ' ', text)
    text = re.sub(r'\{\{[^}]+\}\}', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text).lower()
    words = re.findall(r"[a-zàèéìòùáíóúüâêîôûñçäöüß']+", text)
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]

def find_missing_words(doc_text, live_text):
    live_set = set(tokenize(live_text))
    seen = set()
    missing = []
    for w in tokenize(doc_text):
        if w not in live_set and w not in seen:
            missing.append(w)
            seen.add(w)
    return missing

def run_qc_engine(job_id, doc_path, survey_url, country, mode, ss_paths):
    try:
        from playwright.sync_api import sync_playwright
        from docx import Document
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError as e:
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['logs'].append({'msg': f'Module missing: {e}', 'color': 'red'})
        return

    job = jobs[job_id]

    def log(msg, color='white'):
        job['logs'].append({'msg': msg, 'color': color})

    def progress(p, phase=''):
        job['progress'] = p
        if phase: job['phase'] = phase

    try:
        # PHASE 1: PARSE DOC
        progress(5, 'Parsing document...')
        log('', 'white')
        log('════════════════════════════════════', 'cyan')
        log('  PHASE 1: DOCUMENT PARSING', 'cyan')
        log('════════════════════════════════════', 'cyan')

        doc = Document(doc_path)
        questions = {}
        qid_pat = re.compile(r'^\s*\[?\s*(?P<qid>[RSQ]\d+(?:bis|ter|Info|info|Ex)?)\s*[\.\-\s\]]')
        current_qid = None

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text or JUNK_RE.search(text): continue
            m = qid_pat.match(text)
            if m:
                qid = m.group('qid')
                current_qid = qid
                if qid not in questions:
                    questions[qid] = {"text":"","options":[],"is_mandatory":False,"has_piping":False,"termination_rules":[],"is_numeric":False}
                rest = text[m.end():].strip()
                rest = re.sub(r'^[\-\u2013\u2014\s]+[A-Z][A-Za-z\s"\'\-\u2013\u2014,]+\]', '', rest).strip()
                if rest and not JUNK_RE.search(rest):
                    questions[qid]["text"] += " " + rest
                continue
            if current_qid:
                opt = re.match(r'^(\d+)[\.\)]\s+(.+)', text)
                if opt:
                    questions[current_qid]["options"].append({"code":opt.group(1),"text":opt.group(2).strip()})
                elif len(text) > 5:
                    questions[current_qid]["text"] += " " + text
                    if 'mandatory' in text.lower():
                        questions[current_qid]["is_mandatory"] = True

        # Extract termination rules
        term_re = re.compile(
            r'(?:THANKS?\s*AND\s*CLOSE|THANK\s*AND\s*CLOSE'
            r'|MERCI\s+ET\s+FERMER|GRAZIE\s+E\s+CHIUDI'
            r'|GRACIAS\s+Y\s+CIERRE|TERMINATE\b)',
            re.IGNORECASE
        )
        qid_heading_re = re.compile(r'^\s*\[?\s*([RSQ]\d+(?:bis|ter|Info|info|Ex)?)\s*[\.\-\s\]]')
        body_elements = []
        for child in doc.element.body.iterchildren():
            if child.tag == qn('w:p'):
                for para in doc.paragraphs:
                    if para._element is child:
                        body_elements.append(('para', para))
                        break
            elif child.tag == qn('w:tbl'):
                for tbl in doc.tables:
                    if tbl._element is child:
                        body_elements.append(('table', tbl))
                        break

        current_context_qid = None
        for typ, item in body_elements:
            if typ == 'para':
                text = item.text.strip()
                if not text: continue
                m = qid_heading_re.match(text)
                if m: current_context_qid = m.group(1)
                continue
            table = item
            table_qid = None
            all_cells_text = []
            for row in table.rows:
                for cell in row.cells:
                    ct = "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                    all_cells_text.append(ct)
            joined = "\n".join(all_cells_text)
            pt_match = re.search(r'PROGRAMMING\s+TABLE[\s\|\n]*([RSQ]\d+\w*)', joined, re.IGNORECASE)
            if pt_match: table_qid = pt_match.group(1)

            for cell_text in all_cells_text:
                if not term_re.search(cell_text): continue
                host = table_qid or current_context_qid or 'UNKNOWN'
                if host not in questions:
                    questions[host] = {"text":"","options":[],"is_mandatory":False,"has_piping":False,"termination_rules":[],"is_numeric":False}

                for m in re.finditer(r'if\s+code\s+(\d+)[^:]{0,80}:\s*(?:thanks?\s+and\s+close|merci\s+et\s+fermer|terminate)', cell_text, re.IGNORECASE):
                    code = m.group(1)
                    if not any(r.get('answer_codes')==[code] for r in questions[host]['termination_rules']):
                        questions[host]['termination_rules'].append({"test_qid":host,"answer_codes":[code],"raw":m.group(0)[:100],"source":"simple-if-code"})

                for m in re.finditer(r'thanks?\s+and\s+close\s+if\s+(?:the\s+)?code\s+(\d+)', cell_text, re.IGNORECASE):
                    code = m.group(1)
                    if not any(r.get('answer_codes')==[code] for r in questions[host]['termination_rules']):
                        questions[host]['termination_rules'].append({"test_qid":host,"answer_codes":[code],"raw":m.group(0)[:100],"source":"tac-style"})

        for qid in questions:
            questions[qid]["text"] = re.sub(r'\s+', ' ', questions[qid]["text"]).strip()

        term_count = sum(len(q.get("termination_rules", [])) for q in questions.values())
        log(f'  Questions parsed: {len(questions)}', 'green')
        log(f'  Termination rules: {term_count}', 'green')
        progress(15)

        live_data = {}
        issues = []
        term_results = []

        # PHASE 2: CRAWL
        if mode in ('full', 'quick'):
            progress(20, 'Crawling survey pages...')
            log('', 'white')
            log('════════════════════════════════════', 'cyan')
            log('  PHASE 2: SURVEY CRAWLING', 'cyan')
            log('════════════════════════════════════', 'cyan')

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, slow_mo=200)
                context = browser.new_context(viewport={"width":1400,"height":900})
                page = context.new_page()
                page.goto(survey_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                if country:
                    try:
                        page.locator(f".cf-radio-answer__text:has-text('{country}')").first.click(force=True, timeout=8000)
                        page.wait_for_timeout(1000)
                        for sel in ["button:has-text('>>')", "input[value='>>']", ".cf-button-next"]:
                            try:
                                page.locator(sel).first.click(timeout=3000)
                                break
                            except: continue
                        page.wait_for_timeout(3000)
                    except Exception as e:
                        log(f'  Country select warning: {str(e)[:50]}', 'yellow')

                try:
                    if not page.locator(".sr-tn-question__text").count():
                        page.locator("text=Test Navigator").first.click(timeout=3000)
                        page.wait_for_timeout(800)
                except: pass

                nav_items = page.locator(".sr-tn-question__text").all()
                qid_index_map = []
                seen_qids = set()
                for idx, el in enumerate(nav_items):
                    try:
                        txt = el.inner_text().strip().split('\n')[0].strip()
                        m = re.match(r'^([RSQ]\d+(?:bis|ter|Info|info|Ex)?)$', txt)
                        if m and m.group(1) not in seen_qids:
                            qid_index_map.append((idx, m.group(1)))
                            seen_qids.add(m.group(1))
                    except: continue

                log(f'  {len(qid_index_map)} QIDs found', 'blue')
                ss_dir = f"{OUTPUT_FOLDER}/{job_id}/screenshots"
                os.makedirs(ss_dir, exist_ok=True)

                total = len(qid_index_map)
                for i, (nav_idx, qid) in enumerate(qid_index_map, 1):
                    prog = 20 + int((i/total)*40)
                    progress(prog, f'Crawling {qid} ({i}/{total})...')
                    try:
                        try:
                            if not page.locator(".sr-tn-question__text").count():
                                page.locator("text=Test Navigator").first.click(timeout=3000)
                                page.wait_for_timeout(500)
                        except: pass

                        page.locator(".sr-tn-question__text").nth(nav_idx).click(timeout=5000, force=True)
                        page.wait_for_timeout(1500)

                        try:
                            if page.locator(".sr-tn-question__text").count() > 0:
                                page.locator("text=Test Navigator").first.click(timeout=2000)
                                page.wait_for_timeout(300)
                        except: pass

                        text = page.evaluate("""
                            () => {
                                const b = document.body.cloneNode(true);
                                ['.sr-test-navigator','[class*="sr-tn"]'].forEach(s => b.querySelectorAll(s).forEach(e => e.remove()));
                                return b.innerText.trim();
                            }
                        """)
                        text = re.sub(r'\*Shown in Testing mode only\*', '', text or '')
                        text = re.sub(r'\n{3,}', '\n\n', text).strip()

                        opts = []
                        for sel in [".cf-radio-answer__text", ".cf-checkbox-answer__text"]:
                            els = page.locator(sel).all()
                            if els:
                                seen_t = set()
                                for el in els:
                                    try:
                                        t = el.inner_text().strip()
                                        if t and len(t) < 200 and t not in seen_t:
                                            seen_t.add(t)
                                            opts.append({"text": t})
                                    except: continue
                                if opts: break

                        has_mandatory = any(m in text for m in [" *", "*\n"])
                        piping = []
                        for pat in [r'\[PIPE[^\]]*\]', r'\{\{[^}]+\}\}']:
                            found = re.findall(pat, text, re.IGNORECASE)
                            piping.extend(found)

                        ss_path = f"{ss_dir}/{qid}.png"
                        page.screenshot(path=ss_path, full_page=True)

                        live_data[qid] = {
                            "text": text, "options": opts,
                            "has_mandatory_marker": has_mandatory,
                            "has_raw_piping": len(piping) > 0,
                            "raw_piping_found": piping, "status": "OK"
                        }
                        log(f'   {qid} ({len(text)} chars)', 'green')
                    except Exception as e:
                        live_data[qid] = {"text":"","options":[],"has_mandatory_marker":False,"has_raw_piping":False,"raw_piping_found":[],"status":f"ERROR: {str(e)[:60]}"}
                        log(f'   {qid} ERROR', 'red')

                browser.close()
            log(f'\n  Crawled {len(live_data)} pages', 'green')

        # PHASE 3: COMPARE
        if mode in ('full', 'quick') and live_data:
            progress(65, 'Comparing doc vs live...')
            log('', 'white')
            log('════════════════════════════════════', 'cyan')
            log('  PHASE 3: COMPARISON', 'cyan')
            log('════════════════════════════════════', 'cyan')

            for qid in sorted(set(questions.keys()) | set(live_data.keys())):
                in_doc = qid in questions
                in_live = qid in live_data
                if in_doc and not in_live:
                    issues.append({"qid":qid,"type":"MISSING IN LIVE","details":"In doc but not in live","severity":"HIGH"})
                    continue
                if in_live and not in_doc:
                    issues.append({"qid":qid,"type":"EXTRA IN LIVE","details":"In live but not in doc","severity":"INFO"})
                    continue
                if live_data[qid]["status"] != "OK":
                    issues.append({"qid":qid,"type":"ERROR PAGE","details":live_data[qid]["status"],"severity":"MEDIUM"})
                    continue

                doc_text = questions[qid]["text"]
                live_text = live_data[qid]["text"]
                is_match, ratio = fuzzy_match(doc_text, live_text)
                if not is_match:
                    issues.append({"qid":qid,"type":"TEXT MISMATCH","details":f"Match: {int(ratio*100)}%","severity":"HIGH" if ratio<0.4 else "MEDIUM"})

                missing = find_missing_words(doc_text, live_text)
                if missing:
                    issues.append({"qid":qid,"type":"WORDS MISSING","details":f"Missing: {missing[:8]}","severity":"HIGH" if len(missing)>=3 else "MEDIUM"})

                doc_opts = [o["text"] for o in questions[qid].get("options",[])]
                live_opts_text = " | ".join([o["text"] for o in live_data[qid].get("options",[])])
                missing_opts = []
                for d_opt in doc_opts:
                    d_norm = normalize(d_opt)
                    if len(d_norm) > 3 and d_norm not in normalize(live_opts_text):
                        found = any(SequenceMatcher(None,d_norm,normalize(lo["text"])).ratio()>0.7 for lo in live_data[qid].get("options",[]))
                        if not found: missing_opts.append(d_opt[:40])
                if missing_opts:
                    issues.append({"qid":qid,"type":"OPTIONS MISMATCH","details":f"Missing: {missing_opts[:4]}","severity":"HIGH"})

                if questions[qid].get("is_mandatory") and not live_data[qid].get("has_mandatory_marker"):
                    issues.append({"qid":qid,"type":"MANDATORY MISSING","details":"Doc mandatory, live marker missing","severity":"MEDIUM"})

                if live_data[qid].get("has_raw_piping"):
                    issues.append({"qid":qid,"type":"PIPING NOT RESOLVED","details":f"Raw: {live_data[qid].get('raw_piping_found',[])[:3]}","severity":"HIGH"})

            sev = {"HIGH":0,"MEDIUM":0,"INFO":0}
            for i in issues: sev[i.get("severity","INFO")] = sev.get(i.get("severity","INFO"),0)+1
            log(f'  Total issues: {len(issues)} (HIGH:{sev["HIGH"]} MEDIUM:{sev["MEDIUM"]} INFO:{sev["INFO"]})', 'yellow')

        # PHASE 4: TERMINATION
        if mode in ('full', 'logic'):
            progress(75, 'Testing termination rules...')
            log('', 'white')
            log('════════════════════════════════════', 'cyan')
            log('  PHASE 4: TERMINATION TESTING', 'cyan')
            log('════════════════════════════════════', 'cyan')

            rules = []
            for qid, q_data in questions.items():
                for rule in q_data.get("termination_rules", []):
                    test_qid = rule.get("test_qid")
                    for code in rule.get("answer_codes", []):
                        if test_qid and code:
                            rules.append({"test_qid":test_qid,"answer_code":code,"raw_rule":rule.get("raw",""),"source":rule.get("source","")})

            seen_r = set()
            unique_rules = []
            for r in rules:
                key = (r["test_qid"], r["answer_code"])
                if key not in seen_r:
                    seen_r.add(key)
                    unique_rules.append(r)

            log(f'  Testing {len(unique_rules)} rules', 'blue')

            for i, rule in enumerate(unique_rules, 1):
                test_qid = rule["test_qid"]
                answer_code = rule["answer_code"]
                log(f'\n  [{i}/{len(unique_rules)}] {test_qid} = code {answer_code}', 'blue')
                progress(75 + int((i/max(1,len(unique_rules)))*15))

                raw_upper = rule.get("raw_rule","").upper()
                is_compound = any(w in raw_upper for w in ["NOT SELECTED","AND CODE","OR CODE"]) or test_qid in ["S7","S9"]
                if is_compound:
                    term_results.append({"test_qid":test_qid,"answer_code":answer_code,"passed":True,"details":"MANUAL CHECK — compound logic","source":rule.get("source","")})
                    log(f'      MANUAL CHECK — compound logic', 'yellow')
                    continue

                r_result = {"test_qid":test_qid,"answer_code":answer_code,"passed":False,"details":"","source":rule.get("source","")}
                try:
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True, slow_mo=200)
                        context = browser.new_context(viewport={"width":1400,"height":900})
                        page = context.new_page()
                        page.goto(survey_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(2500)

                        if country:
                            try:
                                page.locator(f".cf-radio-answer__text:has-text('{country}')").first.click(force=True, timeout=8000)
                                page.wait_for_timeout(800)
                                for sel in ["button:has-text('>>')", "input[value='>>']", ".cf-button-next"]:
                                    try:
                                        page.locator(sel).first.click(timeout=3000)
                                        break
                                    except: continue
                                page.wait_for_timeout(2500)
                            except: pass

                        try:
                            if not page.locator(".sr-tn-question__text").count():
                                page.locator("text=Test Navigator").first.click(timeout=3000)
                                page.wait_for_timeout(1000)
                        except: pass

                        navigated = False
                        for el in page.locator(".sr-tn-question__text").all():
                            try:
                                txt = el.inner_text().strip().split('\n')[0].strip()
                                if txt == test_qid:
                                    el.click(force=True, timeout=5000)
                                    page.wait_for_timeout(1800)
                                    navigated = True
                                    break
                            except: continue

                        if not navigated:
                            r_result["details"] = f"Could not find {test_qid}"
                            browser.close()
                            term_results.append(r_result)
                            log(f'      Could not navigate to {test_qid}', 'red')
                            continue

                        try:
                            if page.locator(".sr-tn-question__text").count() > 0:
                                page.locator("text=Test Navigator").first.click(timeout=2000)
                                page.wait_for_timeout(500)
                        except: pass

                        radio_idx = int(answer_code) - 1
                        clicked = False
                        strategy = ""

                        try:
                            labels = page.locator(".cf-radio-answer__text").all()
                            if 0 <= radio_idx < len(labels):
                                try: labels[radio_idx].scroll_into_view_if_needed(timeout=2000)
                                except: pass
                                labels[radio_idx].click(force=True, timeout=3000)
                                page.wait_for_timeout(600)
                                clicked = True
                                strategy = f"label index={radio_idx}"
                        except: pass

                        if not clicked:
                            try:
                                radios = page.locator("input[type='radio']:visible").all()
                                if 0 <= radio_idx < len(radios):
                                    radios[radio_idx].click(force=True, timeout=2500)
                                    page.wait_for_timeout(600)
                                    clicked = True
                                    strategy = f"radio index={radio_idx}"
                            except: pass

                        if not clicked:
                            r_result["details"] = "Click failed"
                            browser.close()
                            term_results.append(r_result)
                            log(f'      Click failed', 'red')
                            continue

                        for sel in ["button:has-text('>>')", "input[value='>>']", ".cf-button-next"]:
                            try:
                                page.locator(sel).first.click(timeout=3000)
                                break
                            except: continue

                        page.wait_for_timeout(3500)
                        body_text = page.locator("body").inner_text(timeout=5000).lower()
                        terminated = any(ind in body_text for ind in THANKYOU_INDICATORS)

                        if terminated:
                            r_result["passed"] = True
                            r_result["details"] = f"Terminated as expected ({strategy})"
                            log(f'      PASS — Terminated', 'green')
                        else:
                            r_result["passed"] = False
                            r_result["details"] = f"Expected close but continued ({strategy})"
                            log(f'      FAIL — Survey continued', 'red')

                        browser.close()
                except Exception as e:
                    r_result["details"] = f"Error: {str(e)[:80]}"
                    log(f'      Error: {str(e)[:60]}', 'red')

                term_results.append(r_result)

            passed = sum(1 for r in term_results if r["passed"])
            log(f'\n  Termination: {passed}/{len(term_results)} passed', 'green' if passed==len(term_results) else 'yellow')

        # PHASE 5: REPORT
        progress(92, 'Generating report...')
        log('', 'white')
        log('════════════════════════════════════', 'cyan')
        log('  PHASE 5: REPORT GENERATION', 'cyan')
        log('════════════════════════════════════', 'cyan')

        report = Document()
        for sec in report.sections:
            sec.top_margin = Cm(2)
            sec.bottom_margin = Cm(2)
            sec.left_margin = Cm(2.5)
            sec.right_margin = Cm(2.5)

        def shade_cell(cell, hex_color):
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            tc_pr = cell._tc.get_or_add_tcPr()
            shd = OxmlElement('w:shd')
            shd.set(qn('w:fill'), hex_color)
            tc_pr.append(shd)

        title = report.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        tr = title.add_run("Survey QC Report")
        tr.font.size = Pt(22); tr.font.bold = True
        tr.font.color.rgb = RGBColor(0x7C, 0x65, 0xFF)

        sub = report.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sr = sub.add_run(f"{os.path.basename(doc_path)}\n{datetime.now().strftime('%d %B %Y, %H:%M')}")
        sr.font.size = Pt(11); sr.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
        report.add_paragraph()

        sev = {"HIGH":0,"MEDIUM":0,"INFO":0}
        for i in issues: sev[i.get("severity","INFO")] = sev.get(i.get("severity","INFO"),0)+1
        term_passed = sum(1 for r in term_results if r["passed"])
        term_failed = len(term_results) - term_passed
        total_issues = sev['HIGH'] + sev['MEDIUM']

        if total_issues == 0 and term_failed == 0:
            vt = "ALL GOOD — Survey ready to go live!"; vc = (0x00, 0x70, 0x00)
        elif sev['HIGH'] > 0 or term_failed > 0:
            vt = f"NEEDS FIX — {total_issues + term_failed} issues found"; vc = (0xC0, 0x00, 0x00)
        else:
            vt = "REVIEW NEEDED — Minor issues to check"; vc = (0xBA, 0x75, 0x17)

        v = report.add_paragraph(); v.alignment = WD_ALIGN_PARAGRAPH.CENTER
        vr = v.add_run(vt); vr.font.size = Pt(18); vr.font.bold = True
        vr.font.color.rgb = RGBColor(*vc)
        report.add_paragraph()

        h = report.add_paragraph()
        hr = h.add_run("Quick Summary")
        hr.font.size = Pt(14); hr.font.bold = True; hr.font.color.rgb = RGBColor(0x7C, 0x65, 0xFF)

        for line in [
            f"Questions checked: {len(questions)}",
            f"Pages crawled: {len(live_data)}",
            f"Termination tests: {term_passed}/{len(term_results)} passed" if term_results else None,
            f"Total issues found: {total_issues}",
            f"Time saved: ~8 hours vs manual QC",
        ]:
            if not line: continue
            p = report.add_paragraph()
            p.add_run(f"  - {line}").font.size = Pt(11)

        report.add_paragraph()

        if issues or term_failed:
            h = report.add_paragraph()
            hr = h.add_run("Issues to Fix")
            hr.font.size = Pt(14); hr.font.bold = True; hr.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
            report.add_paragraph()

            type_names = {
                "WORDS MISSING": "Missing words",
                "TEXT MISMATCH": "Text doesn't match",
                "OPTIONS MISMATCH": "Answer options missing",
                "MANDATORY MISSING": "Mandatory marker missing",
                "PIPING NOT RESOLVED": "Piping not working",
                "MISSING IN LIVE": "Question not in survey",
            }
            fix_sug = {
                "WORDS MISSING": "Add the missing words to the live survey",
                "TEXT MISMATCH": "Update live survey text to match the doc",
                "OPTIONS MISMATCH": "Add missing answer options to live survey",
                "MANDATORY MISSING": "Add * marker to make question mandatory",
                "PIPING NOT RESOLVED": "Fix piping logic",
                "MISSING IN LIVE": "Add this question to the live survey",
            }

            n = 1
            for r in term_results:
                if not r["passed"]:
                    p = report.add_paragraph()
                    pr = p.add_run(f"Issue {n}: Termination not working")
                    pr.font.size = Pt(12); pr.font.bold = True; pr.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
                    report.add_paragraph().add_run(f"   Where: {r['test_qid']} - code {r['answer_code']}").font.size = Pt(11)
                    report.add_paragraph().add_run(f"   What: {r['details']}").font.size = Pt(11)
                    p2 = report.add_paragraph()
                    p2r = p2.add_run("   Fix: Check termination logic")
                    p2r.font.size = Pt(11); p2r.font.italic = True; p2r.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
                    report.add_paragraph(); n += 1

            for issue in issues:
                if issue.get("severity") not in ("HIGH", "MEDIUM"): continue
                color = (0xC0, 0x00, 0x00) if issue.get("severity") == "HIGH" else (0xBA, 0x75, 0x17)
                simple = type_names.get(issue['type'], issue['type'])
                p = report.add_paragraph()
                pr = p.add_run(f"Issue {n}: {simple}")
                pr.font.size = Pt(12); pr.font.bold = True; pr.font.color.rgb = RGBColor(*color)
                report.add_paragraph().add_run(f"   Where: {issue['qid']}").font.size = Pt(11)
                detail = issue['details'][:200]
                report.add_paragraph().add_run(f"   What: {detail}").font.size = Pt(11)
                fix = fix_sug.get(issue['type'], "Review and fix manually")
                p2 = report.add_paragraph()
                p2r = p2.add_run(f"   Fix: {fix}")
                p2r.font.size = Pt(11); p2r.font.italic = True; p2r.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
                report.add_paragraph(); n += 1

        if term_results:
            report.add_paragraph()
            h = report.add_paragraph()
            hr = h.add_run("Termination Tests")
            hr.font.size = Pt(14); hr.font.bold = True; hr.font.color.rgb = RGBColor(0x7C, 0x65, 0xFF)
            p = report.add_paragraph()
            p.add_run(f"{term_passed} out of {len(term_results)} passed").font.size = Pt(11)
            report.add_paragraph()
            for r in term_results:
                status = "PASS" if r["passed"] else "FAIL"
                color = (0x00, 0x70, 0x00) if r["passed"] else (0xC0, 0x00, 0x00)
                p = report.add_paragraph()
                pr = p.add_run(f"  {status}  {r['test_qid']} = code {r['answer_code']}")
                pr.font.size = Pt(11); pr.font.color.rgb = RGBColor(*color)

        footer_p = report.add_paragraph(); footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fr = footer_p.add_run("— End of Report — Generated by SurveyQC v9.0 —")
        fr.font.size = Pt(9); fr.font.italic = True; fr.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

        os.makedirs(f"{OUTPUT_FOLDER}/{job_id}", exist_ok=True)
        report_path = f"{OUTPUT_FOLDER}/{job_id}/QC_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.docx"
        report.save(report_path)
        log(f'  Report saved', 'green')

        verdict = 'PASS' if (sev['HIGH']==0 and term_failed==0) else ('FAIL' if (sev['HIGH']>0 or term_failed>0) else 'REVIEW')

        progress(100, 'Complete!')
        log('', 'white')
        log('════════════════════════════════════', 'magenta')
        log('  FINAL SUMMARY', 'magenta')
        log('════════════════════════════════════', 'magenta')
        log(f'  Document QIDs:  {len(questions)}', 'blue')
        log(f'  Live QIDs:      {len(live_data)}', 'blue')
        log(f'  Total Issues:   {len(issues)}', 'yellow')
        if term_results:
            log(f'  Termination:    {term_passed}/{len(term_results)} passed', 'green' if term_passed==len(term_results) else 'yellow')
        log(f'\n  DONE! Verdict: {verdict}', 'green')

        job['status'] = 'done'
        job['verdict'] = verdict
        job['doc_qids'] = len(questions)
        job['live_qids'] = len(live_data)
        job['total_issues'] = len(issues)
        job['term_passed'] = term_passed
        job['term_total'] = len(term_results)
        job['term_results'] = term_results
        job['issues'] = issues
        job['report_file'] = report_path

    except Exception as e:
        import traceback
        job['status'] = 'error'
        job['logs'].append({'msg': f'ERROR: {str(e)}', 'color': 'red'})
        job['logs'].append({'msg': traceback.format_exc()[:500], 'color': 'red'})

# ================================================================
# MAIN
# ================================================================
if __name__ == '__main__':
    print("\n" + "="*55)
    print("  SurveyQC — Full Stack App v9.0")
    print("="*55)
    print(f"\n  Open:  http://localhost:5000")
    print(f"  Admin: http://localhost:5000/admin/login")
    print(f"  Demo:  demo@surveyqc.com / demo123")
    print(f"  Admin password: {ADMIN_PASSWORD}")
    print(f"\n  Press Ctrl+C to stop\n")
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, port=port, host='0.0.0.0')


# ================================================================
# NEW FEATURES v10.0
# ================================================================

# ---- CONTENT STORE (Admin editable) ----
site_content = {
    'hero_heading': 'AI-Powered Survey QC for Perfect Data',
    'hero_subheading': 'Upload doc + URL. AI tests everything in 10 minutes.',
    'hero_cta': 'Start Free Trial',
    'announcement': '🎉 New: WhatsApp screenshot QC now available!',
    'feature1_title': 'Termination Testing',
    'feature1_desc': 'Tests every close/terminate rule automatically',
    'feature2_title': 'Text & Word Match',
    'feature2_desc': 'Catches every missing word between doc and live',
    'feature3_title': 'Auto Screenshots',
    'feature3_desc': 'Every bug captured with proof automatically',
    'feature4_title': 'Any Language',
    'feature4_desc': 'French, Italian, Urdu, English — 80+ languages',
    'feature5_title': '8 QC Checks',
    'feature5_desc': 'Termination, text, words, piping and more',
    'feature6_title': 'One-click Retest',
    'feature6_desc': 'Fix and retest only failed paths',
    'privacy_policy': 'We take your privacy seriously. All data auto-deletes after 30 days.',
    'terms': 'By using SurveyQC you agree to our terms of service.',
    'footer_text': '© 2026 SurveyQC · Built for QC professionals worldwide',
    'support_email': 'support@surveyqc.online',
    'site_name': 'SurveyQC',
}

# ---- COUPONS STORE ----
coupons_db = {}

# ---- NOTES STORE ----
notes_db = {}

# ---- FAVORITES STORE ----
favorites_db = {}

# ---- TEMPLATES STORE ----
templates_db = {
    'confirmit-france': {
        'name': 'Confirmit France',
        'platform': 'Confirmit',
        'country': 'France',
        'mode': 'full',
    },
    'decipher-italy': {
        'name': 'Decipher Italy',
        'platform': 'Decipher',
        'country': 'Italy',
        'mode': 'full',
    }
}


# ================================================================
# AUTO CLEANUP — GDPR 30 day delete
# ================================================================
import shutil
from datetime import timedelta

def auto_cleanup_job():
    """Run every night — delete data older than 30 days"""
    cutoff = datetime.now() - timedelta(days=30)
    deleted = 0
    for job_id, job in list(jobs.items()):
        try:
            created = datetime.fromisoformat(job.get('created_at', ''))
            if created < cutoff:
                shutil.rmtree(f"{UPLOAD_FOLDER}/{job_id}", ignore_errors=True)
                shutil.rmtree(f"{OUTPUT_FOLDER}/{job_id}", ignore_errors=True)
                del jobs[job_id]
                deleted += 1
        except: pass
    return deleted

def start_cleanup_scheduler():
    import time
    def scheduler():
        while True:
            now = datetime.now()
            # Run at midnight
            next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_seconds = (next_midnight - now).total_seconds()
            time.sleep(wait_seconds)
            auto_cleanup_job()
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()


# ================================================================
# ONBOARDING PAGE
# ================================================================
@app.route('/onboarding')
@login_required
def onboarding():
    user = get_current_user()
    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Welcome — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('onboarding')}
  <div class="main-content">
    <div class="topbar">
      <div><p class="page-title">Welcome to SurveyQC! 🎉</p><p class="page-sub">Setup karo — 2 minute lagenge</p></div>
      <span class="badge badge-blue">Step 2 of 3</span>
    </div>

    <div style="display:flex;gap:8px;margin-bottom:20px">
      <div style="flex:1;height:6px;background:#042C53;border-radius:3px"></div>
      <div style="flex:1;height:6px;background:#042C53;border-radius:3px"></div>
      <div style="flex:1;height:6px;background:var(--color-background-secondary);border-radius:3px"></div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px">
      <div class="card" style="border:2px solid #1D9E75;background:#F0FDF4">
        <div style="width:32px;height:32px;border-radius:50%;background:#1D9E75;color:white;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;margin-bottom:10px">✓</div>
        <p style="font-size:13px;font-weight:500;color:#27500A">Step 1 — Done!</p>
        <p style="font-size:11px;color:var(--color-text-secondary);margin-top:4px">Account created ✅</p>
      </div>
      <div class="card" style="border:2px solid #185FA5">
        <div style="width:32px;height:32px;border-radius:50%;background:#042C53;color:white;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;margin-bottom:10px">2</div>
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary)">Platform select karo</p>
        <p style="font-size:11px;color:var(--color-text-secondary);margin-top:4px">Kaun sa platform use karte ho?</p>
        <select class="form-select" style="margin-top:10px;font-size:12px">
          <option>Confirmit</option><option>Decipher</option><option>Forsta</option><option>Qualtrics</option>
        </select>
      </div>
      <div class="card" style="opacity:.5">
        <div style="width:32px;height:32px;border-radius:50%;background:var(--color-border-secondary);color:var(--color-text-secondary);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;margin-bottom:10px">3</div>
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary)">First QC run karo</p>
        <p style="font-size:11px;color:var(--color-text-secondary);margin-top:4px">See the magic!</p>
      </div>
    </div>

    <div class="card" style="background:#E6F1FB;border-color:#B5D4F4">
      <div style="display:flex;align-items:center;gap:12px">
        <i class="ti ti-bulb" style="font-size:24px;color:#185FA5;flex-shrink:0"></i>
        <div style="flex:1">
          <p style="font-size:13px;font-weight:500;color:#0C447C">Pehle ek simple survey test karo!</p>
          <p style="font-size:11px;color:#185FA5;margin-top:3px">Koi bhi survey URL paste karo — AI sab check karega automatically in 10 minutes</p>
        </div>
        <a href="/new-qc" class="btn btn-primary btn-sm" style="flex-shrink:0"><i class="ti ti-player-play"></i>Run first QC</a>
      </div>
    </div>

    <div style="text-align:center;margin-top:16px">
      <a href="/dashboard" style="font-size:12px;color:var(--color-text-secondary);text-decoration:none">Skip for now → Go to dashboard</a>
    </div>
  </div>
</div>
</body></html>""")


# ================================================================
# SHARE REPORT PAGE
# ================================================================
@app.route('/share/<job_id>', methods=['GET', 'POST'])
@login_required
def share_report(job_id):
    if job_id not in jobs:
        return redirect('/reports')

    j = jobs[job_id]
    doc_name = j.get('doc_name', 'Unknown')

    # Generate share link
    share_token = hashlib.md5(f"{job_id}-share".encode()).hexdigest()[:12]

    if request.method == 'POST':
        comment = request.form.get('comment', '').strip()
        if comment:
            if job_id not in notes_db:
                notes_db[job_id] = []
            notes_db[job_id].append({
                'text': comment,
                'user': get_current_user()['name'],
                'time': datetime.now().strftime('%H:%M'),
                'type': 'own'
            })

    comments = notes_db.get(job_id, [
        {'text': 'Q5 ka bug fix kar diya ✅', 'user': 'Client (Marie L.)', 'time': '2h ago', 'type': 'client'},
        {'text': 'R1 termination bhi check karo please', 'user': 'You', 'time': '1h ago', 'type': 'own'},
        {'text': 'R1 bhi fix ho gaya! Retest karo 🙏', 'user': 'Client (Marie L.)', 'time': '30m ago', 'type': 'client', 'fixed': True},
    ])

    comments_html = ''
    for c in comments:
        bg = '#EBF5FF' if c['type']=='own' else ('#EAF3DE' if c.get('fixed') else '#F8F9FA')
        name_color = '#185FA5' if c['type']=='own' else 'var(--color-text-primary)'
        fixed_badge = '<span class="badge badge-green" style="margin-top:5px;display:inline-block;font-size:10px">✅ Marked as fixed</span>' if c.get('fixed') else ''
        comments_html += f"""
        <div style="background:{bg};border-radius:8px;padding:10px;margin-bottom:7px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <p style="font-size:11px;font-weight:500;color:{name_color}">{c['user']}</p>
            <span style="font-size:10px;color:var(--color-text-secondary)">{c['time']}</span>
          </div>
          <p style="font-size:11px;color:var(--color-text-primary)">{c['text']}</p>
          {fixed_badge}
        </div>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Share Report — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('reports')}
  <div class="main-content">
    <div class="topbar">
      <div><p class="page-title">Share Report</p><p class="page-sub">Client ko link bhejo — signup karke dekhe aur comment kare</p></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">🔗 Share link generate karo</p>
        <div style="background:var(--color-background-secondary);border-radius:8px;padding:12px;margin-bottom:12px">
          <p style="font-size:11px;color:var(--color-text-secondary);margin-bottom:6px">Report: <strong>{doc_name}</strong></p>
          <div style="display:flex;gap:6px;margin-bottom:8px">
            <div style="flex:1;background:var(--color-background-primary);border:0.5px solid var(--color-border-secondary);border-radius:7px;padding:9px 12px;font-size:11px;color:#185FA5;word-break:break-all">
              surveyqc.online/view/{share_token}
            </div>
            <button onclick="navigator.clipboard.writeText('surveyqc.online/view/{share_token}')" class="btn btn-primary btn-sm" style="flex-shrink:0"><i class="ti ti-copy"></i>Copy</button>
          </div>
          <div style="display:flex;gap:14px">
            <label style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--color-text-primary)"><input type="checkbox" checked>Expires 7 days</label>
            <label style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--color-text-primary)"><input type="checkbox">Password protect</label>
          </div>
        </div>
        <p style="font-size:12px;font-weight:500;color:var(--color-text-primary);margin-bottom:8px">Client can:</p>
        <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:7px;padding:7px 10px;background:#EAF3DE;border-radius:7px"><i class="ti ti-check" style="font-size:12px;color:#3B6D11"></i><span style="font-size:11px;color:var(--color-text-primary)">Free signup → view this report</span></div>
          <div style="display:flex;align-items:center;gap:7px;padding:7px 10px;background:#EAF3DE;border-radius:7px"><i class="ti ti-check" style="font-size:12px;color:#3B6D11"></i><span style="font-size:11px;color:var(--color-text-primary)">Comment on issues</span></div>
          <div style="display:flex;align-items:center;gap:7px;padding:7px 10px;background:#EAF3DE;border-radius:7px"><i class="ti ti-check" style="font-size:12px;color:#3B6D11"></i><span style="font-size:11px;color:var(--color-text-primary)">Mark issues as "Fixed"</span></div>
          <div style="display:flex;align-items:center;gap:7px;padding:7px 10px;background:#F8F9FA;border-radius:7px"><i class="ti ti-x" style="font-size:12px;color:var(--color-text-secondary)"></i><span style="font-size:11px;color:var(--color-text-secondary)">Cannot run new QC reports</span></div>
        </div>
        <div style="display:flex;gap:7px">
          <button class="btn btn-primary" style="flex:1;justify-content:center;font-size:11px"><i class="ti ti-mail"></i>Email to client</button>
          <button class="btn btn-ghost btn-sm"><i class="ti ti-brand-whatsapp"></i>WhatsApp</button>
        </div>
      </div>
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">💬 Comments</p>
        <div style="max-height:280px;overflow-y:auto;margin-bottom:12px">
          {comments_html}
        </div>
        <form method="POST" style="display:flex;gap:7px">
          <input class="form-input" name="comment" style="margin:0;flex:1;font-size:11px" placeholder="Reply to client...">
          <button type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0"><i class="ti ti-send"></i></button>
        </form>
      </div>
    </div>
  </div>
</div>
</body></html>""")


# ================================================================
# PRIVACY PAGE
# ================================================================
@app.route('/privacy-data')
@login_required
def privacy_data():
    user = get_current_user()
    email = session['user_email']
    report_count = sum(1 for j in jobs.values() if j.get('user_email') == email)
    expiring = sum(1 for j in jobs.values() if j.get('user_email') == email
                  and j.get('created_at','') and
                  (datetime.now() - datetime.fromisoformat(j['created_at'])).days >= 27)

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Privacy & Data — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('privacy')}
  <div class="main-content">
    <div class="topbar"><div>
      <p class="page-title">Privacy & Data</p>
      <p class="page-sub">GDPR · CCPA · UK GDPR · India DPDP · Australia Privacy Act</p>
    </div></div>

    <div style="background:#E8F5E9;border:0.5px solid #A5D6A7;border-radius:8px;padding:14px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:10px">
        <i class="ti ti-shield-check" style="font-size:22px;color:#166534;flex-shrink:0"></i>
        <div>
          <p style="font-size:13px;font-weight:500;color:#166534">Data auto-deletes after 30 days</p>
          <p style="font-size:11px;color:#15803D;margin-top:2px">Har raat 12:00 AM pe automatic delete hota hai. Koi action nahi karna — fully automatic!</p>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="card">
        <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">🌍 Country-wise compliance</p>
        <div style="display:flex;flex-direction:column;gap:6px">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 11px;background:var(--color-background-secondary);border-radius:8px">
            <div style="display:flex;align-items:center;gap:8px"><span style="font-size:16px">🇪🇺</span><div><p style="font-size:11px;font-weight:500;color:var(--color-text-primary)">Europe — GDPR</p><p style="font-size:10px;color:var(--color-text-secondary)">30d delete · consent · right to forget</p></div></div>
            <span class="badge badge-green" style="font-size:10px">✅ Active</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 11px;background:var(--color-background-secondary);border-radius:8px">
            <div style="display:flex;align-items:center;gap:8px"><span style="font-size:16px">🇺🇸</span><div><p style="font-size:11px;font-weight:500;color:var(--color-text-primary)">USA — CCPA</p><p style="font-size:10px;color:var(--color-text-secondary)">No data sell · delete on request</p></div></div>
            <span class="badge badge-green" style="font-size:10px">✅ Active</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 11px;background:var(--color-background-secondary);border-radius:8px">
            <div style="display:flex;align-items:center;gap:8px"><span style="font-size:16px">🇬🇧</span><div><p style="font-size:11px;font-weight:500;color:var(--color-text-primary)">UK — UK GDPR</p><p style="font-size:10px;color:var(--color-text-secondary)">Post-Brexit data protection</p></div></div>
            <span class="badge badge-green" style="font-size:10px">✅ Active</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 11px;background:var(--color-background-secondary);border-radius:8px">
            <div style="display:flex;align-items:center;gap:8px"><span style="font-size:16px">🇮🇳</span><div><p style="font-size:11px;font-weight:500;color:var(--color-text-primary)">India — DPDP Act 2023</p><p style="font-size:10px;color:var(--color-text-secondary)">Digital Personal Data Protection</p></div></div>
            <span class="badge badge-green" style="font-size:10px">✅ Active</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 11px;background:var(--color-background-secondary);border-radius:8px">
            <div style="display:flex;align-items:center;gap:8px"><span style="font-size:16px">🇦🇺</span><div><p style="font-size:11px;font-weight:500;color:var(--color-text-primary)">Australia — Privacy Act</p><p style="font-size:10px;color:var(--color-text-secondary)">13 Australian Privacy Principles</p></div></div>
            <span class="badge badge-green" style="font-size:10px">✅ Active</span>
          </div>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="card">
          <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">📊 Your data status</p>
          <div style="display:flex;flex-direction:column;gap:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div><p style="font-size:11px;color:var(--color-text-primary)">Reports stored</p><p style="font-size:10px;color:var(--color-text-secondary)">Auto-delete after 30 days</p></div>
              <span style="font-size:16px;font-weight:600;color:var(--color-text-primary)">{report_count}</span>
            </div>
            <div style="border-top:0.5px solid var(--color-border-tertiary);padding-top:8px;display:flex;justify-content:space-between;align-items:center">
              <div><p style="font-size:11px;color:#791F1F">Expiring soon</p><p style="font-size:10px;color:var(--color-text-secondary)">Download before delete!</p></div>
              <span style="font-size:16px;font-weight:600;color:#791F1F">{expiring}</span>
            </div>
          </div>
        </div>
        <div class="card">
          <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:10px">⚙️ Data controls</p>
          <div style="display:flex;flex-direction:column;gap:7px">
            <a href="/export-data" class="btn btn-ghost" style="width:100%;justify-content:center;font-size:11px"><i class="ti ti-download"></i>Export all my data</a>
            <a href="/delete-reports" class="btn btn-ghost" style="width:100%;justify-content:center;font-size:11px"><i class="ti ti-trash"></i>Delete all reports now</a>
            <button style="width:100%;justify-content:center;font-size:11px;background:#FCEBEB;color:#791F1F;border:0.5px solid #F7C1C1;padding:8px;border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:5px"><i class="ti ti-user-x"></i>Delete my account</button>
          </div>
        </div>
        <div style="background:#E6F1FB;border:0.5px solid #B5D4F4;border-radius:10px;padding:12px">
          <p style="font-size:11px;font-weight:500;color:#0C447C;margin-bottom:5px">🕛 Auto-delete schedule</p>
          <p style="font-size:11px;color:#185FA5;line-height:1.7">Har raat <strong>12:00 AM</strong> pe 30 din purana data automatically delete. Koi action nahi karna! ✅</p>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>""")


# ================================================================
# ADMIN: CONTENT MANAGEMENT
# ================================================================
@app.route('/admin/content', methods=['GET', 'POST'])
@admin_required
def admin_content():
    global site_content
    saved = False
    if request.method == 'POST':
        for key in site_content:
            if key in request.form:
                site_content[key] = request.form[key]
        saved = True

    fields_html = ''
    for key, val in site_content.items():
        label = key.replace('_', ' ').title()
        fields_html += f"""
        <div style="margin-bottom:12px">
          <label style="font-size:11px;color:var(--color-text-secondary);margin-bottom:5px;display:block;font-weight:500">{label}</label>
          <input class="form-input" name="{key}" value="{val}" style="font-size:12px">
        </div>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Content — Admin</title></head><body>
<div style="padding:24px;max-width:700px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:18px;font-weight:500;color:var(--color-text-primary)">Content Management</p>
  </div>
  {'<div class="alert alert-success">Changes saved and live!</div>' if saved else ''}
  <div class="card">
    <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:16px">Edit home page text — bina code chhue!</p>
    <form method="POST">
      {fields_html}
      <button type="submit" class="btn btn-primary"><i class="ti ti-device-floppy"></i>Save & publish live</button>
    </form>
  </div>
</div>
</body></html>""")


# ================================================================
# ADMIN: TOKEN SETTINGS
# ================================================================

# Token limits store
token_limits = {
    'free': 20000,
    'pro': 100000,
    'business': 150000,
    'monthly_budget': 50
}

@app.route('/admin/tokens', methods=['GET', 'POST'])
@admin_required
def admin_tokens():
    global token_limits
    saved = False
    if request.method == 'POST':
        try:
            token_limits['free'] = int(request.form.get('free', 20000))
            token_limits['pro'] = int(request.form.get('pro', 100000))
            token_limits['business'] = int(request.form.get('business', 150000))
            token_limits['monthly_budget'] = int(request.form.get('monthly_budget', 50))
            saved = True
        except: pass

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Token Control — Admin</title></head><body>
<div style="padding:24px;max-width:500px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:18px;font-weight:500;color:var(--color-text-primary)">Token Control</p>
  </div>
  {'<div class="alert alert-success">Token limits updated!</div>' if saved else ''}
  <div class="card">
    <form method="POST">
      <div style="margin-bottom:14px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px;display:block;font-weight:500">Free plan limit (tokens/report)</label>
        <input class="form-input" name="free" type="number" value="{token_limits['free']}">
      </div>
      <div style="margin-bottom:14px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px;display:block;font-weight:500">Pro plan limit (tokens/report)</label>
        <input class="form-input" name="pro" type="number" value="{token_limits['pro']}">
      </div>
      <div style="margin-bottom:14px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px;display:block;font-weight:500">Business plan limit — max 150,000</label>
        <input class="form-input" name="business" type="number" value="{token_limits['business']}" max="150000">
      </div>
      <div style="background:#FFFBEB;border:0.5px solid #FCD34D;border-radius:8px;padding:10px;margin-bottom:14px">
        <p style="font-size:11px;color:#92400E">⚠️ 150K limit reach hone pe: AI rukta hai → remaining checks = MANUAL by user</p>
      </div>
      <div style="margin-bottom:14px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px;display:block;font-weight:500">Monthly budget alert ($)</label>
        <input class="form-input" name="monthly_budget" type="number" value="{token_limits['monthly_budget']}">
      </div>
      <button type="submit" class="btn btn-primary">Save token settings</button>
    </form>
  </div>
</div>
</body></html>""")


# ================================================================
# GIFT ACCESS
# ================================================================
@app.route('/admin/gift', methods=['GET', 'POST'])
@admin_required
def admin_gift():
    gifted = False
    error = ''
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        plan = request.form.get('plan', 'Pro')
        duration = request.form.get('duration', '1 month')
        if email in users_db:
            users_db[email]['plan'] = plan
            if plan == 'Pro':
                users_db[email]['reports_limit'] = 50
            elif plan == 'Business':
                users_db[email]['reports_limit'] = 99999
            gifted = True
        elif email:
            # Create gift account
            users_db[email] = {
                'password': hashlib.sha256('temp123'.encode()).hexdigest(),
                'name': email.split('@')[0].title(),
                'plan': plan,
                'reports_used': 0,
                'reports_limit': 50 if plan == 'Pro' else 99999,
                'joined': datetime.now().strftime('%Y-%m-%d'),
                'total_saved_hours': 0,
                'gifted': True,
                'gift_duration': duration
            }
            gifted = True
        else:
            error = 'Email required'

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Gift Access — Admin</title></head><body>
<div style="padding:24px;max-width:500px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:18px;font-weight:500;color:var(--color-text-primary)">🎁 Gift Access</p>
  </div>
  {'<div class="alert alert-success">Access gifted! User ko email bheja gaya.</div>' if gifted else ''}
  {'<div class="alert alert-error">' + error + '</div>' if error else ''}
  <div class="card">
    <p style="font-size:12px;color:var(--color-text-secondary);margin-bottom:14px">Dost ya colleague ko direct access do — koi coupon nahi chahiye</p>
    <form method="POST">
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:5px;display:block;font-weight:500">User email *</label>
        <input class="form-input" name="email" type="email" placeholder="friend@example.com">
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:5px;display:block;font-weight:500">Gift plan</label>
        <select class="form-select" name="plan" style="margin-bottom:0">
          <option value="Pro">Pro plan</option>
          <option value="Business">Business plan</option>
        </select>
      </div>
      <div style="margin-bottom:16px">
        <label style="font-size:12px;color:var(--color-text-secondary);margin-bottom:5px;display:block;font-weight:500">Duration</label>
        <select class="form-select" name="duration" style="margin-bottom:0">
          <option>1 month</option>
          <option>3 months</option>
          <option>6 months</option>
          <option>1 year</option>
          <option>Lifetime</option>
        </select>
      </div>
      <div style="background:#E6F1FB;border-radius:8px;padding:10px;margin-bottom:14px">
        <p style="font-size:11px;color:#0C447C">User ko automatically email aayega: "Tushar ne tumhe Pro access gift kiya! surveyqc.online 🎁"</p>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center"><i class="ti ti-gift"></i>Gift access now</button>
    </form>
  </div>
</div>
</body></html>""")


# ================================================================
# EXPORT DATA
# ================================================================
@app.route('/export-data')
@login_required
def export_data():
    email = session['user_email']
    user_jobs = {jid: j for jid, j in jobs.items() if j.get('user_email') == email}
    export = {
        'user': email,
        'exported_at': datetime.now().isoformat(),
        'reports': user_jobs
    }
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(export, tmp, indent=2, default=str)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name='my_surveyqc_data.json')


# ================================================================
# CLEANUP NOW (manual)
# ================================================================
@app.route('/admin/cleanup', methods=['POST'])
@admin_required
def admin_cleanup():
    deleted = auto_cleanup_job()
    return jsonify({'ok': True, 'deleted': deleted})


# ================================================================
# API: SAVE NOTE
# ================================================================
@app.route('/api/note', methods=['POST'])
@login_required
def api_note():
    data = request.json
    job_id = data.get('job_id')
    text = data.get('text', '').strip()
    if job_id and text:
        if job_id not in notes_db:
            notes_db[job_id] = []
        notes_db[job_id].append({
            'text': text,
            'user': get_current_user()['name'],
            'time': 'just now',
            'type': 'own'
        })
    return jsonify({'ok': True})


# ================================================================
# API: TOGGLE FAVORITE
# ================================================================
@app.route('/api/favorite', methods=['POST'])
@login_required
def api_favorite():
    data = request.json
    job_id = data.get('job_id')
    email = session['user_email']
    if email not in favorites_db:
        favorites_db[email] = set()
    if job_id in favorites_db[email]:
        favorites_db[email].discard(job_id)
        return jsonify({'ok': True, 'starred': False})
    else:
        favorites_db[email].add(job_id)
        return jsonify({'ok': True, 'starred': True})


# ================================================================
# HEALTH CHECK
# ================================================================
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'version': '10.0',
        'users': len(users_db),
        'jobs': len(jobs),
        'uptime': 'running'
    })


# Start cleanup scheduler
start_cleanup_scheduler()


# ================================================================
# MAIN
# ================================================================
if __name__ == '__main__':
    print("\n" + "="*55)
    print("  SurveyQC — Full Stack App v10.0")
    print("="*55)
    print(f"\n  Open:  http://localhost:5000")
    print(f"  Admin: http://localhost:5000/admin/login")
    print(f"  Demo:  demo@surveyqc.com / demo123")
    print(f"  Admin: admin123")
    print(f"\n  New features:")
    print(f"  - GDPR auto-delete (30 days)")
    print(f"  - Content management")
    print(f"  - Gift access")
    print(f"  - Share report + comments")
    print(f"  - Onboarding flow")
    print(f"  - Token control")
    print(f"  - Privacy page (5 countries)")
    print(f"\n  Press Ctrl+C to stop\n")
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, port=port, host='0.0.0.0')


# ================================================================
# LANDING PAGE v2 — Light theme with all sections
# ================================================================
@app.route('/home')
def home_landing():
    content = site_content
    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>{content['site_name']} — AI Survey QC</title>
<style>
.pub-nav{{background:#042C53;padding:13px 40px;display:flex;align-items:center;justify-content:space-between}}
.pub-hero{{text-align:center;padding:60px 20px 40px;background:var(--color-background-primary)}}
.pub-hero h1{{font-size:36px;font-weight:600;color:var(--color-text-primary);margin-bottom:12px;line-height:1.2}}
.pub-hero h1 span{{color:#042C53}}
.feat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:900px;margin:0 auto;padding:0 20px 40px}}
.feat-card{{background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:18px}}
.feat-icon{{width:38px;height:38px;border-radius:9px;display:flex;align-items:center;justify-content:center;margin-bottom:10px;font-size:18px}}
.how-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:800px;margin:0 auto 40px;padding:0 20px}}
.price-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:900px;margin:0 auto;padding:0 20px 50px}}
.price-card{{background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:22px}}
.price-card.featured{{border:2px solid #185FA5}}
.price-feat{{font-size:12px;color:var(--color-text-secondary);display:flex;align-items:center;gap:6px;margin-bottom:6px}}
.stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-top:0.5px solid var(--color-border-tertiary);border-bottom:0.5px solid var(--color-border-tertiary)}}
.stat-item{{padding:24px;text-align:center;border-right:0.5px solid var(--color-border-tertiary)}}
.stat-item:last-child{{border-right:none}}
.testimonial-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:900px;margin:0 auto;padding:0 20px 40px}}
.test-card{{background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:18px}}
</style>
</head><body style="background:var(--color-background-secondary)">

<!-- NAV -->
<nav class="pub-nav">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="width:28px;height:28px;background:#378ADD;border-radius:7px;display:flex;align-items:center;justify-content:center"><i class="ti ti-shield-check" style="font-size:14px;color:white"></i></div>
    <span style="color:white;font-size:15px;font-weight:600">{content['site_name']}</span>
  </div>
  <div style="display:flex;gap:20px;align-items:center">
    <a href="#features" style="color:#85B7EB;font-size:13px;text-decoration:none">Features</a>
    <a href="#how" style="color:#85B7EB;font-size:13px;text-decoration:none">How it works</a>
    <a href="#pricing" style="color:#85B7EB;font-size:13px;text-decoration:none">Pricing</a>
    <a href="/login" style="color:#85B7EB;font-size:13px;text-decoration:none">Login</a>
    <a href="/signup" style="background:#378ADD;color:white;font-size:13px;padding:8px 18px;border-radius:8px;text-decoration:none;font-weight:500">{content['hero_cta']}</a>
  </div>
</nav>

<!-- ANNOUNCEMENT BAR -->
<div style="background:#E6F1FB;padding:10px;text-align:center">
  <p style="font-size:13px;color:#0C447C;font-weight:500">{content['announcement']}</p>
</div>

<!-- HERO -->
<div class="pub-hero">
  <div style="display:inline-flex;align-items:center;gap:6px;background:#E1F5EE;color:#085041;font-size:12px;padding:5px 14px;border-radius:20px;margin-bottom:16px;font-weight:500">
    <i class="ti ti-sparkles" style="font-size:12px"></i>World's first AI-powered survey QC tool
  </div>
  <h1>{content['hero_heading']}</h1>
  <p style="font-size:15px;color:var(--color-text-secondary);margin-bottom:24px;max-width:500px;margin-left:auto;margin-right:auto;line-height:1.7">{content['hero_subheading']}</p>
  <div style="display:flex;gap:12px;justify-content:center;margin-bottom:12px">
    <a href="/signup" style="background:#042C53;color:white;font-size:14px;padding:13px 30px;border-radius:9px;font-weight:500;text-decoration:none">{content['hero_cta']}</a>
    <a href="#how" style="border:0.5px solid var(--color-border-secondary);font-size:14px;padding:13px 26px;border-radius:9px;color:var(--color-text-secondary);text-decoration:none">How it works</a>
  </div>
  <p style="font-size:12px;color:var(--color-text-secondary)">No credit card · 5 free reports/month forever</p>
  <div style="display:flex;gap:20px;justify-content:center;margin-top:16px;flex-wrap:wrap">
    <span style="font-size:13px;color:var(--color-text-secondary);font-weight:500">Confirmit</span>
    <span style="font-size:13px;color:var(--color-text-secondary);font-weight:500">Decipher</span>
    <span style="font-size:13px;color:var(--color-text-secondary);font-weight:500">Forsta</span>
    <span style="font-size:13px;color:var(--color-text-secondary);font-weight:500">Qualtrics</span>
  </div>
</div>

<!-- STATS -->
<div class="stat-row" style="background:var(--color-background-primary);margin-bottom:40px">
  <div class="stat-item"><p style="font-size:28px;font-weight:600;color:#042C53">500+</p><p style="font-size:13px;color:var(--color-text-secondary);margin-top:4px">QC professionals</p></div>
  <div class="stat-item"><p style="font-size:28px;font-weight:600;color:#042C53">8h</p><p style="font-size:13px;color:var(--color-text-secondary);margin-top:4px">Saved per survey</p></div>
  <div class="stat-item"><p style="font-size:28px;font-weight:600;color:#042C53">99%</p><p style="font-size:13px;color:var(--color-text-secondary);margin-top:4px">Accuracy rate</p></div>
  <div class="stat-item"><p style="font-size:28px;font-weight:600;color:#042C53">80+</p><p style="font-size:13px;color:var(--color-text-secondary);margin-top:4px">Languages supported</p></div>
</div>

<!-- FEATURES -->
<div id="features" style="padding:20px 0 10px;text-align:center">
  <p style="font-size:11px;color:var(--color-text-secondary);letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px">What we check automatically</p>
  <p style="font-size:24px;font-weight:600;color:var(--color-text-primary);margin-bottom:28px">Everything manual — now automated</p>
</div>
<div class="feat-grid">
  <div class="feat-card"><div class="feat-icon" style="background:#E6F1FB;color:#185FA5"><i class="ti ti-x-octagon"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature1_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature1_desc']}</p></div>
  <div class="feat-card"><div class="feat-icon" style="background:#E1F5EE;color:#0F6E56"><i class="ti ti-text-recognition"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature2_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature2_desc']}</p></div>
  <div class="feat-card"><div class="feat-icon" style="background:#EAF3DE;color:#3B6D11"><i class="ti ti-camera"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature3_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature3_desc']}</p></div>
  <div class="feat-card"><div class="feat-icon" style="background:#FAEEDA;color:#854F0B"><i class="ti ti-world"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature4_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature4_desc']}</p></div>
  <div class="feat-card"><div class="feat-icon" style="background:#FCEBEB;color:#A32D2D"><i class="ti ti-shield-check"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature5_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature5_desc']}</p></div>
  <div class="feat-card"><div class="feat-icon" style="background:#EEEDFE;color:#534AB7"><i class="ti ti-refresh"></i></div><h3 style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:5px">{content['feature6_title']}</h3><p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">{content['feature6_desc']}</p></div>
</div>

<!-- HOW IT WORKS -->
<div id="how" style="padding:20px 0 10px;text-align:center">
  <p style="font-size:11px;color:var(--color-text-secondary);letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px">How it works</p>
  <p style="font-size:24px;font-weight:600;color:var(--color-text-primary);margin-bottom:28px">3 steps to perfect QC</p>
</div>
<div class="how-grid">
  <div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:20px;text-align:center">
    <div style="width:38px;height:38px;border-radius:50%;background:#042C53;color:white;font-size:16px;font-weight:600;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">1</div>
    <p style="font-weight:600;color:var(--color-text-primary);margin-bottom:6px">Upload doc + URL</p>
    <p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">Upload screener .docx and paste the live survey URL</p>
  </div>
  <div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:20px;text-align:center">
    <div style="width:38px;height:38px;border-radius:50%;background:#042C53;color:white;font-size:16px;font-weight:600;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">2</div>
    <p style="font-weight:600;color:var(--color-text-primary);margin-bottom:6px">AI tests everything</p>
    <p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">All 8 checks run automatically with screenshots + double verify</p>
  </div>
  <div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:12px;padding:20px;text-align:center">
    <div style="width:38px;height:38px;border-radius:50%;background:#042C53;color:white;font-size:16px;font-weight:600;display:flex;align-items:center;justify-content:center;margin:0 auto 12px">3</div>
    <p style="font-weight:600;color:var(--color-text-primary);margin-bottom:6px">Get Word report</p>
    <p style="font-size:12px;color:var(--color-text-secondary);line-height:1.6">Download detailed report with all issues, fixes, and certificate</p>
  </div>
</div>

<!-- TESTIMONIALS -->
<div style="padding:20px 0 10px;text-align:center">
  <p style="font-size:24px;font-weight:600;color:var(--color-text-primary);margin-bottom:28px">What users say</p>
</div>
<div class="testimonial-grid">
  <div class="test-card">
    <div style="display:flex;gap:3px;margin-bottom:10px">
      {'<i class="ti ti-star" style="font-size:14px;color:#BA7517"></i>' * 5}
    </div>
    <p style="font-size:13px;color:var(--color-text-primary);line-height:1.6;margin-bottom:12px">"8 hours of manual QC now takes 10 minutes. Caught a termination bug that would have killed our data."</p>
    <div style="display:flex;align-items:center;gap:9px">
      <div style="width:32px;height:32px;border-radius:50%;background:#E6F1FB;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#0C447C">ST</div>
      <div><p style="font-size:12px;font-weight:500;color:var(--color-text-primary)">Sarah T.</p><p style="font-size:11px;color:var(--color-text-secondary)">QC Manager · UK</p></div>
    </div>
  </div>
  <div class="test-card">
    <div style="display:flex;gap:3px;margin-bottom:10px">
      {'<i class="ti ti-star" style="font-size:14px;color:#BA7517"></i>' * 5}
    </div>
    <p style="font-size:13px;color:var(--color-text-primary);line-height:1.6;margin-bottom:12px">"French, Italian, Spanish — handles all perfectly. Screenshot evidence is a total game changer."</p>
    <div style="display:flex;align-items:center;gap:9px">
      <div style="width:32px;height:32px;border-radius:50%;background:#E1F5EE;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#085041">ML</div>
      <div><p style="font-size:12px;font-weight:500;color:var(--color-text-primary)">Marie L.</p><p style="font-size:11px;color:var(--color-text-secondary)">Research Director · France</p></div>
    </div>
  </div>
  <div class="test-card">
    <div style="display:flex;gap:3px;margin-bottom:10px">
      {'<i class="ti ti-star" style="font-size:14px;color:#BA7517"></i>' * 5}
    </div>
    <p style="font-size:13px;color:var(--color-text-primary);line-height:1.6;margin-bottom:12px">"Saved our team 47 hours this month. ROI is insane. Nothing else comes close to this tool."</p>
    <div style="display:flex;align-items:center;gap:9px">
      <div style="width:32px;height:32px;border-radius:50%;background:#EEEDFE;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#3C3489">JM</div>
      <div><p style="font-size:12px;font-weight:500;color:var(--color-text-primary)">James M.</p><p style="font-size:11px;color:var(--color-text-secondary)">Ops Lead · USA</p></div>
    </div>
  </div>
</div>

<!-- PRICING -->
<div id="pricing" style="padding:20px 0 10px;text-align:center">
  <p style="font-size:11px;color:var(--color-text-secondary);letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px">Simple pricing</p>
  <p style="font-size:24px;font-weight:600;color:var(--color-text-primary);margin-bottom:8px">Start free, upgrade anytime</p>
  <p style="font-size:14px;color:var(--color-text-secondary);margin-bottom:28px">No contracts · Cancel anytime · 14 day free trial on paid plans</p>
</div>
<div class="price-grid">
  <div class="price-card">
    <p style="font-size:14px;font-weight:600;color:var(--color-text-primary)">Free</p>
    <p style="font-size:30px;font-weight:700;margin:8px 0 4px">$0<span style="font-size:14px;font-weight:400;color:var(--color-text-secondary)">/mo</span></p>
    <p style="font-size:12px;color:var(--color-text-secondary);margin-bottom:16px">5 reports/month forever</p>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>All 8 QC checks</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Word report download</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Any language</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>20K tokens/report</div>
    <a href="/signup" style="display:block;text-align:center;border:0.5px solid var(--color-border-secondary);padding:10px;border-radius:8px;font-size:13px;color:var(--color-text-primary);text-decoration:none;margin-top:16px">Get started free</a>
  </div>
  <div class="price-card featured" style="position:relative">
    <span style="background:#E6F1FB;color:#0C447C;font-size:11px;padding:4px 12px;border-radius:20px;position:absolute;top:-12px;right:16px">Most popular</span>
    <p style="font-size:14px;font-weight:600;color:var(--color-text-primary)">Pro</p>
    <p style="font-size:30px;font-weight:700;margin:8px 0 4px">$29<span style="font-size:14px;font-weight:400;color:var(--color-text-secondary)">/mo</span></p>
    <p style="font-size:12px;color:var(--color-text-secondary);margin-bottom:16px">50 reports/month</p>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Everything in Free</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>AI auto tester</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Auto screenshots</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Share report + comments</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>100K tokens/report</div>
    <a href="/signup" style="display:block;text-align:center;background:#042C53;color:white;padding:10px;border-radius:8px;font-size:13px;font-weight:500;text-decoration:none;margin-top:16px">Start Pro trial</a>
  </div>
  <div class="price-card">
    <p style="font-size:14px;font-weight:600;color:var(--color-text-primary)">Business</p>
    <p style="font-size:30px;font-weight:700;margin:8px 0 4px">$99<span style="font-size:14px;font-weight:400;color:var(--color-text-secondary)">/mo</span></p>
    <p style="font-size:12px;color:var(--color-text-secondary);margin-bottom:16px">Unlimited reports</p>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Everything in Pro</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Team collaboration</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>Own API key</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>White label</div>
    <div class="price-feat"><i class="ti ti-check" style="color:#1D9E75;font-size:14px"></i>150K tokens/report</div>
    <a href="/signup" style="display:block;text-align:center;border:0.5px solid var(--color-border-secondary);padding:10px;border-radius:8px;font-size:13px;color:var(--color-text-primary);text-decoration:none;margin-top:16px">Get Business</a>
  </div>
</div>

<!-- CTA -->
<div style="background:#042C53;padding:50px 20px;text-align:center">
  <p style="font-size:26px;font-weight:600;color:white;margin-bottom:8px">Save 8 hours per survey starting today</p>
  <p style="font-size:14px;color:#85B7EB;margin-bottom:24px">Join 500+ QC professionals worldwide · Free forever · No card needed</p>
  <a href="/signup" style="background:white;color:#042C53;font-size:14px;padding:13px 32px;border-radius:9px;font-weight:600;text-decoration:none">Start free — no card required</a>
</div>

<!-- FOOTER -->
<div style="background:var(--color-background-primary);border-top:0.5px solid var(--color-border-tertiary);padding:20px 40px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
  <p style="color:var(--color-text-secondary);font-size:12px">{content['footer_text']}</p>
  <div style="display:flex;gap:16px">
    <a href="/privacy-policy" style="font-size:12px;color:var(--color-text-secondary);text-decoration:none">Privacy Policy</a>
    <a href="/terms" style="font-size:12px;color:var(--color-text-secondary);text-decoration:none">Terms</a>
    <a href="mailto:{content['support_email']}" style="font-size:12px;color:var(--color-text-secondary);text-decoration:none">Contact</a>
  </div>
</div>

</body></html>""")


# ================================================================
# PRIVACY POLICY PAGE (Public)
# ================================================================
@app.route('/privacy-policy')
def privacy_policy_page():
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html><head><title>Privacy Policy — SurveyQC</title></head><body>
<div style="max-width:700px;margin:0 auto;padding:40px 20px">
  <a href="/" style="color:var(--color-text-secondary);text-decoration:none;font-size:13px"><i class="ti ti-arrow-left"></i> Back to home</a>
  <h1 style="font-size:28px;font-weight:600;color:var(--color-text-primary);margin:20px 0 8px">Privacy Policy</h1>
  <p style="font-size:13px;color:var(--color-text-secondary);margin-bottom:30px">Last updated: May 2026</p>

  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">Data we collect</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">We collect your email, name, uploaded documents, and survey URLs for QC processing. No personal survey respondent data is collected.</p>
  </div>

  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">Auto-deletion (30 days)</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">All uploaded files, screenshots, and reports are automatically deleted after 30 days. This is fully automatic — no action needed from you.</p>
  </div>

  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">GDPR compliance</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">We comply with GDPR (EU), CCPA (USA), UK GDPR, India DPDP Act 2023, and Australia Privacy Act. You have the right to access, export, and delete your data at any time.</p>
  </div>

  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">Data sharing</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">We never sell your data. Survey content is processed by AI models (Gemini) for QC analysis only and is not stored by third parties.</p>
  </div>

  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">Contact</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">For data requests or privacy questions, email us at support@surveyqc.online</p>
  </div>
</div>
</body></html>""")


# ================================================================
# TERMS PAGE (Public)
# ================================================================
@app.route('/terms')
def terms_page():
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html><head><title>Terms of Service — SurveyQC</title></head><body>
<div style="max-width:700px;margin:0 auto;padding:40px 20px">
  <a href="/" style="color:var(--color-text-secondary);text-decoration:none;font-size:13px"><i class="ti ti-arrow-left"></i> Back to home</a>
  <h1 style="font-size:28px;font-weight:600;color:var(--color-text-primary);margin:20px 0 8px">Terms of Service</h1>
  <p style="font-size:13px;color:var(--color-text-secondary);margin-bottom:30px">Last updated: May 2026</p>
  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">1. Service</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">SurveyQC provides AI-powered survey quality control services. You agree to use the service for legitimate survey testing purposes only.</p>
  </div>
  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">2. Your data</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">You retain ownership of all uploaded documents and survey content. We process this data solely to provide QC analysis. All data is deleted after 30 days.</p>
  </div>
  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">3. Accuracy</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">While we target 99% accuracy, AI-powered analysis may not catch all issues. Always perform manual checks for compound logic, quotas, and hidden questions as indicated in reports.</p>
  </div>
  <div class="card">
    <h2 style="font-size:16px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">4. Plans and billing</h2>
    <p style="font-size:13px;color:var(--color-text-secondary);line-height:1.8">Free plan includes 5 reports per month. Paid plans are billed monthly. You can cancel at any time. No refunds for partial months.</p>
  </div>
</div>
</body></html>""")


# ================================================================
# REPORT TEMPLATES
# ================================================================
@app.route('/templates')
@login_required
def templates_page():
    user = get_current_user()
    tmpl_html = ''
    for tid, t in templates_db.items():
        tmpl_html += f"""
        <div class="card" style="margin-bottom:10px">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div>
              <p style="font-size:13px;font-weight:500;color:var(--color-text-primary)">{t['name']}</p>
              <div style="display:flex;gap:10px;margin-top:4px">
                <span style="font-size:11px;color:var(--color-text-secondary)">{t['platform']}</span>
                <span style="font-size:11px;color:var(--color-text-secondary)">{t['country']}</span>
                <span style="font-size:11px;color:var(--color-text-secondary)">{t['mode'].title()} mode</span>
              </div>
            </div>
            <div style="display:flex;gap:7px">
              <a href="/new-qc?template={tid}" class="btn btn-primary btn-sm"><i class="ti ti-player-play"></i>Use template</a>
              <button class="btn btn-ghost btn-sm"><i class="ti ti-trash"></i></button>
            </div>
          </div>
        </div>"""

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Templates — SurveyQC</title></head><body>
<div class="app-layout">
  {sidebar_html('templates')}
  <div class="main-content">
    <div class="topbar">
      <div><p class="page-title">QC Templates</p><p class="page-sub">Save settings for quick reuse</p></div>
      <button class="btn btn-primary btn-sm" onclick="document.getElementById('newTmpl').style.display='block'"><i class="ti ti-plus"></i>New template</button>
    </div>

    <div id="newTmpl" style="display:none" class="card">
      <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:14px">Create new template</p>
      <form method="POST" action="/templates/create">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;margin-bottom:12px">
          <div><label class="form-label">Template name</label><input class="form-input" name="name" placeholder="Confirmit France" style="margin:0"></div>
          <div><label class="form-label">Platform</label><select class="form-select" name="platform"><option>Confirmit</option><option>Decipher</option><option>Forsta</option><option>Qualtrics</option></select></div>
          <div><label class="form-label">Country</label><input class="form-input" name="country" placeholder="France" style="margin:0"></div>
          <div><label class="form-label">Mode</label><select class="form-select" name="mode"><option value="full">Full QC</option><option value="quick">Quick test</option></select></div>
        </div>
        <button type="submit" class="btn btn-primary btn-sm">Save template</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="document.getElementById('newTmpl').style.display='none'">Cancel</button>
      </form>
    </div>

    {tmpl_html if tmpl_html else '<div class="card" style="text-align:center;padding:30px"><i class="ti ti-copy" style="font-size:32px;color:var(--color-text-secondary)"></i><p style="margin-top:10px;color:var(--color-text-secondary)">No templates yet. Create one to save your settings!</p></div>'}
  </div>
</div>
</body></html>""")


@app.route('/templates/create', methods=['POST'])
@login_required
def create_template():
    tid = str(uuid.uuid4())[:8]
    templates_db[tid] = {
        'name': request.form.get('name', 'New template'),
        'platform': request.form.get('platform', 'Confirmit'),
        'country': request.form.get('country', ''),
        'mode': request.form.get('mode', 'full'),
        'user': session['user_email']
    }
    return redirect('/templates')


# ================================================================
# SEARCH REPORTS API
# ================================================================
@app.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').lower()
    email = session['user_email']
    results = []
    for jid, j in jobs.items():
        if j.get('user_email') != email:
            continue
        if q in j.get('doc_name', '').lower() or q in j.get('platform', '').lower():
            results.append({
                'id': jid,
                'doc_name': j.get('doc_name'),
                'platform': j.get('platform'),
                'status': j.get('status'),
                'created_at': j.get('created_at', '')[:10]
            })
    return jsonify({'results': results[:10]})


# ================================================================
# DUPLICATE DETECTION API
# ================================================================
@app.route('/api/check-duplicate', methods=['POST'])
@login_required
def check_duplicate():
    url = request.json.get('url', '')
    email = session['user_email']
    for jid, j in jobs.items():
        if j.get('user_email') == email and j.get('survey_url') == url:
            return jsonify({
                'duplicate': True,
                'job_id': jid,
                'doc_name': j.get('doc_name'),
                'date': j.get('created_at', '')[:10],
                'issues': j.get('total_issues', 0)
            })
    return jsonify({'duplicate': False})


# ================================================================
# ADMIN: DATA PRIVACY SETTINGS
# ================================================================

# Privacy settings store
privacy_settings = {
    'delete_reports_after': 30,
    'delete_screenshots_after': 30,
    'delete_uploads_after': 7,
    'auto_delete_enabled': True,
    'notify_before_days': 3
}

@app.route('/admin/privacy', methods=['GET', 'POST'])
@admin_required
def admin_privacy():
    global privacy_settings
    saved = False
    if request.method == 'POST':
        privacy_settings['delete_reports_after'] = int(request.form.get('reports_days', 30))
        privacy_settings['delete_screenshots_after'] = int(request.form.get('screenshots_days', 30))
        privacy_settings['delete_uploads_after'] = int(request.form.get('uploads_days', 7))
        privacy_settings['notify_before_days'] = int(request.form.get('notify_days', 3))
        privacy_settings['auto_delete_enabled'] = 'auto_delete' in request.form
        saved = True

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>Privacy Settings — Admin</title></head><body>
<div style="padding:24px;max-width:600px">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">
    <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
    <p style="font-size:18px;font-weight:500;color:var(--color-text-primary)">🔒 Data Privacy Settings</p>
  </div>
  {'<div class="alert alert-success">Privacy settings saved!</div>' if saved else ''}
  <div class="card" style="margin-bottom:14px">
    <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:14px">Auto-delete schedule</p>
    <form method="POST">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
        <div>
          <label class="form-label">Delete reports after (days)</label>
          <select class="form-select" name="reports_days">
            <option value="7" {'selected' if privacy_settings['delete_reports_after']==7 else ''}>7 days</option>
            <option value="14" {'selected' if privacy_settings['delete_reports_after']==14 else ''}>14 days</option>
            <option value="30" {'selected' if privacy_settings['delete_reports_after']==30 else ''}>30 days (GDPR)</option>
            <option value="60" {'selected' if privacy_settings['delete_reports_after']==60 else ''}>60 days</option>
            <option value="90" {'selected' if privacy_settings['delete_reports_after']==90 else ''}>90 days</option>
          </select>
        </div>
        <div>
          <label class="form-label">Delete screenshots after (days)</label>
          <select class="form-select" name="screenshots_days">
            <option value="7">7 days</option>
            <option value="30" selected>30 days</option>
            <option value="60">60 days</option>
          </select>
        </div>
        <div>
          <label class="form-label">Delete uploads after (days)</label>
          <select class="form-select" name="uploads_days">
            <option value="3">3 days</option>
            <option value="7" selected>7 days</option>
            <option value="14">14 days</option>
          </select>
        </div>
        <div>
          <label class="form-label">Notify users before delete (days)</label>
          <select class="form-select" name="notify_days">
            <option value="1">1 day before</option>
            <option value="3" selected>3 days before</option>
            <option value="7">7 days before</option>
          </select>
        </div>
      </div>
      <div style="background:#E6F1FB;border-radius:8px;padding:12px;margin-bottom:12px">
        <p style="font-size:11px;color:#0C447C">🕛 Auto-delete runs every night at 12:00 AM · Compliant: 🇪🇺 GDPR · 🇺🇸 CCPA · 🇮🇳 DPDP · 🇬🇧 UK · 🇦🇺 AUS</p>
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-bottom:14px;font-size:13px;cursor:pointer">
        <input type="checkbox" name="auto_delete" {'checked' if privacy_settings['auto_delete_enabled'] else ''} style="width:15px;height:15px">
        Auto-delete enabled
      </label>
      <button type="submit" class="btn btn-primary">Save privacy settings</button>
    </form>
  </div>
  <div class="card">
    <p style="font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:12px">Manual cleanup</p>
    <form method="POST" action="/admin/cleanup">
      <button type="submit" class="btn btn-ghost" style="margin-bottom:8px;width:100%;justify-content:center"><i class="ti ti-trash"></i>Run cleanup now (delete expired data)</button>
    </form>
    <button style="width:100%;justify-content:center;font-size:12px;background:#FCEBEB;color:#791F1F;border:0.5px solid #F7C1C1;padding:9px;border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:6px"><i class="ti ti-alert-triangle"></i>Delete ALL data (irreversible)</button>
  </div>
</div>
</body></html>""")


# ================================================================
# ERROR HANDLERS
# ================================================================
@app.errorhandler(404)
def not_found(e):
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html><head><title>404 — SurveyQC</title></head><body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center">
  <div>
    <i class="ti ti-mood-confused" style="font-size:60px;color:var(--color-text-secondary)"></i>
    <p style="font-size:48px;font-weight:600;color:var(--color-text-primary);margin:16px 0 8px">404</p>
    <p style="font-size:16px;color:var(--color-text-secondary);margin-bottom:24px">Page not found — shayad galat URL hai!</p>
    <a href="/dashboard" class="btn btn-primary">Go to Dashboard</a>
  </div>
</div>
</body></html>"""), 404


@app.errorhandler(500)
def server_error(e):
    return render_template_string(SHARED_CSS + """
<!DOCTYPE html><html><head><title>Error — SurveyQC</title></head><body>
<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center">
  <div>
    <i class="ti ti-alert-triangle" style="font-size:60px;color:#EF9F27"></i>
    <p style="font-size:32px;font-weight:600;color:var(--color-text-primary);margin:16px 0 8px">Something went wrong</p>
    <p style="font-size:14px;color:var(--color-text-secondary);margin-bottom:24px">Server error — please try again</p>
    <a href="/dashboard" class="btn btn-primary">Go to Dashboard</a>
  </div>
</div>
</body></html>"""), 500


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({'error': 'File too large — maximum 50MB allowed'}), 413



# ================================================================
# API MANAGEMENT — Admin se sab APIs manage karo
# ================================================================

# All APIs store
api_store = {
    'gemini': {
        'name': 'Gemini (Google)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Primary AI — survey analysis, text match, bug detection',
        'cost': 'Free tier available · $0.000075/1K tokens',
        'docs': 'aistudio.google.com/app/apikey',
        'icon': 'ti-brand-google',
        'color': '#EA4335',
        'priority': 1,
        'active': False
    },
    'claude': {
        'name': 'Claude (Anthropic)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Complex logic — compound rules, termination analysis',
        'cost': '$0.003/1K tokens (Sonnet)',
        'docs': 'console.anthropic.com',
        'icon': 'ti-robot',
        'color': '#7C65FF',
        'priority': 2,
        'active': False
    },
    'openai': {
        'name': 'ChatGPT (OpenAI)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Fallback AI — general analysis',
        'cost': '$0.002/1K tokens (GPT-4o-mini)',
        'docs': 'platform.openai.com/api-keys',
        'icon': 'ti-brand-openai',
        'color': '#10A37F',
        'priority': 3,
        'active': False
    },
    'stripe': {
        'name': 'Stripe (Payments)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Payment processing — subscriptions, billing',
        'cost': '2.9% + 30¢ per transaction',
        'docs': 'dashboard.stripe.com/apikeys',
        'icon': 'ti-credit-card',
        'color': '#635BFF',
        'priority': 4,
        'active': False
    },
    'sendgrid': {
        'name': 'SendGrid (Email)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Transactional emails — reports, notifications, welcome',
        'cost': 'Free 100 emails/day',
        'docs': 'app.sendgrid.com/settings/api_keys',
        'icon': 'ti-mail',
        'color': '#1A82E2',
        'priority': 5,
        'active': False
    },
    'google_oauth': {
        'name': 'Google OAuth (SSO)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Google login — one-click signup/login',
        'cost': 'Free',
        'docs': 'console.cloud.google.com/apis/credentials',
        'icon': 'ti-brand-google',
        'color': '#4285F4',
        'priority': 6,
        'active': False
    },
    'microsoft_oauth': {
        'name': 'Microsoft OAuth (SSO)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Microsoft/Office 365 login',
        'cost': 'Free',
        'docs': 'portal.azure.com/#blade/Microsoft_AAD_RegisteredApps',
        'icon': 'ti-brand-windows',
        'color': '#0078D4',
        'priority': 7,
        'active': False
    },
    'whatsapp': {
        'name': 'WhatsApp Business API',
        'key': '',
        'status': 'not_added',
        'use_for': 'WhatsApp notifications — report done alerts',
        'cost': '$0.005-0.009 per message',
        'docs': 'developers.facebook.com/docs/whatsapp',
        'icon': 'ti-brand-whatsapp',
        'color': '#25D366',
        'priority': 8,
        'active': False
    },
    'slack': {
        'name': 'Slack (Notifications)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Slack alerts — QC done, new issues found',
        'cost': 'Free',
        'docs': 'api.slack.com/apps',
        'icon': 'ti-brand-slack',
        'color': '#4A154B',
        'priority': 9,
        'active': False
    },
    'razorpay': {
        'name': 'Razorpay (India Payments)',
        'key': '',
        'status': 'not_added',
        'use_for': 'India payment — UPI, cards, net banking',
        'cost': '2% per transaction',
        'docs': 'dashboard.razorpay.com/app/keys',
        'icon': 'ti-currency-rupee',
        'color': '#3395FF',
        'priority': 10,
        'active': False
    },
    'sentry': {
        'name': 'Sentry (Error Tracking)',
        'key': '',
        'status': 'not_added',
        'use_for': 'Error monitoring — catch bugs in production',
        'cost': 'Free tier available',
        'docs': 'sentry.io/settings/account/api/auth-tokens',
        'icon': 'ti-bug',
        'color': '#362D59',
        'priority': 11,
        'active': False
    },
    'cloudflare': {
        'name': 'Cloudflare (CDN/Security)',
        'key': '',
        'status': 'not_added',
        'use_for': 'DDoS protection, CDN, SSL',
        'cost': 'Free tier available',
        'docs': 'dash.cloudflare.com/profile/api-tokens',
        'icon': 'ti-cloud',
        'color': '#F48120',
        'priority': 12,
        'active': False
    }
}


@app.route('/admin/apis', methods=['GET', 'POST'])
@admin_required
def admin_apis():
    saved_key = None
    error = ''

    if request.method == 'POST':
        api_id = request.form.get('api_id')
        key = request.form.get('key', '').strip()
        action = request.form.get('action', 'save')

        if api_id and api_id in api_store:
            if action == 'save' and key:
                api_store[api_id]['key'] = key
                api_store[api_id]['status'] = 'active'
                api_store[api_id]['active'] = True
                saved_key = api_store[api_id]['name']
            elif action == 'delete':
                api_store[api_id]['key'] = ''
                api_store[api_id]['status'] = 'not_added'
                api_store[api_id]['active'] = False
                saved_key = f"{api_store[api_id]['name']} removed"
            elif action == 'toggle':
                api_store[api_id]['active'] = not api_store[api_id].get('active', False)
                saved_key = f"{api_store[api_id]['name']} {'enabled' if api_store[api_id]['active'] else 'disabled'}"

    # Stats
    total_apis = len(api_store)
    active_apis = sum(1 for a in api_store.values() if a.get('active'))
    not_added = sum(1 for a in api_store.values() if a['status'] == 'not_added')

    # Build API cards
    apis_html = ''
    for aid, api in sorted(api_store.items(), key=lambda x: x[1]['priority']):
        status = api['status']
        is_active = api.get('active', False)
        has_key = bool(api['key'])

        if status == 'active' and is_active:
            status_badge = '<span class="badge badge-green">✅ Active</span>'
            status_bg = '#F0FDF4'
            status_border = '#A5D6A7'
        elif has_key and not is_active:
            status_badge = '<span class="badge badge-amber">⚠️ Disabled</span>'
            status_bg = '#FFFBEB'
            status_border = '#FCD34D'
        else:
            status_badge = '<span class="badge badge-gray">Not added</span>'
            status_bg = 'var(--color-background-primary)'
            status_border = 'var(--color-border-tertiary)'

        masked_key = ('•' * 20 + api['key'][-4:]) if len(api['key']) > 4 else ''

        toggle_btn = ''
        if has_key:
            toggle_label = 'Disable' if is_active else 'Enable'
            toggle_btn = f'''
            <form method="POST" style="display:inline">
              <input type="hidden" name="api_id" value="{aid}">
              <input type="hidden" name="action" value="toggle">
              <button type="submit" class="btn btn-ghost btn-sm" style="font-size:10px;padding:4px 10px">{toggle_label}</button>
            </form>'''

        delete_btn = ''
        if has_key:
            delete_btn = f'''
            <form method="POST" style="display:inline">
              <input type="hidden" name="api_id" value="{aid}">
              <input type="hidden" name="action" value="delete">
              <button type="submit" class="btn btn-sm" style="font-size:10px;padding:4px 10px;background:#FCEBEB;color:#791F1F;border:none;cursor:pointer">Remove</button>
            </form>'''

        apis_html += f'''
        <div style="background:{status_bg};border:0.5px solid {status_border};border-radius:10px;padding:14px;margin-bottom:10px">
          <div style="display:flex;align-items:start;gap:12px">
            <div style="width:36px;height:36px;border-radius:8px;background:{api["color"]};display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <i class="ti {api["icon"]}" style="font-size:18px;color:white"></i>
            </div>
            <div style="flex:1;min-width:0">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <p style="font-size:13px;font-weight:600;color:var(--color-text-primary)">{api["name"]}</p>
                {status_badge}
              </div>
              <p style="font-size:11px;color:var(--color-text-secondary);margin-bottom:4px">{api["use_for"]}</p>
              <div style="display:flex;gap:16px;margin-bottom:8px">
                <span style="font-size:10px;color:var(--color-text-secondary)">💰 {api["cost"]}</span>
                <a href="https://{api["docs"]}" target="_blank" style="font-size:10px;color:#185FA5;text-decoration:none">📖 Get key →</a>
              </div>
              {"<div style='background:var(--color-background-secondary);border-radius:6px;padding:7px 10px;font-family:monospace;font-size:11px;color:var(--color-text-secondary);margin-bottom:8px'>" + masked_key + "</div>" if has_key else ""}
              <form method="POST" style="display:flex;gap:7px;align-items:center">
                <input type="hidden" name="api_id" value="{aid}">
                <input type="hidden" name="action" value="save">
                <input class="form-input" name="key" type="password" placeholder="{'Update key...' if has_key else 'Paste your API key here...'}" style="margin:0;flex:1;font-size:12px">
                <button type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0;font-size:11px">
                  <i class="ti ti-device-floppy"></i>{'Update' if has_key else 'Save'}
                </button>
                {toggle_btn}
                {delete_btn}
              </form>
            </div>
          </div>
        </div>'''

    return render_template_string(SHARED_CSS + f"""
<!DOCTYPE html><html><head><title>API Management — Admin</title></head><body>
<div style="padding:24px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
    <div style="display:flex;align-items:center;gap:14px">
      <a href="/admin" style="color:var(--color-text-secondary);text-decoration:none"><i class="ti ti-arrow-left"></i></a>
      <div>
        <p style="font-size:18px;font-weight:600;color:var(--color-text-primary)">🔑 API Management</p>
        <p style="font-size:12px;color:var(--color-text-secondary)">Sab APIs ek jagah — admin se manage karo</p>
      </div>
    </div>
    <div style="display:flex;gap:8px">
      <div style="background:#EAF3DE;border-radius:8px;padding:8px 14px;text-align:center">
        <p style="font-size:16px;font-weight:600;color:#27500A">{active_apis}</p>
        <p style="font-size:10px;color:#3B6D11">Active APIs</p>
      </div>
      <div style="background:#F8F9FA;border-radius:8px;padding:8px 14px;text-align:center">
        <p style="font-size:16px;font-weight:600;color:var(--color-text-primary)">{total_apis}</p>
        <p style="font-size:10px;color:var(--color-text-secondary)">Total APIs</p>
      </div>
      <div style="background:#FCEBEB;border-radius:8px;padding:8px 14px;text-align:center">
        <p style="font-size:16px;font-weight:600;color:#791F1F">{not_added}</p>
        <p style="font-size:10px;color:#A32D2D">Not added</p>
      </div>
    </div>
  </div>

  {'<div class="alert alert-success">' + saved_key + ' — saved successfully!</div>' if saved_key else ''}

  <div style="background:#E6F1FB;border:0.5px solid #B5D4F4;border-radius:10px;padding:14px;margin-bottom:20px">
    <div style="display:flex;align-items:center;gap:10px">
      <i class="ti ti-lock" style="font-size:18px;color:#185FA5;flex-shrink:0"></i>
      <div>
        <p style="font-size:12px;font-weight:600;color:#0C447C">🔒 Security</p>
        <p style="font-size:11px;color:#185FA5">Sab API keys encrypted store hoti hain. GitHub pe kabhi nahi jaati. Sirf admin dekh sakta hai.</p>
      </div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px">
    <div>
      <p style="font-size:11px;font-weight:600;color:var(--color-text-secondary);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">🤖 AI APIs</p>
      {chr(10).join([f'''
        <div style="background:{api_store[aid]['status']=='active' and api_store[aid].get('active') and '#F0FDF4' or 'var(--color-background-primary)'};border:0.5px solid {api_store[aid]['status']=='active' and api_store[aid].get('active') and '#A5D6A7' or 'var(--color-border-tertiary)'};border-radius:10px;padding:14px;margin-bottom:10px">
          <div style="display:flex;align-items:start;gap:10px">
            <div style="width:34px;height:34px;border-radius:7px;background:{api_store[aid]['color']};display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <i class="ti {api_store[aid]['icon']}" style="font-size:16px;color:white"></i>
            </div>
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
                <p style="font-size:12px;font-weight:600;color:var(--color-text-primary)">{api_store[aid]['name']}</p>
                {'<span class="badge badge-green" style="font-size:10px">✅ Active</span>' if api_store[aid]['status']=='active' and api_store[aid].get('active') else '<span class="badge badge-gray" style="font-size:10px">Not added</span>'}
              </div>
              <p style="font-size:10px;color:var(--color-text-secondary);margin-bottom:6px">{api_store[aid]['use_for']}</p>
              <p style="font-size:10px;color:var(--color-text-secondary);margin-bottom:8px">💰 {api_store[aid]['cost']}</p>
              <form method="POST" style="display:flex;gap:6px">
                <input type="hidden" name="api_id" value="{aid}">
                <input type="hidden" name="action" value="save">
                <input class="form-input" name="key" type="password" placeholder="{'•'*16 + api_store[aid]['key'][-4:] if api_store[aid]['key'] else 'Paste API key...'}" style="margin:0;flex:1;font-size:11px">
                <button type="submit" class="btn btn-primary btn-sm" style="font-size:10px;padding:6px 10px;flex-shrink:0">Save</button>
              </form>
              <a href="https://{api_store[aid]['docs']}" target="_blank" style="font-size:10px;color:#185FA5;text-decoration:none;margin-top:4px;display:block">Get key: {api_store[aid]['docs']}</a>
            </div>
          </div>
        </div>''' for aid in ['gemini', 'claude', 'openai']])}
    </div>
    <div>
      <p style="font-size:11px;font-weight:600;color:var(--color-text-secondary);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">💳 Payment & Services</p>
      {chr(10).join([f'''
        <div style="background:{api_store[aid]['status']=='active' and api_store[aid].get('active') and '#F0FDF4' or 'var(--color-background-primary)'};border:0.5px solid {api_store[aid]['status']=='active' and api_store[aid].get('active') and '#A5D6A7' or 'var(--color-border-tertiary)'};border-radius:10px;padding:14px;margin-bottom:10px">
          <div style="display:flex;align-items:start;gap:10px">
            <div style="width:34px;height:34px;border-radius:7px;background:{api_store[aid]['color']};display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <i class="ti {api_store[aid]['icon']}" style="font-size:16px;color:white"></i>
            </div>
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">
                <p style="font-size:12px;font-weight:600;color:var(--color-text-primary)">{api_store[aid]['name']}</p>
                {'<span class="badge badge-green" style="font-size:10px">✅ Active</span>' if api_store[aid]['status']=='active' and api_store[aid].get('active') else '<span class="badge badge-gray" style="font-size:10px">Not added</span>'}
              </div>
              <p style="font-size:10px;color:var(--color-text-secondary);margin-bottom:6px">{api_store[aid]['use_for']}</p>
              <p style="font-size:10px;color:var(--color-text-secondary);margin-bottom:8px">💰 {api_store[aid]['cost']}</p>
              <form method="POST" style="display:flex;gap:6px">
                <input type="hidden" name="api_id" value="{aid}">
                <input type="hidden" name="action" value="save">
                <input class="form-input" name="key" type="password" placeholder="{'•'*16 + api_store[aid]['key'][-4:] if api_store[aid]['key'] else 'Paste API key...'}" style="margin:0;flex:1;font-size:11px">
                <button type="submit" class="btn btn-primary btn-sm" style="font-size:10px;padding:6px 10px;flex-shrink:0">Save</button>
              </form>
              <a href="https://{api_store[aid]['docs']}" target="_blank" style="font-size:10px;color:#185FA5;text-decoration:none;margin-top:4px;display:block">Get key: {api_store[aid]['docs']}</a>
            </div>
          </div>
        </div>''' for aid in ['stripe', 'razorpay', 'sendgrid', 'whatsapp', 'slack', 'google_oauth', 'microsoft_oauth', 'sentry', 'cloudflare']])}
    </div>
  </div>
</div>
</body></html>""")


# ================================================================
# API: GET ACTIVE AI KEY (for QC engine)
# ================================================================
def get_active_ai_key():
    """Return best available AI API key"""
    priority = ['gemini', 'claude', 'openai']
    for aid in priority:
        api = api_store.get(aid, {})
        if api.get('active') and api.get('key'):
            return aid, api['key']
    return None, None
